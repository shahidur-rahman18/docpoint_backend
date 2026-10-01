from django.db import models
from django.conf import settings


class DoctorProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_profile'
    )
    specialization = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    qualification = models.CharField(max_length=255, blank=True, null=True)
    consultation_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00
    )
    chamber_address = models.TextField(blank=True, null=True)
    is_available = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"Dr. {self.user.email} - {self.specialization or 'General'}"