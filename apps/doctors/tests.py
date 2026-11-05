from unittest.mock import patch

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.avatar_storage import AvatarStorageUnavailable
from apps.accounts.models import CustomUser
from apps.appointments.models import Appointment
from apps.doctors.models import DoctorProfile


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
