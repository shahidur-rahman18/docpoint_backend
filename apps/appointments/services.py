from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import PatientProfile
from apps.doctors.models import Chamber, ChamberSchedule, DoctorProfile
from .models import Appointment


BLOCKING_STATUSES = (
    Appointment.AppointmentStatus.PENDING,
    Appointment.AppointmentStatus.CONFIRMED,
)


def _schedules_for_date(chamber, appointment_date):
    return ChamberSchedule.objects.filter(
        chamber=chamber,
        weekday=appointment_date.weekday(),
        effective_from__lte=appointment_date,
        is_active=True,
    ).filter(
        Q(effective_until__isnull=True)
        | Q(effective_until__gte=appointment_date)
    ).order_by('start_time')


def _is_past_slot(appointment_date, start_time):
    today = timezone.localdate()
    return appointment_date < today or (
        appointment_date == today
        and start_time <= timezone.localtime().time().replace(tzinfo=None)
    )


def get_available_slots(chamber, appointment_date):
    schedules = _schedules_for_date(chamber, appointment_date)
    if not schedules:
        return []

    booked_intervals = list(Appointment.objects.filter(
        doctor_id=chamber.doctor_id,
        appointment_date=appointment_date,
        status__in=BLOCKING_STATUSES,
    ).values_list('time_slot_start', 'time_slot_end'))

    available_slots = []
    seen_slots = set()
    for schedule in schedules:
        shift_end = datetime.combine(appointment_date, schedule.end_time)
        slot_start = datetime.combine(appointment_date, schedule.start_time)
        duration = timedelta(minutes=schedule.appointment_duration_minutes)

        while slot_start + duration <= shift_end:
            slot_end = slot_start + duration
            start_time = slot_start.time()
            end_time = slot_end.time()
            slot_key = (start_time, end_time)
            if (
                slot_key not in seen_slots
                and not _is_past_slot(appointment_date, start_time)
                and not any(
                    booked_start < end_time and booked_end > start_time
                    for booked_start, booked_end in booked_intervals
                )
            ):
                available_slots.append({
                    'start_time': start_time.isoformat(),
                    'end_time': end_time.isoformat(),
                })
                seen_slots.add(slot_key)
            slot_start = slot_end

    return available_slots


def _missing_profile_fields(patient):
    try:
        profile = PatientProfile.objects.select_for_update().get(user=patient)
    except PatientProfile.DoesNotExist:
        return list(PatientProfile.REQUIRED_FIELDS)
    return profile.missing_required_fields


def validate_appointment_reschedule(
    *,
    appointment,
    doctor,
    chamber,
    appointment_date,
    time_slot_start,
    check_availability=True,
):
    if chamber is None or chamber.doctor_id != doctor.pk or not chamber.is_active:
        raise ValidationError({
            'chamber': 'Rescheduled appointments require an active chamber belonging to the selected doctor.',
        })

    if appointment_date < timezone.localdate():
        raise ValidationError({
            'appointment_date': 'Appointments cannot be scheduled for a past date.',
        })

    if _is_past_slot(appointment_date, time_slot_start):
        raise ValidationError({
            'time_slot_start': 'Appointments cannot be scheduled for a past time.',
        })

    schedule = None
    slot_end = None
    for candidate in _schedules_for_date(chamber, appointment_date):
        if (
            candidate.start_time <= time_slot_start < candidate.end_time
            and (
                datetime.combine(appointment_date, time_slot_start)
                - datetime.combine(appointment_date, candidate.start_time)
            ).total_seconds()
            % (candidate.appointment_duration_minutes * 60) == 0
        ):
            candidate_slot_end = (
                datetime.combine(appointment_date, time_slot_start)
                + timedelta(minutes=candidate.appointment_duration_minutes)
            ).time()
            if candidate_slot_end <= candidate.end_time:
                schedule = candidate
                slot_end = candidate_slot_end
                break

    if schedule is None:
        raise ValidationError({
            'time_slot_start': 'The selected time is not an available slot in the active schedule.',
        })

    if (
        check_availability
        and Appointment.objects.filter(
            doctor=doctor,
            appointment_date=appointment_date,
            status__in=BLOCKING_STATUSES,
            time_slot_start__lt=slot_end,
            time_slot_end__gt=time_slot_start,
        ).exclude(pk=appointment.pk).exists()
    ):
        raise ValidationError({
            'time_slot_start': 'This appointment slot has already been booked.',
        })

    return slot_end


@transaction.atomic
def create_appointment_booking(
    *,
    patient,
    chamber,
    appointment_date,
    time_slot_start,
    symptoms_note=None,
):
    try:
        locked_chamber = Chamber.objects.select_for_update().get(pk=chamber.pk)
    except Chamber.DoesNotExist as exc:
        raise ValidationError({'chamber': 'The selected chamber is not available.'}) from exc

    if not locked_chamber.is_active:
        raise ValidationError({'chamber': 'Appointments cannot be booked at an inactive chamber.'})

    doctor = DoctorProfile.objects.select_for_update().get(
        pk=locked_chamber.doctor_id,
    )

    missing_fields = _missing_profile_fields(patient)
    if missing_fields:
        raise ValidationError({
            'profile_incomplete': True,
            'missing_fields': missing_fields,
            'detail': 'Complete your patient profile before booking an appointment.',
        })

    if appointment_date < timezone.localdate():
        raise ValidationError({
            'appointment_date': 'Appointments cannot be booked for a past date.',
        })

    schedules = list(_schedules_for_date(locked_chamber, appointment_date))
    matching_schedule = next(
        (
            schedule
            for schedule in schedules
            if schedule.start_time <= time_slot_start < schedule.end_time
            and (
                datetime.combine(appointment_date, time_slot_start)
                - datetime.combine(appointment_date, schedule.start_time)
            ).total_seconds()
            % (schedule.appointment_duration_minutes * 60) == 0
            and datetime.combine(appointment_date, time_slot_start)
            + timedelta(minutes=schedule.appointment_duration_minutes)
            <= datetime.combine(appointment_date, schedule.end_time)
        ),
        None,
    )
    if matching_schedule is None:
        raise ValidationError({
            'time_slot_start': 'The selected time is not an available slot in the active schedule.',
        })

    duration = timedelta(minutes=matching_schedule.appointment_duration_minutes)
    slot_end = (
        datetime.combine(appointment_date, time_slot_start) + duration
    ).time()
    if _is_past_slot(appointment_date, time_slot_start):
        raise ValidationError({
            'time_slot_start': 'Appointments cannot be booked for a past time.',
        })

    overlapping_appointments = Appointment.objects.filter(
        doctor=doctor,
        appointment_date=appointment_date,
        status__in=BLOCKING_STATUSES,
        time_slot_start__lt=slot_end,
        time_slot_end__gt=time_slot_start,
    )
    if overlapping_appointments.exists():
        raise ValidationError({
            'time_slot_start': 'This appointment slot has already been booked.',
        })

    try:
        with transaction.atomic():
            return Appointment.objects.create(
                patient=patient,
                doctor=doctor,
                chamber=locked_chamber,
                appointment_date=appointment_date,
                time_slot_start=time_slot_start,
                time_slot_end=slot_end,
                symptoms_note=symptoms_note,
                total_fee=doctor.consultation_fee,
            )
    except IntegrityError as exc:
        cause = exc.__cause__
        constraint_name = getattr(
            getattr(cause, 'diag', None),
            'constraint_name',
            None,
        )
        sqlite_constraint = (
            'appointments_appointment.doctor_id, '
            'appointments_appointment.appointment_date, '
            'appointments_appointment.time_slot_start'
        )
        if (
            constraint_name == 'appt_active_doctor_slot_unique'
            or sqlite_constraint in str(exc)
        ):
            raise ValidationError({
                'time_slot_start': 'This appointment slot has already been booked.',
            }) from exc
        raise
