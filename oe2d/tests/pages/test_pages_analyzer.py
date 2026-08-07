'''Hermetic test for the composite PageAnalyzer module.

Verifies that forward() returns the content predictor's fields AND a real
detector-sourced skew, WITHOUT making a live vision call: the inner `analyze`
predictor is replaced with a stub returning fixed content, so only the
deterministic skew path exercises real code.
'''
import os

import dspy

from ... import pages
from ...pages import deskew

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMAGE = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages', 'images',
                      'barry-mi-sovc-official-results-p1.png')


class _StubPredictor:
    '''Stand-in for the inner dspy.Predict: returns fixed content, no LM call.'''
    def __call__(self, image, electoral_context=''):
        return dspy.Prediction(
            candidate_orientation='columns',
            contest_name_present=True,
            candidate_names_present=True,
            headers_present=True,
            precinct_scope='multi_precinct',
            precinct_orientation='rows',
            ruled_table=True,
            contests_across='single',
            precinct_rows='multiple',
            value_columns='total_only',
        )


def test_forward_combines_stub_content_with_real_skew():
    analyzer = pages.PageAnalyzer()
    analyzer.analyze = _StubPredictor()
    image = dspy.Image(_IMAGE)

    prediction = analyzer(image=image)

    # Content comes straight from the stub.
    assert prediction.candidate_orientation == 'columns'
    assert prediction.contest_name_present is True
    assert prediction.precinct_scope == 'multi_precinct'
    assert prediction.precinct_orientation == 'rows'
    assert prediction.ruled_table is True

    # Skew is the real detector run on the real image, matching detect_skew.
    assert prediction.skew_degrees == deskew.detect_skew(_IMAGE)
    assert isinstance(prediction.skew_degrees, float)


def test_image_to_pil_round_trips_a_committed_png():
    image = dspy.Image(_IMAGE)
    recovered = pages._image_to_pil(image)
    assert recovered.size[0] > 0 and recovered.size[1] > 0
