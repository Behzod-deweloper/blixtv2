from unittest.mock import patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .cdn_urls import get_video_cdn_url
from .models import Chapter, Comic, Video
from .serializers import VideoSerializer


@override_settings(
    R2_STORAGE_ENABLED=True,
    R2_BUCKET_NAME="test-bucket",
    R2_CDN_BASE_URL="https://cdn.blixtv.uz",
    R2_MULTIPART_PART_SIZE=8 * 1024 * 1024,
    R2_MULTIPART_PRESIGNED_URL_TTL=900,
    R2_MULTIPART_TOKEN_TTL=24 * 60 * 60,
    VIDEO_MAX_UPLOAD_SIZE=1024 * 1024,
)
class R2MultipartUploadTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(
            username="multipart-admin",
            email="multipart-admin@example.com",
            password="secure-test-password",
        )
        comic = Comic.objects.create(title="Multipart Drama", slug="multipart-drama")
        self.chapter = Chapter.objects.create(comic=comic, number=7)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.start_url = reverse("video-multipart-initiate")

    def upload_data(self, **overrides):
        data = {
            "chapter_id": self.chapter.pk,
            "filename": "episode.mp4",
            "content_type": "video/mp4",
            "file_size": 1024,
            "title": "Episode 7",
        }
        data.update(overrides)
        return data

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_initiate_returns_signed_upload_token_without_video_bytes(self, get_client):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}

        response = self.client.post(
            self.start_url,
            self.upload_data(),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["part_count"], 1)
        self.assertEqual(response.data["part_size"], 8 * 1024 * 1024)
        self.assertNotIn("upload_id", response.data)
        client.create_multipart_upload.assert_called_once()
        call = client.create_multipart_upload.call_args.kwargs
        self.assertEqual(call["Bucket"], "test-bucket")
        self.assertRegex(
            call["Key"],
            r"^multipart-drama/video/chapter-7/1080p/episode-[0-9a-f]{12}\.mp4$",
        )
        self.assertEqual(call["ContentType"], "video/mp4")

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_initiate_token_preserves_selected_single_output_format(self, get_client):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}

        response = self.client.post(
            self.start_url,
            self.upload_data(single_format_only=True, output_format="480"),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        from .r2_multipart import load_upload_token

        payload = load_upload_token(response.data["upload_token"], self.admin.pk)
        self.assertEqual(payload["output_height"], 480)

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_part_url_is_scoped_to_user_upload_and_number(self, get_client):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.generate_presigned_url.return_value = (
            "https://r2.example.test/upload?signature=temporary"
        )
        start = self.client.post(
            self.start_url,
            self.upload_data(),
            format="json",
        )

        response = self.client.post(
            reverse("video-multipart-part-url"),
            {"upload_token": start.data["upload_token"], "part_number": 1},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["url"], client.generate_presigned_url.return_value)
        self.assertEqual(client.generate_presigned_url.call_args.args[0], "upload_part")
        params = client.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(params["Bucket"], "test-bucket")
        self.assertEqual(params["UploadId"], "multipart-id")
        self.assertEqual(params["PartNumber"], 1)

    @patch("catalog.multipart_uploads.get_r2_client")
    @override_settings(R2_MULTIPART_PART_SIZE=512)
    def test_resume_returns_parts_already_saved_in_r2(self, get_client):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.list_parts.return_value = {
            "IsTruncated": False,
            "Parts": [
                {"PartNumber": 1, "ETag": '"part-one"', "Size": 512},
            ],
        }
        start = self.client.post(self.start_url, self.upload_data(), format="json")

        response = self.client.post(
            reverse("video-multipart-resume"),
            {"upload_token": start.data["upload_token"]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["completed"])
        self.assertEqual(response.data["part_count"], 2)
        self.assertEqual(
            response.data["parts"],
            [{"PartNumber": 1, "ETag": '"part-one"', "Size": 512}],
        )
        client.list_parts.assert_called_once_with(
            Bucket="test-bucket",
            Key=client.create_multipart_upload.call_args.kwargs["Key"],
            UploadId="multipart-id",
            MaxParts=1000,
        )

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_resume_reports_when_r2_upload_has_expired(self, get_client):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.list_parts.side_effect = ClientError(
            {"Error": {"Code": "NoSuchUpload", "Message": "Upload is missing"}},
            "ListParts",
        )
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Object is missing"}},
            "HeadObject",
        )
        start = self.client.post(self.start_url, self.upload_data(), format="json")

        response = self.client.post(
            reverse("video-multipart-resume"),
            {"upload_token": start.data["upload_token"]},
            format="json",
        )

        self.assertEqual(response.status_code, 410)

    @patch("catalog.tasks.process_video_hls.delay")
    @patch("catalog.multipart_uploads.get_r2_client")
    def test_complete_saves_video_and_returns_cdn_url(self, get_client, enqueue_task):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.head_object.side_effect = [
            ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Object is missing"}},
                "HeadObject",
            ),
            {"ContentLength": 1024},
        ]
        start = self.client.post(
            self.start_url,
            self.upload_data(),
            format="json",
        )
        key = client.create_multipart_upload.call_args.kwargs["Key"]

        response = self.client.post(
            reverse("video-multipart-complete"),
            {
                "upload_token": start.data["upload_token"],
                "parts": [{"PartNumber": 1, "ETag": '"part-etag"'}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        video = Video.objects.get(pk=response.data["id"])
        self.assertEqual(video.chapter, self.chapter)
        self.assertEqual(video.title, "Episode 7")
        self.assertEqual(video.original_file.name, key)
        self.assertEqual(
            response.data["cdn_url"],
            f"https://cdn.blixtv.uz/{key}",
        )
        self.assertEqual(response.data["status"], Video.Status.UPLOADING)
        client.complete_multipart_upload.assert_called_once_with(
            Bucket="test-bucket",
            Key=key,
            UploadId="multipart-id",
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": '"part-etag"'}]},
        )
        enqueue_task.assert_called_once_with(video.pk)

    @patch("catalog.tasks.process_video_hls.delay")
    @patch("catalog.multipart_uploads.get_r2_client")
    def test_complete_recovers_if_r2_finished_before_page_closed(
        self,
        get_client,
        enqueue_task,
    ):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.head_object.return_value = {"ContentLength": 1024}
        start = self.client.post(self.start_url, self.upload_data(), format="json")

        response = self.client.post(
            reverse("video-multipart-complete"),
            {"upload_token": start.data["upload_token"], "parts": []},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(Video.objects.filter(pk=response.data["id"]).exists())
        client.complete_multipart_upload.assert_not_called()
        enqueue_task.assert_called_once_with(response.data["id"])

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_start_rejects_non_video_extensions(self, get_client):
        response = self.client.post(
            self.start_url,
            self.upload_data(filename="payload.exe"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        get_client.assert_not_called()

    @patch("catalog.multipart_uploads.get_r2_client")
    def test_start_rejects_video_above_configured_size(self, get_client):
        response = self.client.post(
            self.start_url,
            self.upload_data(
                file_size=1024 * 1024 + 1,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        get_client.assert_not_called()

    def test_video_cdn_url_always_uses_configured_cdn_origin(self):
        self.assertEqual(
            get_video_cdn_url("comic/video/chapter-1/episode one.m3u8"),
            "https://cdn.blixtv.uz/comic/video/chapter-1/episode%20one.m3u8",
        )
        self.assertEqual(
            get_video_cdn_url("https://old-public-bucket.example/video/episode.mp4"),
            "https://cdn.blixtv.uz/video/episode.mp4",
        )
        with self.assertRaises(ValueError):
            get_video_cdn_url("../private/episode.mp4")

    def test_video_serializer_uses_cdn_for_original_and_hls_urls(self):
        video = Video(
            chapter=self.chapter,
            title="Episode 7",
            original_file="comic/video/chapter-7/episode.mp4",
            hls_playlist="comic/video/chapter-7/master.m3u8",
            status=Video.Status.READY,
        )

        data = VideoSerializer(video).data

        self.assertEqual(
            data["original_video"],
            "https://cdn.blixtv.uz/comic/video/chapter-7/episode.mp4",
        )
        self.assertEqual(
            data["hls_playlist"],
            "https://cdn.blixtv.uz/comic/video/chapter-7/master.m3u8",
        )

    def test_multipart_upload_requires_admin_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.start_url,
            self.upload_data(),
            format="json",
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(R2_STORAGE_ENABLED=True)
    def test_video_admin_add_page_loads_direct_upload_controls(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin:catalog_video_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="videoInput"')
        self.assertContains(response, "catalog/admin/video_form_multipart.js")
        self.assertContains(response, 'data-r2-multipart-enabled="true"')
        self.assertContains(response, 'data-r2-module-url="/static/catalog/admin/r2-multipart-upload.js"')

    @patch("catalog.tasks.process_video_hls.delay")
    @patch("catalog.multipart_uploads.get_r2_client")
    def test_completion_queues_the_selected_single_output_format(
        self,
        get_client,
        enqueue_task,
    ):
        client = get_client.return_value
        client.create_multipart_upload.return_value = {"UploadId": "multipart-id"}
        client.head_object.return_value = {"ContentLength": 1024}
        start = self.client.post(
            self.start_url,
            self.upload_data(single_format_only=True, output_format="360"),
            format="json",
        )

        response = self.client.post(
            reverse("video-multipart-complete"),
            {
                "upload_token": start.data["upload_token"],
                "parts": [{"PartNumber": 1, "ETag": '"part-etag"'}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        enqueue_task.assert_called_once_with(response.data["id"], 360)
