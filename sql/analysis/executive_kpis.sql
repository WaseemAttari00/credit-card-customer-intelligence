-- Headline KPIs as of Sep 2005 (monthly NTD figures are estimates; see mart.assumptions).
SELECT
    COUNT(*)                                                            AS customers,
    COUNT(churn_score)                                                  AS active_customers_sep,   -- balance or new charges in Sep
    SUM(GREATEST(balance_sep, 0))                                       AS total_balance_sep,
    SUM(credit_limit)                                                   AS total_credit_limit,
    SUM(GREATEST(balance_sep, 0)) / SUM(credit_limit)                   AS portfolio_utilization,
    SUM(est_monthly_purchases)                                          AS est_monthly_purchase_volume,
    SUM(est_monthly_revenue)                                            AS est_monthly_revenue,
    SUM(est_contribution_before_losses)                                 AS est_monthly_contribution_before_losses,
    SUM(ecl_next_month)                                                 AS ecl_next_month,
    SUM(est_risk_adjusted_contribution)                                 AS est_monthly_risk_adj_contribution,
    AVG(actual_default_oct::INT)                                        AS actual_default_rate_oct,
    AVG(pd_score)                                                       AS avg_predicted_pd,
    AVG(churn_score)                                                    AS avg_predicted_dormancy,
    AVG((est_risk_adjusted_contribution < 0)::INT)                      AS share_loss_making
FROM mart.customer_360;
