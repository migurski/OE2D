# oe2d.pages — handoff: fold skew into the program as a composite Module

Pick-up doc for two tasks, in order:
1. Rewrite the page analyzer so **the program is the full per-page unit** — one
   `dspy.Module` that returns the six VLM content fields AND `skew_degrees`, with
   skew computed **deterministically in-module** (NOT via an LLM tool).
2. Then report how to optimize it (command + expectations).

## Why a composite Module, not an LLM tool
Decided with Mike: skew is unconditional and deterministic, so there's no
decision for an LLM to make — a tool would add latency/tokens/failure modes for
nothing. Instead compose a `dspy.Module` whose `forward()` runs the content
`dspy.Predict` and calls the projection-profile detector directly. Same unit,
zero extra LLM overhead. Skew stays deterministic; it's just inside the program.

## Current state (all committed on branch migurski/categorize-sources)
- `oe2d/pages/__init__.py`: `PageProperties` (pydantic, 6 content fields, NO
  skew), `PageAnalysis(dspy.Signature)` (image in; 6 typed OutputFields with
  per-field `desc`), `build_analyzer() -> dspy.Predict(PageAnalysis)` (loads
  `OPTIMIZED_MODEL_PATH` if present), `analyze_image(path)`, `analyze_page(...)`,
  `render_source(...)` (INFERENCE_DPI=300), `_instrument()` (cmpnd, tag
  'oe2d-pages'), CLI `main()`. `OUTPUT_FIELDS = tuple(PageProperties.model_fields)`.
- `oe2d/pages/deskew.py`: `detect_skew(image_path, max_angle=3.0) -> float`.
  Projection-profile, Postl objective (sum of SQUARED row sums), FIXED binarize
  cut `<160` (Otsu is WORSE — rails to boundary), `_WORK_EDGE=1600`, coarse 0.5
  then fine 0.05. Sign: +CCW; rotate by -angle to straighten. CLI `main()`.
  Internals: `_ink(PIL)->ndarray`, `_sharpness(ink, angle)`, `_best_angle(...)`.
- `oe2d/pages/datasets.py`: loads `oe2d-data/pages/labels.jsonl` (75 rows = 60
  real + 15 `crop_top` header-absence negatives) -> dspy.Examples (image in);
  `split()` by fixture, synthetic-train-only, drops synthetics of val/removed
  fixtures; `subsample()`; sets `_synthetic/_transform/_fixture` attrs.
- `oe2d/pages/metrics.py`: `score_page` content-only, `FIELD_WEIGHTS` (6 fields,
  candidate_orientation 3, precinct_scope 2, rest 1), prose feedback.
- `oe2d/pages/optimize.py`: GEPA, `STUDENT_MODEL=categorize.TASK_LM` (Kimi
  vision), `REFLECTION_MODEL` Bedrock Opus, `instruction_proposer=
  instruction_proposal.MultiModalInstructionProposer()` (REQUIRED — else the
  image is str()'d as base64 into the reflection prompt and blows the context
  window), `build_program()`, `field_accuracy()`, `run_digest()` (order-
  independent), args incl `--max-examples` (quick pass) / `--max-metric-calls` /
  `--val-fraction`. Entry `oe2d-optimize-pages`. `_instrument()` is called BEFORE
  the LMs are built (correct cmpnd order).
- `oe2d-data/pages/`: `images/` (75 PNGs), `labels.jsonl`, `deskew-scans/` (4
  measured rough scans, deskew-validation only), `README.md`.
- Tests `oe2d/tests/pages/`: `test_pages_datasets.py`, `test_pages_metrics.py`,
  `test_pages_deskew.py` (synthetic angle recovery + real + rough measured).
  Full suite was green.
- pyproject entry points: `oe2d-analyze-page`, `oe2d-optimize-pages`,
  `oe2d-detect-skew`; package-data `oe2d.pages = ["model/*.json"]`.
- Key prior finding: first GEPA run had content at 100% (best_idx=0, NO lift —
  stock prompt already optimal on this data). Skew was removed from the VLM after
  it scored 53%/0.78deg MAE (VLM can't estimate fine rotation). The deterministic
  detector gets MAE ~0.03deg, max ~0.07deg on 12 real scans (clean + rough), with
  a ~0.3deg sensitivity floor on sparse/noisy pages.

## TASK 1 — implement the composite Module

### deskew.py: add an in-memory entry point
`detect_skew` currently opens a path. Add a function that works on an already-
loaded PIL image, and have `detect_skew(path)` delegate:
```python
def detect_skew_pil(image: 'Image.Image', max_angle=_MAX_ANGLE) -> float:
    # (current body of detect_skew from the `if max(image.size)...` downscale on)
def detect_skew(image_path, max_angle=_MAX_ANGLE) -> float:
    return detect_skew_pil(Image.open(image_path), max_angle)
```

### __init__.py: PageAnalyzer module + skew back on the output
- Re-add `skew_degrees: float` to `PageProperties` (LAST field), with a comment
  that it is detector-sourced, not VLM. `OUTPUT_FIELDS` then includes it again —
  BUT metrics.FIELD_WEIGHTS must stay 6 content fields (skew not scored), and
  `field_accuracy` iterates `metrics.FIELD_WEIGHTS`, so that's fine. Double-check
  nothing else iterates OUTPUT_FIELDS expecting only content (datasets
  record_to_example iterates OUTPUT_FIELDS to pull labels from the record — the
  labels.jsonl rows have NO skew_degrees, so guard: skip skew_degrees there, or
  `record.get(name)` returns None and you set it; simplest: in record_to_example
  iterate the content fields only, i.e. `metrics`-independent list. Cleanest:
  define `CONTENT_FIELDS` in __init__ and have datasets use that, OUTPUT_FIELDS
  = CONTENT_FIELDS + ('skew_degrees',)). Pick one and keep it consistent.
- Add:
```python
class PageAnalyzer(dspy.Module):
    def __init__(self):
        super().__init__()
        self.analyze = dspy.Predict(PageAnalysis)
    def forward(self, image):
        c = self.analyze(image=image)
        skew = deskew.detect_skew_pil(_image_to_pil(image))
        return dspy.Prediction(
            candidate_orientation=c.candidate_orientation,
            contest_name_present=c.contest_name_present,
            candidate_names_present=c.candidate_names_present,
            headers_present=c.headers_present,
            precinct_scope=c.precinct_scope,
            precinct_orientation=c.precinct_orientation,
            skew_degrees=skew,
        )
```
- `_image_to_pil(dspy.Image)`: parse `.url` (verified format
  `data:image/png;base64,<b64>`); `base64.b64decode` the part after the comma;
  `PIL.Image.open(io.BytesIO(...))`. Handle the (rare) plain-path url too.
- `build_analyzer()` returns `PageAnalyzer()` and `.load(OPTIMIZED_MODEL_PATH)`
  if present. NOTE: load/save now target the composite; the trained state is the
  inner `analyze` predictor — dspy handles nested module save/load, but VERIFY a
  round-trip (save then load then predict) once.
- `analyze_image` calls `build_analyzer()(image=dspy.Image(path))` and builds
  `PageProperties(...)` incl `skew_degrees=float(prediction.skew_degrees)`.

### optimize.py: point GEPA at the composite
- `build_program()` returns `PageAnalyzer()`. GEPA optimizes the named inner
  `analyze` predictor automatically (it targets `dspy.Predict` sub-modules), so
  the MultiModalInstructionProposer + metric are unchanged. VERIFY GEPA still
  finds the predictor (a quick `--max-examples 6 --max-metric-calls 4` dry run).
- `program.set_lm(student_lm)` must set the LM on the inner predictor — with a
  dspy.Module, `set_lm` propagates to sub-predictors; verify.
- field_accuracy already iterates content fields via metrics.FIELD_WEIGHTS; it
  now also could report skew, but skew is deterministic — optional to print a
  deterministic skew MAE against the deskew-scans set as a sanity line.

### tests
- Keep deskew tests as-is (they call detect_skew directly).
- Add a `PageAnalyzer` unit test that does NOT call the LM: monkeypatch/replace
  `self.analyze` with a stub returning fixed content, feed a committed image,
  assert the returned prediction has the stub's content AND a real
  `skew_degrees` from the detector. (Avoids a live vision call in tests.)
- Update `analyze_image`/PageProperties expectations if any test asserts the
  field set.

## TASK 2 — how to optimize (tell Mike after Task 1 lands)
Command (console script needs `pip install -e .`; module form always works):
```
AWS_PROFILE=cmpnd-org-account-mike .venv/bin/python -m oe2d.pages.optimize -v \
    --max-examples 12 --max-metric-calls 40      # quick; drop --max-examples for full (17 val)
```
- Needs FIREWORKS_AI_API_KEY (task LM, in .env) + Bedrock creds via AWS_PROFILE
  (Opus reflection LM — NOT in .env; same setup as oe2d-optimize-categorizer,
  which Mike has run). cmpnd tracing auto-on if CMPND_API_KEY set (it is).
- Expect: content ~100% again (no lift; the stock prompt is already optimal on
  this data — that's a real result, not a failure). Skew is deterministic and
  not part of the GEPA objective, so it won't move. The value of running it is
  confirmation + the trained artifact at oe2d/pages/model/optimized_page_analyzer.json.
- Watch: the load line, a cmpnd Opus reflection trace tagged oe2d-pages, and the
  per-field table. Any error is most likely Bedrock auth or a Kimi
  AdapterParseError on a rollout (field_accuracy tolerates the latter).

## Gotchas (learned, don't rediscover)
- dspy.Image str() == full base64 -> use MultiModalInstructionProposer in GEPA.
- dspy.Example compares by VALUE -> in tests assert membership by identity (`is`).
- deskew: Postl (sum of squared row sums) >> sum-of-squared-diffs for small real
  tilts; FIXED threshold, NOT Otsu (Otsu rails to -3.5deg on gray scans).
- Skew sensitivity floor ~0.3deg on sparse/noisy pages (gogebic p2 0.2deg -> 0);
  acceptable for OCR.
- Env: `.venv-linux` (this container) vs Mike's `.venv` (macOS, py3.14). Run
  modules with the venv python; tests via `python -m pytest oe2d/tests/pages/`.
- Verify full test suite green after Task 1: `python -m pytest oe2d/tests/ -q`.
