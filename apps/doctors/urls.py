from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DoctorProfileViewSet

router = DefaultRouter()
router.register(r'', DoctorProfileViewSet, basename='doctor')

urlpatterns = [
    path('', include(router.urls)),
]