from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from .models import Bookmark, Chapter, Comic, ComicRating, ComicView, Genre, Video
from .tasks import (
    _delete_local_media_copy_of_r2_source,
    _delete_storage_tree,
    _store_hls_output,
    _transcode_rendition,
    _write_master_playlist,
    process_video_hls,
)


class ComicMediaDeletionTests(TestCase):
    def save_media_file(self, name):
        return default_storage.save(name, ContentFile(b"media"))

    def test_deleting_chapter_removes_its_video_tree_only(self):
        with TemporaryDirectory() as storage_directory, override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": storage_directory},
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            },
        ):
            comic = Comic.objects.create(title="Cleanup Comic", slug="cleanup-comic")
            deleted_chapter = Chapter.objects.create(comic=comic, number=1)
            kept_chapter = Chapter.objects.create(comic=comic, number=2)
            deleted_files = [
                self.save_media_file("cleanup-comic/video/chapter-1/1080p/episode.mp4"),
                self.save_media_file("cleanup-comic/video/chapter-1/1080p/episode.m3u8"),
                self.save_media_file("cleanup-comic/video/chapter-1/360p/episode/index.m3u8"),
                self.save_media_file("cleanup-comic/video/chapter-1/360p/episode/segment.ts"),
            ]
            kept_file = self.save_media_file(
                "cleanup-comic/video/chapter-2/1080p/another-episode.mp4"
            )
            logo_file = self.save_media_file("cleanup-comic/logo/cover.webp")

            deleted_chapter.delete()

            self.assertTrue(all(not default_storage.exists(name) for name in deleted_files))
            self.assertTrue(default_storage.exists(kept_file))
            self.assertTrue(default_storage.exists(logo_file))
            self.assertTrue(Chapter.objects.filter(pk=kept_chapter.pk).exists())

    def test_deleting_comic_removes_all_its_videos_and_logo_only(self):
        with TemporaryDirectory() as storage_directory, override_settings(
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": storage_directory},
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            },
        ):
            comic = Comic.objects.create(title="Cleanup Comic", slug="cleanup-comic")
            Chapter.objects.create(comic=comic, number=1)
            Chapter.objects.create(comic=comic, number=2)
            comic_files = [
                self.save_media_file("cleanup-comic/video/chapter-1/1080p/episode.mp4"),
                self.save_media_file("cleanup-comic/video/chapter-1/360p/episode/index.m3u8"),
                self.save_media_file("cleanup-comic/video/chapter-2/1080p/another-episode.mp4"),
                self.save_media_file("cleanup-comic/logo/cover.webp"),
            ]
            other_comic_file = self.save_media_file("keep-comic/video/chapter-1/episode.mp4")

            comic.delete()

            self.assertTrue(all(not default_storage.exists(name) for name in comic_files))
            self.assertTrue(default_storage.exists(other_comic_file))


class VideoAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="video-admin",
            email="video-admin@example.com",
            password="Admin-Password-2026!",
        )
        comic = Comic.objects.create(title="Admin Comic", slug="admin-comic")
        self.chapter = Chapter.objects.create(comic=comic, number=1)
        self.client.force_login(self.admin_user)

    def test_video_form_defaults_to_latest_chapter_drama_and_has_recent_filter(self):
        latest_drama = Comic.objects.create(title="Latest Drama", slug="latest-drama")
        first_chapter = Chapter.objects.create(comic=latest_drama, number=1)
        latest_chapter = Chapter.objects.create(comic=latest_drama, number=2)

        response = self.client.get(reverse("admin:catalog_video_add"))

        self.assertEqual(response.status_code, 200)
        form = response.context["adminform"].form
        self.assertEqual(form["comic"].value(), latest_drama.pk)
        self.assertEqual(form["chapter"].value(), latest_chapter.pk)
        self.assertEqual(form.fields["recent_comics_only"].label, "Faqat yangi qo‘shilgan dramalar")
        field_order = list(form.fields)
        self.assertEqual(field_order[:4], ["comic", "recent_comics_only", "chapter", "title"])
        self.assertEqual(
            field_order[field_order.index("original_file") : field_order.index("original_file") + 3],
            ["original_file", "single_format_only", "output_format"],
        )
        self.assertEqual(
            form.fields["output_format"].choices,
            [("360", "360p"), ("480", "480p"), ("720", "720p")],
        )
        rendered_field_order = response.context["adminform"].fieldsets[0][1]["fields"]
        self.assertEqual(
            rendered_field_order[:4],
            ("comic", "recent_comics_only", "chapter", "title"),
        )
        self.assertEqual(
            rendered_field_order[
                rendered_field_order.index("original_file") :
                rendered_field_order.index("original_file") + 3
            ],
            ("original_file", "single_format_only", "output_format"),
        )
        self.assertEqual(
            list(form.fields).index("recent_comics_only"),
            list(form.fields).index("comic") + 1,
        )
        self.assertContains(response, 'data-recent-comic="true"')
        latest_chapter_option = f'value="{latest_chapter.pk}"'
        self.assertContains(response, latest_chapter_option)
        self.assertContains(response, 'data-latest-chapter="true"')
        self.assertContains(response, ">2-qism</option>")
        self.assertContains(response, 'name="single_format_only"')
        self.assertContains(response, 'name="output_format"')
        self.assertNotContains(response, "Latest Drama - 2.00-qism")
        self.assertNotContains(response, f'value="{first_chapter.pk}" data-latest-chapter="true"')
        self.assertContains(response, "catalog/admin/video_form_options.js")
        self.assertContains(response, "catalog/admin/chapter_form.css")

    def test_video_form_marks_only_five_latest_dramas_for_recent_filter(self):
        created_comics = [
            Comic.objects.create(title=f"Video Drama {index}", slug=f"video-drama-{index}")
            for index in range(6)
        ]

        response = self.client.get(reverse("admin:catalog_video_add"))

        self.assertEqual(response.status_code, 200)
        latest_five = set(
            Comic.objects.order_by("-created_at", "-pk")
            .values_list("pk", flat=True)[:5]
        )
        response_html = response.content.decode()
        for comic in created_comics:
            option_start = response_html.index(f'value="{comic.pk}"')
            option_end = response_html.index("</option>", option_start)
            option_html = response_html[option_start:option_end]
            self.assertEqual(
                'data-recent-comic="true"' in option_html,
                comic.pk in latest_five,
            )

    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_can_upload_video_for_chapter_and_queues_processing(self, enqueue_task):
        add_page = self.client.get(reverse("admin:catalog_video_add"))
        chapter_field = add_page.context["adminform"].form.fields["chapter"]
        self.assertTrue(add_page.context["adminform"].form.is_multipart())
        self.assertContains(add_page, 'enctype="multipart/form-data"')
        self.assertEqual(chapter_field.queryset.count(), 1)
        self.assertEqual(len(chapter_field.widget.chapter_comic_ids), 1)
        self.assertContains(add_page, 'type="file"')
        self.assertContains(add_page, "Kompyuterdan video yuklash")
        self.assertContains(add_page, 'accept=".mp4,.mov,video/mp4,video/quicktime"')
        self.assertContains(add_page, 'name="comic"')
        self.assertContains(add_page, 'name="chapter"')
        self.assertContains(add_page, f'data-comic-id="{self.chapter.comic_id}"')
        self.assertContains(add_page, "catalog/admin/video_form_options.js")

        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "Admin upload",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(title="Admin upload")
        self.assertEqual(video.chapter, self.chapter)
        self.assertEqual(video.status, Video.Status.UPLOADING)
        self.assertRegex(
            video.original_file.name,
            r"^admin-comic/video/chapter-1/1080p/episode-[0-9a-f]{12}\.mp4$",
        )
        enqueue_task.assert_called_once_with(video.pk)

    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_can_choose_a_single_hls_format(self, enqueue_task):
        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "Single format upload",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
                "single_format_only": "on",
                "output_format": "480",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(title="Single format upload")
        enqueue_task.assert_called_once_with(video.pk, 480)

    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_requires_a_format_when_single_format_is_selected(self, enqueue_task):
        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "Missing format",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
                "single_format_only": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "O‘girish uchun video formatini tanlang.")
        self.assertFalse(Video.objects.filter(title="Missing format").exists())
        enqueue_task.assert_not_called()

    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_can_upload_video_without_title(self, enqueue_task):
        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(chapter=self.chapter)
        self.assertEqual(video.title, "")
        enqueue_task.assert_called_once_with(video.pk)

    @override_settings(R2_STORAGE_ENABLED=True)
    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_can_process_video_already_uploaded_to_r2(self, enqueue_task):
        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "Existing R2 video",
                "original_object_key": "admin-comic/video/chapter-1/1080p/original.mp4",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(title="Existing R2 video")
        self.assertEqual(
            video.original_file.name,
            "admin-comic/video/chapter-1/1080p/original.mp4",
        )
        self.assertEqual(video.status, Video.Status.UPLOADING)
        enqueue_task.assert_called_once_with(video.pk)

    @override_settings(R2_STORAGE_ENABLED=True)
    @patch("catalog.tasks.process_video_hls.delay")
    def test_admin_rejects_existing_r2_key_outside_selected_manhwa_chapter(self, enqueue_task):
        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": self.chapter.pk,
                "title": "Wrong folder",
                "original_object_key": "other-comic/video/chapter-1/1080p/episode.mp4",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "R2 obyekt kalitini admin-comic/video/chapter-1/1080p/ ostidan kiriting.")
        self.assertFalse(Video.objects.filter(title="Wrong folder").exists())
        enqueue_task.assert_not_called()

    def test_admin_rejects_chapter_from_another_comic(self):
        other_comic = Comic.objects.create(title="Other Comic", slug="other-comic")
        other_chapter = Chapter.objects.create(comic=other_comic, number=1)

        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.chapter.comic_id,
                "chapter": other_chapter.pk,
                "title": "Mismatched video",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tanlangan dramaga tegishli qismni tanlang.")
        self.assertFalse(Video.objects.filter(title="Mismatched video").exists())

    def test_admin_rejects_public_url_instead_of_an_r2_object_key(self):
        for value in (
            "https://pub.example.r2.dev/video/episode.mp4",
            "https:/pub.example.r2.dev/video/episode.mp4",
        ):
            with self.subTest(value=value):
                response = self.client.post(
                    reverse("admin:catalog_video_add"),
                    {
                        "comic": self.chapter.comic_id,
                        "chapter": self.chapter.pk,
                        "title": "Bad R2 reference",
                        "original_object_key": value,
                        "_save": "Save",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "R2 obyekt kalitini kiriting.")
                self.assertFalse(Video.objects.filter(title="Bad R2 reference").exists())


class ChapterAdminNumberTests(TestCase):
    def setUp(self):
        admin_user = get_user_model().objects.create_superuser(
            username="chapter-admin",
            email="chapter-admin@example.com",
            password="test-password",
        )
        self.drama = Comic.objects.create(title="Admin Drama", slug="admin-drama")
        self.empty_drama = Comic.objects.create(title="Empty Drama", slug="empty-drama")
        Chapter.objects.create(comic=self.drama, number=50)
        self.client.force_login(admin_user)

    def test_new_chapter_form_suggests_next_integer_for_selected_drama(self):
        response = self.client.get(reverse("admin:catalog_chapter_add"))

        self.assertEqual(response.status_code, 200)
        recent_field = response.context["adminform"].form.fields["recent_comics_only"]
        self.assertEqual(recent_field.label, "Faqat yangi qo‘shilgan dramalar")
        chapter_form = response.context["adminform"].form
        self.assertEqual(chapter_form["comic"].value(), self.drama.pk)
        field_order = list(chapter_form.fields)
        self.assertEqual(field_order.index("recent_comics_only"), field_order.index("comic") + 1)
        number_field = response.context["adminform"].form.fields["number"]
        self.assertEqual(number_field.widget.attrs["step"], 1)
        self.assertEqual(number_field.widget.attrs["min"], 1)
        published_at_field = response.context["adminform"].form.fields["published_at"]
        self.assertEqual(published_at_field.label, "Rejalashtirilgan nashr vaqti")
        self.assertIn("Videoni oldindan biriktirish mumkin", published_at_field.help_text)
        self.assertContains(response, 'data-next-chapter-number="51"')
        self.assertContains(response, 'data-next-chapter-number="1"')
        self.assertContains(response, 'data-recent-comic="true"')
        self.assertContains(response, "catalog/admin/chapter_form.js")
        self.assertContains(response, "catalog/admin/chapter_form.css")

    def test_recent_comics_filter_marks_only_the_latest_five_dramas(self):
        created_comics = [
            Comic.objects.create(title=f"Recent Drama {index}", slug=f"recent-drama-{index}")
            for index in range(6)
        ]

        response = self.client.get(reverse("admin:catalog_chapter_add"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.count(b'data-recent-comic="true"'), 5)
        latest_five = set(
            Comic.objects.order_by("-created_at", "-pk")
            .values_list("pk", flat=True)[:5]
        )
        response_html = response.content.decode()
        for comic in created_comics:
            option_start = response_html.index(f'value="{comic.pk}"')
            option_end = response_html.index("</option>", option_start)
            option_html = response_html[option_start:option_end]
            self.assertEqual(
                'data-recent-comic="true"' in option_html,
                comic.pk in latest_five,
            )

    def test_comic_inline_prefills_next_chapter_number(self):
        response = self.client.get(
            reverse("admin:catalog_comic_change", args=[self.drama.pk])
        )

        self.assertEqual(response.status_code, 200)
        chapter_formset = response.context["inline_admin_formsets"][0].formset
        self.assertEqual(chapter_formset.empty_form["number"].value(), 51)

    def test_new_chapter_accepts_suggested_next_integer(self):
        response = self.client.post(
            reverse("admin:catalog_chapter_add"),
            {
                "comic": self.drama.pk,
                "number": 51,
                "title": "",
                "published_at": "",
                "pages-TOTAL_FORMS": "0",
                "pages-INITIAL_FORMS": "0",
                "pages-MIN_NUM_FORMS": "0",
                "pages-MAX_NUM_FORMS": "1000",
                "_save": "Saqlash",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Chapter.objects.filter(comic=self.drama, number=51).exists()
        )


class ChapterSchedulingTests(TestCase):
    def setUp(self):
        admin_user = get_user_model().objects.create_superuser(
            username="chapter-schedule-admin",
            email="chapter-schedule-admin@example.com",
            password="test-password",
        )
        self.drama = Comic.objects.create(title="Scheduled Drama", slug="scheduled-drama")
        self.chapter = Chapter.objects.create(
            comic=self.drama,
            number=1,
            published_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_login(admin_user)

    def test_scheduled_datetime_uses_uzbekistan_timezone(self):
        response = self.client.get(reverse("admin:catalog_chapter_add"))
        form_class = response.context["adminform"].form.__class__
        form = form_class(
            data={
                "comic": str(self.drama.pk),
                "number": "2",
                "title": "",
                "published_at_0": "2030-05-01",
                "published_at_1": "12:30",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["published_at"].utcoffset(),
            timedelta(hours=5),
        )

    @patch("catalog.tasks.process_video_hls.delay")
    def test_video_can_be_added_before_scheduled_chapter_is_published(self, enqueue_task):
        add_video_page = self.client.get(reverse("admin:catalog_video_add"))
        chapter_field = add_video_page.context["adminform"].form.fields["chapter"]
        self.assertIn(self.chapter, chapter_field.queryset)
        self.assertContains(add_video_page, f'data-comic-id="{self.drama.pk}"')

        response = self.client.post(
            reverse("admin:catalog_video_add"),
            {
                "comic": self.drama.pk,
                "chapter": self.chapter.pk,
                "title": "Scheduled episode",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
                "_save": "Saqlash",
            },
        )

        self.assertEqual(response.status_code, 302)
        video = Video.objects.get(title="Scheduled episode")
        self.assertEqual(video.chapter, self.chapter)
        enqueue_task.assert_called_once_with(video.pk)

        chapter_response = self.client.get(
            reverse("comic-chapters", kwargs={"slug": self.drama.slug})
        )
        comic_response = self.client.get(
            reverse("comic-detail", kwargs={"slug": self.drama.slug})
        )
        comic_list_response = self.client.get(reverse("comic-list"))
        popular_response = self.client.get(
            reverse("popular-comics"),
            {"period": "all-time"},
        )
        self.assertEqual(chapter_response.data["results"], [])
        self.assertEqual(comic_response.data["chapter_count"], 0)
        list_drama = next(
            item for item in comic_list_response.data["results"] if item["slug"] == self.drama.slug
        )
        popular_drama = next(
            item for item in popular_response.data if item["slug"] == self.drama.slug
        )
        self.assertEqual(list_drama["chapter_count"], 0)
        self.assertEqual(popular_drama["chapter_count"], 0)

        Chapter.objects.filter(pk=self.chapter.pk).update(
            published_at=timezone.now() - timedelta(minutes=1)
        )
        cache.clear()
        chapter_response = self.client.get(
            reverse("comic-chapters", kwargs={"slug": self.drama.slug})
        )
        self.assertEqual(chapter_response.data["results"][0]["videos"][0]["title"], video.title)
        comic_response = self.client.get(
            reverse("comic-detail", kwargs={"slug": self.drama.slug})
        )
        self.assertEqual(comic_response.data["chapter_count"], 1)


class ComicCoverAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="comic-cover-admin",
            email="comic-cover-admin@example.com",
            password="test-only-password",
        )
        self.client.force_login(self.admin_user)

    def test_comic_admin_explains_cloudflare_cover_url_format(self):
        response = self.client.get(reverse("admin:catalog_comic_add"))

        self.assertEqual(response.status_code, 200)
        cover_field = response.context["adminform"].form.fields["cover_image"]
        self.assertIn("<drama-slug>/logo/<filename>", cover_field.help_text)
        self.assertIn("Cloudflare R2", cover_field.help_text)
        self.assertContains(response, 'name="logo_upload"')
        self.assertContains(response, "Cloudflare R2 ga drama logotipini yuklash")

    @override_settings(R2_STORAGE_ENABLED=True)
    @patch("catalog.admin.default_storage.save")
    def test_new_comic_uploads_logo_to_its_slug_folder(self, save_logo):
        save_logo.side_effect = lambda name, _file: name
        response = self.client.post(
            reverse("admin:catalog_comic_add"),
            {
                "title": "Uploaded Logo Comic",
                "slug": "uploaded-logo-comic",
                "synopsis": "",
                "cover_image": "",
                "logo_upload": SimpleUploadedFile(
                    "cover.png",
                    b"png-image",
                    content_type="image/png",
                ),
                "kind": Comic.Kind.MANHWA,
                "status": Comic.Status.ONGOING,
                "release_year": "",
                "chapters-TOTAL_FORMS": "0",
                "chapters-INITIAL_FORMS": "0",
                "chapters-MIN_NUM_FORMS": "0",
                "chapters-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        comic = Comic.objects.get(slug="uploaded-logo-comic")
        self.assertEqual(comic.cover_image, "cover.png")
        save_logo.assert_called_once()
        self.assertEqual(
            save_logo.call_args.args[0],
            "uploaded-logo-comic/logo/cover.png",
        )

    def test_new_comic_converts_local_logo_url_to_filename(self):
        response = self.client.post(
            reverse("admin:catalog_comic_add"),
            {
                "title": "New R2 Comic",
                "slug": "new-r2-comic",
                "synopsis": "",
                "cover_image": "http://127.0.0.1:5173/images/comic_logo/logo19.png",
                "kind": Comic.Kind.MANHWA,
                "status": Comic.Status.ONGOING,
                "release_year": "",
                "chapters-TOTAL_FORMS": "0",
                "chapters-INITIAL_FORMS": "0",
                "chapters-MIN_NUM_FORMS": "0",
                "chapters-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        comic = Comic.objects.get(slug="new-r2-comic")
        self.assertEqual(comic.cover_image, "logo19.png")

    @override_settings(R2_STORAGE_ENABLED=True)
    def test_comic_admin_rejects_non_image_logo_upload(self):
        response = self.client.post(
            reverse("admin:catalog_comic_add"),
            {
                "title": "Invalid Logo Comic",
                "slug": "invalid-logo-comic",
                "synopsis": "",
                "cover_image": "",
                "logo_upload": SimpleUploadedFile(
                    "video.mp4",
                    b"not-an-image",
                    content_type="video/mp4",
                ),
                "kind": Comic.Kind.MANHWA,
                "status": Comic.Status.ONGOING,
                "release_year": "",
                "chapters-TOTAL_FORMS": "0",
                "chapters-INITIAL_FORMS": "0",
                "chapters-MIN_NUM_FORMS": "0",
                "chapters-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Yaroqli nomga ega PNG, JPG yoki WEBP faylini ishlating.")
        self.assertFalse(Comic.objects.filter(slug="invalid-logo-comic").exists())


class ComicListOrderingApiTests(APITestCase):
    def test_comic_api_filters_by_release_year_and_orders_by_average_rating(self):
        lower_rated = Comic.objects.create(
            title="Older Rated",
            slug="older-rated",
            release_year=2023,
        )
        higher_rated = Comic.objects.create(
            title="Higher Rated",
            slug="higher-rated",
            release_year=2023,
        )
        Comic.objects.create(
            title="Different Year",
            slug="different-year",
            release_year=2022,
        )
        user = get_user_model().objects.create_user(username="catalog-rating-user")
        ComicRating.objects.create(comic=lower_rated, user=user, score=4)
        ComicRating.objects.create(comic=higher_rated, user=user, score=9)

        response = self.client.get(
            reverse("comic-list"),
            {"release_year": 2023, "ordering": "-average_rating"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [item["slug"] for item in response.data["results"]],
            ["higher-rated", "older-rated"],
        )

    def test_comic_api_filters_by_release_year_range(self):
        Comic.objects.create(title="2022 Drama", slug="drama-2022", release_year=2022)
        Comic.objects.create(title="2023 Drama", slug="drama-2023", release_year=2023)
        Comic.objects.create(title="2024 Drama", slug="drama-2024", release_year=2024)
        Comic.objects.create(title="Unknown Year Drama", slug="drama-unknown")

        response = self.client.get(
            reverse("comic-list"),
            {"release_year__gte": 2022, "release_year__lte": 2023},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {item["slug"] for item in response.data["results"]},
            {"drama-2022", "drama-2023"},
        )

    @override_settings(R2_PUBLIC_MEDIA_URL="https://pub.example.r2.dev/")
    def test_comic_api_resolves_r2_cover_object_key_to_public_url(self):
        comic = Comic.objects.create(
            title="R2 Cover",
            slug="r2-cover",
            cover_image="r2-cover.webp",
        )

        response = self.client.get(reverse("comic-list"))

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.data["results"] if item["id"] == comic.pk)
        self.assertEqual(
            result["cover_image"],
            "https://pub.example.r2.dev/r2-cover/logo/r2-cover.webp",
        )

    @override_settings(R2_PUBLIC_MEDIA_URL="https://pub.example.r2.dev/")
    def test_comic_api_keeps_logo_folder_when_full_r2_key_is_stored(self):
        comic = Comic.objects.create(
            title="Prefixed R2 Cover",
            slug="prefixed-r2-cover",
            cover_image="logo/r2-cover.webp",
        )

        response = self.client.get(reverse("comic-list"))

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.data["results"] if item["id"] == comic.pk)
        self.assertEqual(
            result["cover_image"],
            "https://pub.example.r2.dev/prefixed-r2-cover/logo/r2-cover.webp",
        )

    @override_settings(R2_PUBLIC_MEDIA_URL="https://pub.example.r2.dev/")
    def test_comic_api_preserves_manhwa_folder_for_full_relative_cover_key(self):
        comic = Comic.objects.create(
            title="Nested R2 Cover",
            slug="nested-r2-cover",
            cover_image="nested-r2-cover/logo/cover.webp",
        )

        response = self.client.get(reverse("comic-list"))

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.data["results"] if item["id"] == comic.pk)
        self.assertEqual(
            result["cover_image"],
            "https://pub.example.r2.dev/nested-r2-cover/logo/cover.webp",
        )

    @override_settings(
        R2_PUBLIC_URL="https://pub.example.r2.dev",
        R2_PUBLIC_MEDIA_URL="https://cdn.blixtv.uz/",
        R2_CDN_BASE_URL="https://cdn.blixtv.uz",
    )
    def test_comic_api_rewrites_existing_r2_public_url_to_custom_domain(self):
        old_public_url = (
            "https://pub.example.r2.dev/revenge-of-the-iron-blooded-sword-hound/"
            "logo/cover.webp"
        )
        comic = Comic.objects.create(
            title="Existing R2 URL Cover",
            slug="existing-r2-url-cover",
            cover_image=old_public_url,
        )

        response = self.client.get(reverse("comic-list"))

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.data["results"] if item["id"] == comic.pk)
        self.assertEqual(
            result["cover_image"],
            "https://cdn.blixtv.uz/revenge-of-the-iron-blooded-sword-hound/logo/cover.webp",
        )

    def test_comic_api_returns_cloudflare_cover_url_unchanged(self):
        cover_url = "https://pub.example.r2.dev/blix/comics/covers/example.webp"
        comic = Comic.objects.create(
            title="Cloudflare Cover",
            slug="cloudflare-cover",
            cover_image=cover_url,
        )

        response = self.client.get(reverse("comic-list"))

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.data["results"] if item["id"] == comic.pk)
        self.assertEqual(result["cover_image"], cover_url)

    @override_settings(
        R2_CDN_BASE_URL="https://cdn.blixtv.uz",
        R2_STORAGE_ENABLED=True,
    )
    def test_video_api_resolves_hls_key_to_public_url(self):
        comic = Comic.objects.create(title="R2 Comic", slug="r2-comic")
        chapter = Chapter.objects.create(comic=comic, number=1)
        video = Video.objects.create(
            chapter=chapter,
            title="R2 episode",
            original_file="r2-comic/video/chapter-1/1080p/episode-01.mp4",
            hls_playlist="r2-comic/video/chapter-1/1080p/episode-01.m3u8",
            status=Video.Status.READY,
        )

        response = self.client.get(reverse("comic-chapters", kwargs={"slug": chapter.comic.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["results"][0]["videos"][0]["hls_playlist"],
            "https://cdn.blixtv.uz/r2-comic/video/chapter-1/1080p/episode-01.m3u8",
        )
        self.assertEqual(
            response.data["results"][0]["videos"][0]["original_video"],
            "https://cdn.blixtv.uz/r2-comic/video/chapter-1/1080p/episode-01.mp4",
        )

    @override_settings(
        MEDIA_URL="/media/",
        R2_PUBLIC_MEDIA_URL="https://pub.example.r2.dev/",
        R2_STORAGE_ENABLED=False,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
            },
        },
    )
    def test_video_api_keeps_local_media_urls_when_r2_storage_is_disabled(self):
        video = Video.objects.create(
            title="Local episode",
            original_file="video/episode-02.mp4",
            hls_playlist="video/episode-02/master.m3u8",
            status=Video.Status.READY,
        )
        chapter = Chapter.objects.create(
            comic=Comic.objects.create(title="Local Comic", slug="local-comic"),
            number=1,
        )
        video.chapter = chapter
        video.save(update_fields=["chapter"])

        response = self.client.get(reverse("comic-chapters", kwargs={"slug": chapter.comic.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["results"][0]["videos"][0]["hls_playlist"],
            "/media/video/episode-02/master.m3u8",
        )
        self.assertEqual(
            response.data["results"][0]["videos"][0]["original_video"],
            "/media/video/episode-02.mp4",
        )

    @override_settings(R2_STORAGE_ENABLED=True)
    def test_failed_video_with_malformed_legacy_key_does_not_break_chapter_api(self):
        chapter = Chapter.objects.create(
            comic=Comic.objects.create(title="Broken Link Comic", slug="broken-link-comic"),
            number=1,
        )
        Video.objects.create(
            chapter=chapter,
            title="Failed old upload",
            original_file="https:/pub.example.r2.dev/video/episode.mp4",
            status=Video.Status.ERROR,
        )

        response = self.client.get(reverse("comic-chapters", kwargs={"slug": chapter.comic.slug}))

        self.assertEqual(response.status_code, 200)
        video_data = response.data["results"][0]["videos"][0]
        self.assertEqual(video_data["status"], Video.Status.ERROR)
        self.assertIsNone(video_data["hls_playlist"])
        self.assertIsNone(video_data["original_video"])

    def test_comic_search_treats_sql_syntax_as_plain_text(self):
        Comic.objects.create(title="A comic", slug="a-comic")

        response = self.client.get(
            reverse("comic-list"),
            {"search": "' OR 1=1 --", "ordering": "-created_at"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_comic_search_rejects_queries_longer_than_100_characters(self):
        response = self.client.get(reverse("comic-list"), {"search": "a" * 101})

        self.assertEqual(response.status_code, 400)

    def test_latest_activity_orders_chapters_and_new_comics_together(self):
        now = timezone.now()
        older_comic = Comic.objects.create(title="Older", slug="older")
        chapter_comic = Comic.objects.create(title="Chapter Update", slug="chapter-update")
        new_comic = Comic.objects.create(title="New Comic", slug="new-comic")
        Comic.objects.filter(pk=older_comic.pk).update(created_at=now - timedelta(days=10))
        Comic.objects.filter(pk=chapter_comic.pk).update(created_at=now - timedelta(days=10))
        Comic.objects.filter(pk=new_comic.pk).update(created_at=now - timedelta(hours=1))
        Chapter.objects.create(
            comic=chapter_comic,
            number=12,
            published_at=now - timedelta(minutes=30),
        )

        response = self.client.get(reverse("comic-list"), {"ordering": "-latest_activity_at"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [comic["slug"] for comic in response.data["results"]],
            ["chapter-update", "new-comic", "older"],
        )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "popular-comics-tests",
        }
    }
)
class PopularComicsApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.comics = [
            Comic.objects.create(title=f"Comic {index}", slug=f"comic-{index}")
            for index in range(12)
        ]

    def test_popular_endpoint_returns_top_ten_by_weekly_views(self):
        now = timezone.now()
        for index, comic in enumerate(self.comics):
            for _ in range(index):
                ComicView.objects.create(comic=comic)
        ComicView.objects.filter(comic=self.comics[11]).update(created_at=now - timedelta(days=8))

        response = self.client.get(reverse("popular-comics"), {"period": "weekly"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 10)
        self.assertEqual(response.data[0]["slug"], "comic-10")

    def test_popular_endpoint_supports_ranked_batches_without_duplicates(self):
        comics = self.comics + [
            Comic.objects.create(title=f"Extra Comic {index}", slug=f"extra-comic-{index}")
            for index in range(20)
        ]
        for rank, comic in enumerate(comics):
            for _ in range(rank):
                ComicView.objects.create(comic=comic)

        first_batch = self.client.get(
            reverse("popular-comics"),
            {"period": "all-time", "limit": 30, "offset": 0},
        )
        next_batch = self.client.get(
            reverse("popular-comics"),
            {"period": "all-time", "limit": 11, "offset": 30},
        )

        self.assertEqual(first_batch.status_code, 200)
        self.assertEqual(len(first_batch.data), 30)
        self.assertEqual(next_batch.status_code, 200)
        self.assertEqual(len(next_batch.data), 2)
        self.assertEqual(first_batch.data[-1]["slug"], "comic-2")
        self.assertEqual(
            {comic["slug"] for comic in first_batch.data}.intersection(
                comic["slug"] for comic in next_batch.data
            ),
            set(),
        )

    def test_popular_endpoint_rejects_invalid_batch_parameters(self):
        response = self.client.get(
            reverse("popular-comics"),
            {"period": "all-time", "limit": "many", "offset": 0},
        )

        self.assertEqual(response.status_code, 400)

    def test_popular_endpoint_returns_top_ten_by_views_in_last_24_hours(self):
        now = timezone.now()
        for index, comic in enumerate(self.comics):
            for _ in range(index):
                ComicView.objects.create(comic=comic)
        ComicView.objects.filter(comic=self.comics[11]).update(created_at=now - timedelta(hours=25))

        response = self.client.get(reverse("popular-comics"), {"period": "24h"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 10)
        self.assertEqual(
            [comic["slug"] for comic in response.data],
            [f"comic-{index}" for index in range(10, 0, -1)],
        )

    def test_popular_endpoint_rejects_unknown_period(self):
        response = self.client.get(reverse("popular-comics"), {"period": "yearly"})

        self.assertEqual(response.status_code, 400)

    def test_popular_cache_is_invalidated_after_new_view_events(self):
        response = self.client.get(reverse("popular-comics"), {"period": "weekly"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(cache.get("popular-comics:v3:weekly"))

        with self.captureOnCommitCallbacks(execute=True):
            view_response = self.client.post(
                reverse("comic-view-record", kwargs={"slug": self.comics[0].slug})
            )

        self.assertEqual(view_response.status_code, 204)
        self.assertIsNone(cache.get("popular-comics:v3:weekly"))

    def test_genre_deletion_invalidates_cached_popular_comics(self):
        genre = Genre.objects.create(name="Action", slug="action")
        comic = self.comics[5]
        comic.genres.add(genre)
        response = self.client.get(reverse("popular-comics"), {"period": "weekly"})

        self.assertEqual(response.status_code, 200)
        cached_comic = next(item for item in response.data if item["slug"] == comic.slug)
        self.assertEqual([item["name"] for item in cached_comic["genres"]], ["Action"])

        with self.captureOnCommitCallbacks(execute=True):
            genre.delete()

        self.assertIsNone(cache.get("popular-comics:v3:weekly"))
        updated_response = self.client.get(reverse("popular-comics"), {"period": "weekly"})
        updated_comic = next(item for item in updated_response.data if item["slug"] == comic.slug)
        self.assertEqual(updated_comic["genres"], [])

    def test_chapter_deletion_invalidates_cached_popular_comics(self):
        chapter = Chapter.objects.create(comic=self.comics[5], number=1)
        response = self.client.get(reverse("popular-comics"), {"period": "monthly"})

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(cache.get("popular-comics:v3:monthly"))

        with self.captureOnCommitCallbacks(execute=True):
            chapter.delete()

        self.assertIsNone(cache.get("popular-comics:v3:monthly"))


class VideoUploadApiTests(APITestCase):
    def setUp(self):
        self.media_dir = TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.media_settings = override_settings(
            MEDIA_ROOT=self.media_dir.name,
            VIDEO_MAX_UPLOAD_SIZE=16,
            R2_STORAGE_ENABLED=False,
            STORAGES={
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage"
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
                },
            },
        )
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.chapter = Chapter.objects.create(
            comic=Comic.objects.create(title="Upload Comic", slug="upload-comic"),
            number=1,
        )
        self.staff_user = get_user_model().objects.create_user(
            username="video-admin",
            email="video-admin@example.com",
            password="Strong-Password-2026!",
            is_staff=True,
        )

    def upload(self, filename="episode.mp4", content=b"video-data", chapter=None):
        self.client.force_authenticate(self.staff_user)
        data = {
            "title": " Episode 1 ",
            "original_file": SimpleUploadedFile(filename, content, content_type="video/mp4"),
        }
        if chapter is not None:
            data["chapter"] = chapter.pk
        return self.client.post(reverse("video-upload"), data, format="multipart")

    def test_r2_cleanup_removes_only_the_matching_local_media_copy(self):
        source_key = "upload-comic/video/chapter-1/1080p/episode.mp4"
        local_source = Path(self.media_dir.name) / Path(*source_key.split("/"))
        local_source.parent.mkdir(parents=True)
        local_source.write_bytes(b"local duplicate")
        unrelated_file = local_source.with_name("keep-this.mp4")
        unrelated_file.write_bytes(b"unrelated")

        with override_settings(R2_STORAGE_ENABLED=True, MEDIA_ROOT=self.media_dir.name):
            removed = _delete_local_media_copy_of_r2_source(source_key)

        self.assertTrue(removed)
        self.assertFalse(local_source.exists())
        self.assertTrue(unrelated_file.exists())

    def test_cleanup_keeps_local_source_when_r2_is_disabled(self):
        source_key = "upload-comic/video/chapter-1/1080p/episode.mp4"
        local_source = Path(self.media_dir.name) / Path(*source_key.split("/"))
        local_source.parent.mkdir(parents=True)
        local_source.write_bytes(b"local source")

        with override_settings(R2_STORAGE_ENABLED=False, MEDIA_ROOT=self.media_dir.name):
            removed = _delete_local_media_copy_of_r2_source(source_key)

        self.assertFalse(removed)
        self.assertTrue(local_source.exists())

    def test_video_upload_api_requires_chapter_for_folder_layout(self):
        self.client.force_authenticate(self.staff_user)

        response = self.client.post(
            reverse("video-upload"),
            {
                "title": "Episode without chapter",
                "original_file": SimpleUploadedFile(
                    "episode.mp4",
                    b"video-data",
                    content_type="video/mp4",
                ),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Video.objects.exists())

    def test_video_file_fields_allow_nested_r2_object_keys(self):
        self.assertEqual(Video._meta.get_field("original_file").max_length, 500)
        self.assertEqual(Video._meta.get_field("hls_playlist").max_length, 500)

    @patch("catalog.tasks.process_video_hls.delay")
    def test_video_upload_saves_comic_chapter_scoped_file_and_queues_processing(self, enqueue_task):
        response = self.upload(chapter=self.chapter)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], Video.Status.UPLOADING)
        video = Video.objects.get(pk=response.data["id"])
        self.assertEqual(video.title, "Episode 1")
        self.assertEqual(video.chapter, self.chapter)
        self.assertEqual(video.status, Video.Status.UPLOADING)
        self.assertRegex(
            video.original_file.name,
            r"^upload-comic/video/chapter-1/1080p/episode-[0-9a-f]{12}\.mp4$",
        )
        self.assertTrue(Path(video.original_file.path).is_file())
        enqueue_task.assert_called_once_with(video.pk)

    def test_video_upload_requires_staff_permission(self):
        response = self.client.post(
            reverse("video-upload"),
            {
                "title": "Episode",
                "original_file": SimpleUploadedFile("episode.mp4", b"video"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 401)

    @patch("catalog.tasks.process_video_hls.delay")
    def test_video_upload_rejects_unsupported_extension(self, enqueue_task):
        response = self.upload(filename="episode.avi")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Video.objects.count(), 0)
        enqueue_task.assert_not_called()

    @patch("catalog.tasks.process_video_hls.delay")
    def test_video_upload_rejects_files_over_configured_size(self, enqueue_task):
        response = self.upload(content=b"x" * 17)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Video.objects.count(), 0)
        enqueue_task.assert_not_called()

    def test_hls_master_playlist_lists_available_quality_variants(self):
        output_dir = Path(self.media_dir.name) / "hls-test"
        output_dir.mkdir()

        _write_master_playlist(
            output_dir,
            source_width=1920,
            source_height=1080,
            renditions=((360, 800_000), (480, 1_400_000), (720, 2_800_000)),
            rendition_paths={
                360: "360p/example-manhwa/chapter-4/index.m3u8",
                480: "480p/example-manhwa/chapter-4/index.m3u8",
                720: "720p/example-manhwa/chapter-4/index.m3u8",
            },
        )

        playlist = (output_dir / "master.m3u8").read_text(encoding="utf-8")
        self.assertEqual(playlist.count("#EXT-X-STREAM-INF:"), 3)
        self.assertIn("360p/example-manhwa/chapter-4/index.m3u8", playlist)
        self.assertIn("720p/example-manhwa/chapter-4/index.m3u8", playlist)
        self.assertNotIn("1080p/index.m3u8", playlist)

    @patch("catalog.tasks.subprocess.run")
    def test_hls_rendition_uses_aac_audio_and_zero_delay_muxing(self, run):
        rendition_dir = Path(self.media_dir.name) / "rendition"

        _transcode_rendition(Path(self.media_dir.name) / "input.mp4", rendition_dir, 360, 800_000)

        command = run.call_args.args[0]
        self.assertIn("aac", command)
        self.assertIn("aac_low", command)
        self.assertIn("48000", command)
        self.assertEqual(command[command.index("-loglevel") + 1], "error")
        self.assertEqual(command[command.index("-filter_threads") + 1], "1")
        self.assertEqual(command[command.index("-preset") + 1], "superfast")
        self.assertEqual(command.count("-threads:v"), 2)
        self.assertTrue(
            all(
                command[index + 1] == "1"
                for index, value in enumerate(command)
                if value == "-threads:v"
            )
        )
        self.assertEqual(command[command.index("-muxpreload") + 1], "0")
        self.assertEqual(command[command.index("-muxdelay") + 1], "0")

    def test_hls_storage_skips_existence_checks_when_storage_overwrites(self):
        output_dir = Path(self.media_dir.name) / "hls-overwrite-test"
        output_dir.mkdir()
        (output_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
        storage = Mock(file_overwrite=True)
        storage.save.side_effect = lambda name, _file_obj: name

        with patch("catalog.tasks.default_storage", storage):
            _store_hls_output(output_dir, "hls-prefix")

        storage.exists.assert_not_called()
        storage.delete.assert_not_called()
        storage.save.assert_called_once()

    @patch("catalog.tasks._probe_video", return_value=(1920, 1080))
    @patch("catalog.tasks._transcode_rendition")
    def test_hls_task_marks_video_ready_and_stores_playlist(self, transcode, probe):
        video = Video.objects.create(
            title="Episode",
            original_file=SimpleUploadedFile("episode.mp4", b"video"),
        )

        def write_rendition(_input_path, rendition_dir, _height, _bitrate):
            rendition_dir.mkdir(parents=True)
            (rendition_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (rendition_dir / "segment_00000.ts").write_bytes(b"segment")

        transcode.side_effect = write_rendition
        result = process_video_hls(video.pk)

        video.refresh_from_db()
        self.assertEqual(result, video.pk)
        self.assertEqual(video.status, Video.Status.READY)
        self.assertRegex(
            video.hls_playlist.name,
            r"^unassigned/video/chapter-unknown/1080p/episode-[0-9a-f]{12}\.m3u8$",
        )
        self.assertTrue(Path(video.hls_playlist.path).is_file())
        video_stem = Path(video.original_file.name).stem
        self.assertTrue(
            (
                Path(video.hls_playlist.path).parent
                / ".."
                / "360p"
                / video_stem
                / "index.m3u8"
            ).is_file()
        )
        self.assertEqual(
            (
                Path(video.hls_playlist.path).parent
                / ".."
                / "360p"
                / video_stem
                / "segment_00000.ts"
            ).read_bytes(),
            b"segment",
        )
        self.assertEqual(transcode.call_count, 3)

    @patch("catalog.tasks._probe_video", return_value=(1920, 1080))
    @patch("catalog.tasks._transcode_rendition")
    def test_hls_task_generates_only_the_requested_format(self, transcode, probe):
        video = Video.objects.create(
            title="Single format episode",
            original_file=SimpleUploadedFile("episode.mp4", b"video"),
        )

        def write_rendition(_input_path, rendition_dir, _height, _bitrate):
            rendition_dir.mkdir(parents=True)
            (rendition_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (rendition_dir / "segment_00000.ts").write_bytes(b"segment")

        transcode.side_effect = write_rendition
        process_video_hls(video.pk, 480)

        video.refresh_from_db()
        self.assertEqual(video.status, Video.Status.READY)
        self.assertEqual(transcode.call_count, 1)
        transcode.assert_called_once()
        master_playlist = Path(video.hls_playlist.path).read_text(encoding="utf-8")
        self.assertEqual(master_playlist.count("#EXT-X-STREAM-INF:"), 1)
        self.assertIn("../480p/", master_playlist)
        self.assertFalse(video.hls_playlist.storage.exists(
            f"unassigned/video/chapter-unknown/360p/{Path(video.original_file.name).stem}/index.m3u8"
        ))

    @patch("catalog.tasks._probe_video", return_value=(1920, 1080))
    @patch("catalog.tasks._transcode_rendition")
    def test_chapter_upload_keeps_original_and_stores_hls_in_same_chapter_folder(
        self,
        transcode,
        probe,
    ):
        chapter = Chapter.objects.create(
            comic=Comic.objects.create(title="Chapter Comic", slug="chapter-comic"),
            number=12,
        )
        video = Video.objects.create(
            title="Chapter episode",
            chapter=chapter,
            original_file=SimpleUploadedFile("episode.mp4", b"original-video"),
        )

        def write_rendition(_input_path, rendition_dir, _height, _bitrate):
            rendition_dir.mkdir(parents=True)
            (rendition_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (rendition_dir / "segment_00000.ts").write_bytes(b"segment")

        transcode.side_effect = write_rendition
        process_video_hls(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.status, Video.Status.READY)
        self.assertRegex(
            video.original_file.name,
            r"^chapter-comic/video/chapter-12/1080p/episode-[0-9a-f]{12}\.mp4$",
        )
        video_stem = Path(video.original_file.name).stem
        self.assertEqual(
            video.hls_playlist.name,
            f"chapter-comic/video/chapter-12/1080p/{video_stem}.m3u8",
        )
        playlist = Path(video.hls_playlist.path).read_text(encoding="utf-8")
        self.assertIn(f"../360p/{video_stem}/index.m3u8", playlist)
        self.assertTrue(
            (
                Path(video.hls_playlist.path).parent
                / ".."
                / "360p"
                / video_stem
                / "index.m3u8"
            ).is_file()
        )
        with video.original_file.open("rb") as original_file:
            self.assertEqual(original_file.read(), b"original-video")

        second_video = Video.objects.create(
            title="Another chapter video",
            chapter=chapter,
            original_file=SimpleUploadedFile("episode.mp4", b"second-video"),
        )
        process_video_hls(second_video.pk)

        video.refresh_from_db()
        second_video.refresh_from_db()
        self.assertNotEqual(video.original_file.name, second_video.original_file.name)
        self.assertTrue(video.original_file.storage.exists(video.original_file.name))
        self.assertTrue(video.hls_playlist.storage.exists(video.hls_playlist.name))
        self.assertTrue(
            second_video.hls_playlist.storage.exists(second_video.hls_playlist.name)
        )
        self.assertTrue(
            video.original_file.storage.exists(
                f"chapter-comic/video/chapter-12/360p/{video_stem}/index.m3u8"
            )
        )

    @patch("catalog.tasks._probe_video", return_value=(754, 210))
    @patch("catalog.tasks._transcode_rendition")
    def test_small_source_keeps_original_without_generating_unavailable_hls_qualities(
        self,
        transcode,
        probe,
    ):
        video = Video.objects.create(
            title="Small source episode",
            original_file=SimpleUploadedFile("small-source.mp4", b"video"),
        )

        def write_rendition(_input_path, rendition_dir, _height, _bitrate):
            rendition_dir.mkdir(parents=True)
            (rendition_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
            (rendition_dir / "segment_00000.ts").write_bytes(b"segment")

        transcode.side_effect = write_rendition
        process_video_hls(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.status, Video.Status.READY)
        self.assertEqual(transcode.call_count, 0)
        self.assertFalse(video.hls_playlist.name)
        self.assertTrue(video.original_file.storage.exists(video.original_file.name))

    @patch("catalog.tasks._probe_video", side_effect=RuntimeError("ffprobe failed"))
    def test_hls_task_marks_video_error_and_reraises_processing_failures(self, probe):
        video = Video.objects.create(
            title="Broken episode",
            original_file=SimpleUploadedFile("episode.mp4", b"video"),
        )

        with self.assertRaisesRegex(RuntimeError, "ffprobe failed"):
            process_video_hls(video.pk)

        video.refresh_from_db()
        self.assertEqual(video.status, Video.Status.ERROR)

    @patch(
        "catalog.tasks.default_storage.listdir",
        side_effect=ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Missing prefix"}},
            "ListObjectsV2",
        ),
    )
    def test_hls_cleanup_ignores_missing_object_storage_prefix(self, listdir):
        _delete_storage_tree("videos/hls/missing")

        listdir.assert_called_once_with("videos/hls/missing")

    @patch(
        "catalog.tasks.default_storage.listdir",
        side_effect=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not permitted"}},
            "ListObjectsV2",
        ),
    )
    def test_hls_cleanup_reraises_unexpected_storage_errors(self, listdir):
        with self.assertRaises(ClientError):
            _delete_storage_tree("videos/hls/restricted")


class AccountApiTests(APITestCase):
    def test_registration_creates_user_without_returning_password(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new-reader",
                "email": "reader@example.com",
                "password": "Strong-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("password", response.data)
        user = get_user_model().objects.get(username="new-reader")
        self.assertTrue(user.check_password("Strong-Password-2026!"))

    def test_registration_rejects_case_insensitive_duplicate_email(self):
        get_user_model().objects.create_user(
            username="existing-reader",
            email="reader@example.com",
            password="Strong-Password-2026!",
        )

        response = self.client.post(
            reverse("register"),
            {
                "username": "another-reader",
                "email": "READER@example.com",
                "password": "Another-Password-2026!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_login_returns_jwt_pair(self):
        user = get_user_model().objects.create_user(
            username="reader",
            email="reader@example.com",
            password="Strong-Password-2026!",
        )

        response = self.client.post(
            reverse("token_obtain_pair"),
            {"email": user.email, "password": "Strong-Password-2026!"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


class UserComicActionsApiTests(APITestCase):
    def setUp(self):
        self.comic = Comic.objects.create(title="Test Comic", slug="test-comic")
        self.user = get_user_model().objects.create_user(
            username="reader",
            email="reader@example.com",
            password="Strong-Password-2026!",
        )
        self.client.force_authenticate(self.user)

    def test_bookmark_is_idempotent_and_appears_in_user_list(self):
        url = reverse("comic-bookmark", args=[self.comic.slug])

        first_response = self.client.post(url)
        second_response = self.client.post(url)
        list_response = self.client.get(reverse("my-bookmarks"))

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(Bookmark.objects.filter(user=self.user, comic=self.comic).count(), 1)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["results"][0]["slug"], self.comic.slug)

    def test_rating_is_validated_and_updated_instead_of_duplicated(self):
        url = reverse("comic-rating", args=[self.comic.slug])

        invalid_response = self.client.post(url, {"score": 11}, format="json")
        first_response = self.client.post(url, {"score": 7}, format="json")
        update_response = self.client.post(url, {"score": 9}, format="json")

        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.data["score"], 9)
        self.assertEqual(ComicRating.objects.filter(user=self.user, comic=self.comic).count(), 1)

    def test_comic_detail_includes_view_and_rating_counts(self):
        Chapter.objects.create(comic=self.comic, number=1)
        Chapter.objects.create(comic=self.comic, number=2)
        ComicRating.objects.create(comic=self.comic, user=self.user, score=8)
        self.client.post(reverse("comic-view-record", args=[self.comic.slug]))
        self.client.force_authenticate(user=None)
        self.client.post(reverse("comic-view-record", args=[self.comic.slug]))

        response = self.client.get(reverse("comic-detail", args=[self.comic.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["view_count"], 2)
        self.assertEqual(response.data["rating_count"], 1)
        self.assertEqual(response.data["average_rating"], 8.0)
        self.assertEqual(response.data["chapter_count"], 2)

    def test_public_view_record_does_not_require_session_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(
            reverse("comic-view-record", args=[self.comic.slug]),
            HTTP_ORIGIN="http://127.0.0.1:5173",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(ComicView.objects.filter(comic=self.comic).count(), 1)

    def test_bookmark_api_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse("comic-bookmark", args=[self.comic.slug]))

        self.assertEqual(response.status_code, 401)