'''DSPy signature for the page analyzer, plus the label vocabularies its fields use.

The signature's docstring is the SEED instruction GEPA evolves; the optimized
instruction lives on the predictor and is serialized into the model artifact, so
editing here changes only the starting point, not a committed optimized program.
'''
from __future__ import annotations

import enum

import dspy


# Per-page label vocabularies as StrEnums; the DSPy output fields (below) and the
# pydantic PageProperties result model both take their types from these, so the
# taxonomy has a single named definition. StrEnum members ARE their wire value
# ('columns'), so gold JSON coerces in and metric comparisons and JSON output stay
# string-clean, while DSPy still constrains the model to the member values.
class CandidateOrientation(enum.StrEnum):
    COLUMNS = 'columns'
    ROWS = 'rows'


class PrecinctScope(enum.StrEnum):
    MULTI_PRECINCT = 'multi_precinct'
    PER_PRECINCT = 'per_precinct'
    COUNTY = 'county'


# The precinct axis is only meaningful for multi_precinct pages; NONE covers
# per_precinct (one precinct, named in a header) and county (no precinct at all),
# so the field is always a concrete member rather than null.
class PrecinctAxis(enum.StrEnum):
    ROWS = 'rows'
    COLUMNS = 'columns'
    NONE = 'none'


class PageAnalysis(dspy.Signature):
    '''Report factual, in-page observations about ONE election-results page image.

    You are shown a single page image, not a whole document. Describe only what is
    visible on THIS page; do not infer contests or precincts that would be on other
    pages.
    '''
    image: dspy.Image = dspy.InputField(desc='A single rendered election-results page')
    candidate_orientation: CandidateOrientation = dspy.OutputField(
        desc="'columns' when each candidate/party is a column (and precincts run "
             "down the rows); 'rows' when each candidate/party is a row")
    contest_name_present: bool = dspy.OutputField(
        desc='Is a contest/office title visible on this page? A continuation page '
             'that just carries more candidate columns or more precinct rows often '
             'has none')
    candidate_names_present: bool = dspy.OutputField(
        desc='Are candidate or party names visible on this page? False on a bare '
             'data-only continuation page')
    headers_present: bool = dspy.OutputField(
        desc='Are column/row headers labeling the numbers present on this page?')
    precinct_scope: PrecinctScope = dspy.OutputField(
        desc="'multi_precinct' when the page lays out many precincts along an axis; "
             "'per_precinct' when the page is a single precinct named in a heading "
             "with its results below; 'county' when the page shows county-wide "
             "aggregates with no precinct dimension")
    precinct_orientation: PrecinctAxis = dspy.OutputField(
        desc="For a multi_precinct page, whether precincts are 'rows' or 'columns'; "
             "otherwise 'none'")
