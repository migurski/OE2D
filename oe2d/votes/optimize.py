'''GEPA-optimize the vote extractor against the precinct-row gold set.

Usage: oe2d-votes-optimize OUT [--max-metric-calls N] ...

Builds the same composite VoteExtractor the CLI runs and uses GEPA to evolve the signature-docstring
instructions of its two NAMED inner predictors -- interpret_columns (InterpretResultsPage) and
interpret_rows (InterpretPrecinctPage) -- against oe2d-data/votes. GEPA optimizes each independently
from the metric's FEEDBACK prose, which names the rows a run missed or invented with their vote
magnitudes (metrics.score_extraction). The deterministic read/stitch code in forward() is outside the
objective. The task LM is the Sonnet interpreter; the reflection LM is Bedrock Opus. The optimized
program is saved as JSON and validation F1 is printed.

Unlike oe2d.pages, the example inputs are small text (file path, pages, office, the candidate
context) -- the page grids are read INSIDE forward(), never stuffed into an Example -- so GEPA's
default text reflection is fine and no multimodal instruction proposer is needed.

GEPA checkpoints to a repo-root gepa-votes-<digest> dir, where the digest fingerprints the run config
(contests, split, models); re-running resumes a matching run. Touching a gepa.stop file there stops
gracefully.

Requires Bedrock credentials (Sonnet task LM + Opus reflection LM). Scanned contests read via
Textract during rollouts, so AWS creds must be set.
'''
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys

import dspy
from dspy import teleprompt

from .. import votes
from . import datasets, evaluate, metrics

# The reflection LM rewrites the prompts from the metric's feedback, where a strong model matters
# most; the task LM (the interpreter being optimized) defaults to the shipped Sonnet.
LM_CLAUDE_OPUS45: str = 'bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0'

# Repo root (oe2d/votes -> oe2d -> repo); each run gets a visible gepa-votes-<digest> checkpoint dir
# here, the digest fingerprinting the run config so resume only ever targets a matching run.
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_digest(examples: list, val_fraction: float, student: str) -> str:
    '''Fingerprint the run config so a changed setup forks a new checkpoint dir. Hashes the SORTED
    set of contest ids plus the split fraction and the two model ids, so the digest is independent of
    example order and stable for the same data.'''
    rows: list[str] = sorted(getattr(example, '_id', '') for example in examples)
    parts: list[str] = ['val=%s' % val_fraction, 'student=%s' % student,
                        'reflect=%s' % LM_CLAUDE_OPUS45] + rows
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:8]


def build_program() -> votes.VoteExtractor:
    '''Construct the composite vote-extraction program GEPA will optimize. GEPA evolves the prompts
    of the two named inner predictors (interpret_columns / interpret_rows); the read/stitch code in
    forward() is deterministic and outside the objective (the metric scores only the emitted rows).'''
    return votes.VoteExtractor()


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='GEPA-optimize the vote extractor.')
    parser.add_argument('out',
                        help='Where to save the optimized program JSON (the committed model lives at '
                             '%s)' % votes.OPTIMIZED_MODEL_PATH)
    parser.add_argument('--student', default=votes.LM_CLAUDE_SONNET45,
                        help='litellm model id for the interpreter LM being optimized '
                             '(default: the committed inference model)')
    parser.add_argument('--max-metric-calls', type=int, default=120, help='GEPA metric-call budget')
    parser.add_argument('--reflection-minibatch-size', type=int, default=4)
    parser.add_argument('--num-threads', type=int, default=4,
                        help='Parallel rollouts (each reads + interprets one contest); lower if throttled')
    parser.add_argument('--num-retries', type=int, default=10,
                        help='litellm retries per LM call (exponential backoff) for throttling')
    parser.add_argument('--log-dir', default=None,
                        help='GEPA checkpoint dir (default gepa-votes-<digest> at the repo root)')
    parser.add_argument('--val-fraction', type=float, default=0.3)
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    votes._instrument()

    print('Loading vote gold set (fetching sources)...', flush=True)
    trainset, valset = datasets.load_split(val_fraction=args.val_fraction)
    print('Loaded %d train + %d val contest(s).' % (len(trainset), len(valset)), flush=True)

    student_lm: dspy.LM = dspy.LM(model=args.student, temperature=1.0, max_tokens=4096,
                                  num_retries=args.num_retries)
    reflection_lm: dspy.LM = dspy.LM(model=LM_CLAUDE_OPUS45, temperature=1.0, max_tokens=8192,
                                     num_retries=args.num_retries)
    dspy.configure(lm=student_lm)
    program: votes.VoteExtractor = build_program()
    program.set_lm(student_lm)

    log_dir: str = args.log_dir or os.path.join(
        _REPO_ROOT, 'gepa-votes-%s' % run_digest(trainset + valset, args.val_fraction, args.student))
    os.makedirs(log_dir, exist_ok=True)
    resuming: bool = os.path.exists(os.path.join(log_dir, 'gepa_state.bin'))
    print('%s GEPA run in %s' % ('Resuming' if resuming else 'Starting', log_dir), flush=True)
    print('  (touch %s to stop gracefully)' % os.path.join(log_dir, 'gepa.stop'), flush=True)

    optimizer: teleprompt.GEPA = teleprompt.GEPA(
        metric=metrics.score_extraction,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=args.reflection_minibatch_size,
        num_threads=args.num_threads,
        reflection_lm=reflection_lm,
        log_dir=log_dir,
    )
    optimized: votes.VoteExtractor = optimizer.compile(program, trainset=trainset, valset=valset)

    out_dir: str = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    optimized.save(args.out)
    print('\nOptimized program saved to %s' % args.out)

    print('\nValidation results:')
    evaluate.print_report(evaluate.score_examples(optimized, valset))


if __name__ == '__main__':
    main()
