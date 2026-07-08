-- Analytic + validation views. v_* are analytic; v_val_* compare sim vs IPEDS.

-- Fall census headcount by year with composition (headcount/composition
-- columns are undergraduate; graduate students in the grad_* columns)
CREATE OR REPLACE VIEW v_fall_enrollment AS
WITH rows_lvl AS (
    SELECT st.*, t.fall_year,
           COALESCE(st.student_level, 'UG') AS lvl
    FROM student_terms st JOIN terms t USING (term_id)
    WHERE t.term_type = 'FA'
)
SELECT fall_year,
       COUNT(*) FILTER (lvl = 'UG') AS headcount,
       COUNT(*) FILTER (lvl = 'UG' AND enrollment_status = 'FT') AS full_time,
       COUNT(*) FILTER (lvl = 'UG' AND enrollment_status = 'PT') AS part_time,
       COUNT(*) FILTER (lvl = 'UG' AND student_type = 'FIRST_TIME') AS first_time,
       COUNT(*) FILTER (lvl = 'UG' AND student_type = 'TRANSFER_IN') AS transfer_in,
       COUNT(*) FILTER (lvl = 'UG' AND NOT degree_seeking) AS nondegree,
       COUNT(*) FILTER (lvl = 'GR') AS grad_headcount,
       COUNT(*) FILTER (lvl = 'GR' AND enrollment_status = 'FT') AS grad_full_time,
       COUNT(*) FILTER (lvl = 'GR' AND enrollment_status = 'PT') AS grad_part_time
FROM rows_lvl
GROUP BY fall_year ORDER BY fall_year;

CREATE OR REPLACE VIEW v_fall_enrollment_demo AS
SELECT t.fall_year, s.race_ethnicity, s.gender, COUNT(*) AS headcount
FROM student_terms st
JOIN terms t USING (term_id)
JOIN students s USING (student_id)
WHERE t.term_type = 'FA'
GROUP BY ALL ORDER BY 1, 2, 3;

-- Fall-to-fall retention of first-time degree-seeking cohorts
CREATE OR REPLACE VIEW v_retention AS
WITH cohort AS (
    SELECT st.student_id, t.fall_year, st.enrollment_status
    FROM student_terms st JOIN terms t USING (term_id)
    WHERE t.term_type = 'FA' AND st.student_type = 'FIRST_TIME' AND st.degree_seeking
), returned AS (
    SELECT st.student_id, t.fall_year
    FROM student_terms st JOIN terms t USING (term_id)
    WHERE t.term_type = 'FA'
)
SELECT c.fall_year AS cohort_year, c.enrollment_status,
       COUNT(*) AS cohort_n,
       COUNT(r.student_id) AS returned_n,
       ROUND(100.0 * COUNT(r.student_id) / COUNT(*), 1) AS retention_pct
FROM cohort c
LEFT JOIN returned r
       ON r.student_id = c.student_id AND r.fall_year = c.fall_year + 1
GROUP BY ALL ORDER BY 1, 2;

-- Course success (A/B/C = success) by term and subject
CREATE OR REPLACE VIEW v_course_success AS
SELECT r.term_id, c.subject,
       COUNT(*) AS enrollments,
       ROUND(100.0 * COUNT(*) FILTER (r.grade IN ('A','B','C')) / COUNT(*), 1)
           AS success_pct,
       ROUND(100.0 * COUNT(*) FILTER (r.grade = 'W') / COUNT(*), 1)
           AS withdrawal_pct
FROM registrations r JOIN courses c USING (course_id)
GROUP BY ALL ORDER BY 1, 2;

-- Completions by academic year, level, CIP
CREATE OR REPLACE VIEW v_completions AS
SELECT acad_year, award_level, cip_code, COUNT(*) AS awards
FROM awards GROUP BY ALL ORDER BY 1, 2, 3;

-- 150%-time completion of first-time FULL-TIME fall cohorts (mirrors the
-- IPEDS GRS cohort; the window is 3 years at 2-year institutions and
-- 6 years at 4-year institutions, read from sim_meta)
CREATE OR REPLACE VIEW v_gradrate_150 AS
WITH win AS (
    SELECT COALESCE((SELECT CAST(value AS INT) FROM sim_meta
                     WHERE key = 'gr_window'), 3) AS w
), cohort AS (
    SELECT st.student_id, t.fall_year
    FROM student_terms st JOIN terms t USING (term_id)
    WHERE t.term_type = 'FA' AND st.student_type = 'FIRST_TIME'
      AND st.degree_seeking AND st.enrollment_status = 'FT'
)
SELECT c.fall_year AS cohort_year,
       COUNT(DISTINCT c.student_id) AS cohort_n,
       COUNT(DISTINCT a.student_id) AS completers_150,
       ROUND(100.0 * COUNT(DISTINCT a.student_id)
             / COUNT(DISTINCT c.student_id), 1) AS grad_rate_150
FROM cohort c CROSS JOIN win
LEFT JOIN awards a
       ON a.student_id = c.student_id
      AND a.award_date < MAKE_DATE(c.fall_year + win.w, 9, 1)
GROUP BY ALL ORDER BY 1;

-- Financial aid summary by year. The pct base matches the aid type's level:
-- undergraduate enrollment for UG aid (per SFA), graduate enrollment for
-- fellowships/assistantships.
CREATE OR REPLACE VIEW v_financial_aid AS
WITH enrolled AS (
    SELECT t.acad_year, COALESCE(st.student_level, 'UG') AS lvl,
           COUNT(DISTINCT st.student_id) AS n
    FROM student_terms st JOIN terms t USING (term_id)
    GROUP BY ALL
)
SELECT f.acad_year, f.aid_type,
       COUNT(DISTINCT f.student_id) AS recipients,
       ROUND(AVG(f.amount_disbursed), 0) AS avg_amount,
       ROUND(100.0 * COUNT(DISTINCT f.student_id) / MAX(e.n), 1)
           AS pct_of_level_enrolled
FROM financial_aid f
JOIN enrolled e
  ON e.acad_year = f.acad_year
 AND e.lvl = CASE WHEN f.aid_type IN ('FELLOWSHIP', 'ASSISTANTSHIP')
                  THEN 'GR' ELSE 'UG' END
GROUP BY ALL ORDER BY 1, 2;

-- ============ validation views (sim vs IPEDS target) ============

CREATE OR REPLACE VIEW v_val_fall_enrollment AS
SELECT tg.fall_year, tg.metric,
       tg.value AS ipeds_value,
       CASE tg.metric
            WHEN 'total' THEN sim.headcount
            WHEN 'full_time' THEN sim.full_time
            WHEN 'part_time' THEN sim.part_time
            WHEN 'first_time' THEN sim.first_time
            WHEN 'transfer_in' THEN sim.transfer_in
            WHEN 'nondegree' THEN sim.nondegree
            WHEN 'grad_total' THEN sim.grad_headcount
            WHEN 'grad_full_time' THEN sim.grad_full_time
            WHEN 'grad_part_time' THEN sim.grad_part_time
       END AS sim_value
FROM ipeds_targets tg JOIN v_fall_enrollment sim USING (fall_year)
WHERE tg.category = 'enrollment' AND tg.dim1 IS NULL
ORDER BY tg.fall_year, tg.metric;

CREATE OR REPLACE VIEW v_val_retention AS
SELECT tg.fall_year AS cohort_year,
       tg.dim1 AS status,
       tg.value AS ipeds_pct,
       r.retention_pct AS sim_pct,
       r.cohort_n AS sim_cohort
FROM ipeds_targets tg
JOIN v_retention r
  ON r.cohort_year = tg.fall_year AND r.enrollment_status = tg.dim1
WHERE tg.category = 'retention' AND tg.metric = 'ret_pct'
  AND tg.fall_year < (SELECT MAX(fall_year) FROM terms)
ORDER BY 1, 2;

CREATE OR REPLACE VIEW v_val_completions AS
WITH sim AS (
    SELECT a.acad_year, a.award_level, COUNT(*) AS n
    FROM awards a GROUP BY ALL
)
SELECT tg.fall_year,
       tg.dim1 AS award_level,
       tg.value AS ipeds_n,
       COALESCE(sim.n, 0) AS sim_n,
       tg.estimated
FROM ipeds_targets tg
LEFT JOIN sim
       ON sim.acad_year = tg.fall_year || '-' ||
          RIGHT(CAST(tg.fall_year + 1 AS VARCHAR), 2)
      AND sim.award_level = tg.dim1
WHERE tg.category = 'completions' AND tg.metric = 'awards_by_level'
ORDER BY 1, 2;

CREATE OR REPLACE VIEW v_val_aid AS
WITH enrolled AS (
    SELECT t.acad_year, t.fall_year, COUNT(DISTINCT st.student_id) AS n
    FROM student_terms st JOIN terms t USING (term_id)
    WHERE COALESCE(st.student_level, 'UG') = 'UG'
    GROUP BY ALL
), pell AS (
    SELECT f.acad_year,
           COUNT(DISTINCT f.student_id) AS recips,
           AVG(f.amount_disbursed) AS avg_amt
    FROM financial_aid f WHERE f.aid_type = 'PELL' GROUP BY 1
)
SELECT tg.fall_year, tg.metric,
       tg.value AS ipeds_value,
       CASE tg.metric
            WHEN 'pell_pct' THEN ROUND(100.0 * pell.recips / e.n, 1)
            WHEN 'pell_avg' THEN ROUND(pell.avg_amt, 0)
       END AS sim_value
FROM ipeds_targets tg
JOIN enrolled e ON e.fall_year = tg.fall_year
LEFT JOIN pell ON pell.acad_year = e.acad_year
WHERE tg.category = 'aid' AND tg.metric IN ('pell_pct', 'pell_avg')
ORDER BY 1, 2;

CREATE OR REPLACE VIEW v_val_gradrate AS
SELECT tg.fall_year AS cohort_year,
       MAX(CASE WHEN tg.metric = 'cohort' THEN tg.value END) AS ipeds_cohort,
       MAX(CASE WHEN tg.metric = 'completers_150' THEN tg.value END)
           AS ipeds_completers_150,
       ROUND(100.0 * MAX(CASE WHEN tg.metric = 'completers_150' THEN tg.value END)
             / MAX(CASE WHEN tg.metric = 'cohort' THEN tg.value END), 1)
           AS ipeds_rate,
       MAX(g.cohort_n) AS sim_cohort,
       MAX(g.completers_150) AS sim_completers,
       MAX(g.grad_rate_150) AS sim_rate
FROM ipeds_targets tg
LEFT JOIN v_gradrate_150 g ON g.cohort_year = tg.fall_year
WHERE tg.category = 'gradrate'
GROUP BY 1 ORDER BY 1;
