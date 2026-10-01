from rest_framework.permissions import BasePermission
from apps.accounts.models import CustomUser


class IsAdminRole(BasePermission):
    """
    Allows access only to users with ADMIN role or superusers.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            (request.user.role == CustomUser.Role.ADMIN or request.user.is_superuser)
        )


class IsDoctorRole(BasePermission):
    """
    Allows access only to users with DOCTOR role.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == CustomUser.Role.DOCTOR
        )


class IsPatientRole(BasePermission):
    """
    Allows access only to users with PATIENT role.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            request.user.role == CustomUser.Role.PATIENT
        )