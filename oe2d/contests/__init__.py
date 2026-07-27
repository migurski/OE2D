'''Locate target contests within a whole source file, returning each target's page SET.

Given a source file (PDF pages or spreadsheet sheets) and target contests -- each a plain
label ("President", "U.S. Senate (full term)") plus shared free-form context -- find the
set of pages/sheets where each contest's results appear. It LOCATES; reconstructing the
vote tables is a separate, later step.

The anchor is the contest TITLE. A cheap read (oe2d.pagetext -- free structured text, local
tesseract OCR for scans) detects every contest title in the document -- both "vote for"
marker titles and marker-free running headers -- and the pages each appears on. A DSPy
program then maps each target label to the document's own title wording (the judgment calls:
"Representative in Congress" == "U.S. House", "Electors of President" == "President", full-
term vs partial/unexpired), and the pages carrying the matched titles are the answer.

Usage: oe2d-contests file.pdf --target President --target "U.S. Senate (full term)" \
           --context "presidential race, Harris vs Trump; the full-term Senate seat"
       oe2d-contests --titles file.pdf        # list the document's contest titles
       oe2d-contests --gold barry             # run a labeled fixture
'''
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

import dotenv
import dspy
import pydantic

from .. import pagetext, rendering, source_table
from . import signatures

logger: logging.Logger = logging.getLogger(__name__)

OPTIMIZED_MODEL_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'model', 'optimized_contest_locator.json')


class Target(pydantic.BaseModel):
    '''A contest to find, plus knowledge that aids finding and interpreting it.'''
    contest: str
    context: str = pydantic.Field(
        default='',
        description='Free-form knowledge about the race and its candidates, e.g. '
                    '"presidential race between Trump and Harris, third-party Stein and Oliver"')


class ContestLocation(pydantic.BaseModel):
    '''Where a target contest was found: the SET of units carrying its votes, and the
    observed title. The page set is the single representation -- a contest's pages are often
    scattered (stacked precinct reports, compound docs), so an explicit sorted list is the
    truth; contiguous blocks are just the special case where the list happens to be a run.'''
    target: str
    pages: list[int]
    observed_title: str | None = None


# Party abbreviations/labels appear on every results page, so they are weak evidence;
# _significant_words drops them when comparing a target label to a contest title.
_PARTY_TOKENS: frozenset[str] = frozenset({
    'dem', 'rep', 'lib', 'grn', 'ust', 'nlp', 'ind', 'wf', 'con',
    'democratic', 'republican', 'libertarian', 'green', 'constitution',
})


def _is_party(token: str) -> bool:
    return token.strip().lower() in _PARTY_TOKENS


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


def _heading_candidates(text: str) -> list[str]:
    '''Lines on a page that COULD be a marker-free contest heading -- a purely STRUCTURAL recall
    net, no lexicon. A candidate is a multi-word line carrying no vote data: not a banner, not a
    numeric/turnout row (a title may hold a contest number + a district ordinal, so count NUMBERS
    not digits and allow up to two). This deliberately over-collects -- candidate-name fragments,
    subtotal labels, and column headers pass too. Deciding which candidates actually NAME a contest
    is a judgment left to the LLM (classify_titles); a word-list did that badly (missed real races
    it had not memorised, kept subtotal labels that shared an office word).'''
    out: list[str] = []
    for raw in text.splitlines():
        line: str = re.sub(r'\s+', ' ', raw.strip())
        if _is_banner(line):
            continue
        words: list[str] = re.findall(r"[A-Za-z][A-Za-z.'/,-]*", line)
        if len(words) < 3:                                  # a heading is multi-word
            continue
        # Reject data/turnout rows by counting NUMBERS, not digits: a title may carry a contest
        # number plus a district ordinal ("1 U.S. Representative, 12th Congressional") = 2 numbers.
        if len(re.findall(r'\d[\d,]*', line)) > 2 or '%' in line:
            continue
        out.append(line[:160])
    return out


def header_title_index(texts: list[str]) -> dict[int, list[str]]:
    '''Marker-free heading CANDIDATES that recur on a BLOCK of pages (a running header or an
    unmarked section title), keyed by unit -- a structural recall net. A real contest heading
    repeats on several pages but NOT on most of them (that would be a universal table-header or
    banner). Recovers running-header vendors (Alameda) and compound-doc sections that drop the
    "vote for" suffix (Yolo/San Joaquin). Over-collects by design; classify_titles culls it.'''
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


def _is_data_row(line: str) -> bool:
    '''A vote-data row: >=2 bare numbers, no percent / date-time / registration preamble. Used to
    find where a results table starts so we can read the title directly above it.'''
    low: str = line.lower()
    if '%' in line or 'registered' in low or '/' in line or ':' in line:
        return False
    return len(re.findall(r'\b\d[\d,]*\b', line)) >= 2


def _table_titles(text: str) -> list[str]:
    '''The heading line directly above each vote table on a page -- a phrase-free, marker-free
    title signal (the title always sits above its results). Catches contests a "vote for" marker
    misses, notably ballot propositions/measures ("PROPOSITION 3", "MEASURE V"). Fails only where
    2D layout is scrambled in the text layer (rotated/mirrored pages); the marker and recurrence
    signals cover those, and the union is deduped and judged by the LLM classifier.'''
    lines: list[str] = [re.sub(r'\s+', ' ', x.strip()) for x in text.splitlines() if x.strip()]
    out: list[str] = []
    prev: str | None = None
    in_table: bool = False
    for line in lines:
        if _is_data_row(line):
            if not in_table and prev:              # a table just began -> prev line is its title
                out.append(prev[:160])
                in_table = True
        else:
            in_table = False
            if not _is_banner(line):
                prev = line
    return out


def _page_titles(text: str) -> set[str]:
    '''All candidate contest-title strings on a page -- the union of the cheap, phrase-free
    signals (title-above-a-table, "vote for" marker, multi-word heading). Over-collects on
    purpose (candidate fragments, subtotal labels slip in); the LLM classifier culls the union
    down to real contests, once per distinct string across the document.'''
    got: set[str] = set(_table_titles(text)) | set(_title_lines(text)) | set(_heading_candidates(text))
    return {re.sub(r'\s+', ' ', g.strip()) for g in got if g.strip()}


# A contest's title recurs on >= this many pages when the vendor repeats it (per precinct, or as
# a running header); below it the title appeared once and heads a block of continuation pages.
_RECUR_MIN: int = 3

# Classify the distinct candidate strings in chunks this size, so the LLM's verbatim-echo output
# stays under the token limit on ballot-heavy files (a county can carry >120 contest strings).
_CLASSIFY_CHUNK: int = 25


def _locate_pages(units: list[int], title_pages: list[int], unit_count: int) -> set[int]:
    '''Pages carrying a contest's votes, from the pages its title string OCCURS on. If the title
    recurs (>= _RECUR_MIN), each occurrence is a self-contained result (one precinct / one running-
    header page) -> the occurrences ARE the answer, no span inference (this is what kills the
    segmentation bleed). If it occurs once, it heads a block -> span it to the next title page.'''
    if len(units) >= _RECUR_MIN:
        return set(units)
    pages: set[int] = set()
    for p in units:
        after: list[int] = [q for q in title_pages if q > p]
        end: int = (after[0] - 1) if after else unit_count
        pages |= set(range(p, end + 1))
    return pages


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


class TitleEvidence(pydantic.BaseModel):
    '''One distinct contest title observed in a document, with the text under it.'''
    title: str = pydantic.Field(desc='The observed contest-title text, verbatim')
    units: list[int] = pydantic.Field(desc='Units where this title appears')
    sample: str = pydantic.Field(
        default='', desc='Text under the title on its first page (its candidate rows)')


def contest_evidence(path: str, unit_count: int | None = None, page_budget: int | None = None,
                     classify=None) -> tuple[list[TitleEvidence], int]:
    '''Read the document once (target-agnostic) and learn its CONTEST-STRING VOCABULARY.

    Surface candidate title strings on every page (_page_titles: title-above-a-table, "vote for"
    marker, multi-word heading -- a union that over-collects), collect the DISTINCT strings, and
    when a classifier is given, judge that small set once ("which are contests?"). Because a
    document speaks a tiny vocabulary that repeats -- one precinct's contests recur for every
    precinct -- the expensive judgment runs per distinct string, not per page. Returns one
    TitleEvidence per kept contest string: the pages its string OCCURS on (locating is then a
    string match, not a span guess) and the text under its first occurrence. Without a classifier
    the raw candidate union is kept (offline / deterministic use).
    '''
    if unit_count is None:
        unit_count = count_units(path)
    limit: int = min(unit_count, page_budget) if page_budget else unit_count
    texts: list[str] = list(pagetext.layout_texts(path, limit))
    per_page: list[set[str]] = [_page_titles(t) for t in texts]
    distinct: list[str] = sorted({c for cands in per_page for c in cands})
    known: set[str] = set(classify(distinct)) if (classify and distinct) else set(distinct)
    by_title: dict[str, dict] = {}
    for unit, (cands, text) in enumerate(zip(per_page, texts), 1):
        for title in known & cands:
            slot = by_title.setdefault(title, {'units': [], 'sample': ''})
            slot['units'].append(unit)
            if not slot['sample']:
                at: int = text.find(title[:40])
                slot['sample'] = (text[at:] if at >= 0 else text)[:800]
    evidence: list[TitleEvidence] = [
        TitleEvidence(title=title, units=sorted(slot['units']), sample=slot['sample'])
        for title, slot in by_title.items()]
    return evidence, unit_count


def segments_for_titles(index: dict[int, list[str]], matched_titles: list[str],
                        unit_count: int) -> list[tuple[int, int]]:
    '''Title-to-next-title spans for chosen title strings. Retained for the deterministic eval
    floor (evaluate.py); the shipped locator uses occurrence-based _locate_pages instead.'''
    wanted: set[str] = {title.strip() for title in matched_titles}
    starts: list[int] = sorted(index)
    spans: list[tuple[int, int]] = []
    for pos, unit in enumerate(starts):
        if any(title.strip() in wanted for title in index[unit]):
            end: int = (starts[pos + 1] - 1) if pos + 1 < len(starts) else unit_count
            spans.append((unit, end))
    return spans


# The task LM: Fireworks' Kimi K2, driving both of ContestLocator's predictors (classify,
# match). Kept here beside the program, not in a shared config module, so the LM lives next
# to what uses it. litellm reads FIREWORKS_AI_API_KEY.
LM_KIMI_K2P7: str = 'fireworks_ai/accounts/fireworks/models/kimi-k2p7-code'


def _collapse_digits(text: str) -> str:
    '''A digit-collapsed form of a candidate string: every digit becomes '1', so strings that
    differ only in their numbers ("District 12" / "District 14" -> "District 11") map to one
    form. Whether a string NAMES a contest doesn't depend on the specific numbers, so each form
    is classified once and the verdict applied to all its originals. Digits are REPLACED, not
    stripped, so the form the model classifies stays realistic ("District 11", not "District ").
    Grouping only -- the vocabulary keeps every original string verbatim, so districts stay
    distinct downstream.'''
    return re.sub(r'\d', '1', text)


class ContestLocator(dspy.Module):
    '''Learn a document's contest-string vocabulary, then locate targets by string occurrence.

    detect (contest_evidence: surface candidate strings on every page) -> classify (an LLM pass,
    chunked, that keeps only the distinct strings naming a contest -- culling candidate fragments,
    subtotal labels, totals) -> interpret (a ReAct agent that searches the learned strings and
    returns which are the target, using free-form context) -> locate (_locate_pages: the pages a
    matched string OCCURS on, since a recurring title needs no span inference; a once-only title
    spans to the next title). Both LLM steps are optimizable and run per DISTINCT string / per
    target, not per page. The LLM is trusted end to end: an unavailable LM is fatal, and an empty
    match means the contest is absent (no heuristic fallback in the live path).
    '''
    def __init__(self) -> None:
        super().__init__()
        self._evidence: list[TitleEvidence] = []
        self.classify: dspy.Module = dspy.Predict(signatures.ClassifyContestTitles)
        self.match: dspy.Module = dspy.ReAct(
            signatures.MatchContestTitles,
            tools=[self.search_titles, self.inspect_title, self.list_titles],
            max_iters=8)

    def _classify_headers(self, candidates: list[str]) -> list[str]:
        '''Keep only the candidate strings that name a contest (LLM). The verdict is
        digit-invariant, so strings that differ only in numbers (per-precinct total rows,
        "district N") are collapsed to ONE representative and classified once, then the verdict
        is applied to every original sharing that form -- far fewer LLM calls on ballot-heavy /
        by-precinct files. Chunked so the verbatim echo can't overrun the token limit. The LLM
        is required: a failed classify call propagates (an unavailable LM is fatal).'''
        groups: dict[str, list[str]] = {}
        for candidate in candidates:
            groups.setdefault(_collapse_digits(candidate), []).append(candidate)
        forms: list[str] = list(groups)          # digit-collapsed forms, one per group, sent to the LLM
        chunk_total: int = (len(forms) + _CLASSIFY_CHUNK - 1) // _CLASSIFY_CHUNK
        logger.info('classifying %d candidate string(s) as %d digit-collapsed form(s) in %d '
                    'chunk(s) of up to %d (one LLM call each)...',
                    len(candidates), len(forms), chunk_total, _CLASSIFY_CHUNK)
        kept_forms: set[str] = set()
        for number, start in enumerate(range(0, len(forms), _CLASSIFY_CHUNK), 1):
            chunk: list[str] = forms[start:start + _CLASSIFY_CHUNK]
            logger.info('  chunk %d/%d: classifying %d form(s)...', number, chunk_total, len(chunk))
            raw: list[str] = self.classify(candidates=chunk).contest_titles
            kept: set[str] = {t.strip().lower() for t in raw}        # verbatim, case-tolerant
            matched: list[str] = [form for form in chunk if form.strip().lower() in kept]
            kept_forms.update(matched)
            logger.info('  chunk %d/%d: kept %d form(s)', number, chunk_total, len(matched))
        chosen: list[str] = [c for c in candidates if _collapse_digits(c) in kept_forms]
        logger.info('classified %d candidates -> %d contest titles', len(candidates), len(chosen))
        return chosen

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
        # The LLM is required: a failed match call propagates (an unavailable LM is fatal).
        # An empty result means the contest is not in the document -- trust the LLM, no
        # heuristic fallback.
        prediction = self.match(contest=target.contest, context=target.context)
        return [t for t in prediction.matching_titles
                if any(t.strip() == e.title.strip() for e in self._evidence)]

    def forward(self, file_path: str, targets: list[Target],
                unit_count: int | None = None, page_budget: int | None = None) -> dspy.Prediction:
        evidence, units = contest_evidence(file_path, unit_count, page_budget,
                                           classify=self._classify_headers)
        self._evidence = evidence          # the tools read this document's learned vocabulary
        occ: dict[str, list[int]] = {e.title: e.units for e in evidence}
        title_pages: list[int] = sorted({p for e in evidence for p in e.units})
        logger.info('learned %d contest strings on %d title-pages', len(evidence), len(title_pages))
        locations: list[ContestLocation] = []
        for target in targets:
            logger.info('interpreting for %r', target.contest)
            matched: list[str] = self._interpret(target)
            logger.info('interpreted %r -> %d matching title(s)', target.contest, len(matched))
            pages: set[int] = set()
            for title in matched:
                if title in occ:
                    pages |= _locate_pages(occ[title], title_pages, units)
            locations.append(ContestLocation(target=target.contest, pages=sorted(pages),
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
    '''Construct the locator. A trained artifact, when present, fully governs (its saved
    prompts AND lm win); otherwise bind the stock inference LM.'''
    locator: ContestLocator = ContestLocator()
    if os.path.exists(OPTIMIZED_MODEL_PATH):
        locator.load(OPTIMIZED_MODEL_PATH)
    else:
        # temperature 0 for settled classification, with headroom so a verbatim-echo
        # classify pass or a multi-step ReAct trace doesn't truncate.
        locator.set_lm(dspy.LM(LM_KIMI_K2P7, temperature=0.0, max_tokens=8192))
    return locator


def locate(file_path: str, targets: list[Target],
           page_budget: int | None = None) -> list[dict]:
    '''Locate targets in a file with the full program; return plain dicts.'''
    _instrument()
    locator: ContestLocator = build_locator()
    prediction = locator(file_path=file_path, targets=targets, page_budget=page_budget)
    return [location.model_dump() if isinstance(location, ContestLocation)
            else dict(location) for location in prediction.locations]


def parse_target(spec: str, context: str = '') -> Target:
    '''Parse a CLI target: a contest label ("President"), sharing the run-wide context.'''
    return Target(contest=spec.strip(), context=context)


def resolve_context(spec: str) -> str:
    '''Resolve a --context value. An @-prefixed value reads the named file (curl-style,
    for long prose kept in a file); any other value is used verbatim.'''
    if not spec.startswith('@'):
        return spec
    try:
        with open(spec[1:], encoding='utf-8') as handle:
            return handle.read()
    except OSError as error:
        raise SystemExit(f'cannot read --context file {spec[1:]!r}: {error}')


def _safe_filename(label: str) -> str:
    '''A filesystem-safe name from a contest label ("U.S. Senate (full term)").'''
    return re.sub(r'[^A-Za-z0-9._-]+', '-', label).strip('-') or 'contest'


def write_trimmed_per_target(path: str, locations: list[dict], out_dir: str) -> list[str]:
    '''Write one trimmed copy of the source per located target into out_dir, each named for
    its target and sliced to that target's matched pages. Targets that matched no pages are
    skipped. Creates out_dir if needed; returns the paths written, in target order.'''
    os.makedirs(out_dir, exist_ok=True)
    ext: str = os.path.splitext(path)[1]
    written: list[str] = []
    for location in locations:
        pages: list[int] = location['pages']
        if not pages:
            continue
        out: str = os.path.join(out_dir, f'{_safe_filename(location["target"])}{ext}')
        write_trimmed(path, pages, out)
        written.append(out)
    return written


def write_trimmed(path: str, pages: list[int], out: str) -> None:
    '''Write a copy of the source containing ONLY the given 1-based pages/sheets, in order.

    Supports the paged containers the locator reads: PDF (page extraction) and xlsx (sheet
    extraction). Other containers have no meaningful page slice; raises for them.'''
    kept: list[int] = sorted(set(pages))
    if not kept:
        raise SystemExit('nothing to trim: no pages matched')
    container: str = rendering.detect_container(path)
    if container in ('vector_pdf', 'scanned_pdf'):
        _trim_pdf(path, kept, out)
    elif container == 'xlsx':
        _trim_xlsx(path, kept, out)
    else:
        raise SystemExit(f'--trim supports PDF and xlsx sources, not {container!r}')


def _trim_pdf(path: str, pages: list[int], out: str) -> None:
    import pypdf
    reader: pypdf.PdfReader = pypdf.PdfReader(path)
    writer: pypdf.PdfWriter = pypdf.PdfWriter()
    total: int = len(reader.pages)
    for page in pages:
        if 1 <= page <= total:
            writer.add_page(reader.pages[page - 1])
    with open(out, 'wb') as handle:
        writer.write(handle)


def _trim_xlsx(path: str, pages: list[int], out: str) -> None:
    import openpyxl
    workbook: openpyxl.Workbook = openpyxl.load_workbook(path)
    keep: set[int] = set(pages)
    for index, worksheet in enumerate(list(workbook.worksheets), 1):
        if index not in keep:
            workbook.remove(worksheet)
    workbook.save(out)


def main() -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Locate target contests in a source file, returning each target's page set.")
    parser.add_argument('path', nargs='?', help='Source file (PDF/spreadsheet)')
    parser.add_argument('--target', action='append', default=[],
                        help='Repeatable contest label, e.g. --target President '
                             '--target "U.S. Senate (full term)"')
    parser.add_argument('--context', default='',
                        help='Free-form prose about the races and candidates, shared by all '
                             '--target contests (the LLM uses it to interpret the titles). '
                             'Use @path to read the prose from a file')
    parser.add_argument('--gold', help='Run a labeled fixture by name substring (uses its gold targets)')
    parser.add_argument('--budget', type=int, default=None, help='Cap units read')
    parser.add_argument('--trim', metavar='DIR',
                        help='Also write, into directory DIR, one copy of the source (PDF or '
                             "xlsx) per --target -- each trimmed to that target's matched "
                             'pages/sheets and named for the target')
    parser.add_argument('--titles', action='store_true',
                        help='Inspect only: list every contest title detected in the document, in '
                             'its own words, with the pages each covers (no targets; uses the LLM '
                             'classifier to cull non-contest headings)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Log read progress to stderr (per-page reads, OCR, interpret)')
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
    elif args.titles:
        if not args.path:
            parser.error('give a path to inspect with --titles')
        path, targets = args.path, []          # target-agnostic: --titles just lists the doc's titles
    else:
        if not args.path or not args.target:
            parser.error('give a path and at least one --target, or use --gold')
        context: str = resolve_context(args.context)
        path, targets = args.path, [parse_target(spec, context) for spec in args.target]

    if args.titles:
        _instrument()
        locator: ContestLocator = build_locator()          # its LLM classifier culls the recall net
        evidence, _units = contest_evidence(
            path, page_budget=args.budget, classify=locator._classify_headers)
        titles = [{'title': e.title, 'pages': e.units} for e in evidence]
        print(json.dumps(titles, indent=2))
        return

    locations: list[dict] = locate(path, targets, args.budget)
    print(json.dumps(locations, indent=2))

    if args.trim:
        written: list[str] = write_trimmed_per_target(path, locations, args.trim)
        for out in written:
            print(f'# wrote {out}', file=sys.stderr)
        empty: list[str] = [loc['target'] for loc in locations if not loc['pages']]
        if empty:
            print(f'# no pages matched, skipped: {", ".join(empty)}', file=sys.stderr)


if __name__ == '__main__':
    main()
