# Seed sources for categorizer labeling

`seed_sources.tsv` is a curated, deliberately diverse list of election-result
sources to run `oe2d-categorize-source` against and then hand-label. Hand
labels become the per-stage gold set for GEPA optimization of the categorizer.

Drawn from the MI and PA 2024 general sources repos (the richest starting
points) plus the local CA `fixtures/`. It is a pointer list, not a download:
`repo` + `file` locate each source under `openelections-sources-<st>/2024/general/`
(or the local `fixtures/` dir).

## Columns

- `repo` — `openelections-sources-mi` / `-pa`, or `fixtures` for local files
- `file` — exact basename
- `container_hint` — best guess before reading; `pdf?` and `xls?` mean the
  deterministic layer resolves the real container (vector vs scanned PDF,
  binary vs XML xls) at read time
- `grain_hint` — precinct / district / county cue from the file name, or `?`
- `notes` — which bases this file covers (see axes below)

## Coverage axes (why these files)

- **container**: every non-PDF is included because they are rare and precious
  — `xlsx`, `xls`, `csv`, `txt`, `zip`, `docx`. PDFs span both vendor-normalized
  exports (usually vector) and county-original scans (often bitmap).
- **grain**: precinct- vs district- vs county-level, from filename and content.
- **structure / quirks**: summary-report vs precinct-report vs SOVC vs canvass;
  `multi-part` workbooks that need stitching (e.g. Otsego parts 1/2, Kalkaska
  #1/#2); the CA fixtures cover rotated headers (CW and CCW), no-vertical-lines
  stacked contests, and landscape side-by-side contests.
- **election type**: a few 2024 primaries are included alongside the general.

## Schema gaps this set surfaces

- `docx` is a real container (3 MI files) not yet in `categorize.Container`.
- `primary` vs `general` and `summary-report` vs `precinct-report` are axes the
  current taxonomy does not capture; revisit if routing needs them.
