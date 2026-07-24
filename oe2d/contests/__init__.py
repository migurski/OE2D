'''Locate target contests within a whole source file, returning unit ranges.

Given a source file (PDF pages or spreadsheet sheets) and target contests -- each
with flexible hint tokens (candidate names, running mates, parties) -- find the
contiguous page/sheet range(s) where each contest's results appear. It LOCATES;
reconstructing the vote tables is a separate, later step.

Cheap first: a deterministic scan reads each unit's text as cheaply as possible
(oe2d.pagetext -- free structured text, local tesseract OCR for scans) and fuzzy-
matches the target tokens, assembling contiguous runs with a max-gap bridge. A DSPy
program then maps each target to the run(s) whose on-page contest title and matched
candidates correspond -- the judgment calls, e.g. "Representative in Congress" ==
"U.S. House", "Electors of President" == "President", "U.S." vs "US".

Usage: oe2d-locate-contests file.pdf --target "President=Harris,Walz,Trump,Vance"
       oe2d-locate-contests --gold barry            # run a labeled fixture
'''
from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import sys

import dotenv
import dspy
import pydantic

from .. import categorize, pagetext, source_table

_FUZZY_THRESHOLD: float = 0.85
DEFAULT_MAX_GAP: int = 2

OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_contest_locator.json')


class Target(pydantic.BaseModel):
    '''A contest to find, plus free-form tokens that aid finding it.'''
    contest: str
    hints: list[str] = pydantic.Field(
        default_factory=list,
        description='Candidate names, running mates, parties -- any tokens marking the contest')


class ContestLocation(pydantic.BaseModel):
    '''Where a target contest was found: unit range(s) and the observed title.'''
    target: str
    ranges: list[tuple[int, int]]
    observed_title: str | None = None


class RunEvidence(pydantic.BaseModel):
    '''One deterministic candidate run handed to the LLM for target assignment.'''
    scan_guess: str = pydantic.Field(description='Target whose hints matched this run')
    unit_start: int
    unit_end: int
    observed_titles: list[str] = pydantic.Field(description='Contest headings seen on these units')
    matched_tokens: list[str] = pydantic.Field(description='Target hint tokens that matched')


class UnitHit(pydantic.BaseModel):
    '''A single unit where at least one target's tokens matched.'''
    unit: int
    matched: dict[str, list[str]]      # target.contest -> matched tokens
    title: str


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def token_hits(tokens: list[str], text: str) -> list[str]:
    '''Which of tokens appear in text, by substring or fuzzy single-word match.'''
    lowered: str = text.lower()
    vocab: set[str] = _words(text)
    found: list[str] = []
    for token in tokens:
        needle: str = token.lower()
        if needle in lowered:
            found.append(token)
            continue
        if any(difflib.SequenceMatcher(None, needle, word).ratio() >= _FUZZY_THRESHOLD
               for word in vocab):
            found.append(token)
    return found


def _title(text: str) -> str:
    '''Best-effort contest heading: first substantial non-boilerplate line.'''
    for line in text.splitlines():
        stripped: str = line.strip()
        if len(stripped) < 5:
            continue
        low: str = stripped.lower()
        if low.startswith('page') or low.startswith('sovc') or re.match(r'^[\d/:\s%.,\-]+$', stripped):
            continue
        return stripped[:120]
    return ''


def count_units(path: str) -> int:
    '''Number of pages (PDF) or sheets (spreadsheet); 1 if unknown.'''
    try:
        return source_table.page_count(path)
    except Exception:
        return 1


def scan_for_targets(path: str, targets: list[Target], unit_count: int | None = None,
                     max_gap: int = DEFAULT_MAX_GAP, page_budget: int | None = None) -> list[UnitHit]:
    '''Deterministic per-unit cheap-text scan -> hit list.

    Early-stops once every target has been seen and its run has closed (current unit
    more than max_gap past its last hit). An unseen target forces scanning to the end
    (or the page_budget cap) -- you cannot prove a contest absent without looking.
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    hits: list[UnitHit] = []
    last_hit: dict[str, int | None] = {target.contest: None for target in targets}
    for unit in range(1, limit + 1):
        text: str = pagetext.unit_text(path, unit)
        matched: dict[str, list[str]] = {}
        for target in targets:
            got: list[str] = token_hits(target.hints + [target.contest], text)
            if got:
                matched[target.contest] = got
                last_hit[target.contest] = unit
        if matched:
            hits.append(UnitHit(unit=unit, matched=matched, title=_title(text)))
        if all(seen is not None and unit - seen > max_gap for seen in last_hit.values()):
            break
    return hits


def assemble_ranges(units: list[int], max_gap: int = DEFAULT_MAX_GAP) -> list[tuple[int, int]]:
    '''Group unit numbers into contiguous ranges, bridging gaps of up to max_gap.'''
    ordered: list[int] = sorted(set(units))
    if not ordered:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for unit in ordered[1:]:
        if unit - prev <= max_gap + 1:
            prev = unit
        else:
            ranges.append((start, prev))
            start = prev = unit
    ranges.append((start, prev))
    return ranges


def build_evidence(hits: list[UnitHit], targets: list[Target],
                   max_gap: int = DEFAULT_MAX_GAP) -> list[RunEvidence]:
    '''Assemble per-target contiguous runs with observed titles and matched tokens.'''
    evidence: list[RunEvidence] = []
    for target in targets:
        matched_hits: list[UnitHit] = [h for h in hits if target.contest in h.matched]
        for start, end in assemble_ranges([h.unit for h in matched_hits], max_gap):
            run_hits: list[UnitHit] = [h for h in matched_hits if start <= h.unit <= end]
            titles: list[str] = list(dict.fromkeys(h.title for h in run_hits if h.title))
            tokens: list[str] = sorted({tok for h in run_hits for tok in h.matched[target.contest]})
            evidence.append(RunEvidence(scan_guess=target.contest, unit_start=start,
                                        unit_end=end, observed_titles=titles, matched_tokens=tokens))
    return evidence


class LocateContests(dspy.Signature):
    '''Assign each requested target contest to the unit range(s) where its results appear.

    You receive deterministic scan evidence: candidate runs (contiguous unit ranges) with the
    contest titles observed on those units and which target hint tokens matched. On-page contest
    titles often differ in wording from the requested target -- "Representative in Congress" is a
    "U.S. House" race, "Electors of President" is "President", "U.S." vs "US", "House" vs "Congress".
    Use judgment to map each target to the run(s) whose observed titles and matched candidates truly
    correspond. Keep the unit ranges from the evidence; do not invent unit numbers. Omit a target
    with no corresponding run.
    '''
    targets: list[Target] = dspy.InputField(desc='Contests to locate, with hint tokens')
    evidence: list[RunEvidence] = dspy.InputField(desc='Deterministic candidate runs from the scan')
    locations: list[ContestLocation] = dspy.OutputField(desc='Confirmed target -> unit ranges')


class ContestLocator(dspy.Module):
    '''Deterministic cheap-text scan, then LLM target assignment over the evidence.'''
    def __init__(self) -> None:
        super().__init__()
        self.locate: dspy.Module = dspy.Predict(LocateContests)

    def forward(self, file_path: str, targets: list[Target], unit_count: int | None = None,
                max_gap: int = DEFAULT_MAX_GAP, page_budget: int | None = None) -> dspy.Prediction:
        hits: list[UnitHit] = scan_for_targets(file_path, targets, unit_count, max_gap, page_budget)
        evidence: list[RunEvidence] = build_evidence(hits, targets, max_gap)
        return self.locate(targets=targets, evidence=evidence)


def _instrument() -> None:
    '''Turn on cmpnd tracing when a key is configured (tag oe2d-contests).'''
    dotenv.load_dotenv()
    key: str | None = os.environ.get('CMPND_API_KEY')
    if not key:
        return
    try:
        import cmpnd
        cmpnd.configure(api_key=key, endpoint=os.environ.get('CMPND_ENDPOINT'),
                        project_tags=['oe2d-contests'])
        cmpnd.auto_instrument()
    except Exception:
        pass


def build_locator() -> ContestLocator:
    '''Construct the locator, loading the optimized prompt if present.'''
    locator: ContestLocator = ContestLocator()
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        locator.load(OPTIMIZED_MODEL_PATH)
    return locator


def locate(file_path: str, targets: list[Target], max_gap: int = DEFAULT_MAX_GAP,
           page_budget: int | None = None) -> list[dict]:
    '''Locate targets in a file with the full program; return plain dicts.'''
    _instrument()
    dspy.configure(lm=dspy.LM(categorize.TASK_LM, temperature=0.0, max_tokens=4096))
    locator: ContestLocator = build_locator()
    prediction = locator(file_path=file_path, targets=targets, max_gap=max_gap,
                         page_budget=page_budget)
    return [location.model_dump() if isinstance(location, ContestLocation)
            else dict(location) for location in prediction.locations]


def parse_target(spec: str) -> Target:
    '''Parse a "Contest=tok1,tok2,..." CLI target spec.'''
    contest, _, rest = spec.partition('=')
    hints: list[str] = [h.strip() for h in rest.split(',') if h.strip()]
    return Target(contest=contest.strip(), hints=hints)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Locate target contests in a source file, returning unit ranges.')
    parser.add_argument('path', nargs='?', help='Source file (PDF/spreadsheet)')
    parser.add_argument('--target', action='append', default=[],
                        help='Repeatable "Contest=candidate,candidate,..." spec')
    parser.add_argument('--gold', help='Run a labeled fixture by name substring (uses its gold targets)')
    parser.add_argument('--max-gap', type=int, default=DEFAULT_MAX_GAP)
    parser.add_argument('--budget', type=int, default=None, help='Cap units scanned')
    parser.add_argument('--scan-only', action='store_true',
                        help='Deterministic scan + runs only; no LLM, no API')
    parser.add_argument('-v', '--verbose', action='store_true')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('dspy').setLevel(logging.INFO)

    from . import datasets
    if args.gold:
        path, targets, gold = datasets.gold_request(args.gold)
        print(f'# {os.path.basename(path)}  (gold range {gold})', file=sys.stderr)
    else:
        if not args.path or not args.target:
            parser.error('give a path and at least one --target, or use --gold')
        path, targets = args.path, [parse_target(spec) for spec in args.target]

    if args.scan_only:
        hits = scan_for_targets(path, targets, max_gap=args.max_gap, page_budget=args.budget)
        evidence = build_evidence(hits, targets, args.max_gap)
        print(json.dumps({'units_hit': [h.unit for h in hits],
                          'runs': [e.model_dump() for e in evidence]}, indent=2))
        return

    print(json.dumps(locate(path, targets, args.max_gap, args.budget), indent=2))


if __name__ == '__main__':
    main()
