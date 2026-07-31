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
