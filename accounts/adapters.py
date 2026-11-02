import logging
import re

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


logger = logging.getLogger(__name__)
_OAUTH_ERROR_PATTERN = re.compile(r"""["']error["']\s*:\s*["']([a-zA-Z0-9_-]+)["']""")
_KNOWN_OAUTH_ERRORS = {
    "access_denied",
    "invalid_client",
    "invalid_grant",
    "invalid_request",
    "invalid_scope",
    "redirect_uri_mismatch",
    "server_error",
    "temporarily_unavailable",
    "unauthorized_client",
}


class LoggingSocialAccountAdapter(DefaultSocialAccountAdapter):
    def on_authentication_error(
        self,
        request,
        provider,
        error=None,
        exception=None,
        extra_context=None,
    ):
        provider_id = getattr(provider, "id", "unknown")
        if exception is None:
            logger.warning(
                "Social login failed: provider=%s error=%s",
                provider_id,
                error or "unknown",
            )
        else:
            match = _OAUTH_ERROR_PATTERN.search(str(exception))
            oauth_error = match.group(1) if match else "unknown"
            if oauth_error not in _KNOWN_OAUTH_ERRORS:
                oauth_error = "unknown"
            logger.warning(
                "Social login failed: provider=%s exception=%s oauth_error=%s",
                provider_id,
                type(exception).__name__,
                oauth_error,
            )
        return super().on_authentication_error(
            request,
            provider,
            error=error,
            exception=exception,
            extra_context=extra_context,
        )
