'''Scoring for extracted precinct rows against gold: set F1 / IoU over whole rows.

A row is correct only if every canonical field matches (precinct, office, district, party,
candidate, and each vote figure), so the score catches BOTH a missing row (false negative) and a
spurious or wrong-valued row (false positive) -- a plain recall count would miss the latter. F1
and IoU are monotonic in each other; both are reported, with the offending rows.
'''
from __future__ import annotations

from . import CANON_COLUMNS


def row_key(row: dict) -> tuple:
    '''A normalized whole-row key over the canonical columns (values coerced to trimmed str).'''
    return tuple(str(row.get(column, '') if row.get(column) is not None else '').strip()
                 for column in CANON_COLUMNS)


def score(got: list[dict], gold: list[dict]) -> dict:
    '''Precision / recall / F1 / IoU over whole-row keys, plus the false positives/negatives.'''
    got_keys: set = {row_key(r) for r in got}
    gold_keys: set = {row_key(r) for r in gold}
    true_positive: int = len(got_keys & gold_keys)
    false_positive: int = len(got_keys - gold_keys)
    false_negative: int = len(gold_keys - got_keys)
    precision: float = true_positive / (true_positive + false_positive) if got_keys else 0.0
    recall: float = true_positive / (true_positive + false_negative) if gold_keys else 0.0
    f1: float = (2 * true_positive / (2 * true_positive + false_positive + false_negative)
                 if (got_keys or gold_keys) else 1.0)
    iou: float = (true_positive / (true_positive + false_positive + false_negative)
                  if (got_keys or gold_keys) else 1.0)
    return {'precision': precision, 'recall': recall, 'f1': f1, 'iou': iou,
            'true_positive': true_positive, 'false_positive': false_positive,
            'false_negative': false_negative,
            'false_positives': sorted(got_keys - gold_keys),
            'false_negatives': sorted(gold_keys - got_keys)}
