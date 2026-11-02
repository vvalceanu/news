"""Point the sweep schedule at run_sweep, which also queues title translation after each sweep."""

from django.db import migrations

NAME = "sweep-miniflux"


def forwards(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).update(func="articles.tasks.run_sweep")


def backwards(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).update(func="articles.tasks.sweep")


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0003_title_en"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
