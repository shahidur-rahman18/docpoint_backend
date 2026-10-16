from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
# ১. ডিফল্ট TokenObtainPairView বাদ দিয়ে আমদের কাস্টম CookieTokenObtainPairView ইমপোর্ট করতে হবে
from apps.accounts.views import (
    AdminDoctorCreateView,
    PasswordSetupView,
    UserProfileView,
    CookieTokenObtainPairView,
    CookieTokenRefreshView,
    LogoutView
)

urlpatterns = [
    path('admin/doctors/create/', AdminDoctorCreateView.as_view(), name='admin_doctor_create'),
    path('setup-password/', PasswordSetupView.as_view(), name='password_setup'),

    # ২. এখানে ডিফল্ট ভিউয়ের জায়গায় আমাদের কাস্টম কুকি ভিউ বসানো হলো
    path('login/', CookieTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', CookieTokenRefreshView.as_view(), name='token_refresh'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('me/', UserProfileView.as_view(), name='user_profile'),
]