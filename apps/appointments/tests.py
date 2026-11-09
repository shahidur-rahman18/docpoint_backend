from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.appointments.models import Appointment
from apps.doctors.models import DoctorSchedule, empty_weekly_schedule


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


class DoctorAvailabilityBookingTests(TestCase):
    def setUp(self):
        self.doctor_user = CustomUser.objects.create_user(
            email='available-doctor@example.test',
            password='safe-doctor-password',
            role=CustomUser.Role.DOCTOR,
        )
        self.doctor = self.doctor_user.doctor_profile
        self.patient = CustomUser.objects.create_user(
            email='booking-patient@example.test',
            password='safe-patient-password',
            role=CustomUser.Role.PATIENT,
        )
        self.appointment_date = timezone.localdate() + timedelta(days=1)
        weekly_schedule = empty_weekly_schedule()
        weekday = self.appointment_date.strftime('%A').upper()
        weekly_schedule[weekday] = [{'start': '09:00', 'end': '13:00'}]
        DoctorSchedule.objects.create(
            doctor=self.doctor,
            effective_from=self.appointment_date,
            timezone='Asia/Dhaka',
            weekly_schedule=weekly_schedule,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.patient)
        self.payload = {
            'doctor': self.doctor.id,
            'appointment_date': self.appointment_date.isoformat(),
            'time_slot_start': '10:00',
            'time_slot_end': '10:30',
        }

    def test_patient_can_book_within_doctor_availability(self):
        response = self.client.post(
            reverse('appointment-list'),
            self.payload,
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['doctor'], self.doctor.id)

    def test_patient_cannot_book_outside_doctor_availability(self):
        self.payload['time_slot_start'] = '14:00'
        self.payload['time_slot_end'] = '14:30'

        response = self.client.post(
            reverse('appointment-list'),
            self.payload,
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('appointment_date', response.data)

    def test_patient_cannot_book_before_doctor_sets_availability(self):
        DoctorSchedule.objects.filter(doctor=self.doctor).delete()

        response = self.client.post(
            reverse('appointment-list'),
            self.payload,
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('appointment_date', response.data)
