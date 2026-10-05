-- mart.portfolio_monthly
-- Grain: one row per statement month. PK: month_index.
-- Portfolio-level KPIs used for the executive trend charts.
-- Note: Sep 2005 delinquency is not comparable with Apr-Aug because of the status coding
-- change in the source data (flag column sep_status_coding_differs).
DROP TABLE IF EXISTS mart.portfolio_monthly CASCADE;

CREATE TABLE mart.portfolio_monthly AS
WITH transitions AS (
    SELECT
        f.*,
        LAG(f.is_delinquent_60plus) OVER (PARTITION BY f.customer_id ORDER BY f.month_index) AS prev_60plus
    FROM core.fact_account_month f
),
monthly AS (
    SELECT
        t.month_index,
        COUNT(*)                                                         AS accounts,
        COUNT(*) FILTER (WHERE t.is_inactive = FALSE)                    AS active_accounts,
        COUNT(*) FILTER (WHERE t.is_inactive)                            AS inactive_accounts,
        SUM(GREATEST(t.bill_amount, 0))                                  AS total_balance,
        SUM(t.credit_limit)                                              AS total_limit,
        SUM(t.payment_amount)                                            AS total_payments,
        SUM(GREATEST(t.est_new_charges, 0))                              AS total_new_charges,
        AVG(t.utilization)                                               AS avg_utilization,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.utilization)       AS median_utilization,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY LEAST(t.payment_ratio, 1)) AS median_payment_ratio,
        AVG(t.is_delinquent::INT)                                        AS delinquency_rate_any,
        AVG(t.is_delinquent_60plus::INT)                                 AS delinquency_rate_60plus,
        COUNT(*) FILTER (WHERE t.is_delinquent_60plus AND NOT t.prev_60plus) AS new_60plus_accounts,
        COUNT(*) FILTER (WHERE NOT t.is_delinquent AND t.prev_60plus)        AS cured_from_60plus,
        AVG(t.paid_in_full::INT) FILTER (WHERE t.prev_bill_amount > 0)       AS paid_in_full_rate
    FROM transitions t
    GROUP BY t.month_index
)
SELECT
    m.month_index,
    d.month_start,
    d.month_label,
    m.accounts,
    CASE WHEN m.month_index = 1 THEN NULL ELSE m.active_accounts END    AS active_accounts,   -- undefined in Apr
    CASE WHEN m.month_index = 1 THEN NULL ELSE m.active_accounts::NUMERIC / m.accounts END AS active_rate,
    m.total_balance,
    m.total_limit,
    m.total_balance / m.total_limit                                     AS portfolio_utilization,
    m.total_payments,
    CASE WHEN m.month_index = 1 THEN NULL ELSE m.total_new_charges END  AS total_new_charges,
    m.avg_utilization,
    m.median_utilization,
    m.median_payment_ratio,
    m.paid_in_full_rate,
    m.delinquency_rate_any,
    m.delinquency_rate_60plus,
    m.new_60plus_accounts,
    m.cured_from_60plus,
    m.total_balance - LAG(m.total_balance) OVER (ORDER BY m.month_index) AS balance_mom_change,
    (m.month_index = 6)                                                 AS sep_status_coding_differs
FROM monthly m
JOIN core.dim_month d USING (month_index);

ALTER TABLE mart.portfolio_monthly ADD PRIMARY KEY (month_index);
