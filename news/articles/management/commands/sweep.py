from django.core.management.base import BaseCommand, CommandError

from articles import tasks
from articles.sources import SourceUnavailable


class Command(BaseCommand):
    help = "Copy new entries from Miniflux into the Article table now (the worker also does this on a schedule)."

    def handle(self, *args, **options):
        try:
            added = tasks.sweep(tasks.get_source())
        except SourceUnavailable as exc:
            # CommandError prints one line to stderr and exits with status 1, without a traceback.
            raise CommandError(f"Miniflux unavailable: {exc}. Check MINIFLUX_URL and MINIFLUX_API_KEY.") from exc
        self.stdout.write(f"Added {added} articles.")
