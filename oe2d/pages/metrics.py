'''Scoring metric for the page analyzer, written for GEPA reflection.

GEPA improves a program from the *text* its metric returns, so score_page yields
a dspy.Prediction carrying both a weighted scalar and prose naming what was wrong.

Each example is scored by its `eval_kind` (set in datasets.record_to_example):
- 'content' (real pages and header-crops): the six content fields, weighted with
  candidate_orientation heaviest (it most changes how a page is read) and
  precinct_scope next. skew_degrees is NOT scored here — a real page's skew is
  0.0 (vector) or null (scanned), so scoring it would be a vacuous freebie.
- 'skew' (rotate augmentations, which carry a real known angle): skew_degrees
  only, by tolerance. This is where skew is actually measured and where a rotated
  val page serves as a held-out skew test.

Splitting the objective this way keeps the content metric honest (real pages
only) while still measuring — and lightly guarding — skew in the same GEPA run.
'''
from __future__ import annotations

import dspy

# Weights need not sum to 1; the content score normalizes by the total scored
# weight. skew is intentionally absent (scored on its own kind, below).
CONTENT_WEIGHTS: dict[str, float] = {
    'candidate_orientation': 3.0,
    'precinct_scope': 2.0,
    'contest_name_present': 1.0,
    'candidate_names_present': 1.0,
    'headers_present': 1.0,
    'precinct_orientation': 1.0,
}

# A predicted skew within this many degrees of the gold angle earns full credit.
SKEW_TOLERANCE_DEGREES: float = 0.5


def _skew_ok(pred_value, gold_value) -> bool:
    '''True when the predicted skew is within tolerance of the gold angle.'''
    try:
        return abs(float(pred_value) - float(gold_value)) <= SKEW_TOLERANCE_DEGREES
    except (TypeError, ValueError):
        return False


def _score_skew(gold, pred):
    '''Score a skew example (0/1 within tolerance) with prose feedback.'''
    gold_value = getattr(gold, 'skew_degrees', None)
    pred_value = getattr(pred, 'skew_degrees', None)
    if gold_value is None:                      # no angle to score against
        return dspy.Prediction(score=1.0, feedback='No skew label; not scored.')
    ok: bool = _skew_ok(pred_value, gold_value)
    feedback: str = (
        f'Skew {"correct" if ok else "WRONG"}: predicted {pred_value!r}, expected '
        f'~{gold_value!r} (within {SKEW_TOLERANCE_DEGREES} deg). Estimate the page '
        'rotation off horizontal in degrees: 0.0 if straight, positive = '
        'counter-clockwise.')
    return dspy.Prediction(score=1.0 if ok else 0.0, feedback=feedback)


def _score_content(gold, pred):
    '''Weighted score over the content fields with per-field mismatch feedback.'''
    total_weight: float = 0.0
    earned: float = 0.0
    misses: list[str] = []
    hits: list[str] = []
    for name, weight in CONTENT_WEIGHTS.items():
        total_weight += weight
        gold_value = getattr(gold, name, None)
        pred_value = getattr(pred, name, None)
        if pred_value == gold_value:
            earned += weight
            hits.append(name)
        else:
            misses.append(f'{name}: predicted {pred_value!r}, expected {gold_value!r}')
    score: float = earned / total_weight if total_weight else 1.0
    if misses:
        feedback: str = (
            f'Score {score:.2f}. Wrong fields:\n  - ' + '\n  - '.join(misses)
            + '\nLook only at what is visible on THIS page: whether candidates are '
            'columns or rows, whether a contest title / candidate names / headers '
            'appear here (a continuation page may lack them), and the precinct scope.'
        )
    else:
        feedback = f'Score {score:.2f}. All scored fields correct: {", ".join(hits)}.'
    return dspy.Prediction(score=score, feedback=feedback)


def score_page(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''Route to the content or skew scorer by the example's eval_kind.'''
    if getattr(gold, 'eval_kind', 'content') == 'skew':
        return _score_skew(gold, pred)
    return _score_content(gold, pred)
