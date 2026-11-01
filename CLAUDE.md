# CLAUDE.md

## Docs in two languages

- `README.md` is the source; `README_ru.md` is its translation. Change both in the same edit.

## Diagrams

- `docs/diagrams/<name>.svg` (English) is the source of truth. `<name>_ru.svg` is its
  translation for `README_ru.md`. Never edit an `_ru.svg` by hand: it is rebuilt from the
  English file and hand edits are lost.
- To change a diagram: edit the English SVG, add every new or changed string to `T` in
  `docs/diagrams/tools/translate_ru.py` (or to `KEEP_ALL` if it stays in English), then run
  `python3 docs/diagrams/tools/translate_ru.py`. The script fails and names any string it
  cannot translate.
- `example.svg` and `example_ru.svg` are generated: edit the rows and strings in
  `docs/diagrams/tools/example.py` (English first, then Russian), then run it.
- Russian text is longer. After a rebuild, render both files in light and dark mode
  (headless Chrome `--screenshot`; dark with `--force-dark-mode
  --blink-settings=preferredColorScheme=0`) and fix overflows with a `TWEAKS` entry, not by
  editing the `_ru.svg`.
- Stays in English in Russian diagrams and Mermaid: ticket statuses and state names, branch
  names, URLs, intents (`approve`, `merge`, `prepare_release`), product and tool names.
- Each diagram has its Mermaid source in a `<details>` block under it. The SVG shows the same
  data but is laid out by hand, not exported from Mermaid. Mermaid in `README_ru.md` is a
  translation of the English block: keep the state identifiers, translate the labels.
