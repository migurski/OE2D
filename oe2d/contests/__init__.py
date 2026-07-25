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

Usage: oe2d-locate-contests file.pdf --target President --target "U.S. Senate (full term)" \
           --context "presidential race, Harris vs Trump; the full-term Senate seat"
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


# Leading words of table-header / boilerplate rows that repeat on results pages but are
# NOT contest titles -- so the marker-free header detector must not emit them.
_HEADER_STOP: frozenset[str] = frozenset({
    'choice', 'candidate', 'precinct', 'registered', 'voters', 'voter', 'cast', 'times',
    'total', 'totals', 'turnout', 'party', 'ballots', 'ballot', 'cumulative', 'election',
    'official', 'statement', 'reporting', 'run', 'undervotes', 'overvotes', 'write',
})


# A loose "this line names an elected OFFICE (a noun) or ballot question" recall net -- NOT
# target matching (the LLM does that). It only gates marker-free heading candidates so two
# kinds of impostor are rejected: mirrored/garbled candidate rows (no readable office word),
# and bare geography SUBTOTAL labels ("1st Congressional District", "5th Senatorial District")
# that carry a district adjective but NO office noun. So the net holds office NOUNS only --
# 'congressional'/'senatorial'/'district' are deliberately excluded (they are the geography a
# subtotal label is made of). Need not be complete; a missed local contest is not our target.
_OFFICE_ANCHORS: frozenset[str] = frozenset({
    'president', 'vice', 'senator', 'senate', 'representative', 'congress',
    'assembly', 'assemblymember', 'legislature', 'governor', 'lieutenant', 'supervisor', 'mayor',
    'council', 'councilmember', 'clerk', 'treasurer', 'auditor', 'controller', 'comptroller',
    'attorney', 'sheriff', 'assessor', 'coroner', 'board', 'director', 'trustee', 'commissioner',
    'superintendent', 'judge', 'justice', 'measure', 'proposition', 'recall', 'bond', 'member',
    'secretary', 'education', 'mayor',
})


def _heading_candidates(text: str) -> list[str]:
    '''Lines on a page that could be a marker-free contest heading (a running header or a
    section title that carries no "vote for" marker). A heading is a multi-word phrase that
    NAMES AN OFFICE / ballot question and carries no vote data -- not a banner, not a table-
    header row, not a numeric row, and not a mirrored/garbled candidate line. Permissive on
    WHICH office (the LLM interprets that); strict on "looks like a contest heading".'''
    out: list[str] = []
    for raw in text.splitlines():
        line: str = re.sub(r'\s+', ' ', raw.strip())
        if _is_banner(line):
            continue
        words: list[str] = re.findall(r"[A-Za-z][A-Za-z.'/,-]*", line)
        if len(words) < 3:                                  # contest titles are multi-word
            continue
        if len(re.findall(r'\d', line)) > 2 or '%' in line:  # a data/turnout row, not a title
            continue
        first: str = re.sub(r'^[\d.\s]+', '', line).split(' ')[0].lower().strip(".,'/-")
        if first in _HEADER_STOP:                           # "Choice Party...", "Precinct ..."
            continue
        lowered: set[str] = {w.lower().strip(".,'/-") for w in words}
        if lowered.isdisjoint(_OFFICE_ANCHORS):             # no office NOUN -> not a heading
            continue
        out.append(line[:160])
    return out


def header_title_index(texts: list[str]) -> dict[int, list[str]]:
    '''Marker-free titles: heading lines that recur on a BLOCK of pages (a running header or
    an unmarked section title), keyed by unit. A true contest heading repeats on several pages
    but NOT on most of them (that would be a universal table-header/banner); it identifies the
    contest whose block those pages belong to. Recovers running-header vendors (Alameda) and
    compound-doc sections that drop the "vote for" suffix (Yolo/San Joaquin district+precinct).'''
    n: int = len(texts)
    per_page: list[set[str]] = [set(_heading_candidates(t)) for t in texts]
    counts: dict[str, int] = {}
    for headings in per_page:
        for h in headings:
            counts[h] = counts.get(h, 0) + 1
    cap: int = max(3, int(n * 0.5))                         # reject universal lines (banners/headers)
    keep: set[str] = {h for h, c in counts.items() if 3 <= c <= cap}
    index: dict[int, list[str]] = {}
    for unit, headings in enumerate(per_page, 1):
        chosen: list[str] = [h for h in headings if h in keep]
        if chosen:
            index[unit] = chosen
    return index


def contest_title_index(path: str, unit_count: int | None = None,
                        page_budget: int | None = None) -> dict[int, list[str]]:
    '''Map each unit bearing contest-title line(s) to ALL its titles (cheap text / OCR).

    Reads every unit (no early stop) because a contest can recur anywhere in a by-precinct
    document. Captures every "vote for" marker title on a page (summary/precinct layouts stack
    several contests per page), then merges marker-free repeated headings (running-header and
    unmarked-section vendors). A page's marker titles win; header titles fill pages that have
    none. Returns {} only when the text carries no titles of either kind.
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    texts: list[str] = list(pagetext.layout_texts(path, limit))
    marker: dict[int, list[str]] = {}
    for unit, text in enumerate(texts, 1):
        titles: list[str] = _title_lines(text)
        if titles:
            marker[unit] = titles
    headers: dict[int, list[str]] = header_title_index(texts)
    index: dict[int, list[str]] = {}
    for unit in range(1, limit + 1):
        if unit in marker:
            index[unit] = marker[unit]                      # marker titles win
        elif unit in headers:
            index[unit] = headers[unit]                     # else marker-free heading
    return index


def _is_banner(line: str) -> bool:
    '''A page banner / boilerplate line (page number, timestamp), not a contest name.'''
    low: str = line.strip().lower()
    return (not low or low.startswith('page') or low.startswith('sovc')
            or bool(re.match(r'^[\d/:\s%.,\-]+$', line.strip())))


def _title_regions(text: str) -> list[tuple[str, str]]:
    '''Contest titles on a page paired with the text under each (title line through the line
    before the next title). The title is the "vote for" marker line, prefixed with the line
    above only when the marker line has no contest name before it (Electionware "PRESIDENTIAL
    ELECTORS / Vote For 1"); a page banner above the marker is dropped. The region is where a
    contest's candidates sit, for reading back later.'''
    lines: list[str] = text.splitlines()
    markers: list[int] = [i for i, line in enumerate(lines) if _CONTEST_MARKER.search(line)]
    regions: list[tuple[str, str]] = []
    for pos, i in enumerate(markers):
        match = _CONTEST_MARKER.search(lines[i])
        before: str = lines[i][:match.start()]
        if len(re.findall(r'[A-Za-z]', before)) >= 4:      # contest name already on this line
            title: str = lines[i].strip()
        else:
            above: str = lines[i - 1].strip() if i > 0 else ''
            title = (('' if _is_banner(above) else above) + ' ' + lines[i].strip()).strip()
        end: int = markers[pos + 1] if pos + 1 < len(markers) else len(lines)
        region: str = '\n'.join(lines[i:end]).strip()
        regions.append((title[:160], region))
    return regions


def _title_lines(text: str) -> list[str]:
    '''Just the contest-title strings on a page (see _title_regions).'''
    return [title for title, _ in _title_regions(text)]


def _title_matches(target: Target, title: str) -> bool:
    '''Conservative EXACT-word check, for the offline fallback and the eval floor only.

    Every significant target word must appear verbatim in the title. It deliberately does
    NO fuzzy matching -- wording variants (senate~senator, presidential~president, "U.S.
    House"~"Representative in Congress") are the LLM's job, handled by the ReAct search tools
    (which substring-match on a keyword the model chooses). Keeping this dumb avoids the
    endless tail of a hand-rolled similarity function.
    '''
    want: set[str] = _significant_words(target.contest)
    have: set[str] = set(re.findall(r'[a-z]+', title.lower()))
    return bool(want) and want <= have


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
    '''One distinct contest title observed in a document, with the text under it.'''
    title: str = pydantic.Field(desc='The observed contest-title text, verbatim')
    units: list[int] = pydantic.Field(desc='Units where this title appears')
    sample: str = pydantic.Field(
        default='', desc='Text under the title on its first page (its candidate rows)')


class MatchContestTitles(dspy.Signature):
    '''Find which of a document's observed contest titles are the requested target contest.

    Detection of the titles is already done deterministically; your job is the interpretation
    the strings cannot do. A document can hold hundreds of contest titles, so do NOT expect
    them in the prompt -- EXPLORE with the tools: search the titles by keyword, and read the
    candidate rows under a title (inspect_title) to confirm the race by who ran in it. Titles
    vary widely by jurisdiction and vendor:
    "U.S. House" may appear as "Representative in Congress", "House of Representatives", or
    "Congressional District N"; "State House" as "Representative in State Legislature" or
    "State Assembly"; "President" as "Electors of President and Vice-President", "Presidential
    Electors", or "PRESIDENT AND VICE PRESIDENT". Search several wordings. Use the context (the
    race, its candidates) to confirm a match and to disambiguate near-duplicates -- different
    districts, and full-term vs partial/unexpired-term seats -- choosing the one the target
    refers to. Return the matching titles verbatim as the tools reported them; return none if
    the document has no such contest.
    '''
    contest: str = dspy.InputField(desc='The target contest label to find')
    context: str = dspy.InputField(desc='Free-form knowledge about the race and its candidates')
    matching_titles: list[str] = dspy.OutputField(
        desc='The observed titles (verbatim) that are the target contest')


def contest_evidence(path: str, unit_count: int | None = None,
                     page_budget: int | None = None) -> tuple[dict[int, list[str]],
                                                             list[TitleEvidence], int]:
    '''Read the document once (target-agnostic): title index + distinct-title evidence.

    Deterministic detection only -- every distinct contest title, the units it appears on,
    and the text under its first occurrence (its candidate rows, for reading back via a
    tool). Interpretation -- which titles ARE the target -- is the LLM's job.
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    texts: list[str] = list(pagetext.layout_texts(path, limit))
    headers: dict[int, list[str]] = header_title_index(texts)
    index: dict[int, list[str]] = {}
    by_title: dict[str, dict] = {}
    for unit, text in enumerate(texts, 1):
        regions: list[tuple[str, str]] = _title_regions(text)
        if regions:                                        # marker titles win, with their regions
            index[unit] = [title for title, _ in regions]
            for title, region in regions:
                slot = by_title.setdefault(title, {'units': [], 'sample': ''})
                slot['units'].append(unit)
                if not slot['sample']:
                    slot['sample'] = region[:800]
        elif unit in headers:                              # else marker-free headings on this page
            index[unit] = headers[unit]
            for title in headers[unit]:
                slot = by_title.setdefault(title, {'units': [], 'sample': ''})
                slot['units'].append(unit)
                if not slot['sample']:
                    at: int = text.find(title[:40])
                    slot['sample'] = (text[at:] if at >= 0 else text)[:800]
    evidence: list[TitleEvidence] = [
        TitleEvidence(title=title, units=sorted(slot['units']), sample=slot['sample'])
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
    '''Deterministic title detection, tool-driven interpretation, deterministic segmentation.

    detect (contest_evidence) -> interpret (a ReAct agent that SEARCHES the observed titles
    via tools and returns which are the target, using free-form context) -> segment
    (segments_for_titles: each matched title to the next). The model queries the title index
    rather than being handed hundreds of titles, so context stays bounded no matter how large
    the ballot. The deterministic prefix match (_title_matches) is the offline fallback.
    '''
    def __init__(self) -> None:
        super().__init__()
        self._evidence: list[TitleEvidence] = []
        self.match: dspy.Module = dspy.ReAct(
            MatchContestTitles,
            tools=[self.search_titles, self.inspect_title, self.list_titles],
            max_iters=8)

    def search_titles(self, keyword: str) -> list[str]:
        '''Return observed contest titles containing the keyword (case-insensitive substring).
        Try several wordings for a contest (e.g. "congress", "representative", "house").'''
        low: str = keyword.lower()
        seen: list[str] = list(dict.fromkeys(e.title for e in self._evidence if low in e.title.lower()))
        return seen[:30]

    def inspect_title(self, title: str) -> str:
        '''Return the text under an observed title (its candidate rows) so you can confirm
        the race by the candidates who ran in it. Pass a title verbatim from search_titles.'''
        want: str = title.strip().lower()
        for e in self._evidence:
            if e.title.strip().lower() == want or want in e.title.strip().lower():
                return e.sample or '(no rows captured)'
        return '(no such title)'

    def list_titles(self) -> list[str]:
        '''Return all distinct observed contest titles (an overview; may be long).'''
        return list(dict.fromkeys(e.title for e in self._evidence))[:250]

    def _interpret(self, target: Target) -> list[str]:
        try:
            prediction = self.match(contest=target.contest, context=target.context)
            matched: list[str] = [t for t in prediction.matching_titles
                                  if any(t.strip() == e.title.strip() for e in self._evidence)]
            if matched:
                return matched
        except Exception:
            pass
        return [e.title for e in self._evidence if _title_matches(target, e.title)]

    def forward(self, file_path: str, targets: list[Target], unit_count: int | None = None,
                max_gap: int = DEFAULT_MAX_GAP, page_budget: int | None = None) -> dspy.Prediction:
        index, evidence, units = contest_evidence(file_path, unit_count, page_budget)
        self._evidence = evidence          # the tools read this document's titles
        logger.info('detected %d distinct titles on %d pages', len(evidence), len(index))
        locations: list[ContestLocation] = []
        for target in targets:
            logger.info('interpreting for %r', target.contest)
            matched: list[str] = self._interpret(target)
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
    dspy.configure(lm=dspy.LM(categorize.TASK_LM, temperature=0.0, max_tokens=8192))
    locator: ContestLocator = build_locator()
    prediction = locator(file_path=file_path, targets=targets, max_gap=max_gap,
                         page_budget=page_budget)
    return [location.model_dump() if isinstance(location, ContestLocation)
            else dict(location) for location in prediction.locations]


def parse_target(spec: str, context: str = '') -> Target:
    '''Parse a CLI target: normally just a contest label ("President"). A legacy
    "Contest=tok,tok" form still sets hint tokens for the deterministic --scan-only path.'''
    contest, _, rest = spec.partition('=')
    hints: list[str] = [h.strip() for h in rest.split(',') if h.strip()]
    return Target(contest=contest.strip(), context=context, hints=hints)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description='Locate target contests in a source file, returning unit ranges.')
    parser.add_argument('path', nargs='?', help='Source file (PDF/spreadsheet)')
    parser.add_argument('--target', action='append', default=[],
                        help='Repeatable contest label, e.g. --target President '
                             '--target "U.S. Senate (full term)"')
    parser.add_argument('--context', default='',
                        help='Free-form prose about the races and candidates, shared by all '
                             '--target contests (the LLM uses it to interpret the titles)')
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
        index, evidence, units = contest_evidence(path, page_budget=args.budget)
        for target in targets:
            matched = [e.title for e in evidence if _title_matches(target, e.title)]
            print(json.dumps({
                'target': target.contest,
                'observed_titles': [e.title for e in evidence],
                'deterministic_matches': matched,
                'segments': segments_for_titles(index, matched, units)}, indent=2))
        return

    print(json.dumps(locate(path, targets, args.max_gap, args.budget), indent=2))


if __name__ == '__main__':
    main()
