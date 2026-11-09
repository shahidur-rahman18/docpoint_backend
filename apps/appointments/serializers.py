from datetime import time

from rest_framework import serializers
from apps.accounts.models import CustomUser
from .models import Appointment
from apps.doctors.models import DoctorSchedule
from apps.doctors.serializers import DoctorProfileSerializer

class AppointmentPatientSerializer(serializers.ModelSerializer):
    avatar_url = serializers.URLField(read_only=True, allow_null=True)

    class Meta:
        model = CustomUser
        fields = ['id', 'email', 'first_name', 'last_name', 'avatar_url']


class AppointmentSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorProfileSerializer(source='doctor', read_only=True)
    patient_email = serializers.ReadOnlyField(source='patient.email')
    patient_detail = AppointmentPatientSerializer(source='patient', read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id',
            'patient',
            'patient_email',
            'patient_detail',
            'doctor',
            'doctor_detail',
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
        instance = self.instance
        doctor = attrs.get('doctor', instance.doctor if instance else None)
        appointment_date = attrs.get(
            'appointment_date',
            instance.appointment_date if instance else None,
        )
        proposed_start = attrs.get(
            'time_slot_start',
            instance.time_slot_start if instance else None,
        )
        proposed_end = attrs.get(
            'time_slot_end',
            instance.time_slot_end if instance else None,
        )
        status_value = attrs.get(
            'status',
            instance.status if instance else Appointment.AppointmentStatus.PENDING,
        )

        if proposed_start >= proposed_end:
            raise serializers.ValidationError(
                {"time_slot_end": "অ্যাপয়েন্টমেন্ট শেষের সময় অবশ্যই শুরু হওয়ার সময়ের পরে হতে হবে।"}
            )

        active_statuses = [
            Appointment.AppointmentStatus.PENDING,
            Appointment.AppointmentStatus.CONFIRMED,
        ]
        if status_value in active_statuses:
            schedule = DoctorSchedule.objects.filter(doctor=doctor).first()
            weekday = appointment_date.strftime('%A').upper()
            available_slots = (
                schedule.weekly_schedule.get(weekday, [])
                if schedule
                and schedule.effective_from is not None
                and schedule.effective_from <= appointment_date
                and (
                    schedule.effective_until is None
                    or appointment_date <= schedule.effective_until
                )
                else []
            )
            is_available = any(
                time.fromisoformat(slot['start']) <= proposed_start
                and proposed_end <= time.fromisoformat(slot['end'])
                for slot in available_slots
            )
            if not is_available:
                raise serializers.ValidationError({
                    'appointment_date': (
                        'The doctor is not available for the selected date and time.'
                    ),
                })

        if status_value in active_statuses:
            overlapping_appointments = Appointment.objects.filter(
                doctor=doctor,
                appointment_date=appointment_date,
                status__in=active_statuses,
                time_slot_start__lt=proposed_end,
                time_slot_end__gt=proposed_start
            )

            if instance:
                overlapping_appointments = overlapping_appointments.exclude(id=instance.id)

            if overlapping_appointments.exists():
                raise serializers.ValidationError(
                    {"non_field_errors": "নির্বাচিত ডাক্তার এই নির্দিষ্ট সময়ে অন্য অ্যাপয়েন্টমেন্টে ব্যস্ত আছেন। অনুগ্রহ করে অন্য সময় বেছে নিন।"}
                )

        return attrs

    def create(self, validated_data):
        doctor = validated_data.get('doctor')
        validated_data['total_fee'] = doctor.consultation_fee
        return super().create(validated_data)