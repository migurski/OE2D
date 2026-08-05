'''Score the contest locator against the gold sets.

The metric is page-set recall/precision/F1 per target (metrics.score_pages): did the located pages
cover the target contest's true pages, without dragging in others? The real evaluation is the
full-documents set (download via source_url, original coordinates); the committed fixture excerpts
(local coordinates, offline) are the fast smoke test.

Runs the locator ONCE per document with all that document's targets -- classify is document-wide, and
the ReAct match runs per target, so grouping matches how the pipeline is meant to run and avoids
re-OCRing a document per target. --student swaps the interpreter LM (stock prompts) so a model sweep
can find a Bedrock replacement that holds the incumbent's accuracy; --model evaluates a saved artifact.

    oe2d-contests-evaluate                 # full-documents gold on the shipped LM
    oe2d-contests-evaluate --fixtures      # fast offline smoke test on the committed excerpts
    oe2d-contests-evaluate --student bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
'''
from __future__ import annotations

import argparse
import collections
import logging
import sys
import time

import dspy

from .. import contests
from . import datasets, metrics

logger: logging.Logger = logging.getLogger(__name__)


def load_locator(student: str | None, model: str | None) -> contests.ContestLocator:
    '''The locator to score: a saved artifact (--model, its prompts AND lm win), else a stock locator
    on the given --student LM, else the shipped default (build_locator).'''
    contests._instrument()
    if model:
        locator: contests.ContestLocator = contests.ContestLocator()
        locator.load(model)
        return locator
    if student:
        locator = contests.ContestLocator()
        locator.set_lm(dspy.LM(student, temperature=0.0, max_tokens=8192))
        return locator
    return contests.build_locator()


def _gold_pages(row: dict, fixtures: bool) -> set[int]:
    '''The gold page set for a row. Fixtures carry a local-coordinate `fixture_range`; the full
    documents carry `range`/`pages` in original coordinates (metrics.gold_pages).'''
    if fixtures:
        span = row.get('fixture_range')
        return set(range(span[0], span[1] + 1)) if span else set()
    return metrics.gold_pages(row)


def _title_hit(gold_title: str, observed: str | None) -> bool:
    '''Did the located observed_title match the gold one (whitespace/case-tolerant)? A coarse probe of
    the match predictor alone, separate from the page score.'''
    if not observed:
        return False
    norm = lambda s: ' '.join(s.split()).lower()
    return norm(gold_title) == norm(observed)


def score_documents(locator: contests.ContestLocator, rows: list[dict],
                    fixtures: bool) -> list[dict]:
    '''Run the locator once per document (all its targets) and score each target's pages against gold.
    fixtures uses the local committed excerpts (offline, local coordinates); otherwise the full
    documents are downloaded and scored in original coordinates.'''
    by_doc: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        key: str = row['fixture_path'] if fixtures else row['source_url']
        by_doc[key].append(row)
    results: list[dict] = []
    started: float = time.monotonic()
    for index, (key, group) in enumerate(by_doc.items(), 1):
        logger.info('locating %d/%d %s (%d target(s)) ...', index, len(by_doc), key, len(group))
        try:
            path: str = datasets.fixture_path(group[0]) if fixtures else datasets.fetch_original(group[0])
            targets: list[contests.Target] = [datasets.row_target(row) for row in group]
            prediction = locator(file_path=path, targets=targets)
            located: dict[str, contests.ContestLocation] = {loc.target: loc for loc in prediction.locations}
        except Exception as error:
            for row in group:
                results.append({'target': row['target'], 'doc': key,
                                'error': '%s: %s' % (type(error).__name__, str(error)[:80])})
            continue
        for row in group:
            location = located.get(row['target'])
            pred_pages: set[int] = set(location.pages) if location else set()
            gold: set[int] = _gold_pages(row, fixtures)
            score: dict = metrics.score_pages(gold, pred_pages)
            results.append({'target': row['target'], 'doc': key, 'metric': score,
                            'gold': len(gold), 'got': len(pred_pages),
                            'title_hit': _title_hit(row.get('observed_title', ''),
                                                     location.observed_title if location else None)})
    logger.info('located %d target(s) across %d document(s) in %.0fs',
                len(rows), len(by_doc), time.monotonic() - started)
    return results


def print_report(results: list[dict]) -> None:
    '''Per-target line plus macro recall/precision/F1 and the title-hit rate.'''
    recalls: list[float] = []
    precisions: list[float] = []
    f1s: list[float] = []
    hits: int = 0
    for record in results:
        if 'error' in record:
            print('%-46s ERROR %s' % (record['target'][:46], record['error']))
            continue
        metric: dict = record['metric']
        recalls.append(metric['recall'])
        precisions.append(metric['precision'])
        f1s.append(metric['f1'])
        hits += record['title_hit']
        print('%-30s R=%.2f P=%.2f F1=%.2f  got=%d gold=%d  title=%s'
              % (record['target'][:30], metric['recall'], metric['precision'], metric['f1'],
                 record['got'], record['gold'], 'hit' if record['title_hit'] else 'MISS'))
    scored: int = len(f1s)
    if scored:
        print('\nmacro over %d target(s): recall=%.3f  precision=%.3f  F1=%.3f  title-hit=%d/%d'
              % (scored, sum(recalls) / scored, sum(precisions) / scored, sum(f1s) / scored,
                 hits, scored))
    errors: int = sum('error' in r for r in results)
    if errors:
        print('%d target(s) errored (see above).' % errors)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Score the contest locator (page-set recall/precision/F1 per target).')
    parser.add_argument('--student', default=None,
                        help='litellm model id for the interpreter LM on the STOCK prompts, e.g. '
                             'bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0')
    parser.add_argument('--model', default=None,
                        help='Evaluate a saved locator artifact instead (its baked-in LM governs)')
    parser.add_argument('--fixtures', action='store_true',
                        help='Score the committed excerpt fixtures (offline, local coordinates); '
                             'default is the full-documents gold (downloads via source_url)')
    parser.add_argument('--only', default=None,
                        help='Score only targets whose contest label contains this substring')
    parser.add_argument('--doc', default=None,
                        help='Score only documents whose source_url (or fixture_path) contains this '
                             'substring -- for cheaply re-scoring a few documents')
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)

    rows: list[dict] = datasets.load_fixtures() if args.fixtures else datasets.load_originals()
    if args.only:
        rows = [r for r in rows if args.only.lower() in r['target'].lower()]
    if args.doc:
        key: str = 'fixture_path' if args.fixtures else 'source_url'
        rows = [r for r in rows if args.doc.lower() in r[key].lower()]
    if not rows:
        raise SystemExit('no gold rows to score')

    locator: contests.ContestLocator = load_locator(args.student, args.model)
    which: str = 'fixtures' if args.fixtures else 'full documents'
    print('evaluating the contest locator over %d target(s) [%s]' % (len(rows), which))
    print_report(score_documents(locator, rows, args.fixtures))


if __name__ == '__main__':
    main()
