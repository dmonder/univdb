"""Configuration defaults for collegesim."""
import os
from dataclasses import dataclass, field

def _default_source():
    """Prefer a project-local ipeds_*.duckdb; fall back to the IPEDSR copy."""
    import glob
    local = sorted(glob.glob("ipeds_*.duckdb"))
    if local:
        return local[-1]
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "IPEDSR", "database", "ipeds.duckdb",
    )


DEFAULT_SOURCE_DB = _default_source()

# Award-level detail codes in the IPEDS C (completions) tables, mapped to
# canonical labels used in the simulated database. Rollup codes (12, 13, 14,
# 15) are excluded. Codes 20/21 replaced code 1 splits starting with C2020_A.
AWLEVEL_MAP = {
    "1": "CERT_LT1YR",
    "2": "CERT_1TO2YR",
    "3": "ASSOCIATE",
    "4": "CERT_2TO4YR",
    "5": "BACHELORS",
    "6": "POSTBAC_CERT",
    "7": "MASTERS",
    "8": "POSTMA_CERT",
    "17": "DOCTORAL_RS",
    "18": "DOCTORAL_PP",
    "19": "DOCTORAL_OTH",
    "20": "CERT_LT12WK",
    "21": "CERT_12WKTO1YR",
}

# Award levels earned by graduate-level students
GRAD_LEVELS = {"MASTERS", "POSTBAC_CERT", "POSTMA_CERT",
               "DOCTORAL_RS", "DOCTORAL_PP", "DOCTORAL_OTH"}

# Race/ethnicity column stems in EF####A (2010+ format) -> canonical label.
RACE_COLS = {
    "EFAIAN": "American Indian or Alaska Native",
    "EFASIA": "Asian",
    "EFBKAA": "Black or African American",
    "EFHISP": "Hispanic or Latino",
    "EFNHPI": "Native Hawaiian or Other Pacific Islander",
    "EFWHIT": "White",
    "EF2MOR": "Two or more races",
    "EFUNKN": "Race/ethnicity unknown",
    "EFNRAL": "Nonresident alien",
}

# EF####B age band codes (detail rows; 1/2/7 are totals/subtotals).
AGE_BANDS = {
    3: ("Under 18", 16, 17),
    4: ("18-19", 18, 19),
    5: ("20-21", 20, 21),
    6: ("22-24", 22, 24),
    8: ("25-29", 25, 29),
    9: ("30-34", 30, 34),
    10: ("35-39", 35, 39),
    11: ("40-49", 40, 49),
    12: ("50-64", 50, 64),
    13: ("65 and over", 65, 75),
}


@dataclass
class SimConfig:
    unitid: int
    school_name: str = ""
    start_year: int = 2014          # first fall term (academic year 2014-15)
    end_year: int = 2023            # last fall term (academic year 2023-24)
    source_db: str = DEFAULT_SOURCE_DB
    out_db: str = ""                # defaults to output/sim_<unitid>.duckdb
    seed: int = 42
    malform_rate: float = 0.005     # share of rows corrupted per table
    # persistence rates without IPEDS targets (fall->spring, spring->summer)
    fall_to_spring: float = 0.85
    summer_rate: float = 0.25
    # institutions above size_limit fall headcount are refused (with
    # suggestions for smaller, structurally similar schools) unless force=True
    size_limit: int = 10000
    force: bool = False
    # institution geography, filled from the IPEDS HD table at build time
    school_city: str = ""
    school_state: str = ""
    school_zip: str = ""
    # identifier templates; placeholders: program {prefix} {cip} {cip_dotted},
    # course {subject} {number}, section {term} {course} {section}
    program_id_format: str = "{prefix}{cip}"
    course_id_format: str = "{subject}-{number}"
    section_id_format: str = "{term}-{course}-{section}"
    field_defaults: dict = field(default_factory=dict)

    def default_out_db(self):
        return os.path.join("output", f"sim_{self.unitid}.duckdb")
