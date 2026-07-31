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
| calaveras president | rows | **1.000** | |
| barry president | columns | **1.000** | was 0.898 — see the write-in section below |
| calhoun ×3, barry straight-party, calaveras us-house, adams AG | — | untested | should run; some need `pages` filled |
| gogebic, huron ×4 | — | blocked | **scanned** — need a Textract read path |

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
- **Scanned** → **Textract (not built yet)**. The three untracked repo-root prototypes
  (`pdf2excel.py`, `stitch-textract-results.py`, `prepare-openelections-csv.py`) are the starting
  point. This blocks the 5 scanned examples.

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

### 3. Stitch + emit (deterministic)
- **columns** `extract_contest`: interpret each page → `walk_page` (schema-driven blocks) →
  `_precinct_groups` (a repeated candidate starts a new group) → align candidate-pages within a
  group and accumulate. `votes_to_rows` → canonical CSV.
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
- **vote-weighted** F1: each row contributes by a **concave** weight `votes ** weight_exponent`
  (default `0.5` = √votes). An error in a 673-vote major-party row (√ ≈ 25.9) costs far more than one
  in a 3-vote write-in (√ ≈ 1.7) — ~15×, not 224× (linear) and not equal. `weight_exponent` tunes it:
  `1.0` linear (write-ins nearly free), toward `0` approaches the plain per-row count. Rationale: a
  mistake in a party row is catastrophic; a small write-in miss should be cheaper, not free. The two
  views coincide only when there are no errors.

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
comma-spaces (`Hastings,Ward`→`Hastings, Ward`) and **all 24 president write-in rows rebuilt
additively from the source** (the reference CSV had zeroed them; county-checksum 66); Calhoun commas
stripped. Expect more as we add sources — especially write-in columns dropped by the reference.

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
  `_split_party`, `_consolidate_write_in`). 18 pass.

## Next steps (in rough priority)
1. **Header-slice interpretation (LLM cost)** — today we send every cell of every page to the
   interpreter, numbers included, though it only needs structure. Rows path already interprets one
   sample page; trimming its prompt to the header region + one precinct block is safe. Columns path
   is the real lever (it interprets *every* page — Barry = 15 calls): prototype interpreting from a
   header + first-block slice, and/or interpret the first page fully then reuse its schema on
   siblings, falling back to a full read only when a checksum fails.
2. **Textract read path** — unblocks the 5 scanned examples (Gogebic, Huron ×4). Wire the prototype
   scripts; deskew per the settled `oe2d.pages` notes.
3. **Fill `pages` and run the untested examples** — Calhoun ×3, Barry straight-party, Calaveras
   us-house, Adams AG (some `index.jsonl` rows still have `pages: null`; scan each source for the
   contest title). Watch for reference-dropped write-ins as in Barry.
4. **`evaluate.py` / `optimize.py`** mirroring `pages`/`contests` — a CLI to score the whole gold set
   (report plain + weighted F1) and a GEPA loop over the interpreters, so we measure and improve
   systematically instead of ad-hoc runs.
5. **Wire `oe2d.pages` for orientation** — currently passed in / read from gold geometry; it should
   come from the image-based analyzer on a sample page.
6. **Add the remaining coverage gaps** — a ballot measure (Yes/No), a State House/Senate split.
