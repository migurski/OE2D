'''Load the vote-extraction gold set (oe2d-data/votes/).

Each gold example is an index.jsonl record (metadata: office, district, source_url, the contest
pages, container, geometry/schema features, checksums, candidate_context) plus a canonical
`<county>__<contest>__expected.csv` of the rows the extractor must reproduce. The numbers are
copied from human-authored state-repo CSVs -- they are the ground truth, not re-derived from the
PDFs. Sources live remotely (openelections-sources-*); fetch_source downloads and caches one.
'''
from __future__ import annotations

import csv
import json
import os
import urllib.request

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DATA_DIR: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'votes')
_INDEX_PATH: str = os.path.join(_DATA_DIR, 'index.jsonl')
_CACHE_DIR: str = os.path.join(_DATA_DIR, '.cache')


def load_index(path: str = _INDEX_PATH) -> list[dict]:
    '''Every gold record (metadata only).'''
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def find(id_substring: str) -> dict:
    '''The single gold record whose id contains id_substring (error if not exactly one).'''
    matches: list[dict] = [r for r in load_index() if id_substring in r['id']]
    if len(matches) != 1:
        raise SystemExit('want exactly one gold id matching %r, found %d' % (id_substring, len(matches)))
    return matches[0]


def expected_rows(record: dict) -> list[dict]:
    '''The canonical gold rows for a record.'''
    path: str = os.path.join(_DATA_DIR, record['expected_csv'])
    with open(path, encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def candidate_context(record: dict) -> str:
    '''The expected-candidate prose supplied to the interpreter, one "Name (PARTY)" line per
    distinct candidate. Stands in for what oe2d.contests provides from external race knowledge.

    Deliberately does NOT string-classify rows into candidate vs write-in/vote-integrity -- that
    is language interpretation, which is the LLM's job, not Python's. Every distinct label is
    listed; the interpreter matches columns/rows to these (echoing the supplied name+party) and
    keeps anything unmatched verbatim, so listing a special here is harmless. (In production the
    list comes from oe2d.contests and naturally contains only the real candidates.)'''
    lines: list[str] = []
    seen: set[str] = set()
    for row in expected_rows(record):
        name, party = row['candidate'], row['party']
        if name in seen:
            continue
        seen.add(name)
        lines.append('%s (%s)' % (name, party) if party else name)
    return 'Expected candidates in this contest:\n' + '\n'.join('- ' + line for line in lines)


def fetch_source(record: dict) -> str:
    '''Download the record's source file to a local cache (once) and return its path.'''
    os.makedirs(_CACHE_DIR, exist_ok=True)
    name: str = record['id'] + os.path.splitext(record['source_url'])[1]
    path: str = os.path.join(_CACHE_DIR, name)
    if not os.path.exists(path):
        urllib.request.urlretrieve(record['source_url'], path)
    return path
