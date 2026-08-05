'''GEPA-optimize the contest locator against the full-documents gold.

Evolves the signature-docstring instructions of the locator's two named predictors --
ClassifyContestTitles (the document-wide Predict) and MatchContestTitles (the per-target ReAct
agent) -- from the metric's FEEDBACK prose (metrics.score_location names, per target, the missed /
extra pages and whether the matched title was the gold wording). The deterministic OCR/detect and the
page-locating are outside the objective. The task LM is the --student interpreter being optimized; the
reflection LM is Bedrock Opus. The optimized program is saved as JSON (re-bound to temperature 0 for
deterministic inference) and validation score is printed.

COST NOTE: every metric call runs the locator on a whole document -- OCR + classify + a multi-step
ReAct match per target -- so a rollout is far pricier than a single-call extractor. Keep
--max-metric-calls modest, --num-threads low, and --max-train small (a curated handful of documents
spanning the organization types is enough to teach the behaviour; validate on the full set afterwards
with oe2d-contests-evaluate).

    oe2d-contests-optimize out.json --student bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0 \\
        --max-tokens 16384 --max-train 10 --max-metric-calls 60
'''
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys

import dspy
from dspy import teleprompt

from .. import contests
from . import datasets, metrics

# Reflection rewrites the prompts from the metric feedback -- a strong model matters most here; the
# task LM (the predictor being optimized) defaults to the shipped Kimi.
LM_CLAUDE_OPUS45: str = 'bedrock/us.anthropic.claude-opus-4-5-20251101-v1:0'
_REPO_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_digest(examples: list, val_fraction: float, student: str) -> str:
    '''Fingerprint the run config so a changed setup forks a new checkpoint dir.'''
    ids: list[str] = sorted(getattr(example, '_id', '') for example in examples)
    parts: list[str] = ['val=%s' % val_fraction, 'student=%s' % student,
                        'reflect=%s' % LM_CLAUDE_OPUS45] + ids
    return hashlib.sha256('\n'.join(parts).encode()).hexdigest()[:8]


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='GEPA-optimize the contest locator.')
    parser.add_argument('out', help='Where to save the optimized program JSON (the committed model '
                                    'lives at %s)' % contests.OPTIMIZED_MODEL_PATH)
    parser.add_argument('--student', default=contests.LM_CLAUDE_HAIKU45,
                        help='litellm model id for the interpreter LM being optimized '
                             '(default: the shipped inference model)')
    parser.add_argument('--max-tokens', type=int, default=8192,
                        help='LM max_tokens (raise to ~16384 for a model whose ReAct traces truncate)')
    parser.add_argument('--max-metric-calls', type=int, default=60,
                        help='GEPA metric-call budget (each call is a full document ReAct run -- keep modest)')
    parser.add_argument('--reflection-minibatch-size', type=int, default=3)
    parser.add_argument('--num-threads', type=int, default=2,
                        help='Parallel rollouts (each OCRs + ReAct-searches one document); low avoids throttling')
    parser.add_argument('--num-retries', type=int, default=10)
    parser.add_argument('--val-fraction', type=float, default=0.3)
    parser.add_argument('--max-train', type=int, default=None,
                        help='Cap the trainset to this many documents (ReAct cost control)')
    parser.add_argument('--log-dir', default=None)
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)

    contests._instrument()

    print('Loading contest gold (fetching source documents)...', flush=True)
    trainset, valset = datasets.split(datasets.load_examples(), val_fraction=args.val_fraction)
    if args.max_train is not None:
        trainset = trainset[:args.max_train]
    print('Loaded %d train + %d val document(s).' % (len(trainset), len(valset)), flush=True)

    student_lm: dspy.LM = dspy.LM(model=args.student, temperature=1.0, max_tokens=args.max_tokens,
                                  num_retries=args.num_retries)
    reflection_lm: dspy.LM = dspy.LM(model=LM_CLAUDE_OPUS45, temperature=1.0, max_tokens=8192,
                                     num_retries=args.num_retries)
    dspy.configure(lm=student_lm)
    program: contests.ContestLocator = contests.ContestLocator()
    program.set_lm(student_lm)

    log_dir: str = args.log_dir or os.path.join(
        _REPO_ROOT, 'gepa-contests-%s' % run_digest(trainset + valset, args.val_fraction, args.student))
    os.makedirs(log_dir, exist_ok=True)
    resuming: bool = os.path.exists(os.path.join(log_dir, 'gepa_state.bin'))
    print('%s GEPA run in %s' % ('Resuming' if resuming else 'Starting', log_dir), flush=True)
    print('  (touch %s to stop gracefully)' % os.path.join(log_dir, 'gepa.stop'), flush=True)

    optimizer: teleprompt.GEPA = teleprompt.GEPA(
        metric=metrics.score_location,
        max_metric_calls=args.max_metric_calls,
        reflection_minibatch_size=args.reflection_minibatch_size,
        num_threads=args.num_threads,
        reflection_lm=reflection_lm,
        log_dir=log_dir,
    )
    optimized: contests.ContestLocator = optimizer.compile(program, trainset=trainset, valset=valset)

    # Re-bind inference to temperature 0 BEFORE saving: the student ran at temp 1.0 for GEPA
    # exploration, and that setting would otherwise be baked into the shipped artifact.
    optimized.set_lm(dspy.LM(model=args.student, temperature=0.0, max_tokens=args.max_tokens))
    out_dir: str = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    optimized.save(args.out)
    print('\nOptimized program saved to %s' % args.out)
    print('Now score it on the full set:  oe2d-contests-evaluate --model %s' % args.out)


if __name__ == '__main__':
    main()
