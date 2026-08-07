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

import collections
import json
import os
import urllib.request

import dspy

from .. import config, contests
from . import metrics

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'contests')
_FIXTURES_PATH: str = os.path.join(_DATA_DIR, 'training-sample-excerpts.jsonl')
_ORIGINALS_PATH: str = os.path.join(_DATA_DIR, 'training-full-documents.jsonl')
_CACHE_DIR: str = config.SOURCE_CACHE_DIR


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
    '''Download a full-document row's source to the shared source cache (once) and return its path.
    Named by a readable slug of the file plus a url hash (config.source_cache_name), so the several gold
    rows sharing one document (Alameda's six targets) download ONE copy.'''
    os.makedirs(_CACHE_DIR, exist_ok=True)
    url: str = row['source_url']
    path: str = os.path.join(_CACHE_DIR, config.source_cache_name(url))
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
    return contests.Target(contest=row['target'], electoral_context=context)


def fixture_path(row: dict) -> str:
    '''Absolute path to a fixture row's local trimmed sample.'''
    return os.path.normpath(os.path.join(_DATA_DIR, row['fixture_path']))


def load_examples(rows: list[dict] | None = None) -> list[dspy.Example]:
    '''One dspy.Example per DOCUMENT (all its target contests), for GEPA/evaluate over the full
    documents. Inputs are the local file path (downloaded) and the document's Targets; the gold rides
    as `gold_targets` (per contest: label, page set, observed title) for the score_location metric.
    Grouping by document matches how the locator runs -- classify once per document, the ReAct match
    per target -- so a GEPA rollout is one document-run, not one redundant OCR per target. Pass `rows`
    to build examples for a curated subset (only those documents are downloaded).'''
    rows = rows if rows is not None else load_originals()
    by_doc: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_doc[row['source_url']].append(row)
    examples: list[dspy.Example] = []
    for url, group in by_doc.items():
        gold_targets: list[dict] = [{'target': r['target'], 'pages': sorted(metrics.gold_pages(r)),
                                     'observed_title': r.get('observed_title', '')} for r in group]
        example: dspy.Example = dspy.Example(
            file_path=fetch_original(group[0]),
            targets=[row_target(r) for r in group],
            gold_targets=gold_targets).with_inputs('file_path', 'targets')
        example._id = url
        examples.append(example)
    return examples


def split(examples: list[dspy.Example], val_fraction: float = 0.3) -> tuple[list, list]:
    '''Deterministic train/val split BY DOCUMENT (an example is one document), so a document's
    contests never straddle the split. Sorted by id, every round(1/val_fraction)-th document is
    held out for validation.'''
    stride: int = max(2, round(1 / val_fraction))
    ordered: list = sorted(examples, key=lambda e: getattr(e, '_id', ''))
    val: list = [e for i, e in enumerate(ordered) if i % stride == 0]
    train: list = [e for i, e in enumerate(ordered) if i % stride != 0]
    return train, val


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
