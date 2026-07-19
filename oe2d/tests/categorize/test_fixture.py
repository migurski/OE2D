'''Tests for oe2d.categorize.fixture — local excerpting only (no network).'''
import os

from oe2d import categorize
from oe2d.categorize import fixture

FIXTURES: str = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fixtures')


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_excerpt_pdf_keeps_page_budget(tmp_path):
    out: str = fixture.excerpt(_fixture('amador-pdf-p5-p7-p10-p30.pdf'), str(tmp_path), pages=2)
    assert categorize.detect_container(out) == 'vector_pdf'
    categorize.source_table.page_count.cache_clear()
    assert categorize.count_pages(out, 'vector_pdf') == 2


def test_small_spreadsheet_copied_whole(tmp_path):
    # Under the size cap, the workbook is copied whole so it previews natively.
    src: str = _fixture('sf-xlsx-sheet2.xlsx')
    out: str = fixture.excerpt(src, str(tmp_path), sheets=1, rows=15)
    assert categorize.detect_container(out) == 'xlsx'
    assert os.path.getsize(out) == os.path.getsize(src)


def test_excerpt_xlsx_trims_sheets_and_rows(tmp_path):
    # spreadsheet_max_bytes=0 forces the trim path even for a small file.
    out: str = fixture.excerpt(_fixture('sf-xlsx-sheet2.xlsx'), str(tmp_path),
                               sheets=1, rows=15, spreadsheet_max_bytes=0)
    assert categorize.detect_container(out) == 'xlsx'
    rows = categorize.source_table.read_xlsx_page(out, 1)
    assert len(rows) <= 15


def test_excerpt_xls_xml_stays_xml_and_shrinks(tmp_path):
    src: str = _fixture('santa-clara-xls-sheets2-3.xls')
    out: str = fixture.excerpt(src, str(tmp_path), sheets=1, rows=10, spreadsheet_max_bytes=0)
    # Container is preserved: still XML SpreadsheetML, not converted away.
    assert categorize.detect_container(out) == 'xls_xml'
    assert os.path.getsize(out) < os.path.getsize(src)


def test_slugify():
    assert fixture.slugify('2024 Otsego County, MI results.pdf') == '2024-otsego-county-mi-results'
