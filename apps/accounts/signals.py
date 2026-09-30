from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from apps.doctors.models import DoctorProfile
from apps.accounts.models import PatientProfile

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        if instance.role == User.Role.DOCTOR:
            DoctorProfile.objects.create(user=instance)
        elif instance.role == User.Role.PATIENT:
            PatientProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if instance.role == User.Role.DOCTOR and hasattr(instance, 'doctor_profile'):
        instance.doctor_profile.save()
    elif instance.role == User.Role.PATIENT and hasattr(instance, 'patient_profile'):
        instance.patient_profile.save()