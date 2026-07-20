from src.ops.reconcile import format_report, reconcile


def test_balanced_when_bronze_equals_silver_plus_rejected():
    result = reconcile(bronze_count=2000, silver_count=1950, rejected_count=50)
    assert result.balanced is True
    assert result.delta == 0


def test_unbalanced_when_records_are_missing():
    result = reconcile(bronze_count=2000, silver_count=1900, rejected_count=50)
    assert result.balanced is False
    assert result.delta == 50


def test_report_includes_balanced_status():
    result = reconcile(bronze_count=100, silver_count=90, rejected_count=10)
    report = format_report(result)
    assert "BALANCED" in report
    assert "bronze:   100" in report


def test_report_flags_mismatch_and_shows_delta():
    result = reconcile(bronze_count=100, silver_count=80, rejected_count=10)
    report = format_report(result)
    assert "MISMATCH" in report
    assert "delta" in report
    assert "10" in report  # the actual delta value


def test_reconcile_handles_zero_rejected():
    result = reconcile(bronze_count=500, silver_count=500, rejected_count=0)
    assert result.balanced is True
