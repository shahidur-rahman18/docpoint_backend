from rest_framework import viewsets, permissions
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from .models import DoctorProfile
from .serializers import DoctorProfileSerializer
from .filters import DoctorFilter
from apps.accounts.permissions import IsAdminRole, IsDoctorRole


class IsDoctorOwnerOrAdmin(permissions.BasePermission):
    """
    Custom Permission: Allow anyone to READ (GET).
    Only the owner (Doctor) or Admin can WRITE (PATCH/PUT).
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        if request.user.role == 'ADMIN' or request.user.is_staff:
            return True
        return obj.user == request.user


class DoctorProfileViewSet(viewsets.ModelViewSet):
    queryset = DoctorProfile.objects.select_related('user').all()
    serializer_class = DoctorProfileSerializer
    permission_classes = [IsDoctorOwnerOrAdmin]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = DoctorFilter
    search_fields = ['specialization', 'qualification', 'user__first_name', 'user__last_name', 'user__email']
    ordering_fields = ['consultation_fee', 'id']
    ordering = ['id']