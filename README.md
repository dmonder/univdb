# univdb

Tools for working with IPEDS (Integrated Postsecondary Education Data System)
data, and for building realistic simulated college databases from it.

The repo has two parts:

| Part | Language | What it does |
| --- | --- | --- |
| `import_ipeds2.r` | R | Downloads the NCES IPEDS Access databases and loads them into one DuckDB file. |
| `collegesim/` | Python | Builds a simulated 10-year student information system (SIS) database for a real institution, calibrated to its IPEDS data. |

To query IPEDS data from R, see the separate
[IPEDSR](https://github.com/stanislavzza/IPEDSR) package by David Eubanks.
`collegesim` can also read the IPEDSR package's database if you have it
installed.

## Why simulated data?

Real student records are private. A simulated database gives you something
safe to teach with, test reports against, and share. It has the same shape as
a real SIS (students, courses, grades, awards, financial aid) and its totals
track the institution's actual IPEDS numbers. It also includes a small,
controllable share of malformed records, so data-cleaning work has something
to find.

No real student data is used or stored anywhere in this repo.

## Quick start

### 1. Build the IPEDS database

You need R and the Microsoft Access ODBC driver (Windows).

```r
source("import_ipeds2.r")
```

The script downloads the Access files from NCES into `data/`, then writes
`ipeds_<first year>-<last year>.duckdb`. Rerun it when NCES releases new data.

### 2. Build a simulated college

You need Python 3.10+ and three packages:

```sh
pip install duckdb numpy faker
```

Then build and check a database:

```sh
python -m collegesim build --school Haywood --start-year 2014 --end-year 2023
python -m collegesim validate --db output/sim_198668.duckdb
```

`--school` takes a name fragment or an IPEDS UNITID. Two-year and four-year
institutions both work. `validate` compares the simulation to its IPEDS
targets and reports PASS or FAIL.

See [`collegesim/README.md`](collegesim/README.md) for every option,
including ID formats, size limits, and adding a new IPEDS year to an existing
database.

## What's not in the repo

The IPEDS source files and every generated database are large and easy to
rebuild, so `.gitignore` excludes them:

- `data/` and `*.accdb` (downloaded NCES Access files)
- `*.duckdb` (the IPEDS database and simulated databases)
- `output/`

The two `IPEDS*Tablesdoc.xlsx` files are the NCES table documentation for
the 2023-24 and 2024-25 releases.

## Credits

IPEDS data comes from the National Center for Education Statistics (NCES).
