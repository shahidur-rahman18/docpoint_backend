from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.appointments.models import Appointment
from apps.accounts.models import MedicalRecord
from apps.accounts.permissions import IsAdminRole, IsPatientRole
from apps.accounts.serializers import (
    AdminDoctorCreateSerializer,
    AdminPatientListSerializer,
    AdminPatientUpdateSerializer,
    EmailVerifySerializer,
    ForgotPasswordSerializer,
    AccountCreationResponseSerializer,
    LoginResponseSerializer,
    MedicalRecordSerializer,
    MessageResponseSerializer,
    PatientProfileSerializer,
    PatientSignupSerializer,
    PasswordSetupSerializer,
    PasswordSetupTokenResponseSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    ResendOTPSerializer,
    SuccessMessageResponseSerializer,
    TokenRefreshResponseSerializer,
    UserProfileSerializer,
)


User = get_user_model()

DEMO_ROLE_SETTINGS = {
    'admin': ('DEMO_ADMIN_EMAIL', 'DEMO_ADMIN_PASSWORD', User.Role.ADMIN),
    'doctor': ('DEMO_DOCTOR_EMAIL', 'DEMO_DOCTOR_PASSWORD', User.Role.DOCTOR),
}


def _set_auth_cookies(response, access_token, refresh_token):
    is_prod = not settings.DEBUG
    samesite_val = 'None' if is_prod else 'Lax'
    response.set_cookie(
        key='refresh_token',
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite=samesite_val,
        max_age=7 * 24 * 60 * 60,
        path='/',
    )
    response.set_cookie(
        key='access_token',
        value=access_token,
        httponly=True,
        secure=is_prod,
        samesite=samesite_val,
        max_age=30 * 60,
        path='/',
    )


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Register a patient account',
        description='Creates an inactive patient account and sends an email verification code.',
        responses={status.HTTP_201_CREATED: AccountCreationResponseSerializer},
    ),
)
class PatientSignupView(generics.CreateAPIView):
    serializer_class = PatientSignupSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {'message': 'Verification code sent to your email.', 'email': user.email},
            status=status.HTTP_201_CREATED,
        )


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Verify patient email',
        description='Verifies the email address and returns authentication tokens.',
        responses={status.HTTP_200_OK: PasswordSetupTokenResponseSerializer},
    ),
)
class EmailVerifyView(generics.GenericAPIView):
    serializer_class = EmailVerifySerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = serializer.save()
        response = Response(tokens, status=status.HTTP_200_OK)

        is_prod = not settings.DEBUG
        samesite = 'None' if is_prod else 'Lax'
        response.set_cookie(
            'access_token',
            tokens['access'],
            httponly=True,
            secure=is_prod,
            samesite=samesite,
            max_age=30 * 60,
            path='/',
        )
        response.set_cookie(
            'refresh_token',
            tokens['refresh'],
            httponly=True,
            secure=is_prod,
            samesite=samesite,
            max_age=7 * 24 * 60 * 60,
            path='/',
        )
        return response


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Resend email verification code',
        responses={status.HTTP_200_OK: MessageResponseSerializer},
    ),
)
class ResendOTPView(generics.GenericAPIView):
    serializer_class = ResendOTPSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'If the account needs verification, a code has been sent.'},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Request a password reset',
        responses={status.HTTP_200_OK: MessageResponseSerializer},
    ),
)
class ForgotPasswordView(generics.GenericAPIView):
    serializer_class = ForgotPasswordSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'If the account exists, a password reset email has been sent.'},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Confirm password reset',
        responses={status.HTTP_200_OK: MessageResponseSerializer},
    ),
)
class PasswordResetConfirmView(generics.GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'Password reset successful.'},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    post=extend_schema(
        tags=['Authentication'],
        summary='Change the authenticated user password',
        responses={status.HTTP_200_OK: MessageResponseSerializer},
    ),
)
class PasswordChangeView(generics.GenericAPIView):
    serializer_class = PasswordChangeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'message': 'Password changed successfully.'},
            status=status.HTTP_200_OK,
        )


@extend_schema_view(
    get=extend_schema(tags=['Patients'], summary='Get patient profile'),
    put=extend_schema(tags=['Patients'], summary='Replace patient profile'),
    patch=extend_schema(tags=['Patients'], summary='Update patient profile'),
)
class PatientProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = PatientProfileSerializer
    permission_classes = [permissions.IsAuthenticated, IsPatientRole]

    def get_object(self):
        return self.request.user.patient_profile


@extend_schema_view(
    get=extend_schema(tags=['Patients'], summary='List patient medical records'),
    post=extend_schema(tags=['Patients'], summary='Create a patient medical record'),
)
class MedicalRecordListCreateView(generics.ListCreateAPIView):
    serializer_class = MedicalRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsPatientRole]

    def get_queryset(self):
        return MedicalRecord.objects.filter(patient=self.request.user.patient_profile)

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user.patient_profile)


@extend_schema_view(
    get=extend_schema(tags=['Patients'], summary='Get a patient medical record'),
    put=extend_schema(tags=['Patients'], summary='Replace a patient medical record'),
    patch=extend_schema(tags=['Patients'], summary='Update a patient medical record'),
    delete=extend_schema(tags=['Patients'], summary='Delete a patient medical record'),
)
class MedicalRecordDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = MedicalRecordSerializer
    permission_classes = [permissions.IsAuthenticated, IsPatientRole]

    def get_queryset(self):
        return MedicalRecord.objects.filter(patient=self.request.user.patient_profile)


def _admin_patient_queryset():
    appointment_model = Appointment.AppointmentStatus
    upcoming_appointments = Appointment.objects.filter(
        status__in=[
            appointment_model.PENDING,
            appointment_model.CONFIRMED,
        ],
        appointment_date__gte=timezone.localdate(),
    ).select_related('doctor__user').order_by('appointment_date', 'time_slot_start')
    completed_visits = Appointment.objects.filter(
        status=appointment_model.COMPLETED,
    ).select_related('doctor__user').order_by('-appointment_date', '-time_slot_start')
    return (
        User.objects.filter(role=User.Role.PATIENT)
        .select_related('patient_profile')
        .prefetch_related(
            Prefetch(
                'patient_appointments',
                queryset=upcoming_appointments,
                to_attr='upcoming_patient_appointments',
            ),
            Prefetch(
                'patient_appointments',
                queryset=completed_visits,
                to_attr='completed_patient_appointments',
            ),
        )
        .order_by('id')
    )


@extend_schema_view(
    get=extend_schema(
        tags=['Patients'],
        summary='List all patients (admin only)',
        description=(
            'Returns patient accounts with their patient profiles, next scheduled appointment '
            'doctor (or latest completed visit doctor), and latest completed visit date. '
            'Requires an administrator account.'
        ),
    ),
)
class AdminPatientListView(generics.ListAPIView):
    serializer_class = AdminPatientListSerializer
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        return _admin_patient_queryset()


@extend_schema_view(
    get=extend_schema(tags=['Patients'], summary='Get patient details (admin only)'),
    patch=extend_schema(tags=['Patients'], summary='Update a patient (admin only)'),
    put=extend_schema(tags=['Patients'], summary='Replace patient details (admin only)'),
    delete=extend_schema(
        tags=['Patients'],
        summary='Permanently delete a patient and related records (admin only)',
        description=(
            'Permanently deletes the patient account, profile, appointments, and medical records.'
        ),
    ),
)
class AdminPatientDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AdminPatientListSerializer
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        return _admin_patient_queryset()

    def get_serializer_class(self):
        if self.request.method in {'PATCH', 'PUT'}:
            return AdminPatientUpdateSerializer
        return AdminPatientListSerializer


@extend_schema_view(
    post=extend_schema(
        tags=['Doctors'],
        summary='Create a doctor account',
        description='Admin-only endpoint. Creates the doctor account and sends a password setup email.',
        responses={status.HTTP_201_CREATED: AccountCreationResponseSerializer},
    ),
)
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


@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Set up a doctor password',
    ),
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


@extend_schema_view(
    get=extend_schema(tags=['Accounts'], summary='Get the authenticated user profile'),
    put=extend_schema(tags=['Accounts'], summary='Replace the authenticated user profile'),
    patch=extend_schema(tags=['Accounts'], summary='Update the authenticated user profile'),
)
class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


    # ২. নতুন কুকি-বেসড লগইন ভিউ এখানে যুক্ত করা হলো
@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Log in',
        description='Returns access and refresh tokens and sets them as HttpOnly cookies.',
        responses={status.HTTP_200_OK: LoginResponseSerializer},
    ),
)
class CookieTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            data = response.data
            refresh_token = data.get('refresh')
            access_token = data.get('access')
            _set_auth_cookies(response, access_token, refresh_token)

            # কুকির পাশাপাশি বডিতেও টোকেন রিটার্ন করা হচ্ছে (হাইব্রিড সাপোর্ট)
            response.data = {
                "success": True,
                "message": "Login successful",
                "access": access_token,
                "refresh": refresh_token,
            }

        return response


@extend_schema_view(
    get=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Get the configured demo account email',
        responses={status.HTTP_200_OK: OpenApiResponse(description='The demo account email.')},
    ),
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Log in to a demo account',
        description='Authenticates using the selected demo account credentials stored on the server.',
        responses={status.HTTP_200_OK: LoginResponseSerializer},
    ),
)
class DemoLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'demo_login'

    def _demo_account(self, role):
        if not isinstance(role, str) or role not in DEMO_ROLE_SETTINGS:
            return None

        email_setting, password_setting, expected_role = DEMO_ROLE_SETTINGS[role]
        email = getattr(settings, email_setting, '')
        password = getattr(settings, password_setting, '')
        if not email or not password:
            return None

        return email, password, expected_role

    def get(self, request, *args, **kwargs):
        role = request.query_params.get('role')
        demo_account = self._demo_account(role)
        if not demo_account:
            return Response(
                {'detail': 'Demo login is unavailable for this role.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({'email': demo_account[0]}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        role = request.data.get('role')
        demo_account = self._demo_account(role)
        if not demo_account:
            return Response(
                {'detail': 'Demo login is unavailable for this role.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        email, password, expected_role = demo_account
        user = authenticate(request, email=email, password=password)
        if not user or user.role != expected_role:
            return Response(
                {'detail': 'Demo login is unavailable for this role.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)
        response = Response(
            {
                'success': True,
                'message': 'Login successful',
                'access': access_token,
                'refresh': refresh_token,
            },
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, access_token, refresh_token)
        return response




@extend_schema_view(
    post=extend_schema(
        auth=[],
        tags=['Authentication'],
        summary='Refresh access token',
        description='Accepts a refresh token from the request cookie or request body.',
        responses={status.HTTP_200_OK: TokenRefreshResponseSerializer},
    ),
)
class CookieTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        # রিকোয়েস্ট কুকি অথবা রিকোয়েস্ট বডি থেকে refresh_token গ্রহণ করা হচ্ছে
        refresh_token = request.COOKIES.get('refresh_token') or request.data.get('refresh')

        if refresh_token:
            request.data['refresh'] = refresh_token
        else:
            return Response(
                {"detail": "Refresh token is missing or expired."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            data = response.data
            new_access_token = data.get('access')

            is_prod = not settings.DEBUG
            samesite_val = 'None' if is_prod else 'Lax'

            # নতুন access_token কে আবার HttpOnly কুকি হিসেবে সেট করা হচ্ছে
            response.set_cookie(
                key='access_token',
                value=new_access_token,
                httponly=True,
                secure=is_prod,
                samesite=samesite_val,
                max_age=30 * 60,  # ৩০ মিনিট
                path='/'
            )

            # কুকির পাশাপাশি বডিতেও নতুন অ্যাক্সেস টোকেন দেওয়া হচ্ছে
            response.data = {
                "success": True,
                "message": "Token refreshed successfully",
                "access": new_access_token,
            }

        return response



@extend_schema_view(
    post=extend_schema(
        tags=['Authentication'],
        summary='Log out',
        description='Clears the access and refresh token cookies.',
        request=None,
        responses={status.HTTP_200_OK: SuccessMessageResponseSerializer},
    ),
)
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        response = Response(
            {"success": True, "message": "Logged out successfully"},
            status=status.HTTP_200_OK
        )
        is_prod = not settings.DEBUG
        samesite_val = 'None' if is_prod else 'Lax'
        # ব্রাউজার থেকে কুকিগুলো মুছে ফেলা হচ্ছে
        response.delete_cookie('access_token', path='/', samesite=samesite_val)
        response.delete_cookie('refresh_token', path='/', samesite=samesite_val)
        return response