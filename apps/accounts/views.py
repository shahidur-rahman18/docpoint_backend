from rest_framework import generics, permissions, status
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from apps.accounts.serializers import (
    AdminDoctorCreateSerializer,
    PasswordSetupSerializer,
    UserProfileSerializer
)
from apps.accounts.permissions import IsAdminRole

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

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"message": "Password set successfully and account activated. You can now login."},
            status=status.HTTP_200_OK
        )


class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user