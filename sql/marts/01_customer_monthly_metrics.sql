-- mart.customer_monthly_metrics
-- Grain: one row per customer per statement month. PK: (customer_id, month_index).
-- Adds rolling and running metrics on top of core.fact_account_month for trend analysis.
DROP TABLE IF EXISTS mart.customer_monthly_metrics CASCADE;

CREATE TABLE mart.customer_monthly_metrics AS
SELECT
    f.customer_id,
    f.month_index,
    f.month_start,
    f.credit_limit,
    f.bill_amount,
    f.payment_amount,
    f.est_new_charges,
    f.utilization,
    f.payment_ratio,
    f.delinquency_bucket,
    f.is_delinquent,
    f.is_inactive,
    -- rolling 3-month metrics (fewer months at the start of the window)
    AVG(f.utilization)     OVER w3                                         AS util_3m_avg,
    SUM(f.payment_amount)  OVER w3                                         AS payments_3m,
    AVG(f.est_new_charges) OVER w3                                         AS new_charges_3m_avg,
    -- month-over-month change
    f.bill_amount - LAG(f.bill_amount) OVER w                              AS bill_mom_change,
    (f.bill_amount - LAG(f.bill_amount) OVER w) / NULLIF(ABS(LAG(f.bill_amount) OVER w), 0) AS bill_mom_pct,
    LAG(f.delinquency_bucket) OVER w                                       AS prev_delinquency_bucket,
    -- running totals since Apr
    SUM(f.is_delinquent::INT) OVER (w ROWS UNBOUNDED PRECEDING)            AS delinquent_months_to_date,
    -- where this customer's balance ranks among all customers that month (0 = lowest, 1 = highest)
    PERCENT_RANK() OVER (PARTITION BY f.month_index ORDER BY f.bill_amount) AS bill_percentile_in_month
FROM core.fact_account_month f
WINDOW w  AS (PARTITION BY f.customer_id ORDER BY f.month_index),
       w3 AS (PARTITION BY f.customer_id ORDER BY f.month_index ROWS BETWEEN 2 PRECEDING AND CURRENT ROW);

ALTER TABLE mart.customer_monthly_metrics ADD PRIMARY KEY (customer_id, month_index);
