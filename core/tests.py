import json
from unittest.mock import Mock, patch
from urllib.request import Request

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from core.email_backend import ResendEmailBackend


@override_settings(
    RESEND_API_KEY='re_test_key',
    DEFAULT_FROM_EMAIL='DocPoint <no-reply@example.com>',
)
class ResendEmailBackendTests(SimpleTestCase):
    @patch('core.email_backend.urlopen')
    def test_sends_email_using_resend_api(self, urlopen):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        urlopen.return_value = response
        message = EmailMultiAlternatives(
            'Welcome',
            'Set up your password',
            to=['doctor@example.com'],
            cc=['admin@example.com'],
            reply_to=['support@example.com'],
        )
        message.attach_alternative('<p>Set up your password</p>', 'text/html')

        sent_count = ResendEmailBackend().send_messages([message])

        self.assertEqual(sent_count, 1)
        request = urlopen.call_args.args[0]
        self.assertIsInstance(request, Request)
        self.assertEqual(request.full_url, 'https://api.resend.com/emails')
        self.assertEqual(request.get_header('Authorization'), 'Bearer re_test_key')
        self.assertEqual(json.loads(request.data), {
            'from': 'DocPoint <no-reply@example.com>',
            'to': ['doctor@example.com'],
            'subject': 'Welcome',
            'text': 'Set up your password',
            'cc': ['admin@example.com'],
            'reply_to': ['support@example.com'],
            'html': '<p>Set up your password</p>',
        })
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 10)

    @override_settings(RESEND_API_KEY='')
    def test_requires_api_key(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            'RESEND_API_KEY must be configured to send email.',
        ):
            ResendEmailBackend().send_messages([
                EmailMultiAlternatives(
                    'Welcome',
                    'Set up your password',
                    to=['doctor@example.com'],
                ),
            ])
