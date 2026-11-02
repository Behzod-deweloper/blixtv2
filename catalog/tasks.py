import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

from botocore.exceptions import ClientError
from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage

from .models import Video
from .media_cleanup import delete_storage_tree as _delete_storage_tree


logger = logging.getLogger(__name__)
RENDITIONS = (
    (360, 800_000),
    (480, 1_400_000),
    (720, 2_800_000),
)
AUDIO_BITRATE = 128_000


def _probe_video(video_path):
    result = subprocess.run(
        [
            settings.FFPROBE_BINARY,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise ValueError("Uploaded file does not contain a readable video stream.")
    return streams[0]["width"], streams[0]["height"]


def _transcode_rendition(input_path, rendition_dir, height, bitrate):
    rendition_dir.mkdir(parents=True, exist_ok=True)
    output_playlist = rendition_dir / "index.m3u8"
    subprocess.run(
        [
            settings.FFMPEG_BINARY,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-threads:v",
            "1",
            "-i",
            str(input_path),
            "-filter_threads",
            "1",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale=-2:min({height}\\,ih)",
            "-c:v",
            "libx264",
            "-threads:v",
            "1",
            "-preset",
            "superfast",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(int(bitrate * 1.07)),
            "-bufsize",
            str(bitrate * 2),
            "-force_key_frames",
            "expr:gte(t,n_forced*6)",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            str(AUDIO_BITRATE),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-muxpreload",
            "0",
            "-muxdelay",
            "0",
            "-hls_time",
            "6",
            "-hls_playlist_type",
            "vod",
            "-hls_flags",
            "independent_segments",
            "-hls_segment_filename",
            str(rendition_dir / "segment_%05d.ts"),
            "-f",
            "hls",
            str(output_playlist),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=settings.VIDEO_TRANSCODE_TIMEOUT_SECONDS,
    )


def _write_master_playlist(
    output_dir,
    source_width,
    source_height,
    renditions,
    rendition_paths=None,
    playlist_name="master.m3u8",
):
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for height, bitrate in renditions:
        output_height = min(height, source_height)
        output_width = max(2, int(source_width * output_height / source_height) // 2 * 2)
        bandwidth = int(bitrate * 1.07) + AUDIO_BITRATE
        average_bandwidth = bitrate + AUDIO_BITRATE
        playlist_path = rendition_paths[height] if rendition_paths else f"{height}p/index.m3u8"
        lines.extend(
            [
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},AVERAGE-BANDWIDTH={average_bandwidth},RESOLUTION={output_width}x{output_height}",
                playlist_path,
            ]
        )
    playlist_path = output_dir / playlist_name
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _delete_hls_outputs(video_prefix, extra_heights=(), video_stem=None):
    heights = {height for height, _bitrate in RENDITIONS}
    heights.update(extra_heights)
    for height in heights:
        rendition_prefix = PurePosixPath(video_prefix) / f"{height}p"
        if video_stem:
            rendition_prefix /= video_stem
        _delete_storage_tree(rendition_prefix.as_posix())
    master_name = (
        PurePosixPath(video_prefix) / "1080p" / f"{video_stem}.m3u8"
        if video_stem
        else PurePosixPath(video_prefix) / "master.m3u8"
    )
    default_storage.delete(master_name.as_posix())


def _store_hls_output(output_dir, storage_prefix):
    for local_path in sorted(output_dir.rglob("*")):
        if not local_path.is_file():
            continue

        relative_path = local_path.relative_to(output_dir).as_posix()
        storage_name = str(PurePosixPath(storage_prefix) / relative_path)
        if not getattr(default_storage, "file_overwrite", False):
            if default_storage.exists(storage_name):
                default_storage.delete(storage_name)
        with local_path.open("rb") as file_obj:
            saved_name = default_storage.save(storage_name, File(file_obj))
        if saved_name != storage_name:
            raise RuntimeError(f"Storage changed the expected HLS object name: {storage_name}")


def _delete_local_media_copy_of_r2_source(source_key):
    if not settings.R2_STORAGE_ENABLED:
        return False

    media_root = Path(settings.MEDIA_ROOT).resolve()
    local_source = (media_root / Path(*PurePosixPath(source_key).parts)).resolve()
    try:
        local_source.relative_to(media_root)
    except ValueError as error:
        raise ValueError("Video source path resolves outside MEDIA_ROOT.") from error

    if not local_source.is_file():
        return False
    local_source.unlink()
    return True


def _cleanup_local_video_copy_after_ready(video):
    try:
        if _delete_local_media_copy_of_r2_source(video.original_file.name):
            logger.info("Deleted local media copy of R2 source video %s.", video.pk)
    except OSError:
        logger.exception(
            "Video %s is ready in R2, but its local media source copy could not be deleted.",
            video.pk,
        )


@shared_task(name="catalog.process_video_hls")
def process_video_hls(video_id, output_height=None):
    task_started = time.perf_counter()
    allowed_heights = {height for height, _bitrate in RENDITIONS}
    if output_height is not None and output_height not in allowed_heights:
        raise ValueError(f"Unsupported video output height: {output_height}")

    video = Video.objects.get(pk=video_id)
    video.status = Video.Status.PROCESSING
    video.save(update_fields=["status"])

    output_relative_dir = None
    output_upload_started = False
    extra_heights = ()
    chapter_layout_stem = None
    try:
        original_key = PurePosixPath(video.original_file.name)
        video_stem = original_key.stem
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", video_stem):
            raise ValueError("Stored video filename has an invalid name.")
        if (
            len(original_key.parts) == 5
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,259}", original_key.parts[0])
            and original_key.parts[1] == "video"
            and re.fullmatch(r"chapter-(?:[0-9]+(?:\.[0-9]+)?|unknown)", original_key.parts[2])
            and original_key.parts[3] == "1080p"
            and original_key.suffix.lower() in {".mp4", ".mov"}
        ):
            output_relative_dir = PurePosixPath(*original_key.parts[:3])
            chapter_layout_stem = video_stem
        elif (
            len(original_key.parts) == 3
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,132}", original_key.parts[0])
            and original_key.parts[1] == "1080p"
            and re.fullmatch(r"original\.(mp4|mov)", original_key.parts[2], re.IGNORECASE)
        ):
            output_relative_dir = PurePosixPath(original_key.parts[0])
        elif (
            len(original_key.parts) == 3
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,259}", original_key.parts[0])
            and re.fullmatch(r"chapter-?[0-9]+(?:\.[0-9]+)?", original_key.parts[1])
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.(mp4|mov)", original_key.parts[2], re.IGNORECASE)
        ):
            output_relative_dir = original_key.parent / video_stem
        elif (
            len(original_key.parts) == 1
            and original_key.suffix.lower() in {".mp4", ".mov"}
        ):
            output_relative_dir = PurePosixPath(video_stem)
        elif (
            len(original_key.parts) == 4
            and original_key.parts[0] == "video"
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,259}", original_key.parts[1])
            and re.fullmatch(r"chapter--?[0-9]+(?:\.[0-9]+)?", original_key.parts[2])
        ):
            output_relative_dir = original_key.parent / video_stem
        elif original_key.parts and original_key.parts[0] == "video":
            if len(original_key.parts) != 2:
                raise ValueError("R2 source videos must be stored directly under video/.")
            output_relative_dir = PurePosixPath("video") / video_stem
        elif original_key.parts[:2] == ("videos", "originals") and len(original_key.parts) == 3:
            output_relative_dir = PurePosixPath("videos") / "hls" / video_stem
        else:
            raise ValueError("Stored video key has an unsupported path.")

        with tempfile.TemporaryDirectory(prefix="mangalab-hls-") as temp_dir:
            work_dir = Path(temp_dir)
            original_path = work_dir / f"original{original_key.suffix.lower()}"
            download_started = time.perf_counter()
            source_bytes = 0
            with default_storage.open(original_key.as_posix(), "rb") as source_file:
                with original_path.open("wb") as local_file:
                    while chunk := source_file.read(1024 * 1024):
                        local_file.write(chunk)
                        source_bytes += len(chunk)
            logger.info(
                "Downloaded HLS source for video %s: %s bytes in %.2f seconds.",
                video.pk,
                source_bytes,
                time.perf_counter() - download_started,
            )

            probe_started = time.perf_counter()
            source_width, source_height = _probe_video(original_path)
            logger.info(
                "Probed HLS source for video %s (%sx%s) in %.2f seconds.",
                video.pk,
                source_width,
                source_height,
                time.perf_counter() - probe_started,
            )
            available_renditions = (
                tuple(rendition for rendition in RENDITIONS if rendition[0] == output_height)
                if output_height is not None
                else tuple(rendition for rendition in RENDITIONS if rendition[0] <= source_height)
            )
            if not available_renditions:
                _delete_hls_outputs(
                    output_relative_dir.as_posix(),
                    (source_height,),
                    chapter_layout_stem,
                )
                video.hls_playlist.name = ""
                video.status = Video.Status.READY
                video.save(update_fields=["hls_playlist", "status"])
                _cleanup_local_video_copy_after_ready(video)
                return video.pk

            output_dir = work_dir / "hls"
            output_dir.mkdir()
            comic_slug = video.chapter.comic.slug if video.chapter_id else "unassigned"
            chapter_number = (
                format(video.chapter.number.normalize(), "f")
                if video.chapter_id
                else "unknown"
            )
            if chapter_layout_stem:
                rendition_paths = {
                    height: f"../{height}p/{chapter_layout_stem}/index.m3u8"
                    for height, _bitrate in available_renditions
                }
                master_playlist_name = f"1080p/{chapter_layout_stem}.m3u8"
            else:
                chapter_path = PurePosixPath(comic_slug) / f"chapter-{chapter_number}"
                rendition_paths = {
                    height: f"{height}p/{chapter_path.as_posix()}/index.m3u8"
                    for height, _bitrate in available_renditions
                }
                master_playlist_name = "master.m3u8"
            for height, bitrate in available_renditions:
                rendition_dir = (
                    output_dir / f"{height}p" / chapter_layout_stem
                    if chapter_layout_stem
                    else output_dir / f"{height}p" / Path(*chapter_path.parts)
                )
                transcode_started = time.perf_counter()
                _transcode_rendition(original_path, rendition_dir, height, bitrate)
                logger.info(
                    "Transcoded video %s to %sp in %.2f seconds.",
                    video.pk,
                    height,
                    time.perf_counter() - transcode_started,
                )

            _write_master_playlist(
                output_dir,
                source_width,
                source_height,
                available_renditions,
                rendition_paths,
                master_playlist_name,
            )
            _delete_hls_outputs(
                output_relative_dir.as_posix(),
                extra_heights,
                chapter_layout_stem,
            )
            output_upload_started = True
            storage_started = time.perf_counter()
            _store_hls_output(output_dir, output_relative_dir.as_posix())
            logger.info(
                "Stored HLS outputs for video %s in %.2f seconds.",
                video.pk,
                time.perf_counter() - storage_started,
            )

        playlist_name = (
            output_relative_dir / "1080p" / f"{chapter_layout_stem}.m3u8"
            if chapter_layout_stem
            else output_relative_dir / "master.m3u8"
        ).as_posix()
        video.hls_playlist.name = playlist_name
        video.status = Video.Status.READY
        video.save(update_fields=["hls_playlist", "status"])
        _cleanup_local_video_copy_after_ready(video)
        logger.info(
            "Finished HLS processing for video %s in %.2f seconds.",
            video.pk,
            time.perf_counter() - task_started,
        )
        return video.pk
    except Exception:
        video.status = Video.Status.ERROR
        video.save(update_fields=["status"])
        if output_upload_started and output_relative_dir is not None:
            _delete_hls_outputs(
                output_relative_dir.as_posix(),
                extra_heights,
                chapter_layout_stem,
            )
        logger.exception("HLS processing failed for video %s.", video.pk)
        raise
