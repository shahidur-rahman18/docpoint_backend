from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import CustomUser
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
