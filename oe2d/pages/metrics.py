'''Scoring metric for the page analyzer, written for GEPA reflection.

GEPA improves a program from the *text* its metric returns, so score_page yields
a dspy.Prediction carrying both a weighted scalar and prose naming which fields
were wrong. candidate_orientation is weighted heaviest (it most changes how a
page is read); ruled_table and precinct_scope next (they route the read path);
the presence flags and axis are lighter.

Skew is not scored here — it is not a program output. A VLM cannot estimate fine
page rotation, so skew is detected separately and deterministically in
oe2d.pages.deskew.
'''
from __future__ import annotations

import dspy

# Weights need not sum to 1; the score normalizes by the total scored weight.
FIELD_WEIGHTS: dict[str, float] = {
    'candidate_orientation': 3.0,
    'ruled_table': 2.0,
    'precinct_scope': 2.0,
    'contest_name_present': 1.0,
    'candidate_names_present': 1.0,
    'headers_present': 1.0,
    'precinct_orientation': 1.0,
}


def score_page(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''Weighted per-field score plus prose feedback for GEPA reflection.

    Returns a dspy.Prediction with .score in [0, 1] and .feedback text listing
    each mismatch (predicted vs gold) so the reflection model knows what to fix.
    '''
    total_weight: float = 0.0
    earned: float = 0.0
    misses: list[str] = []
    hits: list[str] = []
    for name, weight in FIELD_WEIGHTS.items():
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
            'appear here (a continuation page may lack them), the precinct scope, and '
            'whether the table is a drawn grid of ruling lines or just aligned columns.'
        )
    else:
        feedback = f'Score {score:.2f}. All scored fields correct: {", ".join(hits)}.'

    return dspy.Prediction(score=score, feedback=feedback)
