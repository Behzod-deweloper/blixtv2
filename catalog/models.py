from decimal import Decimal
from pathlib import Path
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.text import slugify


def video_chapter_storage_prefix(chapter):
    if chapter is None:
        return "unassigned/video/chapter-unknown"

    chapter_number = format(Decimal(str(chapter.number)).normalize(), "f")
    return f"{chapter.comic.slug}/video/chapter-{chapter_number}"


def video_original_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    video_name = slugify(Path(filename).stem)[:120] or "video"
    video_name = f"{video_name}-{uuid.uuid4().hex[:12]}"
    return (
        f"{video_chapter_storage_prefix(instance.chapter)}/"
        f"1080p/{video_name}{extension}"
    )


class Genre(models.Model):
    name = models.CharField(max_length=80, unique=True, verbose_name="nom")
    slug = models.SlugField(max_length=100, unique=True, verbose_name="slug")

    class Meta:
        ordering = ["name"]
        verbose_name = "janr"
        verbose_name_plural = "janrlar"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Comic(models.Model):
    class Status(models.TextChoices):
        ONGOING = "ongoing", "Davom etmoqda"
        COMPLETED = "completed", "Yakunlangan"
        HIATUS = "hiatus", "Tanaffus"

    class Kind(models.TextChoices):
        MANGA = "manga", "Manga"
        MANHWA = "manhwa", "Manhwa"
        MANHUA = "manhua", "Manhua"

    title = models.CharField(max_length=240, verbose_name="sarlavha")
    slug = models.SlugField(max_length=260, unique=True, verbose_name="slug")
    synopsis = models.TextField(blank=True, verbose_name="qisqacha matn")
    cover_image = models.CharField(max_length=1000, blank=True, verbose_name="muqova rasmi")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.MANHWA, verbose_name="turi")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ONGOING, verbose_name="holati")
    release_year = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="chiqarilgan yil")
    genres = models.ManyToManyField(Genre, related_name="comics", blank=True, verbose_name="janrlar")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="yaratilgan vaqti")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="yangilangan vaqti")

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name = "drama"
        verbose_name_plural = "dramalar"
        indexes = [
            models.Index(fields=["status", "-created_at"], name="comic_status_created_idx"),
            models.Index(fields=["kind", "status"], name="comic_kind_status_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class Chapter(models.Model):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="chapters", verbose_name="komik")
    number = models.DecimalField(max_digits=8, decimal_places=2, verbose_name="raqam")
    title = models.CharField(max_length=240, blank=True, verbose_name="sarlavha")
    published_at = models.DateTimeField(null=True, blank=True, verbose_name="nashr qilingan vaqti")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="yaratilgan vaqti")

    class Meta:
        ordering = ["-number"]
        verbose_name = "qism"
        verbose_name_plural = "qismlar"
        constraints = [
            models.UniqueConstraint(fields=["comic", "number"], name="chapter_comic_number_unique"),
        ]
        indexes = [models.Index(fields=["comic", "-number"], name="chapter_comic_num_idx")]

    def __str__(self):
        return f"{self.comic.title} - {self.number}-qism"


class ChapterPage(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="pages", verbose_name="bob")
    page_number = models.PositiveSmallIntegerField(verbose_name="sahifa raqami")
    image_url = models.URLField(max_length=1000, verbose_name="rasm URL")

    class Meta:
        ordering = ["page_number"]
        verbose_name = "bob sahifasi"
        verbose_name_plural = "bob sahifalari"
        constraints = [
            models.UniqueConstraint(fields=["chapter", "page_number"], name="page_chapter_number_unique"),
            models.CheckConstraint(condition=Q(page_number__gte=1), name="page_number_positive"),
        ]


class Video(models.Model):
    class Status(models.TextChoices):
        UPLOADING = "uploading", "Yuklanmoqda"
        PROCESSING = "processing", "Qayta ishlanmoqda"
        READY = "ready", "Tayyor"
        ERROR = "error", "Xato"

    chapter = models.ForeignKey(
        Chapter,
        on_delete=models.CASCADE,
        related_name="videos",
        null=True,
        blank=True,
        verbose_name="bob",
    )
    title = models.CharField(max_length=240, verbose_name="nom")
    original_file = models.FileField(upload_to=video_original_upload_to, max_length=500, verbose_name="asosiy video fayl")
    hls_playlist = models.FileField(upload_to="video/", max_length=500, null=True, blank=True, verbose_name="HLS play list")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="yaratilgan vaqti")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADING, verbose_name="holati")

    class Meta:
        ordering = ["-created_at", "id"]
        verbose_name = "video"
        verbose_name_plural = "videolar"

    def __str__(self):
        return self.title


class ComicView(models.Model):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="view_events", verbose_name="komik")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="comic_views",
        verbose_name="foydalanuvchi",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="ko'rilgan vaqti")

    class Meta:
        verbose_name = "ko'rish"
        verbose_name_plural = "ko'rishlar"
        indexes = [
            models.Index(fields=["comic", "-created_at"], name="view_comic_created_idx"),
        ]


class ComicRating(models.Model):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="ratings", verbose_name="komik")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comic_ratings", verbose_name="foydalanuvchi")
    score = models.PositiveSmallIntegerField(verbose_name="ball")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="yaratilgan vaqti")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="yangilangan vaqti")

    class Meta:
        verbose_name = "reyting"
        verbose_name_plural = "reytinglar"
        constraints = [
            models.UniqueConstraint(fields=["comic", "user"], name="rating_comic_user_unique"),
            models.CheckConstraint(condition=Q(score__gte=1, score__lte=10), name="rating_score_1_to_10"),
        ]
        indexes = [models.Index(fields=["comic", "score"], name="rating_comic_score_idx")]


class Bookmark(models.Model):
    comic = models.ForeignKey(Comic, on_delete=models.CASCADE, related_name="bookmarks", verbose_name="komik")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks", verbose_name="foydalanuvchi")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="yaratilgan vaqti")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "xatcho'p"
        verbose_name_plural = "xatcho'plar"
        constraints = [
            models.UniqueConstraint(fields=["comic", "user"], name="bookmark_comic_user_unique"),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="bookmark_user_created_idx")]