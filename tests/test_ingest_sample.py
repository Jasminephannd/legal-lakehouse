from src.ingest.sample import _extract_year, stratified_sample


def _make_record(jurisdiction: str, date: str | None, version_id: str) -> dict:
    return {
        "version_id": version_id,
        "type": "decision",
        "jurisdiction": jurisdiction,
        "source": "nsw_caselaw",
        "citation": f"Test v Case [{date}] TEST 1" if date else "Test v Case (undated) TEST 1",
        "mime": "text/html",
        "date": date,
        "url": f"https://example.com/{version_id}",
        "when_scraped": "2024-09-13T22:44:32+10:00",
        "text": "Some judgment text.",
    }


def test_extract_year_from_date():
    assert _extract_year("2019-06-21") == "2019"


def test_extract_year_null_becomes_unknown():
    assert _extract_year(None) == "unknown"


def test_covers_multiple_jurisdictions_when_present():
    jurisdictions = ["commonwealth", "new_south_wales", "tasmania"]
    records = [
        _make_record(j, f"{2015 + i % 5}-01-01", f"{j}-{i}")
        for j in jurisdictions
        for i in range(200)
    ]
    sample = stratified_sample(records, target_total=90, jurisdictions=jurisdictions)
    jurisdictions_seen = {r["jurisdiction"] for r in sample}
    assert jurisdictions_seen == set(jurisdictions)


def test_stratification_caps_a_dominant_jurisdiction():
    # A corpus skewed the way the real one is (NSW caselaw ~half of all
    # documents): the abundant jurisdiction must not be allowed to eat
    # the whole sample just because it has far more supply.
    jurisdictions = ["commonwealth", "new_south_wales", "tasmania"]
    records = (
        [_make_record("tasmania", f"{2010 + i % 10}-01-01", f"tas-{i}") for i in range(900)]
        + [_make_record("commonwealth", f"{2010 + i % 10}-01-01", f"cw-{i}") for i in range(50)]
        + [_make_record("new_south_wales", f"{2010 + i % 10}-01-01", f"nsw-{i}") for i in range(50)]
    )
    sample = stratified_sample(records, target_total=90, jurisdictions=jurisdictions)

    tas_count = sum(1 for r in sample if r["jurisdiction"] == "tasmania")
    base_quota = 90 // len(jurisdictions)  # 30
    hard_ceiling = base_quota + max(1, int(base_quota * 0.5))  # 45

    assert tas_count <= hard_ceiling
    assert {r["jurisdiction"] for r in sample} == set(jurisdictions)


def test_year_spread_within_a_jurisdiction():
    records = [
        _make_record("commonwealth", f"{year}-01-01", f"cw-{year}-{i}")
        for year in ["2015", "2016", "2017", "2018", "2019"]
        for i in range(50)
    ]
    sample = stratified_sample(records, target_total=30, jurisdictions=["commonwealth"])
    years_seen = {_extract_year(r["date"]) for r in sample}
    assert len(years_seen) > 1


def test_null_dates_bucketed_as_unknown_not_dropped():
    records = [_make_record("commonwealth", None, f"cw-{i}") for i in range(10)]
    sample = stratified_sample(records, target_total=5, jurisdictions=["commonwealth"])
    assert len(sample) == 5
    assert all(r["date"] is None for r in sample)


def test_sample_never_exceeds_target_total():
    jurisdictions = ["commonwealth", "tasmania"]
    records = [
        _make_record(j, f"{2010 + i % 10}-01-01", f"{j}-{i}")
        for j in jurisdictions
        for i in range(500)
    ]
    sample = stratified_sample(records, target_total=37, jurisdictions=jurisdictions)
    assert len(sample) == 37


def test_duplicate_version_id_not_double_counted():
    dup = _make_record("commonwealth", "2020-01-01", "cw-dup")
    records = [dup, dup, dup] + [
        _make_record("commonwealth", f"{2010 + i}-01-01", f"cw-{i}") for i in range(20)
    ]
    sample = stratified_sample(records, target_total=10, jurisdictions=["commonwealth"])
    version_ids = [r["version_id"] for r in sample]
    assert len(version_ids) == len(set(version_ids))
