# oe2d.votes — handoff 4

Continues `votes-HANDOFF-3.md` (which covered the training-data expansion through Plumas/Ontonagon and
the geometry-alignment work). This doc is **self-contained** — read it to pick up after a context
compaction. Read HANDOFF-2 for the original architecture rationale; HANDOFF-3 for the intermediate
detail; this for the current state and how to continue.

## Goal

Build `oe2d.votes`: extract OpenElections precinct CSV rows from a located contest's pages, scored
against the human-authored state-repo **results** CSVs as ground truth. Core principle: **the LLM
decides structure/language; deterministic Python moves the digits, never the reverse.** The composite
`VoteExtractor` (a `dspy.Module`) does read → interpret (LLM) → stitch → canonical rows. The two named
inner predictors (`interpret_columns` / `interpret_rows`) are what GEPA would optimize; their
signature-docstring instructions carry the how-to-decide guidance. Everything else (reader dispatch,
walkers, stitch, consensus, write-in consolidation, all-zero drop) is deterministic Python.

## Status snapshot (current)

- **Gold: 33 contests, all 1.000.** 16 original + Branch 4 + Columbia 3 + Plumas 2 + Ontonagon 4 +
  Montmorency 4. Score the whole set with `oe2d-votes-evaluate` (no args); the content-addressed
  Textract cache makes re-runs free.
- **Rendered at 400 DPI** (`votes.TEXTRACT_DPI = 400`).
- **Tests: `oe2d/tests/votes/` 53 pass** (`test_votes_walker`, `_metrics`, `_scope_flat_tables`,
  `_normalize_columns`, `_join_flat_table_pages`, `_align_columns`). `oe2d/tests/pages/` pass too.
- Gold data: `oe2d-data/votes/index.jsonl` (one record per contest) + one
  `<county>__<contest>__expected.csv` each.

## Environment / operational notes (READ FIRST)

- **AWS**: profile `cmpnd-mike-root`, region `us-west-2`. Textract (boto3), the Bedrock Sonnet
  interpreter, and the Bedrock Maverick pages VLM all use the ambient `AWS_PROFILE`. Every run must
  export: `AWS_CONFIG_FILE=./.aws/config AWS_SHARED_CREDENTIALS_FILE=./.aws/credentials
  AWS_PROFILE=cmpnd-mike-root AWS_REGION_NAME=us-west-2`.
- **Interpreter LM**: `votes.LM_CLAUDE_SONNET45` (Bedrock Sonnet 4.5). Pages VLM:
  `pages.LM_LLAMA4_MAVERICK` (Fireworks Kimi K2 is suspended — billing).
- **Textract cost + cache**: cheap `DetectDocumentText` (~$0.0015/pg) vs `AnalyzeDocument TABLES`
  (~$0.015/pg, 10x). Cache: `./.cache/textract/` (caller cwd, gitignored), **content-addressed** on
  `sha1(file BYTES + page + mode + render-DPI)` so the same source at any path shares one entry.
  `votes.textract_usage()` → `{calls, usd}` (paid calls only); `oe2d-votes-evaluate` prints a spend
  line. DPI is part of the key, so switching DPI re-pays (once) and re-OCRs.
- **Venv**: `.venv-linux` on Linux. Local scratch for host-visible files: `./tmp/`.
- **Results CSVs (ground truth)**:
  `openelections-data-{ca,mi,pa}/{year}/counties/{YYYYMMDD}__{st}__general__{county}__precinct.csv`
  (raw.githubusercontent). 2024 general = `20241105`, 2020 general = `20201103`.
- **Source PDFs**: `openelections-sources-{ca,mi,pa}/{year}/general/...`. Find via the GitHub trees
  API; verify `sha1` matches the local `tmp/new-kinds/*.pdf` copy so the Textract cache carries over.

## Read strategies and the read paths (the heart of it)

`VoteExtractor.forward(...)` dispatches on **read_strategy** (READ MECHANICS) and **orientation**
(CONTENT structure), kept orthogonal. Either left `None` is DETECTED from a sample page via
`detect_dispatch` (oe2d.pages image VLM + text-layer check), checksum-confirmed. Gold records carry
both explicitly. `ReadStrategy = Literal['auto','ruled_scan','flat_tables','flat_grouped','ruled_columns']`.

`_read_votes` routes:
- **`ruled_scan` / `flat_tables`** → `_extract_scanned_tables` → `scope_flat_tables`. FLAT contest
  (one row per precinct, one total per candidate). Continuation semantics: a page's tables + later
  pages' tables are one contest; the anchor is the best candidate-name-match table, continuations
  aligned to it. Reconcile-confirmed (Sigma precincts == printed county total); falls back to auto on
  mismatch. Used by Huron (scan), Branch (borderless vector), Columbia (scan count+percent).
- **`flat_grouped`** → `_extract_grouped_tables` → `join_flat_table_pages`. FLAT contest whose
  candidate columns are SPLIT across pages that repeat the SAME precincts (Hart SOVC too wide for one
  page). Runs `scope_flat_tables` per page, joins by precinct (union candidate columns, SUM write-in
  rows), matching precincts by `_precinct_key` (strips punctuation, so "01 - Chilcoot" == "01
  Chilcoot"). Used by Plumas president, Ontonagon (all 4).
- **`ruled_columns`** → `_extract_contest(..., via_tables=True)`. COLUMNS with vote-METHOD sub-rows
  (Election Day / AV / Early Voting / Total per precinct), scanned. Reads each page's candidate grid
  via Textract TABLES (picks the table whose header names the most candidates), then the method
  walker + candidate/precinct-group stitch. Used by Montmorency (all 4).
- **`auto` + orientation 'rows'** → `_extract_precinct_contest` (precinct-major: one precinct per
  page, candidates as rows, methods as columns). Used by Calaveras, Adams.
- **`auto` + orientation 'columns'** → `_extract_contest` (via_tables=False): reads `read_page_grid`
  per page (source_table for ruled vector → read_rotated_grid → read_text_grid → read_scanned_grid for
  a scan), method walker + groups. Used by the original MI SOVCs (Oscoda, Barry, Gogebic, Calhoun).

### Geometry column alignment (the big HANDOFF-3→4 change)

The old exact-column-count gate in `scope_flat_tables` was brittle: Textract re-segments a page
slightly differently across pages/DPIs (a count split from its percent, a title word wrapping),
diverging the column count and silently dropping a page. Now:

- **`read_scanned_tables` returns `(StringGrid, ColumnX)` per table** — `ColumnX` = per-column
  x-centres (normalized) from Textract cell geometry. `read_flat_tables` strips percent/spacer columns
  from BOTH grid and ColumnX together (via `_kept_columns`). Threaded through `scope_flat_tables(...,
  column_x=)` and `join_flat_table_pages(..., pages_column_x=)`.
- **`_align_columns` separates IDENTITY from POSITION.** IDENTITY (does a table belong to THIS
  contest?) is by candidate NAMES: a header-bearing table must name ceil-3/4 of the candidates (a
  stacked full-width neighbour sharing a surname, e.g. Jill Stein vs Dave Stein, is rejected); a
  header-less / label-only-header continuation rests on sharing the anchor's WIDTH. POSITION (which
  cell holds each count?) is by GEOMETRY: each candidate claims the nearest count-bearing column to
  its anchor x-centre (`_X_TOLERANCE = 0.04`). No geometry (hand-built test grids) → name/anchor
  fallback, still unit-tested.
- **`_snap_to_counts`** repairs the ANCHOR: the interpreter can map a candidate onto an empty
  split-off party cell ("(REP)"); snap it to the nearest count column (Ontonagon president Trump).
- **Type aliases** (`oe2d/votes/__init__.py` top): `StringRow = list[str]` (a grid row),
  `StringGrid = list[StringRow]`, `ColumnMap = dict[int,int]` (candidate position → grid column),
  `ColumnX = list[float]` (a grid's per-column x-centres).

### DPI is a per-cell TRADE, not a global optimum

400 disambiguates dense-scan digits 300 misreads (Ontonagon printed a flat-top **5** Textract reads
as `$` at 300). But 400 also breaks cells 300 got (a scan speck read as `7.`) — so there is NO single
best global DPI. With geometry alignment + `_cell_count` tolerating a trailing period ("7." → 7) and
`_snap_to_counts`, 400 no longer breaks the reads a naive bump used to, so it is the committed
default. Switching DPI re-OCRs precinct-name STRINGS (the Hart `NN - Name` dash appears/disappears),
so three 300-built golds were re-LABELLED to the 400 read — **no vote value changed** (verify the
nonzero-vote row set is identical old-vs-new before committing such a rebuild). **FUTURE IDEA (not
built): run a PAIR of DPIs all the way through, diff, reconcile discrepancies against the source** —
the durable answer to the DPI trade.

### Cheap vs TABLES Textract — same OCR, different assignment

Important (Mike verified this): `DetectDocumentText` (cheap) and `AnalyzeDocument TABLES` OCR the
**identical WORD set** on the same rendered image (241 vs 241, 120 vs 120, empty symmetric diff).
TABLES wins ONLY on cell ASSIGNMENT via the drawn grid lines. So `read_scanned_grid`'s word-clustering
(row-gap + a ">= 4 counts per data row" rule) is what drops method sub-rows / narrow write-in cells;
`ruled_columns` (TABLES) fixes it by placing the same words with the ruled grid. Don't reach for a
higher DPI to "find" missing words — they're already detected; the reconstruction is the problem.

## The gold-build process (validated on 7 counties)

Per contest: 1) pull the human results CSV; 2) find the contest's page range (map by rendering titles
or cheap-Textract/pdfplumber grep); 3) run the extractor and **diff against the results** —
`compare by (precinct, party)` for named candidates + write-in SUM per precinct, so candidate-name
formatting doesn't mask value diffs; 4) build the expected CSV from the extractor's output rows
(canonical columns) + an index record; 5) verify 1.000 with `oe2d-votes-evaluate --only <id>` AND
that values reconcile to the results.

- **Gold is built FROM the extractor output** (source-faithful precinct + candidate names), with
  values VERIFIED against the results CSV. `datasets.candidate_context` re-derives the context from
  the expected CSV, so the gold is self-consistent at eval time.
- **Index record fields** (mirror an existing one, e.g. `datasets.find('ontonagon-2024-general-president')`):
  id, state, county, office, district, source_url, pages, container, read_strategy,
  geometry{candidate_orientation, precinct_axis}, schema_features, checksums, candidate_context
  (real names, no write-in), expected_csv, expected_row_count, precinct_count.
- **Canonical columns**: `county, precinct, office, district, party, candidate, votes, election_day,
  early_voting, absentee_mail, provisional`. Method mapping for MI ClearBallot: Election Day →
  election_day, Early Voting → early_voting, AV Counting Boards → absentee_mail, Total → votes.
- **Office strings** (canonical): `President`, `U.S. Senate`, `U.S. House` (+ district), `Straight
  Party`, `Attorney General`. Normalize `Straight Ticket` → `Straight Party`.

## Conventions that are Mike's calls (do NOT decide these silently)

- **Diverging gold from the published GitHub reference is Mike's decision, not autonomous.** When the
  source PDF and the reference CSV disagree, SURFACE it (which precincts/values, why) and get sign-off
  before recording a value that differs from the reference. He was uneasy to learn earlier sessions
  rebuilt Barry/Adams/Calhoun/Columbia write-in gold without it.
- **Source wins on NAMES.** When the source scan and the results CSV disagree on a precinct or
  candidate name (Hart `NN - Name` dash; results OCR typo "Greenhom" vs source "Greenhorn"; Greenland
  "…, Precinct 1" the results dropped; minor candidate "Chase Oliver /" as the scan prints it), the
  gold keeps the SOURCE-faithful string. Values must still match the results.
- **Write-ins**: the extractor consolidates the SOURCE's write-in columns/rows into one `Write-ins`
  row (`WRITE_IN_LABEL`). Reference CSVs frequently record write-ins as all-zero even when the source
  has real write-in votes — checksum a 0 write-in against the source (`Σ candidates == printed Total`)
  and record the source-correct value (à la Barry; Columbia MOUNT PLEASANT write-in 4 vs reference 0;
  Missaukee/Branch Peter Sonski a named write-in that folds into Write-ins). This is a decision to
  RAISE, per the first bullet.
- **All-zero precinct drop**: `votes_to_rows` drops a precinct whose every candidate total is 0 (an
  out-of-county placeholder like "Duncan Township (Houghton County)"); the results exclude them too.

## Determinism / DSPy cache (READ before trusting a re-run)

DSPy caches every LM response by prompt, so **re-running the extractor replays cached schemas** — a
repeated-run "determinism" check proves nothing. To test a read is genuinely stable, build the LM with
`cache=False` (`dspy.LM(..., cache=False)`) and run several times (Plumas president verified this way).
The committed gold's 1.000 must hold on a COLD cache, not just a warm one.

## Deferred batch files (recon banked — start here)

Four `tmp/new-kinds/*.pdf` remain, all DEFERRED for context budget. Suggested order easiest-first:

- **Bay** (`robustness--bay-mi.pdf`, 286 pp vector, 47 precincts, methods, Electionware). UNEXAMINED —
  standard methods layout, probably `ruled_columns` or the `auto`/columns method path. Do this first.
- **Mono** (`optional--mono-ca.pdf`, 75 pp vector, 13 precincts, methods). Per-precinct "Election
  Summary Report" documents (~6 pp each; President on that report's p1) → ROWS orientation
  (`_extract_precinct_contest`). NOTE: its propositions are NOT in the results CSV, so there is no
  ballot-measure gold — only President / U.S. House / State Assembly. Needs report-boundary page
  mapping across the 75 pp.
- **Missaukee** (`vector--missaukee-mi.pdf`, 7 pp vector, 18 precincts, votes-only). ALL federal
  contests are ONE wide FLAT mega-grid on p1 (Straight Party | President | US Senate | US House |
  State House as side-by-side candidate-column blocks). `flat_tables` reads Straight Party / President
  / US Senate CLEAN (0 by-party diffs) but **US House fails (67 diffs)** and President write-in is +1
  (Richland). WHY: the Textract grid header carries only PARTY labels (`Dem/Rep/Lib/Grn`, repeated per
  contest) + contest TITLES (`Rep Congress 4th`); candidate NAMES (Barr/Bergman/Gale/Hakola) are NOT
  in the grid, so the interpreter scopes US House's 4 columns among 37 by title+position and grabs the
  wrong ones (it sits between US Senate and State House). FIX = title/position-aware column scoping for
  a single-table multi-contest grid; there's no continuation/geometry to lean on. This is the most
  interesting remaining problem.
- **Nevada** (`robustness--nevada-ca.pdf`, **832 pp** vector, 97 precincts, methods). Per-precinct
  layout (President/US House/State Senate/State Assembly). Huge; needs a page-mapping pass first.

## Then the deferred infrastructure (from HANDOFF-2/3)

1. Teach `detect_dispatch` to pick `flat_tables` / `flat_grouped` / `ruled_columns` from the page
   image (gold currently carries read_strategy explicitly).
2. Cheap→TABLES **escalation** (cost): try the cheap read, checksum, escalate to TABLES only on
   reconcile failure. Bounded for now by the content-addressed cache.
3. GEPA optimization of the two interpreter predictors once there's error signal (e.g. Missaukee US
   House would be a good driver).
4. The **dual-DPI reconciliation** idea (see the DPI section).

## Where to look in the code

`oe2d/votes/__init__.py` — everything. Key functions, top to bottom: type aliases + `ReadStrategy` +
`TEXTRACT_DPI` (top); `_cell_count` / `_assign_methods` / `_consolidate_write_in` / `_reconciles`
(digit helpers); `_kept_columns` / `_normalize_table_columns`; `read_scanned_tables` (→ grid+ColumnX)
/ `read_flat_tables` / `read_scanned_grid` / `read_rotated_grid` / `read_text_grid` / `read_page_grid`;
`_textract_blocks` (cache + spend); `_snap_to_counts` / `_align_columns` / `scope_flat_tables`;
`_name_tokens` / `_precinct_key`; `join_flat_table_pages`; `walk_page` / `_precinct_groups`;
`class VoteExtractor` (`forward` → `_read_votes` → the five `_extract_*` methods); `votes_to_rows`;
`build_extractor`. Tests mirror the pure functions in `oe2d/tests/votes/`. Metrics + datasets +
signatures are sibling modules. `oe2d/votes/evaluate.py` is the scoring CLI (`--only`, `--detect`,
`--detected`, `--val-only`).
