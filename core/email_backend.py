import json
import urllib.error
import urllib.request
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    api_url = 'https://api.resend.com/emails'

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = settings.RESEND_API_KEY

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            raise ImproperlyConfigured('RESEND_API_KEY must be configured to send email.')

        sent_count = 0
        for message in email_messages:
            recipients = message.recipients()
            if not recipients:
                continue

            payload = {
                'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
                'to': message.to,
                'subject': message.subject,
                'text': message.body,
            }
            if message.cc:
                payload['cc'] = message.cc
            if message.bcc:
                payload['bcc'] = message.bcc
            if message.reply_to:
                payload['reply_to'] = message.reply_to

            for content, mimetype in message.alternatives:
                if mimetype == 'text/html':
                    payload['html'] = content
            request = urllib.request.Request(
                self.api_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                },
                method='POST',
            )
            try:
                with urllib.request.urlopen(request, timeout=10):
                    sent_count += 1
            except urllib.error.HTTPError as e:
                # Resend থেকে আসা আসল এরর মেসেজ কনসোলে প্রিন্ট করার জন্য
                error_body = e.read().decode('utf-8')
                print(f"\n[Resend API Error] Code {e.code}: {error_body}\n")
                if not self.fail_silently:
                    raise
            except Exception as e:
                print(f"\n[Email Backend Error]: {str(e)}\n")
                if not self.fail_silently:
                    raise

        return sent_count