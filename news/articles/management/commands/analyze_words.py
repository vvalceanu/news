from django.core.management.base import BaseCommand, CommandError

from articles import tasks
from articles.models import Article


class Command(BaseCommand):
    help = (
        "Split articles' German summaries into words with lemmas (spaCy + simplemma, in a "
        "subprocess). No API calls, so it's free to rerun. Overwrites the stored words."
    )

    def add_arguments(self, parser):
        parser.add_argument("article_ids", type=int, nargs="+")

    def handle(self, *args, article_ids, **options):
        failed = []
        for article_id in article_ids:
            if not Article.objects.filter(pk=article_id).exists():
                raise CommandError(f"No article {article_id}.")
            if not tasks.analyze_words(article_id):
                failed.append(article_id)
                self.stdout.write(f"Article {article_id}: not analyzed (no summaries, or the analysis failed; see the log)")
                continue
            article = Article.objects.get(pk=article_id)
            for level in ("a2", "b1"):
                words = getattr(article, f"words_{level}")
                lemmas = [w for w in words if w["lemma"]]
                unknown = sorted({w["lemma"] for w in lemmas if not w["known"]})
                joined = sorted({w["lemma"] for w in lemmas if w["verb"] is not None})
                self.stdout.write(
                    f"Article {article_id} {level.upper()}: {len(lemmas)} words\n"
                    f"  separable verbs: {', '.join(joined) or '-'}\n"
                    f"  not in the dictionary: {', '.join(unknown) or '-'}"
                )
        if failed:
            raise CommandError(f"Not analyzed: {', '.join(map(str, failed))}")
