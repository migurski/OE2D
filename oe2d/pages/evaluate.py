'''Score a page-analysis program on the gold set: per-field accuracy and every mismatch.

No GEPA -- just run a program over the pages gold and report, so you can see whether there
is error signal worth optimizing (and which layouts a model gets wrong) before spending a
budget, or regression-check the shipped program. Three things to evaluate:

- --student MODEL : a candidate vision model on the STOCK prompt (baseline a model)
- --model PATH    : a saved program artifact (its own baked-in LM governs)
- neither         : the shipped program (build_analyzer -- committed artifact or stock LM)

score_fields here is shared with oe2d.pages.optimize's end-of-run scorecard, so the two
cannot drift. Credentials come from the environment; set CMPND_API_KEY to trace to cmpnd.

Usage:
    oe2d-pages-evaluate --student bedrock/us.meta.llama4-scout-17b-instruct-v1:0
    oe2d-pages-evaluate --model oe2d/pages/model/optimized_page_analyzer.json --val-only
'''
from __future__ import annotations

import argparse
import collections
import logging
import sys
import time

import dspy

from .. import pages
from . import datasets, metrics

logger: logging.Logger = logging.getLogger(__name__)


def _predict_all(program: dspy.Module, examples: list, num_threads: int) -> list:
    '''One prediction per example, in order. Parallel rollouts when num_threads > 1 (via
    dspy.Parallel, the executor GEPA uses); a failed rollout is FATAL -- max_errors=1 cancels
    and raises rather than returning None, so an outage can't be silently scored as misses.'''
    if num_threads <= 1:
        started: float = time.monotonic()
        last_log: float = started
        predictions: list = []
        for index, example in enumerate(examples, 1):
            predictions.append(program(image=example.image,
                                       electoral_context=getattr(example, 'electoral_context', '')))
            now: float = time.monotonic()
            if now - last_log >= 10:        # progress at most every ~10s, adapts to speed
                logger.info('  ...%d/%d (%.0fs elapsed)', index, len(examples), now - started)
                last_log = now
        return predictions
    pairs = [(program, dspy.Example(image=e.image, electoral_context=getattr(e, 'electoral_context', ''))
              .with_inputs('image', 'electoral_context')) for e in examples]
    runner = dspy.Parallel(num_threads=num_threads, max_errors=1,
                           provide_traceback=True, disable_progress_bar=True)
    return runner(pairs)


def score_fields(program: dspy.Module, examples: list, num_threads: int = 1) -> tuple[dict, dict, list]:
    '''Run program over examples; return per-field {correct}, {total}, and a list of
    (fixture, field, predicted, gold) misses. Skew is not scored (not a content field).
    Logs a run summary (and, sequentially, throttled per-page progress) under -v.'''
    correct: dict[str, int] = collections.defaultdict(int)
    total: dict[str, int] = collections.defaultdict(int)
    misses: list[tuple[str, str, object, object]] = []
    started: float = time.monotonic()
    logger.info('scoring %d pages (%d thread%s)...', len(examples), num_threads,
                '' if num_threads == 1 else 's')
    predictions = _predict_all(program, examples, num_threads)
    for example, prediction in zip(examples, predictions):
        for field in metrics.FIELD_WEIGHTS:
            total[field] += 1
            gold = getattr(example, field, None)
            pred = getattr(prediction, field, None)
            if pred == gold:
                correct[field] += 1
            else:
                misses.append((getattr(example, '_fixture', '?'), field, pred, gold))
    logger.info('scored %d pages in %.0fs', len(examples), time.monotonic() - started)
    return correct, total, misses


def print_scorecard(correct: dict, total: dict, misses: list, show_misses: bool = True) -> None:
    '''Print per-field accuracy and, optionally, each mismatch.'''
    for field in metrics.FIELD_WEIGHTS:
        pct: str = f'{correct[field] / total[field]:.0%}' if total[field] else 'n/a'
        print(f'  {field:24} {correct[field]}/{total[field]} = {pct}')
    if show_misses:
        print(f'\n{len(misses)} field misses:')
        for fixture, field, pred, gold in misses:
            name: str = fixture.split('/')[-1][:28]
            print(f'  {name:30} {field:22} pred={str(pred):16} gold={gold}')


def build_target(student: str | None, model: str | None,
                 temperature: float, max_tokens: int, num_retries: int) -> dspy.Module:
    '''The program to evaluate: a saved artifact (its own LM governs), a candidate model on
    the stock prompt, or the shipped program.'''
    if model:
        analyzer: pages.PageAnalyzer = pages.PageAnalyzer()
        analyzer.load(model)                 # artifact carries its own lm; it governs
        return analyzer
    if student:
        analyzer = pages.PageAnalyzer()
        analyzer.set_lm(dspy.LM(student, temperature=temperature, max_tokens=max_tokens,
                                num_retries=num_retries))
        return analyzer
    return pages.build_analyzer()            # shipped program (committed artifact or stock LM)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Score a page-analysis program on the gold set (per-field accuracy + misses).')
    parser.add_argument('--student', default=None,
                        help='litellm model id to evaluate on the STOCK prompt, e.g. '
                             'bedrock/us.meta.llama4-scout-17b-instruct-v1:0')
    parser.add_argument('--model', default=None,
                        help='Evaluate a saved program artifact instead (its baked-in LM governs)')
    parser.add_argument('--val-only', action='store_true',
                        help='Score only the held-out validation split (default: all real pages)')
    parser.add_argument('--num-threads', type=int, default=4,
                        help='Parallel rollouts (each a single LM call); lower if throttled')
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
        logging.getLogger('dspy').setLevel(logging.INFO)

    pages._instrument()          # cmpnd tracing when CMPND_API_KEY is set (tag oe2d-pages)
    program: dspy.Module = build_target(args.student, args.model, args.temperature,
                                        args.max_tokens, args.num_retries)

    examples: list = (datasets.load_split()[1] if args.val_only
                      else [e for e in datasets.load_examples() if not getattr(e, '_synthetic', False)])
    correct, total, misses = score_fields(program, examples, num_threads=args.num_threads)

    label: str = args.model or args.student or 'shipped program'
    scope: str = 'validation' if args.val_only else 'real'
    print(f'\nevaluated: {label}')
    print(f'over {len(examples)} {scope} pages (temperature {args.temperature}):')
    print_scorecard(correct, total, misses)


if __name__ == '__main__':
    main()
