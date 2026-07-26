'''metrics: page-overlap scoring of predicted runs against gold rows.'''
from ...contests import metrics


def test_gold_pages_uses_explicit_list_over_range():
    row = {'range': [1, 100], 'pages': [1, 8, 14]}
    assert metrics.gold_pages(row) == {1, 8, 14}


def test_gold_pages_falls_back_to_range():
    assert metrics.gold_pages({'range': [22, 25]}) == {22, 23, 24, 25}


def test_run_pages_unions_runs():
    assert metrics.run_pages([(2, 4), (10, 11)]) == {2, 3, 4, 10, 11}


def test_score_pages_partial_overlap():
    s = metrics.score_pages({1, 2, 3, 4}, {3, 4, 5})
    assert s['hit'] == 2
    assert s['recall'] == 0.5
    assert round(s['precision'], 3) == round(2 / 3, 3)


def test_score_pages_empty_prediction_is_zero_recall():
    s = metrics.score_pages({1, 2}, set())
    assert s['recall'] == 0.0 and s['pred_n'] == 0


def test_spans_overlap_detects_region_hit_and_miss():
    assert metrics.spans_overlap([22, 36], [(20, 25)]) is True
    assert metrics.spans_overlap([22, 36], [(1, 3)]) is False


def test_score_row_reports_region_hit():
    row = {'range': [2, 2], 'pages': [2]}
    s = metrics.score_row(row, [(2, 4)])
    assert s['recall'] == 1.0 and s['region_hit'] is True
    assert s['precision'] < 1.0        # predicted 2,3,4 but only 2 is gold
