from datetime import time

from django.conf import settings
from django.db import migrations


WEEKDAYS = (
    ('MONDAY', 0),
    ('TUESDAY', 1),
    ('WEDNESDAY', 2),
    ('THURSDAY', 3),
    ('FRIDAY', 4),
    ('SATURDAY', 5),
    ('SUNDAY', 6),
)
APPOINTMENT_DURATION_MINUTES = 30
CHAMBER_NAME = 'Primary Chamber'


def copy_legacy_schedules(apps, schema_editor):
    DoctorProfile = apps.get_model('doctors', 'DoctorProfile')
    DoctorSchedule = apps.get_model('doctors', 'DoctorSchedule')
    Chamber = apps.get_model('doctors', 'Chamber')
    ChamberSchedule = apps.get_model('doctors', 'ChamberSchedule')
    database = schema_editor.connection.alias

    for legacy_schedule in DoctorSchedule.objects.using(database).select_related(
        'doctor',
    ).order_by('pk'):
        if legacy_schedule.timezone != settings.TIME_ZONE:
            raise RuntimeError(
                f'DoctorSchedule {legacy_schedule.pk} uses timezone '
                f'{legacy_schedule.timezone!r}, but the project timezone is '
                f'{settings.TIME_ZONE!r}; map it explicitly before migrating.'
            )
        if legacy_schedule.effective_from is None:
            raise RuntimeError(
                f'DoctorSchedule {legacy_schedule.pk} has no effective_from date.'
            )
        if (
            legacy_schedule.effective_until is not None
            and legacy_schedule.effective_until < legacy_schedule.effective_from
        ):
            raise RuntimeError(
                f'DoctorSchedule {legacy_schedule.pk} has an invalid date range.'
            )

        doctor = DoctorProfile.objects.using(database).get(
            pk=legacy_schedule.doctor_id,
        )
        existing_chambers = list(
            Chamber.objects.using(database).filter(doctor_id=doctor.pk).order_by('pk')
        )
        if len(existing_chambers) > 1:
            raise RuntimeError(
                f'Doctor {doctor.pk} has multiple chambers; map DoctorSchedule '
                'to a specific chamber before migrating.'
            )
        if existing_chambers:
            chamber = existing_chambers[0]
            if chamber.address.strip() != (doctor.chamber_address or '').strip():
                raise RuntimeError(
                    f'Doctor {doctor.pk} has an existing chamber whose address '
                    'does not match DoctorProfile.chamber_address.'
                )
        else:
            address = (doctor.chamber_address or '').strip()
            if not address:
                raise RuntimeError(
                    f'Doctor {doctor.pk} has no chamber address; add a verified '
                    'address before migrating the schedule.'
                )
            chamber = Chamber.objects.using(database).create(
                doctor_id=doctor.pk,
                name=CHAMBER_NAME,
                address=address,
                is_active=True,
            )

        if ChamberSchedule.objects.using(database).filter(
            chamber_id=chamber.pk,
        ).exists():
            raise RuntimeError(
                f'Chamber {chamber.pk} already has schedules; reconcile them '
                'with DoctorSchedule before migrating.'
            )

        weekly_schedule = legacy_schedule.weekly_schedule
        schedules_to_create = []
        for day_name, weekday in WEEKDAYS:
            windows = weekly_schedule.get(day_name, [])
            if not isinstance(windows, list):
                raise RuntimeError(
                    f'DoctorSchedule {legacy_schedule.pk} has invalid '
                    f'{day_name} windows.'
                )

            parsed_windows = []
            for window in windows:
                try:
                    start_time = time.fromisoformat(window['start'])
                    end_time = time.fromisoformat(window['end'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f'DoctorSchedule {legacy_schedule.pk} has an invalid '
                        f'{day_name} time window.'
                    ) from exc

                if (
                    start_time >= end_time
                    or (
                        end_time.hour * 60 + end_time.minute
                        - start_time.hour * 60
                        - start_time.minute
                    ) < APPOINTMENT_DURATION_MINUTES
                ):
                    raise RuntimeError(
                        f'DoctorSchedule {legacy_schedule.pk} has a {day_name} '
                        'window shorter than one appointment or with invalid times.'
                    )
                parsed_windows.append((start_time, end_time))

            parsed_windows.sort()
            for previous, current in zip(parsed_windows, parsed_windows[1:]):
                if current[0] < previous[1]:
                    raise RuntimeError(
                        f'DoctorSchedule {legacy_schedule.pk} has overlapping '
                        f'{day_name} windows.'
                    )

            schedules_to_create.extend(
                ChamberSchedule(
                    chamber_id=chamber.pk,
                    weekday=weekday,
                    start_time=start_time,
                    end_time=end_time,
                    appointment_duration_minutes=APPOINTMENT_DURATION_MINUTES,
                    effective_from=legacy_schedule.effective_from,
                    effective_until=legacy_schedule.effective_until,
                    is_active=True,
                )
                for start_time, end_time in parsed_windows
            )

        ChamberSchedule.objects.using(database).bulk_create(schedules_to_create)


class Migration(migrations.Migration):

    dependencies = [
        ('appointments', '0004_appointment_appt_active_doctor_slot_unique'),
        ('doctors', '0007_merge_doctorschedule_chamber'),
    ]

    operations = [
        migrations.RunPython(
            copy_legacy_schedules,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
