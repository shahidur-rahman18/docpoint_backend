from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


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


class Chamber(models.Model):
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name='chambers',
    )
    name = models.CharField(max_length=120)
    address = models.TextField()
    is_active = models.BooleanField(default=True, db_index=True)

    def __str__(self):
        return f"{self.name} - Dr. {self.doctor.user.email}"


class ChamberSchedule(models.Model):
    class Weekday(models.IntegerChoices):
        SATURDAY = 5, 'Saturday'
        SUNDAY = 6, 'Sunday'
        MONDAY = 0, 'Monday'
        TUESDAY = 1, 'Tuesday'
        WEDNESDAY = 2, 'Wednesday'
        THURSDAY = 3, 'Thursday'
        FRIDAY = 4, 'Friday'

    chamber = models.ForeignKey(
        Chamber,
        on_delete=models.PROTECT,
        related_name='schedules',
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    appointment_duration_minutes = models.PositiveSmallIntegerField()
    effective_from = models.DateField(default=timezone.localdate)
    effective_until = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(weekday__gte=0) & Q(weekday__lte=6),
                name='doc_ch_sched_weekday_range',
            ),
            models.CheckConstraint(
                condition=Q(start_time__lt=F('end_time')),
                name='doc_ch_sched_times_order',
            ),
            models.CheckConstraint(
                condition=Q(appointment_duration_minutes__gt=0),
                name='doc_ch_sched_duration_gt0',
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True)
                | Q(effective_until__gte=F('effective_from')),
                name='doc_ch_sched_effective_dates',
            ),
            models.UniqueConstraint(
                fields=(
                    'chamber',
                    'weekday',
                    'start_time',
                    'end_time',
                    'effective_from',
                ),
                name='doc_ch_sched_unique_shift',
                condition=Q(is_active=True),
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.chamber_id is None
            or self.weekday is None
            or self.start_time is None
            or self.end_time is None
            or self.effective_from is None
            or not self.is_active
            or self.start_time >= self.end_time
            or (
                self.effective_until is not None
                and self.effective_until < self.effective_from
            )
        ):
            return

        schedules = ChamberSchedule.objects.filter(
            chamber__doctor_id=self.chamber.doctor_id,
            weekday=self.weekday,
            is_active=True,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        )
        if self.pk is not None:
            schedules = schedules.exclude(pk=self.pk)

        schedules = schedules.filter(
            Q(effective_until__isnull=True)
            | Q(effective_until__gte=self.effective_from)
        )
        if self.effective_until is not None:
            schedules = schedules.filter(effective_from__lte=self.effective_until)

        if schedules.exists():
            raise ValidationError({
                'start_time': 'This schedule overlaps another effective schedule for this chamber and weekday.',
            })

    def __str__(self):
        return (
            f"{self.chamber} - {self.get_weekday_display()} "
            f"{self.start_time}-{self.end_time}"
        )