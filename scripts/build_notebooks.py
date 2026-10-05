"""Builds the analysis notebooks from cell definitions and executes them against the database.

    python scripts/build_notebooks.py

Run after the pipeline. Keeping notebooks as generated artifacts means they are always re-executed
top to bottom against the current data (no stale outputs or out-of-order cells).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
NB_DIR = ROOT / "notebooks"

SETUP = """import sys, json, warnings
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent))
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from IPython.display import Image, display
from src.db import read_sql
from src.evaluation import plots  # applies the shared chart style
pd.set_option("display.max_columns", 30); pd.set_option("display.width", 160)
pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
FIG = Path("../reports/figures")"""


def md(text):
    return nbf.v4.new_markdown_cell(text.strip())


def code(text):
    return nbf.v4.new_code_cell(text.strip())


EDA = [
    md("""# 01 - Exploratory data analysis

Data: UCI *Default of Credit Card Clients* (Taiwan, 2005), loaded by the pipeline into PostgreSQL.
30,000 cardholders with a six-month history (Apr-Sep 2005) of statement balances, payments and repayment
status, plus whether they defaulted on the October payment.

This notebook looks at the data as it sits in the `core` schema and checks the assumptions the rest of the
project depends on."""),
    code(SETUP),
    md("## 1. Size and grain\n`core.fact_account_month` should have exactly one row per customer per month."),
    code("""read_sql('''SELECT (SELECT count(*) FROM core.dim_customer) AS customers,
                  (SELECT count(*) FROM core.fact_account_month) AS customer_months,
                  (SELECT avg(defaulted_next_month::int) FROM core.fact_default_outcome) AS oct_default_rate''')"""),
    md("""## 2. Data-quality findings
Results of the validation step (full report in `docs/data_quality_report.md`). Nothing failed an ERROR check,
so no rows were quarantined. The warnings are mostly real customer behaviour (overpayment, over-limit) plus
two coding issues."""),
    code("""read_sql('''SELECT check_name, severity, rows_failed, pct_failed FROM audit.dq_check_results
            WHERE run_id = (SELECT max(run_id) FROM audit.pipeline_runs WHERE status = 'loaded')
              AND (rows_failed > 0) ORDER BY severity, rows_failed DESC''')"""),
    md("""## 3. Which bill does a payment pay?
The UCI documentation lists bills and payments by month but doesn't say how they line up. If the payment in
month *t* pays the bill from month *t-1*, it should match that bill exactly more often (people paying in full)."""),
    code("""read_sql('''SELECT avg((payment_amount = prev_bill_amount)::int) AS pays_previous_bill,
                  avg((payment_amount = bill_amount)::int)      AS pays_same_month_bill
           FROM core.fact_account_month WHERE month_index > 1 AND payment_amount > 0''')"""),
    md("""Payments match the previous month's bill far more often, so I use
`new charges_t = bill_t - bill_(t-1) + payment_t`. This identity is what makes activity, purchases and
interest estimable at all."""),
    md("## 4. Repayment status coding by month"),
    code("""s = read_sql('''SELECT month_index, status_code, count(*) n FROM core.fact_account_month
                  WHERE status_code BETWEEN -2 AND 3 GROUP BY 1, 2''')
s.pivot(index="status_code", columns="month_index", values="n").fillna(0).astype(int).rename(
    columns=dict(zip(range(1, 7), ["Apr", "May", "Jun", "Jul", "Aug", "Sep"])))"""),
    md("""Status 1 ("one month late") barely exists before September; in Apr-Aug accounts jump from 0 to 2.
In September it suddenly covers ~3,700 accounts. I treat September's codes as not comparable with the earlier
months: trend comparisons use 60+ DPD (status >= 2), and the dormancy model avoids raw status codes."""),
    md("## 5. Portfolio trends"),
    code("""port = read_sql("SELECT * FROM mart.portfolio_monthly ORDER BY month_index")
port[["month_label", "active_rate", "total_balance", "portfolio_utilization", "paid_in_full_rate",
      "delinquency_rate_60plus", "new_60plus_accounts", "cured_from_60plus"]]"""),
    code("""fig, axes = plt.subplots(1, 3, figsize=(14, 3.6))
m = port.month_label.str[:3]
axes[0].plot(m, port.total_balance / 1e9, marker="o"); axes[0].set_title("Balance outstanding (NT$ bn)")
axes[1].plot(m, port.portfolio_utilization * 100, marker="o"); axes[1].set_title("Portfolio utilization (%)")
axes[2].plot(m, port.delinquency_rate_60plus * 100, marker="o"); axes[2].set_title("60+ DPD rate (%), Sep not comparable")
plt.tight_layout(); plt.show()"""),
    md("""Balances grew about 32% between April and September while 60+ DPD delinquency rose from ~10% to ~15%
(Aug). The data comes from the start of Taiwan's 2005-06 card-debt crisis, which fits this picture."""),
    md("## 6. Default rate by observable characteristics"),
    code("""read_sql('''SELECT f.status_latest AS sep_status, count(*) customers, avg(o.defaulted_next_month::int) default_rate
           FROM features.customer_risk_features f JOIN core.fact_default_outcome o USING (customer_id)
           WHERE f.status_latest <= 4 GROUP BY 1 ORDER BY 1''')"""),
    code("""from scipy.stats import chi2_contingency
d = read_sql('''SELECT c.limit_tier, c.age_band, c.education, o.defaulted_next_month::int y
               FROM core.dim_customer c JOIN core.fact_default_outcome o USING (customer_id)''')
for col in ["limit_tier", "age_band", "education"]:
    t = d.groupby(col).y.agg(customers="size", default_rate="mean")
    chi2, p, dof, _ = chi2_contingency(pd.crosstab(d[col], d.y))
    display(t); print(f"chi-square p-value for {col}: {p:.2e}")"""),
    md("""Current repayment status is by far the strongest single signal. Default rate falls steadily with credit
limit (the bank gave larger limits to customers it judged safer). The differences across limit tiers and age
bands are very unlikely to be chance (tiny chi-square p-values), but they are descriptive, not causal.
Note that even customers who "paid duly" in September have a ~17% recorded default rate, so the label is broad:
it is a missed payment, not a write-off."""),
    md("## 7. Roll rates and delinquency cohorts"),
    code("""r = read_sql('''SELECT from_bucket, to_bucket, sum(accounts) n FROM mart.roll_rates
               WHERE from_month_index <= 4 GROUP BY 1, 2''')
rr = r.pivot(index="from_bucket", columns="to_bucket", values="n").fillna(0)
(rr.div(rr.sum(axis=1), axis=0) * 100).round(1)"""),
    code("""read_sql('''SELECT cohort_month, months_since_entry, accounts, share_still_60plus, share_cured, oct_default_rate
           FROM mart.delinquency_cohorts ORDER BY cohort_month_index, months_since_entry''')"""),
    md("""Delinquency is sticky but not permanent: about 30% of 60 DPD accounts are current again the next month,
and more than half (56-59%) of each new 60+ DPD cohort has cured two months later. (The "30 DPD" row is tiny because of
the coding issue above.)

**Next:** feature engineering (`02_feature_engineering.ipynb`)."""),
]

FEATURES = [
    md("""# 02 - Feature engineering and target construction

Features are built in SQL (`sql/features/`). This notebook explains the design, checks the targets and looks
for leakage."""),
    code(SETUP),
    md("""## 1. Two prediction problems, two time designs

| | PD model | Dormancy model |
|---|---|---|
| Unit | customer | customer x snapshot month |
| Features | Apr-Sep 2005 (6 months) | months t-1 and t |
| Target | missed payment in Oct 2005 | no balance and no new charges in t+1 **and** t+2 |
| Eligible | everyone | customers active in month t |
| Split | stratified customer split (one label month only) | train = May snapshot, test = Jul snapshot |

For the dormancy model the training labels (Jun-Jul) end before the test snapshot (end of Jul), so nothing from
the test period leaks into training. Sep 2005 is the scoring snapshot (predicts Oct-Nov, no labels yet)."""),
    code("""read_sql('''SELECT snapshot_month_index AS snapshot, count(*) FILTER (WHERE is_eligible) eligible,
                  sum(churn_label) FILTER (WHERE is_eligible) dormant_next_2m,
                  avg(churn_label) FILTER (WHERE is_eligible) dormancy_rate
           FROM features.churn_snapshots GROUP BY 1 ORDER BY 1''')"""),
    md("""Dormancy is rare (1.2-1.6% of active customers per snapshot), so accuracy would be meaningless:
a model predicting "nobody goes dormant" would be 98.5% accurate. I evaluate with PR-AUC, lift and capture
rate instead."""),
    md("""## 2. Leakage checks
1. The default label lives only in `core.fact_default_outcome`, and a test (`tests/test_features.py`) fails if any
   feature SQL file mentions it.
2. If a feature leaked the target it would separate the classes almost perfectly on its own. Single-feature
   ROC-AUCs are a quick check:"""),
    code("""from sklearn.metrics import roc_auc_score
from src.modeling.features import PD_FEATURES, CHURN_FEATURES, load_pd_data, load_churn_data
pdd = load_pd_data()
rows = []
for f in [c for c in PD_FEATURES if c != "education"]:
    x = pdd[f].fillna(pdd[f].median())
    auc = roc_auc_score(pdd.target, x)
    rows.append((f, max(auc, 1 - auc)))
pd.DataFrame(rows, columns=["feature", "single_feature_auc"]).sort_values("single_feature_auc", ascending=False).head(10)"""),
    code("""ch = load_churn_data(); ch = ch[ch.snapshot_month_index == 2]
rows = []
for f in [c for c in CHURN_FEATURES if c != "education"]:
    x = ch[f].fillna(ch[f].median())
    auc = roc_auc_score(ch.churn_label, x)
    rows.append((f, max(auc, 1 - auc)))
pd.DataFrame(rows, columns=["feature", "single_feature_auc"]).sort_values("single_feature_auc", ascending=False).head(10)"""),
    md("""The strongest single PD feature (months delinquent) reaches an AUC of about 0.73, and the strongest dormancy
feature (current utilization) about 0.84. That is strong but well short of the near-perfect separation a leaked
label would give, and it makes sense: a customer with almost no balance is the obvious dormancy candidate."""),
    md("## 3. Do the main features relate to default in a sensible direction?"),
    code("""pdd["pay_ratio_bin"] = pd.qcut(pdd.payment_ratio_avg, 5, duplicates="drop")
pdd["util_bin"] = pd.cut(pdd.util_avg_6m, [-10, 0.1, 0.3, 0.5, 0.7, 0.9, 10])
display(pdd.groupby("months_since_delinquent").target.agg(customers="size", default_rate="mean"))
display(pdd.groupby("pay_ratio_bin", observed=True).target.agg(customers="size", default_rate="mean"))
display(pdd.groupby("util_bin", observed=True).target.agg(customers="size", default_rate="mean"))"""),
    md("""Default risk falls the longer it has been since the last delinquency and the larger the share of the bill a
customer pays, and above ~10% utilization it rises steadily with utilization. The lowest-utilization group is
slightly riskier than the 10-30% group because it includes inactive accounts that the broad label still marks as
defaulting. These patterns match credit-risk intuition, which is a good sign the features were computed correctly
(independent recalculations are also in `tests/test_features.py`).

**Next:** modeling (`03_modeling.ipynb`)."""),
]

MODELING = [
    md("""# 03 - Modeling, evaluation and explainability

Models are trained by `src/modeling/train.py` (run through the pipeline). This notebook reads the saved
results so the numbers here are exactly what the pipeline produced."""),
    code(SETUP + """
R = json.load(open("../reports/model_results.json"))"""),
    md("""## 1. PD model - model comparison
Logistic regression, random forest and LightGBM were each tuned with the same 5-fold stratified CV on the 80%
training split. Selection uses CV ROC-AUC only; the test set is reported but was not used to choose."""),
    code("""pd.DataFrame(R["pd"]["cv_results"]).drop(columns=["best_params"]).merge(pd.DataFrame(R["pd"]["test_comparison"]), on="model")"""),
    md("""LightGBM and the random forest are almost tied and both beat logistic regression by about 0.013 AUC.
The train-vs-CV gap (about 0.03) shows mild overfitting, which is acceptable. LightGBM was selected.
Its raw probabilities were already well calibrated, so no calibration layer was added:"""),
    code("""pd.DataFrame(R["pd"]["calibration_table"])"""),
    code("""display(Image(FIG / "pd_roc_pr.png")); display(Image(FIG / "pd_calibration_lift.png"))"""),
    code("""pd.DataFrame(R["pd"]["lift"])"""),
    md("""## 2. Choosing a threshold
Probabilities feed the expected-loss calculation directly, but a risk team acting on a flag needs a cut-off.
With calibrated probabilities the cost-minimising threshold is `C_FP / (C_FP + C_FN)`. C_FN (average loss on a
missed defaulter) comes from the data and assumptions; C_FP (cost of acting on a good customer) is unknown, so
I show a range:"""),
    code("""pd.DataFrame(R["pd"]["cost_table"])"""),
    code("""pd.DataFrame(R["pd"]["thresholds"])"""),
    md("""The threshold depends heavily on the false-positive cost, which is a business input, not a modeling one.
At 0.5 the model flags about 12% of customers with roughly two-thirds precision; at 0.3 it catches more than
half of defaulters at about 54% precision."""),
    md("## 3. Fairness check\nSex is not a model input. Are predictions still calibrated within each group?"),
    code("""pd.DataFrame(R["pd"]["fairness"])"""),
    md("""## 4. Dormancy model
Same candidates plus a class-weighted LightGBM. Selection by CV PR-AUC on the May snapshot; the test is the
July snapshot (out-of-time)."""),
    code("""pd.DataFrame(R["churn"]["cv_results"]).drop(columns=["best_params"]).merge(pd.DataFrame(R["churn"]["test_comparison"]), on="model")"""),
    md("""The random forest had the best CV PR-AUC, but the CV standard deviations (~0.03) are larger than the gaps
between models, and on the out-of-time test LightGBM did better. I kept the CV-based choice: switching after
seeing the test results would make the test estimate optimistic. With more months of data I would select
models with rolling out-of-time validation. Class weighting did not improve ranking and wrecked calibration
(test Brier 0.063 vs 0.011)."""),
    code("""print({k: round(v, 4) for k, v in R["churn"]["test_metrics"]["at_top_10pct"].items() if isinstance(v, float)})
pd.DataFrame(R["churn"]["lift"])"""),
    code("""display(Image(FIG / "churn_roc_pr.png")); display(Image(FIG / "churn_calibration_lift.png"))"""),
    md("""Contacting the top 10% of active customers by predicted risk would reach about two-thirds of the customers
who actually went dormant. Precision is low in absolute terms (~8%) because the event is rare, but that is
about 7x the base rate. Mean predicted probability on the test snapshot is a little above the observed rate,
because dormancy was more common in May (training) than in July."""),
    md("## 5. Explainability (SHAP)"),
    code("""display(Image(FIG / "pd_shap_beeswarm.png")); display(Image(FIG / "churn_shap_beeswarm.png"))"""),
    md("""**PD:** recent delinquency dominates (months since last delinquency, September status, worst status), followed
by available credit and spending. Low available credit and recent late payments push risk up; paying a larger
share of the bill and having headroom on the limit push it down.

**Dormancy:** small and shrinking balances, low utilization and paying off most of the bill push the prediction
up - these are customers winding the card down. These are associations the model learned, not causes."""),
    md("""### Local explanations for individual customers
Reason codes list up to three features pushing the prediction up and two pushing it down; features carrying
less than 5% of the customer's total SHAP attribution are left out so that negligible effects aren't shown as
reasons. Examples: the riskiest customer, a customer near 25% PD and the safest customer."""),
    code("""ex = read_sql('''SELECT customer_id, round(pd_score::numeric, 3) pd, pd_risk_band, pd_drivers_up, pd_drivers_down
                FROM ml.pd_scores WHERE customer_id IN (
                  (SELECT customer_id FROM ml.pd_scores ORDER BY pd_score DESC LIMIT 1),
                  (SELECT customer_id FROM ml.pd_scores ORDER BY abs(pd_score - 0.25) LIMIT 1),
                  (SELECT customer_id FROM ml.pd_scores ORDER BY pd_score LIMIT 1))''')
for r in ex.itertuples():
    print(f"Customer {r.customer_id}: PD {r.pd:.1%} ({r.pd_risk_band})\\n  raises risk: {r.pd_drivers_up}\\n  lowers risk: {r.pd_drivers_down}\\n")"""),
    code("""ex = read_sql('''SELECT customer_id, churn_score, churn_drivers_up, churn_drivers_down
                FROM ml.churn_scores ORDER BY churn_score DESC LIMIT 3''')
for r in ex.itertuples():
    print(f"Customer {r.customer_id}: P(dormant in Oct-Nov) {r.churn_score:.1%}\\n  raises: {r.churn_drivers_up}\\n  lowers: {r.churn_drivers_down}\\n")"""),
    md("**Next:** segmentation, profitability and retention (`04_business_analysis.ipynb`)."),
]

BUSINESS = [
    md("""# 04 - Segmentation, profitability and retention prioritisation

Turns the model outputs into business answers: who are the customers, who makes money, where is the risk,
and who should the retention team contact?"""),
    code(SETUP + """
R = json.load(open("../reports/model_results.json"))
q = lambda name: read_sql(open(f"../sql/analysis/{name}.sql").read())"""),
    md("""## 1. Segmentation
K-means on six behaviour ratios (utilization, share of bill paid, share of months paid in full, share of months
inactive, new charges / limit, share of months 60+ DPD). k was chosen with silhouette and bootstrap stability:"""),
    code("""pd.DataFrame(R["segmentation"]["k_selection"])"""),
    md("""k = 4 has the best silhouette and k = 5 is close; both are very stable (ARI ~0.99). I used k = 5 because it
splits out heavy-spend revolvers, whose economics differ from ordinary revolvers."""),
    code("""pd.DataFrame(R["segmentation"]["profile"])"""),
    md("## 2. Profitability (estimates)"),
    code("""q("executive_kpis").T"""),
    code("""seg = q("segment_summary")
seg[["segment", "customers", "avg_monthly_purchases", "actual_default_rate", "avg_pd", "avg_dormancy_score",
     "total_monthly_revenue", "total_ecl", "total_risk_adj_contribution", "share_loss_making", "share_of_total_contribution"]]"""),
    md("""Ordinary revolvers carry most of the estimated value: interest on revolving balances is the main revenue line.
Delinquent revolvers generate plenty of revenue but nearly all of it is offset by expected losses. Transactors
and dormant/low-use customers come out slightly negative because interchange on modest spend barely covers
servicing costs plus a share of expected losses. That last result depends on the assumptions (see sensitivity)."""),
    code("""c = q("value_concentration")
c[c.top_percent_of_customers.isin([1, 5, 10, 20, 50, 60, 80, 100])]"""),
    code("""q("profit_sensitivity")"""),
    md("""The portfolio stays profitable across the 4-12% loss-rate range, but the share of loss-making customers moves
from about a quarter to about half, so customer-level profitability should be read as a ranking more than as
precise numbers."""),
    md("## 3. Where is the credit risk?"),
    code("""q("risk_by_group")"""),
    md("## 4. Retention prioritisation"),
    code("""read_sql('''SELECT priority_tier, count(*) customers, avg(churn_score) avg_p_dormant, avg(pd_score) avg_pd,
                  avg(est_risk_adjusted_contribution) avg_value, sum(expected_value_at_risk) value_at_risk
           FROM mart.retention_priority GROUP BY 1 ORDER BY 1''')"""),
    code("""read_sql('''SELECT segment, count(*) active, avg(churn_score) avg_p_dormant,
                  avg(est_risk_adjusted_contribution) avg_value, sum(expected_value_at_risk) value_at_risk
           FROM mart.retention_priority GROUP BY 1 ORDER BY value_at_risk DESC''')"""),
    md("""The customers most likely to go dormant (dormant/low-use and transactors) are the ones with little value,
while the valuable revolvers rarely go dormant. The total 12-month value at risk from dormancy (about NT$0.3M)
is tiny next to one month of expected credit loss (about NT$10M), and an expensive retention offer only pays
for a few dozen customers:"""),
    code("""q("retention_cost_sensitivity")"""),
    code("""b = q("retention_budget_curve")
b.loc[b.groupby("contact_cost").cum_benefit_model.idxmax()]"""),
    md("""With a 10% budget (about 2,450 customers) the budget is not the constraint: at NT$100 per contact the best
plan contacts only ~50-60 customers, and even a NT$10 digital nudge is worth sending to under 1,000. Contacting
more people in model order starts to lose money.

**Caveats:** the model predicts who is likely to go dormant, not who will respond to an offer, and the save rate
is an assumption. Any campaign should keep a random holdout group so the actual uplift can be measured."""),
    code("""read_sql('''SELECT customer_id, segment, round(churn_score::numeric, 3) p_dormant, round(pd_score::numeric, 3) pd,
                  round(est_risk_adjusted_contribution::numeric) value_per_month,
                  round(expected_net_benefit::numeric) net_benefit, priority_reason
           FROM mart.retention_priority WHERE priority_tier = '1. Contact now' ORDER BY priority_rank LIMIT 10''')"""),
    md("## 5. Are the segment differences statistically meaningful?"),
    code("""pd.DataFrame(json.load(open("../reports/business_summary.json"))["significance_tests"]).T"""),
    md("""Default and dormancy rates differ across segments far more than chance would explain (very small p-values).
With 30,000 customers almost any difference is "significant", so the size of the gap matters more than the
p-value; the gaps here are large (e.g. ~63% vs ~14% default rate for delinquent revolvers vs transactors)."""),
]


def build(name, cells):
    nb = nbf.v4.new_notebook()
    nb.cells = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    p = NB_DIR / name
    nbf.write(nb, p)
    subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace",
                    "--ExecutePreprocessor.timeout=600", str(p)], check=True, cwd=NB_DIR)
    print("built", p.name)


if __name__ == "__main__":
    NB_DIR.mkdir(exist_ok=True)
    build("01_eda.ipynb", EDA)
    build("02_feature_engineering.ipynb", FEATURES)
    build("03_modeling.ipynb", MODELING)
    build("04_business_analysis.ipynb", BUSINESS)
