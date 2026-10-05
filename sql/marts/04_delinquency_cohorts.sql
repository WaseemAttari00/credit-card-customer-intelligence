-- mart.delinquency_cohorts
-- Grain: one row per (cohort_month, months_since_entry). PK: (cohort_month_index, months_since_entry).
--
-- Cohort = the month an account FIRST reached 60+ days past due. I track what happens to each
-- cohort in the following months (still delinquent, cured, worse) and its Oct default rate.
-- Accounts already 60+ DPD in April are excluded: their delinquency may have started before
-- the data begins (left-censoring), so their "first" month is unknown.
DROP TABLE IF EXISTS mart.delinquency_cohorts CASCADE;

CREATE TABLE mart.delinquency_cohorts AS
WITH first_60 AS (
    SELECT customer_id, MIN(month_index) AS cohort_month_index
    FROM core.fact_account_month
    WHERE is_delinquent_60plus
    GROUP BY customer_id
),
cohort_members AS (
    SELECT f60.*
    FROM first_60 f60
    WHERE f60.cohort_month_index > 1
),
followup AS (
    SELECT
        cm.cohort_month_index,
        f.month_index - cm.cohort_month_index AS months_since_entry,
        f.customer_id,
        f.status_code,
        f.is_delinquent,
        f.is_delinquent_60plus
    FROM cohort_members cm
    JOIN core.fact_account_month f
      ON f.customer_id = cm.customer_id AND f.month_index >= cm.cohort_month_index
)
SELECT
    fu.cohort_month_index,
    d.month_label                                                       AS cohort_month,
    fu.months_since_entry,
    COUNT(*)                                                            AS accounts,
    AVG(fu.is_delinquent_60plus::INT)                                   AS share_still_60plus,
    AVG((fu.status_code >= 3)::INT)                                     AS share_90plus,
    AVG((NOT fu.is_delinquent)::INT)                                    AS share_cured,
    AVG(o.defaulted_next_month::INT)                                    AS oct_default_rate
FROM followup fu
JOIN core.fact_default_outcome o USING (customer_id)
JOIN core.dim_month d ON d.month_index = fu.cohort_month_index
GROUP BY fu.cohort_month_index, d.month_label, fu.months_since_entry;

ALTER TABLE mart.delinquency_cohorts ADD PRIMARY KEY (cohort_month_index, months_since_entry);
