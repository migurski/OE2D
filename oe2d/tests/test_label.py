'''Tests for oe2d.label — non-interactive helpers only.'''
import os

from oe2d import label

FIXTURES: str = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_iter_targets_skips_readme():
    targets = label.iter_targets(FIXTURES)
    assert len(targets) > 50
    assert all(not t.lower().endswith('.md') for t in targets)
    assert all(os.path.isfile(t) for t in targets)


def test_done_roundtrip(tmp_path):
    out = str(tmp_path / 'category.jsonl')
    record = {'path': 'x.pdf', 'container': 'scanned_pdf',
              'orientation': 'candidate_rows', 'grain': 'precinct',
              'quirks': ['rotated_headers']}
    label.append_record(out, record)
    label.append_record(out, {**record, 'path': 'y.xlsx'})
    done = label.load_done(out)
    assert set(done) == {'x.pdf', 'y.xlsx'}
    assert done['x.pdf']['orientation'] == 'candidate_rows'


def test_preview_text_for_xlsx():
    xlsx = next(t for t in label.iter_targets(FIXTURES) if t.endswith('.xlsx'))
    preview = label.format_preview(xlsx, 'xlsx')
    assert preview and '\n' in preview


def test_preview_none_for_zip():
    zips = [t for t in label.iter_targets(FIXTURES) if t.endswith('.zip')]
    assert label.format_preview(zips[0], 'zip') is None
