from rest_framework import serializers
from .models import DoctorProfile
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