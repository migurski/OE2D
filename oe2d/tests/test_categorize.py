'''Tests for oe2d.categorize — deterministic layer only (no LM required).'''
import json
import os
import subprocess
import sys

from oe2d import categorize

FIXTURES: str = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


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


def test_categorize_dict_shape():
    os.environ['OE2D_NO_LM'] = '1'
    result: dict = categorize.categorize(_fixture('glenn-pdf-p15.pdf'))
    assert result['container'] == 'vector_pdf'
    assert result['page_count'] == 1
    assert result['grain'] == 'unknown'
    assert result['orientation'] == 'unknown'
    assert result['llm_used'] is False
    assert set(result) == {
        'path', 'file_name', 'container', 'page_count',
        'orientation', 'grain', 'quirks', 'llm_used',
    }


def test_cli_entry_point():
    env: dict = dict(os.environ, OE2D_NO_LM='1')
    # The console script is installed alongside the running interpreter.
    script: str = os.path.join(os.path.dirname(sys.executable), 'oe2d-categorize-source')
    proc = subprocess.run(
        [script, _fixture('sf-xlsx-sheet2.xlsx')],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    payload: dict = json.loads(proc.stdout)
    assert payload['container'] == 'xlsx'
    assert payload['grain'] == 'unknown'
