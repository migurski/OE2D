'''Measure the deterministic title locator against the originals gold.

Runs the cheap, no-LLM title detection (contest_title_index + _title_matches -> segments)
on each full original document (downloaded and cached via source_url) and scores the
predicted page set against the corrected gold with oe2d.contests.metrics. Prints a per-file
and aggregate scorecard broken down by document organization -- the deterministic floor the
LLM interpret step builds on (it can't bridge wording variants like senate~senator, so
Senate/House score 0 here and the LLM recovers them).

Usage: python -m oe2d.contests.evaluate [--only NAME] [--exclude NAME] [--limit N]
       [--budget N] [--cache DIR]
'''
from __future__ import annotations

import argparse
import os
import time
import urllib.parse
import urllib.request

from . import Target, count_units, contest_title_index, segments_for_titles, _title_matches
from . import datasets, metrics

_DEFAULT_CACHE: str = os.environ.get('OE2D_ORIGINALS_CACHE', '/tmp/oe2d-originals')


def cache_name(source_url: str) -> str:
    '''Stable local filename for a source_url (its unquoted basename).'''
    base: str = urllib.parse.unquote(source_url.rsplit('/', 1)[-1])
    return base.replace('/', '_')


def resolve(source_url: str, cache_dir: str) -> str:
    '''Local path to the original, downloading into the cache on first use.'''
    os.makedirs(cache_dir, exist_ok=True)
    path: str = os.path.join(cache_dir, cache_name(source_url))
    if not os.path.exists(path):
        urllib.request.urlretrieve(source_url, path)
    return path


def title_runs(path: str, target: Target, budget: int | None) -> list[tuple[int, int]]:
    '''Deterministic title spans: exact-word-match a target to the document's own titles,
    then each matched title to the next title - 1. Empty where wording differs (the LLM's job).'''
    units: int = count_units(path)
    index = contest_title_index(path, unit_count=units, page_budget=budget)
    matched = {t for titles in index.values() for t in titles if _title_matches(target, t)}
    return segments_for_titles(index, matched, units)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', help='Substring filter on source_url (one/few files)')
    parser.add_argument('--exclude', help='Skip files whose source_url contains this substring')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--budget', type=int, default=None, help='Cap units read per file')
    parser.add_argument('--cache', default=_DEFAULT_CACHE)
    args = parser.parse_args()

    rows = datasets.load_originals()
    if args.only:
        rows = [r for r in rows if args.only.lower() in r['source_url'].lower()]
    if args.exclude:
        rows = [r for r in rows if args.exclude.lower() not in r['source_url'].lower()]
    if args.limit:
        rows = rows[:args.limit]

    print(f'{"file":26s} {"org":22s} {"target":16s} '
          f'{"recall":>7s} {"prec":>6s} {"f1":>6s} {"region":>7s} {"secs":>6s}')
    by_org: dict[str, list[dict]] = {}
    for row in rows:
        name: str = cache_name(row['source_url'])[:24]
        target: Target = datasets.row_target(row)
        started: float = time.monotonic()
        path: str = resolve(row['source_url'], args.cache)
        runs = title_runs(path, target, args.budget)
        elapsed: float = time.monotonic() - started
        s = metrics.score_row(row, runs)
        by_org.setdefault(row['organization'], []).append(s)
        print(f'{name:26s} {row["organization"]:22s} {row["target"][:16]:16s} '
              f'{s["recall"]:7.2f} {s["precision"]:6.2f} {s["f1"]:6.2f} '
              f'{"yes" if s["region_hit"] else "NO":>7s} {elapsed:6.1f}')

    print('\nBy organization (mean):')
    for org, scores in sorted(by_org.items()):
        n = len(scores)
        mr = sum(s['recall'] for s in scores) / n
        mp = sum(s['precision'] for s in scores) / n
        mf = sum(s['f1'] for s in scores) / n
        hit = sum(1 for s in scores if s['region_hit'])
        print(f'  {org:26s} n={n:2d}  recall={mr:.2f} prec={mp:.2f} f1={mf:.2f} '
              f'region_hit={hit}/{n}')

    alls = [s for v in by_org.values() for s in v]
    n = len(alls)
    print(f'\nOverall n={n}  recall={sum(s["recall"] for s in alls)/n:.2f} '
          f'precision={sum(s["precision"] for s in alls)/n:.2f} '
          f'f1={sum(s["f1"] for s in alls)/n:.2f} '
          f'region_hit={sum(1 for s in alls if s["region_hit"])}/{n}')


if __name__ == '__main__':
    main()
