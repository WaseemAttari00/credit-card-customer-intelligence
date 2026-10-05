-- "What if we can only contact X% of active customers?"
-- Cumulative expected net benefit when contacting customers in model-priority order, for three
-- contact costs (cheap digital nudge, mid, phone call / fee waiver), plus a random-order baseline.
-- Eligible = PD < 50% and positive risk-adjusted value (same exclusions as mart.retention_priority).
WITH params AS (
    SELECT MAX(value) FILTER (WHERE parameter = 'save_rate') AS save_rate
    FROM mart.assumptions
),
costs (contact_cost) AS (VALUES (10), (50), (100)),
eligible AS (
    SELECT r.customer_id, r.churn_score * p.save_rate * r.value_if_retained AS expected_gain
    FROM mart.retention_priority r
    CROSS JOIN params p
    WHERE r.pd_score < 0.5 AND r.est_risk_adjusted_contribution > 0
),
ranked AS (
    SELECT
        c.contact_cost,
        ROW_NUMBER() OVER (PARTITION BY c.contact_cost ORDER BY e.expected_gain DESC, e.customer_id) AS rnk,
        e.expected_gain - c.contact_cost                                                         AS net_benefit,
        AVG(e.expected_gain - c.contact_cost) OVER (PARTITION BY c.contact_cost)                AS avg_net_random
    FROM eligible e
    CROSS JOIN costs c
),
curve AS (
    SELECT
        contact_cost,
        rnk,
        SUM(net_benefit) OVER (PARTITION BY contact_cost ORDER BY rnk) AS cum_benefit_model,
        rnk * avg_net_random                                          AS cum_benefit_random
    FROM ranked
)
SELECT
    cv.contact_cost,
    cv.rnk                                                            AS customers_contacted,
    cv.rnk::NUMERIC / (SELECT COUNT(*) FROM mart.retention_priority)  AS share_of_active_contacted,
    cv.cum_benefit_model,
    cv.cum_benefit_random
FROM curve cv
WHERE cv.rnk % 5 = 0 OR cv.rnk = 1
ORDER BY cv.contact_cost, cv.rnk;
