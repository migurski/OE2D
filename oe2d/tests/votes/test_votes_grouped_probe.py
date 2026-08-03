'''Tests for the candidate-GROUP probe (_grids_split_candidates, hermetic: hand-built page grids in,
a flat_grouped/not decision out).

A candidate-GROUP contest (flat_grouped) is a flat scan too WIDE for one page: its candidate columns
are split across pages that all repeat the SAME precincts down the rows. Page 1 alone is
indistinguishable from an ordinary flat table, so dispatch upgrades to flat_grouped only when a later
page carries a candidate the first page lacks AND the precinct rows repeat. This pins that decision --
and its rejection of a plain continuation (new precincts) and a single-contest scan (all candidates on
page 1, the shape that must stay auto/ruled).
'''
from ... import votes

# One flat page: [Registered, Precinct(label), then candidate columns]. The header row names the
# candidates; data rows carry a precinct label and counts; the last row is the county Total.
GROUP_P1 = [
    ['Reg', 'Precinct', 'Harris', 'Trump'],
    ['100', 'Alpha', '40', '50'],
    ['120', 'Bravo', '55', '60'],
    ['', 'Total', '95', '110'],
]
# page 2 REPEATS Alpha/Bravo under DIFFERENT candidates -> a candidate group split.
GROUP_P2 = [
    ['Reg', 'Precinct', 'Oliver', 'Stein'],
    ['100', 'Alpha', '3', '4'],
    ['120', 'Bravo', '5', '2'],
    ['', 'Total', '8', '6'],
]
# A plain CONTINUATION: same candidates, NEW precincts (more precincts, not more candidates).
CONT_P2 = [
    ['Reg', 'Precinct', 'Harris', 'Trump'],
    ['110', 'Charlie', '30', '35'],
    ['130', 'Delta', '40', '45'],
    ['', 'Total', '70', '80'],
]
WANTED = {votes._norm(n) for n in ('Harris', 'Trump', 'Oliver', 'Stein')}


def test_group_split_is_detected() -> None:
    # Oliver/Stein appear only on page 2, and Alpha/Bravo repeat -> a candidate-group contest.
    assert votes._grids_split_candidates([GROUP_P1, GROUP_P2], WANTED) is True


def test_continuation_is_not_a_group_split() -> None:
    # page 2 carries the SAME candidates (Harris/Trump) -> no later-only name -> not grouped.
    assert votes._grids_split_candidates([GROUP_P1, CONT_P2], {votes._norm('Harris'), votes._norm('Trump')}) is False


def test_single_page_is_not_a_group_split() -> None:
    assert votes._grids_split_candidates([GROUP_P1], WANTED) is False


def test_later_only_names_without_repeat_precincts_is_not_grouped() -> None:
    # A later page introduces a new candidate BUT on entirely new precincts (not a repeat) -> reject:
    # this is the disjoint shape, not a group split.
    new_precincts = [
        ['Reg', 'Precinct', 'Oliver', 'Stein'],
        ['110', 'Charlie', '3', '4'],
        ['130', 'Delta', '5', '2'],
        ['', 'Total', '8', '6'],
    ]
    assert votes._grids_split_candidates([GROUP_P1, new_precincts], WANTED) is False


def test_grid_precincts_reads_the_nonnumeric_label_column() -> None:
    # the precinct column (index 1) is the leading mostly-non-numeric column; counts are excluded.
    keys = votes._grid_precincts(GROUP_P1)
    assert votes._precinct_key('Alpha') in keys
    assert votes._precinct_key('Bravo') in keys
    assert votes._precinct_key('Total') in keys       # the county-total row's label is a precinct-ish cell
    assert '40' not in keys and '50' not in keys       # numeric candidate cells are not precincts


def test_candidate_tokens_drops_short_tokens_and_party() -> None:
    tokens = votes._candidate_tokens('- Joseph R Biden (DEM)\n- Donald J Trump (REP)')
    assert votes._norm('Biden') in tokens
    assert votes._norm('Trump') in tokens
    assert votes._norm('R') not in tokens              # too short
    assert votes._norm('DEM') not in tokens            # party parenthetical is stripped
