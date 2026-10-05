-- How sensitive is estimated profitability to the loss-rate assumption?
-- ECL scales linearly with the target annual loss rate, so each scenario rescales it.
WITH base AS (
    SELECT value AS base_rate FROM mart.assumptions WHERE parameter = 'target_annual_loss_rate'
),
scenarios (annual_loss_rate) AS (VALUES (0.04), (0.08), (0.12))
SELECT
    s.annual_loss_rate,
    SUM(p.est_monthly_revenue)                                                            AS monthly_revenue,
    SUM(p.ecl_next_month * s.annual_loss_rate / b.base_rate)                              AS monthly_ecl,
    SUM(p.est_contribution_before_losses - p.ecl_next_month * s.annual_loss_rate / b.base_rate) AS monthly_risk_adj_contribution,
    AVG((p.est_contribution_before_losses - p.ecl_next_month * s.annual_loss_rate / b.base_rate < 0)::INT) AS share_loss_making
FROM mart.customer_profitability p
CROSS JOIN base b
CROSS JOIN scenarios s
GROUP BY s.annual_loss_rate
ORDER BY s.annual_loss_rate;
