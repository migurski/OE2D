'''Tests for the page-analysis scoring metric (hermetic; synthetic examples).'''
import dspy

from ...pages import metrics

_FIELDS = dict(
    candidate_orientation='columns', contest_name_present=True,
    candidate_names_present=True, headers_present=True,
    precinct_scope='multi_precinct', precinct_orientation='rows',
)


def _gold(**overrides):
    return dspy.Example(**{**_FIELDS, **overrides})


def _pred(**overrides):
    return dspy.Prediction(**{**_FIELDS, **overrides})


def test_all_correct_scores_one():
    result = metrics.score_page(_gold(), _pred())
    assert result.score == 1.0
    assert 'All scored fields correct' in result.feedback


def test_orientation_is_weighted_heaviest():
    # orientation weight 3 of total weight 9 -> missing it gives 6/9
    result = metrics.score_page(_gold(), _pred(candidate_orientation='rows'))
    assert abs(result.score - (6 / 9)) < 1e-9
    assert 'candidate_orientation' in result.feedback


def test_precinct_scope_mismatch_reported():
    result = metrics.score_page(_gold(), _pred(precinct_scope='county'))
    assert result.score < 1.0
    assert 'precinct_scope' in result.feedback
