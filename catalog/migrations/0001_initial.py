import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Genre",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=80, unique=True)),
                ("slug", models.SlugField(max_length=100, unique=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Comic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=240)),
                ("slug", models.SlugField(max_length=260, unique=True)),
                ("synopsis", models.TextField(blank=True)),
                ("cover_image", models.URLField(blank=True, max_length=1000)),
                ("kind", models.CharField(choices=[("manga", "Manga"), ("manhwa", "Manhwa"), ("manhua", "Manhua")], default="manhwa", max_length=12)),
                ("status", models.CharField(choices=[("ongoing", "Ongoing"), ("completed", "Completed"), ("hiatus", "Hiatus")], default="ongoing", max_length=12)),
                ("release_year", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("genres", models.ManyToManyField(blank=True, related_name="comics", to="catalog.genre")),
            ],
            options={
                "ordering": ["-created_at", "id"],
                "indexes": [
                    models.Index(fields=["status", "-created_at"], name="comic_status_created_idx"),
                    models.Index(fields=["kind", "status"], name="comic_kind_status_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Chapter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.DecimalField(decimal_places=2, max_digits=8)),
                ("title", models.CharField(blank=True, max_length=240)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("comic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chapters", to="catalog.comic")),
            ],
            options={
                "ordering": ["-number"],
                "indexes": [models.Index(fields=["comic", "-number"], name="chapter_comic_num_idx")],
                "constraints": [models.UniqueConstraint(fields=("comic", "number"), name="chapter_comic_number_unique")],
            },
        ),
        migrations.CreateModel(
            name="ChapterPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("page_number", models.PositiveSmallIntegerField()),
                ("image_url", models.URLField(max_length=1000)),
                ("chapter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pages", to="catalog.chapter")),
            ],
            options={
                "ordering": ["page_number"],
                "constraints": [
                    models.UniqueConstraint(fields=("chapter", "page_number"), name="page_chapter_number_unique"),
                    models.CheckConstraint(condition=Q(page_number__gte=1), name="page_number_positive"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ComicView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("comic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="view_events", to="catalog.comic")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="comic_views", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["comic", "-created_at"], name="view_comic_created_idx")],
            },
        ),
        migrations.CreateModel(
            name="ComicRating",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("score", models.PositiveSmallIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("comic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ratings", to="catalog.comic")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comic_ratings", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "indexes": [models.Index(fields=["comic", "score"], name="rating_comic_score_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("comic", "user"), name="rating_comic_user_unique"),
                    models.CheckConstraint(condition=Q(score__gte=1, score__lte=10), name="rating_score_1_to_10"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Bookmark",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("comic", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bookmarks", to="catalog.comic")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="bookmarks", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [models.Index(fields=["user", "-created_at"], name="bookmark_user_created_idx")],
                "constraints": [models.UniqueConstraint(fields=("comic", "user"), name="bookmark_comic_user_unique")],
            },
        ),
    ]