from django.db import models
from django.conf import settings
from apps.doctors.models import DoctorProfile

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
    appointment_date = models.DateField(db_index=True)
    time_slot_start = models.TimeField()
    time_slot_end = models.TimeField()
    symptoms_note = models.TextField(blank=True, null=True)
    total_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AppointmentStatus.choices,
        default=AppointmentStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-appointment_date', '-time_slot_start']

    def __str__(self):
        return f"Appointment #{self.id} - Patient: {self.patient.email} with Dr. {self.doctor.user.email} on {self.appointment_date}"