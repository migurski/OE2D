'''Scoring metric for the page analyzer, written for GEPA reflection.

GEPA improves a program from the *text* its metric returns, so score_page yields
a dspy.Prediction carrying both a weighted scalar and prose naming exactly which
fields were wrong. candidate_orientation is weighted heaviest (it most changes how
a page is read), precinct_scope next; the presence flags and axis are lighter.
skew_degrees is scored by tolerance, not exact equality, and is skipped when the
gold value is unmeasured (null, as on the real scanned pages).
'''
from __future__ import annotations

import dspy

# Weights need not sum to 1; the score normalizes by the total scored weight.
FIELD_WEIGHTS: dict[str, float] = {
    'candidate_orientation': 3.0,
    'precinct_scope': 2.0,
    'contest_name_present': 1.0,
    'candidate_names_present': 1.0,
    'headers_present': 1.0,
    'precinct_orientation': 1.0,
    'skew_degrees': 1.0,
}

# A predicted skew within this many degrees of the gold angle earns full credit.
SKEW_TOLERANCE_DEGREES: float = 0.5


def _skew_ok(pred_value, gold_value) -> bool:
    '''True when the predicted skew is within tolerance of the gold angle.'''
    try:
        return abs(float(pred_value) - float(gold_value)) <= SKEW_TOLERANCE_DEGREES
    except (TypeError, ValueError):
        return False


def score_page(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''Weighted per-field score plus prose feedback for GEPA reflection.

    Returns a dspy.Prediction with .score in [0, 1] and .feedback text listing
    each mismatch (predicted vs gold). skew_degrees uses a +/- tolerance and is
    skipped when the gold angle is null (unmeasured scanned pages).
    '''
    total_weight: float = 0.0
    earned: float = 0.0
    misses: list[str] = []
    hits: list[str] = []

    for name, weight in FIELD_WEIGHTS.items():
        gold_value = getattr(gold, name, None)
        pred_value = getattr(pred, name, None)

        if name == 'skew_degrees':
            if gold_value is None:            # unmeasured (real scanned) — no signal
                continue
            total_weight += weight
            if _skew_ok(pred_value, gold_value):
                earned += weight
                hits.append(name)
            else:
                misses.append(f'{name}: predicted {pred_value!r}, expected ~{gold_value!r} '
                              f'(within {SKEW_TOLERANCE_DEGREES} deg)')
            continue

        total_weight += weight
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
            'appear here (a continuation page may lack them), the precinct scope, '
            'and how tilted the page is.'
        )
    else:
        feedback = f'Score {score:.2f}. All scored fields correct: {", ".join(hits)}.'

    return dspy.Prediction(score=score, feedback=feedback)
