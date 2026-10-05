-- "What if we can only contact X% of active customers?"
-- The curve is built in sql/marts/08_dashboard_curves.sql (so Power BI can read it); this query just returns it.
SELECT contact_cost, customers_contacted, share_of_active_contacted, cum_benefit_model, cum_benefit_random
FROM mart.retention_budget_curve
ORDER BY contact_cost, customers_contacted;
