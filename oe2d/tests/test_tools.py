'''Tests for oe2d.rendering and oe2d.tools — no LM calls.'''
import os

import pytest

from oe2d import rendering, tools

FIXTURES: str = os.path.join(os.path.dirname(__file__), 'fixtures')


def _fx(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_render_pdf_page_to_png():
    png = rendering.render_page(_fx('2024-adams-county-pa-precinct-summary-general-2024.pdf'), 1)
    assert png.endswith('.png')
    assert os.path.getsize(png) > 1000


@pytest.mark.skipif(rendering.find_soffice() is None, reason='LibreOffice not installed')
def test_render_xls_sheet_to_png():
    # sheet 4 of this workbook is the President contest
    png = rendering.render_page(_fx('2024-genesse-county-mi-precinct-level-results.xls'), 4)
    assert os.path.getsize(png) > 1000


def test_find_soffice_never_raises():
    result = rendering.find_soffice()
    assert result is None or os.path.exists(result)


def test_zip_member_tools_return_simple_types():
    zip_path = _fx('2024-armstrong-county-pa-precinct-level-results.zip')
    members = tools.zip_members(zip_path)
    assert members and all(isinstance(m, str) for m in members)
    count = tools.page_count(zip_path, member=members[0])
    assert isinstance(count, int)
    rows = tools.page_table(zip_path, 1, member=members[0])
    assert isinstance(rows, list)
    assert all(isinstance(cell, str) for row in rows for cell in row)


def test_page_table_is_bounded():
    rows = tools.page_table(_fx('2024-genesse-county-mi-precinct-level-results.xls'), 4)
    assert len(rows) <= 100
    assert all(len(row) <= 60 for row in rows)
