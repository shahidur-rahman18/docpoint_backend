from rest_framework import serializers
from django.contrib.auth import get_user_model
from apps.doctors.models import DoctorProfile
from apps.accounts.models import PatientProfile

User = get_user_model()


class AccountDoctorProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorProfile
        fields = ['id', 'specialization', 'qualification', 'consultation_fee', 'chamber_address', 'is_available']


class PatientProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientProfile
        fields = ['id', 'emergency_contact']


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'role', 'phone_number']

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            role=validated_data.get('role', User.Role.PATIENT),
            phone_number=validated_data.get('phone_number', '')
        )
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    doctor_profile = AccountDoctorProfileSerializer(read_only=True)
    patient_profile = PatientProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'role', 'phone_number', 'doctor_profile', 'patient_profile']
        read_only_fields = ['id', 'email', 'role']