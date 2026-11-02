<<<<<<< HEAD
# Mangalab API

Django REST API and MySQL database for the Mangalab frontend. The API project lives separately from the static frontend.

## Database design

- `accounts.User`: email-login user with a case-insensitive unique email.
- `catalog.Genre` and `catalog.Comic`: normalized manga catalog and many-to-many genres.
- `catalog.Chapter` and `catalog.ChapterPage`: ordered chapters and their pages.
- `catalog.ComicView`: timestamped, optional-user view events for real 24-hour/weekly/monthly/all-time rankings.
- `catalog.ComicRating`: one 1–10 rating per user and comic.
- `catalog.Bookmark`: one bookmark per user and comic.

Unique constraints prevent duplicate chapters, ratings, and bookmarks. Indexes support slug lookups and time-bounded popularity queries.

## Local setup

1. Install MySQL 8.0.16 or newer (InnoDB, `utf8mb4`) and Redis locally.
2. Copy `.env.example` to `.env` and replace the development secret and database password.
3. Install and start MySQL and Redis locally. Confirm Redis responds to `redis-cli ping`; it must use the URLs configured by `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND`.
4. Install FFmpeg and FFprobe, and make both binaries available on `PATH` or set their absolute paths in `.env` using `FFMPEG_BINARY` and `FFPROBE_BINARY`.
5. Install Python dependencies in the existing virtual environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

6. Apply migrations:

   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate
   .\.venv\Scripts\python.exe manage.py createsuperuser
   ```

7. Start Django and the Celery worker in separate terminals. Windows uses the `solo` worker pool:

   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
   ```

   ```powershell
   .\.venv\Scripts\celery.exe -A config worker --loglevel=INFO --pool=solo
   ```

   On Linux/macOS, start the worker with `celery -A config worker --loglevel=INFO`.

The API is available at `http://127.0.0.1:8000/api/`; Django admin is at `/admin/`. To open the development site from another device on the same Wi-Fi, run the frontend with `npm run dev:lan`, keep Django bound to `127.0.0.1:8000` (the Vite proxy forwards API requests locally), and open `http://<this-computer-LAN-IP>:5173` on that device. Allow inbound access to frontend port 5173 on the local network only if Windows Firewall blocks it; the backend port does not need to be exposed. Uploaded originals and generated HLS files are stored under `MEDIA_ROOT` (by default, the backend's `media/` directory). Uploads default to a 500 MiB maximum; change `VIDEO_MAX_UPLOAD_SIZE` to adjust it.

`collectstatic` writes static assets to `STATIC_ROOT` (default: `BASE_DIR / "staticfiles"`); configure the web server or hosting platform to serve that directory in production.

## PostgreSQL to MySQL migration

The default Django database backend is MySQL, configured with `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_HOST`, and `MYSQL_PORT` in `.env`. Install dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`. `mysqlclient` is the MySQL driver; `psycopg` remains temporarily available to read a PostgreSQL source during data transfer and may be removed from requirements after migration.

The project models use Django's standard fields; there are no PostgreSQL-only `ArrayField`, `HStoreField`, or GIS types. `accounts.User` has a unique `Lower("email")` constraint, which uses a functional unique index: use Oracle MySQL 8.0.13+ (not MariaDB). MySQL 8.0.16+ is recommended so check constraints are enforced. The configured database uses `utf8mb4` and strict SQL mode.

To migrate data without changing or deleting the PostgreSQL source:

1. Back up PostgreSQL. Create an empty MySQL database and user, then set the five `MYSQL_*` variables in `.env`. Temporarily set `POSTGRES_SOURCE_URL` to the PostgreSQL connection URL. The old PostgreSQL `DATABASE_URL` is also recognized as a temporary source fallback, but is never used as the MySQL target.
2. Apply the schema to MySQL:

   ```powershell
   .\.venv\Scripts\python.exe manage.py migrate --database default
   ```

3. Export application data from PostgreSQL (the command writes UTF-8 JSON):

   ```powershell
   .\.venv\Scripts\python.exe manage.py dumpdata --database postgres_source --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude admin.logentry --exclude sessions.session --output postgres-data.json
   ```

4. Load the fixture into MySQL:

   ```powershell
   .\.venv\Scripts\python.exe manage.py loaddata --database default postgres-data.json
   ```

5. Verify record counts and application behavior against MySQL, keep the PostgreSQL backup until verification is complete, then remove `POSTGRES_SOURCE_URL` and the old PostgreSQL `DATABASE_URL` from `.env`. Restart Django and Celery after switching.

`makemigrations` is not needed for a database-engine switch alone; run it only if model definitions were changed. For very large tables, plan a staged ETL instead of a single JSON fixture. PostgreSQL dump files produced by `pg_dump` are not directly loadable by MySQL.

## Google OAuth with django-allauth

Set `GOOGLE_OAUTH_CLIENT_ID` and the newly rotated `GOOGLE_OAUTH_CLIENT_SECRET` in the backend `.env`; do not commit or paste the secret into source files. Set `FRONTEND_URL` to `https://blixtv.uz` in production and `http://127.0.0.1:5173` for local development. Set `CORS_ALLOWED_ORIGINS` to include the exact frontend origin (production: `https://blixtv.uz`; local: `http://127.0.0.1:5173`). Credentialed CORS is enabled for the allauth session exchange. Apply the allauth/Sites migrations with `.\.venv\Scripts\python.exe manage.py migrate`, then restart Django.

The Sites entry at `SITE_ID` is maintained by an accounts data migration from `FRONTEND_URL`; its `domain` field contains only the host and optional port, without `http://` or `https://`. Django Sites entries are not an OAuth allowlist. Google Cloud Console must separately contain the exact backend callback URI under Authorized redirect URIs.

Register these exact authorized redirect URIs in Google Cloud Console:

- `https://api.blixtv.uz/accounts/google/login/callback/`
- `http://127.0.0.1:8000/accounts/google/login/callback/`

The React login and registration pages link to the backend `/accounts/google/login/` endpoint. `SOCIALACCOUNT_LOGIN_ON_GET = True` makes that link redirect directly to Google's consent screen instead of showing allauth's intermediate confirmation page. This opts into initiating social login with GET; allauth's default POST confirmation offers additional protection against login CSRF. After Google verifies the user, Django redirects to `${FRONTEND_URL}/auth/google/callback`; React then calls `GET /api/auth/google/session/` with the Django session cookie and exchanges that authenticated session for the normal JWT access/refresh pair. No token is placed in the redirect URL. The endpoint requires an authenticated Django session and is throttled. Set frontend `VITE_AUTH_BASE_URL` to the backend origin (`https://api.blixtv.uz` in production or `http://127.0.0.1:8000` locally) so the browser uses the correct OAuth and exchange endpoints. The provider credentials are configured with django-allauth's `SOCIALACCOUNT_PROVIDERS["google"]["APPS"]` setting, so a database `SocialApp` entry is not required. Do not also create a Google `SocialApp` unless removing the settings-based provider app; duplicate provider apps can cause `MultipleObjectsReturned`. A Google `invalid_client` response means Google rejected the client credentials during token exchange; changing the Site domain or switching from `APP` to `APPS` does not fix a mismatched client ID/secret.

This config authenticates the Django session. The existing React API uses JWT, so allauth login alone does not populate the frontend's JWT tokens; an explicit secure session-to-JWT exchange is needed if the React app must call JWT-protected API endpoints after Google login.

## Cloudflare R2 media storage

R2 storage is opt-in. Create an R2 bucket and an Object Read & Write token scoped to that bucket. Enable a public `r2.dev` URL for testing or attach a custom domain to the bucket so browsers can fetch HLS playlists and segments. Set the following in the backend `.env`; never commit or share the access keys:

```dotenv
R2_STORAGE_ENABLED=True
R2_S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
R2_BUCKET_NAME=<bucket-name>
R2_ACCESS_KEY_ID=<access-key-id>
R2_SECRET_ACCESS_KEY=<secret-access-key>
R2_PUBLIC_URL=https://<public-r2-domain>
R2_CDN_BASE_URL=https://cdn.blixtv.uz
```

For EU or another jurisdiction-specific bucket, set `R2_S3_ENDPOINT_URL` to that bucket's jurisdiction-specific S3 endpoint. The older `R2_ENDPOINT_URL` name is accepted as a fallback. `R2_PUBLIC_URL` is the public bucket hostname (without a path) used to identify the public R2 origin; `R2_BUCKET_NAME` must name the same bucket. Public API media URLs use `R2_CDN_BASE_URL`, so object keys are served from the custom domain rather than the `r2.dev` hostname. Existing cover URLs whose origin exactly matches `R2_PUBLIC_URL` are rewritten to the custom domain while retaining their object paths. Configure the bucket's CORS policy to allow `GET` and `HEAD` from the deployed frontend origin (and local development origins when needed). For example:

```json
[
  {
    "AllowedOrigins": ["https://<frontend-domain>", "http://localhost:5173", "http://127.0.0.1:5173"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["Content-Length", "Content-Range", "Accept-Ranges", "ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

Video originals, HLS playlists/segments, and R2-backed logo URLs returned by the API use `R2_CDN_BASE_URL`, which is intentionally restricted to `https://cdn.blixtv.uz`. Point that Cloudflare custom domain at the same R2 bucket used by `R2_BUCKET_NAME`.

### Direct browser multipart video uploads

The admin-only multipart API sends no video bytes through Django. It creates a server-generated object key, returns short-lived presigned `PUT` URLs for 8 MiB parts, verifies the completed object's size, creates the existing `Video` record, and queues HLS processing. No model or database migration is needed. R2 must be enabled and configured before using these endpoints:

- `POST /api/videos/multipart/initiate/` with JSON fields `chapter_id`, `filename`, `content_type`, `file_size`, and optional `title`.
- `POST /api/videos/multipart/part-url/` with `upload_token` and `part_number`; call once per part to get a temporary `url`.
- `POST /api/videos/multipart/resume/` with `upload_token` to list chunks already stored in R2 (or detect an upload that already completed).
- `POST /api/videos/multipart/complete/` with `upload_token` and ordered `parts` containing each `{ "PartNumber": 1, "ETag": "\"etag-from-r2\"" }`.
- `POST /api/videos/multipart/abort/` with `upload_token` to cancel an incomplete upload.

All endpoints require an authenticated staff user. The signed upload token is bound to that user, object key, chapter, file size, and upload ID. Presigned URLs expire after 15 minutes; the token expires after 24 hours. Uploads are limited by `VIDEO_MAX_UPLOAD_SIZE` and accept `.mp4` and `.mov` only. The original video is retained in R2; the existing Celery worker creates HLS outputs after the upload finishes. Successful completion returns the new video ID and its `https://cdn.blixtv.uz/...` original URL.

The reusable browser uploader is `../mangalab.uz/frontend_old/r2-multipart-upload.js`. It reads `File.slice()` chunks and uploads each with `XMLHttpRequest` so progress events work without loading the complete file into memory. It can be imported by a React client:

The browser stores only the signed upload token and file metadata in `localStorage`; on return, the user must reselect the same file in the same chapter. The uploader asks R2 which parts are already present and skips them. Closing the page, browser, or using the pause button does not abort the R2 upload. The token is valid for 24 hours; after that, start a new upload. Use **Uploadni butunlay bekor qilish** only when the saved R2 parts should be discarded.

```js
import { uploadVideoMultipart } from "./r2-multipart-upload.js"

const controller = new AbortController()
const result = await uploadVideoMultipart({
  file,
  chapterId,
  title,
  accessToken,
  signal: controller.signal,
  onProgress: ({ percent, phase }) => setUploadProgress({ percent, phase }),
})
console.log(result.cdn_url)
```

For browser-to-R2 `PUT` requests, configure the bucket's CORS policy to allow the exact origin of the page running the uploader. For Django admin, this is the admin site's origin; if the frontend uses a separate upload page, allow that origin too. `Content-Type` is a request header and belongs in `AllowedHeaders`; `ETag` is returned by R2 and belongs in `ExposeHeaders` so JavaScript can read it. For example, replace the origins below with the actual admin and frontend origins (no trailing slash):

```json
[
  {
    "AllowedOrigins": ["https://<django-admin-domain>", "https://<frontend-domain>"],
    "AllowedMethods": ["GET", "HEAD", "PUT"],
    "AllowedHeaders": ["Content-Type", "Authorization", "X-CSRFToken"],
    "ExposeHeaders": ["ETag", "Content-Length", "Content-Range", "Accept-Ranges"],
    "MaxAgeSeconds": 3600
  }
]
```

In Cloudflare Dashboard, open **R2 → bucket → Settings → CORS Policy**, paste the JSON, and save it. The browser sends the video chunks only to the presigned R2 URLs; Django receives JSON metadata and ETags, not the video bytes. Do not expose R2 access keys in the browser.

Configure an R2 lifecycle rule to abort incomplete multipart uploads after one day so abandoned browser sessions do not leave billable parts in the bucket.

On the Django video add page, when R2 is enabled, selecting an `.mp4` or `.mov` enables **R2 ga yuklash**. The button opens a progress dialog with percent and transferred/total MB. The existing Django save button also uses direct upload when a file is selected, preventing the selected file from being POSTed to Django. After completion, the admin redirects to the new video record while Celery generates HLS.

HLS processing downloads the source to temporary disk in 1 MiB reads and transcodes renditions sequentially. FFmpeg decoder, filter, and encoder threads are limited to reduce peak RAM use, and x264 uses the `superfast` preset to reduce CPU time without raising output bitrates; faster presets can reduce compression efficiency and image quality at the same bitrate. When the configured storage overwrites files, HLS output writes avoid a separate existence-check and delete request for every playlist and segment. Celery logs report source-download, probe, per-rendition transcode, output-storage, and total task time to distinguish CPU work from storage/network delays. Transcoding duration still depends on video duration, resolution, and how many renditions are selected.

Objects served through a public bucket domain are publicly retrievable by URL. If uploaded original videos must remain private, use a separate private bucket for originals before enabling public HLS delivery.

### Manhwa cover images

In Django admin open **Catalog → Comics** to create or edit a comic, then use **Upload manhwa logo to Cloudflare R2** to select a PNG, JPG, or WEBP image from your computer. On save, the backend uploads the image to `<comic-slug>/logo/<filename>` in the configured `R2_BUCKET_NAME`, stores its filename on the comic, and the API returns `R2_PUBLIC_URL/<comic-slug>/logo/<filename>`. R2 shows the folder because the actual logo object is uploaded there; no empty folder marker is created. The shared site header image configured as `logo/logo.png` is separate from these per-comic covers.

Deleting a chapter removes its `<comic-slug>/video/chapter-<number>/` media tree, including the uploaded original and all generated HLS renditions. Deleting a comic removes all of its `<comic-slug>/video/` media and `<comic-slug>/logo/` cover objects. Storage deletion errors are surfaced and stop the database deletion instead of silently leaving the comic or chapter deleted with media cleanup incomplete.

This public-read URL flow does not require enabling `R2_STORAGE_ENABLED`; that setting controls backend writes to R2. The `VITE_R2_PUBLIC_MEDIA_URL` frontend setting is the public R2 hostname (for example `https://<public-r2-domain>`) and is used for static R2 assets such as `logo/logo.png`.

Existing full cover URLs are returned unchanged. If a cover is currently stored in the shared `logo/` folder, copy it to `<comic-slug>/logo/` before using the filename-only admin value; this change does not move existing R2 objects.

Before enabling R2, stop Django and the Celery worker. After setting the R2 values and enabling storage, copy existing local media objects before restarting either service:

```powershell
.\.venv\Scripts\python.exe manage.py copy_media_to_r2
```

The command preserves each media object's existing name so database references and relative HLS segment paths keep working. It skips objects already present in R2; pass `--overwrite` only if the local copy should replace them. Once the copy succeeds, restart Django and Celery. New uploads and generated HLS files will then use R2. Leave `R2_STORAGE_ENABLED=False` to continue storing media locally.

## Video upload and HLS processing

- `POST /api/videos/upload/` accepts `multipart/form-data` fields `title`, `original_file`, and required `chapter` (chapter ID).
- The endpoint is restricted to staff/admin users, accepts `.mp4` and `.mov` files within the configured size limit, stores originals at `<comic-slug>/video/chapter-<number>/1080p/<video-name>.<ext>`, and queues `catalog.process_video_hls` in Celery. `<video-name>` is based on the uploaded filename with a unique suffix.
- Staff can upload a video in Django admin at `/admin/catalog/video/add/`: select its comic and chapter, enter a title, use **Upload video from your computer** to choose an `.mp4` or `.mov` file, and save. Admin uploads use the same configured size limit and queue HLS processing automatically. By default, all supported 360p, 480p, and 720p renditions are generated; enable **Faqat bitta formatga o‘girish** below the upload field to generate only the selected HLS format. The uploaded original is kept in either mode. To process a video uploaded manually to R2, enable `R2_STORAGE_ENABLED`, enter its relative object key in **Existing R2 video object key (optional)**, and save.
- The upload response is HTTP 202 with `{"id": <video-id>, "status": "uploading"}`. The video then moves through `processing` to `ready`, or to `error` if FFmpeg fails.
- FFmpeg creates H.264 HLS video renditions with AAC-LC audio at source video resolutions up to 360p, 480p, and 720p. Variants above the source resolution are skipped. For example, the original is stored at `<comic-slug>/video/chapter-1/1080p/episode-<unique-id>.mp4`, the HLS master at `<comic-slug>/video/chapter-1/1080p/episode-<unique-id>.m3u8`, and rendition playlists/segments under `<comic-slug>/video/chapter-1/<quality>/episode-<unique-id>/...`. Master playlists and renditions are scoped to the uploaded video, so videos in the same chapter do not overwrite each other's HLS files.
- After processing reaches `ready` with R2 enabled, any matching duplicate of the source object under the backend's local `MEDIA_ROOT` is removed. The R2 1080p original stays in the bucket for the player's original-quality option; temporary transcoding files are removed automatically.
- The chapter API returns both `hls_playlist` and `original_video` public URLs. The player defaults to adaptive HLS playback and provides a **1080p (Original)** option to stream the untouched uploaded file. The original is served from the same public bucket as HLS, so it is publicly accessible to anyone with its URL.
- Existing video objects keep their current keys and are not moved by this change. Newly uploaded videos use the comic/chapter folder layout above; re-upload older videos to place their objects in that layout.
- `deploy/nginx-hls.conf` is a production example for serving legacy HLS playlists under `MEDIA_ROOT/video/` and `MEDIA_ROOT/videos/hls/`. New keys have no top-level `video/` prefix; adapt production Nginx routing if local storage is used instead of R2.
- Import `VideoPlayer` from `src/VideoPlayer.jsx` and pass the served playlist URL via its `src` prop.

For production Nginx setup, copy/adapt `deploy/nginx-hls.conf` into the server's Nginx configuration, update the filesystem alias, validate it with `nginx -t`, and reload Nginx. The example enables public, non-credentialed CORS for HLS playback; restrict `Access-Control-Allow-Origin` to the frontend origin if media should only be embedded by your site.

## API endpoints

- `GET /api/comics/?search=...` — searchable comics; the search term is limited to 100 characters and processed through Django REST Framework's ORM-backed search filter.
- `GET /api/comics/popular/?period=24h|weekly|monthly|all-time` — top ten ranked from stored view events in the selected period.
- `GET /api/comics/{slug}/` — comic details.
- `GET /api/comics/{slug}/chapters/` — chapters for a comic.
- `GET /api/genres/` — genres.
- `POST /api/auth/register/` — create an account with `username`, `email`, and `password`.
- `POST /api/auth/token/` — obtain JWT tokens with `email` and `password`.
- `POST /api/auth/token/refresh/` — refresh an access token.
- `GET /api/auth/me/` — current authenticated account, including its avatar URL or `null` when no avatar has been uploaded.
- `PATCH /api/auth/me/` — authenticated `multipart/form-data` avatar upload using the `avatar` field; accepts PNG, JPG, or WEBP files up to 5 MiB. Files are stored under `users/<user-id>/avatar/` with a unique filename for each update so public CDN caches do not keep showing an older image. After a successful upload, older avatar objects in that user's avatar folder are deleted. Deleting the user also deletes their current avatar object from the configured media storage, including Cloudflare R2.
- `GET /api/me/bookmarks/` — current user's paginated bookmarks.
- `GET|POST|DELETE /api/comics/{slug}/bookmark/` — check, add, or remove a bookmark.
- `GET|POST|DELETE /api/comics/{slug}/rating/` — read, set/update, or remove a 1–10 rating.
- `POST /api/comics/{slug}/views/` — record a view event (anonymous or authenticated).

Popularity is based on `ComicView` events rather than hard-coded frontend order. Until view events are recorded, counts can be zero and tie-breaking uses newest comics first.
Popular-comics results are cached in Redis for one minute. Successful catalog, genre, chapter, rating, or view-event changes invalidate all popularity periods after the database transaction commits, so the next request rebuilds the cache from current database data.

Authenticated endpoints accept `Authorization: Bearer <access-token>`. Access tokens expire after 15 minutes; refresh tokens expire after 7 days. Registration and login endpoints are rate-limited.
=======
# blixtv
shunchaki web dasturlashni o'rganish uchun
>>>>>>> 64734b09c8eecfc355b04a58dfd3c80728ef768b
