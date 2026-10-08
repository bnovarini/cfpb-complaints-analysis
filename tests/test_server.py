import os, pathlib, pytest

d = os.environ.get("CFPB_DATA_DIR")
pytestmark = pytest.mark.skipif(not d or not (pathlib.Path(d) / "complaints.parquet").exists(), reason="needs CFPB_DATA_DIR with the Parquet files")


def test_filters_validate():
    from cfpb_complaints.mcp_server import _filters
    with pytest.raises(ValueError):
        _filters(state="Texas")
    with pytest.raises(ValueError):
        _filters(date_from="01/02/2024")
    w, p = _filters(company_contains="navy", date_from="2024-01")
    assert "company ILIKE" in w and p[-1] == "2024-01-01"


def test_counts_and_search():
    from cfpb_complaints.mcp_server import complaint_counts, search_narratives, get_complaint
    r = complaint_counts(group_by=["year"], date_from="2024-01", date_to="2024-12")
    assert r[0]["year"] == 2024 and r[0]["complaints"] == 2734268
    s = search_narratives(phrase="overdraft fee", date_from="2024-01", date_to="2024-01", limit=2)
    assert s and "snippet" in s[0]
    assert get_complaint(1)["error"]


def test_unknown_group_by_rejected():
    from cfpb_complaints.mcp_server import complaint_counts
    with pytest.raises(ValueError):
        complaint_counts(group_by=["nope"])
