-- Default rate, predicted PD and expected loss by customer attribute, in one pass with GROUPING SETS.
-- (The data has no geography or card-product field, so credit-limit tier stands in for "product tier".)
SELECT
    CASE WHEN GROUPING(limit_tier) = 0     THEN 'Credit limit tier'
         WHEN GROUPING(age_band) = 0       THEN 'Age band'
         WHEN GROUPING(education) = 0      THEN 'Education'
         WHEN GROUPING(marital_status) = 0 THEN 'Marital status'
         WHEN GROUPING(sex) = 0            THEN 'Sex'
         WHEN GROUPING(segment) = 0        THEN 'Segment' END                AS dimension,
    COALESCE(limit_tier, age_band, education, marital_status, sex, segment) AS grp,
    COUNT(*)                                                                AS customers,
    AVG(actual_default_oct::INT)                                            AS actual_default_rate,
    AVG(pd_score)                                                           AS avg_pd,
    SUM(ecl_next_month)                                                     AS total_ecl,
    SUM(ead_estimate)                                                       AS total_ead,
    SUM(ecl_next_month) / NULLIF(SUM(ead_estimate), 0)                      AS ecl_rate
FROM mart.customer_360
GROUP BY GROUPING SETS ((limit_tier), (age_band), (education), (marital_status), (sex), (segment))
ORDER BY dimension, grp;
