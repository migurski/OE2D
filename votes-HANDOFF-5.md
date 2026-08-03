# oe2d.votes — handoff 5

Continues `votes-HANDOFF-4.md`. Read HANDOFF-4 for the full architecture (read strategies, geometry
alignment, conventions, environment/ops, code map) — it is still accurate except where noted below.
This doc covers **what changed since HANDOFF-4**; its headline task (the Missaukee mega-grid) is DONE.
The next substantial task, **autonomous dispatch (#3), is now also DONE** (2026-08-03) — the page VLM
learned the read shape and `detect_dispatch` routes on it; see `pages-HANDOFF-2.md` for that side and
the updated Next-steps section here for what remains.

## Status snapshot (current)

- **Gold: 50 contests, macro wF1=1.000 (F1=1.000), ALL cold-cache robust.** One pre-existing
  non-1.000: `columbia-us-house` F1=0.996 (a single zero-vote write-in row; wF1=1.000).
- **Tests: `oe2d/tests/votes/` 67 pass.** Score everything with `oe2d-votes-evaluate`.
- **The Missaukee mega-grid task below is now DONE** (all 5 Missaukee contests in gold). Kept below as
  the design record.
- **Autonomous dispatch (#3) DONE.** `detect_dispatch` now proposes the full read strategy from the
  page VLM's read-shape fields. On the 50-contest gold: `--detect` orientation **92%→100%**,
  read_strategy **46%→74%**; `--detected` (end-to-end, image-driven routing) macro **wF1 0.953 /
  F1 0.969** (gold-dispatch is 1.000). Every newly-detected strategy reads at 1.000 (missaukee
  flat_multi, nevada report_lines_methods, mono report_lines_total, branch flat_tables). Shipped a
  Qwen3-VL analyzer artifact. See the reframed step 1 below for the residual gap.
- Counties/offices now covered (see `datasets.load_index()`): Adams(2) Barry(2) Bay(4) Branch(4)
  Calaveras(2) Calhoun(3) Columbia(3) Gogebic(1) Huron(4) Missaukee(5) Mono(3) Montmorency(4)
  Nevada(5) Ontonagon(4) Oscoda(2) Plumas(2).

## Next steps (overall list, reconciled across HANDOFF-2..5)

The whole deferred batch, the mega-grid, the State House/Senate split, AND autonomous dispatch (#3)
are DONE. What remains:

1. **Close the detected-dispatch residual (the last 0.047 of `--detected`).** #3 shipped at macro
   wF1 0.953; the gap to the gold-dispatch 1.000 is entirely **single-page-undetectable shapes** plus
   one **narrow VLM risk**:
   - **`flat_grouped`** (plumas/ontonagon, ~5 contests): candidate columns are split ACROSS pages, so
     one page can't reveal it — `detect_dispatch` proposes `ruled_scan`, which fails reconcile and
     falls back to `auto`; `auto` reads it only partially. Fix needs a cheap **multi-page probe** (do
     later pages repeat the same precinct labels under DIFFERENT candidate headers?) — see
     pages-HANDOFF-2 "the residual: flat_grouped".
   - **`ruled_columns` vs `auto`** (montmorency president/senate): a scanned method-sub-row page is
     visually identical to Gogebic, which needs `auto` — so we can't safely propose `ruled_columns`
     (it has no reconcile fallback and would break Gogebic). Fix needs a **reconcile-protected
     `ruled_columns`** (capture the printed county total the SOVC drops as a skip block, then confirm
     like the flat family) — then it could be proposed freely and self-correct.
   - **`value_columns` tail risk** (see the risk discussion, 2026-08-03): `value_columns` routes ONLY
     the rows+per_precinct family, and its low headline accuracy (67%) is dominated by non-routing
     columns/county pages; on the routing pages it was correct for every gold contest. The one
     unprotected direction is a genuine Dominion report the VLM calls plain `methods` → routed to
     `auto` with no fallback (the reverse — Electionware-with-percent mislabeled → `report_lines_*` →
     empty → `auto` — is already caught by the empty-guard). Hardening options if a novel source hits
     it: tighten the `value_columns` field description (a lone total with a share-percent is
     `total_only`, not `methods_with_percent`), or a **2-of-3 multi-page vote** on the detection
     (currently ONE VLM call per contest on `pages[0]`, temperature 0 — a real ensemble would sample
     DIFFERENT pages). Not needed on current gold.
   The `--read-strategy` CLI override remains the escape hatch for any source these miss.
2. **Tier-2 breadth fills** (cheap, existing machinery): missing offices in covered counties — Gogebic
   (Straight Party / US Senate / US House), Oscoda (Straight Party / US Senate), Barry (US Senate / US
   House), Calhoun (Straight Party / US Senate), Adams (US Senate / US House), Calaveras (State Senate /
   Assembly).
3. **A ballot-measure (Yes/No) contest** — the one contest *shape* not in gold (all 50 are candidate
   races). From a county whose results include measures.
4. **Cost**: header-slice interpretation (send the interpreter only header + one block, not every
   numeric cell of every page); cheap→TABLES escalation (try cheap, checksum, pay for TABLES only on
   reconcile failure).
5. **Robustness/future**: dual-DPI reconciliation (run a pair of render DPIs, diff, reconcile against
   source); the fuller all-zero roster (document-level precinct roster + name-based out-of-jurisdiction
   filter replacing the zero-sum drop) — deferred until a county needs it.
6. **`columbia-us-house` F1=0.996** — one zero-vote write-in row the flat read drops (wF1=1.000).
7. **GEPA** pass over the two interpreter predictors (harness `optimize.py` exists, never run) — worth
   it since the recurring cold-flaky moments (Mono write-in inclusion, mega-grid non-determinism before
   segmentation) were interpreter prompt-sensitivity, which GEPA hardens.

## What changed since HANDOFF-4

1. **Bay Straight Party + the all-zero drop is now orientation/strategy-scoped.**
   `votes_to_rows(..., drop_all_zero=)`; `forward` sets `keep_all_zero = orientation == 'rows' and not
   read_strategy.startswith('report_lines')`. A per-precinct ROWS report keeps an all-zero precinct
   that genuinely cast ballots (Bay Midland P2 straight party). Flat/columns and the Dominion report
   readers still drop all-zero (out-of-county placeholder rows / phantom precincts).

2. **Two Dominion per-precinct report readers** (`read_report_blocks(path, page, grammar)`), reached by
   two read strategies (NOT a text heuristic — the grammar rides in `read_strategy` the way every read
   mechanic does; the gold record states it, oe2d.pages' VLM is where it would later be proposed):
   - **`report_lines_methods`** — "Precinct Results Report" (Nevada CA). One row per choice; a
     count+percent PAIR per method (Absentee/Early/Election Day/Provisional/Total); running mates wrap
     to a bare line. Full method breakdown.
   - **`report_lines_total`** — "Election Summary Report" (Mono CA). A single Total per choice; the
     name wraps AROUND a floating party+value line ("JOSEPH BIDEN/KAMALA" / "DEM 210" / "HARRIS").
     Votes-only. The precinct name lives only on the report's COVER page, so the reader walks back to
     the nearest preceding page that carries "PRECINCT #..".
   Both reconstruct a clean per-choice grid from word geometry (`_word_lines` + column x-centres) and
   feed the existing precinct-page schema path via `_extract_report_contest` (picks the block naming
   the most expected candidates) → `_extract_precinct_contest(..., choices_only=True)`. `choices_only`
   backfills any count-bearing grid row the interpreter dropped as a write-in, so write-in inclusion is
   deterministic (was flaky). `_word_lines` hermetically tested (`test_votes_report_reader.py`).

3. **Nevada CA 2020 + Mono CA 2020 gold** (the tmp `robustness--nevada-ca.pdf` is 2024 with no 2024
   results; used the 2020 precinct source in `tmp/new-kinds-2020/`, blob-verified). Nevada President
   is **source-correct and deliberately diverges** from reference data-entry errors (cp33 Trump total
   1074 not 1076; cp66 whole precinct un-shifted — the reference slid parties down a row, blanking
   DEM) — Mike approved from the source pages. Both keep 3 single-vote precincts (CP75/CP84/CP96) the
   reference suppressed (no non-hardcoded rule separates them from kept single-vote CP90; source-
   faithful wins — Mike: "never hardcode anything"). Nevada also fills party where the reference left
   it blank on down-ballot rows.

4. **Missaukee US Senate PULLED** (was committed at warm-cache 1.000). LESSON, now firm: **cold-
   validate every new gold with `dspy.LM(..., cache=False)` BEFORE committing** — a warm-cache 1.000
   hid its non-deterministic mega-grid scoping (reads 0 rows cold). See task below.

5. **Caches consolidated** under `./oe2d-cache/` (cwd), subdirs `textract/` and `dspy/`.
   `votes.configure_cache()` points DSPy there at import (DSPY_CACHEDIR still overrides);
   `votes.TEXTRACT_CACHE_DIR = oe2d-cache/textract`. HANDOFF-4's "`.cache/textract`" is now stale.

## ~~THE TASK~~ DONE: Missaukee mega-grid column-scoping

**Solved** (commit "Read the Missaukee mega-grid…"): `segment_multi_grid` cuts the wide table into one
flat sub-table per contest (party-header `Dem`-restart marks each block; the block before the first
`Dem` is Straight Party), `_extract_multi_grid` / read_strategy `flat_multi` picks the block by
candidate name (name hit weighted ×100 over party-abbrev so Straight Party doesn't shadow a 2-candidate
contest) and feeds one sub-table to `scope_flat_tables`. Cold-deterministic. All 5 Missaukee contests
at 1.000. Hermetic tests in `test_votes_multi_grid.py`. The analysis below is the design record.

Source: `tmp/new-kinds/vector--missaukee-mi.pdf` (7 pp vector, 18 precincts, MI votes-only). Results:
`.../2024/counties/20241105__mi__general__missaukee__precinct.csv` — Straight Party (126 rows),
President (162), U.S. Senate (108), U.S. House (72), State House (36). Only **President** is in gold.

### The structure (corrects HANDOFF-4 — the candidate NAMES ARE in the grid)

`read_flat_tables(..., page=1)` returns ONE Textract table, **26 rows × 37 columns**, holding **five
partisan contests side-by-side** plus leading stat columns. The rows that matter:

- **r1 group banners** (word-columns): `Straight`(c5) `Party`(c7) · `Presidential`(c16) ·
  `Congressional`(c28) · `Legislative`(c35).
- **r2 contest titles**: `President/Vice President (1)` (c15-17, x≈0.48-0.52) · `United States Senator
  (1)` (c25-28, x≈0.71-0.77) · `Rep Congress 4th (1)` (c31-34, x≈0.84-0.91) · `103rd Rep (1)`
  (c35-36, x≈0.93-0.96).
- **r3 party-header** — the first-party label `Dem` RESTARTS at each partisan block's first column:
  **c10** (President: Dem Rep Lib UStx Grn NL No-Party-Aff Write-In, c10-18), **c22** (Senate: Dem Rep
  Lib UStx Grn NL Write-In, c22-28), **c31** (US House: Dem Rep Lib Grn, c31-34), **c35** (State House:
  Dem Rep, c35-36). Straight Party (c3-9) has an EMPTY r3 — its "candidates" ARE the party names.
- **r4 candidate names** (the real header): c0 Registered Voters, c1 Poll Book, c2 Township/City
  (precinct), c3-9 `Dem Rep Lib UStx Grn Wk NL` (Straight Party choices), c10-17 President
  (Harris/Walz … West/Abdulla), c18-21 President write-ins (J. Bowman, C. De la Cruz, C. Fox,
  P. Sonski), c22-27 Senate (Slotkin Rogers Solis-Mullen Stein Marsh Dern), c28-30 Senate write-ins
  (Chapman Irvine Willis), c31-34 US House (Barr Bergman Gale Hakola), c35-36 State House (Wojey Borton).
- **data rows r5..**: one per precinct — `RegVoters | PollBook | <precinct> | <counts across all 37
  columns>`. The **LAST row is the county `Total`** (`13010 | 9194 | Total | 945 | 4714 | …`) — a
  reconciliation target.

### Why the current read fails (and only President passes)

`_extract_scanned_tables` → `scope_flat_tables` treats this ONE 37-col table as ONE flat contest and
aligns the contest's `candidate_context` names among ALL 37 columns:

- **President passes**: 8 distinctive names (Harris/Trump/…) anchor the correct contiguous block
  (c10-17) and Σ precincts reconciles to the Total row.
- **U.S. Senate fails cold** (`got=0`): "Stein" collides — President c14 (Jill Stein) AND Senate c25
  (Dave Stein) — and with fewer distinctive anchors the aligner grabs wrong columns, so Σ ≠ Total →
  the read does not reconcile → falls back to the `auto` reader → "No column structure found" → 0 rows.
  Non-deterministic across LLM cache state (warm once looked fine; cold is 0).
- **U.S. House fails** (~67 by-party diffs): grabs the wrong 4-of-37 columns.
- **Straight Party fails** ("No column structure found"): its choices are PARTY names in r4 (c3-9) with
  an empty r3, which the name-aligner does not handle.

The root cause is scope: aligning among all 37 columns is ambiguous. Every contest's columns are a
**contiguous block**, and the block boundaries are printed deterministically.

### Proposed approach (implement + verify cold)

Add a read path for a **single wide multi-contest grid** — e.g. a `flat_multi` strategy, or a
pre-segmentation step inside `_extract_scanned_tables`. Sketch:

1. **Segment the 37 columns into per-contest blocks** deterministically, from the printed structure:
   the r3 first-party (`Dem`) restarts mark each partisan block's start (c10/c22/c31/c35); the r2
   title x-ranges and r1 banners corroborate. Straight Party is the block left of the first `Dem`
   (c3-9), keyed off its r1 "Straight Party" banner, with party-name choices.
2. **Attach each block's write-in columns** (the r3 `Write-In` label / the trailing name columns before
   the next block's `Dem`: President c18-21, Senate c28-30).
3. **For the target contest, restrict alignment to its block only** — inside one block "Stein" is
   unambiguous and the existing `_align_columns` (names + geometry) works on a handful of columns.
4. **Read + reconcile**: precinct label c2, per-precinct counts in the block's columns, party from r3
   (or the party-name choice for Straight Party), consolidate write-ins, and reconcile Σ precincts ==
   the `Total` row per column.
5. Keep it **deterministic** — the segmentation is pure geometry/labels (Python moving digits); if the
   LLM is used at all it should only map method/party labels, not choose columns. Verify with
   `cache=False` and only commit if cold-robust.

Verification: results CSV has all five contests (18 precincts). Expect source-faithful divergences of
the same kinds seen elsewhere (write-in consolidation vs per-candidate reference rows; possibly party
labels). Compare by (precinct, party) for named + write-in SUM per precinct, as the other builders do.
Re-add **U.S. Senate** to gold once it reads cold; add **Straight Party, U.S. House, State House** too.

## Then the cheap breadth work (Tier 2 — same machinery, low new signal)

Fill missing offices in counties we already read, using each county's existing strategy: Gogebic
(Straight Party / US Senate / US House), Oscoda (Straight Party / US Senate), Barry (US Senate / US
House), Calhoun (Straight Party / US Senate), Adams (US Senate / US House / state rows), Calaveras
(State Senate / State Assembly). No new readers; batchable anytime.

## Build / verify process (unchanged from HANDOFF-4, plus the cold rule)

Per contest: pull the results CSV → map the contest's pages → run the extractor → diff by (precinct,
party) for named + write-in SUM → build the expected CSV from the extractor output → **cold-validate
(`cache=False`)** → add the index record → `oe2d-votes-evaluate --only <id>`. Scratch builders from
this session are in the session scratchpad (`build_*.py`, `*_downballot.py`) — mirror their shape.
Gold record fields: mirror an existing record of the same read_strategy (`datasets.find(id)`).

## Where to look in the code

`oe2d/votes/__init__.py` — key additions since HANDOFF-4: `CACHE_ROOT/TEXTRACT_CACHE_DIR/
DSPY_CACHE_DIR` + `configure_cache()` (top); `read_report_blocks` / `_precinct_results_blocks` /
`_summary_report_blocks` / `_word_lines` (the report readers); `_extract_report_contest`;
`_extract_precinct_contest(..., read_grid=, choices_only=)`; `_match_header_line` / `_clean_header_label`
(precinct-header repair). The mega-grid path to change: `_extract_scanned_tables` → `scope_flat_tables`
→ `_align_columns` (all present, top-to-middle of the file). `read_flat_tables` returns the single wide
table for Missaukee p1. `evaluate.py` is the scoring CLI.
