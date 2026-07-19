'''GEPA-optimize the source categorizer against the gold set.

Usage: oe2d-optimize-categorizer [--out FILE] [--max-metric-calls N] ...

Builds the same dspy.RLM(SourceCategorizer, tools=...) the CLI runs, then uses
GEPA to evolve its prompt against labels/category.jsonl. The task LM is Bedrock
Maverick (multimodal, so it also drives the vision inspector); the reflection LM
is Opus. The optimized program is saved as JSON and validation accuracy is
printed per field.

Requires Bedrock credentials, Deno, and LibreOffice — the same runtime pieces
the categorizer itself needs, since GEPA actually runs the RLM over the fixtures.
'''
from __future__ import annotations

import argparse
import collections
import logging
import os
import sys

import dspy
from dspy.teleprompt import GEPA

from .. import categorize
from . import datasets, metrics, tools

# Task LM writes the RLM code and reads the page images; reflection LM rewrites
# the prompt from the metric's feedback. A strong reflection model matters more
# than a strong task model here.
STUDENT_MODEL: str = categorize.MAVERICK_LM
REFLECTION_MODEL: str = 'bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0'

_DEFAULT_OUT: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'labels', 'optimized_categorizer.json')


def build_program(verbose: bool = False) -> dspy.Module:
    '''Construct the RLM categorizer program GEPA will optimize.'''
    return dspy.RLM(
        categorize.SourceCategorizer,
        tools=[tools.page_count, tools.page_table, tools.page_words,
               tools.zip_members, tools.inspect_page],
        verbose=verbose,
    )


def field_accuracy(program: dspy.Module, valset: list) -> dict[str, tuple[int, int]]:
    '''Run the program over valset, returning (correct, scored) per field.'''
    correct: dict[str, int] = collections.defaultdict(int)
    scored: dict[str, int] = collections.defaultdict(int)
    for example in valset:
        prediction = program(**{name: example.get(name) for name in datasets.INPUT_FIELDS})
        for name in datasets.OUTPUT_FIELDS:
            if name == 'grain' and example.grain == 'unknown':
                continue
            scored[name] += 1
            if getattr(prediction, name, None) == getattr(example, name, None):
                correct[name] += 1
    return {name: (correct[name], scored[name]) for name in datasets.OUTPUT_FIELDS}


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='GEPA-optimize the source categorizer.',
    )
    parser.add_argument('--out', default=_DEFAULT_OUT, help='Where to save the optimized program JSON')
    parser.add_argument('--max-metric-calls', type=int, default=180, help='GEPA metric-call budget')
    parser.add_argument('--reflection-minibatch-size', type=int, default=7)
    parser.add_argument('--num-threads', type=int, default=8)
    parser.add_argument('--val-fraction', type=float, default=0.3)
    parser.add_argument('-v', '--verbose', action='store_true', help='stream RLM REPL steps')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        # Mirror categorize.main: without a handler at INFO the RLM's verbose
        # REPL steps are emitted to a logger nobody is listening to.
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    categorize._instrument()

    print('Loading gold set (recomputing container/page_count per fixture)...', flush=True)
    trainset, valset = datasets.load_split(val_fraction=args.val_fraction)
    print(f'Loaded {len(trainset) + len(valset)} examples: {len(trainset)} train, {len(valset)} val.', flush=True)

    student_lm: dspy.LM = dspy.LM(model=STUDENT_MODEL, temperature=1.0, max_tokens=4096)
    reflection_lm: dspy.LM = dspy.LM(model=REFLECTION_MODEL, temperature=1.0, max_tokens=8192)

    # The ambient LM drives both the RLM and, through dspy.settings, the vision
    # inspector; the program's own LM is set to the same student model.
    dspy.configure(lm=student_lm)
    program: dspy.Module = build_program(verbose=args.verbose)
    program.set_lm(student_lm)

    optimizer: GEPA = GEPA(
        metric=metrics.score_category,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=args.reflection_minibatch_size,
        num_threads=args.num_threads,
        reflection_lm=reflection_lm,
    )
    optimized: dspy.Module = optimizer.compile(program, trainset=trainset, valset=valset)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    optimized.save(args.out)
    print(f'\nOptimized program saved to {args.out}')

    print('\nValidation accuracy per field:')
    for name, (hit, total) in field_accuracy(optimized, valset).items():
        pct: str = f'{hit / total:.0%}' if total else 'n/a'
        print(f'  {name:22} {hit}/{total} = {pct}')


if __name__ == '__main__':
    main()
