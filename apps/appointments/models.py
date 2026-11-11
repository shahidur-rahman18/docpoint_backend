from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.doctors.models import Chamber, DoctorProfile


class Appointment(models.Model):
    class AppointmentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        COMPLETED = 'COMPLETED', 'Completed'

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='patient_appointments',
        limit_choices_to={'role': 'PATIENT'}
    )
    doctor = models.ForeignKey(
        DoctorProfile,
        on_delete=models.CASCADE,
        related_name='doctor_appointments'
    )
    chamber = models.ForeignKey(
        Chamber,
        on_delete=models.SET_NULL,
        related_name='appointments',
        null=True,
        blank=True,
    )
    appointment_date = models.DateField(db_index=True)
    time_slot_start = models.TimeField()
    time_slot_end = models.TimeField()
    symptoms_note = models.TextField(blank=True, null=True)
    total_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    # --- আপডেটকৃত অংশ (db_index=True যুক্ত করা হলো) ---
    status = models.CharField(
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING,
        db_index=True # ১. স্ট্যাটাস ফিল্ড দিয়ে দ্রুত ফিল্টারিংয়ের জন্য ইনডেক্স যুক্ত করা হলো
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-appointment_date', '-time_slot_start']
        constraints = [
            models.UniqueConstraint(
                fields=['doctor', 'appointment_date', 'time_slot_start'],
                condition=Q(status__in=['PENDING', 'CONFIRMED']),
                name='appt_active_doctor_slot_unique',
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.chamber_id is not None
            and self.doctor_id is not None
            and self.chamber.doctor_id != self.doctor_id
        ):
            raise ValidationError({
                'chamber': 'The chamber must belong to the selected doctor.',
            })

    def __str__(self):
        return (
            f"Appointment #{self.id} - Patient: {self.patient.email} with "
            f"Dr. {self.doctor.user.email} on {self.appointment_date}"
        )