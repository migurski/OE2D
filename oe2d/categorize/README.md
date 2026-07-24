# oe2d

Categorize (and, later, extract) OpenElections source files.

## CLI

    oe2d-categorize-source path/to/file        # -> JSON categorization
    oe2d-make-fixture --manifest ...           # trim sources into fixtures
    oe2d-label-categories                      # guided gold labeling

`oe2d-categorize-source` always runs the full pipeline — there is no
deterministic-only fallback, and missing pieces fail loudly:

- A deterministic layer sniffs the container (vector vs scanned PDF, binary vs
  XML .xls, xlsx/csv/txt/zip) and page/sheet count, and provides a grain hint
  from the file name. These feed the RLM as inputs.
- A `dspy.RLM` writes Python in a sandbox and calls host-side text tools to look
  at the file — `page_count`, `page_table`, `page_words`, and `zip_members`. A
  scanned PDF has no extractable text; the tools report that and the RLM infers
  what it can from the container, page count, and file name. (Per-page vision is
  moving to the standalone `oe2d.pages` analyzer; the categorizer's own
  `PageInspector` was removed as the first step of that redesign.)

If DSPy, Bedrock credentials, Deno, or LibreOffice are missing, the command
raises rather than emitting a partial categorization.

## Runtime system dependencies

- **Deno** — runs the RLM's Python sandbox (`dspy.PythonInterpreter`). macOS:
  `brew install deno`.
- **LibreOffice** — renders office formats (xlsx, both .xls flavors, csv, docx)
  to PDF for rasterization. The categorizer's text tools read spreadsheets
  directly and no longer need it, but `oe2d.pages` and `oe2d-render-page` do.
  macOS: `brew install --cask libreoffice`. Linux: `apt install
  libreoffice-calc libreoffice-writer`. PDFs need no LibreOffice.
- **optipng** — optional; shrinks rendered images. `brew install optipng`.

The model is hardcoded to Fireworks' Kimi K2 for the RLM — no model override
needed.

## Environment variables

- `FIREWORKS_AI_API_KEY` — Fireworks credentials for the task LM (required).
- `CMPND_API_KEY` / `CMPND_ENDPOINT` — turn on cmpnd tracing.

The optimizer (`oe2d-optimize-categorizer`) additionally needs Bedrock
credentials (`AWS_PROFILE`) for its Opus reflection LM.
