"""A view becomes a table of the same name and columns; procedures are named, not created."""

from __future__ import annotations

from labctl.data import ddl

SQLSERVER = """
CREATE TABLE dbo.loans (loan_id INT PRIMARY KEY, loan_no NVARCHAR(20) NOT NULL, amount DECIMAL(12,2));
CREATE TABLE dbo.qc_reviews (review_id INT PRIMARY KEY, loan_id INT REFERENCES dbo.loans(loan_id),
  status NVARCHAR(12), submitted_at DATETIME2);
GO
CREATE VIEW dbo.v_open_reviews AS
SELECT r.review_id, l.loan_no AS loan_number, r.status, DATEDIFF(day, r.submitted_at, GETDATE()) AS age_days
FROM dbo.qc_reviews r JOIN dbo.loans l ON l.loan_id = r.loan_id
WHERE r.status = 'SUBMITTED';
GO
CREATE VIEW dbo.v_loans (id, number) AS SELECT loan_id, loan_no FROM dbo.loans;
GO
CREATE VIEW dbo.v_all AS SELECT l.* FROM dbo.loans l;
GO
CREATE PROCEDURE dbo.usp_close_review @id INT AS BEGIN UPDATE dbo.qc_reviews SET status = 'CLOSED' WHERE review_id = @id; END
GO
"""


def tables():
    found, problems = ddl.parse_with_problems(SQLSERVER)
    return {t.name: t for t in found}, problems


def cols(t):
    return [(c.name, c.type) for c in t.columns]


def test_views_become_tables_typed_from_their_sources():
    found, problems = tables()
    v = found["v_open_reviews"]
    assert cols(v)[:3] == [("review_id", found["qc_reviews"].columns[0].type),
                           ("loan_number", found["loans"].columns[1].type),
                           ("status", found["qc_reviews"].columns[2].type)]  # fmt: skip
    assert cols(v)[3] == ("age_days", "TEXT")  # an expression: type unknown
    assert "stands in for the view" in v.note
    assert [c.name for c in found["v_loans"].columns] == ["id", "number"]
    assert found["v_loans"].columns[0].type == found["loans"].columns[0].type
    assert [c.name for c in found["v_all"].columns] == ["loan_id", "loan_no", "amount"]
    assert any("view v_open_reviews becomes a table" in p for p in problems)


def test_procedures_are_named_not_created():
    found, problems = tables()
    assert "usp_close_review" not in found
    assert any("procedure usp_close_review is not created" in p for p in problems)


def test_view_tables_get_rows_like_any_table():
    from labctl.data import synthetic

    found, _ = tables()
    rows = synthetic.generate(list(found.values()), 7, {"v_open_reviews": 5, "loans": 3}, None)
    assert len(rows["v_open_reviews"]) == 5
    assert set(rows["v_open_reviews"][0]) == {"review_id", "loan_number", "status", "age_days"}
