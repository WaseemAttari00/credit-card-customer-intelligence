# Power BI dashboard

**What's here and what isn't:** Power BI Desktop wasn't available in the environment I built this in
(and it can't be scripted from Python), so I could not produce a `.pbix` file. Everything needed to build
it is in this folder, and `preview/` has static previews of the four pages, drawn with matplotlib from the
exact tables Power BI would load. The previews show the intended layout and the real numbers; they are not
Power BI screenshots.

| File | Purpose |
|---|---|
| `data/*.csv` | Dashboard tables exported by `python -m src.pipeline --steps export` (gitignored, regenerate them) |
| `powerquery_postgres.pq` | Alternative: load the same tables directly from PostgreSQL |
| `measures.dax` | All DAX measures used on the pages |
| `theme.json` | Colour theme (colourblind-checked categorical palette, status colours for risk) |
| `preview/page*.png` | Static previews of the four pages |

## Build steps (about 1-2 hours)

1. **Load data.** Get Data > Text/CSV for each file in `data/` (or use `powerquery_postgres.pq`).
   Check that `customer_id` is whole number, `month_index` whole number, `*_score`/rates decimal, flags True/False.
2. **Theme.** View > Themes > Browse for themes > `theme.json`.
3. **Relationships** (Model view, single direction unless noted):

   | From (many) | To (one) | Key |
   |---|---|---|
   | `customer_monthly` | `customer_360` | `customer_id` |
   | `customer_monthly` | `dim_month` | `month_index` |
   | `portfolio_monthly` | `dim_month` | `month_index` (1:1) |
   | `retention_priority` | `customer_360` | `customer_id` (1:1, filter both directions) |
   | `customer_360` | `segment_profile` | `segment` |

   `roll_rates`, `delinquency_cohorts`, `model_*`, `feature_importance`, `assumptions` and `dq_check_results`
   stay disconnected (they are already aggregated).
4. **Measures.** Create a `_Measures` table and paste the measures from `measures.dax`.
5. **Sort columns.** Sort `dim_month[month_label]` by `month_index`; the tier/band columns have numeric
   prefixes ("1. ...") so they sort correctly as text.
6. **Pages.** Build the four pages below. Every visual answers one question; the question is the visual title.

Grain reminders: `customer_360` and `retention_priority` are one row per customer, `customer_monthly` is
customer x month. Don't sum balances across months (use the Sep balance or the monthly trend measure).

## Page 1 - Executive overview
*Question: how big is the book, is it getting riskier, and where does the value come from?*

- **KPI cards:** Customers, Active Customers (Sep), Balance Outstanding (Sep), Est Monthly Revenue,
  Expected Credit Loss (Next Month), Est Risk-Adj Contribution, Actual Default Rate (Oct), Loss-Making Share.
- **Line:** Balance by Month by `dim_month[month_label]` - is the book growing?
- **Line:** 60+ DPD Rate by month - is credit quality worsening? Add a text box noting the Sep status-coding change.
- **Bar:** Est Risk-Adj Contribution by `customer_360[segment]` - which segments create or destroy value?
- **Line/area:** value concentration from `reports/tables/analysis_value_concentration.csv` (optional import).
- **Slicers:** segment, limit tier, age band.

## Page 2 - Customer analytics
*Question: who are our customers and how do they use the card?*

- **Bar:** Customers by segment.
- **Scatter:** `segment_profile` avg utilization (x) vs avg payment ratio (y), size = customers.
- **Bar:** average `est_monthly_purchases` by segment - spending behaviour.
- **100% stacked bar:** segment mix by `limit_tier` - product usage by limit tier (the data has no card-product field).
- **Column:** customers by `age_band` and `education` (demographics), with Avg Risk-Adj Contribution as tooltip.
- **Table:** segment, customers, avg purchases, avg utilization, Avg Risk-Adj Contribution per Customer.

## Page 3 - Risk analytics
*Question: where is the credit risk and how much could it cost?*

- **Column:** Customers by `pd_risk_band` (status colours good/warning/serious/critical).
- **Combo (bars + markers, one axis):** `model_lift` (task = "pd") observed rate and mean predicted by decile -
  does the model rank and calibrate risk?
- **Matrix heatmap:** `roll_rates` from_bucket x to_bucket, value = share_of_from_bucket (filter from_month_index <= 4).
- **Column:** Actual Default Rate (Oct) and Avg Predicted PD by `limit_tier` (risk by product tier).
- **Bar:** default rate by education / age band (risk by customer profile). Geography is not in the data.
- **Bar:** Expected Credit Loss (Next Month) by segment.
- **Card:** Test ROC-AUC (Chosen PD Model).

## Page 4 - Retention decision support
*Question: which customers should we prioritise, and why?*

- **Bar:** customers by `retention_priority[priority_tier]`.
- **Scatter:** `churn_score` (x, log scale) vs `est_risk_adjusted_contribution` (y), legend = priority_tier.
- **Line:** cumulative expected net benefit vs share contacted, one line per contact cost
  (`reports/tables/analysis_retention_budget_curve.csv`).
- **Table (the decision list):** customer_id | segment | est_risk_adjusted_contribution (value) |
  churn_score (dormancy risk) | pd_score (credit risk) | expected_net_benefit | priority_tier | priority_reason,
  sorted by `priority_rank`, conditional formatting on the risk columns.
- **Slicers:** priority tier, segment, PD band.
- **Text box:** the assumptions used (from the `assumptions` table) and the note that the save rate must be
  measured with a holdout group.
