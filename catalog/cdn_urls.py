from urllib.parse import quote, urlsplit

from django.conf import settings


def get_video_cdn_url(value):
    value = str(value).strip()
    parsed_url = urlsplit(value)
    object_key = parsed_url.path.lstrip("/") if parsed_url.scheme or parsed_url.netloc else value
    if (
        not object_key
        or "\\" in object_key
        or any(part in {"", ".", ".."} for part in object_key.split("/"))
    ):
        raise ValueError("Video CDN URLs require a valid R2 object key.")
    return f"{settings.R2_CDN_BASE_URL}/{quote(object_key, safe='/')}"
