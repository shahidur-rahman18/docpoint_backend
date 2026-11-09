from django.db import models
from django.db.models import F, Q
from django.conf import settings


WEEKDAYS = (
    'MONDAY',
    'TUESDAY',
    'WEDNESDAY',
    'THURSDAY',
    'FRIDAY',
    'SATURDAY',
    'SUNDAY',
)


def empty_weekly_schedule():
    return {day: [] for day in WEEKDAYS}


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


class DoctorSchedule(models.Model):
    doctor = models.OneToOneField(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name='schedule',
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_until = models.DateField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default='Asia/Dhaka')
    weekly_schedule = models.JSONField(default=empty_weekly_schedule)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(effective_until__isnull=True)
                    | Q(effective_from__isnull=True)
                    | Q(effective_until__gte=F('effective_from'))
                ),
                name='doctor_schedule_valid_effective_dates',
            ),
        ]

    def __str__(self):
        return f"Schedule for {self.doctor}"