from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser, PatientProfile
from apps.appointments.models import Appointment
from apps.doctors.models import Chamber, ChamberSchedule


class DoctorAppointmentListTests(TestCase):
    def setUp(self):
        self.doctor_user = CustomUser.objects.create_user(
            email='doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.doctor = self.doctor_user.doctor_profile
        other_doctor_user = CustomUser.objects.create_user(
            email='other-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        other_doctor = other_doctor_user.doctor_profile
        self.patient = CustomUser.objects.create_user(
            email='patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
            first_name='Demo',
            last_name='Patient',
            avatar_url='https://example.test/patient.png',
        )
        appointment_date = timezone.localdate() + timedelta(days=1)
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=appointment_date,
            time_slot_start='10:00',
            time_slot_end='10:30',
            symptoms_note='Demo appointment',
        )
        Appointment.objects.create(
            patient=self.patient,
            doctor=other_doctor,
            appointment_date=appointment_date + timedelta(days=1),
            time_slot_start='10:00',
            time_slot_end='10:30',
            symptoms_note='Other doctor appointment',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.doctor_user)

    def test_lists_only_own_appointments_with_patient_avatar_details(self):
        response = self.client.get(reverse('appointment-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.appointment.id)
        self.assertEqual(
            response.data[0]['patient_detail'],
            {
                'id': self.patient.id,
                'email': 'patient@example.test',
                'first_name': 'Demo',
                'last_name': 'Patient',
                'avatar_url': 'https://example.test/patient.png',
            },
        )


class AppointmentChamberCompatibilityTests(TestCase):
    def setUp(self):
        doctor_user = CustomUser.objects.create_user(
            email='chamber-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.doctor = doctor_user.doctor_profile
        self.patient = CustomUser.objects.create_user(
            email='chamber-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        self.appointment_date = timezone.localdate() + timedelta(days=1)

    def create_appointment(self, **values):
        return Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor,
            appointment_date=self.appointment_date,
            time_slot_start='10:00',
            time_slot_end='10:30',
            **values,
        )

    def test_legacy_appointment_can_be_created_without_chamber(self):
        appointment = self.create_appointment()

        self.assertIsNone(appointment.chamber)

    def test_chamber_deletion_preserves_appointment_and_clears_chamber(self):
        chamber = Chamber.objects.create(
            doctor=self.doctor,
            name='Main clinic',
            address='1 Clinic Road',
        )
        appointment = self.create_appointment(chamber=chamber)

        chamber.delete()
        appointment.refresh_from_db()

        self.assertIsNone(appointment.chamber)
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())

    def test_model_validation_rejects_chamber_owned_by_another_doctor(self):
        other_doctor_user = CustomUser.objects.create_user(
            email='other-chamber-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        chamber = Chamber.objects.create(
            doctor=other_doctor_user.doctor_profile,
            name='Other clinic',
            address='2 Clinic Road',
        )
        appointment = Appointment(
            patient=self.patient,
            doctor=self.doctor,
            chamber=chamber,
            appointment_date=self.appointment_date,
            time_slot_start='10:00',
            time_slot_end='10:30',
        )

        with self.assertRaises(ValidationError):
            appointment.full_clean()


class AppointmentChamberMigrationTests(TransactionTestCase):
    migrate_from = [('appointments', '0002_alter_appointment_status')]
    migrate_to = [('appointments', '0003_appointment_chamber')]

    def setUp(self):
        super().setUp()
        doctor_user = CustomUser.objects.create_user(
            email='migration-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        patient = CustomUser.objects.create_user(
            email='migration-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        appointment = Appointment.objects.create(
            patient=patient,
            doctor=doctor_user.doctor_profile,
            appointment_date=timezone.localdate() + timedelta(days=1),
            time_slot_start='10:00',
            time_slot_end='10:30',
        )
        self.appointment_id = appointment.pk

        try:
            MigrationExecutor(connection).migrate(self.migrate_from)
            MigrationExecutor(connection).migrate(self.migrate_to)
        except Exception:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            raise

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_appointment_survives_additive_chamber_migration(self):
        migrated_apps = MigrationExecutor(connection).loader.project_state(
            self.migrate_to
        ).apps
        migrated_appointment = migrated_apps.get_model(
            'appointments',
            'Appointment',
        ).objects.get(pk=self.appointment_id)

        self.assertIsNone(migrated_appointment.chamber_id)


class AppointmentAvailabilityAndBookingTests(TestCase):
    def setUp(self):
        self.doctor_user = CustomUser.objects.create_user(
            email='booking-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.doctor = self.doctor_user.doctor_profile
        self.doctor.consultation_fee = '45.00'
        self.doctor.save(update_fields=['consultation_fee'])
        self.patient = CustomUser.objects.create_user(
            email='booking-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        self.patient_profile = self.patient.patient_profile
        self.patient_profile.date_of_birth = date(1990, 1, 1)
        self.patient_profile.gender = PatientProfile.Gender.OTHER
        self.patient_profile.blood_group = PatientProfile.BloodGroup.O_POS
        self.patient_profile.address = '1 Patient Road'
        self.patient_profile.save()
        self.other_patient = CustomUser.objects.create_user(
            email='other-booking-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        self.admin = CustomUser.objects.create_user(
            email='booking-admin@example.test',
            password='safe-admin-password',
            role=CustomUser.Role.ADMIN,
        )
        self.chamber = Chamber.objects.create(
            doctor=self.doctor,
            name='Booking clinic',
            address='1 Clinic Road',
        )
        self.appointment_date = timezone.localdate() + timedelta(days=1)
        while self.appointment_date.weekday() != ChamberSchedule.Weekday.MONDAY:
            self.appointment_date += timedelta(days=1)
        self.appointment_date += timedelta(days=7)
        self.schedule = ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=self.appointment_date.weekday(),
            start_time=time(9, 0),
            end_time=time(10, 0),
            appointment_duration_minutes=30,
            effective_from=self.appointment_date,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.patient)
        self.availability_url = reverse(
            'chamber-available-slots',
            args=[self.chamber.pk],
        )
        self.booking_url = reverse('appointment-list')

    def booking_payload(self, **overrides):
        payload = {
            'chamber': self.chamber.pk,
            'appointment_date': self.appointment_date.isoformat(),
            'time_slot_start': '09:00:00',
            'symptoms_note': 'Routine consultation',
        }
        payload.update(overrides)
        return payload

    def create_appointment(self, **overrides):
        values = {
            'patient': self.patient,
            'doctor': self.doctor,
            'chamber': self.chamber,
            'appointment_date': self.appointment_date,
            'time_slot_start': time(9, 0),
            'time_slot_end': time(9, 30),
        }
        values.update(overrides)
        return Appointment.objects.create(**values)

    def appointment_detail_url(self, appointment):
        return reverse('appointment-detail', args=[appointment.pk])

    def test_available_slots_follow_schedule_and_hide_booked_slots(self):
        Appointment.objects.create(
            patient=self.other_patient,
            doctor=self.doctor,
            chamber=self.chamber,
            appointment_date=self.appointment_date,
            time_slot_start=time(9, 0),
            time_slot_end=time(9, 30),
        )

        response = self.client.get(
            self.availability_url,
            {'date': self.appointment_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['chamber'], self.chamber.pk)
        self.assertEqual(response.data['date'], self.appointment_date.isoformat())
        self.assertEqual(
            response.data['slots'],
            [{'start_time': '09:30:00', 'end_time': '10:00:00'}],
        )

    def test_availability_rejects_invalid_and_past_dates(self):
        invalid_response = self.client.get(
            self.availability_url,
            {'date': 'not-a-date'},
        )
        past_response = self.client.get(
            self.availability_url,
            {'date': (timezone.localdate() - timedelta(days=1)).isoformat()},
        )

        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(past_response.status_code, 400)

    def test_availability_is_empty_without_an_active_schedule(self):
        self.schedule.is_active = False
        self.schedule.save(update_fields=['is_active'])

        response = self.client.get(
            self.availability_url,
            {'date': self.appointment_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['slots'], [])

    def test_availability_respects_schedule_effective_date_bounds(self):
        self.schedule.effective_until = self.appointment_date
        self.schedule.save(update_fields=['effective_until'])
        following_week = self.appointment_date + timedelta(days=7)

        before_start = self.client.get(
            self.availability_url,
            {'date': (self.appointment_date - timedelta(days=7)).isoformat()},
        )
        after_end = self.client.get(
            self.availability_url,
            {'date': following_week.isoformat()},
        )

        self.assertEqual(before_start.status_code, 200)
        self.assertEqual(before_start.data['slots'], [])
        self.assertEqual(after_end.status_code, 200)
        self.assertEqual(after_end.data['slots'], [])

    def test_availability_and_booking_reject_inactive_chamber(self):
        self.chamber.is_active = False
        self.chamber.save(update_fields=['is_active'])

        availability_response = self.client.get(
            self.availability_url,
            {'date': self.appointment_date.isoformat()},
        )
        booking_response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )

        self.assertEqual(availability_response.status_code, 404)
        self.assertEqual(booking_response.status_code, 400)
        self.assertFalse(Appointment.objects.exists())

    def test_booking_derives_doctor_and_slot_end_and_keeps_pending_status(self):
        response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        appointment = Appointment.objects.get()
        self.assertEqual(appointment.patient_id, self.patient.pk)
        self.assertEqual(appointment.doctor_id, self.doctor.pk)
        self.assertEqual(appointment.chamber_id, self.chamber.pk)
        self.assertEqual(appointment.time_slot_end, time(9, 30))
        self.assertEqual(appointment.status, Appointment.AppointmentStatus.PENDING)
        self.assertEqual(str(appointment.total_fee), '45.00')
        self.assertEqual(response.data['chamber'], self.chamber.pk)

    def test_booking_rejects_times_outside_schedule_and_invalid_dates(self):
        outside_schedule = self.client.post(
            self.booking_url,
            self.booking_payload(time_slot_start='10:00:00'),
            format='json',
        )
        misaligned_time = self.client.post(
            self.booking_url,
            self.booking_payload(time_slot_start='09:15:00'),
            format='json',
        )
        invalid_date = self.client.post(
            self.booking_url,
            self.booking_payload(appointment_date='2026-02-31'),
            format='json',
        )
        past_date = self.client.post(
            self.booking_url,
            self.booking_payload(
                appointment_date=(timezone.localdate() - timedelta(days=1)).isoformat(),
            ),
            format='json',
        )

        self.assertEqual(outside_schedule.status_code, 400)
        self.assertEqual(misaligned_time.status_code, 400)
        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(past_date.status_code, 400)
        self.assertFalse(Appointment.objects.exists())

    def test_booking_rejects_past_time(self):
        today = timezone.localdate()
        ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=today.weekday(),
            start_time=time(0, 0),
            end_time=time(0, 30),
            appointment_duration_minutes=30,
            effective_from=today,
        )

        response = self.client.post(
            self.booking_url,
            self.booking_payload(
                appointment_date=today.isoformat(),
                time_slot_start='00:00:00',
            ),
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Appointment.objects.exists())

    def test_incomplete_profile_returns_machine_readable_missing_fields(self):
        self.patient_profile.address = ''
        self.patient_profile.blood_group = ''
        self.patient_profile.save(update_fields=['address', 'blood_group'])

        response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data['profile_incomplete'])
        self.assertEqual(
            set(response.data['missing_fields']),
            {'blood_group', 'address'},
        )
        self.assertFalse(Appointment.objects.exists())

    def test_completing_profile_through_api_allows_booking_retry(self):
        self.patient_profile.address = ''
        self.patient_profile.blood_group = ''
        self.patient_profile.save(update_fields=['address', 'blood_group'])

        blocked_response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )
        self.assertEqual(blocked_response.status_code, 400)
        self.assertTrue(blocked_response.data['profile_incomplete'])

        profile_response = self.client.patch(
            reverse('patient_profile'),
            {
                'blood_group': PatientProfile.BloodGroup.UNKNOWN,
                'address': 'Completed profile address',
            },
            format='json',
        )
        booking_response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )

        self.assertEqual(profile_response.status_code, 200)
        self.assertTrue(profile_response.data['profile_complete'])
        self.assertEqual(booking_response.status_code, 201)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_unknown_blood_group_is_valid_for_booking(self):
        self.patient_profile.blood_group = PatientProfile.BloodGroup.UNKNOWN
        self.patient_profile.save(update_fields=['blood_group'])

        response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.patient_profile.refresh_from_db()
        self.assertEqual(
            self.patient_profile.get_blood_group_display(),
            'Unknown / Not Tested',
        )

    def test_duplicate_booking_is_rejected_and_cancelled_appointment_releases_slot(self):
        first_response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )
        duplicate_response = self.client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )
        appointment = Appointment.objects.get()
        appointment.status = Appointment.AppointmentStatus.CANCELLED
        appointment.save(update_fields=['status'])
        availability_response = self.client.get(
            self.availability_url,
            {'date': self.appointment_date.isoformat()},
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(duplicate_response.status_code, 400)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(len(availability_response.data['slots']), 2)

    def test_booking_rejects_forged_patient_and_doctor_ids(self):
        forged_patient = self.client.post(
            self.booking_url,
            self.booking_payload(patient=self.other_patient.pk),
            format='json',
        )
        forged_doctor = self.client.post(
            self.booking_url,
            self.booking_payload(doctor=999999),
            format='json',
        )
        forged_chamber = self.client.post(
            self.booking_url,
            self.booking_payload(chamber=999999),
            format='json',
        )

        self.assertEqual(forged_patient.status_code, 400)
        self.assertEqual(forged_doctor.status_code, 400)
        self.assertEqual(forged_chamber.status_code, 400)
        self.assertFalse(Appointment.objects.exists())

    def test_only_patients_can_book_and_users_cannot_read_other_patient_appointments(self):
        doctor_client = APIClient()
        doctor_client.force_authenticate(user=self.doctor_user)
        forbidden_booking = doctor_client.post(
            self.booking_url,
            self.booking_payload(),
            format='json',
        )
        self.assertEqual(forbidden_booking.status_code, 403)
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        self.assertEqual(
            admin_client.post(
                self.booking_url,
                self.booking_payload(),
                format='json',
            ).status_code,
            403,
        )

        appointment = Appointment.objects.create(
            patient=self.other_patient,
            doctor=self.doctor,
            chamber=self.chamber,
            appointment_date=self.appointment_date,
            time_slot_start=time(9, 0),
            time_slot_end=time(9, 30),
        )
        response = self.client.get(
            reverse('appointment-detail', args=[appointment.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_retains_appointment_list_access(self):
        Appointment.objects.create(
            patient=self.other_patient,
            doctor=self.doctor,
            chamber=self.chamber,
            appointment_date=self.appointment_date,
            time_slot_start=time(9, 0),
            time_slot_end=time(9, 30),
        )
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)

        response = admin_client.get(self.booking_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_patient_cannot_change_doctor_or_status(self):
        appointment = self.create_appointment()
        url = self.appointment_detail_url(appointment)

        for method, payload in (
            ('put', {
                'doctor': self.doctor.pk + 100,
                'appointment_date': self.appointment_date.isoformat(),
                'time_slot_start': '09:00:00',
                'status': 'COMPLETED',
            }),
            ('patch', {'status': 'CANCELLED'}),
        ):
            response = getattr(self.client, method)(
                url,
                payload,
                format='json',
            )
            self.assertEqual(response.status_code, 400)
            appointment.refresh_from_db()
            self.assertEqual(appointment.doctor_id, self.doctor.pk)
            self.assertEqual(appointment.status, Appointment.AppointmentStatus.PENDING)

    def test_patient_cannot_change_chamber_date_or_time(self):
        appointment = self.create_appointment()
        other_chamber = Chamber.objects.create(
            doctor=self.doctor,
            name='Second booking clinic',
            address='2 Clinic Road',
        )
        url = self.appointment_detail_url(appointment)

        for payload in (
            {'chamber': other_chamber.pk},
            {'appointment_date': (self.appointment_date + timedelta(days=7)).isoformat()},
            {'time_slot_start': '09:30:00'},
        ):
            response = self.client.patch(url, payload, format='json')
            self.assertEqual(response.status_code, 400)
            appointment.refresh_from_db()
            self.assertEqual(appointment.chamber_id, self.chamber.pk)
            self.assertEqual(appointment.appointment_date, self.appointment_date)
            self.assertEqual(appointment.time_slot_start, time(9, 0))

    def test_patient_can_update_only_symptoms_note(self):
        appointment = self.create_appointment()

        response = self.client.put(
            self.appointment_detail_url(appointment),
            {'symptoms_note': 'Updated symptoms'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.symptoms_note, 'Updated symptoms')

    def test_patient_cannot_update_another_patients_appointment(self):
        appointment = self.create_appointment(patient=self.other_patient)

        response = self.client.patch(
            self.appointment_detail_url(appointment),
            {'symptoms_note': 'Unauthorized change'},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        appointment.refresh_from_db()
        self.assertIsNone(appointment.symptoms_note)

    def test_doctor_can_update_status_but_not_booking_fields(self):
        appointment = self.create_appointment()
        doctor_client = APIClient()
        doctor_client.force_authenticate(user=self.doctor_user)

        status_response = doctor_client.patch(
            self.appointment_detail_url(appointment),
            {'status': Appointment.AppointmentStatus.CONFIRMED},
            format='json',
        )
        forged_update = doctor_client.patch(
            self.appointment_detail_url(appointment),
            {'doctor': self.doctor.pk + 100},
            format='json',
        )

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(forged_update.status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.AppointmentStatus.CONFIRMED)
        self.assertEqual(appointment.doctor_id, self.doctor.pk)

    def test_doctor_cannot_update_another_doctors_appointment(self):
        appointment = self.create_appointment()
        other_doctor_user = CustomUser.objects.create_user(
            email='unrelated-update-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other_doctor_user)

        response = other_client.patch(
            self.appointment_detail_url(appointment),
            {'status': Appointment.AppointmentStatus.CONFIRMED},
            format='json',
        )

        self.assertEqual(response.status_code, 404)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.AppointmentStatus.PENDING)

    def test_admin_can_manage_status_and_validated_reschedule(self):
        appointment = self.create_appointment()
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)

        response = admin_client.patch(
            self.appointment_detail_url(appointment),
            {
                'appointment_date': (self.appointment_date + timedelta(days=7)).isoformat(),
                'time_slot_start': '09:30:00',
                'status': Appointment.AppointmentStatus.CONFIRMED,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        appointment.refresh_from_db()
        self.assertEqual(appointment.appointment_date, self.appointment_date + timedelta(days=7))
        self.assertEqual(appointment.time_slot_start, time(9, 30))
        self.assertEqual(appointment.time_slot_end, time(10, 0))
        self.assertEqual(appointment.status, Appointment.AppointmentStatus.CONFIRMED)

    def test_admin_invalid_reschedule_preserves_appointment(self):
        appointment = self.create_appointment()
        other_doctor_user = CustomUser.objects.create_user(
            email='wrong-owner-update-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        other_chamber = Chamber.objects.create(
            doctor=other_doctor_user.doctor_profile,
            name='Wrong owner clinic',
            address='3 Clinic Road',
        )
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)

        response = admin_client.patch(
            self.appointment_detail_url(appointment),
            {'chamber': other_chamber.pk},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(appointment.doctor_id, self.doctor.pk)
        self.assertEqual(appointment.chamber_id, self.chamber.pk)
        self.assertEqual(appointment.appointment_date, self.appointment_date)
        self.assertEqual(appointment.time_slot_start, time(9, 0))
        self.assertEqual(appointment.status, Appointment.AppointmentStatus.PENDING)

        invalid_time_response = admin_client.patch(
            self.appointment_detail_url(appointment),
            {'time_slot_start': '09:15:00'},
            format='json',
        )
        self.assertEqual(invalid_time_response.status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(appointment.time_slot_start, time(9, 0))

    def test_admin_cannot_reactivate_past_cancelled_appointment_with_put_or_patch(self):
        past_date = timezone.localdate() - timedelta(days=7)
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        for method in ('put', 'patch'):
            with self.subTest(method=method):
                appointment = self.create_appointment(
                    appointment_date=past_date,
                    status=Appointment.AppointmentStatus.CANCELLED,
                )
                response = getattr(
                    admin_client,
                    method,
                )(
                    self.appointment_detail_url(appointment),
                    {'status': Appointment.AppointmentStatus.CONFIRMED},
                    format='json',
                )
                appointment.refresh_from_db()

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    appointment.status,
                    Appointment.AppointmentStatus.CANCELLED,
                )
                self.assertEqual(appointment.appointment_date, past_date)

    def test_admin_status_only_reactivation_requires_valid_chamber_schedule_and_slot(self):
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        other_doctor_user = CustomUser.objects.create_user(
            email='status-update-other-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        other_chamber = Chamber.objects.create(
            doctor=other_doctor_user.doctor_profile,
            name='Other status clinic',
            address='4 Clinic Road',
        )

        for scenario in (
            'inactive_chamber',
            'inactive_schedule',
            'missing_chamber',
            'wrong_chamber_owner',
            'overlapping_slot',
        ):
            with self.subTest(scenario=scenario):
                appointment = self.create_appointment(
                    status=Appointment.AppointmentStatus.CANCELLED,
                )
                blocker = None
                original_chamber_active = self.chamber.is_active
                original_schedule_active = self.schedule.is_active
                if scenario == 'inactive_chamber':
                    self.chamber.is_active = False
                    self.chamber.save(update_fields=['is_active'])
                elif scenario == 'inactive_schedule':
                    self.schedule.is_active = False
                    self.schedule.save(update_fields=['is_active'])
                elif scenario == 'missing_chamber':
                    appointment.chamber = None
                    appointment.save(update_fields=['chamber'])
                elif scenario == 'wrong_chamber_owner':
                    appointment.chamber = other_chamber
                    appointment.save(update_fields=['chamber'])
                elif scenario == 'overlapping_slot':
                    blocker = self.create_appointment(
                        patient=self.other_patient,
                        status=Appointment.AppointmentStatus.PENDING,
                    )

                before = {
                    'doctor_id': appointment.doctor_id,
                    'chamber_id': appointment.chamber_id,
                    'appointment_date': appointment.appointment_date,
                    'time_slot_start': appointment.time_slot_start,
                    'time_slot_end': appointment.time_slot_end,
                    'status': appointment.status,
                }
                response = admin_client.patch(
                    self.appointment_detail_url(appointment),
                    {'status': Appointment.AppointmentStatus.CONFIRMED},
                    format='json',
                )
                appointment.refresh_from_db()

                self.assertEqual(response.status_code, 400)
                self.assertEqual(appointment.doctor_id, before['doctor_id'])
                self.assertEqual(appointment.chamber_id, before['chamber_id'])
                self.assertEqual(appointment.appointment_date, before['appointment_date'])
                self.assertEqual(appointment.time_slot_start, before['time_slot_start'])
                self.assertEqual(appointment.time_slot_end, before['time_slot_end'])
                self.assertEqual(appointment.status, before['status'])
                if blocker is not None:
                    blocker.delete()
                if self.chamber.is_active != original_chamber_active:
                    self.chamber.is_active = original_chamber_active
                    self.chamber.save(update_fields=['is_active'])
                if self.schedule.is_active != original_schedule_active:
                    self.schedule.is_active = original_schedule_active
                    self.schedule.save(update_fields=['is_active'])

    def test_admin_can_keep_historical_status_without_current_schedule(self):
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        past_appointment = self.create_appointment(
            appointment_date=timezone.localdate() - timedelta(days=7),
            status=Appointment.AppointmentStatus.COMPLETED,
        )
        self.chamber.is_active = False
        self.chamber.save(update_fields=['is_active'])

        response = admin_client.patch(
            self.appointment_detail_url(past_appointment),
            {'status': Appointment.AppointmentStatus.CANCELLED},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        past_appointment.refresh_from_db()
        self.assertEqual(
            past_appointment.status,
            Appointment.AppointmentStatus.CANCELLED,
        )

    def test_admin_can_reactivate_valid_cancelled_appointment_with_put_or_patch(self):
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.admin)
        appointment = self.create_appointment(
            status=Appointment.AppointmentStatus.CANCELLED,
        )
        for method in ('put', 'patch'):
            with self.subTest(method=method):
                response = getattr(admin_client, method)(
                    self.appointment_detail_url(appointment),
                    {'status': Appointment.AppointmentStatus.CONFIRMED},
                    format='json',
                )

                self.assertEqual(response.status_code, 200)
                appointment.refresh_from_db()
                self.assertEqual(
                    appointment.status,
                    Appointment.AppointmentStatus.CONFIRMED,
                )
                appointment.status = Appointment.AppointmentStatus.CANCELLED
                appointment.save(update_fields=['status'])


@skipUnless(
    connection.vendor == 'postgresql',
    'Concurrent row-locking test requires an isolated PostgreSQL test database.',
)
class ConcurrentAppointmentBookingTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.doctor_user = CustomUser.objects.create_user(
            email='concurrent-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.patient = CustomUser.objects.create_user(
            email='concurrent-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        profile = self.patient.patient_profile
        profile.date_of_birth = date(1990, 1, 1)
        profile.gender = PatientProfile.Gender.OTHER
        profile.blood_group = PatientProfile.BloodGroup.UNKNOWN
        profile.address = '1 Concurrent Road'
        profile.save()
        self.chamber = Chamber.objects.create(
            doctor=self.doctor_user.doctor_profile,
            name='Concurrent clinic',
            address='1 Clinic Road',
        )
        self.appointment_date = timezone.localdate() + timedelta(days=1)
        while self.appointment_date.weekday() != ChamberSchedule.Weekday.MONDAY:
            self.appointment_date += timedelta(days=1)
        ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=self.appointment_date.weekday(),
            start_time=time(9, 0),
            end_time=time(10, 0),
            appointment_duration_minutes=30,
            effective_from=self.appointment_date,
        )

    def test_concurrent_requests_cannot_book_the_same_slot_twice(self):
        barrier = Barrier(2)
        booking_url = reverse('appointment-list')
        payload = {
            'chamber': self.chamber.pk,
            'appointment_date': self.appointment_date.isoformat(),
            'time_slot_start': '09:00:00',
        }

        def book_slot():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(
                user=CustomUser.objects.get(pk=self.patient.pk),
            )
            barrier.wait(timeout=10)
            response = client.post(booking_url, payload, format='json')
            close_old_connections()
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: book_slot(), range(2)))

        self.assertCountEqual(results, [201, 400])
        self.assertEqual(
            Appointment.objects.filter(
                doctor=self.doctor_user.doctor_profile,
                appointment_date=self.appointment_date,
                time_slot_start=time(9, 0),
            ).count(),
            1,
        )
