"""Show an article body the way the deep dive sees it: as Markdown, rendered to sanitized HTML."""

import markdown
import nh3
from django import template
from django.utils.safestring import mark_safe

from articles.deep_dive import article_markdown

register = template.Library()

# Formatting only: no links, images or attributes. Feed text is untrusted, and Markdown passes
# raw HTML through (and turns "[x](javascript:...)" into a link), so the sanitizer has the last word.
ALLOWED_TAGS = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "ul", "ol", "li", "blockquote"}


@register.filter
def article_body(html: str) -> str:
    rendered = markdown.markdown(article_markdown(html))
    return mark_safe(nh3.clean(rendered, tags=ALLOWED_TAGS, attributes={}, clean_content_tags={"script", "style"}))
