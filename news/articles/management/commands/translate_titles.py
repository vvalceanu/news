from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from articles import tasks


class Command(BaseCommand):
    help = "Translate untranslated article titles with Haiku now (the worker also does this after each sweep)."

    def handle(self, *args, **options):
        try:
            translated = tasks.translate_titles(tasks.anthropic_client())
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Translated {translated} titles.")
