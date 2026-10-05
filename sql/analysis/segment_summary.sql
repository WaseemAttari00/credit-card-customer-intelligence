-- One row per behavioural segment: size, risk, dormancy, and estimated economics.
SELECT
    c.segment,
    COUNT(*)                                                         AS customers,
    COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER ()                        AS share_of_customers,
    AVG(c.credit_limit)                                              AS avg_credit_limit,
    AVG(c.utilization_avg_6m)                                        AS avg_utilization,
    AVG(c.payment_ratio_avg)                                         AS avg_payment_ratio,
    AVG(c.est_monthly_purchases)                                     AS avg_monthly_purchases,
    AVG(c.actual_default_oct::INT)                                   AS actual_default_rate,
    AVG(c.pd_score)                                                  AS avg_pd,
    AVG(c.churn_score)                                               AS avg_dormancy_score,     -- active customers only
    COUNT(c.churn_score)                                             AS active_in_sep,
    SUM(c.est_monthly_revenue)                                       AS total_monthly_revenue,
    SUM(c.ecl_next_month)                                            AS total_ecl,
    SUM(c.est_risk_adjusted_contribution)                            AS total_risk_adj_contribution,
    AVG(c.est_risk_adjusted_contribution)                            AS avg_risk_adj_contribution,
    AVG((c.est_risk_adjusted_contribution < 0)::INT)                 AS share_loss_making,
    SUM(c.est_risk_adjusted_contribution)
        / SUM(SUM(c.est_risk_adjusted_contribution)) OVER ()         AS share_of_total_contribution
FROM mart.customer_360 c
GROUP BY c.segment
ORDER BY avg_risk_adj_contribution DESC;
