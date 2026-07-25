'''Load the contest-locating gold sets.

Two purpose-specific sets live in oe2d-data/labels/, deliberately kept apart so the
short committed samples are never confused with the full originals again:

- fixtures.jsonl  -- contest pages in the committed short sample PDFs, in FIXTURE-LOCAL
  coordinates (page 1..N of the trimmed file). Hermetic: runs offline, no network. Use
  for fast smoke tests of the locator. A 2-4 page excerpt cannot show by-precinct or
  split-across-many-pages structure, so this set only checks "does it land on the right
  local pages?", not structural correctness.
- originals.jsonl -- contest span in the FULL url-referenced documents, in ORIGINAL
  coordinates. The real evaluation target (download via source_url). Carries the
  document `organization`, an explicit `pages` list for by-precinct / non-contiguous
  contests, a `confidence`, and `notes`.

`source_pages` (in fixtures.jsonl) is the bridge: fixture-local page k corresponds to
original page source_pages[k-1].
'''
from __future__ import annotations

import json
import os

from . import Target

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LABELS_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'labels')
_FIXTURES_PATH: str = os.path.join(_LABELS_DIR, 'fixtures.jsonl')
_ORIGINALS_PATH: str = os.path.join(_LABELS_DIR, 'originals.jsonl')


def _load(path: str) -> list[dict]:
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_fixtures(path: str = _FIXTURES_PATH) -> list[dict]:
    '''Rows describing the committed short samples (fixture-local coordinates).'''
    return _load(path)


def load_originals(path: str = _ORIGINALS_PATH) -> list[dict]:
    '''Rows describing the full url-referenced documents (original coordinates).'''
    return _load(path)


def row_target(row: dict) -> Target:
    '''The Target (contest + hint tokens) for a gold row (either set).'''
    return Target(contest=row['target'], hints=list(row.get('candidates', [])))


def fixture_path(row: dict) -> str:
    '''Absolute path to a fixtures.jsonl row's local trimmed sample.'''
    return os.path.normpath(os.path.join(_LABELS_DIR, row['fixture_path']))


def fixture_request(name_substring: str) -> tuple[str, list[Target], list[int] | None]:
    '''Resolve a committed fixture by name to (local path, [Target], fixture_range).

    Offline: runs against the trimmed sample. The range is in fixture-local coordinates.
    Returns every target labeled for that fixture (usually one).
    '''
    rows: list[dict] = [r for r in load_fixtures() if name_substring in r['fixture_path']]
    if not rows:
        raise SystemExit(f'no gold fixture matching {name_substring!r}')
    targets: list[Target] = [row_target(r) for r in rows]
    return fixture_path(rows[0]), targets, rows[0].get('fixture_range')
