-- core.dim_month
-- Grain: one row per calendar month. The data is monthly (statement level), so a
-- daily date dimension would add nothing. Includes Oct 2005, the month the default
-- label refers to, flagged as month_type = 'outcome'.
DROP TABLE IF EXISTS core.dim_month CASCADE;

CREATE TABLE core.dim_month AS
SELECT
    gs                                                   AS month_index,      -- 1 = Apr 2005 ... 6 = Sep 2005, 7 = Oct 2005
    to_char(d, 'YYYYMM')::INT                            AS month_key,
    d::DATE                                              AS month_start,
    (d + INTERVAL '1 month' - INTERVAL '1 day')::DATE    AS month_end,
    to_char(d, 'Mon YYYY')                               AS month_label,
    EXTRACT(QUARTER FROM d)::INT                         AS calendar_quarter,
    CASE WHEN gs <= 6 THEN 'statement' ELSE 'outcome' END AS month_type,
    (gs = 6)                                             AS is_latest_statement
FROM generate_series(1, 7) AS gs
CROSS JOIN LATERAL (SELECT DATE '2005-03-01' + make_interval(months => gs) AS d) AS x;

ALTER TABLE core.dim_month ADD PRIMARY KEY (month_index);
CREATE UNIQUE INDEX ON core.dim_month (month_key);
