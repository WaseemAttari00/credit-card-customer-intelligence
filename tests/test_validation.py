"""Unit tests for the data-quality checks (no database needed)."""
import pandas as pd
import pytest

from src.config import load_config
from src.ingestion.extract import EXPECTED_COLUMNS
from src.validation.checks import run_checks

CFG = load_config()["validation"]
META = {"file_sha256": "abc", "source_file": "test.xls", "rows_read": 0}


def good_row(customer_id: int, **overrides) -> dict:
    row = {c: "0" for c in EXPECTED_COLUMNS}
    row.update({"ID": str(customer_id), "LIMIT_BAL": "50000", "SEX": "1", "EDUCATION": "2", "MARRIAGE": "1",
                "AGE": "30", "BILL_AMT1": "1000", "BILL_AMT2": "800", "PAY_AMT1": "800",
                "default payment next month": "0"})
    row.update({k: str(v) for k, v in overrides.items()})
    return row


def make_raw(rows):
    df = pd.DataFrame(rows)[EXPECTED_COLUMNS]
    df.insert(0, "source_row_number", range(3, len(df) + 3))
    return df


def result(results, name):
    return results.set_index("check_name").loc[name]


def test_clean_rows_pass_every_error_check():
    raw = make_raw([good_row(i) for i in range(1, 6)])
    results, flags, clean, rejected = run_checks(raw, META, CFG, "abc")
    assert results[results.severity == "ERROR"].passed.all()
    assert len(clean) == 5 and rejected.empty


@pytest.mark.parametrize("override,check", [
    ({"AGE": "abc"}, "non_numeric_value"),
    ({"AGE": "150"}, "age_out_of_range"),
    ({"LIMIT_BAL": "0"}, "non_positive_limit"),
    ({"PAY_AMT3": "-5"}, "negative_payment"),
    ({"PAY_0": "12"}, "status_out_of_range"),
    ({"SEX": "3"}, "invalid_sex_code"),
    ({"default payment next month": "2"}, "invalid_target"),
    ({"BILL_AMT1": "100.5"}, "non_integer_value"),
])
def test_error_rows_are_quarantined_with_reason(override, check):
    raw = make_raw([good_row(1), good_row(2, **override)])
    results, flags, clean, rejected = run_checks(raw, META, CFG, "abc")
    assert result(results, check).rows_failed == 1
    assert list(clean.ID) == [1]
    assert check in rejected.reject_reasons.iloc[0]


def test_missing_value_is_quarantined():
    row = good_row(2)
    row["AGE"] = None
    results, _, clean, rejected = run_checks(make_raw([good_row(1), row]), META, CFG, "abc")
    assert result(results, "missing_value").rows_failed == 1
    assert len(rejected) == 1


def test_duplicate_ids_quarantine_both_rows():
    raw = make_raw([good_row(1), good_row(1, AGE=40), good_row(2)])
    results, _, clean, rejected = run_checks(raw, META, CFG, "abc")
    assert result(results, "duplicate_customer_id").rows_failed == 2
    assert list(clean.ID) == [2]


def test_warnings_are_flagged_but_loaded():
    raw = make_raw([good_row(1, EDUCATION=5), good_row(2, MARRIAGE=0), good_row(3, BILL_AMT4=-200),
                    good_row(4, BILL_AMT2=90000)])
    results, flags, clean, rejected = run_checks(raw, META, CFG, "abc")
    assert rejected.empty and len(clean) == 4
    assert result(results, "undocumented_education_code").rows_failed == 1
    assert result(results, "undocumented_marriage_code").rows_failed == 1
    assert result(results, "credit_balance").rows_failed == 1
    assert result(results, "over_limit_balance").rows_failed == 1
    assert set(flags.check_name) >= {"undocumented_education_code", "credit_balance"}


def test_duplicate_profile_ignores_id():
    raw = make_raw([good_row(1), good_row(2), good_row(3, AGE=55)])
    results, *_ = run_checks(raw, META, CFG, "abc")
    assert result(results, "duplicate_profile").rows_failed == 2


def test_delinquent_with_no_balance_is_flagged():
    raw = make_raw([good_row(1, PAY_0=2, BILL_AMT1=0), good_row(2)])
    results, *_ = run_checks(raw, META, CFG, "abc")
    assert result(results, "delinquent_with_no_balance").rows_failed == 1


def test_balance_drop_exceeding_payment_is_flagged():
    # Aug bill 800 -> Sep bill 0 with only 300 paid: the drop is not explained by the payment
    raw = make_raw([good_row(1, BILL_AMT1=0, PAY_AMT1=300), good_row(2)])
    results, *_ = run_checks(raw, META, CFG, "abc")
    assert result(results, "balance_drop_exceeds_payment").rows_failed == 1


def test_missing_column_stops_the_pipeline():
    raw = make_raw([good_row(1)]).drop(columns=["AGE"])
    with pytest.raises(ValueError, match="AGE"):
        run_checks(raw, META, CFG, "abc")


def test_checksum_mismatch_is_reported():
    results, *_ = run_checks(make_raw([good_row(1)]), META, CFG, "different-hash")
    assert result(results, "file_checksum").rows_failed == 1


def test_status_coding_shift_detected():
    rows = [good_row(i, PAY_0=1) for i in range(1, 30)] + [good_row(100, PAY_2=1)]
    results, *_ = run_checks(make_raw(rows), META, CFG, "abc")
    assert result(results, "status_coding_shift_latest_month").rows_failed == 1
