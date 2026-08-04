'''Scoring the locator's predicted ranges against the originals gold.

Page-level overlap works for every document organization: by_contest gold is a
contiguous range, by_precinct / primary_split gold is an explicit scattered page list,
and either way the target's true pages are a set, so recall/precision on that set is the
common currency. Also reported: whether the predicted span overlaps the gold span at all
(a coarse "did it land in the right region" signal).
'''
from __future__ import annotations

import dspy


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


def score_location(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''GEPA metric over the locator's per-DOCUMENT prediction: mean page-F1 across the document's
    target contests, plus prose feedback naming, per target, the missed and extra pages and whether
    the matched title was the gold wording.

    The score guides both optimizable predictors, which are entangled -- a page miss can come from the
    classify filter dropping a real contest title OR the ReAct match not returning the right wording --
    so the feedback points at BOTH failure modes: a NOT-FOUND/low-recall target (the match missed a
    wording, or classify culled it), and extra pages (a wrong or adjacent contest was matched). The
    example carries its gold as `gold_targets` (one entry per contest: target label, page set, and the
    gold observed_title).'''
    expected: list = list(getattr(gold, 'gold_targets', None) or [])
    locations: dict = {loc.target: loc for loc in getattr(pred, 'locations', [])}
    f1s: list[float] = []
    lines: list[str] = []
    for item in expected:
        target: str = item['target']
        gold_set: set[int] = set(item['pages'])
        location = locations.get(target)
        got_set: set[int] = set(location.pages) if location else set()
        result: dict = score_pages(gold_set, got_set)
        f1s.append(result['f1'])
        gold_title: str = item.get('observed_title', '')
        if result['f1'] >= 0.999:
            lines.append('  %s: exact (%d page(s)).' % (target, len(gold_set)))
        elif location is None or not got_set:
            lines.append('  %s: NOT FOUND -- no title matched; gold title %r on pages %s.'
                         % (target, gold_title, sorted(gold_set)))
        else:
            lines.append('  %s: F1=%.2f; matched title %r vs gold %r; missed pages %s; extra pages %s.'
                         % (target, result['f1'], location.observed_title, gold_title,
                            sorted(gold_set - got_set), sorted(got_set - gold_set)))
    score: float = sum(f1s) / len(f1s) if f1s else 0.0
    head: list[str] = ['Document mean page-F1 %.3f over %d target(s).' % (score, len(expected))]
    if f1s and all(value >= 0.999 for value in f1s):
        tail: list[str] = ['Every target located exactly -- keep this reading of which strings name a '
                           'contest and which observed wordings are the target.']
    else:
        tail = ['To fix a NOT-FOUND or low-recall target: the match agent must search MORE title '
                'wordings (a contest often appears as a cumulative/summary section AND a per-precinct '
                'section, each carrying its own pages -- return ALL of them), and classify must keep a '
                'real contest title it may have culled. Extra pages mean a wrong or ADJACENT contest '
                'was matched -- distinguish a different district number or a full- vs partial-term seat.']
    return dspy.Prediction(score=score, feedback='\n'.join(head + lines + tail))
