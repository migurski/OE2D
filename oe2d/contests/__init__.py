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

logger: logging.Logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD: float = 0.85
DEFAULT_MAX_GAP: int = 2

OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_contest_locator.json')


class Target(pydantic.BaseModel):
    '''A contest to find, plus knowledge that aids finding and interpreting it.'''
    contest: str
    context: str = pydantic.Field(
        default='',
        description='Free-form knowledge about the race and its candidates, e.g. '
                    '"presidential race between Trump and Harris, third-party Stein and Oliver"')
    hints: list[str] = pydantic.Field(
        default_factory=list,
        description='Candidate names, running mates, parties -- tokens for the cheap name scan')


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


# Party abbreviations/labels appear on every results page, so they are weak
# evidence -- they must not, on their own, qualify a unit for a contest.
_PARTY_TOKENS: frozenset[str] = frozenset({
    'dem', 'rep', 'lib', 'grn', 'ust', 'nlp', 'ind', 'wf', 'con',
    'democratic', 'republican', 'libertarian', 'green', 'constitution',
})


def _is_party(token: str) -> bool:
    return token.strip().lower() in _PARTY_TOKENS


def _unit_qualifies(target: Target, matched: list[str]) -> bool:
    '''Whether a unit's matches are strong enough to count for a target.

    Require TWO distinctive (non-party) matches, so a lone common surname (a "Stein"
    from another race) or a party label alone cannot pull a contest's range along.
    A target with fewer than two distinctive hints is underspecified (e.g. the party-
    only placeholder rows); for those we stay loose and accept a single match rather
    than make them impossible to find.
    '''
    distinctive_hints: list[str] = [h for h in target.hints if not _is_party(h)]
    if len(distinctive_hints) < 2:
        return len(matched) >= 1
    return len([t for t in matched if not _is_party(t)]) >= 2


def count_units(path: str) -> int:
    '''Number of pages (PDF) or sheets (spreadsheet); 1 if unknown.'''
    try:
        return source_table.page_count(path)
    except Exception:
        return 1


# A contest heading carries a "vote for [not more than] N" marker in every vendor seen
# (Dominion, ClearBallot, Electionware, PA primary). Deriving the gold by hand showed the
# TITLE is the durable locate signal -- candidate names are routinely rotated, char-spaced,
# or column-split and fail to match -- so the title index is the backbone of the span.
_CONTEST_MARKER: re.Pattern = re.compile(r'vote for', re.I)
_TITLE_STOP: frozenset[str] = frozenset({
    'of', 'the', 'for', 'in', 'and', 'a', 'to', 'at', 'us', 'u', 's', 'united', 'states',
    'vote', 'not', 'more', 'than', 'district', 'member',
})


def _significant_words(label: str) -> set[str]:
    '''Distinctive lowercase words of a contest label (drop stopwords and parties).'''
    return {w for w in re.findall(r'[a-z]+', label.lower())
            if w not in _TITLE_STOP and not _is_party(w)}


def contest_title_index(path: str, unit_count: int | None = None,
                        page_budget: int | None = None) -> dict[int, str]:
    '''Map each unit bearing contest-title line(s) to ALL its titles (cheap text / OCR).

    Reads every unit (no early stop) because a contest can recur anywhere in a by-precinct
    document. Captures every "vote for" title on a page, since summary/precinct layouts
    stack several contests per page. Returns {} when the text carries no titles.
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    index: dict[int, list[str]] = {}
    for unit, text in enumerate(pagetext.layout_texts(path, limit), 1):
        titles: list[str] = _title_lines(text)
        if titles:
            index[unit] = titles
    return index


def _is_banner(line: str) -> bool:
    '''A page banner / boilerplate line (page number, timestamp), not a contest name.'''
    low: str = line.strip().lower()
    return (not low or low.startswith('page') or low.startswith('sovc')
            or bool(re.match(r'^[\d/:\s%.,\-]+$', line.strip())))


def _title_lines(text: str) -> list[str]:
    '''Contest-title lines on a page: each "vote for" marker line joined with the line
    above it, since some vendors (Electionware "PRESIDENTIAL ELECTORS / Vote For 1") put
    the contest name on the preceding line. A page banner above the marker is dropped.'''
    lines: list[str] = text.splitlines()
    titles: list[str] = []
    for i, line in enumerate(lines):
        if _CONTEST_MARKER.search(line):
            above: str = lines[i - 1].strip() if i > 0 else ''
            if _is_banner(above):
                above = ''
            titles.append((above + ' ' + line.strip()).strip()[:160])
    return titles


def _word_similar(want: str, have: str) -> bool:
    '''Loose word match tolerant of vendor wording: exact, containment, or a shared
    5+ char prefix -- so president~presidential and senate~senator match, house!~congress.'''
    if want == have or want in have or have in want:
        return True
    return len(want) >= 5 and len(have) >= 5 and want[:5] == have[:5]


def _title_matches(target: Target, title: str) -> bool:
    '''Whether a title names the target contest: every significant target word has a
    similar word in the title.'''
    want: set[str] = _significant_words(target.contest)
    have: list[str] = re.findall(r'[a-z]+', title.lower())
    return bool(want) and all(any(_word_similar(w, h) for h in have) for w in want)


def title_segments(index: dict[int, list[str]], target: Target,
                   unit_count: int) -> list[tuple[int, int]]:
    '''Spans for a target from the title index: each matching title to the next title - 1.

    A unit matches if ANY of its titles names the target. Works for by_contest (one span)
    and by_precinct (a span per recurrence). Empty when no title names the target (garbled
    wording, e.g. "U.S. House" vs "Representative in Congress" -- names/LLM cover that) or
    no titles were found at all.
    '''
    starts: list[int] = sorted(index)
    spans: list[tuple[int, int]] = []
    for pos, unit in enumerate(starts):
        if any(_title_matches(target, title) for title in index[unit]):
            end: int = (starts[pos + 1] - 1) if pos + 1 < len(starts) else unit_count
            spans.append((unit, end))
    return spans


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
            if got and _unit_qualifies(target, got):
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


def build_evidence(hits: list[UnitHit], targets: list[Target], max_gap: int = DEFAULT_MAX_GAP,
                   trailing_pad: int | None = None, unit_count: int | None = None) -> list[RunEvidence]:
    '''Assemble per-target contiguous runs with observed titles and matched tokens.

    Each run's END is padded by trailing_pad (default max_gap) so a trailing write-in
    or continuation page that carries votes but no candidate names -- and so never
    matched -- is still included. The pad is clamped to unit_count when known. Only
    the real hit units contribute the titles/tokens evidence.
    '''
    pad: int = max_gap if trailing_pad is None else trailing_pad
    evidence: list[RunEvidence] = []
    for target in targets:
        matched_hits: list[UnitHit] = [h for h in hits if target.contest in h.matched]
        for start, end in assemble_ranges([h.unit for h in matched_hits], max_gap):
            run_hits: list[UnitHit] = [h for h in matched_hits if start <= h.unit <= end]
            titles: list[str] = list(dict.fromkeys(h.title for h in run_hits if h.title))
            tokens: list[str] = sorted({tok for h in run_hits for tok in h.matched[target.contest]})
            padded_end: int = end + pad
            if unit_count is not None:
                padded_end = min(padded_end, unit_count)
            evidence.append(RunEvidence(scan_guess=target.contest, unit_start=start,
                                        unit_end=padded_end, observed_titles=titles, matched_tokens=tokens))
    return evidence


class TitleEvidence(pydantic.BaseModel):
    '''One distinct contest title observed in a document, with supporting signals.'''
    title: str = pydantic.Field(desc='The observed contest-title text, verbatim')
    units: list[int] = pydantic.Field(desc='Units where this title appears')
    header_tokens: list[str] = pydantic.Field(
        default_factory=list, desc='Candidate/party tokens seen on those units')


class MatchContestTitles(dspy.Signature):
    '''Decide which of a document's observed contest titles are the requested target contest.

    Detection of the titles is already done deterministically; your job is the interpretation
    the strings cannot do. Titles vary widely by jurisdiction and vendor -- "U.S. House" may
    appear as "Representative in Congress", "House of Representatives", or "Congressional
    District N"; "State House" as "Representative in State Legislature" or "State Assembly";
    "President" as "Electors of President and Vice-President" or "Presidential Electors".
    Use the free-form context (the race, its candidates) and the observed header tokens to
    confirm a match, and to disambiguate near-duplicates -- different districts, and full-term
    vs partial-term seats -- choosing the one the target refers to. Return the matching titles
    exactly as given in the observed list; return none if no observed title is the target.
    '''
    contest: str = dspy.InputField(desc='The target contest label to find')
    context: str = dspy.InputField(desc='Free-form knowledge about the race and its candidates')
    observed: list[TitleEvidence] = dspy.InputField(
        desc="The document's distinct contest titles with supporting header tokens")
    matching_titles: list[str] = dspy.OutputField(
        desc='The observed titles (verbatim) that are the target contest')


def contest_evidence(path: str, target: Target, unit_count: int | None = None,
                     page_budget: int | None = None) -> tuple[dict[int, list[str]],
                                                              list[TitleEvidence], int]:
    '''Read the document once: title index + distinct-title evidence for the target.

    Deterministic detection only -- returns every distinct contest title, the units it
    appears on, and which of the target's candidate/party hint tokens were seen there.
    Interpretation (which titles ARE the target) is left to MatchContestTitles.
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    index: dict[int, list[str]] = {}
    by_title: dict[str, dict] = {}
    for unit, text in enumerate(pagetext.layout_texts(path, limit), 1):
        titles: list[str] = _title_lines(text)
        if not titles:
            continue
        index[unit] = titles
        found: list[str] = token_hits(target.hints, text) if target.hints else []
        for title in titles:
            slot = by_title.setdefault(title, {'units': [], 'toks': set()})
            slot['units'].append(unit)
            slot['toks'].update(found)
    evidence: list[TitleEvidence] = [
        TitleEvidence(title=title, units=sorted(slot['units']), header_tokens=sorted(slot['toks']))
        for title, slot in by_title.items()]
    return index, evidence, unit_count


def segments_for_titles(index: dict[int, list[str]], matched_titles: list[str],
                        unit_count: int) -> list[tuple[int, int]]:
    '''Title-to-next-title spans for a chosen set of observed title strings.'''
    wanted: set[str] = {title.strip() for title in matched_titles}
    starts: list[int] = sorted(index)
    spans: list[tuple[int, int]] = []
    for pos, unit in enumerate(starts):
        if any(title.strip() in wanted for title in index[unit]):
            end: int = (starts[pos + 1] - 1) if pos + 1 < len(starts) else unit_count
            spans.append((unit, end))
    return spans


class ContestLocator(dspy.Module):
    '''Deterministic title detection, LLM title interpretation, deterministic segmentation.

    detect (contest_evidence) -> interpret (MatchContestTitles: which titles are the target,
    using free-form context) -> segment (segments_for_titles: each matched title to the next).
    The deterministic prefix match (_title_matches) is the fallback when the LLM is
    unavailable or returns nothing, so the program still runs offline.
    '''
    def __init__(self) -> None:
        super().__init__()
        self.match: dspy.Module = dspy.Predict(MatchContestTitles)

    def _interpret(self, target: Target, evidence: list[TitleEvidence]) -> list[str]:
        try:
            prediction = self.match(contest=target.contest, context=target.context,
                                    observed=evidence)
            matched: list[str] = [t for t in prediction.matching_titles
                                  if any(t.strip() == e.title.strip() for e in evidence)]
            if matched:
                return matched
        except Exception:
            pass
        return [e.title for e in evidence if _title_matches(target, e.title)]

    def forward(self, file_path: str, targets: list[Target], unit_count: int | None = None,
                max_gap: int = DEFAULT_MAX_GAP, page_budget: int | None = None) -> dspy.Prediction:
        locations: list[ContestLocation] = []
        for target in targets:
            index, evidence, units = contest_evidence(file_path, target, unit_count, page_budget)
            logger.info('detected %d distinct titles on %d pages; interpreting for %r',
                        len(evidence), len(index), target.contest)
            matched: list[str] = self._interpret(target, evidence)
            logger.info('interpreted %r -> %d matching title(s)', target.contest, len(matched))
            spans: list[tuple[int, int]] = segments_for_titles(index, matched, units)
            locations.append(ContestLocation(target=target.contest, ranges=spans,
                                             observed_title=matched[0] if matched else None))
        return dspy.Prediction(locations=locations)


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


def parse_target(spec: str, context: str = '') -> Target:
    '''Parse a "Contest=tok1,tok2,..." CLI target spec, with optional free-form context.'''
    contest, _, rest = spec.partition('=')
    hints: list[str] = [h.strip() for h in rest.split(',') if h.strip()]
    return Target(contest=contest.strip(), context=context, hints=hints)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Locate target contests in a source file, returning unit ranges.')
    parser.add_argument('path', nargs='?', help='Source file (PDF/spreadsheet)')
    parser.add_argument('--target', action='append', default=[],
                        help='Repeatable "Contest=candidate,candidate,..." spec')
    parser.add_argument('--context', default='',
                        help='Free-form race knowledge applied to the --target contests')
    parser.add_argument('--gold', help='Run a labeled fixture by name substring (uses its gold targets)')
    parser.add_argument('--max-gap', type=int, default=DEFAULT_MAX_GAP)
    parser.add_argument('--budget', type=int, default=None, help='Cap units scanned')
    parser.add_argument('--scan-only', action='store_true',
                        help='Deterministic name scan + runs only; no LLM, no API')
    parser.add_argument('--titles', action='store_true',
                        help='Deterministic title detection + segments only; no LLM, no API')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Log scan progress to stderr (per-page reads, OCR, interpret)')
    parser.add_argument('--debug', action='store_true',
                        help='Also log dspy internals (prompts/responses)')
    args: argparse.Namespace = parser.parse_args()

    if args.verbose or args.debug:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format='%(message)s')
        logging.getLogger('oe2d').setLevel(logging.INFO)
        logging.getLogger('dspy').setLevel(logging.INFO if args.debug else logging.WARNING)

    from . import datasets
    if args.gold:
        path, targets, gold = datasets.fixture_request(args.gold)
        print(f'# {os.path.basename(path)}  (fixture gold range {gold})', file=sys.stderr)
    else:
        if not args.path or not args.target:
            parser.error('give a path and at least one --target, or use --gold')
        path, targets = args.path, [parse_target(spec, args.context) for spec in args.target]

    if args.scan_only:
        units = count_units(path)
        hits = scan_for_targets(path, targets, unit_count=units, max_gap=args.max_gap,
                                page_budget=args.budget)
        evidence = build_evidence(hits, targets, args.max_gap, unit_count=units)
        print(json.dumps({'units_hit': [h.unit for h in hits],
                          'runs': [e.model_dump() for e in evidence]}, indent=2))
        return

    if args.titles:
        for target in targets:
            index, evidence, units = contest_evidence(path, target, page_budget=args.budget)
            matched = [e.title for e in evidence if _title_matches(target, e.title)]
            print(json.dumps({
                'target': target.contest,
                'observed_titles': [e.model_dump() for e in evidence],
                'deterministic_matches': matched,
                'segments': segments_for_titles(index, matched, units)}, indent=2))
        return

    print(json.dumps(locate(path, targets, args.max_gap, args.budget), indent=2))


if __name__ == '__main__':
    main()
