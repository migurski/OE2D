# oe2d.votes — handoff 6

Continues `votes-HANDOFF-5.md`. Read HANDOFF-4 for the architecture and HANDOFF-5 for the
autonomous-dispatch design; this doc records what changed after HANDOFF-5's "detected-dispatch
residual" item and lists what remains. All work here is on branch `migurski/categorize-sources`.

## Status snapshot (current)

- **Gold: 53 contests, macro wF1 = 1.000.** (was 50 at HANDOFF-5.) One pre-existing sub-1.000:
  `columbia-us-house` F1=0.996 (a single zero-vote write-in; wF1=1.000).
- **Autonomous dispatch is now complete**: `--detect` orientation 100%, read_strategy 92%;
  `--detected` (image-driven, end-to-end) macro **wF1 1.000** — matches gold dispatch. Every read
  strategy routes from the page image with no hand-set field; the remaining `--detect` misses
  (Gogebic, Calaveras ×2, Missaukee-president) are all reconcile/empty-guard-protected, so they read
  correctly end-to-end anyway.
- **Tests: `oe2d/tests/votes/` 83 pass; full `oe2d/tests/` 198+.**
- Counties/offices (see `datasets.load_index()`): the HANDOFF-5 set **plus** Alameda(1, U.S. House 17),
  Humboldt(1, President), Mendocino(1, President).

## What changed since HANDOFF-5

1. **Detected-dispatch residual CLOSED (HANDOFF-5 item 1).**
   - `flat_grouped` auto-detected: a deterministic cross-page probe in `detect_dispatch`
     (`_pages_split_candidates`) — a candidate name only on a LATER page + repeating precincts ⇒
     `flat_grouped`. Avoids the trap that a flat read of a grouped doc silently reconciles on a subset.
   - `ruled_columns` auto-routed + reconcile-protected: scanned columns + `precinct_rows=multiple` ⇒
     `ruled_columns`; `_county_totals` recovers the printed grand-total row and
     `_reconciles(strict=True)` confirms EVERY column (strict because the faint-grid failure
     mis-segments only one column — Gogebic's 7/8 would pass a majority test). Gogebic fails strict →
     auto; Montmorency president/senate 0.707/0.985 → 1.000.
   - `value_columns` is the remaining hardening (not needed on gold): a genuine Dominion report the
     VLM calls plain `methods` → auto with no fallback. Options if a novel source hits it: tighten the
     field description, or a 2-of-3 multi-page detection vote.

2. **oe2d.pages: Qwen3-VL is the Python default; the committed artifact was deleted.** The stock
   `optimized_page_analyzer.json` carried only the stock prompt (GEPA was a no-op) — its only real
   effect was binding the LM to Qwen instead of the Llama-4 fallback. `LM_QWEN3_VL` is now the stock
   default, so Python is the single source of truth; the load-if-present hook stays for a real future
   optimization. See `pages-PERFORMANCE.md` / the `pages-read-shape-dispatch` memory.

3. **oe2d.pages: added an optional `context` input; orientation grounded on the candidate-name axis.**
   `PageAnalysis` gained `context` (external prose, e.g. the expected candidates), and
   `candidate_orientation` is now decided by where the candidate NAMES run, NOT the method/percent
   columns that fooled it on per-precinct pages. `votes.detect_dispatch` passes the contest's
   `candidate_context` through. The interpreter ECHOES the supplied party and never reads it off the
   image; presence booleans still come from what's visible. Pages gold net-positive (orientation held
   99%, precinct_scope/precinct_rows +2 each, ruled_table −3 on non-routing vector pages). This fixed
   the Humboldt/Mendocino per-precinct misroute (they had read as `columns`).

4. **New read strategy `precinct_matrix`, auto-detected (Alameda-style SOVC).** A vector columns page
   with the precinct id and the vote-method label in SEPARATE columns (each row = one precinct ×
   method), candidates in columns — `walk_page`'s single label column can't model it. The read reuses
   the shared `interpret_columns` UNCHANGED for the language (candidate columns + `method_labels`); a
   deterministic `read_matrix_page` finds the separate precinct-id column and groups the method rows.
   `detect_dispatch` auto-routes via `_looks_like_matrix` (the tell: a dedicated low-cardinality
   vote-method column, which `walk_page` layouts lack). Privacy-suppressed cells (non-numeric, e.g.
   `***`) are kept as BLANK values (present, not zero); the matrix path keeps its full roster. Zero
   false-fires across the gold.

5. **Three new CA counties banked (all image-routed, no flags, cold/SOS-validated):**
   - **Alameda U.S. House 17** (`precinct_matrix`): 19 precincts, 38 rows. Sums match CA SOS SOV
     (Chen 13,555; Khanna 26,120 vs printed 26,121 — the 1 is a privacy-suppressed precinct's blank).
   - **Humboldt President** (per-precinct rows, `report_lines_methods`→`_extract_precinct_contest`
     fallback): 95 precincts, 855 rows; 12 zero-registration phantoms dropped. Sums match SOS exactly.
   - **Mendocino President** (same shape): 206 precincts, 1854 rows; 39 phantoms dropped. Sums match
     SOS exactly. President pages located by scanning the 1196-page SOVC for the contest title.
   Validation pattern for these: county candidate sums vs the CA SOS Statement of Vote
   (`.../2024-general/sov/16-president.pdf`), plus 1.000 under BOTH gold and `--detected` dispatch
   (two independent interpret runs reproducing the expected).

### Key facts learned this session (for the next builder)

- **Per-precinct rows conventions**: the extractor keeps Undervotes/Overvotes/Write-ins as their own
  rows (source-faithful to the county doc) and consolidates the write-in scatter (a qualified write-in
  like Sonski's handful of votes folds in — a deliberate, don't-fuss call). Nevada/Mono differ only
  because they came through the pure report reader; that inconsistency is accepted, not a bug.
- **Party comes from context, always.** The interpreters echo the supplied party and never read it
  off the source (even when the source prints it inline, as Mendocino does). So gold party accuracy is
  entirely a function of the `candidate_context` — which in production is oe2d.contests' job.
- **read_strategy in a gold record drives the all-zero drop.** A `report_lines_*` record drops
  zero-registration phantom precincts (keep_all_zero=False); an `auto` record keeps them. Humboldt/
  Mendocino use `report_lines_methods` so phantoms drop, matching the SOS/reference roster.
- **Cold-validation nuance**: compare cold runs by row CONTENT (set of row tuples), not raw CSV
  string — row ORDER varies run-to-run but the by-key F1 ignores it. Write expected CSVs precinct-sorted.

## Next steps (carried from HANDOFF-5, updated)

1. **GEPA over the two votes interpreters — now the priority, framed as COST.** Goal: replace the
   expensive Sonnet interpreter LM with a cheaper TEXT model, using GEPA to compile Sonnet's
   competence into the prompt. The interpreters are text-only + structural (not vision), so a cheap
   text model is a natural fit. `evaluate.py --student <model>` measures a model on the stock prompts;
   `optimize.py` (exists, never run) is the GEPA harness. Start by baselining a candidate cheap model
   with `--student` to size the gap before investing in GEPA. Watch the pages lesson: GEPA overfits a
   small val split — evaluate honestly on held-out + the full 53. See the dedicated discussion.
2. **Tier-2 breadth fills** (cheap, existing readers): missing offices in covered counties (Gogebic,
   Oscoda, Barry, Calhoun, Adams, Calaveras).
3. **A ballot-measure (Yes/No) contest** — the one contest *shape* still absent from gold.
4. **Cost — N+M perimeter reads** (from HANDOFF-5 item 4): the columns path (`_extract_contest`) still
   interprets every page; interpret only the ~M+N perimeter page-types, deterministic-fill the
   interior. Complementary to the GEPA/cheap-model lever (this cuts call COUNT, that cuts per-call cost).
5. **Robustness/future**: dual-DPI reconciliation; the fuller all-zero roster (document-level precinct
   roster + name-based out-of-jurisdiction filter).
6. **`columbia-us-house` F1=0.996** — one zero-vote write-in the flat read drops (wF1=1.000).

## Where to look in the code

`oe2d/votes/__init__.py`: `detect_dispatch` (+ `_pages_split_candidates`, `_looks_like_matrix`,
`_propose_read_strategy`); `_reconciles(strict=)` + `_county_totals`; `read_matrix_page` /
`_extract_matrix_contest`; the two interpreters `interpret_columns` / `interpret_rows` (bound to
`LM_CLAUDE_SONNET45`). `oe2d/pages/`: `signatures.PageAnalysis` (now with `context`), `build_analyzer`
(`LM_QWEN3_VL` default). `oe2d/votes/optimize.py`: the GEPA harness. `oe2d/votes/evaluate.py`:
`--detect` / `--detected` / `--only` / `--student` / `--model`.
