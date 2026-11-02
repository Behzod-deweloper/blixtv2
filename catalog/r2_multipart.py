import math
import re
from pathlib import Path, PurePosixPath

import boto3
from botocore.config import Config
from django.conf import settings
from django.core import signing
from django.utils.text import get_valid_filename

from .models import Video, video_original_upload_to


TOKEN_SALT = "catalog.r2-multipart-upload.v1"
MAX_MULTIPART_PARTS = 10_000
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov"}
VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


class MultipartUploadError(Exception):
    pass


def get_r2_client():
    if not settings.R2_STORAGE_ENABLED:
        raise MultipartUploadError("R2 direct uploads are not enabled.")
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_S3_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def normalize_video_filename(filename):
    filename = PurePosixPath(str(filename).replace("\\", "/")).name
    safe_filename = get_valid_filename(filename)
    extension = Path(safe_filename).suffix.lower()
    if not safe_filename or extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise MultipartUploadError("Only .mp4 and .mov video files are supported.")
    return safe_filename, extension


def get_part_count(file_size):
    return math.ceil(file_size / settings.R2_MULTIPART_PART_SIZE)


def create_upload_key(chapter, filename):
    video = Video(chapter=chapter)
    return video_original_upload_to(video, filename)


def create_upload_token(
    *,
    user_id,
    chapter_id,
    title,
    filename,
    content_type,
    file_size,
    key,
    upload_id,
    output_height=None,
):
    return signing.dumps(
        {
            "user_id": user_id,
            "chapter_id": chapter_id,
            "title": title,
            "filename": filename,
            "content_type": content_type,
            "file_size": file_size,
            "key": key,
            "upload_id": upload_id,
            "output_height": output_height,
        },
        salt=TOKEN_SALT,
        compress=True,
    )


def load_upload_token(token, user_id):
    try:
        payload = signing.loads(
            token,
            salt=TOKEN_SALT,
            max_age=settings.R2_MULTIPART_TOKEN_TTL,
        )
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise MultipartUploadError("The multipart upload token is invalid or expired.") from error

    if (
        not isinstance(payload, dict)
        or payload.get("user_id") != user_id
        or not isinstance(payload.get("chapter_id"), int)
        or not isinstance(payload.get("file_size"), int)
        or not isinstance(payload.get("filename"), str)
        or not isinstance(payload.get("content_type"), str)
        or not isinstance(payload.get("key"), str)
        or not isinstance(payload.get("upload_id"), str)
        or not isinstance(payload.get("title"), str)
        or (
            payload.get("output_height") is not None
            and payload.get("output_height") not in {360, 480, 720}
        )
    ):
        raise MultipartUploadError("The multipart upload token is invalid or belongs to another user.")
    return payload


def is_missing_upload_error(error):
    code = error.response.get("Error", {}).get("Code")
    return code in {"NoSuchUpload", "NoSuchKey", "NotFound", "404"}


def get_presigned_part_url(*, client, key, upload_id, part_number):
    return client.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=settings.R2_MULTIPART_PRESIGNED_URL_TTL,
        HttpMethod="PUT",
    )


def validate_completed_parts(parts, expected_part_count):
    if not isinstance(parts, list) or len(parts) != expected_part_count:
        raise MultipartUploadError("Every uploaded part must be included exactly once.")

    completed_parts = []
    for expected_number, part in enumerate(parts, start=1):
        if not isinstance(part, dict) or part.get("PartNumber") != expected_number:
            raise MultipartUploadError("Uploaded parts must be numbered consecutively from 1.")
        etag = part.get("ETag")
        if not isinstance(etag, str) or not re.fullmatch(r'"[^"\r\n]{1,256}"', etag):
            raise MultipartUploadError("Each uploaded part must include its quoted ETag.")
        completed_parts.append({"PartNumber": expected_number, "ETag": etag})
    return completed_parts


def get_expected_content_type(extension):
    return VIDEO_CONTENT_TYPES[extension]


def get_cdn_url(key):
    from .cdn_urls import get_video_cdn_url

    return get_video_cdn_url(key)
