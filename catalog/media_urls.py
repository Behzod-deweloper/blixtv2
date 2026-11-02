from urllib.parse import quote, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_public_media_url(value, prefix=None):
    value = value.strip()
    if not value:
        return ""

    parsed_url = urlsplit(value)
    if parsed_url.scheme or parsed_url.netloc:
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Use an HTTP(S) URL or an R2 object key.")
        public_url = urlsplit(settings.R2_PUBLIC_URL)
        if (
            public_url.netloc
            and parsed_url.scheme == public_url.scheme
            and parsed_url.netloc.lower() == public_url.netloc.lower()
        ):
            cdn_url = urlsplit(settings.R2_CDN_BASE_URL)
            return urlunsplit(
                (cdn_url.scheme, cdn_url.netloc, parsed_url.path, parsed_url.query, parsed_url.fragment)
            )
        return value

    object_key = value.lstrip("/")
    if "\\" in object_key or any(part in {".", ".."} for part in object_key.split("/")):
        raise ValueError("R2 object keys must not contain path traversal segments.")
    if prefix and "/" not in object_key:
        object_key = f"{prefix}/{object_key}"
    if not settings.R2_PUBLIC_MEDIA_URL:
        raise ImproperlyConfigured(
            "Set R2_PUBLIC_URL and R2_BUCKET_NAME to serve R2 object keys publicly."
        )
    return f"{settings.R2_PUBLIC_MEDIA_URL}{quote(object_key, safe='/')}"
