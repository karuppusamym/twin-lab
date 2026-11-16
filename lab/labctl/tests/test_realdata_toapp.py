"""Masked real rows into a demo app's lab: names mapped onto the demo schema, receipt required."""

import json

import pytest

from labctl.data import ddl
from labctl.realdata import toapp

SCHEMA = """
CREATE TABLE qc_reviews (review_id INTEGER PRIMARY KEY, review_no VARCHAR(10) NOT NULL, status VARCHAR(12));
CREATE TABLE review_audit (audit_id INTEGER PRIMARY KEY, review_id INTEGER, event VARCHAR(20));
"""


def test_real_names_match_by_name_and_by_the_mapping():
    rows = {
        "dbo.QC_REVIEWS": [
            {"REVIEW_ID": 1, "ReviewNo": "R-1", "STATUS": "OPEN", "LEGACY_FLAG": "Y"}
        ],
        "RVW_AUDIT_LOG": [{"AUD_ID": 7, "REVIEW_ID": 1, "EVENT": "CREATED"}],
        "AUDIT_ARCHIVE": [{"x": 1}],
    }
    mapping = {
        "tables": {"RVW_AUDIT_LOG": "review_audit"},
        "columns": {"RVW_AUDIT_LOG": {"AUD_ID": "audit_id"}},
        "skip": ["AUDIT_ARCHIVE"],
    }
    out = toapp.map_rows(rows, ddl.parse(SCHEMA), mapping)
    assert out.errors == []
    assert out.rows == {
        "qc_reviews": [{"review_id": 1, "review_no": "R-1", "status": "OPEN"}],
        "review_audit": [{"audit_id": 7, "review_id": 1, "event": "CREATED"}],
    }
    assert any("LEGACY_FLAG" in n for n in out.notes)  # dropped, and said so
    assert any("AUDIT_ARCHIVE" in n and "skipped" in n for n in out.notes)


def test_an_unknown_table_stops_the_load():
    out = toapp.map_rows({"CUSTOMERS": [{"id": 1}]}, ddl.parse(SCHEMA))
    assert out.errors and "CUSTOMERS" in out.errors[0]


def test_only_masked_rows_with_an_unchanged_receipt_are_loaded(tmp_path):
    rows, manifest = tmp_path / "masked.json", tmp_path / "mask.yaml"
    rows.write_text(json.dumps({"qc_reviews": []}), encoding="utf-8")
    manifest.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(toapp.LoadError, match="no mask receipt"):
        toapp.check_receipt(rows)
    toapp.write_receipt(rows, manifest)
    assert toapp.check_receipt(rows)["manifest"] == str(manifest)
    rows.write_text(json.dumps({"qc_reviews": [{"review_id": 1}]}), encoding="utf-8")
    with pytest.raises(toapp.LoadError, match="changed after it was masked"):
        toapp.check_receipt(rows)
