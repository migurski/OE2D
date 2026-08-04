'''Load the contest-locating gold sets.

Two purpose-specific sets live in oe2d-data/contests/, deliberately kept apart so the
short committed samples are never confused with the full originals again:

- training-sample-excerpts.jsonl -- contest pages in the committed short sample PDFs, in
  FIXTURE-LOCAL coordinates (page 1..N of the trimmed file). Hermetic: runs offline, no
  network. Use for fast smoke tests of the locator. A 2-4 page excerpt cannot show
  by-precinct or split-across-many-pages structure, so this set only checks "does it land
  on the right local pages?", not structural correctness.
- training-full-documents.jsonl -- contest span in the FULL url-referenced documents, in
  ORIGINAL coordinates. The real evaluation target (download via source_url). Carries the
  document `organization`, an explicit `pages` list for by-precinct / non-contiguous
  contests, a `confidence`, and `notes`.

`source_pages` (in training-sample-excerpts.jsonl) is the bridge: fixture-local page k
corresponds to original page source_pages[k-1].
'''
from __future__ import annotations

import hashlib
import json
import os
import urllib.request

from .. import contests

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'contests')
_FIXTURES_PATH: str = os.path.join(_DATA_DIR, 'training-sample-excerpts.jsonl')
_ORIGINALS_PATH: str = os.path.join(_DATA_DIR, 'training-full-documents.jsonl')
_CACHE_DIR: str = os.path.join(_DATA_DIR, '.cache')


def _load(path: str) -> list[dict]:
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_fixtures(path: str = _FIXTURES_PATH) -> list[dict]:
    '''Rows describing the committed short samples (fixture-local coordinates).'''
    return _load(path)


def load_originals(path: str = _ORIGINALS_PATH) -> list[dict]:
    '''Rows describing the full url-referenced documents (original coordinates).'''
    return _load(path)


def fetch_original(row: dict) -> str:
    '''Download a full-document row's source to a local cache (once) and return its path. Named by a
    hash of the source URL, so the several gold rows sharing one document (Alameda's six targets)
    download ONE copy.'''
    os.makedirs(_CACHE_DIR, exist_ok=True)
    url: str = row['source_url']
    name: str = hashlib.sha1(url.encode()).hexdigest()[:16] + os.path.splitext(url)[1]
    path: str = os.path.join(_CACHE_DIR, name)
    if not os.path.exists(path):
        urllib.request.urlretrieve(url, path)
    return path


def row_target(row: dict) -> contests.Target:
    '''The Target (contest label + free-form context) for a gold row (either set). The gold
    candidate names are folded into the context prose the LLM reads to interpret the title.'''
    candidates: list[str] = list(row.get('candidates', []))
    names: list[str] = [c for c in candidates if len(c) > 3]      # drop DEM/REP-style codes
    context: str = (f'{row["target"]} race; candidates include {", ".join(names)}'
                    if names else f'{row["target"]} race')
    return contests.Target(contest=row['target'], context=context)


def fixture_path(row: dict) -> str:
    '''Absolute path to a fixture row's local trimmed sample.'''
    return os.path.normpath(os.path.join(_DATA_DIR, row['fixture_path']))


def fixture_request(name_substring: str) -> tuple[str, list[contests.Target], list[int] | None]:
    '''Resolve a committed fixture by name to (local path, [Target], fixture_range).

    Offline: runs against the trimmed sample. The range is in fixture-local coordinates.
    Returns every target labeled for that fixture (usually one).
    '''
    rows: list[dict] = [r for r in load_fixtures() if name_substring in r['fixture_path']]
    if not rows:
        raise SystemExit(f'no gold fixture matching {name_substring!r}')
    targets: list[contests.Target] = [row_target(r) for r in rows]
    return fixture_path(rows[0]), targets, rows[0].get('fixture_range')
