'''Tests for oe2d.categorize deterministic detectors (the RLM step needs creds).'''
import os

from oe2d import categorize

FIXTURES: str = os.path.join(os.path.dirname(__file__), '..', 'source_table', 'fixtures')


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


def test_detect_vector_pdf():
    assert categorize.detect_container(_fixture('glenn-pdf-p15.pdf')) == 'vector_pdf'


def test_detect_xlsx():
    assert categorize.detect_container(_fixture('sf-xlsx-sheet2.xlsx')) == 'xlsx'


def test_detect_xls_xml():
    # Santa Clara export is XML SpreadsheetML despite the .xls extension.
    assert categorize.detect_container(_fixture('santa-clara-xls-sheets2-3.xls')) == 'xls_xml'


def test_grain_from_name():
    assert categorize.grain_from_name('2024 Fresno County, CA precinct-level results.xlsx') == 'precinct'
    assert categorize.grain_from_name('2024 El Dorado County, CA district-level results.xlsx') == 'district'
    assert categorize.grain_from_name('mystery.pdf') == 'unknown'


def test_content_preview_reads_rows():
    preview: str = categorize.content_preview(_fixture('sf-xlsx-sheet2.xlsx'), 'xlsx')
    assert 'PRESIDENT' in preview.upper()


def test_count_pages_deterministic():
    path: str = _fixture('glenn-pdf-p15.pdf')
    assert categorize.count_pages(path, 'vector_pdf') == 1
