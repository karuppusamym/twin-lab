"""A small permit-application form whose rules live in the page and in its JavaScript, written for
the tests of `labctl.pagerules`. Synthetic data only; served from 127.0.0.1, kept in memory.

`/apply` is one form. What it checks and does, and where each rule is written:

- HTML: `required`, `maxlength`, `type="email"`, `type="date"`, `readonly`, `disabled`, a
  tooltip (`title`), a help text (`aria-describedby`), a character counter, hidden message
  elements, and ASP.NET-style `data-val-*` attributes (`fullName`, `refCode`).
- `/static/apply.js` (the application's own script): jQuery-Validate-style `rules` and
  `messages` (written by hand; `/static/lib/jquery.validate-shim.js` stands in for the library and
  only keeps them), plain checks with their messages, a cross-field rule (the end date not before
  the start date), one of two fields required, `setCustomValidity` on the number plate, an input
  mask (upper-casing, characters removed), a dependent drop-down (`GET /api/regions`), a block
  shown by a check box, a counter, the submit button disabled until the form is valid, and a
  session-timeout warning followed by a sign-out.
- `POST /api/applications`: 201 with the application's number.

Run by hand with `python form_app.py [port]`. Tests use `serve()`.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

REGIONS = {
    "NO": [{"code": "OS", "name": "Oslo"}, {"code": "VL", "name": "Vestland"}],
    "SE": [{"code": "ST", "name": "Stockholm"}, {"code": "SK", "name": "Scania"}],
}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Parking permit</title>
<style>
body { font-family: Verdana, sans-serif; margin: 0; background: #fafafa; color: #222; }
main { padding: 16px; max-width: 640px; }
.field { margin-top: 10px; display: grid; gap: 4px; }
.field.inline { display: flex; align-items: center; gap: 8px; }
.field-error, .session-warning { color: #b00020; }
.form-text, .char-count { color: #555; font-size: 0.9em; }
</style></head>
<body>
<div id="sessionWarn" class="session-warning" role="alert" hidden></div>
<main>
<h1>Apply for a parking permit</h1>
<form id="applyForm" novalidate>
  <div class="field">
    <label for="applicationNo">Application</label>
    <input id="applicationNo" name="applicationNo" readonly value="New">
  </div>
  <div class="field">
    <label for="fullName">Full name</label>
    <input id="fullName" name="fullName" type="text" required maxlength="60" autocomplete="name"
           title="As on your passport" data-val="true" data-val-required="Enter your full name."
           data-val-length="Use at most 60 characters." data-val-length-max="60">
    <span id="fullNameError" class="field-error" data-valmsg-for="fullName" hidden></span>
  </div>
  <div class="field">
    <label for="email">E-mail</label>
    <input id="email" name="email" type="email" aria-describedby="emailHelp"
           placeholder="name@example.test">
    <small id="emailHelp" class="form-text">We send the permit to this address.</small>
    <span id="emailError" class="field-error" hidden></span>
  </div>
  <div class="field">
    <label for="confirmEmail">E-mail again</label>
    <input id="confirmEmail" name="confirmEmail" type="email">
    <span id="confirmEmailError" class="field-error" hidden></span>
  </div>
  <div class="field">
    <label for="phone">Phone</label>
    <input id="phone" name="phone" type="tel" inputmode="tel">
    <span id="phoneError" class="field-error" hidden></span>
  </div>
  <div class="field">
    <label for="refCode">Reference code</label>
    <input id="refCode" name="refCode" data-val="true" data-val-length="Use 6 to 10 characters."
           data-val-length-min="6" data-val-length-max="10"
           data-val-regex="Use capital letters and digits only."
           data-val-regex-pattern="^[A-Z0-9]*$">
    <span class="field-error" data-valmsg-for="refCode" hidden>Use 6 to 10 characters.</span>
  </div>
  <div class="field">
    <label for="startDate">From</label>
    <input id="startDate" name="startDate" type="date" required>
    <label for="endDate">To</label>
    <input id="endDate" name="endDate" type="date" required>
    <span id="dateError" class="field-error" hidden></span>
  </div>
  <div class="field">
    <label for="country">Country</label>
    <select id="country" name="country" required>
      <option value="">Choose a country</option>
      <option value="NO">Norway</option>
      <option value="SE">Sweden</option>
    </select>
    <label for="region">Region</label>
    <select id="region" name="region" disabled>
      <option value="">Choose a country first</option>
    </select>
  </div>
  <div class="field inline">
    <input id="hasVehicle" name="hasVehicle" type="checkbox">
    <label for="hasVehicle">I come by car</label>
  </div>
  <div id="vehicleBlock" class="field" hidden>
    <label for="plate">Number plate</label>
    <input id="plate" name="plate">
    <span id="plateError" class="field-error" hidden></span>
  </div>
  <div class="field">
    <label for="notes">Notes</label>
    <textarea id="notes" name="notes" maxlength="200" rows="3"></textarea>
    <span id="notesCount" class="char-count">0 / 200</span>
  </div>
  <button id="submitBtn" type="submit" disabled>Send application</button>
  <p id="formMessage" class="form-message" role="status"></p>
</form>
</main>
<script src="/static/lib/jquery.validate-shim.js"></script>
<script src="/static/apply.js"></script>
</body></html>
"""

SHIM = """/*! jQuery Validation Plugin shim for a Twin Lab test fixture: no jQuery inside */
window.$ = function (selector) {
  return { validate: function (options) { window.__validateOptions = options; return this; } };
};
"""

SCRIPT = r"""/* Parking permit: the page's own checks (a Twin Lab test fixture, synthetic). */
(function () {
  "use strict";
  var form = document.getElementById("applyForm");
  var fullName = document.getElementById("fullName");
  var email = document.getElementById("email");
  var confirmEmail = document.getElementById("confirmEmail");
  var phone = document.getElementById("phone");
  var refCode = document.getElementById("refCode");
  var startDate = document.getElementById("startDate");
  var endDate = document.getElementById("endDate");
  var country = document.getElementById("country");
  var region = document.getElementById("region");
  var hasVehicle = document.getElementById("hasVehicle");
  var vehicleBlock = document.getElementById("vehicleBlock");
  var plate = document.getElementById("plate");
  var notes = document.getElementById("notes");
  var notesCount = document.getElementById("notesCount");
  var submitBtn = document.getElementById("submitBtn");
  var WARN_AFTER = 14 * 60 * 1000;
  var SIGN_OUT_AFTER = 15 * 60 * 1000;

  // Rules in the jQuery Validate way (the shim keeps them; the checks below do the work).
  $("#applyForm").validate({
    rules: {
      email: { required: true, email: true },
      confirmEmail: { equalTo: "#email" },
      refCode: { minlength: 6, maxlength: 10 }
    },
    messages: {
      email: {
        required: "Give an e-mail address.",
        email: "Give a valid e-mail address, such as name@example.test."
      },
      confirmEmail: { equalTo: "The two e-mail addresses differ." }
    }
  });

  function show(id, text) {
    var el = document.getElementById(id);
    el.textContent = text;
    el.hidden = !text;
  }

  function checkName() {
    if (!fullName.value.trim()) { show("fullNameError", "Enter your full name."); return false; }
    if (fullName.value.length > 60) { show("fullNameError", "Use at most 60 characters."); return false; }
    show("fullNameError", "");
    return true;
  }
  function checkEmail() {
    if (!email.value) { show("emailError", "Give an e-mail address."); return false; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s\/]+$/.test(email.value)) {
      show("emailError", "Give a valid e-mail address, such as name@example.test.");
      return false;
    }
    show("emailError", "");
    return true;
  }
  function checkConfirm() {
    if (confirmEmail.value !== email.value) {
      show("confirmEmailError", "The two e-mail addresses differ.");
      return false;
    }
    show("confirmEmailError", "");
    return true;
  }
  function checkPhone() {
    if (phone.value && !/^\+?[0-9 ]{7,15}$/.test(phone.value)) {
      show("phoneError", "Give a phone number of 7 to 15 digits.");
      return false;
    }
    if (!phone.value && !email.value) {
      show("phoneError", "Give a phone number or an e-mail address.");
      return false;
    }
    show("phoneError", "");
    return true;
  }
  function checkDates() {
    if (startDate.value && endDate.value && endDate.value < startDate.value) {
      show("dateError", "The end date must not be before the start date.");
      return false;
    }
    show("dateError", "");
    return true;
  }
  function checkPlate() {
    plate.setCustomValidity(hasVehicle.checked && !/^[A-Z0-9-]{4,8}$/.test(plate.value)
      ? "Give the plate as on the vehicle papers." : "");
    show("plateError", plate.validationMessage);
    return plate.checkValidity();
  }

  fullName.addEventListener("blur", checkName);
  email.addEventListener("blur", checkEmail);
  confirmEmail.addEventListener("blur", checkConfirm);
  phone.addEventListener("blur", checkPhone);
  startDate.addEventListener("change", checkDates);
  endDate.addEventListener("change", checkDates);
  plate.addEventListener("blur", checkPlate);

  // Masks: the reference code in capitals, the phone number without letters.
  refCode.addEventListener("input", function () { refCode.value = refCode.value.toUpperCase(); });
  phone.addEventListener("input", function () { this.value = this.value.replace(/[^0-9+ ]/g, ""); });

  // The regions of the chosen country, from the API.
  country.addEventListener("change", function () {
    region.innerHTML = "";
    region.disabled = true;
    if (!country.value) return;
    fetch("/api/regions?country=" + encodeURIComponent(country.value))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        data.items.forEach(function (it) { region.add(new Option(it.name, it.code)); });
        region.disabled = false;
      });
  });

  // The vehicle's block, shown by the check box; its plate then required.
  hasVehicle.addEventListener("change", function () {
    vehicleBlock.hidden = !hasVehicle.checked;
    plate.required = hasVehicle.checked;
  });

  notes.addEventListener("input", function () {
    notesCount.textContent = notes.value.length + " / 200";
  });

  // Send stays off until the browser finds the form valid.
  form.addEventListener("input", function () { submitBtn.disabled = !form.checkValidity(); });
  form.addEventListener("change", function () { submitBtn.disabled = !form.checkValidity(); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var ok = [checkName(), checkEmail(), checkConfirm(), checkPhone(), checkDates(), checkPlate()];
    if (ok.indexOf(false) >= 0) { show("formMessage", "Check the marked fields."); return; }
    fetch("/api/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fullName: fullName.value, email: email.value, phone: phone.value,
                             refCode: refCode.value, startDate: startDate.value,
                             endDate: endDate.value, country: country.value,
                             region: region.value, plate: plate.value, notes: notes.value })
    }).then(function (r) { return r.json(); }).then(function (d) {
      document.getElementById("applicationNo").value = d.id;
      show("formMessage", "Application received.");
    });
  });

  // The session: a warning a minute before it ends, then the sign-out page.
  setTimeout(function () {
    var warn = document.getElementById("sessionWarn");
    warn.textContent = "Your session ends in one minute. Save your work.";
    warn.hidden = false;
  }, WARN_AFTER);
  setTimeout(function () { window.location.href = "/signed-out"; }, SIGN_OUT_AFTER);
})();
"""

SIGNED_OUT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Signed out</title>
</head><body><main><h1>You were signed out</h1><p><a href="/apply">Start again</a></p></main>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "FixtureForm/1.0"

    def log_message(self, *args):
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, status: int = 200) -> None:
        self._send(json.dumps(data).encode("utf-8"), "application/json", status)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        if url.path in ("/", "/apply"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/signed-out":
            self._send(SIGNED_OUT.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/static/apply.js":
            self._send(SCRIPT.encode("utf-8"), "text/javascript; charset=utf-8")
        elif url.path == "/static/lib/jquery.validate-shim.js":
            self._send(SHIM.encode("utf-8"), "text/javascript; charset=utf-8")
        elif url.path == "/api/regions":
            country = (parse_qs(url.query).get("country") or [""])[0]
            self.json({"country": country, "items": REGIONS.get(country, [])})
        else:
            self._send(b"not found", "text/plain", 404)

    def do_POST(self):  # noqa: N802
        if urlsplit(self.path).path != "/api/applications":
            self._send(b"not found", "text/plain", 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        if not str(data.get("fullName") or "").strip():
            self.json({"error": "Enter your full name."}, 422)
            return
        server = self.server
        with server.lock:  # type: ignore[attr-defined]
            server.number += 1  # type: ignore[attr-defined]
            number = server.number  # type: ignore[attr-defined]
        self.json({"id": f"APP-{number}", "status": "RECEIVED"}, 201)


class Running:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def serve(port: int = 0) -> Running:
    """Start the form on 127.0.0.1 (a free port unless given)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.number = 2000  # type: ignore[attr-defined]
    server.lock = threading.Lock()  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return Running(server)


if __name__ == "__main__":
    import sys
    import time

    run = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8742)
    print(f"permit form  {run.url}/apply")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        run.stop()
