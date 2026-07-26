'''DSPy signatures for the contest locator's two LLM steps.

Each signature's docstring is the SEED instruction GEPA evolves; the optimized
instruction lives on the predictor and is serialized into the model artifact, so
editing here changes only the starting point, not a committed optimized program.

Split by kind of judgment, matching the two independently-optimizable predictors in
ContestLocator: ClassifyContestTitles is document-wide and target-agnostic ("which
strings name a contest at all?"); MatchContestTitles is per-target ("which of those
is THIS race, in all its wordings?").
'''
from __future__ import annotations

import dspy


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
    Electors", or "PRESIDENT AND VICE PRESIDENT". Search several wordings.

    Return EVERY observed title that IS the target contest -- not just one. A single contest is
    often printed under MORE THAN ONE wording in the same document: a cumulative/summary section
    and a per-precinct or per-district section word it differently (e.g. "President and Vice
    President - Vote for One" AND "President and Vice President"), and a write-in tally adds
    another. Each wording carries its own pages of votes, so you must return ALL of them. Keep
    these same-contest duplicates together; only DISTINGUISH near-duplicates that are genuinely
    DIFFERENT races -- a different district number, or a full-term vs partial/unexpired-term seat
    -- and among those include only the ones the target refers to. Use the context (the race, its
    candidates) to confirm a match. Return the matching titles verbatim as the tools reported
    them; return none if the document has no such contest.
    '''
    contest: str = dspy.InputField(desc='The target contest label to find')
    context: str = dspy.InputField(desc='Free-form knowledge about the race and its candidates')
    matching_titles: list[str] = dspy.OutputField(
        desc='The observed titles (verbatim) that are the target contest')


class ClassifyContestTitles(dspy.Signature):
    '''From candidate heading lines pulled from an election results document, return ONLY the ones
    that name a CONTEST -- an office or ballot question voters actually vote on (e.g. "President and
    Vice President", "U.S. Representative, 12th Congressional District", "School Directors - Berkeley",
    "Measure NN - City of Oakland"). DROP everything else: candidate-name fragments ("F. KENNEDY AI -
    ROBERT", "ROBINSON FABIAN DANINO"), party/column labels, running totals ("Assembly District -
    Total 96,106"), footnotes ("*** Indicates vote data was suppressed"), precinct/registration lines,
    and geographic SUBTOTAL or grouping labels ("3rd Assembly District", "1st Congressional District",
    "City of Oroville - District A") -- these group a contest's precincts but are not themselves the
    contest. When a grouping label and the real contest look similar, keep the one phrased as an office
    ("State Assembly, 18th District") and drop the bare geography ("3rd Assembly District"). Return the
    kept lines VERBATIM.'''
    candidates: list[str] = dspy.InputField(desc='Candidate heading lines, verbatim')
    contest_titles: list[str] = dspy.OutputField(
        desc='The subset that name a contest voters vote on, verbatim')
