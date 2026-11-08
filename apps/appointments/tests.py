from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.appointments.models import Appointment


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
