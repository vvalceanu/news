"""An article's body the way the deep dive sees it (Markdown, see `deep_dive.article_markdown`),
rendered to sanitized HTML for the page, or to plain text for the word analysis."""

from html.parser import HTMLParser

import markdown
import nh3

from .deep_dive import article_markdown

# Formatting only: no links, images or attributes. Feed text is untrusted, and Markdown passes
# raw HTML through (and turns "[x](javascript:...)" into a link), so the sanitizer has the last word.
ALLOWED_TAGS = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "ul", "ol", "li", "blockquote"}
# Each of these starts and ends a paragraph of the plain text (a heading or list item is one).
BLOCK_TAGS = ALLOWED_TAGS - {"br", "strong", "em"}


def html(content: str) -> str:
    rendered = markdown.markdown(article_markdown(content))
    return nh3.clean(rendered, tags=ALLOWED_TAGS, attributes={}, clean_content_tags={"script", "style"})


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.parts.append("\n\n" if tag in BLOCK_TAGS else " " if tag == "br" else "")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data):
        self.parts.append(data)


def text(content: str) -> str:
    """Paragraphs separated by a blank line, like a summary; headings and list items are
    paragraphs of their own, and whitespace inside a paragraph is a single space."""
    parser = _Text()
    parser.feed(html(content))
    parser.close()
    paragraphs = (" ".join(paragraph.split()) for paragraph in "".join(parser.parts).split("\n\n"))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
