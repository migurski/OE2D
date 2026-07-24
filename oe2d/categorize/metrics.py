'''Scoring metric for the source categorizer, written for GEPA reflection.

GEPA improves a program by reading the *text* its metric returns, not just a
number, so score_category returns a dspy.Prediction carrying both a weighted
scalar score and prose describing exactly which fields were wrong. Orientation
is weighted heaviest because it is the field that routes a source to its
extractor; grain is next; the layout properties are lighter refinements.
'''
from __future__ import annotations

import dspy

from .. import categorize

# Field weights sum is not required to be 1; the score normalizes by them. The
# routing-critical fields carry the most weight.
FIELD_WEIGHTS: dict[str, float] = {
    'orientation': 3.0,
    'grain': 2.0,
    'has_rotated_headers': 1.0,
    'has_stacked_contests': 1.0,
    'has_side_by_side': 1.0,
    'has_multi_sheet_stitch': 1.0,
}

# grain is folded down to name-cues at the CLI, so a gold 'unknown' grain is not
# something the RLM can be faulted for missing; skip scoring it in that case.
_SCORED_FIELDS: tuple[str, ...] = tuple(FIELD_WEIGHTS)


def _field_value(source: object, name: str):
    '''Read a field from either a dspy.Example or a Prediction, defaulting False.'''
    return getattr(source, name, None)


def score_category(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''Weighted per-field score plus prose feedback for GEPA reflection.

    Returns a dspy.Prediction with .score in [0, 1] and .feedback text listing
    each mismatch (predicted vs gold) so the reflection model knows what to fix.
    '''
    total_weight: float = 0.0
    earned: float = 0.0
    misses: list[str] = []
    hits: list[str] = []

    for name in _SCORED_FIELDS:
        gold_value = _field_value(gold, name)
        # A gold 'unknown' grain carries no signal to learn from; skip it.
        if name == 'grain' and gold_value == 'unknown':
            continue
        weight: float = FIELD_WEIGHTS[name]
        total_weight += weight
        pred_value = _field_value(pred, name)
        if pred_value == gold_value:
            earned += weight
            hits.append(name)
        else:
            misses.append(f'{name}: predicted {pred_value!r}, expected {gold_value!r}')

    score: float = earned / total_weight if total_weight else 1.0

    if misses:
        feedback: str = (
            f'Score {score:.2f}. Wrong fields:\n  - ' + '\n  - '.join(misses)
            + '\nInspect the file with the text tools (page_table/page_words) '
            'before deciding orientation and the layout properties.'
        )
    else:
        feedback = f'Score {score:.2f}. All scored fields correct: {", ".join(hits)}.'

    return dspy.Prediction(score=score, feedback=feedback)
