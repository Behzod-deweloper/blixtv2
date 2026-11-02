from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.throttling import ScopedRateThrottle

from .views import CurrentAccountView, GoogleSessionTokenView, RegistrationView


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"


urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("google/session/", GoogleSessionTokenView.as_view(), name="google-session-token"),
    path("me/", CurrentAccountView.as_view(), name="current-account"),
]