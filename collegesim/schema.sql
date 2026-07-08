-- Simulated SIS schema (star-ish, resembling a community college SIS extract)

CREATE TABLE IF NOT EXISTS institution (
    unitid INTEGER,
    name VARCHAR,
    city VARCHAR,
    state VARCHAR,
    zip VARCHAR
);

CREATE TABLE IF NOT EXISTS terms (
    term_id VARCHAR PRIMARY KEY,      -- e.g. 2014FA
    acad_year VARCHAR,                -- e.g. 2014-15
    fall_year INTEGER,                -- fall year of the academic year
    term_type VARCHAR,                -- FA / SP / SU
    start_date DATE,
    end_date DATE,
    census_date DATE
);

CREATE TABLE IF NOT EXISTS programs (
    program_id VARCHAR PRIMARY KEY,   -- e.g. A55220 style
    cip_code VARCHAR,
    title VARCHAR,
    award_level VARCHAR,              -- ASSOCIATE / CERT_* (see config.AWLEVEL_MAP)
    required_credits INTEGER
);

CREATE TABLE IF NOT EXISTS courses (
    course_id VARCHAR PRIMARY KEY,    -- e.g. ENG-111
    subject VARCHAR,
    course_number VARCHAR,
    title VARCHAR,
    credits INTEGER,
    department VARCHAR
);

CREATE TABLE IF NOT EXISTS sections (
    section_id VARCHAR PRIMARY KEY,   -- e.g. 2014FA-ENG-111-01
    course_id VARCHAR,
    term_id VARCHAR,
    section_no VARCHAR,
    instructor VARCHAR,
    modality VARCHAR,                 -- TRAD / ONLINE / HYBRID
    capacity INTEGER
);

CREATE TABLE IF NOT EXISTS students (
    student_id VARCHAR PRIMARY KEY,
    first_name VARCHAR,
    last_name VARCHAR,
    dob DATE,
    gender VARCHAR,                   -- M / W
    race_ethnicity VARCHAR,
    city VARCHAR,
    state VARCHAR,
    zip VARCHAR,
    first_term VARCHAR,
    entry_type VARCHAR,               -- FIRST_TIME / TRANSFER_IN / NONDEGREE / CONTINUING
    entry_credits INTEGER             -- transfer/prior credits at entry
);

-- one row per student per enrolled term (census snapshot)
CREATE TABLE IF NOT EXISTS student_terms (
    student_id VARCHAR,
    term_id VARCHAR,
    program_id VARCHAR,
    degree_seeking BOOLEAN,
    enrollment_status VARCHAR,        -- FT / PT
    student_type VARCHAR,             -- FIRST_TIME / TRANSFER_IN / CONTINUING / RETURNING / NONDEGREE
    credits_attempted INTEGER,
    credits_earned INTEGER,
    term_gpa DOUBLE,
    student_level VARCHAR              -- UG / GR
);

CREATE TABLE IF NOT EXISTS registrations (
    reg_id BIGINT,
    student_id VARCHAR,
    section_id VARCHAR,
    course_id VARCHAR,
    term_id VARCHAR,
    credits INTEGER,
    grade VARCHAR,                    -- A B C D F W I AU
    grade_points DOUBLE,
    status VARCHAR                    -- COMPLETED / WITHDRAWN / INCOMPLETE
);

CREATE TABLE IF NOT EXISTS awards (
    award_id BIGINT,
    student_id VARCHAR,
    program_id VARCHAR,
    cip_code VARCHAR,
    award_level VARCHAR,
    award_date DATE,
    acad_year VARCHAR
);

CREATE TABLE IF NOT EXISTS financial_aid (
    aid_id BIGINT,
    student_id VARCHAR,
    acad_year VARCHAR,
    aid_type VARCHAR,                 -- PELL / STATE_GRANT / INST_SCHOLARSHIP / FED_LOAN
    amount_offered DOUBLE,
    amount_disbursed DOUBLE
);

-- IPEDS targets captured at build/supplement time (long format), for validation
CREATE TABLE IF NOT EXISTS ipeds_targets (
    fall_year INTEGER,
    category VARCHAR,                 -- enrollment / retention / gradrate / completions / aid / tuition
    metric VARCHAR,
    dim1 VARCHAR,
    dim2 VARCHAR,
    value DOUBLE,
    estimated BOOLEAN
);

-- record of intentionally malformed rows (for graders/tests; analysts may ignore)
CREATE TABLE IF NOT EXISTS sim_malform_log (
    table_name VARCHAR,
    row_key VARCHAR,
    issue VARCHAR
);

-- build-time settings the views and supplement runs need (gr_window, etc.)
CREATE TABLE IF NOT EXISTS sim_meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);
