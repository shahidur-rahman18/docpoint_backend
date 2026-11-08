from rest_framework import viewsets, permissions
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Appointment
from .serializers import AppointmentSerializer
from .permissions import IsAppointmentParticipantOrAdmin


@extend_schema_view(
    list=extend_schema(
        tags=['Appointments'],
        summary='List appointments',
        description='Returns appointments visible to the authenticated user based on their role.',
    ),
    retrieve=extend_schema(tags=['Appointments'], summary='Get an appointment'),
    create=extend_schema(
        tags=['Appointments'],
        summary='Book an appointment',
        description='Books an appointment for the authenticated patient.',
    ),
    update=extend_schema(tags=['Appointments'], summary='Replace an appointment'),
    partial_update=extend_schema(tags=['Appointments'], summary='Update an appointment'),
    destroy=extend_schema(tags=['Appointments'], summary='Cancel or delete an appointment'),
)
class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = Appointment.objects.all()
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