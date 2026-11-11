import logging
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import status, viewsets, permissions
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.exceptions import NotFound
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.accounts.avatar_storage import AvatarStorageUnavailable, delete_avatar
from .models import Chamber, ChamberSchedule, DoctorProfile
from .serializers import (
    ChamberScheduleSerializer,
    ChamberSerializer,
    DoctorProfileSerializer,
)
from .filters import DoctorFilter

logger = logging.getLogger(__name__)


class DoctorDeletionConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        'This doctor cannot be deleted because chamber schedule history exists. '
        'Schedule history must be preserved.'
    )
    default_code = 'doctor_schedule_history_exists'


class IsAdminOrDoctor(permissions.BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (
                user.role == 'ADMIN'
                or user.is_superuser
                or (user.role == 'DOCTOR' and hasattr(user, 'doctor_profile'))
            )
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'ADMIN' or user.is_superuser:
            return True
        return (
            user.role == 'DOCTOR'
            and hasattr(user, 'doctor_profile')
            and obj.doctor_id == user.doctor_profile.pk
        )


class IsScheduleOwnerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return IsAdminOrDoctor().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'ADMIN' or user.is_superuser:
            return True
        return (
            user.role == 'DOCTOR'
            and hasattr(user, 'doctor_profile')
            and obj.chamber.doctor_id == user.doctor_profile.pk
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
    destroy=extend_schema(
        tags=['Doctors'],
        summary='Delete a doctor profile',
        description=(
            'Returns 409 Conflict when chamber schedule history exists. '
            'Schedule history is preserved and must not be deleted to allow '
            'the doctor deletion.'
        ),
    ),
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

        try:
            with transaction.atomic():
                user.delete()
        except ProtectedError as exc:
            raise DoctorDeletionConflict() from exc

        if avatar_public_id:
            try:
                delete_avatar(avatar_public_id, user_id)
            except AvatarStorageUnavailable:
                logger.exception(
                    'Could not remove avatar for deleted doctor user %s.',
                    user_id,
                )


@extend_schema_view(
    list=extend_schema(
        tags=['Doctors'],
        summary='List chambers',
        description=(
            'Administrators can list all chambers. Doctors can list only '
            'their own chambers.'
        ),
    ),
    retrieve=extend_schema(
        tags=['Doctors'],
        summary='Get a chamber',
        description='Doctors can retrieve only their own chambers.',
    ),
    create=extend_schema(
        tags=['Doctors'],
        summary='Create a chamber',
        description=(
            'Doctors create a chamber for their own doctor profile. '
            'Administrators must provide the owning doctor profile ID.'
        ),
    ),
    update=extend_schema(tags=['Doctors'], summary='Replace a chamber'),
    partial_update=extend_schema(tags=['Doctors'], summary='Update a chamber'),
    destroy=extend_schema(
        tags=['Doctors'],
        summary='Deactivate a chamber',
        description=(
            'Deactivates a chamber without deleting its schedules, schedule '
            'history, or appointment relationships.'
        ),
    ),
)
class ChamberViewSet(viewsets.ModelViewSet):
    serializer_class = ChamberSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrDoctor]
    queryset = Chamber.objects.select_related('doctor__user').all()

    def get_queryset(self):
        user = self.request.user
        if user.role == 'ADMIN' or user.is_superuser:
            return self.queryset
        if user.role == 'DOCTOR' and hasattr(user, 'doctor_profile'):
            return self.queryset.filter(doctor=user.doctor_profile)
        return self.queryset.none()

    def perform_create(self, serializer):
        user = self.request.user
        if user.role == 'ADMIN' or user.is_superuser:
            serializer.save()
        else:
            serializer.save(doctor=user.doctor_profile)

    def perform_destroy(self, instance):
        if instance.is_active:
            instance.is_active = False
            instance.save(update_fields=['is_active'])


@extend_schema_view(
    list=extend_schema(
        tags=['Doctors'],
        summary='List schedules for a chamber',
        description=(
            'Returns active and historical schedules for the selected chamber. '
            'Doctors can access only their own chambers; administrators can '
            'access all chambers.'
        ),
    ),
    retrieve=extend_schema(tags=['Doctors'], summary='Get a chamber schedule'),
    create=extend_schema(
        tags=['Doctors'],
        summary='Create a chamber schedule',
        description=(
            'Creates a weekly schedule. Overlapping effective schedules for '
            'the same doctor are rejected, including schedules at other chambers. '
            'Weekday values use Monday=0 through Sunday=6; choices are ordered '
            'Saturday through Friday.'
        ),
    ),
    update=extend_schema(
        tags=['Doctors'],
        summary='Replace a chamber schedule',
        description=(
            'Creates a new schedule version and preserves the previous '
            'effective schedule as history.'
        ),
    ),
    partial_update=extend_schema(
        tags=['Doctors'],
        summary='Update a chamber schedule',
        description=(
            'Creates a new schedule version and preserves the previous '
            'effective schedule as history.'
        ),
    ),
    destroy=extend_schema(
        tags=['Doctors'],
        summary='Retire a chamber schedule',
        description='Deactivates a schedule without deleting its history.',
    ),
)
class ChamberScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = ChamberScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsScheduleOwnerOrAdmin]
    queryset = ChamberSchedule.objects.select_related(
        'chamber',
        'chamber__doctor',
        'chamber__doctor__user',
    ).all()

    def get_chamber(self, lock=False, require_active=False):
        user = self.request.user
        chambers = Chamber.objects.select_related('doctor__user')
        if lock:
            chambers = Chamber.objects.select_for_update().select_related('doctor__user')
        if user.role != 'ADMIN' and not user.is_superuser:
            chambers = chambers.filter(doctor=user.doctor_profile)
        try:
            chamber = chambers.get(pk=self.kwargs['chamber_pk'])
        except Chamber.DoesNotExist as exc:
            raise NotFound('Chamber not found.') from exc
        if require_active and not chamber.is_active:
            raise ValidationError({
                'chamber': 'Schedules cannot be created or updated for an inactive chamber.',
            })
        return chamber

    def get_queryset(self):
        return self.queryset.filter(chamber_id=self.kwargs['chamber_pk'])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if (
            not getattr(self, 'swagger_fake_view', False)
            and 'chamber_pk' in self.kwargs
            and self.request.user.is_authenticated
        ):
            context['chamber'] = self.get_chamber()
        return context

    def perform_create(self, serializer):
        with transaction.atomic():
            chamber = self.get_chamber(lock=True, require_active=True)
            DoctorProfile.objects.select_for_update().get(pk=chamber.doctor_id)
            schedule = ChamberSchedule(
                chamber=chamber,
                **serializer.validated_data,
            )
            try:
                schedule.full_clean()
            except DjangoValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    raise ValidationError(exc.message_dict) from exc
                raise ValidationError(exc.messages) from exc
            schedule.save()
            serializer.instance = schedule

    def update(self, request, *args, **kwargs):
        return self._create_schedule_version(request, partial=False, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self._create_schedule_version(request, partial=True, **kwargs)

    def _create_schedule_version(self, request, partial, **kwargs):
        instance = self.get_object()
        if not instance.is_active:
            raise ValidationError({'detail': 'Inactive schedules cannot be updated.'})

        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            chamber = self.get_chamber(lock=True, require_active=True)
            DoctorProfile.objects.select_for_update().get(pk=chamber.doctor_id)
            current = self.get_queryset().select_for_update().get(pk=instance.pk)
            if not current.is_active:
                raise ValidationError({
                    'detail': 'Inactive schedules cannot be updated.',
                })

            effective_from = serializer.validated_data.get(
                'effective_from',
                max(timezone.localdate() + timedelta(days=1), current.effective_from),
            )
            if (
                current.effective_from <= timezone.localdate()
                and effective_from <= current.effective_from
            ):
                raise ValidationError({
                    'effective_from': (
                        'A schedule version for an existing effective date '
                        'must start after the current schedule version.'
                    ),
                })
            replacement = ChamberSchedule(
                chamber=current.chamber,
                weekday=serializer.validated_data.get('weekday', current.weekday),
                start_time=serializer.validated_data.get(
                    'start_time',
                    current.start_time,
                ),
                end_time=serializer.validated_data.get('end_time', current.end_time),
                appointment_duration_minutes=serializer.validated_data.get(
                    'appointment_duration_minutes',
                    current.appointment_duration_minutes,
                ),
                effective_from=effective_from,
                effective_until=serializer.validated_data.get(
                    'effective_until',
                    current.effective_until,
                ),
            )

            if effective_from > current.effective_from:
                if (
                    current.effective_until is None
                    or current.effective_until >= effective_from
                ):
                    current.effective_until = effective_from - timedelta(days=1)
                    current.save(update_fields=['effective_until'])
            else:
                current.is_active = False
                current.save(update_fields=['is_active'])

            try:
                replacement.full_clean()
            except DjangoValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    raise ValidationError(exc.message_dict) from exc
                raise ValidationError(exc.messages) from exc
            replacement.save()

        response_serializer = self.get_serializer(replacement)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    def perform_destroy(self, instance):
        with transaction.atomic():
            chamber = self.get_chamber(lock=True)
            DoctorProfile.objects.select_for_update().get(pk=chamber.doctor_id)
            current = self.get_queryset().select_for_update().get(pk=instance.pk)
            if current.is_active:
                current.is_active = False
                current.save(update_fields=['is_active'])