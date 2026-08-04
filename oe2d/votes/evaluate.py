'''Score the vote extractor on the whole gold set: per-contest and aggregate plain + weighted F1.

No GEPA -- just run a program over oe2d-data/votes and report, so you can see where the extractor
still errs (and by how much, in votes) before spending an optimization budget, or regression-check
the shipped program. Three things to evaluate:

- --student MODEL : a candidate interpreter LM on the STOCK prompt (baseline a model)
- --model PATH    : a saved program artifact (its own baked-in LM governs)
- neither         : the shipped program (build_extractor -- committed artifact or stock LM)

Every example runs the same read -> interpret -> stitch -> rows flow the CLI runs; a contest's
dispatch (orientation, read_strategy, district) comes from its gold record. Scanned contests read
via Textract, so AWS creds must be set. Credentials come from the environment; set CMPND_API_KEY to
trace to cmpnd.

Usage:
    oe2d-votes-evaluate
    oe2d-votes-evaluate --only barry
    oe2d-votes-evaluate --model oe2d/votes/model/optimized_vote_extractor.json
'''
from __future__ import annotations

import argparse
import logging
import sys
import time

import dspy

from .. import votes
from . import datasets, metrics

logger: logging.Logger = logging.getLogger(__name__)


def build_target(student: str | None, model: str | None,
                 temperature: float, max_tokens: int, num_retries: int) -> votes.VoteExtractor:
    '''The program to evaluate: a saved artifact (its own LM governs), a candidate interpreter LM on
    the stock prompts, or the shipped program.'''
    if model:
        extractor: votes.VoteExtractor = votes.VoteExtractor()
        extractor.load(model)                 # artifact carries its own lm; it governs
        return extractor
    if student:
        extractor = votes.VoteExtractor()
        extractor.set_lm(dspy.LM(student, temperature=temperature, max_tokens=max_tokens,
                                 num_retries=num_retries))
        return extractor
    return votes.build_extractor()            # shipped program (committed artifact or stock LM)


def _interpreter(example) -> str:
    '''Which of the two named predictors this contest exercises: the rows-family (a per-precinct/report
    contest) runs interpret_rows, everything else runs interpret_columns. The gold record's
    orientation and read_strategy decide it -- the same partition GEPA optimizes each predictor over,
    so a sweep reports each interpreter's accuracy separately (they diverge, and one is a cheaper
    model's easier target than the other).'''
    read_strategy: str = getattr(example, 'read_strategy', '') or ''
    orientation: str = getattr(example, 'orientation', '') or ''
    return 'interpret_rows' if orientation == 'rows' or read_strategy.startswith('report_lines') \
        else 'interpret_columns'


def score_examples(program: votes.VoteExtractor, examples: list, detected: bool = False) -> list[dict]:
    '''Run the program on each example and score its rows against the gold. Returns one result dict
    per example (id, container, the metric dict, and got/gold counts). A failed extraction is
    recorded with error text rather than aborting the whole sweep. With detected=True the gold
    orientation/read_strategy are withheld so forward DETECTS them from the page (checksum-confirmed),
    exercising the end-to-end image-driven dispatch instead of the gold field.'''
    results: list[dict] = []
    started: float = time.monotonic()
    for index, example in enumerate(examples, 1):
        logger.info('scoring %d/%d %s ...', index, len(examples), getattr(example, '_id', '?'))
        record: dict = {'id': getattr(example, '_id', '?'),
                        'container': getattr(example, '_container', ''),
                        'orientation': example.orientation,
                        'interpreter': _interpreter(example)}
        inputs: dict = dict(example.inputs())
        if detected:
            inputs['orientation'] = None
            inputs['read_strategy'] = None
        try:
            prediction = program(**inputs)
            metric: dict = metrics.score(prediction.rows, example.rows)
            record.update(metric=metric, got=len(prediction.rows), gold=len(example.rows))
        except Exception as error:            # keep sweeping; a scanned/LM outage shouldn't hide the rest
            record.update(error='%s: %s' % (type(error).__name__, str(error)[:80]))
        results.append(record)
    logger.info('scored %d contests in %.0fs', len(examples), time.monotonic() - started)
    return results


def print_report(results: list[dict]) -> None:
    '''Per-contest line plus a macro-average of weighted and plain F1 over the scored contests.'''
    weighted: list[float] = []
    plain: list[float] = []
    for record in results:
        if 'error' in record:
            print('%-40s %-7s ERROR %s' % (record['id'], record['orientation'], record['error']))
            continue
        metric: dict = record['metric']
        weighted.append(metric['weighted_f1'])
        plain.append(metric['f1'])
        print('%-40s %-7s wF1=%.3f F1=%.3f (tp=%d fp=%d fn=%d)  got=%d gold=%d'
              % (record['id'], record['orientation'], metric['weighted_f1'], metric['f1'],
                 metric['true_positive'], metric['false_positive'], metric['false_negative'],
                 record['got'], record['gold']))
        for tag in ('false_negatives', 'false_positives'):
            for key in metric[tag][:3]:
                print('      %s %s' % (tag[:2].upper(), metrics._describe(key)))
    if weighted:
        print('\nmacro-average over %d scored contest(s): wF1=%.3f  F1=%.3f'
              % (len(weighted), sum(weighted) / len(weighted), sum(plain) / len(plain)))
        # per-interpreter breakdown: the two named predictors are optimized and priced separately, and
        # a cheap model can match one while regressing the other, which a lumped macro would hide.
        for interpreter in ('interpret_columns', 'interpret_rows'):
            group: list[dict] = [r for r in results
                                 if 'metric' in r and r.get('interpreter') == interpreter]
            if group:
                w: list[float] = [r['metric']['weighted_f1'] for r in group]
                f: list[float] = [r['metric']['f1'] for r in group]
                errs: int = sum('error' in r for r in results if r.get('interpreter') == interpreter)
                print('  %-17s wF1=%.3f  F1=%.3f  over %d contest(s)%s'
                      % (interpreter, sum(w) / len(w), sum(f) / len(f), len(group),
                         '  (%d errored)' % errs if errs else ''))
    errors: int = sum('error' in r for r in results)
    if errors:
        print('%d contest(s) errored (see above).' % errors)


def report_dispatch(examples: list) -> None:
    '''Compare the oe2d.pages-driven dispatch (votes.detect_dispatch on a sample page) to each
    contest's gold orientation/read_strategy, so we can see whether the page image alone routes the
    read correctly before trusting it over the gold field. Prints per-contest detected-vs-gold and
    an aggregate; a mismatch names which axis (orientation / read_strategy) diverged.'''
    ok_orient: int = 0
    ok_read: int = 0
    for example in examples:
        gold_orient: str = example.orientation
        gold_read: str = example.read_strategy
        try:
            got: dict = votes.detect_dispatch(example.file_path, example.pages[0],
                                              example.pages, example.candidate_context)
        except Exception as error:
            print('%-40s ERROR %s: %s' % (getattr(example, '_id', '?'),
                                          type(error).__name__, str(error)[:60]))
            continue
        o_ok: bool = got['orientation'] == gold_orient
        r_ok: bool = got['read_strategy'] == gold_read
        ok_orient += o_ok
        ok_read += r_ok
        flags: str = ' '.join(t for t, good in
                              (('orient', o_ok), ('read', r_ok)) if not good)
        print('%-40s orient %s/%s  read %s/%s  scan=%-5s ruled=%-5s %s'
              % (getattr(example, '_id', '?'), got['orientation'], gold_orient,
                 got['read_strategy'], gold_read, got['scanned'], got['ruled_table'],
                 '' if not flags else 'MISS:' + flags))
    n: int = len(examples)
    if n:
        print('\norientation %d/%d = %.0f%%   read_strategy %d/%d = %.0f%%'
              % (ok_orient, n, 100 * ok_orient / n, ok_read, n, 100 * ok_read / n))


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Score the vote extractor on the gold set (per-contest + aggregate F1).')
    parser.add_argument('--student', default=None,
                        help='litellm model id to evaluate on the STOCK prompts, e.g. '
                             'bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0')
    parser.add_argument('--model', default=None,
                        help='Evaluate a saved program artifact instead (its baked-in LM governs)')
    parser.add_argument('--only', default=None,
                        help='Score only contests whose id contains this substring')
    parser.add_argument('--val-only', action='store_true',
                        help='Score only the held-out validation split (default: all contests)')
    parser.add_argument('--detect', action='store_true',
                        help='Instead of scoring extraction, measure the oe2d.pages-driven dispatch '
                             '(detect_dispatch) against each contest gold orientation/read_strategy')
    parser.add_argument('--detected', action='store_true',
                        help='Score extraction using DETECTED dispatch (withhold the gold '
                             'orientation/read_strategy so forward detects them, checksum-confirmed)')
    parser.add_argument('--temperature', type=float, default=0.0)
    parser.add_argument('--max-tokens', type=int, default=4096)
    parser.add_argument('--num-retries', type=int, default=10)
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.student and args.model:
        parser.error('pass --student OR --model, not both: a loaded artifact carries its own LM, '
                     'so --student cannot take effect (the artifact governs)')
    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)

    votes._instrument()          # cmpnd tracing when CMPND_API_KEY is set (tag oe2d-votes)

    examples: list = datasets.load_split()[1] if args.val_only else datasets.load_examples()
    if args.only:
        examples = [e for e in examples if args.only in getattr(e, '_id', '')]

    if args.detect:
        print('dispatch detection (oe2d.pages) vs gold over %d contest(s):\n' % len(examples))
        report_dispatch(examples)
        return

    program: votes.VoteExtractor = build_target(args.student, args.model, args.temperature,
                                                args.max_tokens, args.num_retries)
    label: str = args.model or args.student or 'shipped program'
    scope: str = 'validation' if args.val_only else 'all'
    dispatch: str = 'detected (image-driven)' if args.detected else 'gold'
    print('evaluating: %s  [dispatch: %s]' % (label, dispatch))
    print('over %d %s contest(s):\n' % (len(examples), scope))
    print_report(score_examples(program, examples, detected=args.detected))
    usage: dict = votes.textract_usage()
    if usage['calls']:
        print('\ntextract this run (paid calls only, cache hits free): %s ~$%.4f'
              % (usage['calls'], usage['usd']))


if __name__ == '__main__':
    main()
