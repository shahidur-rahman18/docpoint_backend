from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.accounts.views import AdminDoctorCreateView, PasswordSetupView, UserProfileView

urlpatterns = [
    path('admin/doctors/create/', AdminDoctorCreateView.as_view(), name='admin_doctor_create'),
    path('setup-password/', PasswordSetupView.as_view(), name='password_setup'),
    path('login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('me/', UserProfileView.as_view(), name='user_profile'),
]