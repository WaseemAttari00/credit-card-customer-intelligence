-- core.dim_customer
-- Grain: one row per customer (credit card account holder). PK: customer_id.
-- Decodes the numeric codes, maps undocumented codes to 'Unknown' (keeping the raw
-- code for traceability), adds analysis bands and data-quality flags.
DROP TABLE IF EXISTS core.dim_customer CASCADE;

CREATE TABLE core.dim_customer AS
WITH flags AS (
    SELECT
        customer_id,
        bool_or(check_name = 'duplicate_profile')            AS dq_duplicate_profile,
        bool_or(check_name = 'delinquent_with_no_balance')   AS dq_status_balance_conflict,
        bool_or(check_name = 'never_used_account')           AS dq_never_used,
        count(*) FILTER (WHERE severity = 'WARN')            AS dq_warning_count
    FROM audit.dq_row_flags
    GROUP BY customer_id
)
SELECT
    s.customer_id,
    CASE s.sex_code WHEN 1 THEN 'Male' WHEN 2 THEN 'Female' END                       AS sex,
    s.education_code,
    CASE s.education_code
        WHEN 1 THEN 'Graduate school' WHEN 2 THEN 'University'
        WHEN 3 THEN 'High school'     WHEN 4 THEN 'Other'
        ELSE 'Unknown' END                                                             AS education,
    s.marriage_code,
    CASE s.marriage_code WHEN 1 THEN 'Married' WHEN 2 THEN 'Single' WHEN 3 THEN 'Other'
        ELSE 'Unknown' END                                                             AS marital_status,
    s.age,
    CASE WHEN s.age < 25 THEN '21-24' WHEN s.age < 35 THEN '25-34' WHEN s.age < 45 THEN '35-44'
         WHEN s.age < 55 THEN '45-54' ELSE '55+' END                                    AS age_band,
    s.credit_limit,
    -- Limit is reported once (as of the data extract); I assume it was constant over Apr-Sep 2005.
    CASE WHEN s.credit_limit < 50000  THEN '1. <50k'
         WHEN s.credit_limit < 150000 THEN '2. 50k-150k'
         WHEN s.credit_limit < 300000 THEN '3. 150k-300k'
         ELSE '4. 300k+' END                                                           AS limit_tier,
    NTILE(10) OVER (ORDER BY s.credit_limit, s.customer_id)                            AS credit_limit_decile,
    COALESCE(f.dq_duplicate_profile, FALSE)                                            AS dq_duplicate_profile,
    COALESCE(f.dq_status_balance_conflict, FALSE)                                      AS dq_status_balance_conflict,
    COALESCE(f.dq_never_used, FALSE)                                                   AS dq_never_used,
    COALESCE(f.dq_warning_count, 0)                                                    AS dq_warning_count
FROM staging.stg_credit_card_clients s
LEFT JOIN flags f USING (customer_id);

ALTER TABLE core.dim_customer ADD PRIMARY KEY (customer_id);
