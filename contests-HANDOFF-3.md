# oe2d.contests — handoff 3: migrate the locator LM off Fireworks onto Bedrock

Continues `contests-HANDOFF.md` / `contests-2-HANDOFF.md` (read those for the locator architecture:
detect → classify → ReAct match → locate). This doc is one focused goal and the decision procedure
for it. Branch: `migurski/categorize-sources`.

## The goal (narrow, explicit)

`oe2d.contests` is the only module still off AWS: its interpreter LM is **Kimi K2 (`kimi-k2p7-code`)
on Fireworks** (`LM_KIMI_K2P7`), everything else is Bedrock. **Migrate it onto a Bedrock text model
while retaining the same locate accuracy.** Not a cost play (it's already cheap) — it's provider
consolidation. "Retain success" = hold the incumbent's page-set F1 on the gold.

## Status snapshot

- **Harness built this session** (there was none): `evaluate.py`, `optimize.py`, the GEPA feedback
  metric `metrics.score_location`, per-document `datasets.load_examples`/`split`,
  `datasets.fetch_original`, and the `oe2d-contests-evaluate` / `oe2d-contests-optimize` console
  scripts. Contests tests 25 pass. Commits `f388577` (evaluate), `1b2930e` (optimize).
- **Fixtures sweep done** (the fast, offline, EASY signal). Results below.
- **Full-documents evaluation DONE (2026-08-05)** on the enriched 106-target gold — the incumbent and
  both Bedrock finalists. **Verdict: migrate to Haiku 4.5.** See "RESULTS" next.
- **Eval-data expansion DONE**: votes gold translated into the contests set (60 -> 106 targets, commit
  `eb6545d`); two dead CA URLs repaired (`8942d75`).
- **NOT done / optional**: the two GEPA runs (now OPTIONAL upside, not required — Haiku stock already
  clears the bar).

## RESULTS — full documents (106-target enriched gold, 2026-08-05)

Merged macro page-F1 over each model's scored targets (base run + a sequential re-score of the two
repaired CA compound docs, so all three are on essentially the same set):

| model | full-doc F1 | scored | parse errors | on Bedrock | fixtures (contrast) |
|---|---|---|---|---|---|
| **Haiku 4.5 stock** | **0.892** | 105 | **0** (1 transient throttle) | yes | 0.944 |
| Kimi K2.5 stock | 0.865 | 87 | **19** | yes | 0.882 |
| **incumbent Kimi-FW (the bar)** | **0.835** | 106 | 0 | no | 0.944 |

**The fixtures badly overstated the incumbent.** Kimi-FW and Haiku *tied at 0.944* on the 2-4 page
excerpts, but on full documents the incumbent is the WEAKEST of the three (0.835) while Haiku holds
0.892. This is the handoff's own warning made real: the excerpts test "land on the right local pages,"
not the hard full-document search over hundreds of titles.

**Decision, both gates applied to the full set:**
- **Gate 1 accuracy (>= incumbent 0.835):** Haiku 0.892 PASS (+5.7 pts, an UPGRADE not a lateral move);
  K2.5 0.865 PASS-on-subset; incumbent = bar.
- **Gate 2 reliability (parse errors ~ 0):** Haiku 0 PASS; **K2.5 19 FAIL**; incumbent 0 PASS.
- **=> Migrate to Haiku 4.5.** It clears both gates on the hard set and is already on Bedrock, so the
  migration is a strict improvement, not a "retain success" compromise. GEPA becomes optional upside.

**K2.5's failure is document-reproducible, not flaky.** The 19 errors are ReAct-protocol non-compliance:
the model emits `{"title": "..."}` (jumping to the answer) instead of the required
`next_thought`/`next_tool_name`/`next_tool_args` control fields (NOT truncation — a K2.5 trace showed
`HasTruncation: false`, 8.9k output tokens). Specific compound docs (San Joaquin) trigger it every run;
Yolo it handled at 0.99. So `--max-tokens` won't help, and GEPA (which rewrites signature instructions,
not the adapter's control-field protocol) is a long shot for it — see the per-predictor-split note below.

**Two real full-doc weaknesses surfaced that fixtures hid:** (1) Haiku catastrophically missed San
Joaquin President (recall 1/19 pages) though the incumbent got it 19/19 — a compound-doc recall gap
worth watching if more such docs enter the gold. (2) One Haiku President target is still unscored (a
transient Bedrock rate-limit during the 3-way parallel run; 105 not 106) — immaterial to the verdict.

**To ship:** stock winner, so change `LM_KIMI_K2P7` -> the Haiku id in `build_locator` (no artifact
needed). Left for Mike's go since it swaps the shipped default.

## What we measured (fixtures only — 24 excerpt targets, `--fixtures`)

| model | id (litellm) | F1 | errored | wall time | $/1M in | $/1M out |
|---|---|---|---|---|---|---|
| **Kimi K2 (Fireworks, incumbent)** | `fireworks_ai/…/kimi-k2p7-code` | **0.944** | 0 | ~22 min | — | — |
| **Haiku 4.5** | `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` | **0.944** | 0 | ~17 min | 1.00 | 5.00 |
| **Kimi K2.5 (Bedrock)** | `bedrock/moonshotai.kimi-k2.5` | 0.882 | 0 | **~6 min** | 0.60 | 3.00 |
| Nova Pro | `bedrock/us.amazon.nova-pro-v1:0` | 0.711 | 0 | ~6 min | ~0.80 | ~3.20 |
| Llama 3.3 70B | `bedrock/us.meta.llama3-3-70b-instruct-v1:0` | 0.684 | **5/24** | ~18 min | 0.72 | 0.72 |

Prices are AWS Pricing-API on-demand standard, us-west-2 (Kimi K2.5 also has flex `$0.30/$1.50` and
batch tiers). Bedrock-id gotchas learned: **Qwen and Moonshot ids take NO `us.` prefix** (bare
`bedrock/qwen…`, `bedrock/moonshotai…`); Amazon/Anthropic/Meta take `us.`. Qwen3-235B was excluded on
speed alone (unreasonably slow, host-flaky).

Reading it: **only Haiku holds the incumbent's 0.944** on fixtures. Llama disqualifies itself (5
errored = broken ReAct output). Nova Pro is fast but 23 points short. Kimi-K2.5 is the cheapest and
3–4× the fastest, but 6 points short stock — and it missed two President targets *entirely* (recall
0), not by a rounding error.

## The decision framework (how we pick the model to migrate to)

Criteria, in strict priority order (the goal is "retain success," so accuracy gates everything):

1. **Accuracy — the gate.** Full-documents mean page-F1 **≥ the incumbent Kimi-Fireworks full-doc
   F1**. This is a PASS/FAIL gate, measured on the FULL 60-target set, NOT fixtures. A candidate that
   misses the bar is out regardless of price.
2. **Reliability — the gate.** Errored targets ≈ 0 (malformed ReAct/structured output). This is why
   Llama is already out. A model that flails in the tool loop is disqualified even if its scored
   subset looks fine.
3. **On Bedrock** — the whole point; all candidates satisfy it.
4. **Cost** — tiebreaker among passers. K2.5 ($0.60/$3.00) < Nova Pro (~$0.80/$3.20) < Haiku
   ($1.00/$5.00).
5. **Latency** — tiebreaker; the ReAct loop multiplies it, so a fast passer beats a slow one. K2.5
   (~6 min) ≪ Haiku (~17 min).

### The procedure

**Step 1 — set the real bar.** Run the incumbent on the full documents: `oe2d-contests-evaluate`
(no `--student`). Fixtures 0.944 is NOT the bar — the full-doc number is. Record it.

**Step 2 — get each finalist's full-doc number, stock and GEPA'd.** Finalists are **Haiku** and
**Kimi K2.5** only (Nova Pro / Llama eliminated on criteria 1–2). For each: full-doc F1 stock, then
full-doc F1 of the GEPA-optimized artifact (`--model`). That's five evals: Kimi-FW (bar), Haiku-stock,
Haiku-GEPA, K2.5-stock, K2.5-GEPA.

**Step 3 — apply the gates and pick.** Decision tree, preferring the cheapest/fastest passer:

- **If K2.5 (stock or GEPA) ≥ bar and errors ≈ 0 → migrate to Kimi-K2.5.** Best outcome: cheapest,
  fastest, and the same model family as the incumbent (lowest behavioural risk). GEPA is the lever
  that most likely gets it there (a 6-point gap on a same-family model is exactly GEPA's sweet spot,
  and the module was built to be optimized).
- **Elif Haiku (stock or GEPA) ≥ bar and errors ≈ 0 → migrate to Haiku.** Safe fallback: it already
  matches on fixtures, but it's pricier, slower, and had a `max_tokens` truncation flag (mitigated by
  `--max-tokens 16384`) that must be confirmed gone on full docs.
- **Else → no Bedrock model holds success.** Options then: accept a small, documented regression;
  stay on Kimi-Fireworks for now; or invest in the eval-data expansion + more GEPA budget before
  deciding.

**To ship the winner:** save its artifact to `contests.OPTIMIZED_MODEL_PATH`
(`oe2d/contests/model/optimized_contest_locator.json`) — `build_locator()` auto-loads it (its prompts
AND lm govern), so no code change is needed. For a stock (un-GEPA'd) winner, change `LM_KIMI_K2P7` →
the winner's id in `build_locator`.

## The two planned GEPA runs (launch-ready)

```
oe2d-contests-optimize contests-haiku-gepa.json \
  --student bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0 --max-tokens 16384 \
  --max-train 10 --max-metric-calls 60 --num-threads 2 -v

oe2d-contests-optimize contests-kimi-k2.5-gepa.json \
  --student bedrock/moonshotai.kimi-k2.5 \
  --max-train 10 --max-metric-calls 60 --num-threads 2 -v
```

Parameter rationale — contests GEPA is far pricier than votes/pages because **every metric call runs
the full locator on a document (OCR + classify + a multi-step ReAct match per target), and there is
NO OCR cache**, so it is LM- and OCR-bound both:

- `--max-metric-calls 60` — modest; each call is a whole document ReAct run.
- `--max-train 10` — a curated handful of documents is enough to teach the behaviour; the trainset is
  the ReAct-cost knob. (A smarter curation — smallest docs spanning the 5 organization types — would
  bound wall-clock further; the current cap just takes the first N of the split.)
- `--num-threads 2` — ReAct fan-out plus Bedrock throttle.
- reflection LM is Opus 4.5 (`LM_CLAUDE_OPUS45`, optimization-only).
- `optimize.py` re-binds the student to temperature 0 before `save()` (else the temp-1.0 exploration
  LM ships — the same trap flagged for votes).

Expectation setting (from the pages lesson): **GEPA lifts weak models, no-ops on saturated ones.** So
Haiku's run is really the truncation fix + maybe a marginal gain (it's already at the bar); **the
substantive run is K2.5** — can GEPA close its 6 points and make the cheaper/faster model reach the
bar? That is the question the whole exercise turns on.

## Structural facts that shape all of this

- **Two entangled predictors.** `classify` (a `dspy.Predict`, document-wide: which strings name a
  contest) feeds `match` (a `dspy.ReAct` agent, per-target: which titles are THIS race, via
  `search_titles`/`inspect_title`/`list_titles`). Both run for every target, and a page miss can come
  from either (classify culled the title, or match didn't find a wording). Unlike votes' cleanly
  partitioned interpreters, feedback is entangled — `score_location` names both failure modes so
  reflection can fix either predictor.
- **`match` is an agent, so latency and reliability matter more than price.** A weak model flails in
  the tool loop (Llama's 5 errors; Haiku's trace-truncation). Speed is a real axis: Nova Pro/K2.5 run
  the loop 3–4× faster than Haiku/Kimi.
- **Fixtures are the EASY signal.** 2–4 page excerpts test only "land on the right local pages," not
  the hard full-document search over hundreds of titles. The decision MUST be made on full documents.
- **Per-predictor model split is available (future, no decision yet).** The two predictors have very
  different demands: `classify` is a plain `Predict` (single-shot classification) a smaller/cheaper
  model could carry, while `match` is the ReAct agent that needs correct tool-calling. DSPy binds an LM
  per predictor, so a migration could reserve the capable/tool-following model for `match` and put a
  cheap one on `classify` — the contests analogue of the votes per-interpreter split. K2.5's failures
  are precisely a `match`-side ReAct-protocol problem (it emitted `{"title": ...}` instead of the
  next_thought/next_tool_name/next_tool_args control fields; not truncation), which is what makes this
  split worth considering rather than one model for both.

## De-risking the decision: expand the eval gold from the votes set (do this before trusting GEPA)

The single biggest threat is **small-gold overfit** (60 targets / 32 docs). The votes gold is
already-validated contest-locate gold: each `oe2d-data/votes/index.jsonl` record is
`(source_url, office+district → target, pages → gold page set, candidate_context → candidates)` — the
exact triple `score_location` scores, and those page sets were confirmed to 1.000 when the votes gold
was built. Converting them ≈ doubles the contests set (60 → ~110) and adds contests the current gold
lacks (Bay ×4, Branch ×4, Nevada ×5, …). Build a converter (`votes/index.jsonl` →
`training-full-documents.jsonl` rows) with these caveats:

- votes `pages` already equals "where this contest lives" — no transform. `observed_title` is absent
  (optional; only enriches feedback).
- **Dedup** source_urls already in the contests gold (Alameda/Humboldt/Mendocino overlap).
- **Split discipline**: a doc in both sets must stay on one side of the train/val split, or it leaks.

Doing this BEFORE the GEPA validation makes "K2.5-GEPA holds the bar" a trustworthy claim rather than
a 60-target coincidence.

## Open items / recommended order

1. Optionally build the votes→contests gold converter and enrich the set (de-risks everything below).
2. Full-doc bar: `oe2d-contests-evaluate` (incumbent Kimi-Fireworks). ← unblocks the decision.
3. Run the two GEPA passes (Haiku, K2.5). Multi-hour each; background + checkpointed (`gepa-contests-*`).
4. Full-doc eval of the four artifacts (`--model`) + the two stock finalists; apply the decision tree.
5. Ship the winner to `OPTIMIZED_MODEL_PATH` (or swap the id in `build_locator` for a stock winner).

## Where the code is

`oe2d/contests/`: `evaluate.py` (`--student`/`--model`/`--fixtures`/`--only`, page-set F1 + per-target
title-hit probe), `optimize.py` (GEPA), `metrics.py` (`score_location` GEPA metric + `score_pages`),
`datasets.py` (`load_examples`/`split`/`fetch_original`/`row_target`), `signatures.py`
(`ClassifyContestTitles`, `MatchContestTitles`), `__init__.py` (`ContestLocator`, `build_locator`,
`LM_KIMI_K2P7`, `OPTIMIZED_MODEL_PATH`). Sweep outputs live at the repo root as
`contests-<model>-fixtures.txt`.
