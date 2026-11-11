from rest_framework import serializers
from django.db import IntegrityError, transaction
from apps.accounts.models import CustomUser
from .models import Appointment
from apps.doctors.models import Chamber, DoctorProfile
from apps.doctors.serializers import DoctorProfileSerializer
from .services import (
    create_appointment_booking,
    validate_appointment_reschedule,
)

class AppointmentPatientSerializer(serializers.ModelSerializer):
    avatar_url = serializers.URLField(read_only=True, allow_null=True)

    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'avatar_url']


class AppointmentSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorProfileSerializer(source='doctor', read_only=True)
    patient_email = serializers.ReadOnlyField(source='patient.email')
    patient_detail = AppointmentPatientSerializer(source='patient', read_only=True)
    chamber = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id',
            'patient',
            'patient_email',
            'patient_detail',
            'doctor',
            'doctor_detail',
            'chamber',
            'appointment_date',
            'time_slot_start',
            'time_slot_end',
            'symptoms_note',
            'total_fee',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'patient', 'total_fee', 'created_at', 'updated_at']

    def validate(self, attrs):
        doctor = attrs.get('doctor')
        appointment_date = attrs.get('appointment_date')
        proposed_start = attrs.get('time_slot_start')
        proposed_end = attrs.get('time_slot_end')

        if proposed_start >= proposed_end:
            raise serializers.ValidationError(
                {"time_slot_end": "অ্যাপয়েন্টমেন্ট শেষের সময় অবশ্যই শুরু হওয়ার সময়ের পরে হতে হবে।"}
            )

        overlapping_appointments = Appointment.objects.filter(
            doctor=doctor,
            appointment_date=appointment_date,
            status__in=[Appointment.AppointmentStatus.PENDING, Appointment.AppointmentStatus.CONFIRMED],
            time_slot_start__lt=proposed_end,
            time_slot_end__gt=proposed_start
        )
   
        if self.instance:
            overlapping_appointments = overlapping_appointments.exclude(id=self.instance.id)

        if overlapping_appointments.exists():
            raise serializers.ValidationError(
                {"non_field_errors": "নির্বাচিত ডাক্তার এই নির্দিষ্ট সময়ে অন্য অ্যাপয়েন্টমেন্টে ব্যস্ত আছেন। অনুগ্রহ করে অন্য সময় বেছে নিন।"}
            )

        return attrs

    def create(self, validated_data):
        doctor = validated_data.get('doctor')
        validated_data['total_fee'] = doctor.consultation_fee
        return super().create(validated_data)


class AppointmentUpdateSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorProfileSerializer(source='doctor', read_only=True)
    patient_email = serializers.ReadOnlyField(source='patient.email')
    patient_detail = AppointmentPatientSerializer(source='patient', read_only=True)
    chamber = serializers.PrimaryKeyRelatedField(
        queryset=Chamber.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Appointment
        fields = [
            'id',
            'patient',
            'patient_email',
            'patient_detail',
            'doctor',
            'doctor_detail',
            'chamber',
            'appointment_date',
            'time_slot_start',
            'time_slot_end',
            'symptoms_note',
            'total_fee',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'patient',
            'time_slot_end',
            'total_fee',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'patient': {'required': False},
            'doctor': {'required': False},
            'chamber': {'required': False},
            'appointment_date': {'required': False},
            'time_slot_start': {'required': False},
            'symptoms_note': {'required': False},
            'status': {'required': False},
        }

    def validate(self, attrs):
        request = self.context['request']
        user = request.user
        allowed_fields = {
            'ADMIN': {
                'doctor',
                'chamber',
                'appointment_date',
                'time_slot_start',
                'symptoms_note',
                'status',
            },
            'DOCTOR': {'status'},
            'PATIENT': {'symptoms_note'},
        }.get(user.role, set())
        submitted_fields = set(self.initial_data)
        forbidden_fields = submitted_fields - allowed_fields
        if forbidden_fields:
            raise serializers.ValidationError({
                field: 'You do not have permission to change this appointment field.'
                for field in sorted(forbidden_fields)
            })

        instance = self.instance
        if instance is None:
            return attrs

        if user.role == 'DOCTOR' and 'status' in attrs:
            allowed_transitions = {
                Appointment.AppointmentStatus.PENDING: {
                    Appointment.AppointmentStatus.CONFIRMED,
                    Appointment.AppointmentStatus.CANCELLED,
                },
                Appointment.AppointmentStatus.CONFIRMED: {
                    Appointment.AppointmentStatus.COMPLETED,
                    Appointment.AppointmentStatus.CANCELLED,
                },
            }
            if (
                attrs['status'] != instance.status
                and attrs['status'] not in allowed_transitions.get(instance.status, set())
            ):
                raise serializers.ValidationError({
                    'status': 'This status transition is not allowed.',
                })

        booking_fields = {'doctor', 'chamber', 'appointment_date', 'time_slot_start'}
        status_value = attrs.get('status', instance.status)
        should_validate_schedule = bool(booking_fields.intersection(attrs)) or (
            user.role == 'ADMIN'
            and status_value in (
                Appointment.AppointmentStatus.PENDING,
                Appointment.AppointmentStatus.CONFIRMED,
            )
        )
        if should_validate_schedule:
            doctor = attrs.get('doctor', instance.doctor)
            chamber = attrs.get('chamber', instance.chamber)
            appointment_date = attrs.get('appointment_date', instance.appointment_date)
            time_slot_start = attrs.get(
                'time_slot_start',
                instance.time_slot_start,
            )
            attrs['time_slot_end'] = validate_appointment_reschedule(
                appointment=instance,
                doctor=doctor,
                chamber=chamber,
                appointment_date=appointment_date,
                time_slot_start=time_slot_start,
                check_availability=status_value in (
                    Appointment.AppointmentStatus.PENDING,
                    Appointment.AppointmentStatus.CONFIRMED,
                ),
            )

        return attrs

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                return super().update(instance, validated_data)
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
                raise serializers.ValidationError({
                    'time_slot_start': 'This appointment slot has already been booked.',
                }) from exc
            raise


class AppointmentBookingSerializer(serializers.Serializer):
    chamber = serializers.PrimaryKeyRelatedField(
        queryset=Chamber.objects.all(),
    )
    appointment_date = serializers.DateField(input_formats=['%Y-%m-%d'])
    time_slot_start = serializers.TimeField()
    symptoms_note = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate(self, attrs):
        forbidden_fields = {'doctor', 'patient', 'status', 'time_slot_end', 'total_fee'}
        supplied_fields = forbidden_fields.intersection(self.initial_data)
        if supplied_fields:
            raise serializers.ValidationError({
                field: 'This field is assigned by the server and cannot be supplied.'
                for field in sorted(supplied_fields)
            })
        return attrs

    def create(self, validated_data):
        patient = validated_data.pop('patient')
        return create_appointment_booking(patient=patient, **validated_data)