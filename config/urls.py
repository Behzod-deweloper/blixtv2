from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def api_root(request):
    return Response(
        {
            "comics": "/api/comics/",
            "popular_comics": "/api/comics/popular/?period=24h|weekly|monthly|all-time",
            "genres": "/api/genres/",
            "video_upload": "/api/videos/upload/",
        }
    )


urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/", api_root, name="api-root"),
    path("api/auth/", include("accounts.urls")),
    path("api/", include("catalog.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)