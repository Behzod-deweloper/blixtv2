import logging
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, transaction
from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Chapter, Video
from .r2_multipart import (
    MAX_MULTIPART_PARTS,
    MultipartUploadError,
    create_upload_key,
    create_upload_token,
    get_cdn_url,
    get_expected_content_type,
    get_part_count,
    get_presigned_part_url,
    get_r2_client,
    is_missing_upload_error,
    load_upload_token,
    normalize_video_filename,
    validate_completed_parts,
)
from .tasks import RENDITIONS


logger = logging.getLogger(__name__)


class InitiateMultipartUploadSerializer(serializers.Serializer):
    chapter_id = serializers.IntegerField(min_value=1)
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=100, required=False, allow_blank=True)
    file_size = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=240, required=False, allow_blank=True)
    single_format_only = serializers.BooleanField(required=False, default=False)
    output_format = serializers.ChoiceField(
        choices=[(str(height), str(height)) for height, _bitrate in RENDITIONS],
        required=False,
        allow_blank=True,
    )


class MultipartTokenSerializer(serializers.Serializer):
    upload_token = serializers.CharField()


class PresignedPartSerializer(MultipartTokenSerializer):
    part_number = serializers.IntegerField(min_value=1, max_value=MAX_MULTIPART_PARTS)


class CompleteMultipartUploadSerializer(MultipartTokenSerializer):
    parts = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        max_length=MAX_MULTIPART_PARTS,
    )


class MultipartResumeView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = MultipartTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = load_upload_token(
                serializer.validated_data["upload_token"],
                request.user.pk,
            )
            client = get_r2_client()
            parts = []
            result = client.list_parts(
                Bucket=settings.R2_BUCKET_NAME,
                Key=payload["key"],
                UploadId=payload["upload_id"],
                MaxParts=1000,
            )
            parts.extend(result.get("Parts", []))
            while result.get("IsTruncated"):
                result = client.list_parts(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=payload["key"],
                    UploadId=payload["upload_id"],
                    PartNumberMarker=result["NextPartNumberMarker"],
                    MaxParts=1000,
                )
                parts.extend(result.get("Parts", []))
        except MultipartUploadError as error:
            return Response({"upload_token": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except ClientError as error:
            if not is_missing_upload_error(error):
                logger.exception("Could not list R2 multipart parts.")
                return Response(
                    {"detail": "Could not inspect uploaded R2 parts."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            try:
                head = client.head_object(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=payload["key"],
                )
            except ClientError as head_error:
                if is_missing_upload_error(head_error):
                    return Response(
                        {"detail": "This upload has expired or no longer exists."},
                        status=status.HTTP_410_GONE,
                    )
                logger.exception("Could not inspect completed R2 upload.")
                return Response(
                    {"detail": "Could not inspect the R2 upload."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            except (BotoCoreError, MultipartUploadError):
                logger.exception("Could not inspect completed R2 upload.")
                return Response(
                    {"detail": "Could not inspect the R2 upload."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            if head.get("ContentLength") != payload["file_size"]:
                return Response(
                    {"detail": "The stored video size does not match this upload."},
                    status=status.HTTP_409_CONFLICT,
                )
            video = Video.objects.filter(original_file=payload["key"]).first()
            return Response(
                {
                    "completed": True,
                    "video_id": video.pk if video else None,
                    "file_size": payload["file_size"],
                }
            )
        except (BotoCoreError, MultipartUploadError):
            logger.exception("Could not list R2 multipart parts.")
            return Response(
                {"detail": "Could not inspect uploaded R2 parts."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "completed": False,
                "file_size": payload["file_size"],
                "part_size": settings.R2_MULTIPART_PART_SIZE,
                "part_count": get_part_count(payload["file_size"]),
                "parts": [
                    {
                        "PartNumber": part["PartNumber"],
                        "ETag": part["ETag"],
                        "Size": part["Size"],
                    }
                    for part in parts
                ],
            }
        )


class MultipartUploadView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        if not settings.R2_STORAGE_ENABLED:
            return Response(
                {"detail": "R2 storage must be enabled before direct uploads can be used."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = InitiateMultipartUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["single_format_only"] and not data.get("output_format"):
            return Response(
                {"output_format": "Choose a format when single-format upload is selected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not data["single_format_only"] and data.get("output_format"):
            return Response(
                {"output_format": "Enable single-format upload before choosing a format."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if data["file_size"] > settings.VIDEO_MAX_UPLOAD_SIZE:
            return Response(
                {"file_size": "The video exceeds VIDEO_MAX_UPLOAD_SIZE."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        chapter = Chapter.objects.filter(pk=data["chapter_id"]).select_related("comic").first()
        if chapter is None:
            return Response(
                {"chapter_id": "The selected chapter does not exist."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            filename, extension = normalize_video_filename(data["filename"])
        except MultipartUploadError as error:
            return Response({"filename": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        part_count = get_part_count(data["file_size"])
        if part_count > MAX_MULTIPART_PARTS:
            return Response(
                {"file_size": "The file requires too many multipart chunks."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = get_expected_content_type(extension)
        requested_content_type = data.get("content_type", "")
        if requested_content_type and requested_content_type not in {
            content_type,
            "application/octet-stream",
        }:
            return Response(
                {"content_type": "The content type does not match the video file extension."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = data.get("title", "").strip() or Path(filename).stem
        key = create_upload_key(chapter, filename)

        try:
            client = get_r2_client()
            upload = client.create_multipart_upload(
                Bucket=settings.R2_BUCKET_NAME,
                Key=key,
                ContentType=content_type,
                CacheControl="public, max-age=86400",
            )
        except (ClientError, BotoCoreError, MultipartUploadError):
            logger.exception("Could not start R2 multipart upload for chapter %s.", chapter.pk)
            return Response(
                {"detail": "Could not start the direct R2 upload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        upload_id = upload.get("UploadId") if isinstance(upload, dict) else None
        if not isinstance(upload_id, str) or not upload_id:
            logger.error("R2 returned no multipart upload ID for chapter %s.", chapter.pk)
            return Response(
                {"detail": "R2 did not return a multipart upload ID."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        upload_token = create_upload_token(
            user_id=request.user.pk,
            chapter_id=chapter.pk,
            title=title[:240],
            filename=filename,
            content_type=content_type,
            file_size=data["file_size"],
            key=key,
            upload_id=upload_id,
            output_height=(
                int(data["output_format"])
                if data["single_format_only"]
                else None
            ),
        )
        return Response(
            {
                "upload_token": upload_token,
                "part_size": settings.R2_MULTIPART_PART_SIZE,
                "part_count": part_count,
            },
            status=status.HTTP_201_CREATED,
        )


class MultipartPartUrlView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = PresignedPartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = load_upload_token(
                serializer.validated_data["upload_token"],
                request.user.pk,
            )
        except MultipartUploadError as error:
            return Response({"upload_token": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        part_number = serializer.validated_data["part_number"]
        if part_number > get_part_count(payload["file_size"]):
            return Response(
                {"part_number": "The part number is outside this upload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            url = get_presigned_part_url(
                client=get_r2_client(),
                key=payload["key"],
                upload_id=payload["upload_id"],
                part_number=part_number,
            )
        except (ClientError, BotoCoreError, MultipartUploadError):
            logger.exception("Could not sign R2 multipart part %s.", part_number)
            return Response(
                {"detail": "Could not create a temporary upload URL."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {
                "url": url,
                "part_number": part_number,
                "expires_in": settings.R2_MULTIPART_PRESIGNED_URL_TTL,
            }
        )


class CompleteMultipartUploadView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = CompleteMultipartUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = load_upload_token(
                serializer.validated_data["upload_token"],
                request.user.pk,
            )
        except MultipartUploadError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            client = get_r2_client()
            video = Video.objects.filter(original_file=payload["key"]).first()
            if video:
                return Response(
                    {
                        "id": video.pk,
                        "status": video.status,
                        "cdn_url": get_cdn_url(payload["key"]),
                    },
                    status=status.HTTP_200_OK,
                )

            try:
                head = client.head_object(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=payload["key"],
                )
            except ClientError as error:
                if not is_missing_upload_error(error):
                    raise
                head = None

            if head is None:
                parts = validate_completed_parts(
                    serializer.validated_data["parts"],
                    get_part_count(payload["file_size"]),
                )
                client.complete_multipart_upload(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=payload["key"],
                    UploadId=payload["upload_id"],
                    MultipartUpload={"Parts": parts},
                )
                head = client.head_object(
                    Bucket=settings.R2_BUCKET_NAME,
                    Key=payload["key"],
                )
            if head.get("ContentLength") != payload["file_size"]:
                client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=payload["key"])
                return Response(
                    {"detail": "The completed R2 object size did not match the uploaded file."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except MultipartUploadError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except (ClientError, BotoCoreError):
            logger.exception("Could not complete R2 multipart upload for key %s.", payload["key"])
            return Response(
                {"detail": "Could not complete the R2 upload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            with transaction.atomic():
                chapter = Chapter.objects.get(pk=payload["chapter_id"])
                video = Video.objects.create(
                    chapter=chapter,
                    title=payload["title"],
                    original_file=payload["key"],
                    status=Video.Status.UPLOADING,
                )
        except (Chapter.DoesNotExist, DatabaseError, DjangoValidationError):
            logger.exception("Could not save completed R2 upload for key %s.", payload["key"])
            try:
                client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=payload["key"])
            except (ClientError, BotoCoreError):
                logger.exception("Could not clean up unrecorded R2 object %s.", payload["key"])
            return Response(
                {"detail": "The file uploaded, but its video record could not be saved."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        cdn_url = get_cdn_url(payload["key"])
        from .tasks import process_video_hls

        try:
            if payload.get("output_height") is None:
                process_video_hls.delay(video.pk)
            else:
                process_video_hls.delay(video.pk, payload["output_height"])
        except Exception:
            logger.exception("Could not queue HLS processing for direct upload %s.", video.pk)
            video.status = Video.Status.ERROR
            video.save(update_fields=["status"])
            return Response(
                {
                    "id": video.pk,
                    "status": video.status,
                    "cdn_url": cdn_url,
                    "detail": "The original is uploaded, but HLS processing could not be queued.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "id": video.pk,
                "status": video.status,
                "cdn_url": cdn_url,
            },
            status=status.HTTP_201_CREATED,
        )


class AbortMultipartUploadView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = MultipartTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = load_upload_token(
                serializer.validated_data["upload_token"],
                request.user.pk,
            )
        except MultipartUploadError as error:
            return Response({"upload_token": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        try:
            get_r2_client().abort_multipart_upload(
                Bucket=settings.R2_BUCKET_NAME,
                Key=payload["key"],
                UploadId=payload["upload_id"],
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "NoSuchUpload":
                logger.exception("Could not abort R2 multipart upload.")
                return Response(
                    {"detail": "Could not cancel the R2 upload."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        except (BotoCoreError, MultipartUploadError):
            logger.exception("Could not abort R2 multipart upload.")
            return Response(
                {"detail": "Could not cancel the R2 upload."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"aborted": True})
