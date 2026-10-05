-- mart.customer_360
-- Grain: one row per customer. PK: customer_id.
-- One wide table with everything known about a customer as of Sep 2005: profile,
-- behavior, segment, model scores, estimated profitability and (separately labeled)
-- the actual Oct outcome. This is the main table behind the Power BI customer pages.
DROP TABLE IF EXISTS mart.customer_360 CASCADE;

CREATE TABLE mart.customer_360 AS
SELECT
    -- profile (observed)
    c.customer_id,
    c.sex,
    c.age,
    c.age_band,
    c.education,
    c.marital_status,
    c.credit_limit,
    c.limit_tier,
    -- behavior (observed / calculated from observed)
    r.bill_latest                       AS balance_sep,
    r.util_latest                       AS utilization_sep,
    r.util_avg_6m                       AS utilization_avg_6m,
    r.util_trend_6m                     AS utilization_trend,
    r.payment_ratio_avg,
    r.months_paid_in_full,
    r.payment_total_6m,
    r.new_charges_avg                   AS est_new_charges_avg,
    r.new_charges_trend                 AS est_new_charges_trend,
    r.months_inactive_5m,
    r.status_latest,
    r.max_status_6m,
    r.months_delinquent_6m,
    r.months_60plus_6m,
    -- 60+ DPD used (not status >= 1) because of the Sep status-coding change
    CASE WHEN r.status_latest >= 2 THEN 'Delinquent (60+ DPD)'
         WHEN r.months_inactive_5m = 5 THEN 'Inactive May-Sep'
         WHEN cs.customer_id IS NULL THEN 'Dormant in Sep'
         ELSE 'Active' END              AS account_state_sep,
    -- segment (model: K-means)
    s.segment,
    -- credit risk (model: PD, out-of-fold)
    p.pd_score,
    p.pd_decile,
    p.pd_risk_band,
    p.pd_drivers_up,
    p.pd_drivers_down,
    -- dormancy risk (model; only for customers active in Sep)
    cs.churn_score,
    cs.churn_decile,
    cs.churn_drivers_up,
    cs.churn_drivers_down,
    -- profitability (calculated + modeled; monthly NTD)
    pr.est_monthly_purchases,
    pr.est_monthly_revenue,
    pr.est_monthly_operating_cost,
    pr.est_contribution_before_losses,
    pr.ead_estimate,
    pr.ecl_next_month,
    pr.est_risk_adjusted_contribution,
    pr.value_quintile,
    pr.profitability_band,
    -- actual outcome (observed after the scoring date; for evaluation views only)
    o.defaulted_next_month              AS actual_default_oct,
    -- data-quality flags
    c.dq_duplicate_profile,
    c.dq_status_balance_conflict,
    c.dq_warning_count
FROM core.dim_customer c
JOIN features.customer_risk_features r USING (customer_id)
JOIN ml.customer_segments s            USING (customer_id)
JOIN ml.pd_scores p                    USING (customer_id)
JOIN mart.customer_profitability pr    USING (customer_id)
JOIN core.fact_default_outcome o       USING (customer_id)
LEFT JOIN ml.churn_scores cs           USING (customer_id);

ALTER TABLE mart.customer_360 ADD PRIMARY KEY (customer_id);
