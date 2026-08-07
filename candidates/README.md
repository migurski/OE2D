# candidates/

Per-jurisdiction rosters of the candidates who appeared on the ballot for each federal race,
one plain-text file per county (or county-equivalent). These are used as **candidate context**
for the `oe2d` election-results extraction: given a county's results document, knowing the
offices and the names/parties that should appear helps locate and interpret the contests.

## Layout

```
candidates/<year>/<election>/<State>/<County>.txt
```

e.g. `candidates/2024/general/California/Alameda.txt`. The `<year>/<election>` level leaves
room for other cycles (2022, primaries, …); so far only `2024/general` is populated —
**56 jurisdictions** (50 states + DC + 5 territories), **3,235 files**.

## File format

One line per race the jurisdiction votes in, of the form:

```
Candidates for <office> were <Name> (<CODE>), <Name> (<CODE>), and <Name> (<CODE>)
```

Two candidates are joined with `and`; three or more use a serial comma. Example
(`California/Alameda.txt`):

```
Candidates for president were Kamala Harris (DEM), Donald Trump (REP), Robert F. Kennedy Jr. (IND), Jill Stein (GRN), and Chase Oliver (LIB)
Candidates for U.S. Senate (full term) were Adam Schiff (DEM) and Steve Garvey (REP)
Candidates for U.S. Senate (partial/unexpired term) were Adam Schiff (DEM) and Steve Garvey (REP)
Candidates for U.S. House District 10 were Mark DeSaulnier (DEM) and Katherine Piccinini (REP)
Candidates for U.S. House District 12 were Lateefah Simon (DEM) and Jennifer Tran (DEM)
Candidates for U.S. House District 14 were Eric Swalwell (DEM) and Vin Kruttiventi (REP)
Candidates for U.S. House District 17 were Ro Khanna (DEM) and Anita Chen (REP)
```

Which lines a file has:

- **President** — every jurisdiction that casts presidential votes (the 50 states + DC). Territories have none.
- **U.S. Senate** — the 33 states with a regular 2024 (Class I) election get one line. California and Nebraska each held two races in 2024, so they get two lines: `U.S. Senate (full term)` and `U.S. Senate (partial/unexpired term)`.
- **U.S. House** — one line per congressional district that overlaps the county. At-large states use `U.S. House (at-large)`. DC and the territories elect a non-voting member, written `U.S. House Delegate`, except **Puerto Rico** which uses `Resident Commissioner`.

## Office label strings

| situation | label |
|---|---|
| president | `president` |
| senate, single race | `U.S. Senate` |
| senate, CA/NE (two races) | `U.S. Senate (full term)`, `U.S. Senate (partial/unexpired term)` |
| house, numbered district | `U.S. House District N` |
| house, at-large state | `U.S. House (at-large)` |
| DC / territory delegate | `U.S. House Delegate` |
| Puerto Rico | `Resident Commissioner` |

## Party codes

`DEM`, `REP`, `LIB` (Libertarian), `GRN` (Green), `IND` (Independent), `CST` (Constitution),
and other short codes for minor/local parties. The two major parties are normalized **uniformly**
to `DEM`/`REP` even where the source uses a state-affiliate label — Minnesota's DFL, North Dakota's
Democratic-NPL, and Puerto Rico's `PPD/Democratic` and `PNP/Republican` all become `DEM`/`REP`.
Genuinely distinct third and local parties keep their own codes (e.g. Puerto Rico's `MVC`, `PD`, `PIP`).

## Sources

- **County ↔ congressional-district overlaps** — `county-districts.csv`, produced by a QGIS spatial
  join of the U.S. Census Bureau TIGER/Line county boundaries against the 119th-Congress congressional-
  district boundaries. Each row is one county×district overlap and carries the district number
  (`CD119FP`), the county GEOID/name, and the state name. Counties spanning multiple districts appear
  in multiple rows; at-large states and the delegate jurisdictions use the placeholder codes `00` / `98`.
- **Candidate rosters and parties** — the three 2024 election articles on Wikipedia:
  - President — <https://en.wikipedia.org/wiki/2024_United_States_presidential_election> ("Results by state" table)
  - Senate — <https://en.wikipedia.org/wiki/2024_United_States_Senate_elections> (per-state "Incumbent / Candidates" summary table; the Nebraska special election from its own section)
  - House — <https://en.wikipedia.org/wiki/2024_United_States_House_of_Representatives_elections> (each state's "District / Incumbent / Candidates" table, and the "Non-voting delegates" table for DC and the territories)

## How the files were generated

A one-off Python pass (BeautifulSoup over saved copies of the three articles, plus the CSV):

1. **Counties.** Parse `county-districts.csv` into, per county, its state, a display label, and the
   set of overlapping district codes.
2. **President.** In the presidential "Results by state" table each state row has vote columns for the
   five broken-out national candidates — Donald Trump (REP), Kamala Harris (DEM), Jill Stein (GRN),
   Robert F. Kennedy Jr. (IND), Chase Oliver (LIB). A candidate is included for a state when that
   state's row shows a nonzero vote count for them (so candidates off a given state's ballot are
   dropped). Order is DEM, then REP, then any remaining candidates by descending vote count in that
   state. (DC's row is labeled `D.C.`)
3. **Senate.** The per-state summary table gives the regular-election candidates for the 33 states.
   The two 2024 special elections are added as the `(partial/unexpired term)` line and cause the
   regular line to be marked `(full term)`: California's special had the same two candidates as its
   regular race; Nebraska's special was Pete Ricketts (REP) and Preston Love Jr. (DEM).
4. **House.** Each state's district table gives the candidates per district; the "Non-voting delegates"
   table gives the delegate / Resident Commissioner candidates for DC and the five territories
   (matched to Census names, e.g. "Northern Mariana Islands" → "Commonwealth of the Northern Mariana
   Islands").
5. **Candidate parsing.** Each candidate entry ("Name (Party) 58.9%", with a winner marker) is reduced
   to a name and a party code, applying the major-party normalization above.
6. **Write.** One file per county: the president line (where applicable), the Senate line(s), then one
   House line per overlapping district (numeric districts ascending). The filename is the county's
   `NAMELSAD` with a generic type suffix stripped — `County`, `Parish`, `Borough`, `Census Area`,
   `Municipality`, `Municipio`, `City and Borough` — while distinctive suffixes are kept so
   independent cities and islands stay unambiguous (`Richmond city`, `St. Thomas Island`).

## Validation

The output was spot-checked against the sources: 25 randomly chosen counties plus forced coverage of
the tricky cases — DC, Puerto Rico (Resident Commissioner, local parties), Minnesota (DFL→DEM),
North Dakota (at-large + Democratic-NPL→DEM), Guam and American Samoa delegates, and California and
Nebraska (dual Senate lines). For each, the districts were checked against the CSV and the candidate
name-sets against the Wikipedia tables; all matched.

## Scope notes

- Territories (American Samoa, Guam, Northern Mariana Islands, Puerto Rico, U.S. Virgin Islands) cast
  no presidential or Senate vote, so their files contain only the delegate line.
- Party affiliations follow the source articles as of the data snapshot; a candidate who suspended a
  campaign but remained on a state's ballot (e.g. Kennedy) is included wherever that state recorded
  votes for them.
