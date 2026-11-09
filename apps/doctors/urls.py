from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DoctorProfileViewSet, DoctorScheduleView

router = DefaultRouter()
router.register(r'', DoctorProfileViewSet, basename='doctor')

urlpatterns = [
    path('me/schedule/', DoctorScheduleView.as_view(), name='doctor-schedule'),
    path('', include(router.urls)),
]