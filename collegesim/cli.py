"""Command-line interface.

  python -m collegesim build --school "Haywood" --start-year 2014 --end-year 2023
  python -m collegesim supplement --db output/sim_198668.duckdb --year 2024
  python -m collegesim validate --db output/sim_198668.duckdb
"""
import argparse

from .config import SimConfig, DEFAULT_SOURCE_DB


def main(argv=None):
    p = argparse.ArgumentParser(prog="collegesim",
                                description="Simulated college SIS database "
                                            "modeled on IPEDS data")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a new simulated database")
    b.add_argument("--school", required=True,
                   help="institution name fragment or UNITID")
    b.add_argument("--start-year", type=int, default=2014,
                   help="first fall term (default 2014)")
    b.add_argument("--end-year", type=int, default=2023,
                   help="last fall term (default 2023)")
    b.add_argument("--source", default=DEFAULT_SOURCE_DB,
                   help="path to the IPEDS DuckDB")
    b.add_argument("--out", default="", help="output DuckDB path")
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--malform-rate", type=float, default=0.005,
                   help="share of rows corrupted (default 0.005)")
    b.add_argument("--size-limit", type=int, default=10000,
                   help="refuse schools above this fall headcount and "
                        "suggest smaller, similar ones (default 10000)")
    b.add_argument("--force", action="store_true",
                   help="build even above the size limit")
    b.add_argument("--program-id-format", default="{prefix}{cip}",
                   help="template with {prefix} {cip} {cip_dotted} "
                        "(default '{prefix}{cip}')")
    b.add_argument("--course-id-format", default="{subject}-{number}",
                   help="template with {subject} {number} "
                        "(default '{subject}-{number}')")
    b.add_argument("--section-id-format", default="{term}-{course}-{section}",
                   help="template with {term} {course} {section} "
                        "(default '{term}-{course}-{section}')")
    b.add_argument("--subject-length", type=int, choices=(3, 4), default=3,
                   help="subject prefix length: 3 (NUR) or 4 (NURS)")
    b.add_argument("--course-number-digits", type=int, choices=(3, 4),
                   default=3, help="course number digits: 3 (110) or 4 (1100)")
    b.add_argument("--section-style", choices=("01", "001", "1", "A"),
                   default="01",
                   help="section labels: 01 (default), 001, 1, or A/B/C")
    b.add_argument("--towns", default="",
                   help="comma-separated student home towns "
                        "(default: institution city)")

    s = sub.add_parser("supplement",
                       help="append a newly released IPEDS year")
    s.add_argument("--db", required=True, help="existing simulated DuckDB")
    s.add_argument("--year", type=int, required=True,
                   help="fall year to add (must be latest+1)")
    s.add_argument("--source", default=DEFAULT_SOURCE_DB)
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--malform-rate", type=float, default=None)

    v = sub.add_parser("validate", help="compare sim vs IPEDS targets")
    v.add_argument("--db", required=True)
    v.add_argument("--tolerance", type=float, default=2.0,
                   help="tolerance in pct for calibrated metrics")

    args = p.parse_args(argv)
    if args.cmd == "build":
        from .builder import build
        unitid = int(args.school) if args.school.isdigit() else 0
        cfg = SimConfig(unitid=unitid, school_name=args.school,
                        start_year=args.start_year, end_year=args.end_year,
                        source_db=args.source, out_db=args.out,
                        seed=args.seed, malform_rate=args.malform_rate,
                        size_limit=args.size_limit, force=args.force,
                        program_id_format=args.program_id_format,
                        course_id_format=args.course_id_format,
                        section_id_format=args.section_id_format,
                        subject_length=args.subject_length,
                        course_number_digits=args.course_number_digits,
                        section_style=args.section_style,
                        towns=[t.strip() for t in args.towns.split(",")
                               if t.strip()])
        build(cfg)
    elif args.cmd == "supplement":
        from .builder import supplement
        supplement(args.db, args.source, args.year, args.seed,
                   args.malform_rate)
    elif args.cmd == "validate":
        from .validate import validate
        raise SystemExit(1 if validate(args.db, args.tolerance) else 0)


if __name__ == "__main__":
    main()
