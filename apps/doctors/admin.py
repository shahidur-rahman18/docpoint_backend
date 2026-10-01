from django.contrib import admin
from apps.doctors.models import DoctorProfile


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'specialization', 'consultation_fee', 'is_available']
    list_filter = ['is_available', 'specialization']
    search_fields = ['user__email', 'specialization']