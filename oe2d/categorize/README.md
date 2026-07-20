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
- A `dspy.RLM` writes Python in a sandbox and calls host-side tools to look at
  the file — `page_count`, `page_table`, `page_words`, `zip_members`, and
  `inspect_page`. `inspect_page` renders a page/sheet to an image and runs a
  vision model on it (`PageInspector`), returning text — the only way to read
  scanned PDFs, and how rotated headers / side-by-side layouts get confirmed.
  Only text crosses the sandbox boundary; image bytes never do.

If DSPy, Bedrock credentials, Deno, or LibreOffice are missing, the command
raises rather than emitting a partial categorization.

## Runtime system dependencies

- **LibreOffice** — renders office formats (xlsx, both .xls flavors, csv, docx)
  to PDF for `inspect_page`. macOS: `brew install --cask libreoffice` (found in
  the app bundle automatically). Linux: `apt install libreoffice-calc
  libreoffice-writer`. PDFs need no LibreOffice.
- **Deno** — runs the RLM's Python sandbox (`dspy.PythonInterpreter`). macOS:
  `brew install deno`.
- **optipng** — optional; shrinks rendered images. `brew install optipng`.

The model is hardcoded to Fireworks' Kimi K2 (multimodal) for both the RLM and
the vision inspector — no model override needed.

## Environment variables

- `FIREWORKS_AI_API_KEY` — Fireworks credentials for the task/vision LM (required).
- `CMPND_API_KEY` / `CMPND_ENDPOINT` — turn on cmpnd tracing.

The optimizer (`oe2d-optimize-categorizer`) additionally needs Bedrock
credentials (`AWS_PROFILE`) for its Opus reflection LM.
