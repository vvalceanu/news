from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from articles import tasks
from articles.models import Article


class Command(BaseCommand):
    help = "Run the deep dive (B1 and A2 summaries with Sonnet) for one article now, in this process."

    def add_arguments(self, parser):
        parser.add_argument("article_id", type=int)
        parser.add_argument(
            "--force", action="store_true", help="Run again even if it already ran; overwrites the summaries."
        )

    def handle(self, *args, article_id, force, **options):
        try:
            article = Article.objects.get(pk=article_id)
        except Article.DoesNotExist as exc:
            raise CommandError(f"No article {article_id}.") from exc
        if article.deep_dive_status and not force:
            raise CommandError(f"Article {article_id} is already {article.deep_dive_status}; use --force to redo it.")
        Article.objects.filter(pk=article_id).update(
            deep_dive_status=Article.DeepDive.QUEUED, deep_dive_requested_at=timezone.now()
        )
        try:
            ran = tasks.deep_dive(article_id)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        if not ran:
            raise CommandError("Not run: today's spending cap is reached.")
        article.refresh_from_db()
        for level in ("b1", "a2"):
            self.stdout.write(f"--- {level.upper()} ---\n{getattr(article, f'summary_{level}') or '(failed)'}\n")
        self.stdout.write(f"Total deep dive cost so far: ${article.deep_dive_cost_usd:.4f}")
