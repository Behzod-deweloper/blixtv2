import logging

from botocore.exceptions import BotoCoreError
from django.core.files.storage import default_storage
from rest_framework import generics
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import AccountSerializer, AvatarUploadSerializer, RegistrationSerializer


logger = logging.getLogger(__name__)


class RegistrationView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"


class CurrentAccountView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def get(self, request):
        return Response(AccountSerializer(request.user).data)

    def patch(self, request):
        serializer = AvatarUploadSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        avatar_directory = f"users/{user.pk}/avatar"
        try:
            _, existing_avatars = default_storage.listdir(avatar_directory)
            for filename in existing_avatars:
                stored_name = f"{avatar_directory}/{filename}"
                if stored_name != user.avatar.name:
                    default_storage.delete(stored_name)
        except (BotoCoreError, OSError, NotImplementedError):
            logger.exception("Avatar was updated but old avatar files could not be removed for user %s.", user.pk)
            return Response(
                {"detail": "Avatar was updated, but older avatar files could not be removed. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(AccountSerializer(user).data)


class GoogleSessionTokenView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "google_session"

    def get(self, request):
        refresh = RefreshToken.for_user(request.user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            }
        )