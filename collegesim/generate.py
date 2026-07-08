"""Cohort-based simulation engine.

Generates SIS-style records (students, terms, sections, registrations, awards,
financial aid) calibrated so that fall census headcounts, FT/PT splits,
first-time / transfer-in / non-degree counts, race x gender mix, fall-to-fall
retention, completions by CIP x award level, and Pell percentages/averages
roll up to the institution's actual IPEDS figures. Grad rates and course
success are emergent and reported side-by-side with IPEDS for reference.
"""
import datetime as dt
import math

import numpy as np
from faker import Faker

from .config import AGE_BANDS, GRAD_LEVELS

GENED = [  # (subject, number, title, credits) common lower-division courses
    ("ACA", "122", "College Success", 1),
    ("ENG", "111", "Writing and Inquiry", 3),
    ("ENG", "112", "Writing/Research in the Disciplines", 3),
    ("MAT", "143", "Quantitative Literacy", 3),
    ("MAT", "152", "Statistical Methods I", 4),
    ("MAT", "171", "Precalculus Algebra", 4),
    ("PSY", "150", "General Psychology", 3),
    ("SOC", "210", "Introduction to Sociology", 3),
    ("HIS", "131", "American History I", 3),
    ("BIO", "110", "Principles of Biology", 4),
    ("BIO", "111", "General Biology I", 4),
    ("CHM", "151", "General Chemistry I", 4),
    ("COM", "120", "Interpersonal Communication", 3),
    ("ART", "111", "Art Appreciation", 3),
    ("MUS", "110", "Music Appreciation", 3),
    ("HUM", "115", "Critical Thinking", 3),
    ("CIS", "110", "Introduction to Computers", 3),
    ("PED", "110", "Fit and Well for Life", 2),
    ("ECO", "251", "Prin of Microeconomics", 3),
    ("POL", "120", "American Government", 3),
]
SUBJECT_BY_CIP4 = {"51.38": "NUR", "51.06": "DEN", "51.07": "HMT",
                   "51.09": "RCP", "51.00": "MED", "51.08": "MED"}
SUBJECT_BY_CIP2 = {
    "01": "AGR", "03": "FOR", "10": "DME", "11": "CIS", "12": "COS",
    "13": "EDU", "15": "EGR", "19": "EDU", "22": "LEX", "24": "ACA",
    "26": "BIO", "30": "SCI", "43": "CJC", "44": "HSE", "45": "SOC",
    "46": "CST", "47": "AUT", "48": "WLD", "49": "TRN", "50": "ART",
    "51": "HEA", "52": "BUS",
}
LEVEL_PREFIX = {"ASSOCIATE": "A", "CERT_1TO2YR": "D", "BACHELORS": "B",
                "MASTERS": "M", "POSTBAC_CERT": "PB", "POSTMA_CERT": "PM",
                "DOCTORAL_RS": "P", "DOCTORAL_PP": "P",
                "DOCTORAL_OTH": "P"}  # others -> C
REQ_CREDITS = {"ASSOCIATE": 64, "CERT_1TO2YR": 42, "CERT_2TO4YR": 50,
               "CERT_LT1YR": 16, "CERT_12WKTO1YR": 14, "CERT_LT12WK": 8,
               "BACHELORS": 120, "MASTERS": 36, "POSTBAC_CERT": 18,
               "POSTMA_CERT": 18, "DOCTORAL_RS": 72, "DOCTORAL_PP": 90,
               "DOCTORAL_OTH": 72}
# awardees' probability of leaving after each credential
LEAVE_P = {"ASSOCIATE": 0.80, "BACHELORS": 0.85, "MASTERS": 0.90,
           "POSTBAC_CERT": 0.50, "POSTMA_CERT": 0.50, "DOCTORAL_RS": 0.95,
           "DOCTORAL_PP": 0.95, "DOCTORAL_OTH": 0.95}
# graduate aid heuristics (no IPEDS target exists; SFA covers undergrads
# only): aid_type -> (share of students, mean amount, sd) by program group
GRAD_AID = {
    "DOCTORAL": {"ASSISTANTSHIP": (0.65, 20000, 4000),
                 "FELLOWSHIP": (0.15, 12000, 4000)},
    "MASTERS": {"ASSISTANTSHIP": (0.20, 9000, 3000),
                "FELLOWSHIP": (0.10, 5000, 2000)},
}
GRADE_POINTS = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}
WNC_TOWNS = ["Waynesville", "Canton", "Clyde", "Maggie Valley",
             "Lake Junaluska", "Sylva", "Asheville", "Candler", "Bethel",
             "Cullowhee", "Bryson City", "Hazelwood", "Fines Creek",
             "Cruso", "Balsam", "Leicester"]
COURSE_NUMS = [110, 111, 112, 113, 114, 115, 120, 125, 130, 140, 150,
               211, 212, 213, 214, 215, 220, 225, 230, 240, 250]
UG4_NUMS = [310, 315, 320, 330, 340, 350, 360, 410, 415, 420, 430, 440, 450]
GR_NUMS = [510, 520, 530, 540, 550, 560, 570, 610, 620, 630, 640, 650,
           700, 710, 720, 800]


def _largest_remainder(weights, n):
    """Integer allocation of n across weights, preserving sum."""
    w = np.asarray(weights, dtype=float)
    if w.sum() <= 0:
        w = np.ones_like(w)
    raw = w / w.sum() * n
    base = np.floor(raw).astype(int)
    rem = n - base.sum()
    if rem > 0:
        order = np.argsort(-(raw - base))
        base[order[:rem]] += 1
    return base


class Simulator:
    def __init__(self, con, cfg, cip_title_lookup, profile):
        self.con = con
        self.cfg = cfg
        self.profile = profile
        self.rng = np.random.default_rng(cfg.seed)
        self.fake = Faker("en_US")
        Faker.seed(cfg.seed)
        self.cip_titles = cip_title_lookup
        self.towns = (WNC_TOWNS if cfg.unitid == 198668
                      else [cfg.school_city or "Springfield"])
        self.zip_prefix = (cfg.school_zip or "28721")[:3]
        self.students = {}          # sid -> dict (full population state)
        self.programs = {}          # program_id -> dict
        self.courses = {}           # course_id -> dict
        self.prog_courses = {}      # program_id -> [course_id]
        self.instructors = {}       # subject -> [names]
        self.cohorts = {}           # fall_year -> {"FT": [sid], "PT": [sid]}
        self.grad_rates = {}        # cohort fall_year -> IPEDS 150% grad rate
        self.last_ages = None       # carry-forward age distribution
        self._sid = 0
        self._reg = 0
        self._award = 0
        self._aid = 0
        # per-run row buffers, flushed to the DB after each academic year
        self._buf = {t: [] for t in
                     ("students", "terms", "sections", "student_terms",
                      "registrations", "awards", "financial_aid",
                      "ipeds_targets")}

    # ---------------- catalog ----------------

    def _program_id(self, prefix, cip):
        return self.cfg.program_id_format.format(
            prefix=prefix, cip=cip.replace(".", ""), cip_dotted=cip)

    def _course_id(self, subject, number):
        return self.cfg.course_id_format.format(subject=subject,
                                                number=number)

    def _section_id(self, term_id, course_id, n):
        return self.cfg.section_id_format.format(
            term=term_id, course=course_id, section=f"{n:02d}")

    def _course_pool(self, level):
        """(candidate numbers, courses per program) by award level/sector."""
        if level in GRAD_LEVELS:
            n = 10 if level.startswith("DOCTORAL") else 8
            return GR_NUMS, n
        if level == "BACHELORS":
            return COURSE_NUMS + UG4_NUMS, 16
        if level == "ASSOCIATE":
            return COURSE_NUMS, 14
        if level in ("CERT_1TO2YR", "CERT_2TO4YR"):
            return COURSE_NUMS, 10
        return COURSE_NUMS, 6

    def build_catalog(self, targets_by_year):
        """Programs from the union of completion CIPs across all years."""
        seen = {}
        for t in targets_by_year:
            for c in t["completions"]:
                key = (c["cip"], c["award_level"])
                seen[key] = seen.get(key, 0) + c["n"]
        existing = {(p["cip_code"], p["award_level"])
                    for p in self.programs.values()}
        for (cip, level), _n in sorted(seen.items()):
            if (cip, level) in existing:
                continue
            pid = self._program_id(LEVEL_PREFIX.get(level, "C"), cip)
            while pid in self.programs:
                pid += "X"
            title = self.cip_titles.get(cip, f"Program {cip}")
            self.programs[pid] = {
                "program_id": pid, "cip_code": cip, "title": title,
                "award_level": level,
                "required_credits": REQ_CREDITS.get(level, 40),
            }
        # gen-ed courses
        self.gened_ids = []
        for subj, num, title, cr in GENED:
            cid = self._course_id(subj, num)
            self.courses[cid] = {"course_id": cid, "subject": subj,
                                 "course_number": num, "title": title,
                                 "credits": cr,
                                 "department": "General Education"}
            self.gened_ids.append(cid)
        # program courses (deterministic per CIP so supplement runs align)
        for pid, p in self.programs.items():
            cip = p["cip_code"]
            level = p["award_level"]
            subj = SUBJECT_BY_CIP4.get(cip[:5],
                   SUBJECT_BY_CIP2.get(cip[:2], "GEN"))
            prng = np.random.default_rng(int(cip.replace(".", "")))
            pool, n_courses = self._course_pool(level)
            nums = prng.choice(pool, size=min(n_courses, len(pool)),
                               replace=False)
            ids = []
            for num in sorted(nums):
                cid = self._course_id(subj, num)
                if cid not in self.courses:
                    cr = 4 if (subj in ("NUR", "BIO", "CHM", "EGR")
                               and prng.random() < 0.5) else 3
                    self.courses[cid] = {
                        "course_id": cid, "subject": subj,
                        "course_number": str(num),
                        "title": f"{p['title'][:38]} {num}", "credits": cr,
                        "department": p["title"][:38]}
                ids.append(cid)
            if level not in GRAD_LEVELS:
                ids = ids + self.gened_ids
            self.prog_courses[pid] = ids
        # completions-weighted program sampling for new students, by level
        self._prog_ug, self._prog_gr = [], []
        w_ug, w_gr = [], []
        for pid, p in self.programs.items():
            w = max(1.0, seen.get((p["cip_code"], p["award_level"]), 1))
            if p["award_level"] in GRAD_LEVELS:
                self._prog_gr.append(pid)
                w_gr.append(w)
            else:
                self._prog_ug.append(pid)
                w_ug.append(w)
        self._w_ug = (np.array(w_ug) / sum(w_ug)) if w_ug else None
        self._w_gr = (np.array(w_gr) / sum(w_gr)) if w_gr else None

    def flush_catalog(self):
        rows = [tuple(p.values()) for p in self.programs.values()]
        self.con.executemany(
            "INSERT OR REPLACE INTO programs VALUES (?,?,?,?,?)", rows)
        rows = [tuple(c.values()) for c in self.courses.values()]
        self.con.executemany(
            "INSERT OR REPLACE INTO courses VALUES (?,?,?,?,?,?)", rows)

    # ---------------- people ----------------

    def _new_student(self, fall_year, race, gender, age, entry_type,
                     first_term, entry_credits=0, level="UG"):
        self._sid += 1
        sid = f"S{self._sid:07d}"
        first = (self.fake.first_name_male() if gender == "M"
                 else self.fake.first_name_female())
        dob = dt.date(fall_year - age,
                      int(self.rng.integers(1, 13)),
                      int(self.rng.integers(1, 28)))
        p_local = 0.9 if len(self.towns) > 1 else 0.55
        stu = {
            "student_id": sid, "first_name": first,
            "last_name": self.fake.last_name(), "dob": dob,
            "gender": gender, "race_ethnicity": race,
            "city": (str(self.rng.choice(self.towns))
                     if self.rng.random() < p_local else self.fake.city()),
            "state": self.cfg.school_state or "NC",
            "zip": f"{self.zip_prefix}{self.rng.integers(10, 99)}",
            "first_term": first_term, "entry_type": entry_type,
            "entry_credits": entry_credits,
            # simulation-only state (not persisted as columns)
            "_ability": float(np.clip(self.rng.beta(5, 2.2), 0.15, 0.98)),
            "_credits": float(entry_credits), "_program": None,
            "_degree_seeking": entry_type != "NONDEGREE",
            "_ft": False, "_last_fall": None, "_awards": set(),
            "_left": False, "_terms_enrolled": 0, "_level": level,
            "_destined": False, "_cohort_year": None,
        }
        if stu["_degree_seeking"]:
            pids = self._prog_gr if level == "GR" else self._prog_ug
            wts = self._w_gr if level == "GR" else self._w_ug
            if pids:
                stu["_program"] = str(self.rng.choice(pids, p=wts))
        self.students[sid] = stu
        self._buf["students"].append(tuple(
            stu[k] for k in ("student_id", "first_name", "last_name", "dob",
                             "gender", "race_ethnicity", "city", "state",
                             "zip", "first_term", "entry_type",
                             "entry_credits")))
        return sid

    def _sample_demo_cells(self, deficit, n):
        """Allocate n new students across (race, gender) cells."""
        cells = list(deficit.keys())
        counts = _largest_remainder([max(0, deficit[c]) for c in cells], n)
        out = []
        for cell, k in zip(cells, counts):
            out.extend([cell] * int(k))
        self.rng.shuffle(out)
        return out

    def _sample_age(self, ages, young_bias):
        labels = list(ages.keys())
        w = np.array([max(0.001, ages[b]) for b in labels], dtype=float)
        if young_bias:
            for i, b in enumerate(labels):
                if AGE_BANDS_BY_LABEL[b][1] < 25:
                    w[i] *= 3.0
        band = labels[int(self.rng.choice(len(labels), p=w / w.sum()))]
        lo, hi = AGE_BANDS_BY_LABEL[band]
        return int(self.rng.integers(lo, hi + 1))

    # ---------------- terms ----------------

    def _term_rows(self, fall_year):
        ay = f"{fall_year}-{str(fall_year + 1)[2:]}"
        return [
            (f"{fall_year}FA", ay, fall_year, "FA",
             dt.date(fall_year, 8, 15), dt.date(fall_year, 12, 15),
             dt.date(fall_year, 9, 10)),
            (f"{fall_year + 1}SP", ay, fall_year, "SP",
             dt.date(fall_year + 1, 1, 8), dt.date(fall_year + 1, 5, 10),
             dt.date(fall_year + 1, 2, 1)),
            (f"{fall_year + 1}SU", ay, fall_year, "SU",
             dt.date(fall_year + 1, 5, 20), dt.date(fall_year + 1, 7, 28),
             dt.date(fall_year + 1, 6, 5)),
        ]

    # ---------------- one academic year ----------------

    def run_year(self, targets, prev_retention):
        fy = targets["fall_year"]
        enr = targets["enrollment"]
        ages = targets["ages"] or self.last_ages or DEFAULT_AGES
        self.last_ages = ages
        gr = targets["gradrates"]
        if gr and gr["cohort"]:
            self.grad_rates[fy] = gr["completers_150"] / gr["cohort"]
        self._buf["terms"].extend(self._term_rows(fy))

        # --- assemble the fall census population ---
        n_first, n_first_ft = enr["first_time"], enr["first_time_ft"]
        n_transfer, n_nondeg = enr["transfer_in"], enr["nondegree"]
        n_cont = enr["total"] - n_first - n_transfer - n_nondeg

        chosen = []  # (sid, student_type)
        # retention-enforced returners from last fall's first-time cohort
        prior = self.cohorts.get(fy - 1)
        if prior and prev_retention:
            for status, key in (("FT", "ret_pct_ft"), ("PT", "ret_pct_pt")):
                rate = prev_retention[key]
                if rate is None or not prior[status]:
                    continue
                members = [s for s in prior[status] if s in self.students]
                active = [s for s in members
                          if not self.students[s]["_left"]]
                left = [s for s in members if self.students[s]["_left"]]
                k = int(round(rate / 100 * len(prior[status])))
                # destined completers (see _designate) survive year 1 -> 2
                destined = [s for s in active
                            if self.students[s]["_destined"]]
                rest = [s for s in active
                        if not self.students[s]["_destined"]]
                picks = destined[:k]
                if len(picks) < k and rest:
                    picks += list(self.rng.choice(
                        rest, size=min(k - len(picks), len(rest)),
                        replace=False))
                if len(picks) < k and left:  # early completers who return
                    extra = self.rng.choice(left,
                                            size=min(k - len(picks), len(left)),
                                            replace=False)
                    for sid in extra:
                        self.students[str(sid)]["_left"] = False
                    picks += list(extra)
                chosen.extend((str(sid), "CONTINUING") for sid in picks)
        chosen_ids = {c[0] for c in chosen}
        # other continuing students
        pool = [s for s, v in self.students.items()
                if v["_degree_seeking"] and not v["_left"]
                and v["_level"] == "UG"
                and s not in chosen_ids
                and (prior is None or (s not in prior["FT"] and s not in prior["PT"]))
                and v["_last_fall"] is not None and v["_last_fall"] >= fy - 2]
        need = n_cont - len(chosen)
        if need > 0 and pool:
            def _pw(s):
                v = self.students[s]
                if (v["_destined"] and not v["_awards"]
                        and v["_cohort_year"] is not None
                        and fy - v["_cohort_year"]
                        < self.profile["gr_window"]):
                    return 50.0  # keep destined completers enrolled
                return 2.0 if v["_last_fall"] == fy - 1 else 0.5
            w = np.array([_pw(s) for s in pool])
            k = min(need, len(pool))
            picks = self.rng.choice(pool, size=k, replace=False,
                                    p=w / w.sum())
            chosen.extend((str(s), "CONTINUING") for s in picks)
            need -= k
        chosen_ids = {c[0] for c in chosen}

        # demographics already on campus vs target
        target_rg = {(r, g): n for r, d in enr["race_gender"].items()
                     for g, n in d.items()}
        have = {c: 0 for c in target_rg}
        for sid in chosen_ids:
            key = (self.students[sid]["race_ethnicity"],
                   self.students[sid]["gender"])
            if key in have:
                have[key] += 1
        deficit = {c: target_rg[c] - have[c] for c in target_rg}

        n_new = n_first + n_transfer + n_nondeg + max(0, need)
        cells = self._sample_demo_cells(deficit, n_new)
        i = 0

        def make(n, entry_type, young, credits_lo=0, credits_hi=0,
                 first_term=None):
            nonlocal i
            out = []
            for _ in range(n):
                race, gender = cells[i]; i += 1
                age = self._sample_age(ages, young)
                ec = (int(self.rng.integers(credits_lo, credits_hi + 1))
                      if credits_hi else 0)
                out.append(self._new_student(
                    fy, race, gender, age, entry_type,
                    first_term or f"{fy}FA", ec))
            return out

        first_ids = make(n_first, "FIRST_TIME", young=True)
        transfer_ids = make(n_transfer, "TRANSFER_IN", young=False,
                            credits_lo=12, credits_hi=40)
        nondeg_ids = make(n_nondeg, "NONDEGREE", young=False)
        if need > 0:  # backfill "continuing" students with no in-DB history
            backfill = make(need, "CONTINUING", young=False, credits_lo=12,
                            credits_hi=50,
                            first_term=f"{fy - int(self.rng.integers(1, 4))}FA")
            chosen.extend((s, "CONTINUING") for s in backfill)

        census = (chosen
                  + [(s, "FIRST_TIME") for s in first_ids]
                  + [(s, "TRANSFER_IN") for s in transfer_ids]
                  + [(s, "NONDEGREE") for s in nondeg_ids])
        assert len(census) == enr["total"], (len(census), enr["total"])

        # --- FT/PT assignment (exact) ---
        ft_ids = set(self.rng.choice(first_ids, size=n_first_ft,
                                     replace=False)) if first_ids else set()
        flexible = [s for s, _t in census if s not in set(first_ids)]
        n_ft_flex = enr["full_time"] - n_first_ft
        # sticky: previous FT students preferred
        w = np.array([3.0 if self.students[s]["_ft"] else 1.0
                      for s in flexible])
        if n_ft_flex > 0 and flexible:
            k = min(n_ft_flex, len(flexible))
            ft_ids |= set(str(x) for x in self.rng.choice(
                flexible, size=k, replace=False, p=w / w.sum()))

        # record first-time cohort for future retention, and pre-designate
        # the IPEDS-rate share of it as eventual completers
        self.cohorts[fy] = {
            "FT": [s for s in first_ids if s in ft_ids],
            "PT": [s for s in first_ids if s not in ft_ids]}
        self._designate(fy)

        # --- enroll the three terms ---
        fall_ids = [s for s, _t in census]
        stype = dict(census)
        self._enroll_term(f"{fy}FA", fy, fall_ids, ft_ids, stype)

        p_sp = np.where(
            np.array([stype[s] == "FIRST_TIME" for s in fall_ids]),
            0.80, self.cfg.fall_to_spring)
        spring_ids = [s for s, keep in
                      zip(fall_ids, self.rng.random(len(fall_ids)) < p_sp)
                      if keep]
        n_spring_new = int(round(0.08 * n_first))
        cells2 = self._sample_demo_cells(
            {c: max(1, v) for c, v in target_rg.items()}, n_spring_new)
        cells_save, cells_i = cells, i
        cells, i = cells2, 0
        spring_new = make(n_spring_new, "FIRST_TIME", young=True,
                          first_term=f"{fy + 1}SP")
        cells, i = cells_save, cells_i
        sp_type = {s: ("CONTINUING" if stype.get(s) != "NONDEGREE"
                       else "NONDEGREE") for s in spring_ids}
        sp_type.update({s: "FIRST_TIME" for s in spring_new})
        self._enroll_term(f"{fy + 1}SP", fy, spring_ids + spring_new,
                          ft_ids, sp_type)

        summer_pool = spring_ids + spring_new
        keep = self.rng.random(len(summer_pool)) < self.cfg.summer_rate
        summer_ids = [s for s, k in zip(summer_pool, keep) if k]
        su_type = {s: sp_type.get(s, "CONTINUING") for s in summer_ids}
        self._enroll_term(f"{fy + 1}SU", fy, summer_ids, set(), su_type,
                          summer=True)

        for sid in fall_ids:
            self.students[sid]["_last_fall"] = fy
            self.students[sid]["_ft"] = sid in ft_ids
        for sid in spring_new:
            self.students[sid]["_last_fall"] = fy  # counts as recent activity

        # --- graduate segment (universities only) ---
        grad_enrolled = []
        if enr.get("grad") and enr["grad"]["total"] > 0:
            if self._prog_gr:
                grad_enrolled = self._run_grad_segment(fy, enr["grad"], ages)
            else:
                print(f"  [warn] {targets['acad_year']}: graduate enrollment "
                      f"reported but no graduate programs in completions; "
                      f"graduate students not simulated")

        # --- awards, then aid ---
        gr = targets["gradrates"]
        if gr and gr["cohort"]:
            self.grad_rates[fy] = gr["completers_150"] / gr["cohort"]
        enrolled_year = list(dict.fromkeys(fall_ids + spring_ids + spring_new
                                           + summer_ids))
        self._confer_awards(targets, enrolled_year + grad_enrolled)
        self._grant_aid(targets, enrolled_year)  # SFA metrics are UG-based
        if grad_enrolled:
            self._grant_grad_aid(targets["acad_year"], grad_enrolled)
        self._save_targets(targets)
        self.flush_year()

    def _run_grad_segment(self, fy, g, ages):
        """Graduate students: no IPEDS retention/GR cohorts, so persistence
        is heuristic; headcount, FT/PT and race x gender match EF level 12."""
        pool = [s for s, v in self.students.items()
                if v["_level"] == "GR" and not v["_left"]
                and v["_last_fall"] == fy - 1]
        keep = self.rng.random(len(pool)) < 0.62
        returning = [s for s, k in zip(pool, keep) if k][:g["total"]]
        target_rg = {(r, gd): n for r, d in g["race_gender"].items()
                     for gd, n in d.items()}
        have = {c: 0 for c in target_rg}
        for sid in returning:
            key = (self.students[sid]["race_ethnicity"],
                   self.students[sid]["gender"])
            if key in have:
                have[key] += 1
        deficit = {c: target_rg[c] - have[c] for c in target_rg}
        n_new = g["total"] - len(returning)
        cells = self._sample_demo_cells(deficit, n_new)
        grad_ages = {b: (n if AGE_BANDS_BY_LABEL[b][0] >= 22 else 0.01)
                     for b, n in ages.items()}
        new_ids = []
        for race, gender in cells:
            age = self._sample_age(grad_ages, young_bias=False)
            new_ids.append(self._new_student(
                fy, race, gender, age, "GRADUATE", f"{fy}FA", level="GR"))
        fall_ids = returning + new_ids
        w = np.array([3.0 if self.students[s]["_ft"] else 1.0
                      for s in fall_ids])
        ft_ids = set()
        if g["full_time"] and fall_ids:
            ft_ids = set(str(x) for x in self.rng.choice(
                fall_ids, size=min(g["full_time"], len(fall_ids)),
                replace=False, p=w / w.sum()))
        stype = {s: ("CONTINUING" if s in returning else "GRAD_NEW")
                 for s in fall_ids}
        self._enroll_term(f"{fy}FA", fy, fall_ids, ft_ids, stype)
        keep = self.rng.random(len(fall_ids)) < 0.90
        spring_ids = [s for s, k in zip(fall_ids, keep) if k]
        self._enroll_term(f"{fy + 1}SP", fy, spring_ids, ft_ids,
                          {s: "CONTINUING" for s in spring_ids})
        keep = self.rng.random(len(spring_ids)) < 0.35
        summer_ids = [s for s, k in zip(spring_ids, keep) if k]
        self._enroll_term(f"{fy + 1}SU", fy, summer_ids, set(),
                          {s: "CONTINUING" for s in summer_ids}, summer=True)
        for sid in fall_ids:
            self.students[sid]["_last_fall"] = fy
            self.students[sid]["_ft"] = sid in ft_ids
        return list(dict.fromkeys(fall_ids + spring_ids + summer_ids))

    # ---------------- registrations ----------------

    def _enroll_term(self, term_id, fall_year, sids, ft_ids, stype,
                     summer=False):
        online_p = 0.62 if fall_year == 2020 else 0.28  # COVID year
        demand = {}
        picks = {}
        for sid in sids:
            stu = self.students[sid]
            grad = stu["_level"] == "GR"
            if summer:
                n_courses = int(self.rng.integers(1, 3))
            elif grad:
                n_courses = (int(self.rng.integers(2, 5)) if sid in ft_ids
                             else int(self.rng.integers(1, 3)))
            elif sid in ft_ids:
                n_courses = int(self.rng.integers(4, 6))
            else:
                n_courses = int(self.rng.integers(1, 4))
            pool = (self.prog_courses.get(stu["_program"])
                    or self.gened_ids)
            # first-year undergraduates lean gen-ed
            if not grad and stu["_credits"] < 20:
                pool = self.gened_ids + pool[:6]
            k = min(n_courses, len(set(pool)))
            sel = self.rng.choice(list(dict.fromkeys(pool)), size=k,
                                  replace=False)
            picks[sid] = [str(c) for c in sel]
            for c in picks[sid]:
                demand[c] = demand.get(c, 0) + 1
        # sections
        sections = {}
        for cid, n in sorted(demand.items()):
            subj = self.courses[cid]["subject"]
            if subj not in self.instructors:
                self.instructors[subj] = [self.fake.name() for _ in
                                          range(int(self.rng.integers(3, 8)))]
            n_sec = math.ceil(n / 24)
            secs = []
            for j in range(n_sec):
                sec_id = self._section_id(term_id, cid, j + 1)
                modality = ("ONLINE" if self.rng.random() < online_p else
                            ("HYBRID" if self.rng.random() < 0.2 else "TRAD"))
                self._buf["sections"].append(
                    (sec_id, cid, term_id, f"{j + 1:02d}",
                     str(self.rng.choice(self.instructors[subj])),
                     modality, 24))
                secs.append(sec_id)
            sections[cid] = secs
        sec_fill = {cid: 0 for cid in sections}
        # registrations + grades
        for sid in sids:
            stu = self.students[sid]
            att = earned = 0
            pts = cr_gpa = 0.0
            for cid in picks[sid]:
                secs = sections[cid]
                sec = secs[sec_fill[cid] % len(secs)]
                sec_fill[cid] += 1
                cr = self.courses[cid]["credits"]
                grade, status = self._grade(stu)
                self._reg += 1
                gp = GRADE_POINTS.get(grade)
                self._buf["registrations"].append(
                    (self._reg, sid, sec, cid, term_id, cr, grade, gp, status))
                att += cr
                if grade in ("A", "B", "C", "D"):
                    earned += cr
                if gp is not None:
                    pts += gp * cr
                    cr_gpa += cr
            stu["_credits"] += earned
            stu["_terms_enrolled"] += 1
            gpa = round(pts / cr_gpa, 2) if cr_gpa else None
            self._buf["student_terms"].append(
                (sid, term_id, stu["_program"], stu["_degree_seeking"],
                 "FT" if sid in ft_ids else "PT", stype.get(sid, "CONTINUING"),
                 att, earned, gpa, stu["_level"]))

    def _grade(self, stu):
        r = self.rng.random()
        if r < 0.07:
            return "W", "WITHDRAWN"
        if r < 0.08:
            return "I", "INCOMPLETE"
        q = stu["_ability"] * 4 + self.rng.normal(0, 0.9)
        for cut, g in ((3.4, "A"), (2.5, "B"), (1.5, "C"), (0.7, "D")):
            if q >= cut:
                return g, "COMPLETED"
        return "F", "COMPLETED"

    # ---------------- awards ----------------

    def _fallback_rate(self):
        known = list(self.grad_rates.values())
        return sum(known) / len(known) if known else 0.35

    def _designate(self, cohort_year, already_completed=0):
        """Mark the IPEDS-150%-rate share of a first-time FT cohort as
        destined completers; enrollment selection protects them so the
        simulated grad rate can reach the target."""
        ftc = [s for s in self.cohorts.get(cohort_year, {}).get("FT", [])
               if s in self.students]
        rate = self.grad_rates.get(cohort_year, self._fallback_rate())
        k = int(round(rate * len(ftc))) - already_completed
        cands = [s for s in ftc if not self.students[s]["_awards"]
                 and not self.students[s]["_destined"]]
        for s in self.rng.choice(cands, size=min(max(0, k), len(cands)),
                                 replace=False):
            self.students[str(s)]["_destined"] = True
        for s in ftc:
            self.students[s]["_cohort_year"] = cohort_year

    def designate_from_state(self, next_fall_year):
        """Supplement path: re-designate completers for cohorts still inside
        their 150% window in a reloaded database."""
        for cy in self.cohorts:
            if 0 < next_fall_year - cy < self.profile["gr_window"]:
                done = sum(1 for s in self.cohorts[cy]["FT"]
                           if s in self.students
                           and self.students[s]["_awards"])
                self._designate(cy, already_completed=done)

    def _confer_awards(self, targets, enrolled_year):
        fy = targets["fall_year"]
        ay = targets["acad_year"]
        by_prog = {}
        for sid in enrolled_year:
            stu = self.students[sid]
            if stu["_program"]:
                by_prog.setdefault(stu["_program"], []).append(sid)
        by_cip4 = {}
        by_cip2 = {}
        for pid, p in self.programs.items():
            for sid in by_prog.get(pid, []):
                by_cip4.setdefault(p["cip_code"][:5], []).append(sid)
                by_cip2.setdefault(p["cip_code"][:2], []).append(sid)
        # Budget first awards to recent first-time FT cohort members so the
        # simulated 150% grad rate tracks the institution's IPEDS GR data;
        # remaining awards flow to continuing/part-time students instead.
        fallback_rate = self._fallback_rate()
        budget = {}
        recent = {}
        for cy in range(fy - self.profile["gr_window"] + 1, fy + 1):
            members = [s for s in self.cohorts.get(cy, {}).get("FT", [])
                       if s in self.students]
            rate = self.grad_rates.get(cy, fallback_rate)
            done = sum(1 for s in members if self.students[s]["_awards"])
            budget[cy] = max(0, int(round(rate * len(members))) - done)
            for s in members:
                recent[s] = cy

        def grad_ok(s):
            cy = recent.get(s)
            return (cy is None or self.students[s]["_awards"]
                    or budget[cy] > 0)

        shortfall = []
        comp = sorted(targets["completions"],
                      key=lambda c: c["award_level"] != "ASSOCIATE")
        for c in comp:
            level = c["award_level"]
            pid = LEVEL_PREFIX.get(level, "C") + c["cip"].replace(".", "")
            if pid not in self.programs:
                pid = next((q for q, p in self.programs.items()
                            if p["cip_code"] == c["cip"]
                            and p["award_level"] == level), None)
            key = (c["cip"], level)
            # candidates: own program, then same 4-digit CIP (stackable /
            # embedded credentials), then same 2-digit CIP as a last resort
            grp = "GR" if level in GRAD_LEVELS else "UG"
            # years a student is normally enrolled before this credential
            min_years = max(0, REQ_CREDITS.get(level, 40) // 30 - 1)

            def prio(s):
                """Budget-eligible cohort members in their graduating year
                come first, so the simulated GR tracks IPEDS from both
                sides (the budget alone only caps it)."""
                cy = recent.get(s)
                if (cy is not None and budget.get(cy, 0) > 0
                        and not self.students[s]["_awards"]
                        and fy - cy >= min_years):
                    return 0
                return 1

            take = []
            taken = set()
            all_ds = [s for s in enrolled_year
                      if self.students[s]["_degree_seeking"]
                      and self.students[s]["_level"] == grp]
            for tier, cands in enumerate((by_prog.get(pid, []),
                                          by_cip4.get(c["cip"][:5], []),
                                          by_cip2.get(c["cip"][:2], []),
                                          all_ds)):
                cands = [s for s in cands if s not in taken
                         and self.students[s]["_level"] == grp
                         and key not in self.students[s]["_awards"]]
                cands.sort(key=lambda s: (prio(s),
                                          -self.students[s]["_credits"]))
                for s in cands:
                    if len(take) >= c["n"]:
                        break
                    if not grad_ok(s):
                        continue
                    if s in recent and not self.students[s]["_awards"]:
                        budget[recent[s]] -= 1
                    if tier == 3 and pid:  # cross-CIP pick = program switch
                        self.students[s]["_program"] = pid
                    take.append(s)
                    taken.add(s)
                if len(take) >= c["n"]:
                    break
            if len(take) < c["n"]:
                shortfall.append((c["cip"], level, c["n"] - len(take)))
            for sid in take:
                stu = self.students[sid]
                stu["_awards"].add(key)
                r = self.rng.random()
                if r < 0.15:
                    date = dt.date(fy, 12, 15)
                elif r < 0.90:
                    date = dt.date(fy + 1, 5, 10)
                else:
                    date = dt.date(fy + 1, 7, 28)
                self._award += 1
                self._buf["awards"].append(
                    (self._award, sid, pid, c["cip"], level, date, ay))
                leave_p = LEAVE_P.get(
                    level, 0.20 if self.profile["two_year"] else 0.40)
                if self.rng.random() < leave_p:
                    stu["_left"] = True
        if shortfall:
            n = sum(s[2] for s in shortfall)
            print(f"  [warn] {ay}: {n} award(s) unfilled across "
                  f"{len(shortfall)} program(s) (insufficient candidates)")

    # ---------------- financial aid ----------------

    def _grant_aid(self, targets, enrolled_year):
        aid = targets["aid"]
        if not aid:
            return
        ay = targets["acad_year"]
        n = len(enrolled_year)
        pell_n = int(round(aid["pell_pct"] / 100 * n))
        w = np.array([2.5 if self.students[s].get("_pell") else 1.0
                      for s in enrolled_year])
        pell_ids = ([str(s) for s in self.rng.choice(
            enrolled_year, size=min(pell_n, n), replace=False,
            p=w / w.sum())] if pell_n else [])
        if pell_ids and aid["pell_avg"]:
            amts = self.rng.normal(aid["pell_avg"], 0.28 * aid["pell_avg"],
                                   len(pell_ids))
            amts = np.clip(amts, 650, max(7500, aid["pell_avg"] * 1.8))
            amts *= aid["pell_avg"] / amts.mean()
            for sid, amt in zip(pell_ids, amts):
                self.students[sid]["_pell"] = True
                self._add_aid(sid, ay, "PELL", round(float(amt), 0))
        pell_set = set(pell_ids)
        for sid in enrolled_year:
            self.students[sid]["_pell"] = sid in pell_set
        extra_n = max(0, int(round(aid["grant_pct"] / 100 * n)) - len(pell_ids))
        others = [s for s in enrolled_year if s not in pell_set]
        for sid in self.rng.choice(others, size=min(extra_n, len(others)),
                                   replace=False):
            kind = ("STATE_GRANT" if self.rng.random() < 0.7
                    else "INST_SCHOLARSHIP")
            self._add_aid(str(sid), ay, kind,
                          round(float(np.clip(self.rng.normal(1800, 700),
                                              250, 5000)), 0))
        if aid["loan_pct"] > 0:
            loan_n = int(round(aid["loan_pct"] / 100 * n))
            for sid in self.rng.choice(enrolled_year, size=min(loan_n, n),
                                       replace=False):
                self._add_aid(str(sid), ay, "FED_LOAN",
                              round(float(np.clip(
                                  self.rng.normal(aid["loan_avg"] or 4500,
                                                  1500), 500, 12000)), 0))

    def _grant_grad_aid(self, ay, grad_ids):
        """Fellowships/assistantships for graduate students. Heuristic
        (see GRAD_AID); assistantships are sticky year to year."""
        for sid in grad_ids:
            stu = self.students[sid]
            prog = self.programs.get(stu["_program"])
            grp = ("DOCTORAL" if prog
                   and prog["award_level"].startswith("DOCTORAL")
                   else "MASTERS")
            for kind, (share, mu, sd) in GRAD_AID[grp].items():
                p = share
                if kind == "ASSISTANTSHIP" and stu.get("_assist"):
                    p = 0.90
                if self.rng.random() < p:
                    amt = round(float(np.clip(self.rng.normal(mu, sd),
                                              1000, mu * 2)), 0)
                    self._add_aid(sid, ay, kind, amt)
                    if kind == "ASSISTANTSHIP":
                        stu["_assist"] = True

    def _add_aid(self, sid, ay, kind, amount):
        self._aid += 1
        disb = amount if self.rng.random() > 0.05 else round(amount * 0.5, 0)
        self._buf["financial_aid"].append((self._aid, sid, ay, kind,
                                           amount, disb))

    # ---------------- persistence ----------------

    def _save_targets(self, t):
        fy = t["fall_year"]
        rows = []
        enr = t["enrollment"]
        for m in ("total", "full_time", "part_time", "first_time",
                  "transfer_in", "nondegree"):
            rows.append((fy, "enrollment", m, None, None, enr[m], False))
        for race, d in enr["race_gender"].items():
            for g, v in d.items():
                rows.append((fy, "enrollment", "race_gender", race, g, v,
                             False))
        if enr.get("grad"):
            for m, tag in (("total", "grad_total"),
                           ("full_time", "grad_full_time"),
                           ("part_time", "grad_part_time")):
                rows.append((fy, "enrollment", tag, None, None,
                             enr["grad"][m], False))
        if t["retention"]:
            rows.append((fy, "retention", "ret_pct", "FT",
                         None, t["retention"]["ret_pct_ft"], False))
            if t["retention"]["ret_pct_pt"] is not None:
                rows.append((fy, "retention", "ret_pct", "PT",
                             None, t["retention"]["ret_pct_pt"], False))
        if t["gradrates"]:
            for m, v in t["gradrates"].items():
                rows.append((fy, "gradrate", m, None, None, v, False))
        by_level = {}
        for c in t["completions"]:
            by_level[c["award_level"]] = by_level.get(c["award_level"], 0) \
                + c["n"]
            rows.append((fy, "completions", "awards_by_cip", c["cip"],
                         c["award_level"], c["n"],
                         t["completions_estimated"]))
        for lvl, v in by_level.items():
            rows.append((fy, "completions", "awards_by_level", lvl, None, v,
                         t["completions_estimated"]))
        if t["aid"]:
            for m, v in t["aid"].items():
                rows.append((fy, "aid", m, None, None, v, False))
        if t["tuition"]:
            for m, v in t["tuition"].items():
                rows.append((fy, "tuition", m, None, None, v, False))
        self._buf["ipeds_targets"].extend(rows)

    def flush_year(self):
        inserts = {
            "students": "INSERT INTO students VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            "terms": "INSERT INTO terms VALUES (?,?,?,?,?,?,?)",
            "sections": "INSERT INTO sections VALUES (?,?,?,?,?,?,?)",
            "student_terms": "INSERT INTO student_terms VALUES "
                             "(?,?,?,?,?,?,?,?,?,?)",
            "registrations": "INSERT INTO registrations VALUES "
                             "(?,?,?,?,?,?,?,?,?)",
            "awards": "INSERT INTO awards VALUES (?,?,?,?,?,?,?)",
            "financial_aid": "INSERT INTO financial_aid VALUES (?,?,?,?,?,?)",
            "ipeds_targets": "INSERT INTO ipeds_targets VALUES (?,?,?,?,?,?,?)",
        }
        for tbl, sql in inserts.items():
            if self._buf[tbl]:
                self.con.executemany(sql, self._buf[tbl])
                self._buf[tbl] = []

    # ---------------- state reload (supplement) ----------------

    def load_state(self):
        """Rebuild in-memory state from an existing simulated database."""
        con = self.con
        self._sid = int(con.execute(
            "SELECT COALESCE(MAX(CAST(SUBSTR(student_id, 2) AS INT)), 0) "
            "FROM students WHERE student_id LIKE 'S%'").fetchone()[0])
        self._reg = int(con.execute(
            "SELECT COALESCE(MAX(reg_id), 0) FROM registrations").fetchone()[0])
        self._award = int(con.execute(
            "SELECT COALESCE(MAX(award_id), 0) FROM awards").fetchone()[0])
        self._aid = int(con.execute(
            "SELECT COALESCE(MAX(aid_id), 0) FROM financial_aid").fetchone()[0])
        for r in con.execute("SELECT * FROM programs").fetchall():
            self.programs[r[0]] = dict(zip(
                ("program_id", "cip_code", "title", "award_level",
                 "required_credits"), r))
        for r in con.execute("SELECT * FROM courses").fetchall():
            self.courses[r[0]] = dict(zip(
                ("course_id", "subject", "course_number", "title", "credits",
                 "department"), r))
        rows = con.execute("""
            WITH cum AS (
                SELECT student_id,
                       SUM(credits) FILTER (grade IN ('A','B','C','D'))
                           AS earned
                FROM registrations GROUP BY 1
            ), lastft AS (
                SELECT st.student_id,
                       MAX(t.fall_year)
                           FILTER (t.term_type = 'FA') AS last_fall,
                       ARG_MAX(st.enrollment_status, t.start_date) AS status,
                       ARG_MAX(st.program_id, t.start_date) AS program_id,
                       ARG_MAX(st.student_level, t.start_date) AS lvl
                FROM student_terms st JOIN terms t USING (term_id)
                GROUP BY 1
            )
            SELECT s.student_id, s.gender, s.race_ethnicity, s.first_term,
                   s.entry_type, s.entry_credits,
                   COALESCE(c.earned, 0), l.last_fall, l.status, l.program_id,
                   l.lvl
            FROM students s
            LEFT JOIN cum c USING (student_id)
            LEFT JOIN lastft l USING (student_id)
        """).fetchall()
        for (sid, gender, race, first_term, entry_type, ec, earned,
             last_fall, status, pid, lvl) in rows:
            if lvl is None:  # pre-student_level databases
                prog = self.programs.get(pid)
                lvl = ("GR" if entry_type == "GRADUATE" or
                       (prog and prog["award_level"] in GRAD_LEVELS)
                       else "UG")
            self.students[sid] = {
                "student_id": sid, "gender": gender, "race_ethnicity": race,
                "first_term": first_term, "entry_type": entry_type,
                "entry_credits": ec or 0,
                "_ability": float(np.clip(self.rng.beta(5, 2.2), 0.15, 0.98)),
                "_credits": float((ec or 0) + (earned or 0)),
                "_program": pid,
                "_degree_seeking": entry_type != "NONDEGREE",
                "_ft": status == "FT", "_last_fall": last_fall,
                "_awards": set(), "_left": False, "_terms_enrolled": 1,
                "_pell": False, "_level": lvl,
                "_destined": False, "_cohort_year": None,
            }
        for sid, cip, lvl in con.execute(
                "SELECT student_id, cip_code, award_level FROM awards"
        ).fetchall():
            if sid in self.students:
                self.students[sid]["_awards"].add((cip, lvl))
                if lvl == "ASSOCIATE":
                    self.students[sid]["_left"] = True
        for sid, in con.execute("""
            SELECT DISTINCT student_id FROM financial_aid
            WHERE aid_type = 'PELL' AND acad_year =
                  (SELECT MAX(acad_year) FROM financial_aid)
        """).fetchall():
            if sid in self.students:
                self.students[sid]["_pell"] = True
        # rebuild first-time fall cohorts (needed for retention enforcement)
        for fy, status, sid in con.execute("""
            SELECT t.fall_year, st.enrollment_status, st.student_id
            FROM student_terms st JOIN terms t USING (term_id)
            WHERE t.term_type = 'FA' AND st.student_type = 'FIRST_TIME'
              AND st.degree_seeking
        """).fetchall():
            if sid in self.students and status in ("FT", "PT"):
                self.cohorts.setdefault(fy, {"FT": [], "PT": []})[status] \
                    .append(sid)
        # program sampling lists are rebuilt by build_catalog(), which the
        # supplement path always calls after load_state()


AGE_BANDS_BY_LABEL = {label: (lo, hi) for _c, (label, lo, hi)
                      in AGE_BANDS.items()}
DEFAULT_AGES = {label: n for label, n in
                [("Under 18", 12), ("18-19", 25), ("20-21", 15),
                 ("22-24", 10), ("25-29", 12), ("30-34", 8), ("35-39", 6),
                 ("40-49", 7), ("50-64", 4), ("65 and over", 1)]}
