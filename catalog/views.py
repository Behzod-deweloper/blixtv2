import json
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import Avg, Count, F, Max, Min, Q
from django.db.models.functions import Coalesce, Greatest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Bookmark, Chapter, Comic, ComicRating, ComicView, Genre, Video
from .serializers import (
    ChapterSerializer,
    ComicRatingSerializer,
    ComicSerializer,
    GenreSerializer,
    VideoUploadSerializer,
)


logger = logging.getLogger(__name__)


class ComicListView(generics.ListAPIView):
    serializer_class = ComicSerializer

    def get_queryset(self):
        now = timezone.now()
        visible_chapters = Q(chapters__published_at__isnull=True) | Q(
            chapters__published_at__lte=now
        )
        return Comic.objects.prefetch_related("genres").annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings", distinct=True),
            view_count=Count("view_events", distinct=True),
            chapter_count=Count("chapters", filter=visible_chapters, distinct=True),
            latest_activity_at=Greatest(
                F("created_at"),
                Coalesce(
                    Max(
                        Coalesce("chapters__published_at", "chapters__created_at"),
                        filter=visible_chapters,
                    ),
                    F("created_at"),
                ),
            ),
        )

    filterset_fields = {
        "kind": ["exact"],
        "status": ["exact"],
        "genres__slug": ["exact"],
        "release_year": ["exact", "gte", "lte"],
    }
    search_fields = ["title", "synopsis"]
    ordering_fields = ["title", "created_at", "release_year", "latest_activity_at", "average_rating"]

    def filter_queryset(self, queryset):
        search_term = self.request.query_params.get("search", "")
        if len(search_term) > 100:
            raise ValidationError({"search": "Search must be 100 characters or fewer."})
        return super().filter_queryset(queryset)


class PopularComicsView(generics.ListAPIView):
    serializer_class = ComicSerializer
    pagination_class = None
    cache_timeout = 60

    def get_period(self):
        period = self.request.query_params.get("period", "monthly")
        if period not in {"24h", "weekly", "monthly", "all-time"}:
            raise ValidationError({"period": "Choose 24h, weekly, monthly, or all-time."})
        return period

    def get_limit_and_offset(self):
        try:
            limit = int(self.request.query_params.get("limit", 10))
            offset = int(self.request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            raise ValidationError({"pagination": "Limit and offset must be integers."})
        if not 1 <= limit <= 100 or offset < 0:
            raise ValidationError({"pagination": "Limit must be between 1 and 100 and offset cannot be negative."})
        return limit, offset

    def get_queryset(self):
        period = self.get_period()

        views = Count("view_events", distinct=True)
        if period != "all-time":
            window = timedelta(hours=24) if period == "24h" else timedelta(days=7 if period == "weekly" else 30)
            since = timezone.now() - window
            views = Count(
                "view_events",
                filter=Q(view_events__created_at__gte=since),
                distinct=True,
            )

        return (
            Comic.objects.prefetch_related("genres")
            .annotate(
                period_views=views,
                average_rating=Avg("ratings__score"),
                rating_count=Count("ratings", distinct=True),
                view_count=Count("view_events", distinct=True),
                chapter_count=Count(
                    "chapters",
                    filter=Q(chapters__published_at__isnull=True)
                    | Q(chapters__published_at__lte=timezone.now()),
                    distinct=True,
                ),
            )
            .order_by("-period_views", "-created_at", "id")
        )

    def list(self, request, *args, **kwargs):
        period = self.get_period()
        limit, offset = self.get_limit_and_offset()
        cache_key = f"popular-comics:v3:{period}"
        cached_data = cache.get(cache_key)
        if cached_data is None:
            queryset = self.filter_queryset(self.get_queryset())
            cached_data = json.loads(JSONRenderer().render(self.get_serializer(queryset, many=True).data))
            now = timezone.now()
            next_publish_at = Chapter.objects.filter(published_at__gt=now).aggregate(
                next_publish_at=Min("published_at")
            )["next_publish_at"]
            cache_timeout = self.cache_timeout
            if next_publish_at:
                seconds_until_publish = int((next_publish_at - now).total_seconds())
                cache_timeout = max(1, min(cache_timeout, seconds_until_publish))
            cache.set(cache_key, cached_data, timeout=cache_timeout)
        return Response(cached_data[offset:offset + limit])


class ComicDetailView(generics.RetrieveAPIView):
    serializer_class = ComicSerializer
    lookup_field = "slug"

    def get_queryset(self):
        visible_chapters = Q(chapters__published_at__isnull=True) | Q(
            chapters__published_at__lte=timezone.now()
        )
        return Comic.objects.prefetch_related("genres").annotate(
            average_rating=Avg("ratings__score"),
            rating_count=Count("ratings", distinct=True),
            view_count=Count("view_events", distinct=True),
            chapter_count=Count("chapters", filter=visible_chapters, distinct=True),
        )


class ComicChapterListView(generics.ListAPIView):
    serializer_class = ChapterSerializer

    def get_queryset(self):
        comic = get_object_or_404(Comic, slug=self.kwargs["slug"])
        return (
            Chapter.objects.filter(comic=comic)
            .filter(Q(published_at__isnull=True) | Q(published_at__lte=timezone.now()))
            .prefetch_related("pages", "videos")
        )


class VideoUploadView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = VideoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        video = serializer.save()

        from .tasks import process_video_hls

        try:
            process_video_hls.delay(video.pk)
        except Exception:
            logger.exception("Could not enqueue HLS processing for video %s.", video.pk)
            video.status = Video.Status.ERROR
            video.save(update_fields=["status"])
            return Response(
                {"id": video.pk, "status": video.status, "detail": "Video processing could not be queued."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {"id": video.pk, "status": Video.Status.UPLOADING},
            status=status.HTTP_202_ACCEPTED,
        )


class GenreListView(generics.ListAPIView):
    serializer_class = GenreSerializer
    queryset = Genre.objects.all()
    pagination_class = None


class MyBookmarksView(generics.ListAPIView):
    serializer_class = ComicSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        visible_chapters = Q(chapters__published_at__isnull=True) | Q(
            chapters__published_at__lte=timezone.now()
        )
        return (
            Comic.objects.filter(bookmarks__user=self.request.user)
            .prefetch_related("genres")
            .annotate(
                average_rating=Avg("ratings__score"),
                period_views=Count("view_events", distinct=True),
                rating_count=Count("ratings", distinct=True),
                view_count=Count("view_events", distinct=True),
                chapter_count=Count("chapters", filter=visible_chapters, distinct=True),
            )
            .order_by("-bookmarks__created_at", "id")
        )


class ComicBookmarkView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        bookmarked = Bookmark.objects.filter(comic=comic, user=request.user).exists()
        return Response({"bookmarked": bookmarked})

    def post(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        _, created = Bookmark.objects.get_or_create(comic=comic, user=request.user)
        return Response(
            {"bookmarked": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        deleted, _ = Bookmark.objects.filter(comic=comic, user=request.user).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComicRatingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        rating = get_object_or_404(ComicRating, comic=comic, user=request.user)
        return Response(ComicRatingSerializer(rating).data)

    def post(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        serializer = ComicRatingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            rating, created = ComicRating.objects.update_or_create(
                comic=comic,
                user=request.user,
                defaults={"score": serializer.validated_data["score"]},
            )
        return Response(
            ComicRatingSerializer(rating).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        deleted, _ = ComicRating.objects.filter(comic=comic, user=request.user).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RecordComicViewView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "comic_view"

    def post(self, request, slug):
        comic = get_object_or_404(Comic, slug=slug)
        user = request.user if request.user.is_authenticated else None
        ComicView.objects.create(comic=comic, user=user)
        return Response(status=status.HTTP_204_NO_CONTENT)