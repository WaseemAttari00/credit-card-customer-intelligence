# Findings and recommendations

These follow from the analysis in `notebooks/04_business_analysis.ipynb`. NTD amounts for revenue, cost and
profit are estimates built on the assumptions in `config/config.yaml`; default rates, balances and segment
sizes are observed.

## What the data shows

1. **The book grew and got riskier over the six months.** Balances rose 32% (NT$1.17B to NT$1.54B), portfolio
   utilization went from 23% to 31%, and the share of accounts 60+ days past due rose from about 10% to 15%
   (Apr to Aug). 22.1% of customers missed their October payment.
2. **Ordinary revolvers carry the portfolio.** Five behavioural segments:

   | Segment | Customers | Oct default rate | Avg P(dormant) | Est. monthly risk-adj. contribution |
   |---|---:|---:|---:|---:|
   | Revolvers | 12,073 | 16.3% | 0.3% | NT$7.1M |
   | Heavy-spend revolvers | 4,026 | 20.3% | 0.3% | NT$1.1M |
   | Transactors | 7,181 | 14.4% | 2.7% | -NT$0.2M |
   | Delinquent revolvers | 3,156 | 62.8% | 0.5% | -NT$0.2M |
   | Dormant / low use | 3,564 | 23.4% | 7.1% | -NT$0.5M |

   Revolvers produce about 96% of the total estimated contribution. Value is concentrated: the top 10% of
   customers account for 69% of it and the bottom ~40% are a net drag.
3. **Delinquent revolvers earn revenue but give it all back.** They hold 11% of balances but 29% of expected
   credit loss; their estimated NT$3.2M of monthly revenue is almost fully offset by NT$3.0M of expected loss.
4. **Dormancy risk sits with low-value customers.** The customers most likely to go dormant are dormant/low-use
   customers and transactors with small balances. Valuable revolvers rarely go dormant (0.3%). The total 12-month
   value at risk from dormancy is about NT$0.3M, compared with about NT$10M of expected credit loss *per month*.
5. **Lower credit limits go with much higher default rates** (36% under NT$50k vs 14% at NT$300k+). This
   reflects how the bank assigned limits, not an effect of the limit itself.

## Recommendations

### 1. Put risk management ahead of retention spending
- **What:** focus effort on preventing losses in the high-PD group instead of on dormancy campaigns.
- **Who:** the 3,706 customers with predicted PD >= 50% (12% of the book; 3,668 of them active), most of them in
  the delinquent-revolver segment.
- **Why:** expected credit loss (~NT$10M/month) is more than 30 times the 12-month value at risk from dormancy.
- **Evidence:** test ROC-AUC 0.785; the top PD decile had a 70% default rate (3.2x the average) and
  predicted vs observed rates match by decile.
- **Actions to test:** early collections contact, payment-plan offers, and freezing limit increases.
- **Risks:** a false positive costs goodwill and possibly spend; the right cut-off depends on that cost
  (thresholds for several cost assumptions are in `reports/tables/pd_cost_thresholds.csv`). The PD model was
  only validated on one month, so it should be monitored for drift.

### 2. Use cheap channels for dormancy prevention, and only for a short list
- **What:** send low-cost nudges (app push, email, statement message) rather than paid offers.
- **Who:** the top of the priority list in `mart.retention_priority`. At NT$100 per contact only 57 customers
  have a positive expected net benefit; at NT$10 per contact about 960 do. A 10% contact budget (~2,760
  customers) is not the constraint - contacting more people in model order loses money.
- **Why:** the model finds likely-dormant customers well (top 10% captures 68% of them out-of-time), but most of
  them earn little, so expensive offers don't pay back.
- **Risks:** the 20% save rate is an assumption. The model predicts dormancy, not response to an offer.

### 3. Measure retention with a holdout before scaling it
- **What:** randomly hold out part of the priority list (e.g. 20-30%) and compare 2-month activity and
  contribution between contacted and held-out customers.
- **Why:** this measures the real save rate and incremental profit, which replaces the biggest assumption in the
  prioritisation.
- **Risk:** small samples - with ~1,000 contacts and a ~8% base rate, only large effects will be detectable, so
  the test may need to run over several months.

### 4. Review pricing/limits for delinquent revolvers rather than growing them
- **What:** no limit increases or balance-transfer offers for this segment; review limits on accounts with
  sustained 60+ DPD.
- **Who:** delinquent revolvers (3,156) and other customers in the "Exclude - high credit risk" tier.
- **Evidence:** 62.8% Oct default rate; they generate 29% of expected loss on 11% of balances.
- **Risk:** about half of newly 60+ DPD accounts cure within two months (cohort analysis), so blanket limit cuts
  would also hit customers who would have recovered. Use PD rather than segment membership for the decision.

### 5. Keep the profitable revolvers profitable
- **What:** monitor revolvers' utilization trend and payment ratio, and avoid actions that push them to pay down
  (they rarely go dormant, so retention offers aren't needed).
- **Who:** revolvers and heavy-spend revolvers with PD < 30%.
- **Why:** they generate most of the estimated contribution.
- **Risk:** their value comes mostly from interest on revolving balances, so it is also where credit risk builds
  up if conditions worsen (as they did across these six months).

## Limitations that affect these recommendations

- Six months of data from one bank in one crisis period; patterns may not hold elsewhere or later.
- Revenue, cost and loss figures are estimates; the level of expected loss is calibrated to an assumed 8% annual
  loss rate (results at 4% and 12% are in the sensitivity table).
- "Default" is a broad missed-payment label, and "churn" is a dormancy proxy, not closure.
- No transaction, product, channel or marketing data, so the recommendations can't target by merchant category,
  product or channel response.
