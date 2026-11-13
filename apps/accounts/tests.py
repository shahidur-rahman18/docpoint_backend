from datetime import timedelta
from io import BytesIO, StringIO
import os
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import mail
from django.core.management import call_command, CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from PIL import Image

from apps.accounts.models import CustomUser, EmailVerificationOTP, PatientProfile
from apps.accounts.emails import send_password_reset_email, send_patient_verification_otp
from apps.accounts.serializers import (
    AdminDoctorCreateSerializer,
    EmailVerifySerializer,
    ForgotPasswordSerializer,
    PatientSignupSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    ResendOTPSerializer,
)
from apps.accounts.views import (
    EmailVerifyView,
    ForgotPasswordView,
    MedicalRecordDetailView,
    MedicalRecordListCreateView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PatientProfileView,
    PatientSignupView,
    ResendOTPView,
)
from apps.appointments.models import Appointment
from apps.doctors.models import DoctorProfile
from apps.accounts.models import MedicalRecord


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sender@example.com',
)
class AccountEmailTests(SimpleTestCase):
    def test_sends_patient_verification_otp(self):
        send_patient_verification_otp('patient@example.com', '123456')

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['patient@example.com'])
        self.assertIn('123456', mail.outbox[0].body)
        self.assertIn('10 minutes', mail.outbox[0].body)

    def test_sends_password_reset_link(self):
        reset_url = 'https://frontend.example/reset-password?uid=1&token=abc'
        send_password_reset_email('patient@example.com', reset_url)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['patient@example.com'])
        self.assertIn(reset_url, mail.outbox[0].body)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sender@example.com',
    FRONTEND_URL='https://frontend.example',
)
class PatientAccountSerializerTests(TestCase):
    def test_signup_applies_django_password_validators(self):
        serializer = PatientSignupSerializer(data={
            'email': 'patient@example.com',
            'password': 'patient@example.com',
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('password', serializer.errors)

    def test_signup_creates_inactive_patient_and_sends_otp(self):
        serializer = PatientSignupSerializer(data={
            'email': 'patient@example.com',
            'password': 'Safe-patient-password-123',
            'first_name': 'Patient',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        user = serializer.save()

        self.assertEqual(user.role, CustomUser.Role.PATIENT)
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_verified)
        self.assertTrue(user.patient_profile)
        otp = EmailVerificationOTP.objects.get(user=user)
        self.assertEqual(len(otp.otp_code), 6)
        self.assertIn(otp.otp_code, mail.outbox[0].body)

    def test_email_verification_activates_user_and_returns_tokens(self):
        user, otp = self.create_unverified_patient()
        serializer = EmailVerifySerializer(data={
            'email': user.email,
            'otp': otp.otp_code,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        tokens = serializer.save()

        user.refresh_from_db()
        otp.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_verified)
        self.assertTrue(otp.is_used)
        self.assertEqual(set(tokens), {'access', 'refresh'})

    def test_expired_otp_is_rejected(self):
        user, otp = self.create_unverified_patient()
        otp.expires_at = timezone.now() - timedelta(minutes=1)
        otp.save(update_fields=['expires_at'])

        serializer = EmailVerifySerializer(data={
            'email': user.email,
            'otp': otp.otp_code,
        })
        self.assertFalse(serializer.is_valid())

    def test_resend_invalidates_previous_otp(self):
        user, old_otp = self.create_unverified_patient()
        serializer = ResendOTPSerializer(data={'email': user.email})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        old_otp.refresh_from_db()
        self.assertTrue(old_otp.is_used)
        self.assertEqual(EmailVerificationOTP.objects.filter(user=user, is_used=False).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_forgot_password_sends_reset_link_and_reset_changes_password(self):
        user = CustomUser.objects.create_user(
            email='patient@example.com',
            password='Old-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )
        forgot = ForgotPasswordSerializer(data={'email': user.email})
        self.assertTrue(forgot.is_valid(), forgot.errors)
        forgot.save()

        self.assertEqual(len(mail.outbox), 1)
        reset_url = next(
            line for line in mail.outbox[0].body.splitlines()
            if line.startswith('https://frontend.example/reset-password?')
        )
        query = parse_qs(urlsplit(reset_url).query)
        reset = PasswordResetConfirmSerializer(data={
            'uid': query['uid'][0],
            'token': query['token'][0],
            'new_password': 'New-patient-password-456',
        })
        self.assertTrue(reset.is_valid(), reset.errors)
        reset.save()

        user.refresh_from_db()
        self.assertTrue(user.check_password('New-patient-password-456'))

    def test_password_change_checks_current_password(self):
        user = CustomUser.objects.create_user(
            email='patient@example.com',
            password='Old-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )
        request = type('Request', (), {'user': user})()
        serializer = PasswordChangeSerializer(
            data={
                'old_password': 'Old-patient-password-123',
                'new_password': 'New-patient-password-456',
            },
            context={'request': request},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()

        user.refresh_from_db()
        self.assertTrue(user.check_password('New-patient-password-456'))

    def create_unverified_patient(self):
        user = CustomUser.objects.create_user(
            email='patient@example.com',
            password='Safe-patient-password-123',
            role=CustomUser.Role.PATIENT,
            is_active=False,
            is_verified=False,
        )
        otp = EmailVerificationOTP.objects.create(
            user=user,
            otp_code='123456',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        return user, otp


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sender@example.com',
    FRONTEND_URL='https://frontend.example',
)
class PatientAccountViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = CustomUser.objects.create_user(
            email='patient@example.com',
            password='Old-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )

    def call_view(self, view, method='post', data=None, user=None, **kwargs):
        request = getattr(self.factory, method)(
            '/api/accounts/patient/',
            data=data or {},
            format='json',
        )
        if user is not None:
            force_authenticate(request, user=user)
        return view.as_view()(request, **kwargs)

    def test_signup_view_returns_created(self):
        response = self.call_view(PatientSignupView, data={
            'email': 'new-patient@example.com',
            'password': 'Safe-patient-password-123',
        })

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            CustomUser.objects.filter(
                email='new-patient@example.com',
                is_active=False,
            ).exists()
        )

    def test_email_verify_view_sets_auth_cookies(self):
        user = CustomUser.objects.create_user(
            email='unverified@example.com',
            password='Safe-patient-password-123',
            role=CustomUser.Role.PATIENT,
            is_active=False,
            is_verified=False,
        )
        otp = EmailVerificationOTP.objects.create(
            user=user,
            otp_code='123456',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        response = self.call_view(EmailVerifyView, data={
            'email': user.email,
            'otp': otp.otp_code,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('access_token', response.cookies)
        self.assertIn('refresh_token', response.cookies)

    def test_resend_otp_view_returns_generic_response(self):
        response = self.call_view(ResendOTPView, data={'email': 'unknown@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['message'],
            'If the account needs verification, a code has been sent.',
        )

    def test_forgot_password_view_returns_generic_response(self):
        response = self.call_view(ForgotPasswordView, data={'email': 'unknown@example.com'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['message'],
            'If the account exists, a password reset email has been sent.',
        )

    def test_password_reset_confirm_view_resets_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        response = self.call_view(PasswordResetConfirmView, data={
            'uid': uid,
            'token': token,
            'new_password': 'New-patient-password-456',
        })

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-patient-password-456'))

    def test_password_change_view_requires_authentication_and_changes_password(self):
        unauthenticated = self.call_view(PasswordChangeView, data={})
        self.assertEqual(unauthenticated.status_code, 401)

        response = self.call_view(
            PasswordChangeView,
            data={
                'old_password': 'Old-patient-password-123',
                'new_password': 'New-patient-password-456',
            },
            user=self.user,
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-patient-password-456'))

    def test_patient_profile_view_updates_only_own_profile(self):
        response = self.call_view(
            PatientProfileView,
            method='patch',
            data={'address': 'Dhaka'},
            user=self.user,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.patient_profile.address, 'Dhaka')

    def test_medical_record_views_scope_records_to_authenticated_patient(self):
        response = self.call_view(
            MedicalRecordListCreateView,
            data={
                'title': 'Blood test',
                'recorded_at': '2026-11-08',
                'document_url': 'https://example.com/report.pdf',
            },
            user=self.user,
        )

        self.assertEqual(response.status_code, 201)
        record = MedicalRecord.objects.get()
        self.assertEqual(record.patient, self.user.patient_profile)

        detail = self.call_view(
            MedicalRecordDetailView,
            method='get',
            user=self.user,
            pk=record.pk,
        )
        self.assertEqual(detail.status_code, 200)

        other_user = CustomUser.objects.create_user(
            email='other-patient@example.com',
            password='Other-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )
        other_detail = self.call_view(
            MedicalRecordDetailView,
            method='get',
            user=other_user,
            pk=record.pk,
        )
        self.assertEqual(other_detail.status_code, 404)


class PatientProfileApiTests(TestCase):
    def setUp(self):
        self.patient = CustomUser.objects.create_user(
            email='profile-api-patient@example.com',
            password='Safe-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )
        self.other_patient = CustomUser.objects.create_user(
            email='profile-api-other@example.com',
            password='Safe-patient-password-123',
            role=CustomUser.Role.PATIENT,
        )
        self.doctor = CustomUser.objects.create_user(
            email='profile-api-doctor@example.com',
            password='Safe-doctor-password-123',
            role=CustomUser.Role.DOCTOR,
        )
        self.admin = CustomUser.objects.create_user(
            email='profile-api-admin@example.com',
            password='Safe-admin-password-123',
            role=CustomUser.Role.ADMIN,
        )
        self.client = APIClient()
        self.url = reverse('patient_profile')

    def test_patient_can_view_own_incomplete_profile_and_missing_fields(self):
        self.client.force_authenticate(user=self.patient)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['profile_complete'])
        self.assertEqual(
            set(response.data['missing_fields']),
            {'date_of_birth', 'gender', 'blood_group', 'address'},
        )

    def test_patient_can_update_profile_and_complete_missing_fields(self):
        self.client.force_authenticate(user=self.patient)

        response = self.client.patch(
            self.url,
            {
                'date_of_birth': '1990-01-01',
                'gender': PatientProfile.Gender.OTHER,
                'blood_group': PatientProfile.BloodGroup.UNKNOWN,
                'address': 'Dhaka',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['profile_complete'])
        self.assertEqual(response.data['missing_fields'], [])
        self.assertEqual(response.data['blood_group'], PatientProfile.BloodGroup.UNKNOWN)
        self.patient.patient_profile.refresh_from_db()
        self.assertTrue(self.patient.patient_profile.is_complete)

    def test_patient_can_replace_profile_with_put_and_receive_completion_metadata(self):
        self.client.force_authenticate(user=self.patient)

        payload = {
            'date_of_birth': '1990-01-01',
            'gender': PatientProfile.Gender.OTHER,
            'blood_group': PatientProfile.BloodGroup.UNKNOWN,
            'address': 'Dhaka',
        }
        response = self.client.put(self.url, payload, format='json')

        self.assertEqual(response.status_code, 200)
        for field, value in payload.items():
            with self.subTest(field=field):
                self.assertEqual(response.data[field], value)
        self.assertTrue(response.data['profile_complete'])
        self.assertEqual(response.data['missing_fields'], [])

    def test_incomplete_put_returns_missing_fields_and_invalid_put_is_rejected(self):
        self.client.force_authenticate(user=self.patient)

        incomplete_response = self.client.put(
            self.url,
            {'date_of_birth': '1990-01-01'},
            format='json',
        )

        self.assertEqual(incomplete_response.status_code, 200)
        self.assertFalse(incomplete_response.data['profile_complete'])
        self.assertEqual(
            set(incomplete_response.data['missing_fields']),
            {'gender', 'blood_group', 'address'},
        )
        self.assertEqual(incomplete_response.data['date_of_birth'], '1990-01-01')

        invalid_response = self.client.put(
            self.url,
            {
                'date_of_birth': '1990-01-01',
                'gender': 'INVALID',
                'blood_group': PatientProfile.BloodGroup.UNKNOWN,
                'address': 'Dhaka',
            },
            format='json',
        )

        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn('gender', invalid_response.data)
        self.patient.patient_profile.refresh_from_db()
        self.assertEqual(self.patient.patient_profile.date_of_birth.isoformat(), '1990-01-01')
        self.assertEqual(self.patient.patient_profile.gender, '')
        self.assertEqual(self.patient.patient_profile.address, '')

    def test_partial_profile_update_keeps_existing_fields_and_returns_remaining_missing(self):
        profile = self.patient.patient_profile
        profile.address = 'Existing address'
        profile.save(update_fields=['address'])
        self.client.force_authenticate(user=self.patient)

        response = self.client.patch(
            self.url,
            {'gender': PatientProfile.Gender.FEMALE},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['profile_complete'])
        self.assertEqual(
            set(response.data['missing_fields']),
            {'date_of_birth', 'blood_group'},
        )
        self.assertEqual(response.data['address'], 'Existing address')

    def test_profile_endpoint_does_not_expose_another_patients_profile(self):
        self.client.force_authenticate(user=self.patient)

        response = self.client.get(
            self.url,
            {'patient': self.other_patient.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.patient.patient_profile.pk)
        self.assertNotEqual(response.data['id'], self.other_patient.patient_profile.pk)

    def test_unauthenticated_profile_request_is_rejected(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_profile_rejects_protected_ownership_fields(self):
        self.client.force_authenticate(user=self.patient)

        for field, value in (
            ('user', self.other_patient.pk),
            ('role', CustomUser.Role.ADMIN),
            ('patient', self.other_patient.pk),
        ):
            with self.subTest(field=field):
                response = self.client.patch(
                    self.url,
                    {field: value},
                    format='json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)
                self.patient.patient_profile.refresh_from_db()
                self.assertEqual(
                    self.patient.patient_profile.user_id,
                    self.patient.pk,
                )

    def test_future_or_invalid_date_of_birth_is_rejected(self):
        self.client.force_authenticate(user=self.patient)
        future_response = self.client.patch(
            self.url,
            {'date_of_birth': (timezone.localdate() + timedelta(days=1)).isoformat()},
            format='json',
        )
        malformed_response = self.client.patch(
            self.url,
            {'date_of_birth': 'not-a-date'},
            format='json',
        )

        self.assertEqual(future_response.status_code, 400)
        self.assertEqual(malformed_response.status_code, 400)
        self.patient.patient_profile.refresh_from_db()
        self.assertIsNone(self.patient.patient_profile.date_of_birth)

    def test_invalid_gender_and_blood_group_are_rejected(self):
        self.client.force_authenticate(user=self.patient)

        invalid_gender = self.client.patch(
            self.url,
            {'gender': 'UNKNOWN'},
            format='json',
        )
        invalid_blood_group = self.client.patch(
            self.url,
            {'blood_group': 'X+'},
            format='json',
        )

        self.assertEqual(invalid_gender.status_code, 400)
        self.assertEqual(invalid_blood_group.status_code, 400)

    def test_doctor_and_admin_cannot_use_patient_profile_api(self):
        for user in (self.doctor, self.admin):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 403)


class AvatarProfileApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(
            email='avatar@example.com',
            password='avatar-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.url = reverse('user_profile')
        self.client.force_authenticate(user=self.user)

    @staticmethod
    def create_image(name='avatar.png', image_format='PNG', size=(32, 32)):
        buffer = BytesIO()
        Image.new('RGB', size, color='blue').save(buffer, format=image_format)
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type=f'image/{image_format.lower()}',
        )

    def test_profile_returns_avatar_url(self):
        self.user.avatar_url = 'https://res.cloudinary.com/demo/image/upload/avatar.png'
        self.user.save(update_fields=['avatar_url'])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['avatar_url'], self.user.avatar_url)
        self.assertNotIn('avatar_public_id', response.data)

    @override_settings(
        CLOUDINARY_CLOUD_NAME='test-cloud',
        CLOUDINARY_API_KEY='test-key',
        CLOUDINARY_API_SECRET='test-secret',
    )
    @patch('apps.accounts.avatar_storage.uploader.destroy')
    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_upload_replaces_avatar_and_removes_previous_cloud_asset(
        self,
        upload,
        destroy,
    ):
        self.user.avatar_public_id = 'docpoint/avatars/old-avatar'
        self.user.avatar_url = 'https://res.cloudinary.com/demo/image/upload/old.png'
        self.user.save(update_fields=['avatar_public_id', 'avatar_url'])
        upload.return_value = {
            'secure_url': 'https://res.cloudinary.com/demo/image/upload/new.png',
            'public_id': 'docpoint/avatars/new-avatar',
        }
        destroy.return_value = {'result': 'ok'}

        response = self.client.patch(
            self.url,
            {'avatar': self.create_image()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.avatar_url, upload.return_value['secure_url'])
        self.assertEqual(self.user.avatar_public_id, upload.return_value['public_id'])
        upload.assert_called_once()
        destroy.assert_called_once_with(
            'docpoint/avatars/old-avatar',
            resource_type='image',
            invalidate=True,
            timeout=20,
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME='test-cloud',
        CLOUDINARY_API_KEY='test-key',
        CLOUDINARY_API_SECRET='test-secret',
    )
    @patch('apps.accounts.avatar_storage.uploader.destroy')
    def test_null_avatar_removes_image(self, destroy):
        self.user.avatar_public_id = 'docpoint/avatars/avatar'
        self.user.avatar_url = 'https://res.cloudinary.com/demo/image/upload/avatar.png'
        self.user.save(update_fields=['avatar_public_id', 'avatar_url'])
        destroy.return_value = {'result': 'ok'}

        response = self.client.patch(self.url, {'avatar': None}, format='json')

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.avatar_url)
        self.assertIsNone(self.user.avatar_public_id)
        destroy.assert_called_once()

    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_rejects_non_image_upload(self, upload):
        response = self.client.patch(
            self.url,
            {'avatar': SimpleUploadedFile('not-image.png', b'not an image')},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('avatar', response.data)
        upload.assert_not_called()

    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_rejects_oversized_upload(self, upload):
        image = SimpleUploadedFile('large.png', b'x' * (5 * 1024 * 1024 + 1))

        response = self.client.patch(
            self.url,
            {'avatar': image},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('5 MB or smaller', str(response.data))
        upload.assert_not_called()

    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_rejects_disallowed_image_format(self, upload):
        response = self.client.patch(
            self.url,
            {'avatar': self.create_image(image_format='GIF')},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('JPEG, PNG, or WebP', str(response.data))
        upload.assert_not_called()

    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_rejects_image_with_excessive_dimensions(self, upload):
        response = self.client.patch(
            self.url,
            {'avatar': self.create_image(size=(4097, 32))},
            format='multipart',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('4096 by 4096', str(response.data))
        upload.assert_not_called()

    @override_settings(
        CLOUDINARY_CLOUD_NAME='',
        CLOUDINARY_API_KEY='',
        CLOUDINARY_API_SECRET='',
    )
    @patch('apps.accounts.avatar_storage.uploader.upload')
    def test_reports_unavailable_storage_when_credentials_are_missing(self, upload):
        response = self.client.patch(
            self.url,
            {'avatar': self.create_image()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 503)
        upload.assert_not_called()

    def test_avatar_update_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.patch(
            self.url,
            {'avatar': self.create_image()},
            format='multipart',
        )

        self.assertEqual(response.status_code, 401)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sender@example.com',
    FRONTEND_URL='https://frontend.example',
)
class AdminDoctorCreateEmailTests(TestCase):
    def create_doctor(self):
        serializer = AdminDoctorCreateSerializer(data={
            'email': 'doctor@example.com',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.save()

    def test_sends_setup_email_to_configured_frontend(self):
        user = self.create_doctor()

        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(sent_email.from_email, 'sender@example.com')
        self.assertEqual(sent_email.to, [user.email])

        setup_link = next(
            line for line in sent_email.body.splitlines()
            if line.startswith('https://frontend.example/setup-password?')
        )
        query = parse_qs(urlsplit(setup_link).query)
        self.assertEqual(query.keys(), {'uid', 'token'})

    def test_email_failure_is_propagated_and_user_creation_rolled_back(self):
        with patch(
            'apps.accounts.serializers.send_mail',
            side_effect=RuntimeError('SMTP unavailable'),
        ):
            with self.assertRaisesMessage(RuntimeError, 'SMTP unavailable'):
                self.create_doctor()

        self.assertFalse(
            CustomUser.objects.filter(email='doctor@example.com').exists()
        )


class PasswordSetupTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(
            email='doctor@example.com',
            password=None,
            role=CustomUser.Role.DOCTOR,
            is_active=False,
            is_verified=False,
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)
        self.url = reverse('password_setup')

    def submit_setup(self, token=None, password='doctor-password-123'):
        return self.client.post(self.url, {
            'uid': self.uid,
            'token': token or self.token,
            'new_password': password,
        }, format='json')

    def test_valid_setup_activates_doctor_and_returns_jwt_pair(self):
        response = self.submit_setup()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'access', 'refresh'})
        access = AccessToken(response.data['access'])
        refresh = RefreshToken(response.data['refresh'])
        self.assertEqual(str(access['user_id']), str(self.user.pk))
        self.assertEqual(str(refresh['user_id']), str(self.user.pk))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.check_password('doctor-password-123'))

    def test_invalid_setup_token_returns_no_jwt_pair(self):
        response = self.submit_setup(token='invalid-token')

        self.assertEqual(response.status_code, 400)
        self.assertIn('token', response.data)
        self.assertNotIn('access', response.data)
        self.assertNotIn('refresh', response.data)

    def test_non_doctor_account_cannot_receive_setup_tokens(self):
        self.user.role = CustomUser.Role.PATIENT
        self.user.save()
        self.token = default_token_generator.make_token(self.user)

        response = self.submit_setup()

        self.assertEqual(response.status_code, 400)
        self.assertIn('uid', response.data)
        self.assertNotIn('access', response.data)
        self.assertNotIn('refresh', response.data)

    def test_expired_setup_token_returns_no_jwt_pair(self):
        expired_at = timezone.now().replace(tzinfo=None) + timedelta(days=4)
        with patch.object(default_token_generator, '_now', return_value=expired_at):
            response = self.submit_setup()

        self.assertEqual(response.status_code, 400)
        self.assertIn('token', response.data)
        self.assertNotIn('access', response.data)
        self.assertNotIn('refresh', response.data)

    def test_invalid_password_returns_no_jwt_pair(self):
        response = self.submit_setup(password='short')

        self.assertEqual(response.status_code, 400)
        self.assertIn('new_password', response.data)
        self.assertNotIn('access', response.data)
        self.assertNotIn('refresh', response.data)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)


@override_settings(
    DEMO_ADMIN_EMAIL='demo-admin@example.test',
    DEMO_ADMIN_PASSWORD='safe-demo-admin-password',
    DEMO_DOCTOR_EMAIL='demo-doctor@example.test',
    DEMO_DOCTOR_PASSWORD='safe-demo-doctor-password',
    DEBUG=True,
)
class DemoLoginViewTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            email='demo-admin@example.test',
            password='safe-demo-admin-password',
            role=CustomUser.Role.ADMIN,
        )
        self.doctor = CustomUser.objects.create_user(
            email='demo-doctor@example.test',
            password='safe-demo-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.url = reverse('demo_login')

    def test_returns_demo_email_without_exposing_password(self):
        response = self.client.get(self.url, {'role': 'admin'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'email': 'demo-admin@example.test'})
        self.assertNotIn('password', response.data)

    def test_demo_login_returns_tokens_and_http_only_cookies(self):
        response = self.client.post(self.url, {'role': 'doctor'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertTrue(response.cookies['access_token']['httponly'])
        self.assertTrue(response.cookies['refresh_token']['httponly'])
        self.assertEqual(
            AccessToken(response.data['access'])['user_id'],
            str(self.doctor.pk),
        )

    def test_regular_login_still_returns_tokens_and_http_only_cookies(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            {
                'email': 'demo-admin@example.test',
                'password': 'safe-demo-admin-password',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertTrue(response.cookies['access_token']['httponly'])
        self.assertTrue(response.cookies['refresh_token']['httponly'])

    def test_invalid_or_unconfigured_role_does_not_issue_tokens(self):
        for role in ('patient', 'missing'):
            response = self.client.post(self.url, {'role': role}, format='json')
            self.assertEqual(response.status_code, 404)
            self.assertNotIn('access', response.data)

    @override_settings(DEMO_ADMIN_PASSWORD='')
    def test_missing_demo_credentials_are_unavailable(self):
        response = self.client.post(self.url, {'role': 'admin'}, format='json')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('access', response.data)


class SeedDemoDataTests(TestCase):
    seed_environment = {
        "DEMO_ADMIN_EMAIL": "demo-admin@example.test",
        "DEMO_ADMIN_PASSWORD": "safe-demo-admin-password",
        "DEMO_DOCTOR_EMAIL": "demo-doctor@example.test",
        "DEMO_DOCTOR_PASSWORD": "safe-demo-doctor-password",
        "DEMO_PATIENT_EMAIL": "demo-patient@example.test",
    }

    @patch.dict(os.environ, seed_environment)
    @override_settings(DEBUG=False)
    def test_seed_creates_demo_accounts_profiles_and_appointments_idempotently(self):
        call_command("seed_demo_data", "--allow-production", stdout=StringIO())
        call_command("seed_demo_data", "--allow-production", stdout=StringIO())

        admin = CustomUser.objects.get(email=self.seed_environment["DEMO_ADMIN_EMAIL"])
        doctor = CustomUser.objects.get(email=self.seed_environment["DEMO_DOCTOR_EMAIL"])
        patient = CustomUser.objects.get(email=self.seed_environment["DEMO_PATIENT_EMAIL"])

        self.assertEqual(admin.role, CustomUser.Role.ADMIN)
        self.assertTrue(admin.check_password(self.seed_environment["DEMO_ADMIN_PASSWORD"]))
        self.assertTrue(admin.is_active)
        self.assertTrue(admin.is_verified)
        self.assertFalse(admin.is_superuser)
        self.assertEqual(doctor.role, CustomUser.Role.DOCTOR)
        self.assertTrue(doctor.check_password(self.seed_environment["DEMO_DOCTOR_PASSWORD"]))
        self.assertTrue(doctor.is_active)
        self.assertTrue(doctor.is_verified)
        self.assertTrue(DoctorProfile.objects.filter(user=doctor).exists())
        self.assertTrue(patient.patient_profile)
        self.assertFalse(patient.has_usable_password())
        self.assertTrue(admin.avatar_url.startswith("https://images.unsplash.com/"))
        self.assertTrue(doctor.avatar_url.startswith("https://images.unsplash.com/"))
        self.assertTrue(patient.avatar_url.startswith("https://images.unsplash.com/"))
        self.assertEqual(CustomUser.objects.count(), 41)
        self.assertEqual(DoctorProfile.objects.count(), 20)
        self.assertEqual(PatientProfile.objects.count(), 20)
        self.assertEqual(Appointment.objects.count(), 20)
        self.assertEqual(
            Appointment.objects.filter(doctor=doctor.doctor_profile).count(),
            20,
        )

    @override_settings(DEBUG=False)
    def test_production_database_requires_explicit_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--allow-production"):
            call_command("seed_demo_data")

        self.assertEqual(CustomUser.objects.count(), 0)

    @override_settings(
        DEBUG=True,
        DATABASES={"default": {"HOST": "remote.example.test"}},
    )
    def test_remote_database_requires_explicit_confirmation_even_with_debug(self):
        with self.assertRaisesMessage(CommandError, "--allow-production"):
            call_command("seed_demo_data")


class AdminPatientApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = CustomUser.objects.create_user(
            email="admin-patients@example.com",
            password="safe-admin-password",
            role=CustomUser.Role.ADMIN,
        )
        self.patient = CustomUser.objects.create_user(
            email="patient-details@example.com",
            password=None,
            role=CustomUser.Role.PATIENT,
            first_name="Patient",
            last_name="Example",
            phone_number="+1 555 0100",
        )
        self.patient.patient_profile.date_of_birth = "1990-01-25"
        self.patient.patient_profile.gender = PatientProfile.Gender.FEMALE
        self.patient.patient_profile.blood_group = PatientProfile.BloodGroup.A_POS
        self.patient.patient_profile.address = "123 Main Street"
        self.patient.patient_profile.save()

    def test_admin_can_list_patients_and_view_patient_details(self):
        self.client.force_authenticate(user=self.admin)

        list_response = self.client.get(reverse("admin_patient_list"))
        detail_response = self.client.get(
            reverse("admin_patient_detail", args=[self.patient.pk])
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["id"], self.patient.pk)
        self.assertEqual(
            detail_response.data["patient_profile"]["address"],
            "123 Main Street",
        )
        self.assertEqual(
            detail_response.data["patient_profile"]["blood_group"],
            PatientProfile.BloodGroup.A_POS,
        )

    def test_only_admin_can_access_patient_details(self):
        self.client.force_authenticate(user=self.patient)

        response = self.client.get(
            reverse("admin_patient_detail", args=[self.patient.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_patient_detail_endpoint_does_not_return_non_patient_accounts(self):
        self.client.force_authenticate(user=self.admin)
        doctor = CustomUser.objects.create_user(
            email="not-a-patient@example.com",
            password="safe-doctor-password",
            role=CustomUser.Role.DOCTOR,
        )

        response = self.client.get(reverse("admin_patient_detail", args=[doctor.pk]))

        self.assertEqual(response.status_code, 404)


class SeedDemoPatientsTests(TestCase):
    @override_settings(DEBUG=True, DATABASES={"default": {"HOST": "localhost"}})
    def test_seed_creates_twenty_patients_idempotently_without_doctors_or_appointments(self):
        call_command("seed_demo_patients", stdout=StringIO())
        call_command("seed_demo_patients", stdout=StringIO())

        patients = CustomUser.objects.filter(role=CustomUser.Role.PATIENT)
        self.assertEqual(patients.count(), 20)
        self.assertEqual(PatientProfile.objects.count(), 20)
        self.assertEqual(DoctorProfile.objects.count(), 0)
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertTrue(
            patients.filter(email="alberto.ripley@demo-patients.docpoint.test").exists()
        )
        self.assertTrue(
            PatientProfile.objects.filter(
                user__email="alberto.ripley@demo-patients.docpoint.test",
                address="4150 Hiney Road, Las Vegas, Nevada 89109",
                blood_group=PatientProfile.BloodGroup.O_POS,
            ).exists()
        )

    @override_settings(
        DEBUG=True,
        DATABASES={"default": {"HOST": "remote.example.test"}},
    )
    def test_remote_seed_requires_explicit_confirmation(self):
        with self.assertRaisesMessage(CommandError, "--allow-remote"):
            call_command("seed_demo_patients")
