'''Measure the deterministic locator against the originals gold.

Runs the cheap, no-LLM scan (scan_for_targets + build_evidence) on each full original
document (downloaded and cached via source_url) and scores the predicted ranges against
the corrected gold with oe2d.contests.metrics. Prints a per-file and aggregate scorecard,
broken down by document organization -- the empirical baseline for deciding what the scan
still needs.

Usage: python -m oe2d.contests.evaluate [--only NAME] [--limit N] [--budget N]
       [--cache DIR] [--max-gap N]
'''
from __future__ import annotations

import argparse
import os
import time
import urllib.parse
import urllib.request

from . import (Target, count_units, scan_for_targets, build_evidence, DEFAULT_MAX_GAP,
               contest_title_index, title_segments)
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


def name_runs(path: str, target: Target, units: int, max_gap: int,
              budget: int | None) -> list[tuple[int, int]]:
    '''Name-based scan runs (the committed scan): fuzzy-match hint tokens, bridge gaps.'''
    hits = scan_for_targets(path, [target], unit_count=units, max_gap=max_gap, page_budget=budget)
    evidence = build_evidence(hits, [target], max_gap, unit_count=units)
    return [(e.unit_start, e.unit_end) for e in evidence if e.scan_guess == target.contest]


def title_runs(path: str, target: Target, units: int,
               budget: int | None) -> list[tuple[int, int]]:
    '''Title-based spans: contest title -> next title - 1 (handles by_contest + by_precinct).'''
    index = contest_title_index(path, unit_count=units, page_budget=budget)
    return title_segments(index, target, units)


def predicted_runs(path: str, target: Target, max_gap: int, budget: int | None,
                   predictor: str) -> list[tuple[int, int]]:
    '''Runs for one target on one document (no LLM), by the chosen predictor.'''
    units: int = count_units(path)
    if predictor == 'name':
        return name_runs(path, target, units, max_gap, budget)
    if predictor == 'title':
        return title_runs(path, target, units, budget)
    # union: title spans, falling back to / combined with name runs where titles miss.
    runs = title_runs(path, target, units, budget)
    return runs + name_runs(path, target, units, max_gap, budget)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', help='Substring filter on source_url (one/few files)')
    parser.add_argument('--exclude', help='Skip files whose source_url contains this substring')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--budget', type=int, default=None, help='Cap units scanned per file')
    parser.add_argument('--max-gap', type=int, default=DEFAULT_MAX_GAP)
    parser.add_argument('--cache', default=_DEFAULT_CACHE)
    parser.add_argument('--predictor', choices=('name', 'title', 'union'), default='name',
                        help='name = committed scan; title = title-to-next-title; union = both')
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
        runs = predicted_runs(path, target, args.max_gap, args.budget, args.predictor)
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
