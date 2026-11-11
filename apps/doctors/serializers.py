from django.utils import timezone
from rest_framework import serializers

from .models import Chamber, ChamberSchedule, DoctorProfile
from apps.accounts.serializers import UserProfileSerializer


class DoctorProfileSerializer(serializers.ModelSerializer):
    user_details = UserProfileSerializer(source='user', read_only=True)

    class Meta:
        model = DoctorProfile
        fields = [
            'id',
            'user',
            'user_details',
            'specialization',
            'qualification',
            'consultation_fee',
            'chamber_address',
            'is_available',
        ]
        read_only_fields = ['id', 'user']


class ChamberSerializer(serializers.ModelSerializer):
    doctor = serializers.PrimaryKeyRelatedField(
        queryset=DoctorProfile.objects.select_related('user').all(),
        required=True,
    )

    class Meta:
        model = Chamber
        fields = ['id', 'doctor', 'name', 'address', 'is_active']
        read_only_fields = ['id']

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if (
            user is not None
            and user.is_authenticated
            and not (
                user.role == 'ADMIN' or user.is_superuser
            )
        ):
            fields['doctor'].read_only = True
        return fields

    def validate_doctor(self, doctor):
        if doctor.user.role != 'DOCTOR':
            raise serializers.ValidationError(
                'Chambers can only be assigned to a doctor profile.'
            )
        return doctor


class ChamberScheduleSerializer(serializers.ModelSerializer):
    chamber = serializers.PrimaryKeyRelatedField(read_only=True)
    weekday = serializers.ChoiceField(choices=ChamberSchedule.Weekday.choices)
    weekday_display = serializers.CharField(
        source='get_weekday_display',
        read_only=True,
    )
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = ChamberSchedule
        fields = [
            'id',
            'chamber',
            'weekday',
            'weekday_display',
            'start_time',
            'end_time',
            'appointment_duration_minutes',
            'effective_from',
            'effective_until',
            'is_active',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        effective_from = attrs.get('effective_from')
        if (
            effective_from is not None
            and effective_from < timezone.localdate()
        ):
            raise serializers.ValidationError({
                'effective_from': 'Schedules cannot start in the past.',
            })
        return attrs