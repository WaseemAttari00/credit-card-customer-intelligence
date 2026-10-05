// Power Query (M): load the dashboard tables straight from PostgreSQL instead of the CSVs.
// In Power BI Desktop: Get Data > Blank Query > Advanced Editor, paste ONE block per query,
// and name the query after the table (customer_360, customer_monthly, ...).
// Credentials are entered in Power BI's own prompt; they are never stored in this file.

// ---- query: customer_360
let
    Source = PostgreSQL.Database("localhost:5432", "cc_intel"),
    customer_360 = Source{[Schema = "mart", Item = "customer_360"]}[Data]
in
    customer_360

// ---- query: customer_monthly
// let Source = PostgreSQL.Database("localhost:5432", "cc_intel"),
//     t = Source{[Schema = "mart", Item = "customer_monthly_metrics"]}[Data]
// in t

// Same pattern for the other tables:
//   mart.portfolio_monthly, mart.retention_priority, mart.roll_rates, mart.delinquency_cohorts,
//   mart.assumptions, core.dim_month, ml.segment_profile, ml.model_lift, ml.model_comparison,
//   ml.model_calibration, ml.feature_importance
