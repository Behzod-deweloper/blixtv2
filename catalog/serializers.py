from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from .cdn_urls import get_video_cdn_url
from .media_urls import get_public_media_url
from .models import Chapter, ChapterPage, Comic, ComicRating, Genre, Video


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "name", "slug"]


class ComicSerializer(serializers.ModelSerializer):
    cover_image = serializers.SerializerMethodField()
    genres = GenreSerializer(many=True, read_only=True)
    average_rating = serializers.FloatField(read_only=True, allow_null=True)
    rating_count = serializers.IntegerField(read_only=True, default=0)
    view_count = serializers.IntegerField(read_only=True, default=0)
    chapter_count = serializers.IntegerField(read_only=True, default=0)
    period_views = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Comic
        fields = [
            "id",
            "title",
            "slug",
            "synopsis",
            "cover_image",
            "kind",
            "status",
            "release_year",
            "genres",
            "average_rating",
            "rating_count",
            "view_count",
            "chapter_count",
            "period_views",
            "created_at",
            "updated_at",
        ]

    def get_cover_image(self, comic):
        cover_key = comic.cover_image.strip()
        if cover_key.startswith("logo/"):
            cover_key = cover_key.removeprefix("logo/")
        return get_public_media_url(cover_key, prefix=f"{comic.slug}/logo")


class ChapterPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChapterPage
        fields = ["page_number", "image_url"]


class VideoSerializer(serializers.ModelSerializer):
    hls_playlist = serializers.SerializerMethodField()
    original_video = serializers.SerializerMethodField()

    class Meta:
        model = Video
        fields = ["id", "title", "hls_playlist", "original_video", "created_at", "status"]

    def get_hls_playlist(self, video):
        if video.status != Video.Status.READY or not video.hls_playlist:
            return None
        if settings.R2_STORAGE_ENABLED:
            return get_video_cdn_url(video.hls_playlist.name)
        return video.hls_playlist.url

    def get_original_video(self, video):
        if video.status != Video.Status.READY or not video.original_file:
            return None
        if settings.R2_STORAGE_ENABLED:
            return get_video_cdn_url(video.original_file.name)
        return video.original_file.url


class ChapterSerializer(serializers.ModelSerializer):
    pages = ChapterPageSerializer(many=True, read_only=True)
    videos = VideoSerializer(many=True, read_only=True)

    class Meta:
        model = Chapter
        fields = ["id", "number", "title", "published_at", "pages", "videos"]


class VideoUploadSerializer(serializers.ModelSerializer):
    chapter = serializers.PrimaryKeyRelatedField(
        queryset=Chapter.objects.all(),
        required=True,
        allow_null=False,
    )

    class Meta:
        model = Video
        fields = ["title", "original_file", "chapter"]

    def validate_title(self, value):
        title = value.strip()
        if not title:
            raise serializers.ValidationError("A title is required.")
        return title

    def validate_original_file(self, uploaded_file):
        allowed_extensions = {".mp4", ".mov"}
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in allowed_extensions:
            raise serializers.ValidationError("Only .mp4 and .mov video files are allowed.")

        max_size = settings.VIDEO_MAX_UPLOAD_SIZE
        if uploaded_file.size > max_size:
            max_size_mib = max_size // (1024 * 1024)
            raise serializers.ValidationError(f"Video file must not exceed {max_size_mib} MiB.")
        return uploaded_file


class ComicRatingSerializer(serializers.ModelSerializer):
    score = serializers.IntegerField(min_value=1, max_value=10)

    class Meta:
        model = ComicRating
        fields = ["score", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]