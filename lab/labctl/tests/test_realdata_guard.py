"""Review finding 11: the replica guard refuses every way a write can hide in a read."""

import pytest

from labctl.realdata._db import WriteRefused, check_read_only
from labctl.realdata.closure import Seed


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers WHERE id = ?; DELETE FROM customers",
        "WITH c AS (SELECT id FROM customers) DELETE FROM c",
        "SELECT * INTO copy_of_customers FROM customers",
        "SELECT 1 -- ; DROP TABLE x",
        "SELECT 1 /* */ FROM t",
        "UPDATE customers SET email = NULL",
        "EXEC sp_who",
        "PRAGMA journal_mode = WAL",
    ],
)
def test_writes_hidden_in_reads_are_refused(sql):
    with pytest.raises(WriteRefused):
        check_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers WHERE id = ?",
        "SELECT * FROM customers WHERE note = 'call; then DELETE later'",  # inside a literal
        "WITH c AS (SELECT id FROM customers) SELECT * FROM c",
        "  -- leading comment\nSELECT 1;",
        "PRAGMA index_list('customers')",
        "SELECT updated_at, created_by FROM t",  # words that only contain a keyword
    ],
)
def test_plain_reads_pass(sql):
    check_read_only(sql)


@pytest.mark.parametrize(
    "where", ["id = :id; DELETE FROM customers", "id = :id -- x", "id = :id /* */"]
)
def test_a_seed_condition_is_one_condition(where):
    with pytest.raises(ValueError, match="one condition"):
        Seed(table="customers", where=where)
