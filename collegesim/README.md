# collegesim

Generates a simulated 10-year college SIS database (students, courses,
grades, awards, financial aid) in DuckDB, calibrated to a real institution's
IPEDS data. Works for small-to-medium institutions of any type: 2-year
colleges (e.g. Haywood CC) and 4-year colleges/universities with graduate
programs (e.g. Furman). Structure is derived from the IPEDS HD table
(ICLEVEL/CONTROL) automatically.

## Requirements

Python 3.10+ with `duckdb`, `numpy`, `faker` (`pip install duckdb numpy faker`).
An IPEDS DuckDB built from the NCES Access databases (the project-local
`ipeds_*.duckdb` from `import_ipeds2.r` is found automatically; otherwise the
IPEDSR package database in `%LOCALAPPDATA%\IPEDSR\database\ipeds.duckdb`).

## Usage

```sh
# build 10 years (academic years 2014-15 through 2023-24)
python -m collegesim build --school "Haywood" --start-year 2014 --end-year 2023

# any institution: name fragment or UNITID; 4-year schools work the same way
python -m collegesim build --school Furman ...

# append a newly released IPEDS year to an existing database
python -m collegesim supplement --db output/sim_198668.duckdb --year 2024

# compare the simulation against its stored IPEDS targets
python -m collegesim validate --db output/sim_198668.duckdb
```

Options: `--source` (IPEDS DuckDB path), `--out` (output path, default
`output/sim_<unitid>.duckdb`), `--seed` (default 42, reproducible),
`--malform-rate` (default 0.005), `--program-id-format` /
`--course-id-format` (see Identifiers), `--size-limit` / `--force` (see
Size limit).

## Size limit

Schools above `--size-limit` fall headcount (default 10,000) are refused:
generation at that scale is slow and memory-hungry, and the analytics are no
more interesting. The tool suggests smaller institutions with the same
control and level (preferring the same state) to model instead — e.g. asking
for Texas A&M suggests West Texas A&M. Use `--force` or a higher
`--size-limit` to proceed anyway.

## Identifiers

Program and course IDs are templated:

- `--program-id-format` placeholders: `{prefix}` (award-level letter:
  A associate, B bachelor's, D diploma, M master's, P doctoral, C other
  certificate), `{cip}` (CIP digits, no dot), `{cip_dotted}`.
  Default `{prefix}{cip}` -> `A513801`.
- `--course-id-format` placeholders: `{subject}`, `{number}`.
  Default `{subject}-{number}` -> `NUR-110`.
- `--section-id-format` placeholders: `{term}`, `{course}`, `{section}`
  (zero-padded section number). Default `{term}-{course}-{section}` ->
  `2014FA-NUR-110-01`.

Formats are stored in `sim_meta` and reused by `supplement`, so IDs stay
consistent across runs.

## What is calibrated vs emergent

Calibrated to IPEDS (matches within tolerance, mostly exactly):

- Fall census undergrad headcount, FT/PT split, first-time / transfer-in /
  non-degree counts, race x gender mix (EF*A), age mix (EF*B)
- Graduate headcount, FT/PT, race x gender (EF*A level 12), when present
- Fall-to-fall retention of first-time cohorts, FT and PT (EF*D)
- Awards conferred by 6-digit CIP and award level (C*_A), certificates
  through doctorates
- 150% graduation rate of first-time FT cohorts (GR; 3-year window at
  2-year schools, 6-year at 4-year, with the sector's GRTYPE codes)
- Pell recipient percentage and average amount; other grants; loans (SFA;
  undergraduate-based, per the survey)

Emergent (plausible, no IPEDS equivalent): course enrollments, grades and
course success rates, spring/summer terms, sections/instructors/modality
(online share spikes in 2020-21), GPA, program switching, graduate-student
persistence (no IPEDS retention/GR exists for grads).

## Schema

SIS tables: `institution`, `terms`, `programs`, `courses`, `sections`,
`students`, `student_terms` (census row per student-term, with
`student_level` UG/GR), `registrations` (one row per course enrollment with
grade), `awards`, `financial_aid`. `sim_meta` holds build settings.

Analytic views: `v_fall_enrollment` (UG columns + grad_* columns),
`v_fall_enrollment_demo`, `v_retention`, `v_course_success`, `v_completions`,
`v_gradrate_150`, `v_financial_aid`.

Validation views (sim vs IPEDS): `v_val_fall_enrollment`, `v_val_retention`,
`v_val_completions`, `v_val_aid`, `v_val_gradrate`. IPEDS targets are stored
in `ipeds_targets` at build time.

## Malformed data

About 0.5% of rows (configurable) are deliberately corrupted: null names,
impossible birth dates, duplicate student records, invalid grades, orphan
registrations and census rows, negative credits/disbursements, 10x
disbursements, malformed CIP codes, out-of-year award dates. Every corruption
is listed in `sim_malform_log`. Note the naive averages in `v_val_aid` are
visibly distorted by the dirty aid rows -- by design.

## How grad rates are matched

Two mechanisms work together per first-time full-time cohort: an IPEDS-rate
share of each cohort is pre-designated as eventual completers and protected
in continuing-enrollment selection (headcounts stay exact; this only decides
*who* fills the continuing slots), and award selection budgets first awards
to cohort members inside their 150% window while prioritizing designated
students in their graduating year. Completions totals stay exact; the
leftover awards flow to continuing, part-time, and returning students.

## Notes and caveats

- The most recent year's completions are estimated from the newest available
  C survey (flagged `estimated` in `ipeds_targets`).
- IPEDS GR cohorts don't exactly match EF first-time FT counts at 2-year
  schools (GR includes summer entrants and revisions); the simulation
  applies the IPEDS *rate* to its own cohorts. At 4-year schools they align
  closely.
- Age distributions (EF*B) are reported to IPEDS in odd years; even years
  carry the nearest known distribution forward.
- Student geography comes from the HD table (institution city/state/ZIP
  prefix); Haywood additionally gets a western-NC town list.
- IPEDS-calibrated financial aid is undergraduate-only (matching the SFA
  survey basis). Graduate students receive heuristic FELLOWSHIP and
  ASSISTANTSHIP aid (no IPEDS target exists): doctoral programs ~65%
  assistantships (~$20k) / ~15% fellowships (~$12k); master's ~20%
  assistantships (~$9k) / ~10% fellowships (~$5k); assistantships are
  sticky year to year. Tune `GRAD_AID` in `generate.py` to change this.
- At NC community colleges awards heavily stack (one student, several
  embedded certificates); the generator reproduces this via same-CIP
  fallback tiers in award selection.
