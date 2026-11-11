from datetime import date, time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.avatar_storage import AvatarStorageUnavailable
from apps.accounts.models import CustomUser
from apps.appointments.models import Appointment
from apps.doctors.models import Chamber, ChamberSchedule, DoctorProfile


class DoctorListTests(APITestCase):
    def test_public_doctor_list_includes_user_names(self):
        user = CustomUser.objects.create_user(
            email='doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
            first_name='Ada',
            last_name='Lovelace',
        )
        DoctorProfile.objects.get_or_create(user=user)

        response = self.client.get(reverse('doctor-list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['user_details']['first_name'], 'Ada')
        self.assertEqual(response.data[0]['user_details']['last_name'], 'Lovelace')


class DoctorDeleteTests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            email='admin@example.com',
            password='test-password',
            role=CustomUser.Role.ADMIN,
        )
        self.doctor = CustomUser.objects.create_user(
            email='doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.doctor_id = self.doctor.pk
        self.doctor_profile = DoctorProfile.objects.get(user=self.doctor)
        self.doctor_profile_id = self.doctor_profile.pk
        self.patient = CustomUser.objects.create_user(
            email='patient@example.com',
            password='test-password',
            role=CustomUser.Role.PATIENT,
        )
        self.appointment = Appointment.objects.create(
            patient=self.patient,
            doctor=self.doctor_profile,
            appointment_date='2026-11-05',
            time_slot_start='09:00',
            time_slot_end='09:30',
        )
        self.appointment_id = self.appointment.pk
        self.url = reverse('doctor-detail', args=[self.doctor_profile.pk])

    def test_admin_delete_removes_doctor_account_profile_and_appointments(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.doctor_id).exists())
        self.assertFalse(DoctorProfile.objects.filter(pk=self.doctor_profile_id).exists())
        self.assertFalse(Appointment.objects.filter(pk=self.appointment_id).exists())

    def test_admin_delete_conflicts_when_chamber_schedule_history_exists(self):
        chamber = Chamber.objects.create(
            doctor=self.doctor_profile,
            name='Main clinic',
            address='1 Clinic Road',
        )
        schedule = ChamberSchedule.objects.create(
            chamber=chamber,
            weekday=ChamberSchedule.Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            appointment_duration_minutes=30,
            effective_from=date(2026, 11, 1),
        )
        self.appointment.chamber = chamber
        self.appointment.save(update_fields=['chamber'])
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertIn('schedule history', response.data['detail'].lower())
        self.assertTrue(CustomUser.objects.filter(pk=self.doctor_id).exists())
        self.assertTrue(
            DoctorProfile.objects.filter(pk=self.doctor_profile_id).exists()
        )
        self.assertTrue(Chamber.objects.filter(pk=chamber.pk, is_active=True).exists())
        self.assertTrue(
            ChamberSchedule.objects.filter(pk=schedule.pk, is_active=True).exists()
        )
        self.assertTrue(
            Appointment.objects.filter(
                pk=self.appointment_id,
                chamber=chamber,
                doctor=self.doctor_profile,
            ).exists()
        )

    def test_doctor_cannot_delete_own_account(self):
        self.client.force_authenticate(user=self.doctor)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(CustomUser.objects.filter(pk=self.doctor_id).exists())

    def test_owner_can_still_update_doctor_profile(self):
        self.client.force_authenticate(user=self.doctor)

        response = self.client.patch(self.url, {'specialization': 'Cardiology'})

        self.assertEqual(response.status_code, 200)

    @patch('apps.doctors.views.delete_avatar', side_effect=AvatarStorageUnavailable)
    def test_avatar_storage_failure_does_not_restore_deleted_account(self, delete_avatar):
        self.doctor.avatar_public_id = 'docpoint/avatars/doctor-avatar'
        self.doctor.save(update_fields=['avatar_public_id'])
        self.client.force_authenticate(user=self.admin)

        with self.assertLogs('apps.doctors.views', level='ERROR'):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CustomUser.objects.filter(pk=self.doctor_id).exists())
        delete_avatar.assert_called_once_with('docpoint/avatars/doctor-avatar', self.doctor_id)


class ChamberScheduleModelTests(TestCase):
    def setUp(self):
        doctor_user = CustomUser.objects.create_user(
            email='schedule-doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.chamber = Chamber.objects.create(
            doctor=doctor_user.doctor_profile,
            name='Main clinic',
            address='1 Clinic Road',
        )
        self.effective_from = date(2026, 11, 1)

    def create_schedule(self, **overrides):
        values = {
            'chamber': self.chamber,
            'weekday': ChamberSchedule.Weekday.MONDAY,
            'start_time': time(9, 0),
            'end_time': time(12, 0),
            'appointment_duration_minutes': 30,
            'effective_from': self.effective_from,
        }
        values.update(overrides)
        return ChamberSchedule.objects.create(**values)

    def test_schedule_accepts_valid_configuration(self):
        schedule = self.create_schedule()

        schedule.full_clean()

        self.assertEqual(schedule.get_weekday_display(), 'Monday')
        self.assertIsNone(schedule.effective_until)

    def test_model_validation_rejects_overlapping_effective_shifts(self):
        self.create_schedule()
        overlapping = ChamberSchedule(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.MONDAY,
            start_time=time(11, 30),
            end_time=time(13, 0),
            appointment_duration_minutes=30,
            effective_from=self.effective_from + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_model_validation_allows_adjacent_shifts_and_nonoverlapping_dates(self):
        self.create_schedule(effective_until=self.effective_from + timedelta(days=6))
        adjacent = ChamberSchedule(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.MONDAY,
            start_time=time(12, 0),
            end_time=time(14, 0),
            appointment_duration_minutes=30,
            effective_from=self.effective_from,
        )
        later = ChamberSchedule(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.MONDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            appointment_duration_minutes=30,
            effective_from=self.effective_from + timedelta(days=7),
        )

        adjacent.full_clean()
        later.full_clean()

    def test_database_constraints_reject_invalid_schedule_values(self):
        invalid_schedules = [
            {'weekday': 7},
            {'start_time': time(12, 0), 'end_time': time(9, 0)},
            {'appointment_duration_minutes': 0},
            {
                'effective_from': date(2026, 11, 10),
                'effective_until': date(2026, 11, 9),
            },
        ]

        for invalid_values in invalid_schedules:
            with self.subTest(invalid_values=invalid_values):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self.create_schedule(**invalid_values)


class ChamberManagementAPITests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            email='chamber-admin@example.com',
            password='test-password',
            role=CustomUser.Role.ADMIN,
        )
        self.doctor = CustomUser.objects.create_user(
            email='chamber-doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.other_doctor = CustomUser.objects.create_user(
            email='other-chamber-doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.patient = CustomUser.objects.create_user(
            email='chamber-patient@example.com',
            password='test-password',
            role=CustomUser.Role.PATIENT,
        )
        self.chamber = Chamber.objects.create(
            doctor=self.doctor.doctor_profile,
            name='Main clinic',
            address='1 Clinic Road',
        )
        self.other_chamber = Chamber.objects.create(
            doctor=self.other_doctor.doctor_profile,
            name='Other clinic',
            address='2 Clinic Road',
        )
        self.list_url = reverse('chamber-list')

    def test_doctor_can_create_view_update_and_delete_own_chamber(self):
        self.client.force_authenticate(user=self.doctor)

        create_response = self.client.post(
            self.list_url,
            {
                'doctor': self.other_doctor.doctor_profile.pk,
                'name': 'New clinic',
                'address': '3 Clinic Road',
                'is_active': True,
            },
        )

        self.assertEqual(create_response.status_code, 201)
        created_chamber = Chamber.objects.get(pk=create_response.data['id'])
        self.assertEqual(created_chamber.doctor_id, self.doctor.doctor_profile.pk)

        detail_url = reverse('chamber-detail', args=[created_chamber.pk])
        detail_response = self.client.get(detail_url)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data['id'], created_chamber.pk)

        list_response = self.client.get(self.list_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in list_response.data},
            {self.chamber.pk, created_chamber.pk},
        )

        update_response = self.client.patch(detail_url, {'name': 'Updated clinic'})
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data['name'], 'Updated clinic')

        delete_response = self.client.delete(detail_url)
        self.assertEqual(delete_response.status_code, 204)
        created_chamber.refresh_from_db()
        self.assertFalse(created_chamber.is_active)
        self.assertTrue(Chamber.objects.filter(pk=created_chamber.pk).exists())

    def test_doctor_cannot_view_or_modify_another_doctors_chamber(self):
        self.client.force_authenticate(user=self.doctor)
        detail_url = reverse('chamber-detail', args=[self.other_chamber.pk])

        self.assertEqual(self.client.get(detail_url).status_code, 404)
        self.assertEqual(
            self.client.patch(detail_url, {'name': 'Hijacked'}).status_code,
            404,
        )
        self.assertEqual(self.client.delete(detail_url).status_code, 404)
        self.other_chamber.refresh_from_db()
        self.assertEqual(self.other_chamber.name, 'Other clinic')
        self.assertTrue(self.other_chamber.is_active)

    def test_admin_can_list_all_create_update_and_delete_chambers(self):
        self.client.force_authenticate(user=self.admin)

        list_response = self.client.get(self.list_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 2)
        self.assertEqual(
            self.client.get(
                reverse('chamber-detail', args=[self.chamber.pk])
            ).status_code,
            200,
        )

        create_response = self.client.post(
            self.list_url,
            {
                'doctor': self.other_doctor.doctor_profile.pk,
                'name': 'Admin clinic',
                'address': '4 Clinic Road',
                'is_active': True,
            },
        )
        self.assertEqual(create_response.status_code, 201)
        managed_chamber = Chamber.objects.get(pk=create_response.data['id'])
        self.assertEqual(managed_chamber.doctor_id, self.other_doctor.doctor_profile.pk)

        detail_url = reverse('chamber-detail', args=[self.chamber.pk])
        update_response = self.client.patch(detail_url, {'name': 'Admin updated'})
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data['name'], 'Admin updated')

        delete_response = self.client.delete(
            reverse('chamber-detail', args=[managed_chamber.pk])
        )
        self.assertEqual(delete_response.status_code, 204)
        managed_chamber.refresh_from_db()
        self.assertFalse(managed_chamber.is_active)
        self.assertTrue(Chamber.objects.filter(pk=managed_chamber.pk).exists())

    def test_admin_must_assign_chamber_to_an_existing_doctor_profile(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(
            self.list_url,
            {
                'doctor': 999999,
                'name': 'Invalid clinic',
                'address': '5 Clinic Road',
                'is_active': True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Chamber.objects.filter(name='Invalid clinic').exists())

    def test_admin_cannot_assign_chamber_to_a_non_doctor_profile(self):
        self.client.force_authenticate(user=self.admin)
        admin_profile = DoctorProfile.objects.create(user=self.admin)

        response = self.client.post(
            self.list_url,
            {
                'doctor': admin_profile.pk,
                'name': 'Invalid owner clinic',
                'address': '5 Clinic Road',
                'is_active': True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Chamber.objects.filter(name='Invalid owner clinic').exists())

    def test_patient_cannot_access_chamber_management_api(self):
        self.client.force_authenticate(user=self.patient)

        self.assertEqual(self.client.get(self.list_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.list_url,
                {'name': 'Unauthorized', 'address': 'No access'},
            ).status_code,
            403,
        )

    def test_deleting_chamber_preserves_schedules_and_appointments(self):
        patient = CustomUser.objects.create_user(
            email='appointment-patient@example.com',
            password='test-password',
            role=CustomUser.Role.PATIENT,
        )
        active_schedule = ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.SATURDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            appointment_duration_minutes=30,
            effective_from=date(2026, 12, 1),
        )
        inactive_schedule = ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.SUNDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            appointment_duration_minutes=30,
            effective_from=date(2026, 12, 1),
            is_active=False,
        )
        appointment = Appointment.objects.create(
            patient=patient,
            doctor=self.doctor.doctor_profile,
            chamber=self.chamber,
            appointment_date='2026-12-01',
            time_slot_start='09:00',
            time_slot_end='09:30',
        )
        self.client.force_authenticate(user=self.doctor)

        response = self.client.delete(
            reverse('chamber-detail', args=[self.chamber.pk])
        )

        self.assertEqual(response.status_code, 204)
        self.chamber.refresh_from_db()
        active_schedule.refresh_from_db()
        inactive_schedule.refresh_from_db()
        appointment.refresh_from_db()
        self.assertFalse(self.chamber.is_active)
        self.assertEqual(appointment.chamber_id, self.chamber.pk)
        self.assertEqual(appointment.doctor_id, self.doctor.doctor_profile.pk)
        self.assertTrue(active_schedule.is_active)
        self.assertFalse(inactive_schedule.is_active)
        self.assertEqual(
            ChamberSchedule.objects.filter(chamber=self.chamber).count(),
            2,
        )

    def test_schedule_history_protects_chamber_from_hard_delete(self):
        schedules = [
            ChamberSchedule.objects.create(
                chamber=self.chamber,
                weekday=weekday,
                start_time=time(9, 0),
                end_time=time(10, 0),
                appointment_duration_minutes=30,
                effective_from=date(2026, 12, 1),
                is_active=is_active,
            )
            for weekday, is_active in (
                (ChamberSchedule.Weekday.SATURDAY, True),
                (ChamberSchedule.Weekday.SUNDAY, False),
            )
        ]

        with self.assertRaises(ProtectedError):
            self.chamber.delete()

        self.assertTrue(Chamber.objects.filter(pk=self.chamber.pk).exists())
        self.assertEqual(
            set(
                ChamberSchedule.objects.filter(chamber=self.chamber).values_list(
                    'pk',
                    flat=True,
                )
            ),
            {schedule.pk for schedule in schedules},
        )

    def test_swagger_schema_lists_chamber_management_endpoints(self):
        response = self.client.get(reverse('schema'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('/api/doctors/chambers/', response.data['paths'])
        self.assertIn('/api/doctors/chambers/{id}/', response.data['paths'])


class ChamberScheduleAPITests(APITestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            email='schedule-admin@example.com',
            password='test-password',
            role=CustomUser.Role.ADMIN,
        )
        self.doctor = CustomUser.objects.create_user(
            email='schedule-doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.other_doctor = CustomUser.objects.create_user(
            email='other-schedule-doctor@example.com',
            password='test-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.patient = CustomUser.objects.create_user(
            email='schedule-patient@example.com',
            password='test-password',
            role=CustomUser.Role.PATIENT,
        )
        self.chamber = Chamber.objects.create(
            doctor=self.doctor.doctor_profile,
            name='Main schedule clinic',
            address='1 Schedule Road',
        )
        self.second_chamber = Chamber.objects.create(
            doctor=self.doctor.doctor_profile,
            name='Second schedule clinic',
            address='2 Schedule Road',
        )
        self.other_chamber = Chamber.objects.create(
            doctor=self.other_doctor.doctor_profile,
            name='Other schedule clinic',
            address='3 Schedule Road',
        )
        self.list_url = reverse(
            'chamber-schedule-list',
            args=[self.chamber.pk],
        )
        self.effective_from = timezone.localdate() + timedelta(days=1)
        self.schedule = ChamberSchedule.objects.create(
            chamber=self.chamber,
            weekday=ChamberSchedule.Weekday.SATURDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            appointment_duration_minutes=30,
            effective_from=self.effective_from,
        )
        self.detail_url = reverse(
            'chamber-schedule-detail',
            args=[self.chamber.pk, self.schedule.pk],
        )

    def payload(self, **overrides):
        data = {
            'weekday': ChamberSchedule.Weekday.SATURDAY,
            'start_time': '09:00:00',
            'end_time': '12:00:00',
            'appointment_duration_minutes': 30,
            'effective_from': self.effective_from.isoformat(),
            'effective_until': None,
        }
        data.update(overrides)
        return data

    def test_doctor_can_create_list_and_retrieve_schedules_for_own_chamber(self):
        self.client.force_authenticate(user=self.doctor)

        create_response = self.client.post(
            self.list_url,
            self.payload(weekday=ChamberSchedule.Weekday.SUNDAY),
            format='json',
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(create_response.data['chamber'], self.chamber.pk)
        self.assertEqual(create_response.data['weekday_display'], 'Sunday')

        list_response = self.client.get(self.list_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 2)

        detail_response = self.client.get(self.detail_url)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data['id'], self.schedule.pk)

    def test_schedule_cannot_overlap_across_two_chambers_of_same_doctor(self):
        self.client.force_authenticate(user=self.doctor)
        self.schedule.effective_until = self.effective_from + timedelta(days=6)
        self.schedule.save(update_fields=['effective_until'])
        second_chamber_url = reverse(
            'chamber-schedule-list',
            args=[self.second_chamber.pk],
        )

        overlapping_response = self.client.post(
            second_chamber_url,
            self.payload(),
            format='json',
        )

        self.assertEqual(overlapping_response.status_code, 400)
        self.assertEqual(
            ChamberSchedule.objects.filter(chamber=self.second_chamber).count(),
            0,
        )

        adjacent_response = self.client.post(
            second_chamber_url,
            self.payload(start_time='12:00:00', end_time='14:00:00'),
            format='json',
        )
        self.assertEqual(adjacent_response.status_code, 201)
        next_week_response = self.client.post(
            second_chamber_url,
            self.payload(
                effective_from=(self.effective_from + timedelta(days=7)).isoformat()
            ),
            format='json',
        )
        self.assertEqual(next_week_response.status_code, 201)

    def test_different_doctors_can_have_overlapping_schedules(self):
        self.client.force_authenticate(user=self.other_doctor)
        other_url = reverse(
            'chamber-schedule-list',
            args=[self.other_chamber.pk],
        )

        response = self.client.post(other_url, self.payload(), format='json')

        self.assertEqual(response.status_code, 201)

    def test_update_creates_a_new_version_and_preserves_old_schedule(self):
        self.schedule.effective_from = timezone.localdate() - timedelta(days=10)
        self.schedule.save(update_fields=['effective_from'])
        patient = CustomUser.objects.create_user(
            email='schedule-appointment-patient@example.com',
            password='test-password',
            role=CustomUser.Role.PATIENT,
        )
        appointment = Appointment.objects.create(
            patient=patient,
            doctor=self.doctor.doctor_profile,
            chamber=self.chamber,
            appointment_date=self.effective_from,
            time_slot_start='09:00',
            time_slot_end='09:30',
        )
        self.client.force_authenticate(user=self.doctor)

        response = self.client.patch(
            self.detail_url,
            {'appointment_duration_minutes': 20},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data['id'], self.schedule.pk)
        self.assertEqual(response.data['appointment_duration_minutes'], 20)
        self.schedule.refresh_from_db()
        appointment.refresh_from_db()
        self.assertTrue(self.schedule.is_active)
        self.assertEqual(
            self.schedule.effective_until,
            date.fromisoformat(response.data['effective_from'])
            - timedelta(days=1),
        )
        self.assertEqual(appointment.chamber_id, self.chamber.pk)
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())
        self.assertEqual(
            ChamberSchedule.objects.filter(chamber=self.chamber).count(),
            2,
        )

    def test_put_creates_a_new_schedule_version(self):
        self.client.force_authenticate(user=self.doctor)

        response = self.client.put(
            self.detail_url,
            self.payload(appointment_duration_minutes=20),
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data['id'], self.schedule.pk)
        self.assertEqual(response.data['appointment_duration_minutes'], 20)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)

    def test_update_cannot_rewrite_an_existing_effective_date(self):
        self.schedule.effective_from = timezone.localdate()
        self.schedule.save(update_fields=['effective_from'])
        self.client.force_authenticate(user=self.doctor)

        response = self.client.patch(
            self.detail_url,
            {'effective_from': timezone.localdate().isoformat()},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.schedule.refresh_from_db()
        self.assertTrue(self.schedule.is_active)
        self.assertEqual(self.schedule.effective_from, timezone.localdate())

    def test_delete_retires_schedule_without_deleting_history(self):
        self.client.force_authenticate(user=self.doctor)

        response = self.client.delete(self.detail_url)

        self.assertEqual(response.status_code, 204)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)
        self.assertTrue(
            ChamberSchedule.objects.filter(pk=self.schedule.pk).exists()
        )

        history_response = self.client.get(self.list_url)
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(history_response.data[0]['is_active'], False)

    def test_inactive_chamber_keeps_history_visible_but_rejects_create_and_update(self):
        self.client.force_authenticate(user=self.doctor)
        chamber_url = reverse('chamber-detail', args=[self.chamber.pk])

        deactivate_response = self.client.delete(chamber_url)
        self.assertEqual(deactivate_response.status_code, 204)

        list_response = self.client.get(self.list_url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(
            {item['id'] for item in list_response.data},
            {self.schedule.pk},
        )
        self.assertEqual(self.client.get(self.detail_url).status_code, 200)

        create_response = self.client.post(
            self.list_url,
            self.payload(weekday=ChamberSchedule.Weekday.SUNDAY),
            format='json',
        )
        update_response = self.client.patch(
            self.detail_url,
            {'appointment_duration_minutes': 20},
            format='json',
        )

        self.assertEqual(create_response.status_code, 400)
        self.assertEqual(update_response.status_code, 400)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.appointment_duration_minutes, 30)
        self.assertEqual(
            ChamberSchedule.objects.filter(chamber=self.chamber).count(),
            1,
        )

    def test_doctor_cannot_read_or_modify_another_doctors_schedule(self):
        other_schedule = ChamberSchedule.objects.create(
            chamber=self.other_chamber,
            weekday=ChamberSchedule.Weekday.SATURDAY,
            start_time=time(9, 0),
            end_time=time(12, 0),
            appointment_duration_minutes=30,
            effective_from=self.effective_from,
        )
        self.client.force_authenticate(user=self.doctor)
        other_list_url = reverse(
            'chamber-schedule-list',
            args=[self.other_chamber.pk],
        )
        other_detail_url = reverse(
            'chamber-schedule-detail',
            args=[self.other_chamber.pk, other_schedule.pk],
        )

        self.assertEqual(self.client.get(other_list_url).status_code, 404)
        self.assertEqual(self.client.get(other_detail_url).status_code, 403)
        self.assertEqual(
            self.client.patch(
                other_detail_url,
                {'appointment_duration_minutes': 20},
                format='json',
            ).status_code,
            403,
        )
        self.assertEqual(self.client.delete(other_detail_url).status_code, 403)

    def test_admin_can_manage_schedules_for_any_doctors_chamber(self):
        self.client.force_authenticate(user=self.admin)
        other_url = reverse(
            'chamber-schedule-list',
            args=[self.other_chamber.pk],
        )

        create_response = self.client.post(other_url, self.payload(), format='json')
        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(self.client.get(other_url).status_code, 200)

        schedule_id = create_response.data['id']
        update_response = self.client.patch(
            reverse(
                'chamber-schedule-detail',
                args=[self.other_chamber.pk, schedule_id],
            ),
            {'appointment_duration_minutes': 20},
            format='json',
        )
        self.assertEqual(update_response.status_code, 200)
        delete_response = self.client.delete(
            reverse(
                'chamber-schedule-detail',
                args=[self.other_chamber.pk, update_response.data['id']],
            )
        )
        self.assertEqual(delete_response.status_code, 204)

    def test_patient_and_anonymous_users_cannot_manage_schedules(self):
        anonymous_response = self.client.get(self.list_url)
        self.assertEqual(anonymous_response.status_code, 401)
        anonymous_create = self.client.post(
            self.list_url,
            self.payload(),
            format='json',
        )
        self.assertEqual(anonymous_create.status_code, 401)

        self.client.force_authenticate(user=self.patient)
        patient_response = self.client.get(self.list_url)
        self.assertEqual(patient_response.status_code, 403)
        create_response = self.client.post(
            self.list_url,
            self.payload(),
            format='json',
        )
        self.assertEqual(create_response.status_code, 403)

    def test_schedule_rejects_invalid_duration_and_effective_range(self):
        self.client.force_authenticate(user=self.doctor)

        zero_duration = self.client.post(
            self.list_url,
            self.payload(appointment_duration_minutes=0),
            format='json',
        )
        invalid_dates = self.client.post(
            self.list_url,
            self.payload(effective_until=(self.effective_from - timedelta(days=1)).isoformat()),
            format='json',
        )
        past_start_date = self.client.post(
            self.list_url,
            self.payload(
                effective_from=(timezone.localdate() - timedelta(days=1)).isoformat()
            ),
            format='json',
        )

        self.assertEqual(zero_duration.status_code, 400)
        self.assertEqual(invalid_dates.status_code, 400)
        self.assertEqual(past_start_date.status_code, 400)

    def test_swagger_schema_lists_schedule_endpoints(self):
        response = self.client.get(reverse('schema'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '/api/doctors/chambers/{chamber_pk}/schedules/',
            response.data['paths'],
        )
        self.assertIn(
            '/api/doctors/chambers/{chamber_pk}/schedules/{id}/',
            response.data['paths'],
        )
