-- How many customers are worth a retention contact at different contact costs / save rates?
-- (e.g. NT$10 for an app push or email, NT$100 for a call or fee waiver)
WITH scenarios (contact_cost, save_rate) AS (
    VALUES (10, 0.10), (10, 0.20), (50, 0.20), (100, 0.20), (100, 0.30)
)
SELECT
    s.contact_cost,
    s.save_rate,
    COUNT(*) FILTER (WHERE r.churn_score * s.save_rate * r.value_if_retained - s.contact_cost > 0
                     AND r.pd_score < 0.5)                                                      AS customers_worth_contacting,
    SUM(GREATEST(r.churn_score * s.save_rate * r.value_if_retained - s.contact_cost, 0))
        FILTER (WHERE r.pd_score < 0.5)                                                         AS total_expected_net_benefit
FROM mart.retention_priority r
CROSS JOIN scenarios s
GROUP BY s.contact_cost, s.save_rate
ORDER BY s.contact_cost, s.save_rate;
