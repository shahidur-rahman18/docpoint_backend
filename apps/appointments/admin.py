from django.contrib import admin
from .models import Appointment

@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 
        'patient', 
        'doctor', 
        'appointment_date', 
        'time_slot_start', 
        'time_slot_end', 
        'total_fee', 
        'status', 
        'created_at'
    )
    list_filter = ('status', 'appointment_date', 'doctor')
    search_fields = ('patient__email', 'doctor__user__email', 'symptoms_note')
    ordering = ('-appointment_date', '-time_slot_start')