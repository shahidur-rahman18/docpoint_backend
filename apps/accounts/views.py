from django.conf import settings
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.accounts.permissions import IsAdminRole
from apps.accounts.serializers import (
    AdminDoctorCreateSerializer,
    PasswordSetupSerializer,
    PasswordSetupTokenResponseSerializer,
    UserProfileSerializer,
)


User = get_user_model()


class AdminDoctorCreateView(generics.CreateAPIView):
    serializer_class = AdminDoctorCreateSerializer
    permission_classes = [IsAdminRole]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "message": "Doctor account created successfully. Verification email with password setup link has been sent.",
                "email": user.email
            },
            status=status.HTTP_201_CREATED
        )


class PasswordSetupView(generics.GenericAPIView):
    serializer_class = PasswordSetupSerializer
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses={status.HTTP_200_OK: PasswordSetupTokenResponseSerializer})
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = serializer.save()
        return Response(tokens, status=status.HTTP_200_OK)


class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


    # ২. নতুন কুকি-বেসড লগইন ভিউ এখানে যুক্ত করা হলো
class CookieTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            data = response.data
            refresh_token = data.get('refresh')
            access_token = data.get('access')

            # HttpOnly কুকি সেট করা হচ্ছে
            response.set_cookie(
                key='refresh_token',
                value=refresh_token,
                httponly=True,
                secure=not settings.DEBUG,  # প্রোডাকশনে HTTPS থাকলে True হবে, লোকালহোস্টে HTTP এর জন্য False থাকবে
                samesite='Lax',
                max_age=7 * 24 * 60 * 60,  # ৭ দিন
                path='/'
            )

            response.set_cookie(
                key='access_token',
                value=access_token,
                httponly=True,
                secure=not settings.DEBUG,
                samesite='Lax',
                max_age=30 * 60,  # ৩০ মিনিট
                path='/'
            )

            # সিকিউরিটি বাড়ানোর জন্য রেসপন্স বডি থেকে টোকেনগুলো সরিয়ে দেওয়া হলো
            response.data = {
                "success": True,
                "message": "Login successful"
            }

        return response




class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        # রিকোয়েস্ট কুকি থেকে refresh_token নিয়ে সেটাকে request.data তে যুক্ত করা হচ্ছে
        refresh_token = request.COOKIES.get('refresh_token')

        if refresh_token:
            request.data['refresh'] = refresh_token
        elif not request.data.get('refresh'):
            return Response(
                {"detail": "Refresh token is missing or expired."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            data = response.data
            new_access_token = data.get('access')

            # নতুন access_token কে আবার HttpOnly কুকি হিসেবে সেট করা হচ্ছে
            response.set_cookie(
                key='access_token',
                value=new_access_token,
                httponly=True,
                secure=not settings.DEBUG,
                samesite='Lax',
                max_age=30 * 60,  # ৩০ মিনিট
                path='/'
            )

            # বডি থেকে অ্যাক্সেস টোকেন সরিয়ে সফলতার মেসেজ দেওয়া হচ্ছে
            response.data = {
                "success": True,
                "message": "Token refreshed successfully"
            }

        return response



class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        response = Response(
            {"success": True, "message": "Logged out successfully"},
            status=status.HTTP_200_OK
        )
        # ব্রাউজার থেকে কুকিগুলো মুছে ফেলা হচ্ছে
        response.delete_cookie('access_token', path='/')
        response.delete_cookie('refresh_token', path='/')
        return response