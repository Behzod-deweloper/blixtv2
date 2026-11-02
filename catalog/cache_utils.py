from django.core.cache import cache


POPULAR_CACHE_KEYS = tuple(
    f"popular-comics:v3:{period}" for period in ("24h", "weekly", "monthly", "all-time")
)


def invalidate_popular_comics_cache():
    cache.delete_many(POPULAR_CACHE_KEYS)
