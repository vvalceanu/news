from pathlib import Path

from django.core.management.base import BaseCommand

from dictionary import wiktionary


class Command(BaseCommand):
    help = (
        "Download kaikki.org's Wiktionary dump (about 3 GB, streamed, never stored) and keep the German "
        "entries in FILE (about 70 MB gzipped). Takes about 4 minutes. Run it on your machine, not on Railway; "
        "then load FILE with import_wiktionary."
    )

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path, help="Where to write the German entries, e.g. ../data/wiktionary-de.jsonl.gz")
        parser.add_argument("--url", default=wiktionary.RAW_DUMP_URL)

    def handle(self, *args, file, url, **options):
        self.stdout.write(f"Reading {url} …")
        read, kept = wiktionary.fetch_german(url, file)
        self.stdout.write(f"Kept {kept} German entries of {read} in {file}.")
