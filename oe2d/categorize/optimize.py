'''GEPA-optimize the source categorizer against the gold set.

Usage: oe2d-optimize-categorizer [--out FILE] [--max-metric-calls N] ...

Builds the same dspy.RLM(SourceCategorizer, tools=...) the CLI runs, then uses
GEPA to evolve its prompt against labels/category.jsonl. The task LM is
OpenRouter Maverick (multimodal, so it also drives the vision inspector); the
reflection LM is Bedrock Opus. The optimized program is saved as JSON and
validation accuracy is printed per field.

GEPA checkpoints after each step to a repo-root gepa-<digest> dir, where the
digest fingerprints the run config (examples, split, models). Re-running resumes
a matching run; changing the config forks a fresh dir instead of resuming against
a mismatched checkpoint. Touching a gepa.stop file in the dir stops gracefully.

Requires an OpenRouter key (task LM) plus Bedrock credentials (Opus reflection LM),
Deno, and LibreOffice, since GEPA actually runs the RLM over the fixtures.
'''
from __future__ import annotations

import argparse
import collections
import hashlib
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

# Default output is the package-data model path the CLI auto-loads, so a finished
# run drops the optimized program right where oe2d-categorize-source picks it up.
_DEFAULT_OUT: str = categorize.OPTIMIZED_MODEL_PATH

# Repo root (oe2d/categorize -> oe2d -> repo); each GEPA run gets a visible
# checkpoint dir here named gepa-<digest>, where the digest fingerprints the
# run configuration so resume only ever targets a matching run.
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_digest(examples: list, val_fraction: float) -> str:
    '''Fingerprint the run config so a changed setup forks a new checkpoint dir.

    Covers everything a resume must match: the realized examples (basename plus
    recomputed inputs and gold labels), the split fraction, and both model IDs.
    Deliberately excludes run-budget knobs (max_metric_calls, minibatch) so those
    can be raised on resume. Basenames, not absolute paths, keep it machine-
    independent.
    '''
    parts: list[str] = [
        f'val={val_fraction}', f'student={STUDENT_MODEL}', f'reflect={REFLECTION_MODEL}',
    ]
    for example in sorted(examples, key=lambda ex: os.path.basename(ex.file_path)):
        fields: list[str] = [
            os.path.basename(example.file_path), example.container, str(example.page_count),
            example.orientation, example.grain,
        ]
        fields += [str(getattr(example, name)) for name in categorize.LAYOUT_PROPERTIES]
        parts.append('|'.join(fields))
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:8]


def build_program(verbose: bool = False) -> dspy.Module:
    '''Construct the RLM categorizer program GEPA will optimize.'''
    return dspy.RLM(
        categorize.SourceCategorizer,
        tools=[tools.count_pages, tools.page_table, tools.page_words,
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
    parser.add_argument('--num-threads', type=int, default=2,
                        help='Parallel RLM rollouts; each fires several Bedrock calls, so keep low to avoid throttling')
    parser.add_argument('--num-retries', type=int, default=10,
                        help='litellm retries per LM call (exponential backoff) for Bedrock throttling')
    parser.add_argument('--log-dir', default=None,
                        help='GEPA checkpoint dir (default gepa-<digest> at the repo root, '
                             'derived from the run config); re-running resumes a matching run')
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

    # num_retries lets litellm back off and retry on Bedrock throttling rather
    # than failing the rollout; combined with a low --num-threads it keeps the
    # run under the model's rate limits.
    student_lm: dspy.LM = dspy.LM(model=STUDENT_MODEL, temperature=1.0, max_tokens=4096,
                                  num_retries=args.num_retries)
    reflection_lm: dspy.LM = dspy.LM(model=REFLECTION_MODEL, temperature=1.0, max_tokens=8192,
                                     num_retries=args.num_retries)

    # The ambient LM drives both the RLM and, through dspy.settings, the vision
    # inspector; the program's own LM is set to the same student model.
    dspy.configure(lm=student_lm)
    program: dspy.Module = build_program(verbose=args.verbose)
    program.set_lm(student_lm)

    log_dir: str = args.log_dir or os.path.join(
        _REPO_ROOT, f'gepa-{run_digest(trainset + valset, args.val_fraction)}')
    os.makedirs(log_dir, exist_ok=True)
    resuming: bool = os.path.exists(os.path.join(log_dir, 'gepa_state.bin'))
    print(f'{"Resuming" if resuming else "Starting"} GEPA run in {log_dir}', flush=True)
    print(f'  (touch {os.path.join(log_dir, "gepa.stop")} to stop gracefully; '
          're-run the same command to resume)', flush=True)

    optimizer: GEPA = GEPA(
        metric=metrics.score_category,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=args.reflection_minibatch_size,
        num_threads=args.num_threads,
        reflection_lm=reflection_lm,
        log_dir=log_dir,
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
