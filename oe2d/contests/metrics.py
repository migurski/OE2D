'''Scoring the locator's predicted ranges against the originals gold.

Page-level overlap works for every document organization: by_contest gold is a
contiguous range, by_precinct / primary_split gold is an explicit scattered page list,
and either way the target's true pages are a set, so recall/precision on that set is the
common currency. Also reported: whether the predicted span overlaps the gold span at all
(a coarse "did it land in the right region" signal).
'''
from __future__ import annotations


def gold_pages(row: dict) -> set[int]:
    '''The set of pages where the target truly appears (explicit list, else the range).'''
    if row.get('pages'):
        return set(row['pages'])
    lo, hi = row['range']
    return set(range(lo, hi + 1))


def run_pages(runs: list[tuple[int, int]]) -> set[int]:
    '''Union of the pages covered by predicted (start, end) runs.'''
    pages: set[int] = set()
    for start, end in runs:
        pages |= set(range(start, end + 1))
    return pages


def score_pages(gold: set[int], pred: set[int]) -> dict:
    '''Recall / precision / f1 of a predicted page set against the gold page set.'''
    hit: int = len(gold & pred)
    recall: float = hit / len(gold) if gold else (1.0 if not pred else 0.0)
    precision: float = hit / len(pred) if pred else (1.0 if not gold else 0.0)
    f1: float = (2 * recall * precision / (recall + precision)) if (recall + precision) else 0.0
    return {'recall': recall, 'precision': precision, 'f1': f1,
            'hit': hit, 'gold_n': len(gold), 'pred_n': len(pred)}


def spans_overlap(gold_range: list[int], runs: list[tuple[int, int]]) -> bool:
    '''Whether any predicted run overlaps the gold [min,max] span (coarse region hit).'''
    lo, hi = gold_range
    return any(start <= hi and end >= lo for start, end in runs)


def score_row(row: dict, runs: list[tuple[int, int]]) -> dict:
    '''Full score for one gold row given the locator's predicted runs for its target.'''
    result = score_pages(gold_pages(row), run_pages(runs))
    result['region_hit'] = spans_overlap(row['range'], runs)
    return result
