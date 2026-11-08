from django.conf import settings
from django.core.mail import send_mail


def send_patient_verification_otp(email, otp_code):
    send_mail(
        'Verify your DocPoint email',
        f'Your verification code is {otp_code}. It expires in 10 minutes.',
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )


def send_password_reset_email(email, reset_url):
    send_mail(
        'Reset your DocPoint password',
        f'Use the link below to reset your password:\n{reset_url}',
        settings.DEFAULT_FROM_EMAIL,
        [email],
    )
