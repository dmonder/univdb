"""Compare the simulated database against its stored IPEDS targets."""
import duckdb


def _section(con, title, sql):
    print(f"\n== {title} ==")
    rows = con.execute(sql).fetchall()
    cols = [d[0] for d in con.description]
    widths = [max(len(c), *(len(str(r[i])) for r in rows)) if rows else len(c)
              for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    return rows


def validate(db_path, tolerance_pct=2.0):
    con = duckdb.connect(db_path, read_only=True)
    name = con.execute("SELECT name FROM institution").fetchone()[0]
    print(f"Validation report: {name} ({db_path})")

    failures = 0
    rows = _section(con, "Fall enrollment (sim vs IPEDS)", """
        SELECT fall_year, metric, ipeds_value, sim_value,
               sim_value - ipeds_value AS delta
        FROM v_val_fall_enrollment ORDER BY fall_year, metric""")
    for _y, _m, tgt, sim, _d in rows:
        if tgt and abs(sim - tgt) > max(2, tolerance_pct / 100 * tgt):
            failures += 1

    rows = _section(con, "Fall-to-fall retention pct (sim vs IPEDS)", """
        SELECT cohort_year, status, ipeds_pct, sim_pct,
               ROUND(sim_pct - ipeds_pct, 1) AS delta
        FROM v_val_retention ORDER BY 1, 2""")
    for _y, _s, tgt, sim, _d in rows:
        if tgt and sim is not None and abs(sim - tgt) > 3:
            failures += 1

    rows = _section(con, "Completions by award level (sim vs IPEDS)", """
        SELECT fall_year, award_level, ipeds_n, sim_n,
               sim_n - ipeds_n AS delta, estimated
        FROM v_val_completions ORDER BY 1, 2""")
    for _y, _l, tgt, sim, _d, est in rows:
        if not est and tgt and abs(sim - tgt) > max(2, 0.05 * tgt):
            failures += 1

    _section(con, "Pell aid (sim vs IPEDS)", """
        SELECT fall_year, metric, ipeds_value, sim_value
        FROM v_val_aid ORDER BY 1, 2""")

    _section(con, "Graduation rate 150% (informational; emergent, not fitted)",
             """
        SELECT cohort_year, ipeds_cohort, sim_cohort, ipeds_rate, sim_rate
        FROM v_val_gradrate
        WHERE sim_cohort IS NOT NULL
          AND cohort_year + COALESCE((SELECT CAST(value AS INT) FROM sim_meta
                                      WHERE key = 'gr_window'), 3)
              <= (SELECT MAX(fall_year) FROM terms) + 1
        ORDER BY 1""")

    _section(con, "Course success (sim only; no IPEDS equivalent)", """
        SELECT t.fall_year,
               SUM(cs.enrollments) AS enrollments,
               ROUND(SUM(cs.success_pct * cs.enrollments)
                     / SUM(cs.enrollments), 1) AS success_pct
        FROM v_course_success cs JOIN terms t ON t.term_id = cs.term_id
        GROUP BY 1 ORDER BY 1""")

    n_mal = con.execute(
        "SELECT COUNT(*) FROM sim_malform_log").fetchone()[0]
    print(f"\nMalformed rows logged: {n_mal} (see sim_malform_log)")
    print(f"\n{'PASS' if failures == 0 else 'CHECK'}: "
          f"{failures} calibrated metric(s) outside tolerance")
    con.close()
    return failures
