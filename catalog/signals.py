from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete
from django.dispatch import receiver

from .cache_utils import invalidate_popular_comics_cache
from .media_cleanup import delete_storage_tree
from .models import Chapter, ChapterPage, Comic, ComicRating, ComicView, Genre, video_chapter_storage_prefix


@receiver(post_save, sender=Comic)
@receiver(post_delete, sender=Comic)
@receiver(post_save, sender=Genre)
@receiver(post_delete, sender=Genre)
@receiver(post_save, sender=Chapter)
@receiver(post_delete, sender=Chapter)
@receiver(post_save, sender=ChapterPage)
@receiver(post_delete, sender=ChapterPage)
@receiver(post_save, sender=ComicRating)
@receiver(post_delete, sender=ComicRating)
@receiver(post_save, sender=ComicView)
@receiver(post_delete, sender=ComicView)
def invalidate_popular_cache_for_catalog_changes(**kwargs):
    transaction.on_commit(invalidate_popular_comics_cache)


@receiver(m2m_changed, sender=Comic.genres.through)
def invalidate_popular_cache_for_comic_genres(**kwargs):
    transaction.on_commit(invalidate_popular_comics_cache)


@receiver(pre_delete, sender=Chapter)
def delete_chapter_media(sender, instance, origin, **kwargs):
    origin_model = origin if isinstance(origin, type) else getattr(origin, "model", None)
    if origin_model is Comic:
        return
    if isinstance(origin, Comic) and origin.pk == instance.comic_id:
        return
    delete_storage_tree(video_chapter_storage_prefix(instance))


@receiver(pre_delete, sender=Comic)
def delete_comic_media(sender, instance, **kwargs):
    delete_storage_tree(f"{instance.slug}/video")
    delete_storage_tree(f"{instance.slug}/logo")
