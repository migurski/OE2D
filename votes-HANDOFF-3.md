# oe2d.votes — handoff 3

Continues `votes-HANDOFF-2.md` (which covered the composite `VoteExtractor` module and the
pages-driven dispatch). This doc covers the **training-data expansion** now in progress and the
pipeline changes it forced. Read HANDOFF-2 for the architecture; read this to pick up the current work.

## Goal

Expand the gold set with **new kinds of precinct-level PDFs** (scanned, vector, different vendor
layouts) to build confidence that the extractor generalizes — and fix the gaps the new data exposes.
Focus is **PDFs, precinct-level results** (spreadsheets/CSVs are an easy later expansion — they're
already tables and need no stitching). Ground truth is the human-transcribed OpenElections **results**
CSVs; our job is to reproduce them.

## Environment / operational notes (READ FIRST)

- **AWS**: profile `cmpnd-mike-root` (account 979023696598), region `us-west-2`. Everything — Textract
  (boto3), the Bedrock Sonnet interpreter, and the Bedrock Maverick pages VLM — uses the **ambient
  `AWS_PROFILE`** (no hardcoded profile). Every run must export:
  `AWS_CONFIG_FILE=./.aws/config AWS_SHARED_CREDENTIALS_FILE=./.aws/credentials AWS_PROFILE=cmpnd-mike-root AWS_REGION_NAME=us-west-2`.
- **Fireworks (Kimi K2) is SUSPENDED** (billing). `oe2d.pages` inference was switched to Bedrock Llama
  4 Maverick — `pages.LM_LLAMA4_MAVERICK` = `bedrock/us.meta.llama4-maverick-17b-instruct-v1:0`, used
  by `build_analyzer()`. `LM_KIMI_K2P7` stays as the `oe2d.pages.optimize` training default only. If
  Fireworks comes back, either is fine; Maverick baselined ~99% orientation and reads `ruled_table`
  well. (Saved older Bedrock pages programs exist as untracked `pages-{maverick,qwen,scout,nova}.json`
  but predate the `ruled_table` field, so don't `load()` them — run the current signature stock.)
- **Textract cost + cache** (the expensive dependency):
  - Modes: cheap `DetectDocumentText` (~$0.0015/pg) vs `AnalyzeDocument TABLES` (~$0.015/pg, 10x).
  - **Cache**: `./.cache/textract/` (caller cwd, gitignored). Key is **content-addressed** —
    `sha1(file BYTES + page + mode + render-DPI)` via `_file_digest` — so the same source at any path
    hits one entry (fixed a real bug: it was `abspath`-keyed, and `fetch_source` made 4 copies of a
    shared PDF → same page paid multiple times). Re-runs/re-evals are **free**.
  - **Accounting**: `votes.textract_usage()` → `{calls, usd}` (paid calls only); `_textract_blocks`
    logs each paid call with running spend; `oe2d-votes-evaluate` prints a per-run spend line.
  - `datasets.fetch_source` now names downloads by source-URL hash (one copy per source).

## Where things live

- **New-kind source PDFs**: `tmp/new-kinds/*.pdf` (host-visible scratch, gitignored). Named
  `scanned--/vector--/robustness--/optional--<county>-<state>.pdf`.
- **Results CSVs (ground truth)**: `openelections-data-{ca,mi,pa}/{year}/counties/{YYYYMMDD}__{st}__general__{county}__precinct.csv`
  (raw.githubusercontent). Downloaded copies during this session are in the session scratchpad
  `.../scratchpad/results/*.csv`. Fetch fresh with curl if the scratchpad is gone.
- **Gold**: `oe2d-data/votes/index.jsonl` (now **20 contests**) + one `<county>__<contest>__expected.csv` each.

## The approved batch (user-chosen contests)

All precinct-level PDFs, counties not already in gold. **CA has NO 2024 results transcribed**, so any
CA file must use its **2020** source+results (Plumas/Mono/Nevada are all 2020).

| File (`tmp/new-kinds/`) | kind | contests | status |
|---|---|---|---|
| `vector--branch-mi.pdf` | borderless vector, flat multi-contest | Straight Party, US Senate, US House, President | **DONE, 1.000** (committed) |
| `scanned--columbia-pa.pdf` | scanned, ruled, count+% columns | US House, US Senate, President | **President works (42/42)**; Senate/House blocked (see below) |
| `scanned--plumas-ca.pdf` (2020) | scanned Hart SOV | President, US House | not started |
| `scanned--montmorency-mi.pdf` | scanned, degraded, ClearBallot sub-rows + rotated | Straight Party, US Senate, US House, President | not started (hardest) |
| `vector--missaukee-mi.pdf` | vector multi-contest mega-grid | all four races on p1 | not started |
| `robustness--nevada-ca.pdf` | ClearBallot outside MI — **use 2020 source** | (completeness) | not started |
| `robustness--bay-mi.pdf` | Electionware outside PA | (completeness) | not started |
| `optional--ontonagon-mi.pdf` | scanned ClearBallot flat | (completeness) | not started |
| `optional--mono-ca.pdf` (2020) | Hart SOV rows + **ballot measures** (#4 gap) | (completeness) | not started |

## The gold-build process (validated on Branch)

Per contest: 1) pull the human results CSV; 2) find the contest's page range **including its
county-total row** (needed for the reconcile checksum); 3) run the extractor and **diff against the
results** — the extractor reveals the source's write-in/method structure, results give authoritative
numbers; 4) build the expected CSV (consolidate write-ins → one `Write-ins` row, drop all-zero
precincts, canonical offices) + index entry; 5) verify 1.000 with `oe2d-votes-evaluate --only <id>`.

- **Finding pages**: vector → grep the text layer for the contest title. Scanned → cheap Textract
  text per page (`votes._textract_words`) and grep titles (one-time, cached). Columbia pages found this
  way: President 1-2, US Senate 3-4, US House 11-12, AG 5-6.
- **Office strings** (canonical, from existing gold): `President`, `U.S. Senate`, `U.S. House` (with
  `district`), `Straight Party`. Results CSVs vary (`Straight Ticket` → `Straight Party`). Normalize.
- **Method columns**: results order is `votes, early_voting, election_day, provisional, mail`;
  canonical is `votes, election_day, early_voting, absentee_mail, provisional` (`mail`→`absentee_mail`).
  Flat contests (Branch, Columbia, Plumas) are votes-only.
- **Write-ins**: results represent them variously — a single `Write-Ins` row (Columbia), named
  qualified rows like `... Qualified Write-In` (Montmorency, watch DUPLICATE rows), or a named
  candidate NOT labeled write-in (Branch's `Peter Sonski`, only the source marks it WRITE-IN). The
  extractor consolidates the SOURCE's write-in columns into one `Write-ins`; the gold must sum the
  matching results rows. Identify write-ins from the source / by diffing extractor vs results.
- **read_strategy** in the index: `flat_tables` for a borderless-vector flat contest (Branch);
  `ruled_scan` (== flat path for scans) for a ruled scan; `auto` otherwise. `orientation` in
  `geometry.candidate_orientation`. `district` a string or `''`.
- **Build script pattern**: `tmp/`-style one-off that reads the results CSV, filters+consolidates+
  drops-all-zero, writes CANON_COLUMNS CSVs to `oe2d-data/votes/`, and appends index entries.
  `datasets.candidate_context` auto-derives from the expected CSV (don't hand-maintain it).

## Pipeline changes made this session (all committed unless noted)

1. **`flat_tables` read strategy + `read_flat_tables`** (`5d1bdb1`): the flat multi-contest extractor
   (`_extract_scanned_tables`) reads its tables via **Textract TABLES for BOTH scanned and vector**
   pages. Branch is content-identical to Huron (flat, candidates-as-columns, several contests scoped
   per page) but borderless vector; Textract's table detection segments/reads it cleanly where
   pdfplumber geometry can't. `ruled_scan` is the scan-only spelling of the same path. `read_page_grid`
   also falls back to `read_text_grid` for a borderless vector page (safe; source_table/rotated run
   first). Detection still returns `auto` for vector, so **the gold carries `read_strategy=flat_tables`**
   explicitly (as Huron carries `ruled_scan`); teaching `detect_dispatch` to pick flat for vector is
   future work.
2. **count+percent (colspan-2) normalization** (`baf9922`): `_normalize_table_columns` strips a
   candidate's percent column (pure-percent cells) and all-empty spacer columns from every flat table;
   the flat extractor reads values with `_cell_count` (leading token) so a merged `"122 21.55%"` → 122.
   This is a **general, important** pattern (user-flagged, not unique to Columbia). Got Columbia
   president to 42/42.

## THE OPEN PROBLEM (do this next, carefully)

**Columbia US Senate / US House don't read**, and it's the crux of the count+% family. Textract
segments the SAME contest with **different column counts page to page** (Senate p3 = 9 cols, p4 = 12
cols — p4 has extra split candidate cells + spacer columns that column-normalization doesn't fully
reconcile). The current **single-anchor + column-count scoping** (`_extract_scanned_tables`) learns
one schema from the best-header-match table and reads only tables of that exact column count, so it
**drops the mismatched page** — losing that page's precincts AND the county-total row it needs to
reconcile, so the (partial) read then fails the checksum and falls back to the cheap reader → 0.

- The **right fix** is to **interpret each header-bearing table independently** (a header-less
  continuation reuses the prior schema by column-count match). A prototype of this got ALL THREE
  Columbia contests to 42/42 — **but it regressed Huron president and Branch president/US Senate to 0
  and was REVERTED** (`_extract_scanned_tables` is back to single-anchor). Before reintroducing it:
  understand WHY it broke them — almost certainly the `header_match(grid) >= 2` gate (it only looked
  at `grid[0]`; Columbia president's candidate names are on row 1 under a `PRESIDENTIAL ELECTORS` title
  row, so I widened it to scan `grid[:3]` — that same widening or the gate likely excluded Huron/Branch
  tables whose header is shaped differently). Redo it with a Huron+Branch+Columbia regression harness
  in the loop, not after.
- Also pending: **Columbia president write-in total is off by 4** (extractor 133 vs results 129) —
  reconcile per precinct before writing president gold (is it a source write-in the results dropped, à
  la Barry, or an over-read?).

## Status snapshot

- **Gold: 20 contests, all 1.000** (16 original + Branch 4). `oe2d-votes-evaluate` (no args) scores the
  whole set; caching makes re-runs free.
- Tests: `oe2d/tests/votes/` 20 pass; `oe2d/tests/pages/` pass (after the `.DS_Store`/PNG-pick fix).
- Columbia is the only partial file; the other 7 batch files are not started.

## Next steps (priority)

1. **Fix the multi-page column-count-inconsistency** (per-table interpretation, regression-tested) →
   finish Columbia (Senate/House), reconcile president write-ins, build+commit Columbia gold.
2. **Work the batch** in order of read-path novelty: Plumas (scan Hart), Ontonagon (scan flat),
   Montmorency (degraded scan — real stress test), Missaukee (mega-grid), then robustness (Nevada, Bay)
   and Mono (ballot measures, #4 coverage gap). Follow the validated build process; verify each 1.000.
3. **Then** the deferred items from HANDOFF-2 #3-4: header-slice interpretation (LLM cost), teaching
   `detect_dispatch` to pick `flat_tables` for vector, GEPA optimization once there's error signal.
4. Cheap→TABLES **escalation** (cost): try the cheap read, checksum, escalate to TABLES only on
   reconcile failure — pays the 10x only when needed. Bounded for now by the content-addressed cache.
