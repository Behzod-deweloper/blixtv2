import catalog.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_comic_cover_object_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="video",
            name="original_file",
            field=models.FileField(upload_to=catalog.models.video_original_upload_to),
        ),
        migrations.AlterField(
            model_name="video",
            name="hls_playlist",
            field=models.FileField(blank=True, null=True, upload_to="video/"),
        ),
    ]
