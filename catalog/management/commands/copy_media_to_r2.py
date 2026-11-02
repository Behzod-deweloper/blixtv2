from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Copy existing local media files to the configured Cloudflare R2 bucket."

    def add_arguments(self, parser):
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace R2 objects when the same object name already exists.",
        )

    def handle(self, *args, **options):
        if not settings.R2_STORAGE_ENABLED:
            raise CommandError("Enable and configure R2_STORAGE_ENABLED before copying media.")

        media_root = Path(settings.MEDIA_ROOT).resolve()
        if not media_root.is_dir():
            self.stdout.write(self.style.WARNING(f"No local media directory found at {media_root}."))
            return

        local_storage = FileSystemStorage(location=media_root)
        copied = 0
        skipped = 0
        for local_path in sorted(media_root.rglob("*")):
            if not local_path.is_file():
                continue
            try:
                relative_path = local_path.resolve().relative_to(media_root).as_posix()
            except ValueError as error:
                raise CommandError(f"Media file resolves outside MEDIA_ROOT: {local_path.name}") from error

            if default_storage.exists(relative_path):
                if not options["overwrite"]:
                    skipped += 1
                    continue
                default_storage.delete(relative_path)

            with local_storage.open(relative_path, "rb") as source_file:
                saved_name = default_storage.save(relative_path, File(source_file))
            if saved_name != relative_path:
                raise CommandError(f"Storage changed the expected media object name: {relative_path}")
            copied += 1
            self.stdout.write(f"Copied {relative_path}")

        self.stdout.write(self.style.SUCCESS(f"R2 media copy complete: {copied} copied, {skipped} skipped."))
