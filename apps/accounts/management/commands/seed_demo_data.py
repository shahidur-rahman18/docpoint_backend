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
DEMO_ACCOUNT_COUNT = 20
DEMO_AVATAR_URLS = (
    "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=300&h=300&q=80",
    "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=300&h=300&q=80",
    "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?auto=format&fit=crop&w=300&h=300&q=80",
    "https://images.unsplash.com/photo-1517841905240-472988babdf9?auto=format&fit=crop&w=300&h=300&q=80",
    "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=300&h=300&q=80",
    "https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=300&h=300&q=80",
)
DOCTOR_SPECIALTIES = (
    "General Medicine",
    "Cardiology",
    "Dermatology",
    "Pediatrics",
    "Orthopedics",
    "Neurology",
    "Gynecology",
    "ENT",
    "Ophthalmology",
    "Psychiatry",
    "Gastroenterology",
    "Urology",
    "Pulmonology",
    "Oncology",
    "Endocrinology",
    "Nephrology",
    "Dentistry",
    "Physical Medicine",
    "Surgery",
    "Family Medicine",
)


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
        database_host = (
            settings.DATABASES["default"].get("HOST") or ""
        ).strip().lower()
        is_remote_database = database_host not in {
            "",
            "localhost",
            "127.0.0.1",
            "::1",
            "[::1]",
        }
        if (not settings.DEBUG or is_remote_database) and not options["allow_production"]:
            raise CommandError(
                "This is not a local debug database. To seed it intentionally, rerun "
                "with --allow-production."
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
                    avatar_url=DEMO_AVATAR_URLS[0],
                )
                doctors = []
                patients = []
                for index in range(DEMO_ACCOUNT_COUNT):
                    ordinal = index + 1
                    doctor_user, _ = self._upsert_user(
                        user_model,
                        email=self._numbered_email(doctor_email, ordinal),
                        password=doctor_password,
                        role=user_model.Role.DOCTOR,
                        first_name="Demo",
                        last_name="Doctor" if ordinal == 1 else f"Doctor {ordinal:02}",
                        avatar_url=DEMO_AVATAR_URLS[index % len(DEMO_AVATAR_URLS)],
                    )
                    doctor_profile, _ = DoctorProfile.objects.update_or_create(
                        user=doctor_user,
                        defaults={
                            "specialization": DOCTOR_SPECIALTIES[index],
                            "qualification": "MBBS",
                            "consultation_fee": "500.00",
                            "chamber_address": "Demo Clinic, Dhaka",
                            "is_available": True,
                        },
                    )

                    patient_user, _ = self._upsert_user(
                        user_model,
                        email=self._numbered_email(patient_email, ordinal),
                        password=None,
                        role=user_model.Role.PATIENT,
                        first_name="Demo",
                        last_name="Patient" if ordinal == 1 else f"Patient {ordinal:02}",
                        avatar_url=DEMO_AVATAR_URLS[
                            (index + 1) % len(DEMO_AVATAR_URLS)
                        ],
                    )
                    PatientProfile.objects.update_or_create(
                        user=patient_user,
                        defaults={"emergency_contact": ""},
                    )
                    doctors.append(doctor_profile)
                    patients.append(patient_user)

                for index, (doctor_profile, patient_user) in enumerate(
                    zip(doctors, patients)
                ):
                    appointment_date = timezone.localdate() + timedelta(days=index + 1)
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
                            "A demo appointment's 10:00-10:30 slot is already booked. "
                            f"Free the slot for doctor {index + 1} first."
                        )

                    Appointment.objects.update_or_create(
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
                f"{DEMO_ACCOUNT_COUNT} doctors and {DEMO_ACCOUNT_COUNT} patients "
                f"are ready; {DEMO_ACCOUNT_COUNT} demo appointments are ready."
            )
        )
        self.stdout.write(
            "The admin account has the app ADMIN role; it is not a Django superuser. "
            "Patient accounts have no usable password."
        )

    @staticmethod
    def _required_env(name):
        value = os.environ.get(name, "")
        if not value.strip():
            raise CommandError(f"Set {name} in the environment before running this command.")
        return value if name.endswith("_PASSWORD") else value.strip()

    @staticmethod
    def _numbered_email(email, ordinal):
        if ordinal == 1:
            return email
        local_part, domain = email.rsplit("@", 1)
        return f"{local_part}-{ordinal:02}@{domain}"

    @staticmethod
    def _upsert_user(
        user_model, *, email, password, role, first_name, last_name, avatar_url
    ):
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
                avatar_url=avatar_url,
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
            update_fields = [
                "first_name",
                "last_name",
                "is_active",
                "is_verified",
                "password",
            ]
            if not user.avatar_url and not user.avatar_public_id:
                user.avatar_url = avatar_url
                update_fields.append("avatar_url")
            if password is not None:
                user.set_password(password)
            user.save(update_fields=update_fields)

        return user, created
