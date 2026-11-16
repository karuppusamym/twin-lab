import pytest

from lab_agent.matching import PatternError, compile_match


@pytest.mark.parametrize(
    ("pattern", "method", "path", "query", "expected"),
    [
        ("GET /api/cases/search*", "GET", "/api/cases/search", "", True),
        ("GET /api/cases/search*", "get", "/api/cases/search", "caseNo=C-1", True),
        ("GET /api/cases/search*", "POST", "/api/cases/search", "", False),
        ("GET /api/cases/search*", "GET", "/api/cases/C-1001", "", False),
        ("POST /api/cases/*/notes", "POST", "/api/cases/C-1001/notes", "", True),
        ("POST /api/cases/*/notes", "POST", "/api/cases/C-1001", "", False),
        ("POST /api/cases/*", "POST", "/api/cases/C-1001/notes", "", True),  # * spans "/"
        ("/api/*", "DELETE", "/api/x", "", True),
        ("* /api/*", "PATCH", "/api/x", "", True),
        ("*", "GET", "/anything/at/all", "", True),
        ("GET /", "GET", "/", "", True),
        ("GET /", "GET", "/login", "", False),
        ("GET /a.b", "GET", "/aXb", "", False),  # regex characters are literal
        ("GET /api/cases/search?caseNo=C-1*", "GET", "/api/cases/search", "caseNo=C-1001", True),
        ("GET /api/cases/search?caseNo=C-1*", "GET", "/api/cases/search", "caseNo=C-2001", False),
    ],
)
def test_matches(pattern, method, path, query, expected):
    assert compile_match(pattern).matches(method, path, query) is expected


@pytest.mark.parametrize("pattern", ["", "GET", "GET api/x", "G-T /x", "GET /a b"])
def test_bad_patterns(pattern):
    with pytest.raises(PatternError):
        compile_match(pattern)
