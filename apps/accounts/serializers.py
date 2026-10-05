from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
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


class AdminDoctorCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_blank=True)
    
    specialization = serializers.CharField(required=False, allow_blank=True)
    qualification = serializers.CharField(required=False, allow_blank=True)
    consultation_fee = serializers.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    chamber_address = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data.pop('email')
        first_name = validated_data.pop('first_name', '')
        last_name = validated_data.pop('last_name', '')
        phone_number = validated_data.pop('phone_number', '')

        specialization = validated_data.pop('specialization', '')
        qualification = validated_data.pop('qualification', '')
        consultation_fee = validated_data.pop('consultation_fee', 0.00)
        chamber_address = validated_data.pop('chamber_address', '')

        user = User.objects.create_user(
            email=email,
            password=None,
            role=User.Role.DOCTOR,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            is_active=False,
            is_verified=False
        )
        user.set_unusable_password()
        user.save()

        doctor_profile, _ = DoctorProfile.objects.get_or_create(user=user)
        doctor_profile.specialization = specialization
        doctor_profile.qualification = qualification
        doctor_profile.consultation_fee = consultation_fee
        doctor_profile.chamber_address = chamber_address
        doctor_profile.save()

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        setup_url = f"https://dockpoint-frontend.vercel.app/setup-password?uid={uid}&token={token}"
        subject = "Welcome to DocPoint - Set Your Doctor Password"
        message = f"Hello Dr. {email},\n\nYou have been registered as a doctor by the admin.\nPlease click the link below to set your password and activate your account:\n{setup_url}\n\nThank you!"
        
        try:
            send_mail(subject, message, getattr(settings, 'DEFAULT_FROM_EMAIL', 'admin@docpoint.com'), [email], fail_silently=True)
        except Exception:
            pass

        return user


class PasswordSetupSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        uid = attrs.get('uid')
        token = attrs.get('token')

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({"uid": "Invalid UID."})

        if not default_token_generator.check_token(user, token):
            raise serializers.ValidationError({"token": "Invalid or expired token."})

        attrs['user'] = user
        return attrs

    def save(self):
        user = self.validated_data['user']
        new_password = self.validated_data['new_password']
        user.set_password(new_password)
        user.is_active = True
        user.is_verified = True
        user.save()
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    doctor_profile = AccountDoctorProfileSerializer(read_only=True)
    patient_profile = PatientProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'role', 'phone_number', 'is_verified', 'doctor_profile', 'patient_profile']
        read_only_fields = ['id', 'email', 'role', 'is_verified']