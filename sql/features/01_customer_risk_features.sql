-- features.customer_risk_features
-- Grain: one row per customer, as of the end of Sep 2005. PK: customer_id.
-- Observation window: Apr-Sep 2005 (all six statement months).
-- Used by the probability-of-default (PD) model, whose target is a missed payment in Oct 2005.
-- Reads only core.fact_account_month and core.dim_customer, never the label table,
-- so the target cannot leak into the features.
DROP TABLE IF EXISTS features.customer_risk_features CASCADE;

CREATE TABLE features.customer_risk_features AS
WITH agg AS (
    SELECT
        f.customer_id,
        -- repayment status / delinquency history
        MAX(f.status_code) FILTER (WHERE f.month_index = 6)                  AS status_latest,
        MAX(f.status_code) FILTER (WHERE f.month_index = 5)                  AS status_prev,
        MAX(f.status_code)                                                   AS max_status_6m,
        COUNT(*) FILTER (WHERE f.is_delinquent)                              AS months_delinquent_6m,
        COUNT(*) FILTER (WHERE f.is_delinquent_60plus)                       AS months_60plus_6m,
        MAX(f.month_index) FILTER (WHERE f.is_delinquent)                    AS last_delinquent_month,
        MAX(f.month_index) FILTER (WHERE NOT f.is_delinquent)                AS last_current_month,
        -- balances and utilization
        MAX(f.utilization) FILTER (WHERE f.month_index = 6)                  AS util_latest,
        AVG(f.utilization)                                                   AS util_avg_6m,
        MAX(f.utilization)                                                   AS util_max_6m,
        REGR_SLOPE(f.utilization, f.month_index)                             AS util_trend_6m,     -- change per month
        MAX(f.bill_amount) FILTER (WHERE f.month_index = 6)                  AS bill_latest,
        AVG(f.bill_amount)                                                   AS bill_avg_6m,
        COUNT(*) FILTER (WHERE f.is_over_limit)                              AS months_over_limit,
        COUNT(*) FILTER (WHERE f.has_credit_balance)                         AS months_credit_balance,
        -- payment behaviour (months 2-6, where a previous bill exists)
        AVG(LEAST(f.payment_ratio, 1))                                       AS payment_ratio_avg,  -- capped at 1 so overpayments don't dominate
        MAX(LEAST(f.payment_ratio, 1)) FILTER (WHERE f.month_index = 6)      AS payment_ratio_latest,
        MIN(LEAST(f.payment_ratio, 1))                                       AS payment_ratio_min,
        COUNT(*) FILTER (WHERE f.paid_in_full)                               AS months_paid_in_full,
        COUNT(*) FILTER (WHERE f.missed_payment)                             AS months_missed_payment,
        SUM(f.payment_amount)                                                AS payment_total_6m,
        MAX(f.payment_amount) FILTER (WHERE f.month_index = 6)               AS payment_latest,
        -- activity
        AVG(f.est_new_charges)                                               AS new_charges_avg,
        REGR_SLOPE(f.est_new_charges, f.month_index)                         AS new_charges_trend,
        COUNT(*) FILTER (WHERE f.is_inactive)                                AS months_inactive_5m
    FROM core.fact_account_month f
    GROUP BY f.customer_id
)
SELECT
    c.customer_id,
    c.credit_limit,
    c.age,
    c.sex,
    c.education,
    c.marital_status,
    a.status_latest,
    a.status_prev,
    a.max_status_6m,
    a.months_delinquent_6m,
    a.months_60plus_6m,
    -- months since the account was last delinquent (0 = delinquent now, 6 = never in the window)
    COALESCE(6 - a.last_delinquent_month, 6)                                 AS months_since_delinquent,
    -- consecutive delinquent months up to Sep (trailing run length)
    6 - COALESCE(a.last_current_month, 0)                                    AS delinquent_streak,
    a.util_latest,
    a.util_avg_6m,
    a.util_max_6m,
    COALESCE(a.util_trend_6m, 0)                                             AS util_trend_6m,
    a.bill_latest,
    a.bill_avg_6m,
    a.months_over_limit,
    a.months_credit_balance,
    a.payment_ratio_avg,          -- NULL when nothing was owed in any month
    a.payment_ratio_latest,
    a.payment_ratio_min,
    a.months_paid_in_full,
    a.months_missed_payment,
    a.payment_total_6m,
    a.payment_latest,
    a.payment_total_6m / c.credit_limit                                      AS payment_to_limit_6m,
    a.new_charges_avg,
    a.new_charges_avg / c.credit_limit                                       AS new_charges_to_limit,
    COALESCE(a.new_charges_trend, 0)                                         AS new_charges_trend,
    a.months_inactive_5m,
    GREATEST(c.credit_limit - GREATEST(a.bill_latest, 0), 0)                 AS available_credit
FROM agg a
JOIN core.dim_customer c USING (customer_id);

ALTER TABLE features.customer_risk_features ADD PRIMARY KEY (customer_id);
