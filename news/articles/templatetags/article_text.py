"""Show an article body the way the deep dive sees it: as Markdown, rendered to sanitized HTML."""

from django import template
from django.utils.safestring import mark_safe

from articles import body

register = template.Library()


@register.filter
def article_body(html: str) -> str:
    return mark_safe(body.html(html))  # sanitized by nh3, see articles/body.py
