from django.urls import path

from .views import (
    ComicChapterListView,
    ComicBookmarkView,
    ComicDetailView,
    ComicListView,
    ComicRatingView,
    GenreListView,
    MyBookmarksView,
    PopularComicsView,
    RecordComicViewView,
    VideoUploadView,
)
from .multipart_uploads import (
    AbortMultipartUploadView,
    CompleteMultipartUploadView,
    MultipartPartUrlView,
    MultipartResumeView,
    MultipartUploadView,
)


urlpatterns = [
    path("genres/", GenreListView.as_view(), name="genre-list"),
    path("me/bookmarks/", MyBookmarksView.as_view(), name="my-bookmarks"),
    path("comics/", ComicListView.as_view(), name="comic-list"),
    path("comics/popular/", PopularComicsView.as_view(), name="popular-comics"),
    path("videos/upload/", VideoUploadView.as_view(), name="video-upload"),
    path(
        "videos/multipart/initiate/",
        MultipartUploadView.as_view(),
        name="video-multipart-initiate",
    ),
    path(
        "videos/multipart/part-url/",
        MultipartPartUrlView.as_view(),
        name="video-multipart-part-url",
    ),
    path(
        "videos/multipart/resume/",
        MultipartResumeView.as_view(),
        name="video-multipart-resume",
    ),
    path(
        "videos/multipart/complete/",
        CompleteMultipartUploadView.as_view(),
        name="video-multipart-complete",
    ),
    path(
        "videos/multipart/abort/",
        AbortMultipartUploadView.as_view(),
        name="video-multipart-abort",
    ),
    path("comics/<slug:slug>/bookmark/", ComicBookmarkView.as_view(), name="comic-bookmark"),
    path("comics/<slug:slug>/rating/", ComicRatingView.as_view(), name="comic-rating"),
    path("comics/<slug:slug>/", ComicDetailView.as_view(), name="comic-detail"),
    path("comics/<slug:slug>/chapters/", ComicChapterListView.as_view(), name="comic-chapters"),
    path("comics/<slug:slug>/views/", RecordComicViewView.as_view(), name="comic-view-record"),
]