import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import DatabaseError
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError
from apps.accounts.avatar_storage import AvatarStorageUnavailable, delete_avatar, upload_avatar
from apps.accounts.emails import (
    send_password_reset_email,
    send_patient_verification_otp,
)
from apps.doctors.models import DoctorProfile
from apps.accounts.models import EmailVerificationOTP, MedicalRecord, PatientProfile
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()
logger = logging.getLogger(__name__)
MAX_AVATAR_SIZE = 5 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
ALLOWED_AVATAR_FORMATS = {'JPEG', 'PNG', 'WEBP'}


class AvatarImageField(serializers.ImageField):
    def to_internal_value(self, data):
        if getattr(data, 'size', 0) > MAX_AVATAR_SIZE:
            raise ValidationError('Avatar image must be 5 MB or smaller.')
        return super().to_internal_value(data)


class AccountDoctorProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorProfile
        fields = ['id', 'specialization', 'qualification', 'consultation_fee', 'chamber_address', 'is_available']


class PatientProfileSerializer(serializers.ModelSerializer):
    profile_complete = serializers.BooleanField(read_only=True, source='is_complete')
    missing_fields = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        source='missing_required_fields',
    )

    class Meta:
        model = PatientProfile
        fields = [
            'id',
            'date_of_birth',
            'gender',
            'blood_group',
            'address',
            'emergency_contact',
            'profile_complete',
            'missing_fields',
        ]
        read_only_fields = ['id']

    def validate_date_of_birth(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        return value

    def validate(self, attrs):
        protected_fields = {'user', 'role', 'patient', 'profile_complete', 'missing_fields'}
        supplied_fields = protected_fields.intersection(self.initial_data)
        if supplied_fields:
            raise serializers.ValidationError({
                field: 'This field cannot be changed through the patient profile API.'
                for field in sorted(supplied_fields)
            })
        return attrs


class MedicalRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalRecord
        fields = [
            'id',
            'title',
            'description',
            'document_url',
            'document_public_id',
            'recorded_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PatientSignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=15)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate(self, attrs):
        user = User(
            email=attrs['email'],
            first_name=attrs.get('first_name', ''),
            last_name=attrs.get('last_name', ''),
        )
        _validate_password(attrs['password'], user, 'password')
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(
            password=password,
            role=User.Role.PATIENT,
            is_active=False,
            is_verified=False,
            **validated_data,
        )
        _create_verification_otp(user)
        return user


class EmailVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField(min_length=6, max_length=6)

    def validate(self, attrs):
        try:
            user = User.objects.get(
                email__iexact=attrs['email'],
                role=User.Role.PATIENT,
                is_active=False,
            )
        except User.DoesNotExist:
            raise serializers.ValidationError({'email': 'Invalid email or OTP.'})

        otp = EmailVerificationOTP.objects.filter(
            user=user,
            otp_code=attrs['otp'],
            is_used=False,
            expires_at__gt=timezone.now(),
        ).order_by('-created_at').first()
        if otp is None:
            raise serializers.ValidationError({'otp': 'Invalid or expired OTP.'})

        attrs['user'] = user
        attrs['verification_otp'] = otp
        return attrs

    @transaction.atomic
    def save(self):
        user = self.validated_data['user']
        otp = self.validated_data['verification_otp']
        otp.is_used = True
        otp.save(update_fields=['is_used'])
        user.is_active = True
        user.is_verified = True
        user.save(update_fields=['is_active', 'is_verified'])

        refresh = RefreshToken.for_user(user)
        return {'access': str(refresh.access_token), 'refresh': str(refresh)}


class ResendOTPSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate(self, attrs):
        attrs['user'] = User.objects.filter(
            email__iexact=attrs['email'],
            role=User.Role.PATIENT,
            is_active=False,
        ).first()
        return attrs

    @transaction.atomic
    def save(self):
        user = self.validated_data['user']
        if user is not None:
            EmailVerificationOTP.objects.filter(user=user, is_used=False).update(is_used=True)
            _create_verification_otp(user)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate(self, attrs):
        attrs['user'] = User.objects.filter(
            email__iexact=attrs['email'],
            is_active=True,
        ).first()
        return attrs

    def save(self):
        user = self.validated_data['user']
        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = (
                f"{settings.FRONTEND_URL}/reset-password?"
                f"{urlencode({'uid': uid, 'token': token})}"
            )
            send_password_reset_email(user.email, reset_url)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        try:
            user_id = force_str(urlsafe_base64_decode(attrs['uid']))
            user = User.objects.get(pk=user_id, is_active=True)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError({'uid': 'Invalid UID.'})

        if not default_token_generator.check_token(user, attrs['token']):
            raise serializers.ValidationError({'token': 'Invalid or expired token.'})
        _validate_password(attrs['new_password'], user, 'new_password')
        attrs['user'] = user
        return attrs

    @transaction.atomic
    def save(self):
        user = self.validated_data['user']
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password'])
        return user


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_old_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Current password is incorrect.')
        return value

    def validate_new_password(self, value):
        _validate_password(value, self.context['request'].user, 'new_password')
        return value

    def save(self):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password'])
        return user


def _create_verification_otp(user):
    otp_code = f'{secrets.randbelow(1_000_000):06d}'
    EmailVerificationOTP.objects.create(
        user=user,
        otp_code=otp_code,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    send_patient_verification_otp(user.email, otp_code)


def _validate_password(password, user, field):
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: exc.messages})


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

        setup_url = f"{settings.FRONTEND_URL}/setup-password?{urlencode({'uid': uid, 'token': token})}"
        subject = "Welcome to DocPoint - Set Your Doctor Password"
        message = f"Hello Dr. {email},\n\nYou have been registered as a doctor by the admin.\nPlease click the link below to set your password and activate your account:\n{setup_url}\n\nThank you!"

        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email])

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

        if user.role != User.Role.DOCTOR:
            raise serializers.ValidationError({
                "uid": "Password setup is only available for doctor accounts."
            })

        if not default_token_generator.check_token(user, token):
            raise serializers.ValidationError({"token": "Invalid or expired token."})

        attrs['user'] = user
        return attrs

    @transaction.atomic
    def save(self):
        user = self.validated_data['user']
        new_password = self.validated_data['new_password']
        user.set_password(new_password)
        user.is_active = True
        user.is_verified = True
        user.save()

        if user.role != User.Role.DOCTOR or not user.is_active:
            raise serializers.ValidationError(
                "Only active doctor accounts can receive setup tokens."
            )

        refresh = RefreshToken.for_user(user)
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }


class AdminPatientListSerializer(serializers.ModelSerializer):
    patient_profile = PatientProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'first_name',
            'last_name',
            'phone_number',
            'avatar_url',
            'is_verified',
            'patient_profile',
        ]
        read_only_fields = fields


class PasswordSetupTokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class SuccessMessageResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    message = serializers.CharField()


class AccountCreationResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    email = serializers.EmailField()


class LoginResponseSerializer(SuccessMessageResponseSerializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class TokenRefreshResponseSerializer(SuccessMessageResponseSerializer):
    access = serializers.CharField()


class UserProfileSerializer(serializers.ModelSerializer):
    doctor_profile = AccountDoctorProfileSerializer(read_only=True)
    patient_profile = PatientProfileSerializer(read_only=True)
    avatar = AvatarImageField(write_only=True, required=False, allow_null=True)
    avatar_url = serializers.URLField(read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'first_name',
            'last_name',
            'role',
            'phone_number',
            'avatar',
            'avatar_url',
            'is_verified',
            'doctor_profile',
            'patient_profile',
        ]
        read_only_fields = ['id', 'email', 'role', 'is_verified']

    def validate_avatar(self, image):
        if image is None:
            return image

        if image.image.format not in ALLOWED_AVATAR_FORMATS:
            raise ValidationError('Use a JPEG, PNG, or WebP image.')

        width, height = image.image.size
        if width > MAX_AVATAR_DIMENSION or height > MAX_AVATAR_DIMENSION:
            raise ValidationError('Avatar dimensions must not exceed 4096 by 4096 pixels.')

        return image

    def update(self, instance, validated_data):
        avatar = validated_data.pop('avatar', serializers.empty)
        previous_public_id = instance.avatar_public_id
        uploaded_public_id = None

        if avatar is not serializers.empty:
            if avatar is None:
                validated_data['avatar_url'] = None
                validated_data['avatar_public_id'] = None
            else:
                avatar_url, uploaded_public_id = upload_avatar(avatar, instance.pk)
                validated_data['avatar_url'] = avatar_url
                validated_data['avatar_public_id'] = uploaded_public_id

        try:
            updated_user = super().update(instance, validated_data)
        except DatabaseError:
            if uploaded_public_id:
                try:
                    delete_avatar(uploaded_public_id, instance.pk)
                except AvatarStorageUnavailable:
                    logger.exception(
                        'Could not clean up failed avatar upload for user %s.',
                        instance.pk,
                    )
            raise

        if previous_public_id and previous_public_id != updated_user.avatar_public_id:
            try:
                delete_avatar(previous_public_id, instance.pk)
            except AvatarStorageUnavailable:
                logger.exception(
                    'Could not remove replaced avatar for user %s.',
                    instance.pk,
                )

        return updated_user