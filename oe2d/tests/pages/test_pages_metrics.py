'''Tests for the page-analysis scoring metric (hermetic; synthetic examples).'''
import dspy

from oe2d.pages import metrics

_FIELDS = dict(
    candidate_orientation='columns', contest_name_present=True,
    candidate_names_present=True, headers_present=True,
    precinct_scope='multi_precinct', precinct_orientation='rows', skew_degrees=0.0,
)


def _gold(**overrides):
    return dspy.Example(**{**_FIELDS, **overrides})


def _pred(**overrides):
    return dspy.Prediction(**{**_FIELDS, **overrides})


# --- content examples (eval_kind defaults to 'content') ---

def test_content_all_correct_scores_one():
    result = metrics.score_page(_gold(), _pred())
    assert result.score == 1.0
    assert 'All scored fields correct' in result.feedback


def test_content_orientation_is_weighted_heaviest():
    # orientation weight 3 of total content weight 9 -> missing it gives 6/9
    result = metrics.score_page(_gold(), _pred(candidate_orientation='rows'))
    assert abs(result.score - (6 / 9)) < 1e-9
    assert 'candidate_orientation' in result.feedback


def test_content_ignores_skew():
    # a real page's skew is not scored on the content path, so a wrong angle must
    # not lower the content score
    result = metrics.score_page(_gold(), _pred(skew_degrees=9.9))
    assert result.score == 1.0
    assert 'skew' not in result.feedback.lower()


def test_content_precinct_scope_mismatch_reported():
    result = metrics.score_page(_gold(), _pred(precinct_scope='county'))
    assert result.score < 1.0
    assert 'precinct_scope' in result.feedback


# --- skew examples (eval_kind='skew') ---

def test_skew_within_tolerance_is_credited():
    gold = _gold(eval_kind='skew', skew_degrees=1.0)
    assert metrics.score_page(gold, _pred(skew_degrees=1.3)).score == 1.0   # within 0.5
    assert metrics.score_page(gold, _pred(skew_degrees=2.5)).score == 0.0   # outside


def test_skew_example_ignores_content_fields():
    # on the skew path, wrong content fields must not matter
    gold = _gold(eval_kind='skew', skew_degrees=1.0)
    result = metrics.score_page(gold, _pred(skew_degrees=1.0, candidate_orientation='rows'))
    assert result.score == 1.0
    assert 'Skew correct' in result.feedback
