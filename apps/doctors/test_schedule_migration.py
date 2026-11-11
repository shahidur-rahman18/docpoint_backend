import importlib
from datetime import date
from types import SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.test import TestCase

from apps.accounts.models import CustomUser
from apps.doctors.models import Chamber, ChamberSchedule, DoctorSchedule


class CopyDoctorScheduleMigrationTests(TestCase):
    def test_copies_legacy_weekly_windows_to_primary_chamber(self):
        doctor_user = CustomUser.objects.create_user(
            email='legacy-schedule-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        doctor = doctor_user.doctor_profile
        doctor.chamber_address = 'Verified clinic address'
        doctor.save(update_fields=['chamber_address'])
        legacy_schedule = DoctorSchedule.objects.create(
            doctor=doctor,
            effective_from=date(2026, 11, 9),
            effective_until=date(2026, 11, 12),
            timezone=settings.TIME_ZONE,
            weekly_schedule={
                'MONDAY': [
                    {'start': '18:07', 'end': '21:20'},
                    {'start': '22:21', 'end': '23:21'},
                ],
                'TUESDAY': [
                    {'start': '14:03', 'end': '15:21'},
                    {'start': '17:22', 'end': '18:22'},
                ],
                'WEDNESDAY': [{'start': '17:22', 'end': '19:22'}],
                'THURSDAY': [{'start': '18:25', 'end': '20:23'}],
                'FRIDAY': [{'start': '18:26', 'end': '23:23'}],
                'SATURDAY': [{'start': '19:00', 'end': '22:00'}],
                'SUNDAY': [],
            },
        )
        migration = importlib.import_module(
            'apps.doctors.migrations.0008_copy_doctor_schedule_to_chamber',
        )

        migration.copy_legacy_schedules(
            apps,
            SimpleNamespace(connection=connection),
        )

        chamber = Chamber.objects.get(doctor=doctor)
        schedules = ChamberSchedule.objects.filter(chamber=chamber)

        self.assertEqual(chamber.name, 'Primary Chamber')
        self.assertEqual(chamber.address, 'Verified clinic address')
        self.assertEqual(schedules.count(), 8)
        self.assertEqual(
            set(schedules.values_list('appointment_duration_minutes', flat=True)),
            {30},
        )
        self.assertEqual(
            set(schedules.values_list('effective_from', flat=True)),
            {legacy_schedule.effective_from},
        )
        self.assertEqual(
            set(schedules.values_list('effective_until', flat=True)),
            {legacy_schedule.effective_until},
        )
        self.assertTrue(
            DoctorSchedule.objects.filter(pk=legacy_schedule.pk).exists(),
        )
