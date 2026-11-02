import logging
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.files import File
from django.core.files.storage import default_storage
from django.db.models import Max
from django.templatetags.static import static

from .models import (
    Chapter,
    ChapterPage,
    Comic,
    Genre,
    Video,
    video_chapter_storage_prefix,
)
from .tasks import RENDITIONS

logger = logging.getLogger(__name__)


class ComicSelectWithNextChapter(forms.Select):
    def __init__(self, *args, **kwargs):
        self.next_chapter_numbers = {}
        self.recent_comic_ids = set()
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        next_number = self.next_chapter_numbers.get(str(value), 1 if value else None)
        if next_number is not None:
            option["attrs"]["data-next-chapter-number"] = str(next_number)
        if str(value) in self.recent_comic_ids:
            option["attrs"]["data-recent-comic"] = "true"
        return option


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ["name"]}


class ChapterInline(admin.TabularInline):
    model = Chapter
    fields = ("number", "title", "published_at")
    extra = 0
    show_change_link = True

    class Media:
        js = ("catalog/admin/chapter_form.js",)
        css = {"all": ("catalog/admin/chapter_form.css",)}

    def get_formset(self, request, obj=None, **kwargs):
        if obj and obj.pk:
            last_number = obj.chapters.aggregate(last_number=Max("number"))["last_number"]
            next_number = max(1, int(last_number) + 1) if last_number is not None else 1
            base_form = self.form

            class PrefilledChapterForm(base_form):
                def __init__(self, *args, **form_kwargs):
                    super().__init__(*args, **form_kwargs)
                    if not self.instance.pk and not self.is_bound:
                        self.initial["number"] = next_number

            kwargs["form"] = PrefilledChapterForm

        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "comic" and formfield:
            formfield.label = "Drama"
        return formfield


admin.site.site_title = "Mangalab admin"
admin.site.site_header = "Mangalab boshqaruv paneli"
admin.site.index_title = "Boshqaruv"


@admin.register(Comic)
class ComicAdmin(admin.ModelAdmin):
    class ComicForm(forms.ModelForm):
        logo_upload = forms.FileField(
            label="Cloudflare R2 ga drama logotipini yuklash",
            required=False,
            widget=forms.ClearableFileInput(
                attrs={"accept": ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"}
            ),
            help_text=(
                "PNG, JPG yoki WEBP faylini tanlang. Saqlash vaqtida "
                "<drama-slug>/logo/<filename> ga yuklanadi."
            ),
        )

        class Meta:
            model = Comic
            fields = "__all__"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.order_fields(
                [
                    "title",
                    "slug",
                    "synopsis",
                    "cover_image",
                    "logo_upload",
                    "kind",
                    "status",
                    "release_year",
                    "genres",
                ]
            )

        def clean_logo_upload(self):
            uploaded_file = self.cleaned_data.get("logo_upload")
            if not uploaded_file:
                return uploaded_file
            if not settings.R2_STORAGE_ENABLED:
                raise forms.ValidationError(
                    "Drama logotipini yuklashdan oldin Cloudflare R2 saqlashni yoqing."
                )

            filename = Path(uploaded_file.name).name
            if (
                filename != uploaded_file.name
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", filename)
                or Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}
            ):
                raise forms.ValidationError("Yaroqli nomga ega PNG, JPG yoki WEBP faylini ishlating.")
            return uploaded_file

        def clean_cover_image(self):
            value = self.cleaned_data.get("cover_image", "").strip()
            if self.cleaned_data.get("logo_upload") or self.instance.pk or not value:
                return value

            parsed_value = urlsplit(value)
            parts = PurePosixPath(parsed_value.path).parts
            if (
                parsed_value.scheme in {"http", "https"}
                and parsed_value.hostname in {"localhost", "127.0.0.1"}
                and len(parts) == 4
                and parts[1:3] == ("images", "comic_logo")
            ):
                return parts[-1]
            return value

        def clean(self):
            cleaned_data = super().clean()
            if cleaned_data.get("logo_upload") and not cleaned_data.get("slug"):
                self.add_error("slug", "R2 logotipini yuklashdan oldin drama slugini kiriting.")
            return cleaned_data

    list_display = ["title", "kind", "status", "release_year", "updated_at"]
    list_filter = ["kind", "status", "genres"]
    search_fields = ["title", "slug"]
    prepopulated_fields = {"slug": ["title"]}
    filter_horizontal = ["genres"]
    inlines = [ChapterInline]
    form = ComicForm

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "cover_image" and formfield:
            formfield.help_text = (
                "API ushbu rasmini <drama-slug>/logo/<filename> dan qaytaradi. "
                "Cloudflare R2 ga logotip yuborish uchun quyidagi yuklash maydonidan foydalaning."
            )
        return formfield

    def save_model(self, request, obj, form, change):
        logo_upload = form.cleaned_data.get("logo_upload")
        if logo_upload:
            filename = Path(logo_upload.name).name
            obj.cover_image = filename

        super().save_model(request, obj, form, change)

        if not logo_upload:
            return

        object_key = f"{obj.slug}/logo/{filename}"
        saved_name = default_storage.save(
            object_key,
            File(logo_upload, name=filename),
        )
        if saved_name != object_key:
            default_storage.delete(saved_name)
            raise RuntimeError(
                f"R2 storage changed the expected comic logo object name: {object_key}"
            )


class ChapterPageInline(admin.TabularInline):
    model = ChapterPage
    extra = 0
    verbose_name = "qism sahifasi"
    verbose_name_plural = "qism sahifalari"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "chapter" and formfield:
            formfield.label = "Qism"
        return formfield


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    class ChapterForm(forms.ModelForm):
        comic = forms.ModelChoiceField(
            queryset=Comic.objects.order_by("-created_at", "-pk"),
            label="Drama",
            widget=ComicSelectWithNextChapter(),
        )
        recent_comics_only = forms.BooleanField(
            required=False,
            label="Faqat yangi qo‘shilgan dramalar",
            help_text="Belgilansa, drama tanlash ro‘yxatida oxirgi qo‘shilgan 5 ta drama qoladi.",
        )
        number = forms.IntegerField(
            min_value=1,
            label="Qism raqami",
            widget=forms.NumberInput(attrs={"min": 1, "step": 1}),
        )

        class Meta:
            model = Chapter
            fields = "__all__"

        field_order = [
            "comic",
            "recent_comics_only",
            "number",
            "title",
            "published_at",
        ]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            last_numbers = dict(
                Chapter.objects.order_by()
                .values("comic_id")
                .annotate(last_number=Max("number"))
                .values_list("comic_id", "last_number")
            )
            next_numbers = {
                str(comic_id): max(1, int(last_number) + 1)
                for comic_id, last_number in last_numbers.items()
            }
            self.fields["comic"].widget.next_chapter_numbers = next_numbers
            self.fields["comic"].widget.recent_comic_ids = {
                str(comic_id)
                for comic_id in Comic.objects.order_by("-created_at", "-pk")
                .values_list("pk", flat=True)[:5]
            }
            if not self.is_bound and not self.instance.pk:
                latest_chapter = (
                    Chapter.objects.select_related("comic")
                    .order_by("-created_at", "-pk")
                    .first()
                )
                default_comic_id = (
                    latest_chapter.comic_id
                    if latest_chapter
                    else Comic.objects.order_by("-created_at", "-pk")
                    .values_list("pk", flat=True)
                    .first()
                )
                if default_comic_id:
                    self.initial["comic"] = default_comic_id
                    self.initial["number"] = next_numbers.get(str(default_comic_id), 1)

            self.fields["published_at"].label = "Rejalashtirilgan nashr vaqti"
            self.fields["published_at"].help_text = (
                "Kelajakdagi sana va vaqtni kiritsangiz, qism shu vaqtgacha saytda ko'rinmaydi. "
                "Bo'sh qoldirsangiz, qism darhol e'lon qilinadi. Videoni oldindan biriktirish mumkin."
            )

            if self.instance.pk:
                self.fields["number"] = forms.DecimalField(
                    max_digits=8,
                    decimal_places=2,
                    label="Qism raqami",
                    widget=forms.NumberInput(attrs={"step": 1}),
                )

        class Media:
            js = ("catalog/admin/chapter_form.js",)
            css = {"all": ("catalog/admin/chapter_form.css",)}

    list_display = ["comic", "number", "title", "published_at"]
    list_filter = ["comic"]
    search_fields = ["comic__title", "title"]
    inlines = [ChapterPageInline]
    form = ChapterForm

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        formfield = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "comic" and formfield:
            formfield.label = "Drama"
        return formfield


class ChapterInlineForm(ChapterAdmin.ChapterForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("recent_comics_only", None)


ChapterInline.form = ChapterInlineForm


@admin.register(Video)
class VideoAdmin(admin.ModelAdmin):
    class VideoForm(forms.ModelForm):
        comic = forms.ModelChoiceField(
            queryset=Comic.objects.order_by("-created_at", "-pk"),
            label="Drama",
            widget=ComicSelectWithNextChapter(),
            required=True,
        )
        title = forms.CharField(label="Nom", required=False)
        recent_comics_only = forms.BooleanField(
            required=False,
            label="Faqat yangi qo‘shilgan dramalar",
            help_text="Belgilansa, drama tanlash ro‘yxatida oxirgi qo‘shilgan 5 ta drama qoladi.",
        )
        single_format_only = forms.BooleanField(
            required=False,
            label="Faqat bitta formatga o‘girish",
        )
        output_format = forms.ChoiceField(
            required=False,
            label="Video formati",
            choices=[(str(height), f"{height}p") for height, _bitrate in RENDITIONS],
        )
        original_object_key = forms.CharField(
            label="Mavjud R2 video obyekti kaliti (ixtiyoriy)",
            required=False,
            help_text="Faqat video allaqachon R2-da bo'lsa ishlatilsin. Kompyuterdan video yuklash uchun bo'sh qoldiring.",
        )

        class ChapterSelect(forms.Select):
            def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
                option = super().create_option(name, value, label, selected, index, subindex, attrs)
                comic_id = self.chapter_comic_ids.get(str(value))
                if comic_id:
                    option["attrs"]["data-comic-id"] = comic_id
                    if str(value) in self.latest_chapter_ids:
                        option["attrs"]["data-latest-chapter"] = "true"
                return option

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["chapter"].queryset = Chapter.objects.select_related("comic").order_by(
                "comic__title", "-number"
            )
            self.fields["chapter"].label_from_instance = self.chapter_label
            self.fields["chapter"].required = True
            self.fields["chapter"].label = "Qism"
            self.fields["original_file"] = forms.FileField(
                label="Kompyuterdan video yuklash",
                required=False,
                widget=forms.ClearableFileInput(
                    attrs={
                        "id": "videoInput",
                        "accept": ".mp4,.mov,video/mp4,video/quicktime",
                        "data-r2-multipart-enabled": str(
                            settings.R2_STORAGE_ENABLED
                        ).lower(),
                        "data-r2-module-url": static(
                            "catalog/admin/r2-multipart-upload.js"
                        ),
                    }
                ),
                help_text=(
                    "MP4 yoki MOV faylini tanlang. Asl nusxa saqlanadi; pastdagi tanlovga "
                    "qarab HLS formatlari yaratiladi."
                ),
            )
            chapter_comic_ids = dict(Chapter.objects.values_list("pk", "comic_id"))
            self.fields["chapter"].widget = self.ChapterSelect()
            self.fields["chapter"].widget.choices = self.fields["chapter"].choices
            self.fields["chapter"].widget.chapter_comic_ids = {
                str(chapter_id): comic_id for chapter_id, comic_id in chapter_comic_ids.items()
            }
            latest_chapter_ids = set()
            comics_with_latest_chapter = set()
            for chapter_id, comic_id in Chapter.objects.order_by(
                "comic_id", "-created_at", "-pk"
            ).values_list("pk", "comic_id"):
                if comic_id not in comics_with_latest_chapter:
                    comics_with_latest_chapter.add(comic_id)
                    latest_chapter_ids.add(str(chapter_id))
            self.fields["chapter"].widget.latest_chapter_ids = latest_chapter_ids
            if self.instance.pk and self.instance.chapter_id:
                self.fields["comic"].initial = self.instance.chapter.comic_id
            elif not self.instance.pk and not self.is_bound:
                latest_chapter = (
                    Chapter.objects.select_related("comic")
                    .order_by("-created_at", "-pk")
                    .first()
                )
                default_comic_id = (
                    latest_chapter.comic_id
                    if latest_chapter
                    else Comic.objects.order_by("-created_at", "-pk")
                    .values_list("pk", flat=True)
                    .first()
                )
                if default_comic_id:
                    self.initial["comic"] = default_comic_id
                if latest_chapter:
                    self.initial["chapter"] = latest_chapter.pk

            self.fields["comic"].widget.recent_comic_ids = {
                str(comic_id)
                for comic_id in Comic.objects.order_by("-created_at", "-pk")
                .values_list("pk", flat=True)[:5]
            }
            self.order_fields(
                [
                    "comic",
                    "recent_comics_only",
                    "chapter",
                    "title",
                    "original_file",
                    "single_format_only",
                    "output_format",
                    "original_object_key",
                    "hls_playlist",
                    "created_at",
                    "status",
                ]
            )

        @staticmethod
        def chapter_label(chapter):
            number = format(chapter.number.normalize(), "f")
            return f"{number}-qism"

        class Meta:
            model = Video
            fields = "__all__"

        class Media:
            js = (
                "catalog/admin/video_form_options.js",
                "catalog/admin/video_form_multipart.js",
            )
            css = {"all": ("catalog/admin/chapter_form.css",)}

        def clean(self):
            cleaned_data = super().clean()
            comic = cleaned_data.get("comic")
            chapter = cleaned_data.get("chapter")
            uploaded_file = cleaned_data.get("original_file") or self.files.get("original_file")
            if uploaded_file:
                cleaned_data["original_file"] = uploaded_file
            object_key = cleaned_data.get("original_object_key")
            if (
                cleaned_data.get("single_format_only")
                and not cleaned_data.get("output_format")
            ):
                self.add_error("output_format", "O‘girish uchun video formatini tanlang.")
            if comic and chapter and chapter.comic_id != comic.pk:
                self.add_error("chapter", "Tanlangan dramaga tegishli qismni tanlang.")
            if object_key and comic and chapter and chapter.comic_id == comic.pk:
                expected_prefix = f"{video_chapter_storage_prefix(chapter)}/1080p/"
                if not object_key.startswith(expected_prefix):
                    self.add_error(
                        "original_object_key",
                        f"R2 obyekt kalitini {expected_prefix} ostidan kiriting.",
                    )
            if uploaded_file and object_key:
                self.add_error(
                    "original_object_key",
                    "Fayl va mavjud R2 obyekt kalitini bir vaqtda tanlamang.",
                )
            if not self.instance.pk and not uploaded_file and not object_key:
                self.add_error(
                    "original_file",
                    "Video yuklang yoki mavjud R2 obyekt kalitini kiriting.",
                )
            return cleaned_data

        def clean_original_object_key(self):
            value = self.cleaned_data.get("original_object_key", "").strip()
            if not value:
                return ""
            if not settings.R2_STORAGE_ENABLED:
                raise forms.ValidationError(
                    "Mavjud R2 video bilan ishlashdan oldin backend sozlamalarida R2 saqlashni yoqing."
                )
            parsed_value = urlsplit(value)
            if (
                "\\" in value
                or value.startswith("/")
                or parsed_value.scheme
                or parsed_value.netloc
            ):
                raise forms.ValidationError(
                    "To'liq URL yoki fayl manzili emas, R2 obyekt kalitini kiriting."
                )
            parts = PurePosixPath(value).parts
            if (
                not parts
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise forms.ValidationError("Yaroqli nisbiy R2 obyekt kalitini kiriting.")
            if Path(parts[-1]).suffix.lower() not in {".mp4", ".mov"}:
                raise forms.ValidationError("Faqat .mp4 va .mov video fayllari ruxsat etiladi.")
            return PurePosixPath(*parts).as_posix()

        def clean_original_file(self):
            uploaded_file = self.cleaned_data.get("original_file")
            if uploaded_file is None:
                return uploaded_file

            if Path(uploaded_file.name).suffix.lower() not in {".mp4", ".mov"}:
                raise forms.ValidationError("Faqat .mp4 va .mov video fayllari ruxsat etiladi.")
            if uploaded_file.size > settings.VIDEO_MAX_UPLOAD_SIZE:
                max_size_mib = settings.VIDEO_MAX_UPLOAD_SIZE // (1024 * 1024)
                raise forms.ValidationError(f"Video fayl {max_size_mib} MiB dan oshmasligi kerak.")
            return uploaded_file

    form = VideoForm
    fields = (
        "comic",
        "recent_comics_only",
        "chapter",
        "title",
        "original_file",
        "single_format_only",
        "output_format",
        "original_object_key",
        "hls_playlist",
        "created_at",
        "status",
    )
    list_display = ["title", "chapter", "status", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["title", "chapter__title", "chapter__comic__title"]
    readonly_fields = ["hls_playlist", "created_at", "status"]

    def save_model(self, request, obj, form, change):
        original_object_key = form.cleaned_data.get("original_object_key")
        should_process = (
            not change
            or "original_file" in form.changed_data
            or bool(original_object_key)
        )
        if original_object_key:
            obj.original_file.name = original_object_key
        if should_process:
            obj.status = Video.Status.UPLOADING
        super().save_model(request, obj, form, change)

        if not should_process:
            return

        from .tasks import process_video_hls

        try:
            output_height = (
                int(form.cleaned_data["output_format"])
                if form.cleaned_data.get("single_format_only")
                else None
            )
            if output_height is None:
                process_video_hls.delay(obj.pk)
            else:
                process_video_hls.delay(obj.pk, output_height)
        except Exception:
            logger.exception("Could not enqueue HLS processing for video %s.", obj.pk)
            obj.status = Video.Status.ERROR
            obj.save(update_fields=["status"])
            self.message_user(
                request,
                "Video was saved, but HLS processing could not be queued.",
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                "Video was saved and HLS processing was queued.",
                level=messages.SUCCESS,
            )
