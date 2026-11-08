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
