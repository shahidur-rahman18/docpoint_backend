from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from PIL import Image

from apps.accounts.models import CustomUser
from apps.accounts.serializers import AdminDoctorCreateSerializer


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
