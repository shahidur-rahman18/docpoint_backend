from django.utils import timezone
from rest_framework import serializers, status, viewsets, permissions
from rest_framework.exceptions import NotFound
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view
from .models import Appointment
from .serializers import (
    AppointmentBookingSerializer,
    AppointmentSerializer,
    AppointmentUpdateSerializer,
)
from .permissions import IsAppointmentParticipantOrAdmin
from apps.doctors.models import Chamber
from .services import get_available_slots


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

    def get_serializer_class(self):
        if self.action == 'create':
            return AppointmentBookingSerializer
        if self.action in {'update', 'partial_update'}:
            return AppointmentUpdateSerializer
        return self.serializer_class

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


class AvailableSlotsQuerySerializer(serializers.Serializer):
    date = serializers.DateField(input_formats=['%Y-%m-%d'])


class AvailableSlotSerializer(serializers.Serializer):
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class AvailableSlotsResponseSerializer(serializers.Serializer):
    chamber = serializers.IntegerField()
    date = serializers.DateField()
    slots = AvailableSlotSerializer(many=True)


class AvailableSlotsAPIView(GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AvailableSlotsQuerySerializer

    @extend_schema(
        tags=['Appointments'],
        summary='List available appointment slots',
        description=(
            'Returns unbooked slots for an active chamber on the requested '
            'date. The date must use YYYY-MM-DD format and cannot be in the past.'
        ),
        parameters=[AvailableSlotsQuerySerializer],
        request=None,
        responses=AvailableSlotsResponseSerializer,
    )
    def get(self, request, chamber_pk):
        query_serializer = self.get_serializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        appointment_date = query_serializer.validated_data['date']
        if appointment_date < timezone.localdate():
            raise serializers.ValidationError({
                'date': 'Appointments cannot be viewed for a past date.',
            })

        try:
            chamber = Chamber.objects.get(pk=chamber_pk, is_active=True)
        except Chamber.DoesNotExist as exc:
            raise NotFound('Active chamber not found.') from exc

        return Response({
            'chamber': chamber.pk,
            'date': appointment_date.isoformat(),
            'slots': get_available_slots(chamber, appointment_date),
        }, status=status.HTTP_200_OK)