"""Create the Django Q2 schedule that runs the Miniflux sweep.

Schedules live in the database, so this migration is what "turns on" the sweep: it runs
with `migrate` on every deploy and is idempotent. Values are copied here, not imported
from articles.tasks, so later changes to that module don't rewrite history.
"""

from django.db import migrations

NAME = "sweep-miniflux"


def create_schedule(apps, schema_editor):
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.update_or_create(
        name=NAME,
        defaults={
            "func": "articles.tasks.sweep",
            "schedule_type": "I",  # Schedule.MINUTES
            "minutes": 15,
            "repeats": -1,
        },
    )


def delete_schedule(apps, schema_editor):
    apps.get_model("django_q", "Schedule").objects.filter(name=NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0001_initial"),
        ("django_q", "0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more"),
    ]

    operations = [migrations.RunPython(create_schedule, delete_schedule)]
