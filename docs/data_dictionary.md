# Data model and data dictionary

All tables live in one PostgreSQL database (`cc_intel`). Schemas follow the flow of the data:

```
raw  ->  staging  ->  core  ->  features  ->  ml  ->  mart
                 \-> audit (run log + data-quality results)
```

Months are indexed `1 = Apr 2005 ... 6 = Sep 2005`; `7 = Oct 2005` is the month the default label refers to.
Amounts are in New Taiwan dollars (NTD).

## Entity overview

```
             core.dim_month (month_index)
                    |
core.dim_customer --+-- core.fact_account_month (customer_id, month_index)
  (customer_id)     |
                    +-- core.fact_default_outcome (customer_id)   <- label, kept separate
                    +-- features.customer_risk_features (customer_id)
                    +-- features.churn_snapshots (customer_id, snapshot_month_index)
                    +-- ml.pd_scores / ml.churn_scores / ml.customer_segments (customer_id)
                    +-- mart.customer_profitability / mart.customer_360 / mart.retention_priority (customer_id)
```

## raw / staging / audit

| Table | Grain | PK | Purpose |
|---|---|---|---|
| `raw.credit_card_clients` | one row per source row | `source_row_number` | Exact text copy of the .xls, so problems can be traced back to what was delivered |
| `staging.stg_credit_card_clients` | one row per customer | `customer_id` | Typed, renamed (m1..m6 instead of the confusing PAY_0/BILL_AMT1 suffixes), validated rows. CHECK constraints repeat the key rules as a second line of defence |
| `staging.rejected_rows` | one row per quarantined source row | `source_row_number` | Rows that failed an ERROR check, with reasons (empty for this dataset) |
| `audit.pipeline_runs` | one row per pipeline run | `run_id` | Run log: file hash, rows read/loaded/rejected, status. Kept across runs |
| `audit.dq_check_results` | one row per run x check | `(run_id, check_name)` | Result of every data-quality check. Kept across runs |
| `audit.dq_row_flags` | one row per flagged row x check | - | Which customers failed which WARN/INFO checks (latest run) |

## core (cleaned analytical model)

### `core.dim_customer`
- **Grain:** one row per customer. **PK:** `customer_id`.
- Decoded demographics (`sex`, `education`, `marital_status`; undocumented codes -> `Unknown`, raw code kept),
  `age` and `age_band`, `credit_limit`, `limit_tier`, `credit_limit_decile`, and data-quality flags.
- Credit limit is reported once; I assume it was constant over the six months.

### `core.dim_month`
- **Grain:** one row per month (Apr-Oct 2005). **PK:** `month_index`; unique `month_key` (YYYYMM).
- A daily date dimension would add nothing because the data is monthly.

### `core.fact_account_month`
- **Grain:** one row per customer per statement month. **PK:** `(customer_id, month_index)`. 180,000 rows.
- **FKs:** `customer_id -> dim_customer`, `month_index -> dim_month`.
- Observed: `bill_amount`, `payment_amount`, `status_code`, `credit_limit`.
- Derived by arithmetic only (no assumptions):
  - `est_new_charges = bill_t - bill_(t-1) + payment_t` (purchases + interest + fees; NULL in April)
  - `utilization = bill / limit` (can be < 0 for credit balances or > 1 when over limit)
  - `payment_ratio = payment_t / bill_(t-1)` (NULL if nothing was owed)
  - `paid_in_full`, `missed_payment`, `revolving_balance = max(bill_(t-1) - payment_t, 0)`
  - `delinquency_bucket` (Current / 30 / 60 / 90+ DPD), `is_delinquent`, `is_delinquent_60plus`
  - `is_inactive` = nothing owed and no new charges (NULL in April)

### `core.fact_default_outcome`
- **Grain:** one row per customer. **PK:** `customer_id`.
- `defaulted_next_month`: the label (missed payment in Oct 2005). Kept in its own table so that feature SQL
  never touches it (enforced by a test).

## features

### `features.customer_risk_features` (PD model input)
- **Grain:** one row per customer, as of end of Sep 2005. **PK:** `customer_id`.
- Observation window Apr-Sep. Delinquency history (latest/worst status, months delinquent, months since last
  delinquency, current streak), utilization level and trend (`REGR_SLOPE`), payment behaviour (share of bill
  paid, months paid in full, months with no payment), activity (average new charges and trend, inactive months),
  available credit.

### `features.churn_snapshots` (dormancy model input)
- **Grain:** one row per customer per snapshot month (May-Sep). **PK:** `(customer_id, snapshot_month_index)`.
- Features from months t-1 and t (via `LAG`), label from t+1 and t+2 (via `LEAD`).
- `is_eligible` = active in month t. `churn_label` NULL when t+2 is beyond Sep 2005.

## ml (written by Python)

| Table | Grain | Content |
|---|---|---|
| `ml.pd_scores` | customer | Out-of-fold PD, decile, risk band, top SHAP drivers up/down (text) |
| `ml.churn_scores` | customer active in Sep | P(dormant in Oct-Nov), decile, SHAP drivers |
| `ml.customer_segments` | customer | K-means cluster id and segment name |
| `ml.segment_profile` | segment | Average behaviour ratios per segment |
| `ml.model_comparison` | task x model x metric | CV and test metrics for every candidate model |
| `ml.model_lift` | task x decile | Test-set lift table |
| `ml.model_calibration` | task x bin | Predicted vs observed rate |
| `ml.feature_importance` | task x feature | Mean absolute SHAP value |

## mart (business-facing)

| Table | Grain | PK | Purpose |
|---|---|---|---|
| `mart.customer_monthly_metrics` | customer x month | `(customer_id, month_index)` | Rolling 3-month averages, month-over-month change, running delinquency count, balance percentile within month |
| `mart.portfolio_monthly` | month | `month_index` | Portfolio KPIs: active rate, balances, utilization, delinquency, new 60+ and cures |
| `mart.roll_rates` | from month x from bucket x to bucket | all three | Delinquency transition matrix |
| `mart.delinquency_cohorts` | cohort month x months since entry | both | What happens after an account first reaches 60+ DPD |
| `mart.assumptions` | parameter | `parameter` | Profitability/retention assumptions copied from `config.yaml` |
| `mart.customer_profitability` | customer | `customer_id` | Monthly revenue, costs, expected loss and risk-adjusted contribution (see below) |
| `mart.customer_360` | customer | `customer_id` | One wide row per customer: profile, behaviour, segment, scores, profitability, actual outcome |
| `mart.retention_priority` | customer active in Sep | `customer_id` | Value at risk, expected net benefit of a retention contact, priority tier and reason |

### Provenance of profitability columns
- `obs_*` observed in the data.
- `est_*` calculated from observed data plus assumptions in `mart.assumptions` (APR, interchange, fees, rewards,
  funding and servicing costs).
- `pd_score`, `ead_estimate`, `ecl_next_month` model-based. `chargeoff_share` is calibrated so the portfolio's
  annualised expected loss equals `target_annual_loss_rate`.
- None of this is accounting profit; it is an educational estimate for ranking customers.
