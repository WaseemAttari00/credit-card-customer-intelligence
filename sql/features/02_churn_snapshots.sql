-- features.churn_snapshots
-- Grain: one row per customer per snapshot month (May-Sep 2005). PK: (customer_id, snapshot_month_index).
--
-- Churn here means "silent attrition": the account goes dormant (nothing owed and no new
-- charges) for BOTH of the next two months. The data has no account-closure field, and
-- inactivity is the usual proxy card issuers use for attrition.
--
--   lookback window  : snapshot month t and t-1   -> features
--   prediction window: months t+1 and t+2         -> label
--
-- Only customers active in month t are eligible (you can't lose a customer who is
-- already dormant). Label is NULL when t+2 is past Sep 2005 (snapshots Aug and Sep).
-- Status codes are only used as a 60+ DPD flag because the Sep status coding differs
-- from earlier months (see docs/data_quality_report.md); the Sep snapshot is what we score.
DROP TABLE IF EXISTS features.churn_snapshots CASCADE;

CREATE TABLE features.churn_snapshots AS
WITH w AS (
    SELECT
        f.customer_id,
        f.month_index,
        f.credit_limit,
        f.bill_amount,
        f.payment_amount,
        f.est_new_charges,
        f.payment_ratio,
        f.paid_in_full,
        f.is_delinquent_60plus,
        f.is_inactive,
        f.utilization,
        LAG(f.bill_amount)          OVER w AS bill_prev,
        LAG(f.payment_amount)       OVER w AS payment_prev,
        LAG(f.utilization)          OVER w AS util_prev,
        LAG(f.is_delinquent_60plus) OVER w AS delinquent_60plus_prev,
        LEAD(f.is_inactive, 1)      OVER w AS inactive_next1,
        LEAD(f.is_inactive, 2)      OVER w AS inactive_next2
    FROM core.fact_account_month f
    WINDOW w AS (PARTITION BY f.customer_id ORDER BY f.month_index)
)
SELECT
    w.customer_id,
    w.month_index                                               AS snapshot_month_index,
    NOT w.is_inactive                                           AS is_eligible,      -- active at snapshot
    -- label: dormant in both following months (NULL if the window runs past the data)
    CASE WHEN w.inactive_next2 IS NULL THEN NULL
         ELSE (w.inactive_next1 AND w.inactive_next2)::INT END  AS churn_label,
    -- account / customer attributes
    w.credit_limit,
    c.age,
    c.sex,
    c.education,
    c.marital_status,
    -- lookback features (months t and t-1 only)
    w.bill_amount                                               AS bill_t,
    w.bill_prev                                                 AS bill_tm1,
    w.utilization                                               AS util_t,
    w.util_prev                                                 AS util_tm1,
    w.utilization - w.util_prev                                 AS util_change,
    w.payment_amount                                            AS payment_t,
    w.payment_prev                                              AS payment_tm1,
    w.payment_amount / w.credit_limit                           AS payment_to_limit_t,
    w.est_new_charges                                           AS charges_t,
    w.est_new_charges / w.credit_limit                          AS charges_to_limit_t,
    LEAST(w.payment_ratio, 1)                                   AS payment_ratio_t,
    COALESCE(w.paid_in_full, FALSE)::INT                        AS paid_in_full_t,
    (w.bill_amount <= 0)::INT                                   AS zero_balance_t,
    (w.bill_prev <= 0)::INT                                     AS zero_balance_tm1,
    -- balance runoff: how much of last month's balance is left (low = paying down towards zero)
    CASE WHEN w.bill_prev > 0 THEN w.bill_amount / w.bill_prev END AS balance_retention_t,
    w.is_delinquent_60plus::INT                                 AS delinquent_60plus_t,
    w.delinquent_60plus_prev::INT                               AS delinquent_60plus_tm1
FROM w
JOIN core.dim_customer c USING (customer_id)
WHERE w.month_index >= 2;      -- Apr has no previous month, so no lookback

ALTER TABLE features.churn_snapshots ADD PRIMARY KEY (customer_id, snapshot_month_index);
