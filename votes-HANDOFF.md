# oe2d.votes — design handoff

Record of the design work for a new `oe2d.votes` submodule: turn a located election-results
source into OpenElections **precinct** CSV rows. Pick up from here. Nothing is coded yet —
this documents the target, the pipeline shape, the decisions locked, and the hand-built gold
set that will seed the DSPy signatures + training data.

Scope note: **precinct data only** for now (the hard, valuable one). County-summary output is
deferred. Extraction is bounded to the contests `oe2d.contests` already located, so out-of-scope
local offices never enter.

## Where this sits

`oe2d.votes` is the extraction layer on top of the existing modules:

- `oe2d.contests` — locates a contest's page set and yields the target **office label**
  (`Target.contest`, already the OE-standard office), the **observed title** (carries district),
  and known **candidate context**. votes *inherits* office + district + candidate hints from here;
  it does not re-derive them.
- `oe2d.pages` — per-page structure (candidate_orientation, precinct_scope, etc.) — the geometry
  hint the interpreter uses.
- `oe2d.source_table` — reads a vector-PDF/spreadsheet page into a grid (handles rotated headers,
  contest titles, column detection). This is the cheap read path.
- `oe2d.pagetext` / `oe2d.rendering` — text + image rendering already used elsewhere.

It unifies three untracked prototype scripts at repo root that did this by hand:
`pdf2excel.py` (Textract → Excel), `stitch-textract-results.py` (glue split sheets),
`prepare-openelections-csv.py` (→ OE CSV, with brittle `VOTING_METHODS`/regex heuristics). The
DSPy interpreter replaces those heuristics.

## The pipeline (design)

Guiding principle, inherited from `oe2d.contests`: **the model decides structure; deterministic
code moves the digits.** A model never transcribes a vote number.

1. **Read (container-dependent, deterministic).** `vector_pdf → source_table` grid; `scanned_pdf
   → Textract` grid. Output: a normalized grid per page. This is the ONLY place container/scan
   matters — everything downstream is identical.
2. **Interpret (the DSPy judgment — one call per page-group).** Given the grid + known candidates
   + office/district + orientation hint, return a **schema**: which axis is candidates vs methods
   vs precincts, and the label maps — `candidate → (name, party)`, `method label → canonical
   bucket`, pseudo-office / special rows, columns to ignore (percent, `Total Votes`, spacers).
3. **Stitch + emit (deterministic).** Apply the schema; accumulate `(precinct, candidate) →
   {method: votes}` across the contest's pages (contiguous or scattered); pivot methods into
   canonical columns; **assert the checksums**; write canonical CSV.

The interpreter is the one signature worth optimizing. Designing its inputs/outputs is the next task.

## Canonical output schema

Spec-aligned (`openelections/docs` dataentry.md + standardization.md, `openelections/utils`).
Required core, in order, present for every row:

```
county, precinct, office, district, party, candidate, votes
```

Optional method-breakdown columns, emitted only when the source breaks them out, canonical set:

```
election_day, early_voting, absentee_mail, provisional
```

Rules:
- `votes` is the **total** and the primary scored value (always present). Method columns are addends.
- Column order: core, then method columns sorted (utils convention). Our working header:
  `county, precinct, office, district, party, candidate, votes, election_day, early_voting, absentee_mail, provisional`.
- **office** = OE controlled vocabulary (inherited from `oe2d.contests` target).
- **candidate** = verbatim as printed in the source (spec rule). Do NOT canonicalize names.
- **special-row labels** (Write-ins, Not Assigned, Over Votes, …) = verbatim too. The spec has no
  controlled vocab for them, and per-source scoring makes verbatim always match. NOT folded.
- **party** = as authored (abbreviations for MI/PA: DEM/REP/LIB/…). Source drift (`PF`→`PFP`) is left
  as the gold has it.
- No commas in totals (canonical strips them; Calhoun gold had `1,031`).
- Totals rows = blank `precinct` (OE convention). Our gold has none; we may extract a county total
  only to validate, then discard.

### A contest is `(office, district)`
Multi-district counties yield one gold per district (Calhoun U.S. House → separate CD-4 and CD-5),
each single-district, each district read from its own titled section. Same as contests targets.

### Method normalization (canonical wins)
Vendor labels fold into the four buckets; vendor sub-splits are summed:
- `Election Day` / `In-Person` → `election_day`
- `Early Voting` → `early_voting`
- `AV Counting Boards` / `Mail Votes` / `Vote by Mail` / `absentee` / `mail` → `absentee_mail`
- `Provisional` → `provisional`
- `Total` → `votes`

Note the deliberate correction: a 2020 CA gold mapped `Vote by Mail → early_voting`; canonical
overrides that (`→ absentee_mail`) and we store canonical form, not the six-year-old choice.

### Pseudo-offices and special rows
- **Pseudo-offices** (own `office`, blank `candidate`): `Registered Voters`, `Ballots Cast`,
  `Straight Party` (spec-named). `Times Cast`, `Ballots Cast - Blank` also seen. Straight Party's
  "candidates" are party names (`candidate=Democratic`, `party=DEM`).
- **In-contest specials** (candidate column, real office): `Write-ins`, unresolved write-in
  (`Not Assigned` / `Unresolved Write-In`), `Over Votes`/`Under Votes`.
- `Yes`/`No` are real ballot-measure options, not specials.
- The side `Registered Voters` block rides along in every contest's table — it belongs to its own
  extraction pass, NOT duplicated into each contest.

## Structural axes (what changes the interpreter's job)

- **candidate_orientation**: `columns` (contest-major, precincts as rows — MI/EMS) | `rows`
  (precinct-major, contests stacked — CA/PA precinct-summary).
- **method_layout**: `sub_rows` (Election Day/AV/EV/Total stacked per precinct) | `sub_columns`
  (In-Person/Mail/Prov/Total as columns) | `none` (total-only).
- **page organization**: contiguous span | scattered (one page per precinct block).
- **split**: none | horizontal (candidate columns overflow) | vertical (precincts overflow) | both.
- **contest boundary**: page-aligned | **mid-page** (Huron packs contests contiguously; a page holds
  one contest's tail + the next's head → the interpreter must segment contests *within* a grid).
- **header encoding**: rotated 90° (Hart) | clean horizontal (`Name / Mate - PARTY`, EMS) |
  party-prefixed (`DEM HARRIS and WALZ`, Electionware) | percent columns interleaved (ignore).
- **container**: `vector_pdf` (source_table) | `scanned_pdf` (Textract).

## Checksums (make the extractor self-validating)

Three orthogonal integrity checks, verified holding across all gold:
1. **Row / method sum**: `votes == Σ(present method components)` per candidate row. (Degrades to
   n/a for total-only sources.)
2. **Column / cross-candidate**: printed `Total Votes` (or `Total Votes Cast`) `== Σ(candidate
   votes) − unresolved write-in`. Guards horizontal stitching (a dropped candidate column fails it);
   the write-in offset also pins write-in semantics. Note: `Total Votes` **excludes** the unresolved
   write-in.
3. **County grand total**: `Σ(precinct votes) == county total row`. Extract to validate, then discard.

Bake #1 and #2 (write-in-aware) into the metric and as a pipeline self-check.

## The gold set (hand-built, `tmp/votes-gold/`)

16 examples, each an `index.jsonl` metadata record + a `<county>__<contest>__expected.csv` in
canonical form. **Numbers are copied from human-authored state-repo CSVs, not re-derived from the
PDFs** — those authored CSVs are the ground truth `oe2d.votes` must reproduce; re-deriving would
just be the extraction we're building, unchecked. PDFs were rendered only to learn geometry and
spot-check. Every example's checksums are green; gold transcription errors (if any surface later)
are corrected then, flagged by a checksum miss.

Sources of the numbers:
- MI: `openelections-data-mi/2024/counties/20241105__mi__general__<county>__precinct.csv`
- PA: `openelections-data-pa/2024/counties/20241105__pa__general__<county>__precinct.csv`
- CA: this repo's `2020/20201103__ca__general__precinct.csv` (2020; no 2024 CA output exists)
- Source PDFs: `openelections-sources-{mi,pa,ca}/…` (URLs in each record's `source_url`).

| id | rows | orient. | method layout | container | pins |
|---|---|---|---|---|---|
| oscoda-…-president | 91 | columns | sub-rows ×3 | vector | horiz+vert split, rotated, pseudo-office |
| oscoda-…-us-house (d1) | 35 | columns | sub-rows ×3 | vector | district (title-parse) |
| barry-…-president | 312 | columns | sub-rows ×4 | vector | 4th method, percent cols |
| barry-…-straight-party | 192 | columns | sub-rows | vector | pseudo-office, candidate=party |
| calaveras-…-president | 252 | rows | sub-cols | vector | methods-as-cols, scattered, method remap |
| adams-…-president | 408 | rows | sub-cols | vector | 2nd row-vendor (Electionware), party-prefixed |
| gogebic-…-president | 130 | columns | sub-rows ×3 | **scanned** | Textract read path |
| huron-…-president | 288 | columns | total-only | **scanned** | EMS vendor, mid-page boundary, special precincts |
| calhoun-…-president | 728 | columns | sub-rows ×4 | vector | dense single-width, Times Cast |
| huron-…-straight-party | 231 | columns | total-only | scanned | total-only pseudo-office |
| huron-…-us-senate | 224 | columns | total-only | scanned | total-only statewide |
| huron-…-us-house (d9) | 160 | columns | total-only | scanned | total-only + district |
| calaveras-…-us-house (d4) | 140 | rows | sub-cols | vector | row-orient + district |
| adams-…-attorney-general | 510 | rows | sub-cols | vector | statewide row office |
| calhoun-…-us-house-4 | 128 | columns | sub-rows ×4 | vector | multi-district county, CD-4 section |
| calhoun-…-us-house-5 | 96 | columns | sub-rows ×4 | vector | multi-district county, CD-5 section |

`index.jsonl` record fields: `id, state, county, office, district, observed_title, source_url,
pages, container, geometry, schema_features, checksums, candidate_context, expected_csv,
expected_row_count, precinct_count`.

Build scripts (throwaway, in `tmp/`): `make_gold.py` (Oscoda), inline builders for the rest,
`make_gold_batch.py` (non-presidential batch). Working PDFs/CSVs/renders also in `tmp/`.

## Decisions locked
- Single canonical schema, as capacious as practical; store gold in canonical form (methods
  normalized, commas stripped) — don't preserve idiosyncratic per-county authoring.
- candidate + special-row labels verbatim; office + methods normalized; party as authored.
- One gold per `(office, district)` contest.
- Gold numbers = authored CSVs, verbatim; checksums guard transcription.
- Registered Voters etc. are their own pass, not duplicated per contest.

## Open questions / next steps
1. **Design the `oe2d.votes` interpreter signature(s)** — inputs (grid, known candidates, office/
   district, orientation) and outputs (typed column/row schema). This is the immediate next task.
   Likely one `DecodeGrid`-style signature returning axis roles + label maps; consider whether
   `columns` vs `rows` orientation is one signature or two.
2. **Module skeleton** mirroring `pages`/`contests`: `oe2d/votes/{__init__,signatures,datasets,
   metrics,evaluate,optimize}.py`; permanent gold home `oe2d-data/votes/` (move from `tmp/`).
3. **Textract read path** — wire `scanned_pdf` extraction (the prototype `pdf2excel.py` +
   `stitch-textract-results.py` are the starting point); deskew per prior settled notes.
4. **Within-page contest segmentation** for mid-page boundaries (Huron).
5. **Coverage gaps** to add later: a ballot **measure** (Yes/No), a **State House/Senate** split,
   maybe a truly messy scan.
6. **Metric**: set-compare on `(precinct, office, district, party, candidate, votes[, methods])`
   after normalization; partial credit per field; enforce checksums.
