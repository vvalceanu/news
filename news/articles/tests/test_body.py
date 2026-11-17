from articles import body


def test_text_is_paragraphs_without_markup():
    html = (
        "<h2>Neue  Brücke</h2><p>Die <a href='https://x.test'>Brücke</a> ist <b>offen</b>,<br>sagt die\n"
        "Stadt.</p><ul><li>Velo</li><li>Bus &amp; Tram</li></ul><blockquote><p>Endlich!</p></blockquote>"
    )
    assert body.text(html) == "Neue Brücke\n\nDie Brücke ist offen, sagt die Stadt.\n\nVelo\n\nBus & Tram\n\nEndlich!"


def test_text_has_what_the_sanitized_page_shows_and_nothing_else():
    # Markdown passes "<img src=x>" through as raw HTML, so the sanitizer drops it, here as on the page.
    html = "<script>alert(1)</script><p>&lt;img src=x&gt; und [klick](javascript:alert(1)) &lt; 3 &amp;</p>"
    assert body.text(html) == "und klick < 3 &"  # entities decoded; the page escapes the text again


def test_text_of_an_empty_body_is_empty():
    assert body.text("") == ""
    assert body.text("<p> </p><img src='x.png'>") == ""
