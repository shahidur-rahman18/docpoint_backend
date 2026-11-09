from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rest_framework import serializers
from .models import DoctorProfile, DoctorSchedule, WEEKDAYS
from apps.accounts.serializers import UserProfileSerializer


class AvailabilitySlotSerializer(serializers.Serializer):
    start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])


class WeeklyScheduleSerializer(serializers.Serializer):
    MONDAY = AvailabilitySlotSerializer(many=True)
    TUESDAY = AvailabilitySlotSerializer(many=True)
    WEDNESDAY = AvailabilitySlotSerializer(many=True)
    THURSDAY = AvailabilitySlotSerializer(many=True)
    FRIDAY = AvailabilitySlotSerializer(many=True)
    SATURDAY = AvailabilitySlotSerializer(many=True)
    SUNDAY = AvailabilitySlotSerializer(many=True)

    def validate(self, schedule):
        for day in WEEKDAYS:
            slots = schedule[day]
            for index, slot in enumerate(slots):
                if slot['start'] >= slot['end']:
                    raise serializers.ValidationError({
                        day: f'Slot {index + 1} must end after it starts.',
                    })

                for other_slot in slots[:index]:
                    if (
                        slot['start'] < other_slot['end']
                        and slot['end'] > other_slot['start']
                    ):
                        raise serializers.ValidationError({
                            day: 'Availability slots cannot overlap.',
                        })

        return schedule


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


class DoctorScheduleSerializer(serializers.ModelSerializer):
    effective_from = serializers.DateField(required=True, allow_null=True)
    effective_until = serializers.DateField(required=True, allow_null=True)
    timezone = serializers.CharField(required=True, max_length=64)
    weekly_schedule = WeeklyScheduleSerializer(required=True)

    class Meta:
        model = DoctorSchedule
        fields = ['effective_from', 'effective_until', 'timezone', 'weekly_schedule']

    def validate_timezone(self, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise serializers.ValidationError('Use a valid IANA timezone, such as Asia/Dhaka.')
        return value

    def validate(self, attrs):
        effective_from = attrs.get(
            'effective_from',
            self.instance.effective_from if self.instance else None,
        )
        effective_until = attrs.get(
            'effective_until',
            self.instance.effective_until if self.instance else None,
        )

        if effective_from is None:
            raise serializers.ValidationError({
                'effective_from': 'A start date is required to activate the schedule.',
            })
        if effective_until is not None and effective_until < effective_from:
            raise serializers.ValidationError({
                'effective_until': 'The end date must be on or after the start date.',
            })

        return attrs

    @staticmethod
    def _serialize_weekly_schedule(schedule):
        return {
            day: [
                {
                    'start': slot['start'].strftime('%H:%M'),
                    'end': slot['end'].strftime('%H:%M'),
                }
                for slot in schedule[day]
            ]
            for day in WEEKDAYS
        }

    def create(self, validated_data):
        validated_data['weekly_schedule'] = self._serialize_weekly_schedule(
            validated_data['weekly_schedule'],
        )
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data['weekly_schedule'] = self._serialize_weekly_schedule(
            validated_data['weekly_schedule'],
        )
        return super().update(instance, validated_data)