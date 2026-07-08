"""Extract per-year modeling targets for one institution from the IPEDS DuckDB.

Table conventions follow the IPEDS Access databases (and the IPEDSR package):
  HD<yyyy>        directory/characteristics
  EF<yyyy>A       fall enrollment by level/race/gender
  EF<yyyy>B       fall enrollment by age (odd years guaranteed)
  EF<yyyy>D       retention of the PRIOR fall's first-time cohort
  GR<yyyy>        graduation rates (2-yr: cohort entered fall <yyyy>-3)
  C<yyyy>_A       awards conferred July <yyyy-1> - June <yyyy>
  SFA<yy><yy+1>_P1  student financial aid for that aid year
  IC<yyyy>_AY     tuition/fees for academic year <yyyy>-<yyyy+1>
"""
import re

import duckdb

from .config import AWLEVEL_MAP, RACE_COLS, AGE_BANDS


def connect_source(path):
    return duckdb.connect(path, read_only=True)


def _tables(con):
    return {t[0].lower() for t in con.execute("SHOW TABLES").fetchall()}


def _one(con, sql, params=None):
    cur = con.execute(sql, params or [])
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def resolve_school(con, name_or_unitid):
    """Return (unitid, instnm) from the newest HD table."""
    hd = max(t for t in _tables(con) if re.match(r"^hd\d{4}$", t))
    if str(name_or_unitid).isdigit():
        row = _one(con, f"SELECT UNITID, INSTNM FROM {hd} WHERE UNITID = ?",
                   [int(name_or_unitid)])
    else:
        row = _one(con, f"SELECT UNITID, INSTNM FROM {hd} WHERE INSTNM ILIKE ?",
                   [f"%{name_or_unitid}%"])
    if row is None:
        raise ValueError(f"No institution matching {name_or_unitid!r} in {hd}")
    return row["UNITID"], row["INSTNM"]


def characteristics(con, unitid):
    hd = max(t for t in _tables(con) if re.match(r"^hd\d{4}$", t))
    return _one(con, f"SELECT UNITID, INSTNM, CITY, STABBR, ZIP, CONTROL, ICLEVEL "
                     f"FROM {hd} WHERE UNITID = ?", [unitid])


def sector_profile(hd):
    """Structural assumptions derived from ICLEVEL (1 = 4-year and above,
    2 = 2-year, 3 = less than 2-year). GR codes/window follow the IPEDS
    Graduation Rates survey definitions for each sector."""
    two_year = hd["ICLEVEL"] != 1
    if two_year:
        return {"two_year": True, "gr_window": 3,
                "gr_codes": {"cohort": 29, "comp150": 30, "comp100": 35,
                             "transfer_out": 33}}
    return {"two_year": False, "gr_window": 6,
            "gr_codes": {"cohort": 8, "comp150": 12, "comp100": 13,
                         "transfer_out": 16}}


def similar_smaller(con, hd, size_limit, n=6):
    """Smaller institutions with the same control and level, preferring the
    same state -- suggested when the requested school exceeds size_limit."""
    hd_t = max(t for t in _tables(con) if re.match(r"^hd\d{4}$", t))
    year = hd_t[2:]
    return con.execute(f"""
        SELECT h.UNITID, h.INSTNM, h.CITY, h.STABBR, e.EFTOTLT AS fall_headcount
        FROM {hd_t} h JOIN ef{year}a e ON e.UNITID = h.UNITID AND e.EFALEVEL = 1
        WHERE h.CONTROL = ? AND h.ICLEVEL = ? AND h.UNITID <> ?
          AND e.EFTOTLT BETWEEN ? AND ?
        ORDER BY (h.STABBR = ?) DESC, e.EFTOTLT DESC LIMIT ?""",
        [hd["CONTROL"], hd["ICLEVEL"], hd["UNITID"],
         int(size_limit * 0.3), size_limit, hd["STABBR"], n]).fetchall()


def available_fall_years(con, unitid):
    """Fall years with an EF A row for this institution."""
    years = []
    for t in sorted(_tables(con)):
        m = re.match(r"^ef(\d{4})a$", t)
        if m and _one(con, f"SELECT 1 AS x FROM {t} WHERE UNITID=? LIMIT 1", [unitid]):
            years.append(int(m.group(1)))
    return years


def _ef_level(con, unitid, year, level):
    return _one(con, f"SELECT * FROM ef{year}a WHERE UNITID=? AND EFALEVEL=?",
                [unitid, level])


def _race_gender(row):
    return {label: {"M": int(row[stem + "M"] or 0),
                    "W": int(row[stem + "W"] or 0)}
            for stem, label in RACE_COLS.items()}


def enrollment_targets(con, unitid, year):
    """Fall undergraduate enrollment composition from EF<year>A, plus a
    graduate block when the institution enrolls graduate students."""
    ug = _ef_level(con, unitid, year, 2) or _ef_level(con, unitid, year, 1)
    if ug is None:
        return None
    def tot(level):
        r = _ef_level(con, unitid, year, level)
        return int(r["EFTOTLT"]) if r else 0
    out = {
        "total": int(ug["EFTOTLT"]),
        "full_time": tot(22),
        "part_time": tot(42),
        "first_time": tot(4),
        "first_time_ft": tot(24),
        "transfer_in": tot(19),
        "nondegree": tot(11),
        "race_gender": _race_gender(ug),
    }
    gr = _ef_level(con, unitid, year, 12)
    if gr and gr["EFTOTLT"]:
        out["grad"] = {"total": int(gr["EFTOTLT"]),
                       "full_time": tot(32), "part_time": tot(52),
                       "race_gender": _race_gender(gr)}
    else:
        out["grad"] = None
    return out


def age_targets(con, unitid, year):
    """Age-band counts from EF<year>B (undergrad, LSTUDY=2); None if not reported."""
    if f"ef{year}b" not in _tables(con):
        return None
    rows = con.execute(
        f"SELECT EFBAGE, EFAGE09 FROM ef{year}b WHERE UNITID=? AND LSTUDY=2",
        [unitid]).fetchall()
    bands = {int(code): int(n or 0) for code, n in rows}
    if not bands:
        return None
    return {AGE_BANDS[c][0]: bands.get(c, 0) for c in AGE_BANDS}


def retention_targets(con, unitid, cohort_fall_year):
    """Retention of the fall <cohort_fall_year> first-time cohort, from EF<y+1>D."""
    t = f"ef{cohort_fall_year + 1}d"
    if t not in _tables(con):
        return None
    row = _one(con, f"SELECT RET_PCF, RET_PCP, RRFTCTA, RRPTCTA FROM {t} "
                    f"WHERE UNITID=?", [unitid])
    if row is None or row["RET_PCF"] is None:
        return None
    return {"ret_pct_ft": float(row["RET_PCF"]),
            "ret_pct_pt": (None if row["RET_PCP"] is None
                           else float(row["RET_PCP"])),
            "cohort_ft": int(row["RRFTCTA"] or 0),
            "cohort_pt": int(row["RRPTCTA"] or 0)}


def gradrate_targets(con, unitid, cohort_fall_year, profile):
    """GR outcomes for the cohort entering fall <cohort_fall_year>, read from
    GR<cohort + gr_window> using the sector's GRTYPE codes (150% of normal
    time: 3 years at 2-year institutions, 6 years at 4-year)."""
    codes = profile["gr_codes"]
    t = f"gr{cohort_fall_year + profile['gr_window']}"
    if t not in _tables(con):
        return None
    rows = con.execute(
        f"SELECT GRTYPE, GRTOTLT FROM {t} WHERE UNITID=? AND GRTYPE IN "
        f"({','.join(str(c) for c in codes.values())})", [unitid]).fetchall()
    d = {int(g): int(n or 0) for g, n in rows}
    if codes["cohort"] not in d:
        return None
    return {"cohort": d.get(codes["cohort"], 0),
            "completers_150": d.get(codes["comp150"], 0),
            "completers_100": d.get(codes["comp100"], 0),
            "transfer_out": d.get(codes["transfer_out"], 0)}


def completion_targets(con, unitid, fall_year):
    """Awards conferred during academic year <fall_year>-<fall_year+1>,
    from C<fall_year+1>_A. 6-digit CIP detail rows, first majors only.
    Returns (list, estimated_flag); falls back to the newest prior year when
    the survey year is not yet in the source database."""
    tabs = _tables(con)
    for y in range(fall_year + 1, fall_year - 3, -1):
        t = f"c{y}_a"
        if t not in tabs:
            continue
        rows = con.execute(
            f"SELECT CIPCODE, AWLEVEL, CTOTALT FROM {t} "
            f"WHERE UNITID=? AND MAJORNUM=1 AND LENGTH(CIPCODE) > 5 "
            f"AND CTOTALT > 0", [unitid]).fetchall()
        levels = {int(aw) for _c, aw, _n in rows}
        # From C2020_A on, codes 20/21 are a split of code 1 and both are
        # reported; keep code 1 so CERT_LT1YR stays continuous over time.
        skip = {20, 21} if 1 in levels and (levels & {20, 21}) else set()
        out = [{"cip": cip, "award_level": AWLEVEL_MAP[str(int(aw))], "n": int(n)}
               for cip, aw, n in rows
               if str(int(aw)) in AWLEVEL_MAP and int(aw) not in skip]
        if out:
            return out, (y != fall_year + 1)
    return [], True


def aid_targets(con, unitid, fall_year):
    """Aid-year percentages/averages from SFA<yy><yy+1>_P1."""
    yy = str(fall_year)[2:]
    yy2 = str(fall_year + 1)[2:]
    tabs = _tables(con)
    t = f"sfa{yy}{yy2}_p1"
    if t not in tabs:
        t = f"sfa{yy}{yy2}"
        if t not in tabs:
            return None
    row = _one(con, f"SELECT SCUGRAD, UAGRNTP, UAGRNTA, UPGRNTP, UPGRNTA, "
                    f"UFLOANP, UFLOANA FROM {t} WHERE UNITID=?", [unitid])
    if row is None:
        return None
    return {"undergrad_count": int(row["SCUGRAD"] or 0),
            "grant_pct": float(row["UAGRNTP"] or 0),
            "grant_avg": float(row["UAGRNTA"] or 0),
            "pell_pct": float(row["UPGRNTP"] or 0),
            "pell_avg": float(row["UPGRNTA"] or 0),
            "loan_pct": float(row["UFLOANP"] or 0),
            "loan_avg": float(row["UFLOANA"] or 0)}


def tuition_targets(con, unitid, fall_year):
    t = f"ic{fall_year}_ay"
    if t not in _tables(con):
        return None
    row = _one(con, f"SELECT TUITION2, FEE2, TUITION3, FEE3 FROM {t} "
                    f"WHERE UNITID=?", [unitid])
    if row is None:
        return None
    return {"in_state": float(row["TUITION2"] or 0) + float(row["FEE2"] or 0),
            "out_state": float(row["TUITION3"] or 0) + float(row["FEE3"] or 0)}


def _cip_lookup_from(con):
    vs_tabs = [t for t in _tables(con) if re.match(r"^valuesets\d{2}$", t)]
    if not vs_tabs:
        return {}
    rows = con.execute(f"SELECT codeValue, valueLabel FROM {max(vs_tabs)} "
                       f"WHERE varName='CIPCODE'").fetchall()
    return dict(rows)


def cip_titles(con, cips):
    """CIPCODE -> title from the newest valuesets table. Falls back to the
    IPEDSR database (which keeps the metadata tables), then generic titles."""
    import os
    lookup = _cip_lookup_from(con)
    if not lookup:
        alt = os.path.join(os.environ.get("LOCALAPPDATA", ""), "IPEDSR",
                           "database", "ipeds.duckdb")
        if os.path.exists(alt):
            alt_con = duckdb.connect(alt, read_only=True)
            lookup = _cip_lookup_from(alt_con)
            alt_con.close()
    return {c: lookup.get(c, f"Program {c}") for c in cips}


def extract_targets(con, unitid, fall_year, profile):
    """All modeling targets for one fall year / academic year."""
    enr = enrollment_targets(con, unitid, fall_year)
    if enr is None:
        raise ValueError(f"No EF{fall_year}A enrollment for UNITID {unitid}")
    completions, comp_estimated = completion_targets(con, unitid, fall_year)
    return {
        "fall_year": fall_year,
        "acad_year": f"{fall_year}-{str(fall_year + 1)[2:]}",
        "enrollment": enr,
        "ages": age_targets(con, unitid, fall_year),
        "retention": retention_targets(con, unitid, fall_year),
        "gradrates": gradrate_targets(con, unitid, fall_year, profile),
        "completions": completions,
        "completions_estimated": comp_estimated,
        "aid": aid_targets(con, unitid, fall_year),
        "tuition": tuition_targets(con, unitid, fall_year),
    }
