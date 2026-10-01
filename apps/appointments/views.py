from rest_framework import viewsets, permissions
from .models import Appointment
from .serializers import AppointmentSerializer
from .permissions import IsAppointmentParticipantOrAdmin

class AppointmentViewSet(viewsets.ModelViewSet):
    serializer_class = AppointmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAppointmentParticipantOrAdmin]

    def get_queryset(self):
        user = self.request.user

        if user.role == 'ADMIN':
            return Appointment.objects.all().select_related('patient', 'doctor__user')

        if user.role == 'PATIENT':
            return Appointment.objects.filter(patient=user).select_related('patient', 'doctor__user')

        if user.role == 'DOCTOR' and hasattr(user, 'doctor_profile'):
            return Appointment.objects.filter(doctor=user.doctor_profile).select_related('patient', 'doctor__user')

        return Appointment.objects.none()

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)