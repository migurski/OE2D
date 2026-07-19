# oe2d

Categorize (and, later, extract) OpenElections source files.

## CLI

    oe2d-categorize-source path/to/file        # -> JSON categorization
    oe2d-make-fixture --manifest ...           # trim sources into fixtures
    oe2d-label-categories                      # guided gold labeling

`oe2d-categorize-source` has two layers:

- **Deterministic** (always runs, no model): container format (vector vs
  scanned PDF, binary vs XML .xls, xlsx/csv/txt/zip), page/sheet count, and a
  grain hint from the file name.
- **RLM** (runs when a model is configured): a `dspy.RLM` writes Python in a
  sandbox and calls host-side tools to look at the file —
  `page_count`, `page_table`, `page_words`, `zip_members`, and `inspect_page`.
  `inspect_page` renders a page/sheet to an image and runs a vision model on it
  (`PageInspector`), returning text — the only way to read scanned PDFs, and
  the way rotated headers / side-by-side layouts get confirmed. Only text
  crosses the sandbox boundary; image bytes never do.

## Runtime system dependencies

- **LibreOffice** — renders office formats (xlsx, both .xls flavors, csv, docx)
  to PDF for `inspect_page`. macOS: `brew install --cask libreoffice` (found in
  the app bundle automatically). Linux: `apt install libreoffice-calc
  libreoffice-writer`. PDFs need no LibreOffice.
- **Deno** — runs the RLM's Python sandbox (`dspy.PythonInterpreter`). macOS:
  `brew install deno`.
- **optipng** — optional; shrinks rendered images. `brew install optipng`.

The model is hardcoded to Bedrock's Llama-4 Maverick (multimodal) for both the
RLM and the vision inspector — no model override needed.

## Environment variables

- `OE2D_NO_LM=1` — skip the RLM, deterministic output only.
- `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` — presence enables the RLM.
- `CMPND_API_KEY` / `CMPND_ENDPOINT` — turn on cmpnd tracing.
