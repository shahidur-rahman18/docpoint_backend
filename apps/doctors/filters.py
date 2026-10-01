import django_filters
from .models import DoctorProfile

class DoctorFilter(django_filters.FilterSet):
    specialization = django_filters.CharFilter(
        field_name='specialization',
        lookup_expr='icontains'
    )
    min_fee = django_filters.NumberFilter(
        field_name='consultation_fee',
        lookup_expr='gte'
    )
    max_fee = django_filters.NumberFilter(
        field_name='consultation_fee',
        lookup_expr='lte'
    )
    is_available = django_filters.BooleanFilter(
        field_name='is_available'
    )

    class Meta:
        model = DoctorProfile
        fields = ['specialization', 'min_fee', 'max_fee', 'is_available']