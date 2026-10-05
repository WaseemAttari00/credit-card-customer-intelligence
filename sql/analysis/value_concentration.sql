-- Pareto view: what share of total estimated contribution comes from the top X% of customers?
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
FROM by_pct
ORDER BY percentile;
