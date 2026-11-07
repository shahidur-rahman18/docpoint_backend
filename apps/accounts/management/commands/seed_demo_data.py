import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import PatientProfile
from apps.appointments.models import Appointment
from apps.doctors.models import DoctorProfile


DEMO_APPOINTMENT_NOTE = "Demo appointment seeded by seed_demo_data."


class Command(BaseCommand):
    help = (
        "Create or update demo admin, doctor, patient, and appointment records. "
        "Set DEMO_ADMIN_PASSWORD and DEMO_DOCTOR_PASSWORD in the environment."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Confirm that demo data should be written to a non-debug database.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "DEBUG is False. To seed this database intentionally, rerun with "
                "--allow-production."
            )

        admin_email = self._required_env("DEMO_ADMIN_EMAIL")
        admin_password = self._required_env("DEMO_ADMIN_PASSWORD")
        doctor_email = self._required_env("DEMO_DOCTOR_EMAIL")
        doctor_password = self._required_env("DEMO_DOCTOR_PASSWORD")
        patient_email = self._required_env("DEMO_PATIENT_EMAIL")

        user_model = get_user_model()
        try:
            with transaction.atomic():
                admin_user, admin_created = self._upsert_user(
                    user_model,
                    email=admin_email,
                    password=admin_password,
                    role=user_model.Role.ADMIN,
                    first_name="Demo",
                    last_name="Admin",
                )
                doctor_user, doctor_created = self._upsert_user(
                    user_model,
                    email=doctor_email,
                    password=doctor_password,
                    role=user_model.Role.DOCTOR,
                    first_name="Demo",
                    last_name="Doctor",
                )
                patient_user, patient_created = self._upsert_user(
                    user_model,
                    email=patient_email,
                    password=None,
                    role=user_model.Role.PATIENT,
                    first_name="Demo",
                    last_name="Patient",
                )

                doctor_profile, _ = DoctorProfile.objects.update_or_create(
                    user=doctor_user,
                    defaults={
                        "specialization": "General Medicine",
                        "qualification": "MBBS",
                        "consultation_fee": "500.00",
                        "chamber_address": "Demo Clinic, Dhaka",
                        "is_available": True,
                    },
                )
                PatientProfile.objects.update_or_create(
                    user=patient_user,
                    defaults={"emergency_contact": ""},
                )

                appointment_date = timezone.localdate() + timedelta(days=1)
                appointment_lookup = {
                    "patient": patient_user,
                    "doctor": doctor_profile,
                    "symptoms_note": DEMO_APPOINTMENT_NOTE,
                }
                seeded_appointment = Appointment.objects.filter(
                    **appointment_lookup
                ).first()
                overlapping_appointments = Appointment.objects.filter(
                    doctor=doctor_profile,
                    appointment_date=appointment_date,
                    status__in=[
                        Appointment.AppointmentStatus.PENDING,
                        Appointment.AppointmentStatus.CONFIRMED,
                    ],
                    time_slot_start__lt="10:30",
                    time_slot_end__gt="10:00",
                )
                if seeded_appointment:
                    overlapping_appointments = overlapping_appointments.exclude(
                        pk=seeded_appointment.pk
                    )
                if overlapping_appointments.exists():
                    raise CommandError(
                        "The demo appointment's time slot is already booked. "
                        "Free 10:00-10:30 tomorrow before seeding."
                    )

                _, appointment_created = Appointment.objects.update_or_create(
                    **appointment_lookup,
                    defaults={
                        "appointment_date": appointment_date,
                        "time_slot_start": "10:00",
                        "time_slot_end": "10:30",
                        "total_fee": doctor_profile.consultation_fee,
                        "status": Appointment.AppointmentStatus.PENDING,
                    },
                )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data is ready. "
                f"Admin {'created' if admin_created else 'updated'}: {admin_email}; "
                f"doctor {'created' if doctor_created else 'updated'}: {doctor_email}; "
                f"patient {'created' if patient_created else 'updated'}: {patient_email}; "
                f"appointment {'created' if appointment_created else 'updated'}."
            )
        )
        self.stdout.write(
            "The admin account has the app ADMIN role; it is not a Django superuser."
        )

    @staticmethod
    def _required_env(name):
        value = os.environ.get(name, "")
        if not value.strip():
            raise CommandError(f"Set {name} in the environment before running this command.")
        return value if name.endswith("_PASSWORD") else value.strip()

    @staticmethod
    def _upsert_user(user_model, *, email, password, role, first_name, last_name):
        email = user_model.objects.normalize_email(email)
        user = user_model.objects.filter(email=email).first()
        created = user is None

        if created:
            user = user_model.objects.create_user(
                email=email,
                password=password,
                role=role,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
                is_verified=True,
            )
        else:
            if user.role != role:
                raise ValueError(
                    f"{email} already exists with role {user.role}; expected {role}."
                )
            user.first_name = first_name
            user.last_name = last_name
            user.is_active = True
            user.is_verified = True
            if password is not None:
                user.set_password(password)
            user.save(
                update_fields=[
                    "first_name",
                    "last_name",
                    "is_active",
                    "is_verified",
                    "password",
                ]
            )

        return user, created
