'''Tests for the projection-profile skew detector (hermetic).

Rotates a committed page image by known angles and checks detect_skew recovers
them — the exact thing the VLM could not do.
'''
import os

import pytest
from PIL import Image

from oe2d.pages import deskew

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_IMAGES = os.path.join(_REPO_ROOT, 'oe2d-data', 'pages', 'images')
# a dense text page recovers skew cleanly
_PAGE = os.path.join(_IMAGES, 'barry-mi-sovc-official-results-p1.png')


@pytest.mark.parametrize('applied', [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5])
def test_detect_recovers_known_angle(tmp_path, applied):
    rotated = Image.open(_PAGE).convert('RGB').rotate(
        applied, resample=Image.BILINEAR, expand=True, fillcolor='white')
    path = tmp_path / f'rot{applied}.png'
    rotated.save(path)
    estimate = deskew.detect_skew(str(path))
    assert abs(estimate - applied) <= 0.2, f'applied {applied}, detected {estimate}'


def test_straight_page_is_near_zero():
    assert abs(deskew.detect_skew(_PAGE)) <= 0.2
