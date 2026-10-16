from rest_framework_simplejwt.authentication import JWTAuthentication

class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        # প্রথমে ডিফল্ট নিয়মে হেডার থেকে টোকেন নেওয়ার চেষ্টা করবে
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
            if raw_token is not None:
                validated_token = self.get_validated_token(raw_token)
                return self.get_user(validated_token), validated_token

        # যদি হেডারে না পায়, তবে কুকি থেকে access_token রিড করবে
        raw_token = request.COOKIES.get('access_token')
        if raw_token:
            validated_token = self.get_validated_token(raw_token)
            return self.get_user(validated_token), validated_token

        return None