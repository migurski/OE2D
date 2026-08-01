'''Scoring for extracted precinct rows against gold: vote-weighted (and plain) set F1 / IoU.

A row is correct only if every canonical field matches (precinct, office, district, party,
candidate, and each vote figure), so the score catches BOTH a missing row (false negative) and a
spurious or wrong-valued row (false positive) -- a plain recall count would miss the latter.

Two views are reported:
 - plain: each row counts once, so a write-in with 3 votes weighs the same as a 673-vote major
   party row. Good for "how many rows are exactly right".
 - weighted: each row contributes by its vote size, so an error in a big party row is far costlier
   than one in a tiny write-in row -- the mistakes we most want to avoid dominate the score. The
   weight is CONCAVE with +1 smoothing ((votes + 1) ** weight_exponent, default 0.5 = sqrt): a
   small write-in error is cheaper than a big-row error but never zero -- a zero-VOTE row still
   weighs 1, so a spurious or missing zero row (a phantom out-of-county precinct, a dropped 0 row)
   registers as a small error instead of being invisible. exponent 1.0 -> near-linear, -> 0
   approaches the plain per-row count.
'''
from __future__ import annotations

import collections

import dspy

from . import CANON_COLUMNS


def row_key(row: dict) -> tuple:
    '''A normalized whole-row key over the canonical columns (values coerced to trimmed str).'''
    return tuple(str(row.get(column, '') if row.get(column) is not None else '').strip()
                 for column in CANON_COLUMNS)


def _row_votes(row: dict) -> int:
    '''The row's total votes (0 when blank/non-numeric) -- the basis of its weight.'''
    text: str = str(row.get('votes', '') or '').strip().replace(',', '')
    try:
        return max(int(text), 0)
    except ValueError:
        return 0


def _weight(votes: int, exponent: float) -> float:
    '''Concave vote weight with +1 smoothing: a big-row error costs more than a small one,
    sub-linearly, and a zero-vote row still weighs 1 (so a spurious/missing zero row is a small
    error, not invisible).'''
    return float(votes + 1) ** exponent


def _weights_by_key(rows: list[dict], exponent: float) -> dict[tuple, float]:
    '''Sum each distinct row-key's weight (duplicate identical rows add up).'''
    weights: dict[tuple, float] = collections.defaultdict(float)
    for row in rows:
        weights[row_key(row)] += _weight(_row_votes(row), exponent)
    return weights


def score(got: list[dict], gold: list[dict], weight_exponent: float = 0.5) -> dict:
    '''Precision / recall / F1 / IoU over whole-row keys -- both plain (per-row) and vote-weighted.

    weight_exponent tunes the vote weighting: 0.5 (default) is sqrt (concave), 1.0 is linear, and
    values toward 0 approach the plain per-row count. A matched key has identical votes on both
    sides, so its weight is unambiguous; an unmatched row contributes its own votes to the miss.
    '''
    got_keys: set = {row_key(r) for r in got}
    gold_keys: set = {row_key(r) for r in gold}
    true_positive: int = len(got_keys & gold_keys)
    false_positive: int = len(got_keys - gold_keys)
    false_negative: int = len(gold_keys - got_keys)
    precision: float = true_positive / (true_positive + false_positive) if got_keys else 1.0
    recall: float = true_positive / (true_positive + false_negative) if gold_keys else 1.0
    f1: float = (2 * true_positive / (2 * true_positive + false_positive + false_negative)
                 if (got_keys or gold_keys) else 1.0)
    iou: float = (true_positive / (true_positive + false_positive + false_negative)
                  if (got_keys or gold_keys) else 1.0)

    got_weights: dict[tuple, float] = _weights_by_key(got, weight_exponent)
    gold_weights: dict[tuple, float] = _weights_by_key(gold, weight_exponent)
    shared: set = got_keys & gold_keys
    tp_weight: float = sum(gold_weights[key] for key in shared)
    got_weight: float = sum(got_weights.values())
    gold_weight: float = sum(gold_weights.values())
    weighted_precision: float = tp_weight / got_weight if got_weight else 1.0
    weighted_recall: float = tp_weight / gold_weight if gold_weight else 1.0
    weighted_f1: float = (2 * weighted_precision * weighted_recall
                          / (weighted_precision + weighted_recall)
                          if (weighted_precision + weighted_recall) else 1.0)

    return {'precision': precision, 'recall': recall, 'f1': f1, 'iou': iou,
            'weighted_precision': weighted_precision, 'weighted_recall': weighted_recall,
            'weighted_f1': weighted_f1, 'weight_exponent': weight_exponent,
            'true_positive': true_positive, 'false_positive': false_positive,
            'false_negative': false_negative,
            'false_positives': sorted(got_keys - gold_keys),
            'false_negatives': sorted(gold_keys - got_keys)}


# Column positions within a row_key tuple, for turning a key back into readable prose.
_KEY_PRECINCT: int = CANON_COLUMNS.index('precinct')
_KEY_PARTY: int = CANON_COLUMNS.index('party')
_KEY_CANDIDATE: int = CANON_COLUMNS.index('candidate')
_KEY_VOTES: int = CANON_COLUMNS.index('votes')


def _key_votes(key: tuple) -> int:
    '''The vote magnitude carried in a row_key (0 when blank/non-numeric).'''
    try:
        return max(int((key[_KEY_VOTES] or '0').replace(',', '')), 0)
    except (ValueError, IndexError):
        return 0


def _describe(key: tuple) -> str:
    '''One readable line for a missed/spurious row: "precinct | party candidate = votes".'''
    who: str = ' '.join(part for part in (key[_KEY_PARTY], key[_KEY_CANDIDATE]) if part)
    return '%s | %s = %d' % (key[_KEY_PRECINCT] or '(blank)', who or '(blank)', _key_votes(key))


def score_extraction(gold, pred, trace=None, pred_name=None, pred_trace=None):
    '''GEPA metric over extracted precinct rows: weighted-F1 score plus prose feedback.

    Returns a dspy.Prediction with .score = weighted_f1 (the vote-weighted view, so the reflection
    optimizes toward getting the big rows right) and .feedback naming the rows that were MISSED
    (false negatives) and INVENTED (wrong-valued or spurious -- false positives), each WITH its vote
    magnitude and sorted biggest-first, so the reflection learns the weighted priority: a wrong
    600-vote major-party row matters, a spurious 0-vote row barely. The interpreters decide structure
    and language, so the prose points at STRUCTURE errors (a mis-matched candidate, a dropped/extra
    row, a wrong write-in consolidation), never at arithmetic.
    '''
    got: list[dict] = list(getattr(pred, 'rows', None) or [])
    want: list[dict] = list(getattr(gold, 'rows', None) or [])
    result: dict = score(got, want)
    top = lambda keys: sorted(keys, key=_key_votes, reverse=True)[:8]
    misses: list[str] = [_describe(key) for key in top(result['false_negatives'])]
    spurious: list[str] = [_describe(key) for key in top(result['false_positives'])]

    lines: list[str] = ['Weighted F1 %.3f (plain F1 %.3f); %d matched, %d missed, %d spurious.'
                        % (result['weighted_f1'], result['f1'], result['true_positive'],
                           result['false_negative'], result['false_positive'])]
    if misses:
        lines.append('Missed rows (present in gold, not produced) -- biggest first:')
        lines += ['  - ' + line for line in misses]
    if spurious:
        lines.append('Invented rows (produced, wrong or absent in gold) -- biggest first:')
        lines += ['  - ' + line for line in spurious]
    if not misses and not spurious:
        lines.append('Every row exact. Keep this reading of candidate columns, method labels, '
                      'write-in vs write-in-total, and which rows to skip.')
    else:
        lines.append('A row is wrong if ANY of precinct / party / candidate / votes differs. '
                     'Common structural causes: a candidate column matched to the wrong expected '
                     'name, a total/header row not skipped (or a precinct wrongly skipped), a '
                     'write-in total double-counted with its components (or components dropped).')
    return dspy.Prediction(score=result['weighted_f1'], feedback='\n'.join(lines))
