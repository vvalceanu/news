// The reader (templates/articles/_reader.html): click a German word and its details replace the
// English translation beside it; × or Esc brings the English back. The details come from the
// JSON the page embeds (articles/reading.py) and are put in with textContent only, so nothing
// from the dictionary can inject markup.
(() => {
  "use strict";

  const narrow = window.matchMedia("(max-width: 40rem)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function capitalized(text) {
    return text ? text[0].toUpperCase() + text.slice(1) : text;
  }

  function entryNode(entry, match) {
    const section = el("section", null, "word-entry");
    const head = el("h4");
    head.lang = "de";
    head.textContent = entry.article ? `${entry.article} ${entry.word}` : entry.word;
    section.append(head);

    const facts = [entry.class];
    if (entry.plural) facts.push(`plural die ${entry.plural}`);
    if (entry.swiss) facts.push("Swiss");
    section.append(el("p", facts.join(", "), "word-facts"));
    if (match.via) section.append(el("p", capitalized(match.via), "word-via"));

    const meanings = el("ol", null, "word-meanings");
    for (const meaning of entry.meanings) meanings.append(el("li", meaning));
    section.append(meanings);
    return section;
  }

  function detailsNodes(word, entries) {
    const nodes = [];
    const title = el("h3", word.text, "word-title");
    title.lang = "de";
    title.tabIndex = -1;
    nodes.push(title);

    if (word.lemma && word.lemma !== word.text) {
      const lemma = el("p", "Dictionary form: ", "word-lemma");
      const form = el("span", word.lemma);
      form.lang = "de";
      lemma.append(form);
      nodes.push(lemma);
    }
    const kind = word.separable ? "separable verb" : word.pos;
    const grammar = [kind, word.grammar].filter(Boolean).join(", ");
    if (grammar) nodes.push(el("p", capitalized(grammar), "word-grammar"));

    if (word.matches.length) {
      for (const match of word.matches) nodes.push(entryNode(entries[match.entry], match));
    } else {
      nodes.push(el("p", "Not in the dictionary.", "word-missing"));
    }
    return nodes;
  }

  function setUp(reader) {
    const data = JSON.parse(document.getElementById(reader.dataset.details).textContent);
    const german = reader.querySelector(".summary-de");
    const panel = reader.querySelector(".word-panel");
    const body = panel.querySelector(".word-panel-body");
    const words = german.querySelectorAll(".w");
    let opener = null;

    for (const word of words) {
      word.setAttribute("role", "button");
      word.tabIndex = 0;
      word.setAttribute("aria-expanded", "false");
    }

    function mark(parts) {
      for (const word of words) word.classList.remove("is-selected");
      for (const i of parts) german.querySelector(`.w[data-i="${i}"]`)?.classList.add("is-selected");
    }

    function open(word) {
      let details = data.words[word.dataset.i];
      if (details && details.see !== undefined) details = data.words[details.see];
      if (!details) return;
      if (opener) opener.setAttribute("aria-expanded", "false");
      opener = word;
      word.setAttribute("aria-expanded", "true");
      mark(details.parts);
      body.replaceChildren(...detailsNodes(details, data.entries));
      panel.hidden = false;
      panel.scrollTop = 0;
      reader.classList.add("is-open");
      if (narrow.matches) {
        // The panel is a sheet over the bottom of the screen: keep the word above it.
        word.scrollIntoView({ block: "nearest", behavior: reducedMotion.matches ? "auto" : "smooth" });
      }
      body.querySelector(".word-title").focus({ preventScroll: true });
    }

    function close() {
      if (panel.hidden) return;
      panel.hidden = true;
      reader.classList.remove("is-open");
      mark([]);
      if (opener) {
        opener.setAttribute("aria-expanded", "false");
        if (opener.isConnected) opener.focus({ preventScroll: true });
        opener = null;
      }
    }

    german.addEventListener("click", (event) => {
      const word = event.target.closest(".w");
      if (!word) return;
      if (word === opener && !panel.hidden) close();
      else open(word);
    });
    german.addEventListener("keydown", (event) => {
      const word = event.target.closest(".w");
      if (word && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        open(word);
      }
    });
    reader.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
    panel.querySelector(".word-panel-close").addEventListener("click", close);
  }

  for (const reader of document.querySelectorAll(".reader")) setUp(reader);
})();
