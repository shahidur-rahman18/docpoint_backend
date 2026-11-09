import logging

from django.db import transaction
from rest_framework import status, viewsets, permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.accounts.avatar_storage import AvatarStorageUnavailable, delete_avatar
from .models import DoctorProfile, DoctorSchedule, empty_weekly_schedule
from .serializers import DoctorProfileSerializer, DoctorScheduleSerializer
from .filters import DoctorFilter

logger = logging.getLogger(__name__)


class IsDoctorWithProfile(permissions.BasePermission):
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == 'DOCTOR'
            and hasattr(request.user, 'doctor_profile')
        )


@extend_schema(
    tags=['Doctors'],
    summary='Get the authenticated doctor schedule',
    responses=DoctorScheduleSerializer,
)
class DoctorScheduleView(APIView):
    permission_classes = [IsAuthenticated, IsDoctorWithProfile]

    def get(self, request):
        schedule = DoctorSchedule.objects.filter(
            doctor=request.user.doctor_profile,
        ).first()
        if schedule is None:
            return Response({
                'effective_from': None,
                'effective_until': None,
                'timezone': 'Asia/Dhaka',
                'weekly_schedule': empty_weekly_schedule(),
            })
        return Response(DoctorScheduleSerializer(schedule).data)

    @extend_schema(
        summary='Replace the authenticated doctor schedule',
        request=DoctorScheduleSerializer,
        responses=DoctorScheduleSerializer,
    )
    def put(self, request):
        with transaction.atomic():
            doctor = DoctorProfile.objects.select_for_update().get(user=request.user)
            schedule = DoctorSchedule.objects.filter(doctor=doctor).first()
            serializer = DoctorScheduleSerializer(
                instance=schedule,
                data=request.data,
            )
            serializer.is_valid(raise_exception=True)
            saved_schedule = serializer.save(doctor=doctor)

        return Response(
            DoctorScheduleSerializer(saved_schedule).data,
            status=status.HTTP_200_OK,
        )


class IsDoctorOwnerOrAdmin(permissions.BasePermission):
    """Allow public reads, owner/admin updates, and admin-only deletion."""

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method == 'DELETE':
            return request.user.role == 'ADMIN' or request.user.is_superuser
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.method == 'DELETE':
            return request.user.role == 'ADMIN' or request.user.is_superuser
        if request.user.role == 'ADMIN' or request.user.is_staff or request.user.is_superuser:
            return True
        return obj.user == request.user


@extend_schema_view(
    list=extend_schema(
        auth=[],
        tags=['Doctors'],
        summary='List doctors',
        description='Search and filter the publicly available doctor profiles.',
    ),
    retrieve=extend_schema(
        auth=[],
        tags=['Doctors'],
        summary='Get a doctor profile',
    ),
    create=extend_schema(tags=['Doctors'], summary='Create a doctor profile'),
    update=extend_schema(tags=['Doctors'], summary='Replace a doctor profile'),
    partial_update=extend_schema(tags=['Doctors'], summary='Update a doctor profile'),
    destroy=extend_schema(tags=['Doctors'], summary='Delete a doctor profile'),
)
class DoctorProfileViewSet(viewsets.ModelViewSet):
    queryset = DoctorProfile.objects.select_related('user').all()
    serializer_class = DoctorProfileSerializer
    permission_classes = [IsDoctorOwnerOrAdmin]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = DoctorFilter
    search_fields = ['specialization', 'qualification', 'user__first_name', 'user__last_name', 'user__email']
    ordering_fields = ['consultation_fee', 'id']
    ordering = ['id']

    def perform_destroy(self, instance):
        user = instance.user
        user_id = user.pk
        avatar_public_id = user.avatar_public_id

        with transaction.atomic():
            user.delete()

        if avatar_public_id:
            try:
                delete_avatar(avatar_public_id, user_id)
            except AvatarStorageUnavailable:
                logger.exception(
                    'Could not remove avatar for deleted doctor user %s.',
                    user_id,
                )