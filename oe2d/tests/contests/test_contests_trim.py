'''Tests for oe2d.contests.write_trimmed -- slicing a source to its matched pages.'''
import os

import pypdf
import pytest

from ... import contests

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PDF = os.path.join(_REPO_ROOT, 'oe2d-data', 'contests', 'fixtures',
                    'barry-mi-sovc-official-results.pdf')


def test_trim_pdf_keeps_only_named_pages(tmp_path):
    out = str(tmp_path / 'trim.pdf')
    contests.write_trimmed(_PDF, [1, 3], out)
    assert len(pypdf.PdfReader(out).pages) == 2


def test_trim_pdf_dedups_and_skips_out_of_range(tmp_path):
    out = str(tmp_path / 'trim.pdf')
    contests.write_trimmed(_PDF, [2, 2, 999], out)      # dup collapses, 999 is dropped
    assert len(pypdf.PdfReader(out).pages) == 1


def test_trim_with_no_pages_is_an_error(tmp_path):
    with pytest.raises(SystemExit):
        contests.write_trimmed(_PDF, [], str(tmp_path / 'x.pdf'))


def test_write_trimmed_per_target_one_file_each(tmp_path):
    locations = [
        {'target': 'President', 'pages': [1, 2], 'observed_title': None},
        {'target': 'U.S. Senate (full term)', 'pages': [3], 'observed_title': None},
        {'target': 'Nothing Here', 'pages': [], 'observed_title': None},   # skipped
    ]
    written = contests.write_trimmed_per_target(_PDF, locations, str(tmp_path / 'out'))
    assert sorted(os.path.basename(w) for w in written) == \
        ['President.pdf', 'U.S.-Senate-full-term.pdf']                      # named per target
    assert len(pypdf.PdfReader(str(tmp_path / 'out' / 'President.pdf')).pages) == 2
    assert len(pypdf.PdfReader(str(tmp_path / 'out' / 'U.S.-Senate-full-term.pdf')).pages) == 1


def test_resolve_context_verbatim_vs_at_file(tmp_path):
    # A plain string is used as-is, even one that happens to name a real file.
    assert contests.resolve_context('presidential race, Harris vs Trump') \
        == 'presidential race, Harris vs Trump'
    # An @-prefixed value reads the named file.
    notes = tmp_path / 'ctx.txt'
    notes.write_text('full-term Senate seat; Slotkin vs Rogers', encoding='utf-8')
    assert contests.resolve_context(f'@{notes}') == 'full-term Senate seat; Slotkin vs Rogers'
    # A missing @file is a clean error, not a traceback.
    with pytest.raises(SystemExit):
        contests.resolve_context(f'@{tmp_path / "nope.txt"}')
