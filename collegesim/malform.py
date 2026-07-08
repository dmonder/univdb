"""Inject a small, configurable share of realistic data-quality problems.

Every corruption is recorded in sim_malform_log so tests/graders can find
them; analysts working the database "blind" simply encounter dirty data.
"""
import numpy as np


def _log(con, rows):
    if rows:
        con.executemany("INSERT INTO sim_malform_log VALUES (?,?,?)", rows)


def _sample_keys(con, rng, sql, rate):
    keys = [r[0] for r in con.execute(sql).fetchall()]
    n = int(round(len(keys) * rate))
    if n == 0 or not keys:
        return []
    return [str(k) for k in rng.choice(keys, size=min(n, len(keys)),
                                       replace=False)]


def inject(con, rate, seed):
    rng = np.random.default_rng(seed + 1)
    log = []

    # students: missing last names, impossible birth dates
    for sid in _sample_keys(con, rng,
                            "SELECT student_id FROM students", rate * 0.4):
        con.execute("UPDATE students SET last_name = NULL WHERE student_id=?",
                    [sid])
        log.append(("students", sid, "null_last_name"))
    for sid in _sample_keys(con, rng,
                            "SELECT student_id FROM students", rate * 0.2):
        bad = "1900-01-01" if rng.random() < 0.5 else "2091-06-15"
        con.execute("UPDATE students SET dob = ? WHERE student_id=?",
                    [bad, sid])
        log.append(("students", sid, f"impossible_dob:{bad}"))
    # duplicate student records with name-case variants and a new id
    for sid in _sample_keys(con, rng,
                            "SELECT student_id FROM students", rate * 0.3):
        dup = f"X{sid[1:]}"
        con.execute("""
            INSERT INTO students
            SELECT ?, UPPER(first_name), UPPER(last_name), dob, gender,
                   race_ethnicity, city, state, zip, first_term, entry_type,
                   entry_credits
            FROM students WHERE student_id = ?""", [dup, sid])
        log.append(("students", dup, f"duplicate_of:{sid}"))

    # registrations: invalid grades, orphan student ids, negative credits
    for rid in _sample_keys(con, rng,
                            "SELECT reg_id FROM registrations", rate * 0.5):
        bad = str(rng.choice(["ZZ", "", "A+", "P*"]))
        con.execute("UPDATE registrations SET grade=?, grade_points=NULL "
                    "WHERE reg_id=?", [bad, int(rid)])
        log.append(("registrations", rid, f"invalid_grade:{bad}"))
    for rid in _sample_keys(con, rng,
                            "SELECT reg_id FROM registrations", rate * 0.2):
        con.execute("UPDATE registrations SET student_id='S9999999' "
                    "WHERE reg_id=?", [int(rid)])
        log.append(("registrations", rid, "orphan_student_id"))
    for rid in _sample_keys(con, rng,
                            "SELECT reg_id FROM registrations", rate * 0.1):
        con.execute("UPDATE registrations SET credits=-3 WHERE reg_id=?",
                    [int(rid)])
        log.append(("registrations", rid, "negative_credits"))

    # awards: malformed CIP codes, award dates outside the academic year
    for aid in _sample_keys(con, rng,
                            "SELECT award_id FROM awards", rate * 0.5):
        con.execute("""
            UPDATE awards SET cip_code = SUBSTR(REPLACE(cip_code, '.', ''),
                                                1, 5)
            WHERE award_id=?""", [int(aid)])
        log.append(("awards", aid, "malformed_cip"))
    for aid in _sample_keys(con, rng,
                            "SELECT award_id FROM awards", rate * 0.3):
        con.execute("UPDATE awards SET award_date = award_date + INTERVAL "
                    "18 MONTH WHERE award_id=?", [int(aid)])
        log.append(("awards", aid, "award_date_outside_year"))

    # financial aid: negative amounts, disbursed >> offered
    for fid in _sample_keys(con, rng,
                            "SELECT aid_id FROM financial_aid", rate * 0.4):
        con.execute("UPDATE financial_aid SET amount_disbursed = "
                    "-amount_disbursed WHERE aid_id=?", [int(fid)])
        log.append(("financial_aid", fid, "negative_disbursement"))
    for fid in _sample_keys(con, rng,
                            "SELECT aid_id FROM financial_aid", rate * 0.2):
        con.execute("UPDATE financial_aid SET amount_disbursed = "
                    "amount_offered * 10 WHERE aid_id=?", [int(fid)])
        log.append(("financial_aid", fid, "disbursed_10x_offered"))

    # student_terms: a few orphan census rows (very low rate; these touch
    # the enrollment counts, so keep them rare)
    n_st = con.execute("SELECT COUNT(*) FROM student_terms").fetchone()[0]
    k = max(1, int(rate * 0.05 * n_st))
    orphans = con.execute(
        f"SELECT student_id, term_id FROM student_terms "
        f"USING SAMPLE {k} ROWS (reservoir, {seed})").fetchall()
    for sid, tid in orphans:
        con.execute("""
            INSERT INTO student_terms
            SELECT 'S0000000', term_id, program_id, degree_seeking,
                   enrollment_status, 'CONTINUING', credits_attempted,
                   credits_earned, term_gpa, student_level
            FROM student_terms WHERE student_id=? AND term_id=? LIMIT 1""",
            [sid, tid])
        log.append(("student_terms", f"S0000000|{tid}", "orphan_census_row"))

    _log(con, log)
    return len(log)
