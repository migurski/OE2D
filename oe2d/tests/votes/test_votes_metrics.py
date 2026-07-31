'''Tests for the F1 / IoU row-set metric (hermetic).'''
from ...votes import metrics


def _row(precinct, candidate, votes):
    return {'county': 'X', 'precinct': precinct, 'office': 'President', 'district': '',
            'party': '', 'candidate': candidate, 'votes': votes, 'election_day': '',
            'early_voting': '', 'absentee_mail': '', 'provisional': ''}


def test_perfect_match_scores_one():
    gold = [_row('A', 'Harris', 10), _row('A', 'Trump', 20)]
    got = [_row('A', 'Harris', 10), _row('A', 'Trump', 20)]
    s = metrics.score(got, gold)
    assert s['f1'] == 1.0 and s['iou'] == 1.0


def test_wrong_value_is_both_a_false_positive_and_false_negative():
    # a recall-only count would score this 1/2; F1/IoU must punish the spurious row too
    gold = [_row('A', 'Harris', 10), _row('A', 'Trump', 20)]
    got = [_row('A', 'Harris', 10), _row('A', 'Trump', 99)]      # Trump value wrong
    s = metrics.score(got, gold)
    assert s['true_positive'] == 1 and s['false_positive'] == 1 and s['false_negative'] == 1
    assert s['precision'] == 0.5 and s['recall'] == 0.5 and s['f1'] == 0.5
    assert s['iou'] == 1 / 3


def test_weighted_f1_is_one_on_a_perfect_match():
    gold = [_row('A', 'Harris', 10), _row('A', 'Trump', 20)]
    s = metrics.score(list(gold), gold)
    assert s['weighted_f1'] == 1.0 and s['f1'] == 1.0


def test_weighted_f1_punishes_a_big_row_error_more_than_a_small_write_in_error():
    # same single wrong row, but the plain f1 can't tell them apart; the weighted one must
    gold = [_row('A', 'Harris', 100), _row('A', 'Write-ins', 2)]
    big_wrong = [_row('A', 'Harris', 999), _row('A', 'Write-ins', 2)]
    small_wrong = [_row('A', 'Harris', 100), _row('A', 'Write-ins', 9)]
    big = metrics.score(big_wrong, gold)
    small = metrics.score(small_wrong, gold)
    assert big['f1'] == small['f1']                       # plain: both are one row off
    assert big['weighted_f1'] < small['weighted_f1']      # weighted: the big-row error costs far more
    assert small['weighted_f1'] > 0.8                     # a tiny write-in miss stays cheap


def test_weight_exponent_one_makes_a_tiny_write_in_error_nearly_free():
    gold = [_row('A', 'Harris', 500), _row('A', 'Write-ins', 3)]
    got = [_row('A', 'Harris', 500), _row('A', 'Write-ins', 8)]   # 3 -> 8, small absolute miss
    linear = metrics.score(got, gold, weight_exponent=1.0)
    concave = metrics.score(got, gold, weight_exponent=0.5)
    assert linear['weighted_f1'] > 0.98                   # linear: 5 votes against ~1000 is ~free
    assert concave['weighted_f1'] < linear['weighted_f1']  # concave keeps it cheaper, not free


def test_weighted_f1_penalizes_a_spurious_zero_vote_row():
    # a phantom all-zero row (e.g. an out-of-county precinct) must not be invisible: +1 smoothing
    # gives a zero-vote row weight 1, so it registers -- small, but not free
    gold = [_row('A', 'Harris', 100)]
    got = [_row('A', 'Harris', 100), _row('OOC', 'Harris', 0)]    # extra zero-vote row
    s = metrics.score(got, gold)
    assert s['f1'] < 1.0 and s['weighted_f1'] < 1.0       # the zero row is an error in both views
    assert s['weighted_f1'] > 0.9                         # ...but far cheaper than a real-vote error
