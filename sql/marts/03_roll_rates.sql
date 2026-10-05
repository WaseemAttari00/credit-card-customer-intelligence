-- mart.roll_rates
-- Grain: one row per (from_month, from_bucket, to_bucket). PK: (from_month_index, from_bucket, to_bucket).
-- Roll-rate (transition) matrix between delinquency buckets from one month to the next.
-- Standard credit-risk view: of the accounts 30 DPD this month, what share rolls to 60 DPD next month?
DROP TABLE IF EXISTS mart.roll_rates CASCADE;

CREATE TABLE mart.roll_rates AS
WITH pairs AS (
    SELECT
        f.month_index                                                            AS from_month_index,
        f.delinquency_bucket                                                     AS from_bucket,
        LEAD(f.delinquency_bucket) OVER (PARTITION BY f.customer_id ORDER BY f.month_index) AS to_bucket
    FROM core.fact_account_month f
),
counts AS (
    SELECT from_month_index, from_bucket, to_bucket, COUNT(*) AS accounts
    FROM pairs
    WHERE to_bucket IS NOT NULL
    GROUP BY 1, 2, 3
)
SELECT
    c.from_month_index,
    d.month_label                                                                AS from_month,
    c.from_bucket,
    c.to_bucket,
    c.accounts,
    c.accounts::NUMERIC / SUM(c.accounts) OVER (PARTITION BY c.from_month_index, c.from_bucket) AS share_of_from_bucket,
    CASE WHEN c.to_bucket > c.from_bucket THEN 'Roll forward'
         WHEN c.to_bucket < c.from_bucket THEN 'Roll back / cure'
         ELSE 'Stay' END                                                         AS movement,
    (c.from_month_index = 5)                                                     AS into_sep_coding_change
FROM counts c
JOIN core.dim_month d ON d.month_index = c.from_month_index;

ALTER TABLE mart.roll_rates ADD PRIMARY KEY (from_month_index, from_bucket, to_bucket);
