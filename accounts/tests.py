import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import Client, SimpleTestCase, TestCase
from django.test import override_settings
from rest_framework.test import APITestCase


class MySQLDatabaseSettingsTests(SimpleTestCase):
    def test_default_database_uses_mysql_with_utf8mb4_and_strict_mode(self):
        from django.conf import settings

        database = settings.DATABASES["default"]

        self.assertEqual(database["ENGINE"], "django.db.backends.mysql")
        self.assertEqual(database["OPTIONS"]["charset"], "utf8mb4")
        self.assertIn("STRICT_TRANS_TABLES", database["OPTIONS"]["init_command"])


class GoogleOAuthConfigurationTests(TestCase):
    def test_allauth_google_login_and_callback_routes_are_registered(self):
        from django.urls import resolve, reverse

        self.assertEqual(reverse("google_login"), "/accounts/google/login/")
        self.assertEqual(
            reverse("google_callback"),
            "/accounts/google/login/callback/",
        )
        self.assertEqual(
            resolve("/accounts/google/login/callback/").url_name,
            "google_callback",
        )

    def test_google_login_get_redirects_directly_to_google(self):
        response = Client().get(
            "/accounts/google/login/",
            HTTP_HOST="127.0.0.1:8000",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_google_provider_uses_profile_and_email_scopes(self):
        from django.conf import settings
        from allauth.socialaccount.adapter import _build_apps_from_settings

        self.assertTrue(settings.SOCIALACCOUNT_LOGIN_ON_GET)
        self.assertEqual(
            settings.SOCIALACCOUNT_PROVIDERS["google"]["SCOPE"],
            ["profile", "email"],
        )
        self.assertEqual(
            len(settings.SOCIALACCOUNT_PROVIDERS["google"]["APPS"]),
            1,
        )
        configured_app = _build_apps_from_settings(provider="google")["google"][0]
        self.assertEqual(configured_app.client_id, settings.GOOGLE_OAUTH_CLIENT_ID)
        self.assertEqual(configured_app.secret, settings.GOOGLE_OAUTH_CLIENT_SECRET)
        self.assertIn(
            "allauth.account.auth_backends.AuthenticationBackend",
            settings.AUTHENTICATION_BACKENDS,
        )
        self.assertEqual(
            settings.LOGIN_REDIRECT_URL,
            f"{settings.FRONTEND_URL}/auth/google/callback",
        )
        self.assertEqual(settings.LOGOUT_REDIRECT_URL, settings.FRONTEND_URL)
        self.assertEqual(
            settings.SOCIALACCOUNT_ADAPTER,
            "accounts.adapters.LoggingSocialAccountAdapter",
        )
        self.assertEqual(
            settings.LOGGING["loggers"]["accounts.adapters"]["level"],
            "WARNING",
        )
        self.assertIn(
            "oauth_console",
            settings.LOGGING["loggers"]["accounts.adapters"]["handlers"],
        )

    def test_oauth_site_domain_is_a_host_without_scheme(self):
        from django.conf import settings
        from django.contrib.sites.models import Site
        from urllib.parse import urlsplit

        site = Site.objects.get(pk=settings.SITE_ID)

        self.assertEqual(site.domain, urlsplit(settings.FRONTEND_URL).netloc)

    def test_oauth_diagnostics_log_only_allowlisted_error_codes(self):
        from types import SimpleNamespace

        from allauth.socialaccount.providers.oauth2.client import OAuth2Error

        from accounts.adapters import LoggingSocialAccountAdapter

        adapter = LoggingSocialAccountAdapter()
        exception = OAuth2Error(
            "Error retrieving access token: "
            "b'{\"error\":\"invalid_client\",\"client_secret\":\"never-log-this\"}'"
        )

        with self.assertLogs("accounts.adapters", level="WARNING") as captured:
            adapter.on_authentication_error(
                request=None,
                provider=SimpleNamespace(id="google"),
                exception=exception,
            )

        self.assertIn("oauth_error=invalid_client", captured.output[0])
        self.assertNotIn("never-log-this", captured.output[0])


class GoogleSessionTokenTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="google-user",
            email="google-user@example.com",
            password="A-Strong-Password-123!",
        )
        self.url = "/api/auth/google/session/"

    def test_google_session_exchange_requires_a_django_session(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_google_session_exchange_returns_jwt_for_authenticated_user(self):
        from rest_framework_simplejwt.tokens import AccessToken

        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {"access", "refresh"})
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(self.user.pk))


class RegistrationCsrfTests(APITestCase):
    def test_registration_does_not_require_csrf_from_existing_session(self):
        user = get_user_model().objects.create_user(
            username="existing-user",
            email="existing@example.com",
            password="A-Strong-Password-123!",
        )
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=user.email, password="A-Strong-Password-123!"))

        response = client.post(
            "/api/auth/register/",
            {
                "username": "new-user",
                "email": "new@example.com",
                "password": "Another-Strong-Password-456!",
            },
            content_type="application/json",
            HTTP_ORIGIN="http://127.0.0.1:5173",
            HTTP_HOST="127.0.0.1",
        )

        self.assertEqual(response.status_code, 201, response.content)


class CurrentAccountAvatarTests(APITestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.user = get_user_model().objects.create_user(
            username="avatar-user",
            email="avatar@example.com",
            password="test-password",
        )
        self.url = "/api/auth/me/"

    def test_current_account_has_no_avatar_by_default(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["avatar"])

    def test_avatar_upload_saves_to_user_folder_and_returns_url(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(self.user)
        image = SimpleUploadedFile(
            "profile.png",
            b"\x89PNG\r\n\x1a\n" + b"test-image-data",
            content_type="image/png",
        )

        response = self.client.patch(self.url, {"avatar": image}, format="multipart")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(self.user.avatar.name, rf"^users/{self.user.pk}/avatar/avatar-[0-9a-f]{{32}}\.png$")
        self.assertIn(self.user.avatar.name, response.data["avatar"])

    def test_avatar_replacement_uses_a_new_public_url(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(self.user)
        first = SimpleUploadedFile("first.png", b"\x89PNG\r\n\x1a\nfirst", content_type="image/png")
        first_response = self.client.patch(self.url, {"avatar": first}, format="multipart")
        first_avatar_url = first_response.data["avatar"]
        self.user.refresh_from_db()
        first_avatar_name = self.user.avatar.name
        second = SimpleUploadedFile("second.png", b"\x89PNG\r\n\x1a\nsecond", content_type="image/png")

        second_response = self.client.patch(self.url, {"avatar": second}, format="multipart")
        self.user.refresh_from_db()

        self.assertEqual(second_response.status_code, 200)
        self.assertNotEqual(first_avatar_url, second_response.data["avatar"])
        self.assertNotEqual(first_avatar_name, self.user.avatar.name)
        self.assertIn(self.user.avatar.name, second_response.data["avatar"])
        _, stored_avatars = default_storage.listdir(f"users/{self.user.pk}/avatar")
        self.assertEqual(stored_avatars, [self.user.avatar.name.rsplit("/", 1)[-1]])
        self.assertFalse(default_storage.exists(first_avatar_name))

    def test_deleting_user_removes_avatar_from_storage(self):
        with tempfile.TemporaryDirectory() as storage_directory:
            storage_settings = override_settings(
                STORAGES={
                    "default": {
                        "BACKEND": "django.core.files.storage.FileSystemStorage",
                        "OPTIONS": {"location": storage_directory},
                    },
                    "staticfiles": {
                        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                    },
                },
            )
            with storage_settings:
                avatar_name = f"users/{self.user.pk}/avatar/profile.png"
                saved_name = default_storage.save(avatar_name, ContentFile(b"avatar image"))
                self.user.avatar.name = saved_name
                self.user.save(update_fields=["avatar"])
                self.assertTrue(default_storage.exists(saved_name))

                self.user.delete()

                self.assertFalse(default_storage.exists(saved_name))

    def test_avatar_upload_rejects_non_image_content(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(self.user)
        invalid_file = SimpleUploadedFile(
            "profile.png",
            b"not-an-image",
            content_type="image/png",
        )

        response = self.client.patch(self.url, {"avatar": invalid_file}, format="multipart")

        self.assertEqual(response.status_code, 400)
        self.assertIn("avatar", response.data)

    def test_avatar_upload_rejects_files_over_five_megabytes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_authenticate(self.user)
        oversized_file = SimpleUploadedFile(
            "profile.png",
            b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024),
            content_type="image/png",
        )

        response = self.client.patch(self.url, {"avatar": oversized_file}, format="multipart")

        self.assertEqual(response.status_code, 400)
        self.assertIn("avatar", response.data)

    def test_current_account_requires_authentication(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
