"""The database link reads statement text from the Postgres wire protocol, in any chunking."""

from __future__ import annotations

import struct

from lab_agent.dbproxy import SSL_REQUEST, StatementScanner


def startup() -> bytes:
    body = struct.pack("!i", 196608) + b"user\0bot\0database\0qtt\0\0"
    return struct.pack("!i", len(body) + 4) + body


def msg(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!i", len(body) + 4) + body


def q(sql: str) -> bytes:
    return msg(b"Q", sql.encode() + b"\0")


def parse(sql: str) -> bytes:
    return msg(b"P", b"s1\0" + sql.encode() + b"\0" + struct.pack("!h", 0))


def scan(stream: bytes, chunk: int) -> list[str]:
    seen: list[str] = []
    s = StatementScanner(seen.append)
    for i in range(0, len(stream), chunk):
        s.feed(stream[i : i + chunk])
    return seen


def test_simple_and_extended_statements_in_any_chunking():
    stream = (
        startup()
        + q("SELECT * FROM qc_reviews WHERE status = 'OPEN'")
        + parse("UPDATE qc_reviews SET status = $1 WHERE id = $2")
        + msg(b"B", b"\0s1\0" + b"\0" * 6)
        + msg(b"S", b"")
    )
    expect = [
        "SELECT * FROM qc_reviews WHERE status = 'OPEN'",
        "UPDATE qc_reviews SET status = $1 WHERE id = $2",
    ]
    for chunk in (1, 3, 7, 64, len(stream)):
        assert scan(stream, chunk) == expect


def test_ssl_request_refused_then_plain_startup():
    ssl = struct.pack("!ii", 8, SSL_REQUEST)
    assert scan(ssl + startup() + q("SELECT 1 FROM loans"), 5) == ["SELECT 1 FROM loans"]


def test_tls_is_left_alone():
    ssl = struct.pack("!ii", 8, SSL_REQUEST)
    assert scan(ssl + b"\x16\x03\x01" + b"x" * 200, 16) == []


def test_huge_message_skipped_and_reading_goes_on():
    big = msg(b"d", b"x" * (1024 * 1024 + 10))  # COPY data
    assert scan(startup() + big + q("SELECT * FROM cases"), 4096) == ["SELECT * FROM cases"]


def test_garbage_never_raises():
    s = StatementScanner(lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    s.feed(startup() + q("SELECT * FROM x"))  # the callback fails: scanning stops quietly
    assert s.off
