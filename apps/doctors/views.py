import logging

from django.db import transaction
from rest_framework import viewsets, permissions
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from drf_spectacular.utils import extend_schema, extend_schema_view

from apps.accounts.avatar_storage import AvatarStorageUnavailable, delete_avatar
from .models import DoctorProfile
from .serializers import DoctorProfileSerializer
from .filters import DoctorFilter

logger = logging.getLogger(__name__)


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
    list=extend_schema(auth=[]),
    retrieve=extend_schema(auth=[]),
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