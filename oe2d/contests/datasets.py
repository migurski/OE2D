'''Load the enriched contest-locating gold set (oe2d-data/labels/segments.jsonl).

Each row pairs a source file with one target contest, its candidate hint tokens,
and the unit range where it appears (in the ORIGINAL document's coordinates, paired
with source_url). The trimmed fixture at `fixture_path` is a local smoke-test stand-
in; real evaluation runs against the originals via source_url.
'''
from __future__ import annotations

import json
import os

from . import Target

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LABELS_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'labels')
_SEGMENTS_PATH: str = os.path.join(_LABELS_DIR, 'segments.jsonl')


def load_rows(path: str = _SEGMENTS_PATH) -> list[dict]:
    '''Read the enriched segments.jsonl into a list of dicts.'''
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def fixture_path(row: dict) -> str:
    '''Absolute path to a row's local trimmed fixture.'''
    return os.path.normpath(os.path.join(_LABELS_DIR, row['fixture_path']))


def row_target(row: dict) -> Target:
    '''The Target (contest + hint tokens) for a gold row.'''
    return Target(contest=row['target'], hints=list(row.get('candidates', [])))


def gold_request(name_substring: str) -> tuple[str, list[Target], list[int]]:
    '''Resolve a fixture by name substring to (local path, [Target], original range).

    Returns every target labeled for that fixture (usually one) and the original-
    document range from the first matching row, for reference.
    '''
    rows: list[dict] = [r for r in load_rows() if name_substring in r['fixture_path']]
    if not rows:
        raise SystemExit(f'no gold fixture matching {name_substring!r}')
    path: str = fixture_path(rows[0])
    targets: list[Target] = [row_target(r) for r in rows]
    return path, targets, rows[0]['range']
