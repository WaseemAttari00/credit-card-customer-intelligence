-- core.fact_default_outcome
-- Grain: one row per customer. PK: customer_id.
-- The default label (missed payment in Oct 2005) is kept in its own table, separate from
-- anything used to build features. Feature SQL never reads this table; only the modeling
-- code joins it, which makes it easy to check that the label cannot leak into features.
DROP TABLE IF EXISTS core.fact_default_outcome CASCADE;

CREATE TABLE core.fact_default_outcome AS
SELECT
    s.customer_id,
    7                                  AS outcome_month_index,   -- Oct 2005 in core.dim_month
    (s.default_next_month = 1)         AS defaulted_next_month
FROM staging.stg_credit_card_clients s;

ALTER TABLE core.fact_default_outcome ADD PRIMARY KEY (customer_id);
ALTER TABLE core.fact_default_outcome
    ADD CONSTRAINT fk_fdo_customer FOREIGN KEY (customer_id) REFERENCES core.dim_customer (customer_id),
    ADD CONSTRAINT fk_fdo_month    FOREIGN KEY (outcome_month_index) REFERENCES core.dim_month (month_index);
