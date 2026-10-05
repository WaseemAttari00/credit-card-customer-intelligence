-- core.fact_account_month
-- Grain: one row per customer per statement month (Apr-Sep 2005). PK: (customer_id, month_index).
-- 30,000 customers x 6 months = 180,000 rows.
--
-- The source is wide (one column per month). Here I unpivot it with CROSS JOIN LATERAL (VALUES ...)
-- and derive per-month measures. Only observed values and simple arithmetic on them go here;
-- anything that needs an assumption (interest, fees, profit) lives in the profitability mart.
--
-- Timing (checked in EDA): the payment recorded in month t pays the statement of month t-1.
-- So:  bill_t = bill_{t-1} - payment_t + new charges_t (purchases + interest + fees)
--  =>  est_new_charges_t = bill_t - bill_{t-1} + payment_t
DROP TABLE IF EXISTS core.fact_account_month CASCADE;

CREATE TABLE core.fact_account_month AS
WITH long AS (
    SELECT
        s.customer_id,
        s.credit_limit,
        v.month_index,
        v.status_code,
        v.bill_amount,
        v.payment_amount
    FROM staging.stg_credit_card_clients s
    CROSS JOIN LATERAL (VALUES
        (1, s.status_m1, s.bill_m1, s.payment_m1),
        (2, s.status_m2, s.bill_m2, s.payment_m2),
        (3, s.status_m3, s.bill_m3, s.payment_m3),
        (4, s.status_m4, s.bill_m4, s.payment_m4),
        (5, s.status_m5, s.bill_m5, s.payment_m5),
        (6, s.status_m6, s.bill_m6, s.payment_m6)
    ) AS v (month_index, status_code, bill_amount, payment_amount)
),
with_prev AS (
    SELECT
        l.*,
        LAG(l.bill_amount) OVER (PARTITION BY l.customer_id ORDER BY l.month_index) AS prev_bill_amount
    FROM long l
),
derived AS (
    SELECT
        w.*,
        w.bill_amount - w.prev_bill_amount + w.payment_amount AS est_new_charges   -- NULL in Apr (no Mar bill)
    FROM with_prev w
)
SELECT
    d.customer_id,
    d.month_index,
    m.month_key,
    m.month_start,
    d.credit_limit,
    d.bill_amount,
    d.prev_bill_amount,
    d.payment_amount,
    d.est_new_charges,
    d.status_code,
    CASE WHEN d.status_code = -2 THEN 'No consumption'
         WHEN d.status_code = -1 THEN 'Paid duly'
         WHEN d.status_code =  0 THEN 'Revolving (current)'
         ELSE d.status_code || ' month(s) late' END                       AS status_label,
    GREATEST(d.status_code, 0)                                            AS months_past_due,
    CASE WHEN d.status_code <= 0 THEN '0. Current'
         WHEN d.status_code = 1 THEN '1. 30 DPD'
         WHEN d.status_code = 2 THEN '2. 60 DPD'
         ELSE '3. 90+ DPD' END                                            AS delinquency_bucket,
    (d.status_code >= 1)                                                  AS is_delinquent,
    (d.status_code >= 2)                                                  AS is_delinquent_60plus,
    d.bill_amount / d.credit_limit                                        AS utilization,
    -- share of the previous statement that was paid this month (only defined if something was owed)
    CASE WHEN d.prev_bill_amount > 0 THEN d.payment_amount / d.prev_bill_amount END AS payment_ratio,
    (d.prev_bill_amount > 0 AND d.payment_amount >= d.prev_bill_amount)    AS paid_in_full,
    (d.prev_bill_amount > 0 AND d.payment_amount = 0)                      AS missed_payment,
    -- balance carried from last statement after this month's payment (interest-bearing balance)
    CASE WHEN d.prev_bill_amount IS NULL THEN NULL
         ELSE GREATEST(d.prev_bill_amount - d.payment_amount, 0) END      AS revolving_balance,
    (d.bill_amount < 0)                                                   AS has_credit_balance,
    (d.bill_amount > d.credit_limit)                                      AS is_over_limit,
    -- Inactive = nothing owed and no new charges. Undefined in Apr because charges need the Mar bill.
    CASE WHEN d.est_new_charges IS NULL THEN NULL
         ELSE (d.bill_amount <= 0 AND d.est_new_charges <= 0) END         AS is_inactive
FROM derived d
JOIN core.dim_month m ON m.month_index = d.month_index;

ALTER TABLE core.fact_account_month ADD PRIMARY KEY (customer_id, month_index);
ALTER TABLE core.fact_account_month
    ADD CONSTRAINT fk_fam_customer FOREIGN KEY (customer_id) REFERENCES core.dim_customer (customer_id),
    ADD CONSTRAINT fk_fam_month    FOREIGN KEY (month_index) REFERENCES core.dim_month (month_index);
CREATE INDEX ON core.fact_account_month (month_index);
