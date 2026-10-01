from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from apps.accounts.models import CustomUser, PatientProfile


class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['email', 'role', 'phone_number', 'is_staff', 'is_active']
    list_filter = ['role', 'is_staff', 'is_active']
    fieldsets = UserAdmin.fieldsets + (
        ('Extra Info', {'fields': ('role', 'phone_number')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Extra Info', {'fields': ('role', 'phone_number')}),
    )
    ordering = ['email']


admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(PatientProfile)