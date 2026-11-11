from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.appointments.views import AvailableSlotsAPIView
from .views import (
    ChamberScheduleViewSet,
    ChamberViewSet,
    DoctorProfileViewSet,
)

router = DefaultRouter()
router.register(r'', DoctorProfileViewSet, basename='doctor')

chamber_router = DefaultRouter()
chamber_router.register(r'', ChamberViewSet, basename='chamber')

urlpatterns = [
    path(
        'chambers/<int:chamber_pk>/available-slots/',
        AvailableSlotsAPIView.as_view(),
        name='chamber-available-slots',
    ),
    path(
        'chambers/<int:chamber_pk>/schedules/',
        ChamberScheduleViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='chamber-schedule-list',
    ),
    path(
        'chambers/<int:chamber_pk>/schedules/<int:pk>/',
        ChamberScheduleViewSet.as_view({
            'get': 'retrieve',
            'put': 'update',
            'patch': 'partial_update',
            'delete': 'destroy',
        }),
        name='chamber-schedule-detail',
    ),
    path('chambers/', include(chamber_router.urls)),
    path('', include(router.urls)),
]