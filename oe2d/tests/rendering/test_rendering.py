'''Tests for oe2d.rendering -- rasterizing a source page/sheet to a PNG. No LM calls.'''
import os

import pytest

from ... import rendering

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FIXTURES: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'rendering', 'fixtures')


def _fx(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_render_pdf_page_to_png():
    png = rendering.render_page(_fx('beaver-pa-precinct.pdf'), 1)
    assert png.endswith('.png')
    assert os.path.getsize(png) > 1000


@pytest.mark.skipif(rendering.find_soffice() is None, reason='LibreOffice not installed')
def test_render_xls_sheet_to_png():
    # sheet 4 of this workbook is the President contest
    png = rendering.render_page(_fx('genesee-mi-precinct.xls'), 4)
    assert os.path.getsize(png) > 1000


def test_find_soffice_never_raises():
    result = rendering.find_soffice()
    assert result is None or os.path.exists(result)


def test_detect_container_from_extension_and_content():
    assert rendering.detect_container(_fx('beaver-pa-precinct.pdf')) == 'vector_pdf'
    assert rendering.detect_container(_fx('genesee-mi-precinct.xls')) == 'xls_xml'
