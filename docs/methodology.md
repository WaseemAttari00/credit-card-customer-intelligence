# Methodology and design decisions

This document records the main decisions in the project, what I considered, and why I chose what I did.
Numbers are from the final pipeline run (`reports/model_results.json`, `reports/business_summary.json`).

## 1. Dataset choice

| Candidate | Why not / why |
|---|---|
| **UCI Default of Credit Card Clients** (chosen) | Real (anonymised) card data, CC BY 4.0, direct download. 30,000 customers x 6 months of balances, payments and repayment status, plus a genuine next-month default label. Both models can score the *same* customers, which the retention prioritisation needs. |
| Kaggle "BankChurners" | Has a churn label but it is a single snapshot whose 12-month features overlap the period in which customers were leaving (e.g. transaction count over the last 12 months), so a model would partly learn from the churn itself. Unclear provenance, Kaggle login required, and it can't be joined to a risk dataset. |
| IBM TabFormer card transactions | Synthetic. Rich transactions and merchants but no payments or defaults, and the user/card files need a Kaggle login. |

**What the chosen data can't support:** transactions, merchants, geography, card product, income, credit score,
marketing response, account closure. I did not create synthetic data to fill these gaps; the dashboard uses
credit-limit tier, age band and education instead of geography/product. There is no synthetic data anywhere
in this project.

## 2. Understanding the data before modeling

- **Payment timing.** The documentation doesn't say which bill a payment pays. Payments in month t equal the
  bill of month t-1 in 17.6% of cases versus 2.6% for the same month's bill, so a payment pays the previous
  statement. That gives the identity `bill_t = bill_(t-1) - payment_t + new charges_t`, which is how I estimate
  activity, purchases and interest. A test checks this assumption on every run.
- **Status coding change in September.** Status 1 ("one month late") is almost never used in Apr-Aug
  (accounts jump 0 -> 2) but appears 3,688 times in September; in the August-to-September transition more
  than half of a cohort "moves" to status 1, which doesn't happen in any other month. I treat September status
  codes as not comparable with earlier months (trend charts use 60+ DPD; the dormancy model uses only a 60+ DPD
  flag). The PD model still uses September status as a feature, because every customer is scored on the same
  coding.
- **The default label is broad.** 13-17% of customers who didn't use the card or paid in full in September are
  labelled as defaulting in October. It is best read as "missed or late payment next month", not as a credit
  loss. This mattered a lot for the expected-loss calculation (section 6).
- **Undocumented codes, duplicates, credit balances, over-limit balances.** See `docs/data_quality_report.md`.
  None of them justified dropping rows.

## 3. Pipeline design

- **Raw lands as text.** If the source ever contains "N/A" or "1,000" in a numeric column, a validation check
  reports it instead of the reader silently coercing or failing.
- **ERROR vs WARN.** ERROR rows are quarantined in `staging.rejected_rows` with reasons; the pipeline stops if more
  than 1% of rows fail. WARN rows are loaded and flagged, because overpayments and over-limit balances are real.
- **Idempotent.** Every SQL file drops and rebuilds its table; loads are truncate + COPY in a transaction.
  Re-running gives the same result instead of appending duplicates. Audit tables keep the run history.
- **SQL for the data model, Python for ML.** Unpivoting, window features, marts and business rules are SQL;
  model training, SHAP and clustering are Python. Model outputs go back into Postgres so the final marts
  (profitability, customer 360, priority) are SQL joins that Power BI can read.

## 4. PD (credit risk) model

- **Target:** missed payment in Oct 2005. **Features:** Apr-Sep 2005 (31 features: delinquency history,
  utilization level and trend, payment behaviour, activity, available credit, age, education).
- **Excluded inputs:** sex and marital status (lenders generally can't use them in credit decisions). After
  training I checked calibration by sex anyway: mean predicted PD 21.2% vs actual 20.8% for women, 23.4% vs
  24.2% for men.
- **Split.** There is only one label month, so an out-of-time test isn't possible. I used a stratified 80/20
  customer split and 5-fold CV on the 80% for tuning and selection. The limitation: this measures performance
  on new customers from the same period, not on a future period.
- **Models.** Logistic regression (log-transformed, clipped and scaled inputs), random forest, LightGBM, each
  with a small grid. CV ROC-AUC: 0.777 / 0.789 / 0.789 (sd ~0.005). LightGBM was selected; the two tree models are
  effectively tied, and the ~0.013 gain over logistic regression is small but consistent across folds.
- **Test results (LightGBM):** ROC-AUC 0.785, PR-AUC 0.566 (base rate 0.221), Brier 0.134. Train AUC 0.820, so
  overfitting is mild. Top decile default rate 70% (3.2x lift); the top two deciles hold 51% of defaulters.
- **Calibration.** Out-of-fold comparison of raw vs Platt vs isotonic: raw ECE was already 0.006 and the
  alternatives didn't lower the Brier score by more than 1%, so I didn't add a calibration layer (rule set
  before looking at the test set). Decile-level predicted vs observed rates on the test set agree within ~1-2
  points.
- **Scores used downstream are out-of-fold:** each customer's PD comes from a model that didn't see their label.
- **Thresholds.** Expected-loss calculations use the probability directly. For a yes/no risk flag, the
  cost-minimising threshold is `C_FP / (C_FP + C_FN)`. C_FN (average loss on a missed defaulter, NT$1,532) comes
  from the data and loss assumptions; C_FP is unknown, so `reports/tables/pd_cost_thresholds.csv` shows
  thresholds of 0.25, 0.57 and 0.77 for false-positive costs of NT$500, 2,000 and 5,000.

## 5. Dormancy ("churn") model

- **Definition.** The data has no closure field. Card issuers usually measure attrition as inactivity, so
  "churn" here is: active in month t (something owed or new charges), then nothing owed and no new charges in
  both t+1 and t+2. Event rate 1.2-1.6% of active customers per month.
- **Windows.** 2-month lookback (t-1, t) and 2-month outcome (t+1, t+2). Six months of data force short windows;
  longer windows would leave no clean out-of-time test.
- **Out-of-time design.** Train on the May snapshot (labels from Jun-Jul), test on the July snapshot (labels Aug-Sep).
  Training labels end before the test snapshot date, so nothing from the test period is used in training.
  The final scoring model is refit on May-Jul and applied to September (predicting Oct-Nov).
- **Class imbalance.** No resampling. I compared unweighted LightGBM with class-weighted LightGBM: weighting
  didn't improve ranking (CV PR-AUC 0.142 vs 0.146) and badly distorted probabilities (test Brier 0.063 vs 0.011).
  Metrics are PR-AUC, lift and capture rate, never accuracy.
- **Results.** CV PR-AUC: random forest 0.159 (sd 0.030), LightGBM 0.146, logistic regression 0.129. The random
  forest was selected on CV. On the out-of-time test: random forest ROC-AUC 0.900 / PR-AUC 0.108, LightGBM
  0.911 / 0.132, logistic regression 0.903 / 0.116. The top 10% by predicted risk contains 68% of the customers
  who went dormant (8.2% precision vs a 1.2% base rate, 6.8x lift).
- **The model-selection lesson.** The CV differences were within one standard deviation and the random forest's
  train PR-AUC (0.38) was far above its test value, so its CV edge didn't carry over to a later month. I did not
  switch to LightGBM after seeing the test set, because that would make the test estimate optimistic. With more
  history I would select models on rolling out-of-time folds rather than random CV within one snapshot.
- **Drift.** Dormancy was more common in May (1.6%) than July (1.2%); the model's mean prediction on July (1.5%)
  is a little high for that reason.
- **First attempt.** Fully grown random-forest trees overfit badly (train AUC 0.96 vs CV 0.89) and made exact
  TreeSHAP far too slow for 27k customers; I restricted depth to 8-12.

## 6. Profitability and expected loss

`mart.customer_profitability` estimates a **monthly contribution** per customer. It is an educational estimate,
not accounting profit:

```
revenue      = interest (revolving balance x APR/12) + interchange (purchases x 1.5%) + late fees
costs        = rewards (purchases x 0.5%) + funding (balance x 2%/12) + servicing (NT$30)
expected loss = PD x EAD x LGD x charge-off share
risk-adjusted contribution = revenue - costs - expected loss
```

Purchases are new charges minus estimated interest and fees. All parameters are in `config/config.yaml` and
copied to `mart.assumptions`.

**Calibrating the expected loss (a mistake I fixed).** My first version used a charge-off share of 25%, based
on the cohort analysis (25-43% of accounts that reach 60+ DPD are still 60+ three months later). That produced a
monthly expected loss of NT$97M against NT$22M of revenue (an annual loss rate of ~75% of balances) and 98% of
customers loss-making. The cause is the broad default label: most "defaults" in this data are late payments
that cure. Since the data has no charge-offs, the *level* of losses has to come from outside, so I switched to a
top-down calibration: the charge-off share is set so that the portfolio's annualised expected loss equals 8% of
balances. For reference, US bank card charge-off rates were about 3% in early 2006 and peaked at about 10.5% in
2009 (Federal Reserve data); Taiwan was in a card-debt crisis in 2005-06. The implied charge-off share is 3.4%.
PD x EAD still decides *which* customers carry the loss.

**EAD.** EAD = balance + CCF x unused limit. I first used CCF = 0.30, a typical 12-month figure; with that,
transactors and dormant customers (4% of balances) absorbed 24% of expected loss. Because the PD horizon is
one month, little extra drawdown is plausible, so I use CCF = 0.10.

**Sensitivity** (`reports/tables/analysis_profit_sensitivity.csv`): at 4% / 8% / 12% annual loss rates the
portfolio's monthly risk-adjusted contribution is NT$12.6M / 7.4M / 2.3M and the share of loss-making
customers is 24% / 38% / 49%. Customer-level profit is best read as a ranking.

## 7. Segmentation

K-means on six behaviour ratios (no demographics, no model scores). k from 2 to 8 compared on silhouette and
bootstrap stability (adjusted Rand index between the full fit and fits on resamples). k = 4 had the best
silhouette (0.449), k = 5 was close (0.430); both had ARI ~0.99. I chose k = 5 because the extra cluster
separates heavy-spend revolvers (new charges ~20% of limit per month vs ~5% for other revolvers), whose
economics differ. Segment names are assigned from centroids by rule so they don't depend on K-means' arbitrary
cluster numbers. The segments match standard card personas (transactors, revolvers, delinquent, dormant), which
is a sanity check rather than proof.

## 8. Retention prioritisation

For each active customer in September:

```
value_if_retained      = max(risk-adjusted monthly contribution, 0) x 12
expected_value_at_risk = P(dormant) x value_if_retained
expected_net_benefit   = P(dormant) x save_rate x value_if_retained - contact_cost
```

Rules, in order: PD >= 50% -> exclude (risk management's job, not marketing's); not profitable -> exclude;
net benefit <= 0 -> monitor; otherwise rank by net benefit and contact up to 10% of active customers.

Save rate (20%) and contact cost (NT$100) are assumptions. The model predicts who is likely to go dormant, not
who responds to an offer, so a real campaign needs a randomised holdout to measure uplift.

## 9. Testing

65 pytest tests: validation rules on synthetic bad rows, grain/PK checks, month mapping against the raw file,
independent pandas recalculation of SQL features, label-leakage guard on the feature SQL, train/test timing,
profit identities, the loss-calibration identity, retention business rules, model input integrity, saved
model sanity checks, and consistency checks between the Power BI report and its data model.

**Reproducibility.** Seeds are fixed everywhere. I found that LightGBM's multithreaded training was not
bit-for-bit reproducible: the headline metrics were identical across runs, but out-of-fold PDs differed in the
last decimals, which moved a handful of customers across the 50% PD cut-off (56 vs 57 "Contact now"). Setting
`deterministic=True` and `force_row_wise=True` fixed it; two consecutive runs now produce identical checksums for
all PD and dormancy scores.

## 10. Power BI layer

The dashboard is a Power BI Project (`.pbip`), which stores the semantic model as TMDL and the report as PBIR
JSON - both plain text. `scripts/build_powerbi.py` generates it from the warehouse: table definitions come from
Postgres `information_schema`, while the 42 DAX measures and 52 visuals are defined in the script. I validated the
generated report files against Microsoft's published JSON schemas before opening them, and a test checks that
every field and measure reference resolves. Visuals use explicit measures (not implicit column sums), and the
model is a small star around `customer_360`; aggregated tables (curves, lift, roll rates) stay disconnected.

Two issues only showed up once the report rendered: the roll-rate matrix showed 100% everywhere because a
`CALCULATE` filter argument replaced the matrix's column filter (fixed with `KEEPFILTERS`), and the custom
currency format needed escaped letters (`\N\T\$#,0`).
