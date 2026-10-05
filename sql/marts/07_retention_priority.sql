-- mart.retention_priority
-- Grain: one row per customer active in Sep 2005 (the customers the dormancy model scores).
-- PK: customer_id.
--
-- Decision logic (all parameters in mart.assumptions):
--   value_if_retained      = max(risk-adjusted monthly contribution, 0) x horizon_months
--   expected_value_at_risk = P(dormant) x value_if_retained
--   expected_net_benefit   = P(dormant) x save_rate x value_if_retained - contact_cost
--
-- Exclusions come first: customers with PD >= 50% go to risk management, not marketing, and
-- loss-making customers aren't worth paying to keep. The remaining customers with a positive
-- expected net benefit are ranked, and the top `budget_share` of active customers is "Contact now".
--
-- Caveat: the model predicts who is likely to go dormant, not who will respond to an offer
-- (uplift). save_rate is an assumption that should be measured with a holdout test.
DROP TABLE IF EXISTS mart.retention_priority CASCADE;

CREATE TABLE mart.retention_priority AS
WITH a AS (
    SELECT
        MAX(value) FILTER (WHERE parameter = 'horizon_months') AS horizon_months,
        MAX(value) FILTER (WHERE parameter = 'contact_cost')   AS contact_cost,
        MAX(value) FILTER (WHERE parameter = 'save_rate')      AS save_rate,
        MAX(value) FILTER (WHERE parameter = 'budget_share')   AS budget_share
    FROM mart.assumptions
),
base AS (
    SELECT
        c.customer_id,
        c.segment,
        c.limit_tier,
        c.age_band,
        c.churn_score,
        c.churn_decile,
        c.pd_score,
        c.pd_risk_band,
        c.est_monthly_revenue,
        c.est_contribution_before_losses,
        c.ecl_next_month,
        c.est_risk_adjusted_contribution,
        c.churn_drivers_up,
        c.pd_drivers_up,
        GREATEST(c.est_risk_adjusted_contribution, 0) * a.horizon_months                     AS value_if_retained,
        c.churn_score * GREATEST(c.est_risk_adjusted_contribution, 0) * a.horizon_months     AS expected_value_at_risk,
        c.churn_score * a.save_rate * GREATEST(c.est_risk_adjusted_contribution, 0) * a.horizon_months
            - a.contact_cost                                                                AS expected_net_benefit,
        (c.pd_score < 0.5 AND c.est_risk_adjusted_contribution > 0)                         AS passes_risk_and_value,
        a.budget_share
    FROM mart.customer_360 c
    CROSS JOIN a
    WHERE c.churn_score IS NOT NULL
),
ranked AS (
    SELECT
        b.*,
        COUNT(*) OVER ()                                                                    AS active_customers,
        ROW_NUMBER() OVER (ORDER BY (b.passes_risk_and_value AND b.expected_net_benefit > 0) DESC,
                                    b.expected_net_benefit DESC, b.customer_id)             AS priority_rank,
        PERCENT_RANK() OVER (ORDER BY b.est_risk_adjusted_contribution)                     AS value_pct_rank,
        PERCENT_RANK() OVER (ORDER BY b.churn_score)                                        AS churn_pct_rank
    FROM base b
)
SELECT
    r.customer_id,
    r.segment,
    r.limit_tier,
    r.age_band,
    r.churn_score,
    r.churn_decile,
    r.pd_score,
    r.pd_risk_band,
    r.est_monthly_revenue,
    r.est_contribution_before_losses,
    r.ecl_next_month,
    r.est_risk_adjusted_contribution,
    r.value_if_retained,
    r.expected_value_at_risk,
    r.expected_net_benefit,
    r.priority_rank,
    CASE WHEN r.pd_score >= 0.5                       THEN '5. Exclude - high credit risk'
         WHEN r.est_risk_adjusted_contribution <= 0   THEN '4. Exclude - not profitable'
         WHEN r.expected_net_benefit <= 0             THEN '3. Monitor - offer costs more than expected gain'
         WHEN r.priority_rank <= FLOOR(r.budget_share * r.active_customers) THEN '1. Contact now'
         ELSE '2. Next wave' END                                                            AS priority_tier,
    CASE WHEN r.value_pct_rank >= 0.6 AND r.churn_pct_rank >= 0.8 THEN 'High value / high dormancy risk'
         WHEN r.value_pct_rank >= 0.6                             THEN 'High value / lower dormancy risk'
         WHEN r.churn_pct_rank >= 0.8                             THEN 'Lower value / high dormancy risk'
         ELSE 'Lower value / lower dormancy risk' END                                       AS value_risk_quadrant,
    concat_ws(' | ',
        'P(dormant) ' || to_char(r.churn_score * 100, 'FM990.0') || '%',
        'PD ' || to_char(r.pd_score * 100, 'FM990') || '%',
        'risk-adj. value NT$' || to_char(r.est_risk_adjusted_contribution, 'FM999,990') || '/mo',
        'drivers: ' || NULLIF(r.churn_drivers_up, ''))                                      AS priority_reason
FROM ranked r;

ALTER TABLE mart.retention_priority ADD PRIMARY KEY (customer_id);
