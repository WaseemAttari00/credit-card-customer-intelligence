-- Two small curve tables used by the dashboard (and by sql/analysis), materialized so that
-- Power BI reads them like any other table.

-- mart.retention_budget_curve
-- Grain: one row per (contact_cost, customers_contacted), every 5th customer, up to 10% of active customers.
-- Cumulative expected net benefit of contacting eligible customers in model-priority order, for three
-- contact costs, plus a random-order baseline. Eligible = PD < 50% and positive risk-adjusted value
-- (same exclusions as mart.retention_priority).
DROP TABLE IF EXISTS mart.retention_budget_curve CASCADE;

CREATE TABLE mart.retention_budget_curve AS
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
    'NT$' || cv.contact_cost || ' per contact'                        AS contact_cost_label,
    cv.rnk                                                            AS customers_contacted,
    cv.rnk::NUMERIC / (SELECT COUNT(*) FROM mart.retention_priority)  AS share_of_active_contacted,
    cv.cum_benefit_model,
    cv.cum_benefit_random
FROM curve cv
WHERE (cv.rnk % 5 = 0 OR cv.rnk = 1)
  AND cv.rnk <= 0.10 * (SELECT COUNT(*) FROM mart.retention_priority);

ALTER TABLE mart.retention_budget_curve ADD PRIMARY KEY (contact_cost, customers_contacted);


-- mart.value_concentration
-- Grain: one row per percentile of customers (1 = top 1% by estimated risk-adjusted contribution).
-- Pareto view: what share of total estimated contribution comes from the top X% of customers?
DROP TABLE IF EXISTS mart.value_concentration CASCADE;

CREATE TABLE mart.value_concentration AS
WITH pct AS (
    SELECT
        customer_id,
        est_risk_adjusted_contribution AS v,
        NTILE(100) OVER (ORDER BY est_risk_adjusted_contribution DESC) AS percentile
    FROM mart.customer_360
),
by_pct AS (
    SELECT percentile, SUM(v) AS contribution, COUNT(*) AS customers
    FROM pct
    GROUP BY percentile
)
SELECT
    percentile                                                        AS top_percent_of_customers,
    contribution,
    SUM(contribution) OVER (ORDER BY percentile)                      AS cum_contribution,
    SUM(contribution) OVER (ORDER BY percentile) / SUM(contribution) OVER () AS cum_share_of_total
FROM by_pct;

ALTER TABLE mart.value_concentration ADD PRIMARY KEY (top_percent_of_customers);
