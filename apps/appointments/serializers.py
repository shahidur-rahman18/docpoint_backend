from rest_framework import serializers
from .models import Appointment
from apps.doctors.models import DoctorProfile
from apps.doctors.serializers import DoctorProfileSerializer

class AppointmentSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorProfileSerializer(source='doctor', read_only=True)
    patient_email = serializers.ReadOnlyField(source='patient.email')

    class Meta:
        model = Appointment
        fields = [
            'id',
            'patient',
            'patient_email',
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