# oe2d.votes — handoff 2

Supersedes `votes-HANDOFF.md`. That doc captured the *design*; this one captures the *built
module* plus the robustness lessons that shaped it. Read this to pick up work on `oe2d.votes`.

## Goal

Turn a located election-results source into OpenElections **precinct** CSV rows:
`county, precinct, office, district, party, candidate, votes[, method breakdown]`. Input is a
source file plus the pages `oe2d.contests` located for one contest, the office/district, and the
expected candidates. Output is the canonical rows a human would have transcribed by hand — we
score against real human-authored CSVs.

Guiding principle (from `oe2d.contests`): **the LLM decides structure and language; deterministic
code moves the digits, and never the reverse.** A model never reads or returns a vote number.

## Status

Module is built and committed on branch `migurski/categorize-sources` under `oe2d/votes/`.
Current F1 — both plain (per-row) and vote-weighted (fresh DSPy cache):

| example | orientation | F1 | notes |
|---|---|---|---|
| oscoda president | columns | **1.000** | |
| oscoda us-house | columns | **1.000** | district |
| adams president | rows | **1.000** | explicit `Write-In Totals` column, used as the total |
| adams attorney-general | rows | **1.000** | shares president's pages+1; gold over/under terms fixed to doc form |
| calaveras president | rows | **1.000** | |
| calaveras us-house | rows | **1.000** | district; Overvotes/Undervotes canonicalized in the interpreter |
| barry president | columns | **1.000** | was 0.898 — see the write-in section below |
| barry straight-party | columns | **1.000** | all-zero out-of-county precincts dropped; gold Ward-spacing + no-write-in fixes |
| calhoun president | columns | **1.000** | rotated-header read + cross-page stitch; 4 write-in gold rows rebuilt from source |
| calhoun us-house-4 | columns | **1.000** | district; rotated-header read + cross-page precinct stitch |
| calhoun us-house-5 | columns | **1.000** | district; rotated-header read + cross-page precinct stitch |
| gogebic president | columns | **1.000** | **scanned, borderless-read** — cheap-mode Textract + own grid reconstruction |
| huron president / straight-party / us-senate / us-house | columns (flat) | **1.000** | **scanned, ruled TABLES** — flat one-row-per-precinct, cross-page row stitch |

Plain and vote-weighted F1 are **1.000 on all 15 gold contests** — 11 vector, 1 borderless-Hart scan
(Gogebic), and 4 ruled-flat scans (Huron). The whole gold set is closed. (Out-of-county 0-vote
placeholders like Barry's "(Eaton OOC)" and Huron's "Delaware Township (Sanilac County)" -- a Sanilac
township, web-confirmed -- are excluded by a NUMERIC all-zero-precinct drop in `votes_to_rows`, and
the golds were corrected to match; the "is it out-of-county" language judgment lives in the gold, not
in Python.)

16 hand-built gold examples live in `oe2d-data/votes/` (`index.jsonl` + one
`<county>__<contest>__expected.csv` each). Numbers are **copied from human-authored state-repo
CSVs**, not re-derived from PDFs — they are ground truth **for the values a human recorded** — with
one hard-won caveat: **the reference CSVs sometimes drop write-ins entirely** (see below), so where
a checksum against the source contradicts the reference, the source wins and the gold is rebuilt
from it. PDFs are rendered/read to understand structure, spot-check, and rebuild dropped columns.

## Architecture

Three stages, one narrow LLM judgment, chosen by candidate orientation (from `oe2d.pages`).

### 1. Read (container/vendor-dependent, deterministic)
- **Vector-PDF, ruled (Hart SOVC)** → `oe2d.source_table.page_table`.
- **Vector-PDF, text-aligned (Electionware, CA vendor)** → `oe2d.votes.read_text_grid` (pdfplumber
  `find_tables` with `vertical/horizontal_strategy='text'`, `text_tolerance=3`). `source_table`'s
  ruled settings mis-read these and split numbers. Kept in `votes` (vendor-specific to this step).
- **Vector-PDF, rotated-header text-aligned (Calhoun MI SOVC)** → `oe2d.votes.read_rotated_grid`.
  No ruled lines (so `page_table` finds nothing) and column HEADERS are rotated 90°, which the text
  layer emits character-reversed ("acisseJ" for "Jessica"). We recover columns from geometry:
  cluster the `upright=False` header words into candidate columns by an x-gap, un-mirror each token
  (only when the page as a whole scores better reversed — a cheap English-**bigram** check,
  `_reads_better_reversed`, which doubles as the "is this a rotated SOVC" dispatch signal), and bin
  the upright body words by their **center** x (right-aligned counts shift left with more digits).
  No OCR — the text layer is present, just mirrored. `extract_contest` dispatches here when
  `page_table` returns nothing. The bigram reversal is a deterministic text-orientation call; the
  candidate/terminology matching still happens in the LLM.
- **Scanned, RULED (drawn cell borders)** → `oe2d.votes.read_scanned_tables` via **Textract TABLES**
  (`AnalyzeDocument`). Borders let Textract segment cells reliably, including multi-line cells (a
  precinct name wrapped over several lines is ONE cell) and rotated headers — which our word-only
  reconstruction can't group without borders. Returns **every** table on the page (like
  `source_table.page_tables`), because a scanned page holds several contests' tables plus a
  header-less continuation of the previous page's; `extract_scanned_tables` scopes to the target
  contest by header-match (the anchor) + column-count (its header-less continuations), and stitches a
  precinct whose row straddles a page (data at one page's bottom, label continued at the next's top).
  Chosen by `read_strategy='ruled_scan'` (READ MECHANICS — orthogonal to content). Empirically:
  Textract TABLES nails a ruled table but SPLITS a borderless multi-panel Hart page into per-panel
  tables, which is why Gogebic uses the cheap reconstruction below and Huron uses TABLES. The
  ruled-vs-borderless call belongs to `oe2d.pages` (an image VLM field, not yet wired).
- **Scanned, BORDERLESS (no text layer, text-aligned)** → `oe2d.votes.read_scanned_grid` via **cheap-mode Textract**
  (`DetectDocumentText`, words + boxes, inline PNG bytes — NOT `AnalyzeDocument TABLES`). Renders
  with `oe2d.rendering`, deskews (`oe2d.pages.deskew`), then reconstructs the grid ourselves: cluster
  words into rows by y-center; take column x-centers from the counts on real data rows only (≥4
  integers in the data region), so banners/precinct-numbers don't invent columns; snap counts to the
  nearest column; rejoin a precinct name that wrapped across lines (`<place>,` + `Precinct N`). This
  distrusts Textract's table-splitting and is several times cheaper. Blocks are cached under
  `oe2d-data/votes/.cache/textract/`. Dispatched by `read_page_grid` only when a page has no text
  layer, so vector documents never pay for it. Thresholds are MI-SOVC-tuned normalized fractions;
  Textract read the vertical candidate headers upright at 99% confidence (no bigram reversal needed).

### 2. Interpret (the DSPy judgment)
Two orientation-specific signatures in `oe2d/votes/signatures.py`, sharing: expected-candidate
matching, method-label→bucket mapping, write-in flagging, and "never a number".

- **columns** (contest-major: precincts down rows, candidates across columns — MI/EMS):
  `InterpretResultsPage` → `PageSchema` (`first_data_row`, `label_column`, `columns[]` with
  `ColumnRole{index, role, candidate, party, write_in}`, `method_labels` label→bucket,
  `skip_labels`). Runs **per page**.
- **rows** (precinct-major: one precinct per page, contests stacked, candidates down rows, methods
  across columns — CA/PA precinct-summary): `InterpretPrecinctPage` → `PrecinctPageSchema`
  (`precinct_row/column`, `method_columns` col→bucket, `candidate_rows[]` with
  `CandidateRow{row_index, candidate, party, write_in}`). **Learned once from a sample page**, then
  applied deterministically to every structurally-identical page (one LLM call per document, not
  per page).

LM: AWS Bedrock Claude Sonnet 4.5 (`LM_CLAUDE_SONNET45`), temperature 0. `build_interpreter` /
`build_precinct_interpreter` bind it (or load a trained artifact if present) and call
`_instrument()` for Cmpnd tracing.

**Signature design — optimization-ready.** All how-to-decide guidance (candidate matching, party
"don't read it off the doc", write-in vs write-in-total, over/under-vote canonicalization, which
rows to skip) lives in the Signature **docstrings**, because that instruction text is what a prompt
optimizer (**GEPA**) mutates. The pydantic `Field(description=…)` on the nested output models render
into the prompt's output-format spec but are **not** reachable by GEPA, so they state only what each
field structurally *is* — never edge-case reasoning. Rule going forward: new guidance → docstring;
Field descriptions stay minimal and structural. (One consequence already paid off:
`Overvotes`/`Undervotes` canonicalization — rejoining a reader-split `"Ov | ervotes:"` — lives in the
`InterpretPrecinctPage` docstring and closed Calaveras us-house.)

### 3. Stitch + emit (deterministic)
- **columns** `extract_contest`: interpret each page → `walk_page` (schema-driven blocks) →
  **cross-page precinct stitch** → `_precinct_groups` (a repeated candidate starts a new group) →
  align candidate-pages within a group and accumulate. `votes_to_rows` → canonical CSV.
  - **Cross-page precinct stitch (vertical continuation).** We reconstruct tables *horizontally*
    (candidate-group splits, via `_precinct_groups`) AND now *vertically*: a precinct whose rows
    straddle a page break leaves its label plus any early method rows as page N's last block and the
    remaining rows as a label-less first block on page N+1. The stitch merges them when their method
    buckets are **disjoint** (a real straddle splits the four methods across the break; two separate
    precincts would overlap). `walk_page` emits a trailing label-only block so the label survives.
    No-op for documents whose precincts never straddle (Barry, Oscoda). This was the missing piece
    Calhoun exposed and Huron will lean on.
- **rows** `extract_precinct_contest`: learn sample schema → per page, read each candidate row's
  numbers at the **page-consensus count columns** and align to buckets.
- `extract(file, pages, office, context, orientation)` dispatches.

## The hard-won robustness lessons

Table conversion is **not self-consistent within a document** — the same contest's rows/columns
split or merge differently page to page (unlike our earlier assumption that a document is
uniform). Neither exact row/column indices nor exact labels are safe alone. What works:

1. **Consensus across siblings.** The document is self-consistent *per page*, so let the other
   rows/pages inform an ambiguous one:
   - `_count_columns` (rows path): the count-column positions are the ones most candidate rows
     agree on; a stray cell in one row is outvoted. (Fixed Calaveras Trump-230: a phantom `8`
     wedged mid-row.)
   - Consensus `skip_labels` (columns path): a label is a total/header only if ≥2 pages call it one
     AND it isn't a fragment of a real precinct label — drops one page's mistake (a precinct name in
     skip_labels) and a common wrap fragment (`Precinct 1`), keeps a real total. (Fixed Barry
     group-collapse.)
   - Precinct labels by consensus across a group's pages (recover a dropped-to-None first label).
2. **Checksum as an aligner, not just a check.** `_assign_methods`: when a row's numeric-cell count
   ≠ bucket count (a dropped zero component, a spurious cell), use `total == Σcomponents` to place
   the cells — one cell equal to the sum of the others (or of a leading run) is the total; the rest
   fill components left-to-right, missing → 0.
3. **Read a cell's count as its leading token** (`_cell_count`): conversion sometimes merges a count
   with its percent into one cell (`"1 100.00%"` → 1); a pure percent (`"86.32%"`) yields nothing.
4. **Whitespace/case-insensitive label matching** (`_norm`): a wrapped label splits mid-word across
   cells (`"…and T" + "ER MAAT"`).
5. **Contiguous label from the precinct column** (`_contiguous_label`): join adjacent cells until a
   gap — keeps a wrapped precinct name (`"Gettysburg"+"1"`), drops a far-column banner (CA
   `"110 … 817 of 1,056 registered voters"`).
6. **Split the party out of the candidate name** (`_split_party`): the interpreter inconsistently
   left `(DEM)` inside the name on some pages, which made group detection see two candidates and
   merged precinct-groups (Barry Castleton got Orangeville's numbers). Normalize for grouping and
   emission.
7. **Index-based contest scoping (rows path):** when several contests stack on a page their
   over/under/write-in labels repeat, so match candidate rows by the sample's `row_index`, not by
   label.

## Write-in consolidation (and the Barry saga)

Sources split write-ins to excruciating detail (named qualified write-ins, unresolved/scattered,
not-assigned, write-in totals). We **consolidate all of it into one `Write-ins` row per precinct**.
The hard part is telling an *aggregate total* apart from a *component*, because getting it wrong
either double-counts or drops votes. Two layouts:

- **total-plus-breakdown** (Adams): an explicit `Write-In Totals` column that already sums an itemized
  `Not Assigned`. Consolidated = the total; adding the breakdown would double-count.
- **components that add up** (Barry): a bare scattered `Write-in` line (the unresolved write-ins) plus
  the named qualified write-in candidates listed *after* the `Total Votes` column. These are
  **additive** — `Total Votes` totals only the columns to its left (majors + the scattered line), and
  the qualified names sit after it as extra. Castleton = scattered 6 + Sonski 2 = **8**.

How we decide, keeping language in the model:

- The **LLM sets `write_in=true`** on any write-in column/row, and **`write_in_total=true` ONLY on an
  explicit aggregate total** (`Write-In Totals` / `Total Write-Ins`) — never on a bare scattered
  `Write-in` line or a named qualified candidate.
- `_consolidate_write_in(totals, components)`: if any total is present, use it; else **sum the
  components**. This replaced an earlier numeric guess (`biggest == sum(others)` → treat as a total),
  which misfired on Barry when a scattered value happened to equal a qualified value per method
  (Thornapple P1 election-day 1 + 1 collapsed to 1).

**The saga, so it isn't re-learned the hard way.** Barry sat at 0.898 with write-ins "inflated." The
first wrong turn was trusting the `openelections-data-mi` reference CSV, which records **every** Barry
president write-in as `0` — the human transcriber dropped them. The source PDF has real write-ins
(county total 62 scattered + 4 qualified = 66), provable three ways: per-row `votes == ED+EV+AV`, the
per-precinct identity `scattered == Total Votes − Σ(8 majors)` (matched all 24), and the printed
county write-in total. Gold was rebuilt additively from the source; extractor + gold now agree at
**1.000**. Lesson: **the reference is ground truth for values a human recorded, but write-ins are the
column humans most often drop — checksum against the source before trusting a write-in of 0.**
(`tmp/barry_additive.py` rebuilt the gold; `tmp/consolidate_writeins.py` was the original curation.)

## Canonical schema (unchanged from handoff 1, plus write-in policy)

Required core, in order: `county, precinct, office, district, party, candidate, votes`. Optional
method columns, filled only when the source breaks them out, emitted sorted:
`election_day, early_voting, absentee_mail, provisional`. Rules:
- `votes` = the total, always present, primary scored value; method columns are addends.
- office = OE controlled vocabulary (inherited from `oe2d.contests`); candidate = verbatim from
  source; **write-ins = the single consolidated `Write-ins`**; party = as authored; no commas.
- A contest is `(office, district)`; multi-district counties yield one gold per district.
- Method synonyms fold to the four buckets (`In-Person`/`Election Day`→election_day;
  `Vote by Mail`/`Mail Votes`/`AV Counting Boards`/`absentee`→absentee_mail; `Early Voting`; `Provisional`).
- Pseudo-offices (`Registered Voters`, `Ballots Cast`, `Straight Party`) are their own office,
  blank candidate; the ride-along `Registered Voters` column is NOT emitted per contest.
- Non-write-in specials (`Over Votes`/`Overvotes`, `Under Votes`/`Undervotes`) stay verbatim per doc.

## Checksums (self-validation)
1. row/method: `votes == Σ(method components)` (degrades to n/a for total-only).
2. column/cross-candidate: the printed `Total Votes` column totals the columns **to its left**
   (majors + minors + the scattered `Write-in` line); qualified write-ins printed after it are
   additional. So `scattered write-in == Total Votes − Σ(named candidates)` — this is what let us
   rebuild Barry's dropped write-ins and confirm all 24 precincts.
3. county grand total: `Σprecincts == county total row` (Barry write-ins: 62 scattered + 4 qualified
   = 66).
`_assign_methods` already uses #1 as an aligner; #2/#3 are available to add as guards, and #2/#3
proved the Barry write-in rebuild.

## Metric

`oe2d/votes/metrics.py` reports two views over whole-row keys:
- **plain** F1 / IoU: each row counts once (precision, recall, and the actual FP/FN rows). A wrong
  value is both an FP and an FN, so it catches over-emission a recall-only count hides.
- **vote-weighted** F1: each row contributes by a **concave** weight `(votes + 1) ** weight_exponent`
  (default `0.5` = √). An error in a 673-vote major-party row (≈ 25.9) costs far more than one in a
  3-vote write-in (≈ 2.0) — cheap, not equal. The **+1 smoothing** is deliberate: without it a
  zero-vote row weighs 0 and a spurious/missing zero row (a phantom out-of-county precinct, a dropped
  0 row) is *invisible* to the score; with it such a row weighs 1 — small, but it registers.
  `weight_exponent` tunes it: `1.0` near-linear (small errors nearly free), toward `0` approaches the
  plain per-row count. The two views coincide only when there are no errors.

## Gold set

`oe2d-data/votes/index.jsonl` (metadata: `id, state, county, office, district, source_url, pages,
container, geometry/schema_features, checksums, candidate_context, expected_csv, …`) + one
`<county>__<contest>__expected.csv` each, all write-ins consolidated. `datasets.py`:
`load_index`, `find`, `expected_rows`, `candidate_context` (the expected-candidate prose — passes
distinct labels through, **no Python classification**), `fetch_source` (downloads to
`oe2d-data/votes/.cache/`, gitignored). Coverage: both orientations, both method layouts,
contiguous + scattered page-org, vector + scanned, district, multi-district, pseudo-offices,
specials, and now consolidated write-ins.

Gold corrections made (checksums / source cross-checks surface these): Oscoda
`Write-ins`→`Unresolved Write-In` then consolidated; Adams special-row terms to doc terms; Calaveras
`Vote by Mail`→`absentee_mail`, `La Riva` party `PF`→`PFP` (drift), specials to doc terms; Barry
comma-spaces (`Hastings,Ward`→`Hastings, Ward`, in president and straight-party) and **all 24
president write-in rows rebuilt additively from the source** (the reference CSV had zeroed them;
county-checksum 66); Barry straight-party dropped its 24 spurious `Write-ins`=0 rows (a straight-party
ticket has no write-in) and its out-of-county precincts; Adams AG + Calaveras us-house `Over
Votes`/`Under Votes`→`Overvotes`/`Undervotes` to match each county's president gold and the doc form;
Calhoun commas stripped. Expect more as we add sources — especially write-in columns dropped by the
reference, and special-row spellings that drift between contests in the same county.

## Tooling notes
- **AWS**: profile `cmpnd-mike-root`, region `us-west-2` (Bedrock). Set `AWS_PROFILE`,
  `AWS_CONFIG_FILE=./.aws/config`, `AWS_SHARED_CREDENTIALS_FILE=./.aws/credentials`, `AWS_REGION_NAME`.
- **Cmpnd tracing**: `cmpnd` is an optional `[tracing]` extra (`pip install -e .[tracing]`); key from
  `.env` (`CMPND_API_KEY`). Tag `oe2d-votes`. It was silently absent at first — `_instrument`'s soft
  import hid it.
- **DSPy cache**: on-disk cache persists across code edits and will replay stale interpretations.
  For true behavior while iterating: `dspy.configure_cache(enable_disk_cache=False,
  enable_memory_cache=False)`.
- **Tests**: `oe2d/tests/votes/` — hermetic (no LM/source), cover the walker, both metric views, and
  every robustness helper (`_assign_methods`, `_count_columns`, `_cell_count`, `_contiguous_label`,
  `_split_party`, `_consolidate_write_in`). 19 pass.

## Design notes — pinned topics (revisit with scan evidence, not on spec)

Two related ideas came up while planning the scanned read path. Decision: **do not build either up
front; look at a real scan first**, because the amount of machinery needed is sized to how noisy the
OCR actually is, which we can't guess.

1. **Columns from row-vs-row agreement.** We already do the seed of this: `_count_columns` (rows
   path) finds count-column positions by which columns the *most* candidate rows agree on, outvoting
   a stray cell. The columns path doesn't yet — it takes columns from reader geometry (exact vector
   `word.x0`), which won't survive OCR jitter/skew. The generalization for scans: deskew, project
   every numeric token's x-center across *all* rows into a histogram, and the peaks are the columns
   — the table self-calibrates, and more rows means sharper peaks. Then snap each row's tokens to the
   nearest peak, and use checksums (row total == Σmethods, printed Total Votes == Σcandidates,
   Σprecincts == county total) as the disambiguator when geometry is a coin-flip. Sharp version:
   **distrust the OCR vendor's own table cells; take its words+boxes and find columns ourselves.**

2. **Perimeter, not the full N×M page grid.** The columns path interprets *every* page; the rows path
   already interprets one and applies it. A document is a grid of pages: M across (candidate-group
   splits) × N down (precinct continuations). Structure lives on the perimeter — one horizontal
   traverse (≈M pages, one per candidate-group-type) teaches all candidate columns; the vertical
   structure is mostly deterministic. The (N−1)(M−1) interior is pure fill: known columns + Topic-1
   consensus + checksums, no LLM. This reconciles with "re-interpret every page" because only the
   *cell level* drifts within a document (handled by the deterministic helpers), not the *structure*.
   Concretely the win is "**once per distinct page-type, not once per page**" (dozens of calls → a
   handful), with a checksum-triggered re-interpret for a page that doesn't reconcile. Folds in the
   header-slice idea (send only the perimeter pages' header band + label column).

These compose: the LLM reads the perimeter to *name* columns/rows (terminology); deterministic
row-consensus + checksums *fill and validate* the interior (numbers) — the "model decides structure,
code moves digits" principle lifted to the page-grid level.

## Read mechanics vs content structure (orthogonal — keep them apart)

A hard-won lesson from Huron. Two independent axes:
- **Read mechanics** (how pixels become cells): ruled vector → `source_table`; rotated text-aligned →
  `read_rotated_grid`; ruled scan → `read_scanned_tables` (TABLES); borderless scan → `read_scanned_grid`
  (cheap words). Selected by `ReadStrategy` (a `typing.Literal`, not a bare string) and page detection.
- **Content structure** (what the table means): candidate orientation (columns/rows); flat (one row per
  precinct, one total per candidate) vs vote-method sub-rows. Decided from the interpreted content.

Do NOT infer content from the reader or teach the *shared* sub-row interpreter/`walk_page` about flat —
that destabilized Barry (the interpreter occasionally dropped `method_labels`, firing a flat branch and
truncating a wrapped label). Flat lives entirely in `extract_scanned_tables`, which uses the interpreter
ONLY to map header columns → candidates and reads each precinct row's candidate columns directly.

## Next steps (in rough priority)
1. **Wrap the whole program in ONE composite `dspy.Module` (GEPA-ready + Cmpnd-legible).** Today
   `oe2d.votes` is loose functions (`extract`, `extract_contest`, `extract_precinct_contest`,
   `extract_scanned_tables`) that each call `build_interpreter()` / `build_precinct_interpreter()` to
   spin up a bare `dspy.Predict` per call. Mirror `oe2d.pages.PageAnalyzer` and
   `oe2d.contests.ContestLocator`: a single `dspy.Module` (e.g. `VoteExtractor`) IS the program, so
   Cmpnd sees one traced read→interpret→stitch flow and GEPA can evolve every prompt in it.
   - **`__init__`** constructs the NAMED inner predictors that GEPA optimizes -- both interpreters:
     `self.interpret_columns = dspy.Predict(InterpretResultsPage)` and
     `self.interpret_rows = dspy.Predict(InterpretPrecinctPage)`. GEPA evolves each named predictor's
     signature-docstring instruction independently (guidance already lives in the docstrings, not the
     pydantic Field descriptions -- that groundwork is done; see the Interpret section). Naming them as
     module attributes is what makes them discoverable/optimizable.
   - **`forward(file_path, pages, office, candidate_context, orientation, read_strategy) ->
     dspy.Prediction(rows=...)`** runs the current `extract` dispatch inside the module: read
     (deterministic reader dispatch), interpret (the named predictors), walk/stitch/consensus, and
     `votes_to_rows`. The deterministic parts (readers, `walk_page`, `_precinct_groups`, cross-page
     stitch, `_count_columns`/`_assign_methods`/`_consolidate_write_in`, all-zero drop) stay in
     `forward`/helpers -- traced but OUTSIDE the GEPA objective, exactly like `PageAnalyzer`'s skew and
     `ContestLocator`'s `_locate_pages`/tools. This also fixes the current per-call `build_interpreter`
     churn: the predictors live on the module and are shared.
   - **`build_extractor()`** replaces `build_interpreter`/`build_precinct_interpreter`: construct the
     module, `load(OPTIMIZED_MODEL_PATH)` when present (the artifact governs prompt AND lm -- see
     [[lm-artifact-authority]]), else `set_lm(...)`. `_instrument()` (Cmpnd) attaches here, once.
   - **`metrics.py` for GEPA**: adapt the existing whole-row weighted-F1 `score` to return a
     `dspy.Prediction(score=weighted_f1, feedback=<prose>)` like `oe2d.pages.metrics.score_page` --
     GEPA reflects on the FEEDBACK TEXT, so the prose must name what was wrong (the false-negative rows
     it missed and the false-positive rows it invented, WITH their vote magnitudes so the reflection
     learns the weighted priority: a wrong 600-vote row matters, a spurious 0-vote row barely). The
     metric already returns `false_positives`/`false_negatives`; this is mostly formatting them.
   - **`optimize.py` / `evaluate.py`** mirroring `oe2d.pages`: `build_program()` returns
     `VoteExtractor`; `teleprompt.GEPA(metric=score, reflection_lm=..., ...).compile(program, trainset,
     valset)`; `optimized.save(path)`. `evaluate.py` runs the program over the whole gold and reports
     plain + weighted F1 (folds in the long-standing "score the whole gold set" item). `datasets.py`
     (exists) yields `dspy.Example`s with inputs marked (file, pages, office, context, orientation,
     read_strategy) and the gold rows.
   - Watch: GEPA stringifies example inputs into the reflection prompt; our inputs are text (grids,
     not images), so no `MultiModalInstructionProposer` needed -- but keep the reflection prompt small
     (don't stuff whole page grids into `dspy.Example` fields the proposer will echo).
2. **Wire `oe2d.pages` (image VLM) for dispatch** — the whole gold set now passes, but reader/content
   dispatch is carried in the gold (`geometry.candidate_orientation`, `read_strategy`) and detected
   ad-hoc. Replace with one PageAnalyzer pass on a sample page returning skew + `ruled_table` (→ TABLES
   vs cheap/rotated read) + orientation. Confirm each choice with checksums (column totals vs printed
   Total row, etc.). Remaining known Python markers to migrate to the LLM/checksums once `pages` is
   wired: the flat-path grand-total skip (`_norm in ('total','totals')`), which the flat anchor can't
   see on a continuation.
3. **Header-slice interpretation (LLM cost)** — today we send every cell of every page to the
   interpreter, numbers included, though it only needs structure. Rows path already interprets one
   sample page; trimming its prompt to the header region + one precinct block is safe. Columns path
   is the real lever (it interprets *every* page — Barry = 15 calls): prototype interpreting from a
   header + first-block slice, and/or interpret the first page fully then reuse its schema on
   siblings, falling back to a full read only when a checksum fails.
4. **`evaluate.py` / `optimize.py`** mirroring `pages`/`contests` — a CLI to score the whole gold set
   (report plain + weighted F1) and a GEPA loop over the interpreters, so we measure and improve
   systematically instead of ad-hoc runs. All interpreter guidance now lives in the signature
   docstrings, so GEPA can evolve it (see the Interpret section).
5. **Wire `oe2d.pages` for orientation** — currently passed in / read from gold geometry; it should
   come from the image-based analyzer on a sample page.
6. **Add the remaining coverage gaps** — a ballot measure (Yes/No), a State House/Senate split.
