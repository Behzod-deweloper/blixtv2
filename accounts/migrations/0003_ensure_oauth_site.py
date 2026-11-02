from urllib.parse import urlsplit

from django.conf import settings
from django.db import migrations


def ensure_oauth_site(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    database = schema_editor.connection.alias
    domain = urlsplit(settings.FRONTEND_URL).netloc
    Site.objects.using(database).update_or_create(
        pk=settings.SITE_ID,
        defaults={"domain": domain, "name": "Blixtv"},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_user_avatar"),
        ("sites", "0002_alter_domain_unique"),
    ]

    operations = [
        migrations.RunPython(ensure_oauth_site, migrations.RunPython.noop),
    ]
