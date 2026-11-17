from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from dictionary import wiktionary
from dictionary.models import Entry, Redirect

BATCH = 5000


class Command(BaseCommand):
    help = (
        "Replace the dictionary with the German Wiktionary entries in FILE (from fetch_wiktionary). "
        "Runs in one transaction: pages keep showing the old dictionary until it's done, and a bad "
        "file leaves it as it was. To load Railway's database, see the README."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path)

    def handle(self, *args, file, **options):
        if not file.is_file():
            raise CommandError(f"No file {file}.")
        counts = {Entry: 0, Redirect: 0}
        pending = {Entry: [], Redirect: []}

        def flush(model):
            model.objects.bulk_create(pending[model])
            counts[model] += len(pending[model])
            pending[model].clear()

        with transaction.atomic():
            Redirect.objects.all().delete()
            Entry.objects.all().delete()
            for data in wiktionary.read_entries(file):
                entry, redirects = wiktionary.rows(data)
                if entry:
                    pending[Entry].append(entry)
                pending[Redirect].extend(redirects)
                for model in (Entry, Redirect):
                    if len(pending[model]) >= BATCH:
                        flush(model)
            flush(Entry)
            flush(Redirect)
            if not counts[Entry]:
                # Raising rolls back the transaction, so the old dictionary stays.
                raise CommandError(f"No German entries in {file}; the dictionary is unchanged.")
        self.stdout.write(f"Imported {counts[Entry]} entries and {counts[Redirect]} redirects.")
