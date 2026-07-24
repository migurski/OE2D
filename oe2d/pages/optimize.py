'''GEPA-optimize the page analyzer against the per-page gold set.

Usage: oe2d-optimize-pages [--out FILE] [--max-metric-calls N] ...

Builds the same dspy.Predict(PageAnalysis) the CLI runs and uses GEPA to evolve
its prompt against oe2d-data/pages/labels.jsonl. The task LM is the shared
Fireworks Kimi K2 (multimodal) vision model; the reflection LM is Bedrock Opus.
The optimized program is saved as JSON and validation accuracy is printed per
field.

GEPA checkpoints to a repo-root gepa-<digest> dir, where the digest fingerprints
the run config (examples, split, models); re-running resumes a matching run.
Touching a gepa.stop file in that dir stops gracefully.

Requires a Fireworks key (task LM) plus Bedrock credentials (Opus reflection LM).
Source rendering is not needed at optimize time — the training images are
committed PNGs.
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
from dspy.teleprompt.gepa import instruction_proposal

from .. import categorize
from .. import pages
from . import OUTPUT_FIELDS, PageAnalysis, datasets, metrics

# The task LM reads the page image; the reflection LM rewrites the prompt from the
# metric's feedback. A strong reflection model matters most here.
STUDENT_MODEL: str = categorize.TASK_LM
REFLECTION_MODEL: str = 'bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0'

# Repo root (oe2d/pages -> oe2d -> repo); each run gets a visible gepa-<digest>
# checkpoint dir here, the digest fingerprinting the run config so resume only
# ever targets a matching run.
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_digest(examples: list, val_fraction: float) -> str:
    '''Fingerprint the run config so a changed setup forks a new checkpoint dir.

    Builds a per-example string (fixture, eval_kind, and every output field) and
    hashes the SORTED set of them, so the digest is independent of example order
    (and of object identity) — the same data always yields the same checkpoint dir.
    '''
    rows: list[str] = []
    for example in examples:
        fields: list[str] = [getattr(example, '_fixture', ''),
                             getattr(example, 'eval_kind', 'content')]
        fields += [f'{name}={getattr(example, name, None)!r}' for name in OUTPUT_FIELDS]
        rows.append('|'.join(fields))
    parts: list[str] = [
        f'val={val_fraction}', f'student={STUDENT_MODEL}', f'reflect={REFLECTION_MODEL}',
    ] + sorted(rows)
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:8]


def build_program() -> dspy.Module:
    '''Construct the page-analysis program GEPA will optimize.'''
    return dspy.Predict(PageAnalysis)


def content_accuracy(program: dspy.Module, examples: list) -> dict[str, tuple[int, int]]:
    '''Per content-field (correct, scored) over the content examples in a set.

    A rollout that raises is counted as a miss on every field, mirroring how GEPA
    scores a failed rollout, rather than aborting the whole eval.
    '''
    content: list = [ex for ex in examples if getattr(ex, 'eval_kind', 'content') != 'skew']
    correct: dict[str, int] = collections.defaultdict(int)
    scored: dict[str, int] = collections.defaultdict(int)
    failures: int = 0
    for example in content:
        try:
            prediction = program(image=example.image)
        except Exception as error:
            failures += 1
            print(f'  content rollout failed ({type(error).__name__}); counting as a miss',
                  file=sys.stderr)
            prediction = None
        for name in metrics.CONTENT_WEIGHTS:
            scored[name] += 1
            pred = getattr(prediction, name, None) if prediction is not None else None
            if pred == getattr(example, name, None):
                correct[name] += 1
    if failures:
        print(f'  ({failures}/{len(content)} content rollouts errored and scored 0)', file=sys.stderr)
    return {name: (correct[name], scored[name]) for name in metrics.CONTENT_WEIGHTS}


def skew_report(program: dspy.Module, examples: list) -> tuple[int, int, float, int]:
    '''Over the skew examples (rotated pages), return (scored, within_tol, mae, failures).'''
    skew: list = [ex for ex in examples if getattr(ex, 'eval_kind', None) == 'skew']
    scored: int = 0
    within: int = 0
    abs_error: float = 0.0
    failures: int = 0
    for example in skew:
        try:
            prediction = program(image=example.image)
            pred_value = float(prediction.skew_degrees)
        except Exception:
            failures += 1
            continue
        gold_value = float(example.skew_degrees)
        scored += 1
        abs_error += abs(pred_value - gold_value)
        if metrics._skew_ok(pred_value, gold_value):
            within += 1
    mae: float = abs_error / scored if scored else 0.0
    return scored, within, mae, failures


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='GEPA-optimize the page analyzer.',
    )
    parser.add_argument('--out', default=pages.OPTIMIZED_MODEL_PATH,
                        help='Where to save the optimized program JSON')
    parser.add_argument('--max-metric-calls', type=int, default=180, help='GEPA metric-call budget')
    parser.add_argument('--reflection-minibatch-size', type=int, default=7)
    parser.add_argument('--num-threads', type=int, default=4,
                        help='Parallel vision rollouts; each is a single LM call, so this can be '
                             'higher than the categorizer (which fanned out tool calls)')
    parser.add_argument('--num-retries', type=int, default=10,
                        help='litellm retries per LM call (exponential backoff) for throttling')
    parser.add_argument('--log-dir', default=None,
                        help='GEPA checkpoint dir (default gepa-<digest> at the repo root)')
    parser.add_argument('--max-examples', type=int, default=None,
                        help='Cap the REAL pages to a stratified subsample for a quick pass '
                             '(spread across fixtures; shrinks the baseline eval so reflections '
                             'start sooner). Synthetics of dropped fixtures are dropped too')
    parser.add_argument('--val-fraction', type=float, default=0.25)
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    pages._instrument()

    print('Loading per-page gold set...', flush=True)
    examples: list = datasets.load_examples()
    real: list = [ex for ex in examples if not getattr(ex, '_synthetic', False)]
    synthetic: list = [ex for ex in examples if getattr(ex, '_synthetic', False)]
    if args.max_examples:
        real = datasets.subsample(real, args.max_examples)
        print(f'Subsampled to {len(real)} real pages for a quick pass.', flush=True)
    trainset, valset = datasets.split(real + synthetic, val_fraction=args.val_fraction)
    val_content: int = sum(getattr(ex, 'eval_kind', 'content') != 'skew' for ex in valset)
    val_skew: int = sum(getattr(ex, 'eval_kind', None) == 'skew' for ex in valset)
    print(f'Loaded {len(trainset)} train + {len(valset)} val '
          f'({val_content} content, {val_skew} skew-holdout).', flush=True)

    student_lm: dspy.LM = dspy.LM(model=STUDENT_MODEL, temperature=1.0, max_tokens=4096,
                                  num_retries=args.num_retries)
    reflection_lm: dspy.LM = dspy.LM(model=REFLECTION_MODEL, temperature=1.0, max_tokens=8192,
                                     num_retries=args.num_retries)
    dspy.configure(lm=student_lm)
    program: dspy.Module = build_program()
    program.set_lm(student_lm)

    log_dir: str = args.log_dir or os.path.join(
        _REPO_ROOT, f'gepa-pages-{run_digest(trainset + valset, args.val_fraction)}')
    os.makedirs(log_dir, exist_ok=True)
    resuming: bool = os.path.exists(os.path.join(log_dir, 'gepa_state.bin'))
    print(f'{"Resuming" if resuming else "Starting"} GEPA run in {log_dir}', flush=True)
    print(f'  (touch {os.path.join(log_dir, "gepa.stop")} to stop gracefully)', flush=True)

    # The example input is a page IMAGE. Without a multimodal instruction
    # proposer, GEPA stringifies inputs into the reflection prompt, and str() of a
    # dspy.Image is its base64 — which blows past the reflection LM's context
    # window. MultiModalInstructionProposer keeps the image an object and sends it
    # to the (multimodal) Opus reflection LM as a real image block, so the prompt
    # stays small and the reflection can actually see the page.
    optimizer: GEPA = GEPA(
        metric=metrics.score_page,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=args.reflection_minibatch_size,
        num_threads=args.num_threads,
        reflection_lm=reflection_lm,
        instruction_proposer=instruction_proposal.MultiModalInstructionProposer(),
        log_dir=log_dir,
    )
    optimized: dspy.Module = optimizer.compile(program, trainset=trainset, valset=valset)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    optimized.save(args.out)
    print(f'\nOptimized program saved to {args.out}')

    print('\nContent accuracy per field (real val pages):')
    for name, (hit, total) in content_accuracy(optimized, valset).items():
        pct: str = f'{hit / total:.0%}' if total else 'n/a'
        print(f'  {name:24} {hit}/{total} = {pct}')

    scored, within, mae, failures = skew_report(optimized, valset)
    print('\nSkew (rotated val-fixture holdout):')
    if scored:
        print(f'  within {metrics.SKEW_TOLERANCE_DEGREES} deg: {within}/{scored} = '
              f'{within / scored:.0%}; mean abs error {mae:.2f} deg'
              + (f'; {failures} failed' if failures else ''))
    else:
        print('  no skew holdout in this split (val fixtures have no rotations)')


if __name__ == '__main__':
    main()
