-- Pareto view: what share of total estimated contribution comes from the top X% of customers?
-- Built in sql/marts/08_dashboard_curves.sql; this query just returns it.
SELECT top_percent_of_customers, contribution, cum_contribution, cum_share_of_total
FROM mart.value_concentration
ORDER BY top_percent_of_customers;
