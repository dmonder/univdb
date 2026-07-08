"""Build a new simulated database, or supplement an existing one with a
newly released IPEDS year."""
import os

import duckdb

from . import ipeds, malform
from .generate import Simulator

_PKG = os.path.dirname(__file__)


def _run_sql_file(con, name):
    with open(os.path.join(_PKG, name), encoding="utf-8") as f:
        con.execute(f.read())


def _open_out(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    return duckdb.connect(path)


def _check_size(src, hd, targets, cfg):
    peak = max(t["enrollment"]["total"]
               + (t["enrollment"]["grad"]["total"]
                  if t["enrollment"].get("grad") else 0)
               for t in targets)
    if peak <= cfg.size_limit or cfg.force:
        return
    print(f"\n{hd['INSTNM']} peaks at {peak:,} fall headcount, above the "
          f"{cfg.size_limit:,} limit for this generator.")
    print("Simulation at that scale is slow and memory-hungry; consider "
          "modeling a smaller school with the same structure, e.g.:\n")
    for uid, nm, city, st, n in ipeds.similar_smaller(src, hd,
                                                      cfg.size_limit):
        print(f"  {uid}  {nm} ({city}, {st}) - {int(n):,} students")
    print("\nOr rerun with --force (and patience) to proceed anyway, "
          "or raise --size-limit.")
    raise SystemExit(1)


def build(cfg):
    """Create cfg.out_db and simulate cfg.start_year..cfg.end_year."""
    src = ipeds.connect_source(cfg.source_db)
    unitid, name = ipeds.resolve_school(src, cfg.unitid or cfg.school_name)
    cfg.unitid, cfg.school_name = unitid, name
    if not cfg.out_db:
        cfg.out_db = cfg.default_out_db()
    print(f"Modeling {name} (UNITID {unitid}), "
          f"fall {cfg.start_year}-{cfg.end_year}")
    years = ipeds.available_fall_years(src, unitid)
    missing = [y for y in range(cfg.start_year, cfg.end_year + 1)
               if y not in years]
    if missing:
        raise ValueError(f"Source DB lacks EF data for years: {missing}")

    hd = ipeds.characteristics(src, unitid)
    profile = ipeds.sector_profile(hd)
    cfg.school_city = hd["CITY"] or ""
    cfg.school_state = hd["STABBR"] or ""
    cfg.school_zip = str(hd["ZIP"] or "")
    targets = [ipeds.extract_targets(src, unitid, y, profile)
               for y in range(cfg.start_year, cfg.end_year + 1)]
    _check_size(src, hd, targets, cfg)
    cips = {c["cip"] for t in targets for c in t["completions"]}
    titles = ipeds.cip_titles(src, cips)
    src.close()

    if os.path.exists(cfg.out_db):
        os.remove(cfg.out_db)
    con = _open_out(cfg.out_db)
    _run_sql_file(con, "schema.sql")
    con.execute("INSERT INTO institution VALUES (?,?,?,?,?)",
                [hd["UNITID"], hd["INSTNM"], hd["CITY"], hd["STABBR"],
                 hd["ZIP"]])
    meta = {"gr_window": profile["gr_window"],
            "two_year": int(profile["two_year"]),
            "program_id_format": cfg.program_id_format,
            "course_id_format": cfg.course_id_format,
            "section_id_format": cfg.section_id_format}
    con.executemany("INSERT OR REPLACE INTO sim_meta VALUES (?,?)",
                    [(k, str(v)) for k, v in meta.items()])

    sim = Simulator(con, cfg, titles, profile)
    sim.build_catalog(targets)
    sim.flush_catalog()
    prev_retention = None
    for t in targets:
        est = " (completions estimated)" if t["completions_estimated"] else ""
        print(f"  simulating {t['acad_year']}: "
              f"{t['enrollment']['total']} fall headcount{est}")
        sim.run_year(t, prev_retention)
        prev_retention = t["retention"]

    n_bad = malform.inject(con, cfg.malform_rate, cfg.seed)
    print(f"  injected {n_bad} malformed rows (rate {cfg.malform_rate})")
    _run_sql_file(con, "views.sql")
    con.close()
    print(f"Done: {cfg.out_db}")


def _migrate(con):
    """Bring databases built before the sector-profile work up to date."""
    con.execute("ALTER TABLE student_terms ADD COLUMN IF NOT EXISTS "
                "student_level VARCHAR")
    con.execute("CREATE TABLE IF NOT EXISTS sim_meta "
                "(key VARCHAR PRIMARY KEY, value VARCHAR)")


def supplement(db_path, source_db, fall_year, seed=None, malform_rate=None):
    """Append one newly available academic year to an existing database."""
    from .config import SimConfig

    con = duckdb.connect(db_path)
    _migrate(con)
    unitid, name = con.execute(
        "SELECT unitid, name FROM institution").fetchone()
    have = con.execute(
        "SELECT COALESCE(MAX(fall_year), 0) FROM terms").fetchone()[0]
    if fall_year <= have:
        con.close()
        raise ValueError(f"{db_path} already contains fall {fall_year} "
                         f"(latest is {have})")
    if fall_year != have + 1:
        con.close()
        raise ValueError(f"Next year to add is {have + 1}, not {fall_year}")
    meta = dict(con.execute("SELECT key, value FROM sim_meta").fetchall())

    src = ipeds.connect_source(source_db)
    hd = ipeds.characteristics(src, unitid)
    profile = ipeds.sector_profile(hd)
    targets = ipeds.extract_targets(src, unitid, fall_year, profile)
    prev_retention = ipeds.retention_targets(src, unitid, fall_year - 1)
    titles = ipeds.cip_titles(src, {c["cip"] for c in targets["completions"]})
    grad_rates = {}
    for cy in range(fall_year - profile["gr_window"] - 2, fall_year + 1):
        gr = ipeds.gradrate_targets(src, unitid, cy, profile)
        if gr and gr["cohort"]:
            grad_rates[cy] = gr["completers_150"] / gr["cohort"]
    src.close()

    cfg = SimConfig(unitid=unitid, school_name=name, out_db=db_path,
                    school_city=hd["CITY"] or "",
                    school_state=hd["STABBR"] or "",
                    school_zip=str(hd["ZIP"] or ""))
    # reuse the ID formats the database was built with
    cfg.program_id_format = meta.get("program_id_format",
                                     cfg.program_id_format)
    cfg.course_id_format = meta.get("course_id_format", cfg.course_id_format)
    cfg.section_id_format = meta.get("section_id_format",
                                     cfg.section_id_format)
    if seed is not None:
        cfg.seed = seed
    if malform_rate is not None:
        cfg.malform_rate = malform_rate
    cfg.seed += fall_year  # different draws than the original build

    print(f"Supplementing {name} with {targets['acad_year']} "
          f"({targets['enrollment']['total']} fall headcount)")
    sim = Simulator(con, cfg, titles, profile)
    sim.load_state()
    sim.grad_rates = grad_rates
    sim.designate_from_state(fall_year)
    sim.build_catalog([targets])   # add programs for any new CIPs
    sim.flush_catalog()
    sim.last_ages = None
    con.executemany("INSERT OR REPLACE INTO sim_meta VALUES (?,?)",
                    [("gr_window", str(profile["gr_window"])),
                     ("two_year", str(int(profile["two_year"]))),
                     ("program_id_format", cfg.program_id_format),
                     ("course_id_format", cfg.course_id_format),
                     ("section_id_format", cfg.section_id_format)])
    sim.run_year(targets, prev_retention)

    n_bad = malform.inject(con, cfg.malform_rate * 0.1, cfg.seed)
    print(f"  injected {n_bad} malformed rows")
    _run_sql_file(con, "views.sql")
    con.close()
    print(f"Done: {db_path}")
