from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_video"),
    ]

    operations = [
        migrations.AlterField(
            model_name="comic",
            name="cover_image",
            field=models.CharField(blank=True, max_length=1000),
        ),
    ]
