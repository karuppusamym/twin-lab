"""Cookies by name only: who sets them, which requests send them, the body fields that carry them
and the shape of their value; never a value (Demoblaze trial: `user=<guid>` keyed the cart)."""

from __future__ import annotations

import json

from labctl import cookies

GUID = "3f0c2b1e-8a1d-4c55-9f0e-5a7b6c1d2e3f"
SECRET = "c2VjcmV0LXNlc3Npb24tdG9rZW4tdmFsdWU"


def _har():
    def entry(method, url, cookie=None, body=None, set_cookie=None):
        req = {"method": method, "url": url, "headers": []}
        if cookie:
            req["headers"].append({"name": "Cookie", "value": cookie})
        if body is not None:
            req["postData"] = {"mimeType": "application/json", "text": json.dumps(body)}
        resp = {"status": 200, "headers": [], "content": {"mimeType": "text/html", "text": ""}}
        if set_cookie:
            resp["headers"].append({"name": "Set-Cookie", "value": set_cookie})
        return {"request": req, "response": resp}

    return {"log": {"entries": [
        entry("GET", "https://www.shop.example/index.html", cookie=f"user={GUID}"),
        entry("GET", "https://www.shop.example/imgs/a.jpg", cookie=f"user={GUID}; sid={SECRET}"),
        entry("POST", "https://api.shop.example/viewcart", body={"cookie": f"user={GUID}"}),
        entry("POST", "https://www.shop.example/login", set_cookie=f"sid={SECRET}; Path=/; HttpOnly"),
    ]}}  # fmt: skip


SCRIPT = ('document.cookie = "user=" + guid();\n'
          "function ok(t){ document.cookie = 'tokenp_' + \"=\" + t + \"; expires=\" + d; }")  # fmt: skip


def test_cookies_by_name_with_their_use():
    found = {c["name"]: c for c in cookies.observe([_har()], [("/js/index.js", SCRIPT)])}
    user, sid, token = found["user"], found["sid"], found["tokenp_"]
    assert user["set_by"] == ["the script `/js/index.js`"] and user["sent"] == 2
    assert user["value"] == "a UUID"
    assert user["in_body"] == ["`cookie` of `POST /viewcart`"]
    assert sid["set_by"] == ["the answer to POST /login"]
    assert sid["attributes"] == ["Path", "HttpOnly"]
    assert token["sent"] == 0 and token["attributes"] == ["Expires"]
    text = "\n".join(cookies.brief_lines(list(found.values())))
    assert GUID not in text and SECRET not in text and "`user`" in text and "`tokenp_`" in text
