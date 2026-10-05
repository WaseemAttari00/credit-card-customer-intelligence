-- mart.customer_profitability
-- Grain: one row per customer. PK: customer_id.
--
-- An EDUCATIONAL estimate of monthly customer contribution, not accounting profit.
-- Column prefixes make the provenance explicit:
--   obs_  observed in the data
--   est_  calculated from observed data + assumptions in mart.assumptions
--   pd_ / ecl_ / ead_  model-based
--
-- Monthly figures are averages over May-Sep 2005 (April has no previous bill, so interest
-- and purchases can't be estimated for it).
--
-- new charges_t = purchases_t + interest_t + late fee_t  (bill identity from core.fact_account_month)
--   interest_t  = revolving balance carried into month t x APR / 12
--   purchases_t = new charges - interest - late fee   (floored at 0)
--
-- Expected credit loss (next month) = PD x EAD x LGD x chargeoff_share, where chargeoff_share
-- (share of the dataset's broad "default" events that become real losses) is calibrated so that
-- 12 x total ECL / total balance = target_annual_loss_rate. See config.yaml for the reasoning.
DROP TABLE IF EXISTS mart.customer_profitability CASCADE;

CREATE TABLE mart.customer_profitability AS
WITH a AS (
    SELECT
        MAX(value) FILTER (WHERE parameter = 'apr')                    AS apr,
        MAX(value) FILTER (WHERE parameter = 'interchange_rate')       AS interchange_rate,
        MAX(value) FILTER (WHERE parameter = 'late_fee')               AS late_fee,
        MAX(value) FILTER (WHERE parameter = 'rewards_rate')           AS rewards_rate,
        MAX(value) FILTER (WHERE parameter = 'cost_of_funds_annual')   AS cost_of_funds_annual,
        MAX(value) FILTER (WHERE parameter = 'servicing_cost_monthly') AS servicing_cost_monthly,
        MAX(value) FILTER (WHERE parameter = 'ccf')                    AS ccf,
        MAX(value) FILTER (WHERE parameter = 'lgd')                    AS lgd,
        MAX(value) FILTER (WHERE parameter = 'target_annual_loss_rate') AS target_annual_loss_rate
    FROM mart.assumptions
),
monthly AS (
    SELECT
        f.customer_id,
        f.month_index,
        f.bill_amount,
        f.est_new_charges,
        f.revolving_balance * a.apr / 12                              AS est_interest,
        CASE WHEN f.is_delinquent THEN a.late_fee ELSE 0 END          AS est_late_fee
    FROM core.fact_account_month f
    CROSS JOIN a
    WHERE f.month_index >= 2
),
agg AS (
    SELECT
        m.customer_id,
        AVG(m.est_interest)                                                              AS est_interest_income,
        AVG(m.est_late_fee)                                                              AS est_fee_income,
        AVG(GREATEST(m.est_new_charges - m.est_interest - m.est_late_fee, 0))            AS est_monthly_purchases,
        AVG(GREATEST(m.bill_amount, 0))                                                  AS obs_avg_balance
    FROM monthly m
    GROUP BY m.customer_id
),
latest AS (
    SELECT customer_id, bill_amount AS obs_balance_sep
    FROM core.fact_account_month
    WHERE month_index = 6
),
calc AS (
    SELECT
        c.customer_id,
        c.credit_limit                                                    AS obs_credit_limit,
        l.obs_balance_sep,
        g.obs_avg_balance,
        g.est_monthly_purchases,
        -- revenue
        g.est_interest_income,
        g.est_monthly_purchases * a.interchange_rate                      AS est_interchange_income,
        g.est_fee_income,
        -- costs
        g.est_monthly_purchases * a.rewards_rate                          AS est_rewards_cost,
        g.obs_avg_balance * a.cost_of_funds_annual / 12                   AS est_funding_cost,
        a.servicing_cost_monthly                                          AS est_servicing_cost,
        -- credit risk (next-month expected loss)
        p.pd_score,
        GREATEST(l.obs_balance_sep, 0)
            + a.ccf * GREATEST(c.credit_limit - GREATEST(l.obs_balance_sep, 0), 0) AS ead_estimate,
        a.lgd
    FROM core.dim_customer c
    JOIN agg g      USING (customer_id)
    JOIN latest l   USING (customer_id)
    JOIN ml.pd_scores p USING (customer_id)
    CROSS JOIN a
),
calibration AS (
    -- top-down calibration of the charge-off share to the target annual loss rate
    SELECT MAX(a.target_annual_loss_rate) * SUM(GREATEST(k.obs_balance_sep, 0))
           / (12 * SUM(k.pd_score * k.ead_estimate * k.lgd))              AS chargeoff_share
    FROM calc k CROSS JOIN a
),
calc2 AS (
    SELECT k.*, cb.chargeoff_share, k.lgd * cb.chargeoff_share AS effective_loss_rate
    FROM calc k CROSS JOIN calibration cb
)
SELECT
    k.*,
    k.est_interest_income + k.est_interchange_income + k.est_fee_income                AS est_monthly_revenue,
    k.est_rewards_cost + k.est_funding_cost + k.est_servicing_cost                     AS est_monthly_operating_cost,
    k.est_interest_income + k.est_interchange_income + k.est_fee_income
        - k.est_rewards_cost - k.est_funding_cost - k.est_servicing_cost               AS est_contribution_before_losses,
    k.pd_score * k.ead_estimate * k.effective_loss_rate                                AS ecl_next_month,
    k.est_interest_income + k.est_interchange_income + k.est_fee_income
        - k.est_rewards_cost - k.est_funding_cost - k.est_servicing_cost
        - k.pd_score * k.ead_estimate * k.effective_loss_rate                          AS est_risk_adjusted_contribution
FROM calc2 k;

ALTER TABLE mart.customer_profitability ADD PRIMARY KEY (customer_id);

-- value band on the risk-adjusted figure (quintiles of the whole book, plus an explicit loss flag)
ALTER TABLE mart.customer_profitability ADD COLUMN value_quintile INT, ADD COLUMN profitability_band TEXT;
WITH q AS (
    SELECT customer_id, NTILE(5) OVER (ORDER BY est_risk_adjusted_contribution DESC) AS qn
    FROM mart.customer_profitability
)
UPDATE mart.customer_profitability p
SET value_quintile = q.qn,
    profitability_band = CASE WHEN p.est_risk_adjusted_contribution < 0 THEN '4. Loss-making'
                              WHEN q.qn = 1 THEN '1. Top 20% value'
                              WHEN q.qn <= 3 THEN '2. Middle value'
                              ELSE '3. Low value' END
FROM q
WHERE q.customer_id = p.customer_id;
