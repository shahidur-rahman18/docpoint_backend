from rest_framework import permissions

class IsAppointmentParticipantOrAdmin(permissions.BasePermission):
    """
    রোগী, ডাক্তার অথবা অ্যাডমিন ছাড়া অন্য কেউ অ্যাপয়েন্টমেন্ট দেখতে বা পরিবর্তন করতে পারবে না।
    """
    def has_object_permission(self, request, view, obj):
        if request.user.role == 'ADMIN':
            return True

        if obj.patient == request.user:
            return True

        if hasattr(request.user, 'doctor_profile') and obj.doctor == request.user.doctor_profile:
            return True

        return False