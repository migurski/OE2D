# Categorization program — handoff

Status of the `oe2d.categorize` source categorizer, how it works, the decisions
behind it, and what to do next. Branch: `migurski/categorize-sources`.

## The big picture

Goal: build a sequence of DSPy programs that turn election-result **source**
files (in `openelections/openelections-sources-*`) into well-formed
**OpenElections CSVs** (PRs to `openelections/openelections-data-*`), like the
2025 MI #76 and PA #165 PRs.

The plan (chosen early in the session):

1. **Categorizer first** — one program that labels each source by type, so
   everything downstream can be routed to the right extractor. ← *this is what's
   built.*
2. **Per-stage labels** — hand-label a gold set per stage.
3. **Hierarchical routing** — the category routes each source to a specialized
   extractor (vector-PDF-columns, scanned-PDF, Excel, zip, …). ← *not started.*
4. **Seed from the real repos** — fixtures drawn from MI/PA/CA sources.

We are at the end of building step 1's program and its gold set. The next
milestone is **GEPA-optimizing the categorizer** (details below).

## What works today

`oe2d-categorize-source path/to/file` returns a JSON categorization. Verified
end-to-end on scanned MI PDFs: the RLM reasons the file is a scan, calls the
vision tool, renders the page, and reads candidate orientation / grain / layout
off the image. Output shape:

```json
{
  "path": "...", "file_name": "...",
  "container": "scanned_pdf", "page_count": 4,
  "orientation": "candidate_columns", "grain": "precinct",
  "has_rotated_headers": false, "has_stacked_contests": true,
  "has_side_by_side": false, "has_multi_sheet_stitch": false
}
```

- **container** (deterministic): `vector_pdf | scanned_pdf | xlsx | xls_binary |
  xls_xml | csv | txt | zip | unknown`
- **orientation**: `candidate_columns | candidate_rows | unknown`
- **grain**: `precinct | district | county | unknown`
- **has_\*** layout properties (top-level booleans): rotated_headers,
  stacked_contests, side_by_side, multi_sheet_stitch. OCR-needed is *not* a
  property — it's implied by `scanned_pdf`.

`container` and `page_count` are computed deterministically and fed to the RLM
as inputs; the RLM predicts orientation, grain, and the layout properties.

There is **no deterministic-only fallback**: if a runtime piece is missing
(DSPy, an OpenRouter key, Deno, LibreOffice) the command fails loudly rather
than emitting a partial `llm_used=false` result.

## Architecture

Two layers, in `oe2d/categorize/__init__.py`:

1. **Deterministic** — `detect_container` (sniffs bytes: char-count for
   vector-vs-scanned PDF, header for binary-vs-XML `.xls`), `count_pages`,
   `grain_from_name`.
2. **RLM** — `run_rlm` builds a `dspy.RLM(SourceCategorizer, tools=[...])`. The
   RLM writes Python in a Deno/Pyodide sandbox and calls host-side tools to
   inspect the file, then submits the judgment fields.

Tools (`oe2d/categorize/tools.py`), all returning SIMPLE_TYPES so results cross
the sandbox boundary:

- `page_count`, `page_table`, `page_words` — read text (page = sheet number for
  spreadsheets). For a textless PDF page they return a `[no extractable text —
  call inspect_page]` steer so the model doesn't guess.
- `zip_members` — list a zip's contents; page tools take `member=` (keyword-only)
  to read an entry.
- `inspect_page` — the vision path. Renders a page/sheet to a high-res PNG
  (`oe2d/categorize/rendering.py`: pdfplumber for PDFs, LibreOffice→PDF→raster
  for office formats, optipng to shrink), runs the `PageInspector` DSPy program
  (`oe2d/categorize/inspector.py`) on the image, and returns the vision model's
  facts as **text**.

**Model:** OpenRouter Llama-4 Maverick (`MAVERICK_LM`), hardcoded, drives both the
RLM code-writing and the vision inspector (Maverick is multimodal). No override
env vars.

**Tracing:** `cmpnd.auto_instrument()` turns on when `CMPND_API_KEY` is set.

## Package layout

The shipped wheel is runtime-only: `oe2d/` code plus the trained model. The gold
set and fixtures live in a top-level `oe2d-data/` tree that is outside any
package dir, so setuptools never bundles it.

```
oe2d/                          # shipped wheel = runtime + trained model
  __init__.py
  source_table.py              # deterministic tabular reader (PDF/xlsx/xls), shared
  categorize/
    __init__.py                # categorizer core (detect_*, SourceCategorizer, run_rlm, main); auto-loads model
    tools.py                   # host-side RLM tools
    inspector.py               # PageInspector vision program (image + question -> facts)
    rendering.py               # single page/sheet -> compressed PNG (soffice resolver, optipng)
    label.py                   # label-categories: guided gold labeling
    fixture.py                 # make-fixture: trim sources into small fixtures
    datasets.py                # gold JSONL -> dspy.Example, stratified split
    metrics.py                 # score_category feedback metric for GEPA
    optimize.py                # GEPA runner (writes model/optimized_categorizer.json)
    model/
      optimized_categorizer.json  # committed package data, auto-loaded (absent until a run finishes)
      README.md
    README.md                  # how the categorizer runs
  tests/
    source_table/  test_source_table.py
    categorize/    test_*.py

oe2d-data/                     # top-level, committed, NOT in the wheel
  fixtures/
    source_table/              # 8 CA excerpts
    categorize/                # 89 generated fixtures
  labels/
    seed_sources.tsv           # 97 curated sources (manifest)
    category.jsonl             # 88 hand-labeled gold records (paths point at oe2d-data/fixtures/)
    README.md                  # taxonomy + coverage axes
```

Console scripts (all in the package, so all still work): `oe2d-categorize-source`
(= `oe2d.categorize:main`) is the runtime command; `oe2d-make-fixture`,
`oe2d-label-categories`, and `oe2d-optimize-categorizer` are the training/data
tooling. Only the code and the trained model ship in the wheel; `oe2d-data/`
does not. `import source_table` no longer works — it's
`from oe2d import source_table`.

## Running it

```
pip install -e .                 # deps: dspy, boto3, python-dotenv, pdfplumber, openpyxl, xlrd, xlwt, pypdf, pydantic
brew install --cask libreoffice  # office-format rendering (found in the app bundle)
brew install deno optipng        # deno = RLM sandbox; optipng = image shrink (optional)
# .env in the repo root supplies OPENROUTER_API_KEY (the runtime LM) and CMPND_* (tracing)
oe2d-categorize-source oe2d-data/fixtures/categorize/allegan-mi-official-federal-state-and-judicial-votes.pdf
```

The RLM's REPL steps stream to **stderr** by default (`--quiet` to silence);
stdout stays pure JSON. `inspect_page` logs each render + the vision facts. If
`oe2d/categorize/model/optimized_categorizer.json` exists it is loaded onto the
RLM automatically; otherwise the stock prompt runs.

Container-only checks and tests run without creds/Deno; the full categorization
does not.

## Data

**Taxonomy / gold** — `oe2d-data/labels/`:
- `seed_sources.tsv` — 97 sources chosen to span every container × grain ×
  layout, per-shape-capped (≤8) so no PDF shape dominates, with raw-download
  URLs. Built from MI/PA 2024 general + CA fixtures.
- `category.jsonl` — 88 of the 89 fixtures hand-labeled (one skipped as
  unresolvable). Distribution: vector_pdf 35, xlsx 25, scanned_pdf 13, xls_xml 7,
  zip 4, csv/txt/xls_binary/unknown 1 each.

**Fixtures** — `oe2d-data/fixtures/`, small format-preserving excerpts generated
by `oe2d-make-fixture` from the manifest. PDFs keep a few pages; spreadsheets
≤2 MB are copied whole (re-serializing breaks Quick Look and drops candidate
sheets); larger ones are trimmed to a few sheets with a column cap. ~38 MB total,
committed but never shipped in the wheel.

**Labeling workflow** — `oe2d-label-categories` walks each fixture, pre-fills the
deterministic fields, previews it (macOS Quick Look for PDFs; `open` in
Numbers/Excel for spreadsheets — `.xls` are converted to a temp `.xlsx` for
legibility), and asks only for the judgment fields. Appends to `category.jsonl`
with resume support.

## Hard-won lessons (don't re-learn these)

- **Only SIMPLE_TYPES cross the Deno sandbox.** Image bytes can't be returned to
  the RLM. Vision must happen host-side inside a tool that returns text. This is
  why `inspect_page` runs `PageInspector` itself and hands back a string.
- **base64 image data only "counts" as an image when delivered as an image
  content block** (which `dspy.Image` does), never as text in a prompt.
- **cmpnd.auto_instrument() patches `dspy.LM` going forward — it traces any LM
  created *after* the call, regardless of configured-vs-`set_lm`.** The inspector
  originally had `set_lm(maverick)` on an LM built at module-import time, *before*
  `_instrument()` ran, so that instance was never patched and its vision call was
  missing from traces; dropping `set_lm` made it use the ambient LM (created after
  instrumentation), which fixed it. The rule is creation order, not
  configured-vs-`set_lm`. `optimize.py` relies on this: it calls `_instrument()`
  before building the student and reflection LMs, so GEPA's Opus reflection calls
  trace alongside the Maverick task/vision calls (matching train-spam-finder,
  which instruments at module top and traces everything).
- **RLM tool `member` is keyword-only.** Maverick called
  `page_table(path, 1, container)`, so `'xlsx'` landed in `member`, opened the
  xlsx-as-zip, and raised a cryptic `KeyError`. Keyword-only turns that into a
  clear `TypeError`.
- **Empty text tools on a scan invite guessing** — they now return an explicit
  "call inspect_page" steer for textless PDF pages.
- **`.xls` in this corpus is mostly XML SpreadsheetML**, which Numbers renders
  terribly (even the originals) — hence the convert-to-xlsx-for-viewing step.
- **DSPy programs are defined at module top**, not lazily inside functions.
- **`WebFetch` on cmpnd trace JSON summarizes with a small model and can
  hallucinate** — verify trace claims against the raw data / record counts.

## Known gaps / issues

- **docx** categorizes as `unknown` (not in the `Container` literal, no reader).
  One fixture (Crawford MI) exercises it.
- **Detection edge case:** `2024-cass-county-mi-...pdf` is a `vector_pdf` with an
  empty/garbage text layer (0 words). It should behave like a scan. Consider
  having `detect_container` treat a PDF with negligible extractable text as
  `scanned_pdf`. (`mason-mi-...pdf` is similar — labeled needing OCR but detected
  vector.)
- **Maverick occasionally mis-fences generated code** (drops the ```python
  fence), so an intended `inspect_page` call can no-op; the RLM's iteration
  budget usually recovers. A stronger code-writing model would reduce this.
- **Taxonomy may need more axes** the real files surfaced: primary-vs-general,
  summary-report-vs-precinct-report. Revisit if routing needs them.
- **Fixtures are ~38 MB.** Fine for now; `--sheet-max-bytes` can shrink if it
  becomes a burden.

## GEPA optimization (built — not yet run)

The three pieces are in place under `oe2d/categorize/`, modeled on
`~/Documents/Email/train-spam-finder.py`:

1. **`datasets.py`** — loads `oe2d-data/labels/category.jsonl`; for each row recomputes the
   deterministic inputs (`detect_container`, `count_pages`, absolute file path)
   so labels can't drift, wraps as `dspy.Example` with
   `.with_inputs('file_path', 'container', 'page_count')`, and splits train/val
   deterministically (`split` sorts each container group by path and takes every
   stride-th one to val — no `random`), stratified so a container with ≥2
   examples always reaches both sides. `load_split()` does both in one call.
2. **`metrics.py`** — `score_category` returns a `dspy.Prediction(score,
   feedback)`: a weighted per-field scalar (orientation 3, grain 2, each `has_*`
   1) plus **prose** naming each wrong field (predicted vs expected), which is
   what GEPA's reflection reads. A gold `grain == 'unknown'` is not scored (the
   CLI fills grain from name-cues, so the RLM can't be faulted for it).
3. **`optimize.py`** (`oe2d-optimize-categorizer`) — builds the same
   `dspy.RLM(SourceCategorizer, tools=...)` the CLI runs, GEPA-compiles it with
   that metric, saves the optimized program to
   `oe2d/categorize/model/optimized_categorizer.json` (the path the CLI
   auto-loads), and prints val accuracy per field. Task LM = Maverick, reflection
   LM = Opus 4.5. Checkpoints to a repo-root `gepa-<digest>/` dir (digest of the
   run config) so re-running resumes; a `gepa.stop` file stops gracefully. Flags:
   `--max-metric-calls`, `--reflection-minibatch-size`, `--num-threads`,
   `--num-retries`, `--val-fraction`, `--log-dir`, `-v`.

Tests (`test_datasets.py`, `test_metrics.py`) are hermetic — they point the
loader at a tiny temp gold set over the small `source_table` fixtures rather than
opening all 88, and the metric tests use synthetic Examples. No creds needed; 81
tests pass.

**Still to do:** run it (on the Mac, with an OpenRouter key for the task LM,
Bedrock creds for the Opus reflection LM, + Deno + LibreOffice) to produce
`optimized_categorizer.json`, inspect the evolved prompt, and commit the artifact.
The CLI already auto-loads it when present. That closes **Milestone 1**.

Caveats for the run: 88 examples is small but workable for six fields; the
deterministic fields are exact so only the hard predictions are optimized; a full
run makes many OpenRouter calls (and renders images via `inspect_page`), so watch
cost and cmpnd traces. Note the runtime categorizer now needs only OpenRouter;
Bedrock is required solely for the optimizer's Opus reflection LM.

## After the categorizer (roadmap)

- **Descend the routing tree.** For the biggest bucket first (vector-PDF
  candidate-columns → `find-contest-tables.py` is the existing sketch), build the
  next stage as its own program with its own per-stage labels and GEPA run.
  Repeat per route: scanned-PDF (Textract lineage in the repo-root
  `pdf2excel.py`/`stitch-textract-results.py`), Excel, zip, csv/txt.
- **Assemble → OpenElections CSV.** `count-source-votes.py` /
  `prepare-openelections-csv.py` are the sketches. Target schema (from PA #165):
  `county,precinct,office,district,party,candidate,votes,early_voting,election_day,provisional,mail`.
- **Validate end-to-end** against the merged MI/PA gold CSVs (row-set match),
  then open PRs.
- **Schema follow-ups** as labeling/routing demand: docx support, the
  primary/summary axes, the textless-vector-PDF detection fix.
