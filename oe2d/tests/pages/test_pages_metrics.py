'''Tests for the page-analysis scoring metric (hermetic; synthetic examples).'''
import dspy

from oe2d.pages import metrics

_GOLD = dict(
    candidate_orientation='columns', contest_name_present=True,
    candidate_names_present=True, headers_present=True,
    precinct_scope='multi_precinct', precinct_orientation='rows', skew_degrees=0.0,
)


def _gold(**overrides):
    return dspy.Example(**{**_GOLD, **overrides})


def _pred(**overrides):
    return dspy.Prediction(**{**_GOLD, **overrides})


def test_all_correct_scores_one():
    result = metrics.score_page(_gold(), _pred())
    assert result.score == 1.0
    assert 'All scored fields correct' in result.feedback


def test_orientation_is_weighted_heaviest():
    # orientation weight 3 out of total 10 -> missing it drops score to 0.7
    result = metrics.score_page(_gold(), _pred(candidate_orientation='rows'))
    assert abs(result.score - 0.7) < 1e-9
    assert 'candidate_orientation' in result.feedback


def test_skew_within_tolerance_is_credited():
    gold = _gold(skew_degrees=1.0)
    assert metrics.score_page(gold, _pred(skew_degrees=1.3)).score == 1.0   # within 0.5
    assert metrics.score_page(gold, _pred(skew_degrees=2.5)).score < 1.0    # outside


def test_null_gold_skew_is_not_scored():
    # a real scanned page has unmeasured skew; predicting any angle must not be
    # penalized, and the field drops out of the denominator.
    gold = _gold(skew_degrees=None)
    perfect = metrics.score_page(gold, _pred(skew_degrees=9.9))
    assert perfect.score == 1.0
    assert 'skew_degrees' not in perfect.feedback


def test_precinct_scope_mismatch_reported():
    result = metrics.score_page(_gold(), _pred(precinct_scope='county'))
    assert result.score < 1.0
    assert 'precinct_scope' in result.feedback
