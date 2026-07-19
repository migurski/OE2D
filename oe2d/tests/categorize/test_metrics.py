'''Tests for the categorizer scoring metric (no creds needed).'''
import dspy

from oe2d.categorize import metrics


def _gold(**overrides):
    fields = {
        'orientation': 'candidate_columns', 'grain': 'precinct',
        'has_rotated_headers': False, 'has_stacked_contests': True,
        'has_side_by_side': False, 'has_multi_sheet_stitch': False,
    }
    fields.update(overrides)
    return dspy.Example(**fields)


def test_perfect_prediction_scores_one():
    gold = _gold()
    pred = dspy.Prediction(**{name: gold.get(name) for name in metrics.FIELD_WEIGHTS})
    result = metrics.score_category(gold, pred)
    assert result.score == 1.0
    assert 'correct' in result.feedback.lower()


def test_orientation_miss_costs_the_most():
    gold = _gold()
    values = {name: gold.get(name) for name in metrics.FIELD_WEIGHTS}
    orient_wrong = dict(values, orientation='candidate_rows')
    flag_wrong = dict(values, has_side_by_side=True)
    orient_score = metrics.score_category(gold, dspy.Prediction(**orient_wrong)).score
    flag_score = metrics.score_category(gold, dspy.Prediction(**flag_wrong)).score
    assert orient_score < flag_score


def test_unknown_gold_grain_is_not_scored():
    gold = _gold(grain='unknown')
    values = {name: gold.get(name) for name in metrics.FIELD_WEIGHTS}
    # A wrong grain prediction should not be penalized when gold grain is unknown.
    result = metrics.score_category(gold, dspy.Prediction(dict(values, grain='county')))
    assert result.score == 1.0


def test_feedback_names_the_wrong_field():
    gold = _gold()
    values = {name: gold.get(name) for name in metrics.FIELD_WEIGHTS}
    pred = dspy.Prediction(**dict(values, grain='county'))
    result = metrics.score_category(gold, pred)
    assert 'grain' in result.feedback
