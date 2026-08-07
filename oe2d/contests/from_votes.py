'''Convert the votes gold into contest-locating gold rows.

The votes gold (oe2d-data/votes/index.jsonl) is already validated contest-locate gold: every record
carries a source document, an office+district that names one contest, the exact page set where that
contest lives (confirmed to 1.000 when the votes extraction was built), and the candidate names. Those
are precisely the fields metrics.score_location scores, so the votes set roughly doubles the contests
eval gold and adds whole documents the hand-curated set lacks (Bay, Branch, Nevada, ...). This de-risks
the Bedrock-migration decision: a GEPA result that holds on ~110 targets is trustworthy where 60 could
be coincidence.

This is a ONE-SHOT translation, not a runtime dependency: --write appends the converted rows straight
into the curated originals (training-full-documents.jsonl), each tagged `provenance: "votes"` so its
origin stays visible, and the loader reads that one file plainly. A votes (source_url, target) pair
already present is skipped, so a re-run is idempotent -- but a NEW target on a shared document (Alameda's
U.S. House District 17) is kept, and load_examples groups by document so nothing straddles the split.

    python -m oe2d.contests.from_votes            # dry run: report what would be added
    python -m oe2d.contests.from_votes --write    # append the converted rows into the gold file
'''
from __future__ import annotations

import argparse
import json
import os

from . import datasets

_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VOTES_INDEX: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'votes', 'index.jsonl')
_ORIGINALS: str = os.path.join(_REPO_ROOT, 'oe2d-data', 'contests', 'training-full-documents.jsonl')


def has_district(district) -> bool:
    '''Whether a votes `district` field names a real district. The votes gold spells "no district"
    inconsistently -- None, '', or [] -- so a truthy scalar is the only positive signal.'''
    return bool(district) and not isinstance(district, (list, dict))


def target_label(office: str, district) -> str:
    '''The contest label in the contests-gold convention: "U.S. House District 5" when districted,
    else the bare office. (CA's "U.S. Senate (full term)/(partial term)" splits are CA-only and absent
    from the votes set, so a plain office never collides with them.)'''
    return '%s District %s' % (office, district) if has_district(district) else office


def context_prose(target: str, candidates: list[str]) -> str:
    '''Render a candidate list into the free-form electoral-context prose a caller supplies
    ("Candidates for <office> were A, B, and C"), matching how context arrives in real life.'''
    names: list[str] = [c for c in candidates if len(c) > 3]      # drop bare party codes
    label: str = 'president' if target == 'President' else target
    if not names:
        return '%s race' % target
    joined: str = (names[0] if len(names) == 1 else '%s and %s' % (names[0], names[1]) if len(names) == 2
                   else '%s, and %s' % (', '.join(names[:-1]), names[-1]))
    return 'Candidates for %s were %s' % (label, joined)


def to_contest_row(votes_row: dict) -> dict:
    '''One votes index record -> one contests full-document gold row.'''
    pages: list[int] = sorted(votes_row['pages'])
    target: str = target_label(votes_row['office'], votes_row.get('district'))
    return {
        'source_url': votes_row['source_url'],
        'target': target,
        'observed_title': votes_row.get('observed_title', ''),
        'electoral_context': context_prose(target, list(votes_row.get('electoral_context', []))),
        'unit_type': 'page',
        'range': [pages[0], pages[-1]],
        'pages': pages,
        'organization': 'from_votes',
        'confidence': 'high',
        'notes': 'converted from votes gold %r; page set validated to 1.000 in votes extraction'
                 % votes_row.get('id', ''),
        'provenance': 'votes',
    }


def existing_keys(rows: list[dict]) -> set[tuple[str, str]]:
    '''The (source_url, target) pairs already covered by a set of gold rows.'''
    return {(row['source_url'], row['target']) for row in rows}


def convert(votes_rows: list[dict], existing: set[tuple[str, str]]) -> list[dict]:
    '''Convert votes rows to contest rows, skipping (url, target) pairs already in `existing` and any
    duplicate within the votes set itself. Rows missing a page set are skipped (nothing to locate).'''
    out: list[dict] = []
    seen: set[tuple[str, str]] = set(existing)
    for votes_row in votes_rows:
        if not votes_row.get('pages'):
            continue
        row: dict = to_contest_row(votes_row)
        key: tuple[str, str] = (row['source_url'], row['target'])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _load_jsonl(path: str) -> list[dict]:
    with open(path, encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Convert the votes gold into contest-locating gold rows.')
    parser.add_argument('--votes-index', default=_VOTES_INDEX)
    parser.add_argument('--out', default=_ORIGINALS,
                        help='Gold file to append the converted rows into (default: the curated originals)')
    parser.add_argument('--write', action='store_true',
                        help='Actually append the rows (default is a dry-run report)')
    args: argparse.Namespace = parser.parse_args()

    votes_rows: list[dict] = _load_jsonl(args.votes_index)
    curated: list[dict] = datasets.load_originals()
    new_rows: list[dict] = convert(votes_rows, existing_keys(curated))

    docs: set[str] = {row['source_url'] for row in new_rows}
    curated_docs: set[str] = {row['source_url'] for row in curated}
    print('votes rows: %d   curated targets: %d over %d doc(s)'
          % (len(votes_rows), len(curated), len(curated_docs)))
    print('new contest targets from votes: %d over %d doc(s) (%d brand-new doc(s))'
          % (len(new_rows), len(docs), len(docs - curated_docs)))
    print('merged eval set would be %d targets.' % (len(curated) + len(new_rows)))
    for row in new_rows:
        tag: str = 'NEW-DOC' if row['source_url'] not in curated_docs else 'add-target'
        print('  [%s] %-34s %s' % (tag, row['target'][:34], row['source_url'].split('/')[-1][:40]))

    if not new_rows:
        print('\nnothing to add -- every votes target is already present (idempotent).')
        return
    if not args.write:
        print('\n(dry run -- pass --write to append these %d rows to %s)' % (len(new_rows), args.out))
        return
    with open(args.out, 'a', encoding='utf-8') as handle:
        for row in new_rows:
            handle.write(json.dumps(row) + '\n')
    print('\nappended %d rows to %s' % (len(new_rows), args.out))


if __name__ == '__main__':
    main()
