"""Field rules and page behaviour (labctl.pagerules).

Units first, no browser: the tokeniser, each shape read from a script, the rules the HTML
declares, the levels, the masking of messages, the opt-in that carries the scripts and its
check. Then the permit form (fixtures/formdesk) recorded with the real recorder and taken through
`labctl brief` and `labctl build package`. Then the reference applications: Angular (its source
is in the folder, so the validators it really has are written down below by hand) and JSF
(skipped where Java or its jars are missing).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE / "fixtures" / "formdesk"))

import form_app  # noqa: E402
import form_flow  # noqa: E402

from labctl import pagerules as pr  # noqa: E402
from labctl import sanitize  # noqa: E402
from labctl.behaviour import dom as domlib  # noqa: E402
from labctl.builder import complete  # noqa: E402
from labctl.builder import package as pkg  # noqa: E402

# =================================================================================================
# The tokeniser
# =================================================================================================


def kinds(src: str) -> list[tuple[str, str]]:
    return [(t.kind, t.text) for t in pr.tokenize(src)]


def test_strings_are_decoded_and_never_end_early():
    toks = kinds(r"""a = 'it\'s'; b = "say \"hi\" A\x42"; c = 'x // not a comment';""")
    strs = [t for k, t in toks if k == "str"]
    assert strs == ["it's", 'say "hi" AB', "x // not a comment"]


def test_regex_literals_and_division_are_told_apart():
    toks = pr.tokenize(r"x = a / b / c; r = /ab\/c[/]d/gi.test(v); return /^\d+$/; i++ / 2;")
    regexes = [(t.text, t.flags) for t in toks if t.kind == "regex"]
    assert regexes == [(r"ab\/c[/]d", "gi"), (r"^\d+$", "")]
    assert [t.text for t in toks if t.kind == "punct" and t.text == "/"] == ["/", "/", "/"]
    # after a name or a closing bracket it divides; after `(`, `=`, `,`, `!`, `return` it is a regex
    assert pr.tokenize("f(/a/)")[2].kind == "regex"
    assert pr.tokenize("x = (a) / 2")[-2].kind == "punct"
    assert pr.tokenize("!/z/.test(q)")[1].kind == "regex"


def test_template_literals_nest_and_keep_their_expressions_as_tokens():
    toks = pr.tokenize("t = `a ${ b + `in ${c}` } /x/ end`; d = 1 / 2;")
    tmpl = [(t.text, t.flags) for t in toks if t.kind == "tmpl"]
    assert tmpl == [("a ", "head"), ("in ", "head"), ("", "tail"), (" /x/ end", "tail")]
    assert ("name", "b") in [(t.kind, t.text) for t in toks]
    assert not [t for t in toks if t.kind == "regex"]  # `/x/` is template text
    code = pr.Code("m = `Use at least ${n} characters.`;")
    assert [s for _, s in code.strings(0, code.n)] == ["Use at least {…} characters."]


def test_comments_are_apart_and_hide_nothing():
    toks, comments = pr.scan("a = 1; // b = '\n/* c = 'x' */ d = '/* e */';")
    assert [c.text for c in comments] == ["// b = '", "/* c = 'x' */"]
    assert [t.text for t in toks if t.kind == "str"] == ["/* e */"]
    assert "c" not in [t.text for t in toks]


def test_numbers_and_constant_arithmetic_without_running_code():
    assert pr.number("0x1F") == 31 and pr.number("1e3") == 1000.0 and pr.number("1_000") == 1000
    assert pr.arithmetic("14 * 60 * 1000") == 840000
    assert pr.arithmetic("__import__('os')") is None and pr.arithmetic("2 ** 99") is None
    code = pr.Code("var W = 14 * 60 * 1e3; var X = W * 2;")
    assert code.consts == {"W": 840000, "X": 1680000}


# =================================================================================================
# The shapes
# =================================================================================================


def rules_of(src: str) -> list[tuple]:
    out = []
    for f in pr.analyse(src, "test.js"):
        if f["kind"] == "rule":
            out.append((f["field"], f["rule"], f.get("value"), f.get("message")))
        elif f["kind"] == "cross":
            out.append((tuple(f["fields"]), f["rule"], f.get("message")))
    return out


def test_angular_validators_in_minified_code_are_tied_to_their_control():
    src = ('form=new A({title:new d("",{nonNullable:!0,validators:[b.required,b.maxLength(80)]}),'
           'code:new d("",[b.required,b.pattern(/^[A-Z]{3}$/)]),age:["",[b.min(18),b.max(99)]],'
           'mail:this.fb.control("",b.email),note:new d("")});u.required=mC;')  # fmt: skip
    found = set(rules_of(src))
    assert {("title", "required", None, None), ("title", "maxlength", 80, None),
            ("code", "required", None, None), ("code", "pattern", "/^[A-Z]{3}$/", None),
            ("age", "min", 18, None), ("age", "max", 99, None),
            ("mail", "email", None, None)} == found  # fmt: skip


def test_the_angular_reference_bundle_alone_gives_its_validators():
    """From `site/` (the production build, minified), without running anything: the two
    `Validators.required` the source has (`src/app/case-edit.ts`) and their messages."""
    site = REPO / "reference" / "web" / "caseboard-angular" / "site"
    found = [f for p in sorted(site.glob("*.js")) for f in pr.analyse(p.read_text("utf-8"), p.name)]
    rules = {(f["field"], f["rule"]) for f in found if f["kind"] == "rule"}
    assert rules == {("title", "required"), ("owner", "required")}
    messages = {f["message"] for f in found if f["kind"] == "message"}
    assert {"A title is required.", "An owner is required."} <= messages
    assert not [f for f in found if f["kind"] == "timer"]  # the framework's timers are not sessions


def test_jquery_validate_rules_and_messages():
    src = """$("#f").validate({ rules: { email: { required: true, email: true },
      pw2: { equalTo: "#pw" }, code: { rangelength: [2, 4] }, age: "required",
      nick: { required: "#hasNick:checked" } },
      messages: { email: { required: "Give an e-mail address.", email: "Not an address." },
      pw2: "The passwords differ." } });
      $("#zip").rules("add", { minlength: 5, messages: { minlength: "Five digits, please." } });"""
    found = set(rules_of(src))
    assert ("email", "required", None, "Give an e-mail address.") in found
    assert ("email", "email", None, "Not an address.") in found
    assert (("pw2", "pw"), "equals", "The passwords differ.") in found
    assert ("code", "minlength", 2, None) in found and ("code", "maxlength", 4, None) in found
    assert ("age", "required", None, None) in found
    assert ("nick", "required (on a condition)", "hasNick", None) in found
    assert ("zip", "minlength", 5, "Five digits, please.") in found


def test_set_custom_validity_with_its_message_and_condition():
    src = """const pw = document.getElementById("pw"), pw2 = document.querySelector("#pw2");
    pw2.setCustomValidity(pw2.value !== pw.value ? "The passwords differ." : "");
    document.getElementById("code").setCustomValidity("Use the code from the letter.");
    pw.setCustomValidity(pw.value.length < 8 ? "At least 8 characters." : "");"""
    found = set(rules_of(src))
    assert (("pw2", "pw"), "equals", "The passwords differ.") in found
    assert ("code", "custom", None, "Use the code from the letter.") in found
    assert ("pw", "minlength", 8, "At least 8 characters.") in found


def test_plain_checks_near_a_field_and_their_messages():
    src = r"""var n = document.getElementById("name"); var age = $("#age");
    function check() {
      if (!n.value.trim()) { show("Enter your name."); return false; }
      if (n.value.length > 40) { err.textContent = "At most 40 characters."; }
      if (!/^[A-Z]{2}\d{3}$/.test(document.getElementById("ref").value)) alert("Wrong format.");
      if (Number(age.val()) < 18) { show("You must be 18 or older."); }
      if (new Date(document.getElementById("to").value) < new Date(document.getElementById("from").value)) {
        show("The end must not be before the start."); }
      if (!document.getElementById("tel").value && !n.value) { show("Give a phone number or a name."); }
      if (!n.value) { count++; }
      msg.textContent = n.value.length < 2 ? "Too short." : "";
    }"""  # fmt: skip
    found = set(rules_of(src))
    assert ("name", "required", None, "Enter your name.") in found
    assert ("name", "maxlength", 40, "At most 40 characters.") in found
    assert ("ref", "pattern", r"/^[A-Z]{2}\d{3}$/", "Wrong format.") in found
    assert ("age", "min", 18, "You must be 18 or older.") in found
    assert (("to", "from"), "not before", "The end must not be before the start.") in found
    assert (("tel", "name"), "one of", "Give a phone number or a name.") in found
    assert ("name", "minlength", 2, "Too short.") in found
    # `if (!n.value) { count++; }`: no message, nothing refused: not a rule
    assert len([r for r in found if r[0] == "name" and r[1] == "required"]) == 1


def test_behaviour_while_typing_or_choosing():
    src = """var c = document.getElementById("country"), r = document.getElementById("region");
    var box = document.getElementById("car"), block = document.getElementById("carBlock");
    var code = document.getElementById("code"), send = document.getElementById("send");
    c.addEventListener("change", function () {
      fetch("/api/regions?c=" + c.value).then(x => x.json()).then(d => d.forEach(i => r.add(new Option(i.n))));
    });
    box.addEventListener("change", function () { block.hidden = !box.checked; });
    code.addEventListener("input", function () { this.value = this.value.toUpperCase(); });
    $("#phone").mask("99 99 99 99");
    document.getElementById("f").addEventListener("input", function () { send.disabled = !this.checkValidity(); });
    """  # fmt: skip
    found = pr.analyse(src, "b.js")
    got = {(f["kind"], f.get("source") or f.get("field"), f.get("target"), f.get("what"))
           for f in found}  # fmt: skip
    assert ("dependent", "country", "region", None) in got
    assert next(f for f in found if f["kind"] == "dependent")["url"].startswith("/api/regions")
    assert ("toggle", "car", "carBlock", "shown or hidden") in got
    assert ("mask", "code", None, "upper-cases what is typed") in got
    assert ("mask", "phone", None, "mask `99 99 99 99`") in got
    assert ("submit", "f", "send", "enabled when the form is valid") in got


def test_session_timers_with_their_delay_message_and_destination():
    src = """var WARN = 14 * 60 * 1000;
    setTimeout(function () { banner.textContent = "Your session ends in one minute."; }, WARN);
    setTimeout(function () { location.href = "/logout"; }, 15 * 60 * 1000);
    setTimeout(function () { spinner.hidden = true; }, 300);
    setInterval(keepAlive, 60000); function keepAlive() { fetch("/api/ping"); }"""
    timers = [f for f in pr.analyse(src, "t.js") if f["kind"] == "timer"]
    assert [(t["delay_ms"], t.get("goes_to"), t.get("messages")) for t in timers] == [
        (840000, None, ["Your session ends in one minute."]), (900000, "/logout", None),
        (60000, None, None)]  # fmt: skip
    assert timers[2]["repeat"] is True


def test_messages_of_the_code_that_no_shape_took_are_kept_for_tying_by_label():
    found = pr.analyse('x("p",6),n(1,"A title is required."),n(2,"Sign out"),n(3,"Iterable '
                       'cannot be null"),n(4,"Label printer prints blank pages")', "m.js")  # fmt: skip
    assert [f["message"] for f in found if f["kind"] == "message"] == ["A title is required."]


def test_libraries_are_named_not_analysed():
    assert pr.library_name("/js/jquery-3.7.1.min.js", "") == "jQuery"
    assert pr.library_name("/js/jquery.validate.unobtrusive.min.js", "") == (
        "jQuery Validation Unobtrusive")  # fmt: skip
    # named as the code map names them (`labctl.libraries`); the version is kept apart
    assert pr.library_name("/javax.faces.resource/jsf.js.xhtml", "") == "javax.faces"
    assert pr.library_name("/x.js", "/*! Select2 4.1.0 | https://github.com/select2 */") == (
        "Select2")  # fmt: skip
    assert pr.library_name("/static/app.js", "/* the page's own checks */") is None


def test_a_script_of_a_package_folder_is_a_library():
    # Demoblaze trial: a package under node_modules was analysed as the application's own code
    path = "/node_modules/videojs-contrib-hls/dist/videojs-contrib-hls.min.js"
    assert pr.library_name(path, "!function(e){...}") == "videojs-contrib-hls"
    assert pr.library_name("/node_modules/@scope/pkg/index.js", "") == "@scope/pkg"
    assert pr.library_name("/js/node_modules.js", "") is None


# =================================================================================================
# The HTML, the levels, the masking
# =================================================================================================

FORM = """<form id="f"><div class="field"><label for="name">Name</label>
<input id="name" name="name" required maxlength="40" title="As on your passport"
 data-val="true" data-val-required="Enter your name." data-val-length="At most 40 characters."
 data-val-length-max="40"><span class="field-error" data-valmsg-for="name" hidden></span></div>
<div class="field"><label for="mail">Mail</label><input id="mail" name="mail" type="email"
 aria-describedby="mailHelp" placeholder="name@example.test"><small id="mailHelp"
 class="form-text">We write to this address.</small></div>
<input id="pw" name="pw" type="password"><input id="pw2" name="pw2" data-val="true"
 data-val-equalto="The two differ." data-val-equalto-other="*.pw">
<input id="code" data-val="true" data-val-regex="Two letters." data-val-regex-pattern="^[A-Z]{2}$">
<input id="zip" data-rule-minlength="5" data-msg-minlength="Five digits.">
<input id="hours" data-p-required="true" data-p-rmsg="Hours is required" data-p-con="javax.faces.Integer"
 data-p-cmsg="A whole number.">
<input id="from" type="date" min="2026-01-05"><input id="no" readonly>
<select id="st" onchange="mojarra.ab(this,event,'valueChange',0,'f:note')"><option value="a">Open</option></select>
<textarea id="t" maxlength="200"></textarea><span id="tCount" class="char-count">0 / 200</span>
<button id="go" type="submit" disabled>Go</button></form>"""  # fmt: skip


def page_doc(html: str = FORM):
    doc = pr.Doc(set())
    safe = domlib.parse_html(sanitize.page(html, set(), set()).html)
    rec = pr.Rec("r", None, [pr.Snap(1, "after", "/f", pr.raw_tree(html), safe)])
    pr.read_pages(doc, [rec])
    return doc, doc.screens["/f"]


def test_the_html_declares_rules_hints_and_state():
    doc, scr = page_doc()
    f = scr["fields"]
    by = {s: {(r["rule"], r.get("value"), r.get("message"), r["level"]) for r in fld["rules"]}
          for s, fld in f.items()}  # fmt: skip
    assert ("required", None, "Enter your name.", "declared") in by["input#name"]
    assert ("maxlength", 40, "At most 40 characters.", "declared") in by["input#name"]
    assert f["input#name"]["title"] == "As on your passport" and f["input#name"]["label"] == "Name"
    assert ("type", "email", None, "declared") in by["input#mail"]
    assert f["input#mail"]["help"] == "We write to this address."
    assert f["input#mail"]["placeholder"] == "name@example.test"
    assert ("pattern", "^[A-Z]{2}$", "Two letters.", "declared") in by["input#code"]
    assert ("minlength", 5, "Five digits.", "declared") in by["input#zip"]
    assert ("required", None, "Hours is required", "declared") in by["input#hours"]
    assert ("converter", "Integer", "A whole number.", "declared") in by["input#hours"]
    assert ("min", "9999-99-99", None, "declared") in by["input#from"]  # a date: its shape only
    assert f["textarea#t"]["counter"]
    cross = scr["cross"][0]
    assert cross["rule"] == "equals" and cross["fields"] == ["input#pw2", "pw"]
    assert cross["message"] == "The two differ."
    state = {(s["selector"], s["what"]) for s in scr["on_load"]}
    assert {("input#no", "read-only on load"), ("button#go", "disabled on load")} <= state
    ajax = scr["behaviour"][0]
    assert ajax["kind"] == "partial update" and ajax["target"] == "f:note"


def test_levels_merge_to_the_strongest_with_every_piece_of_evidence():
    doc = pr.Doc(set())
    fld = {"selector": "input#a", "rules": [], "_keys": {"a"}}
    doc.add_rule(fld, "required", pr.CODE, "script x")
    doc.add_rule(fld, "required", pr.DECLARED, "HTML", message="Give a.")
    r = fld["rules"][0]
    assert r["level"] == pr.DECLARED and r["evidence"] == ["script x", "HTML"]
    assert r["message"] == "Give a."
    doc.add_rule(fld, "required", pr.CODE, "script y")
    assert r["level"] == pr.DECLARED  # never lowered
    # a message as the recording shows it (a word masked by the sanitiser) is the written one
    assert pr.same_message("Xxxxx your full name.", "Enter your full name.")
    assert not pr.same_message("Enter your name.", "Enter your full name.")


def test_messages_keep_interface_text_and_mask_values():
    doc = pr.Doc({"ingrid"})
    assert doc.msg("Use at least 6 characters.") == "Use at least 6 characters."
    out = doc.msg("No permit for Ingrid under 48213 or ingrid.s@corp.example")
    assert "Ingrid" not in out and "48213" not in out and "corp.example" not in out
    assert out.startswith("No permit for ")


# =================================================================================================
# The opt-in: the application's own scripts
# =================================================================================================


def test_the_opt_in_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv(pr.SCRIPTS_ENV, raising=False)
    assert not pr.carry_scripts()
    monkeypatch.setenv(pr.SCRIPTS_ENV, "carry")
    assert pr.carry_scripts()


def test_the_script_check_finds_secrets_and_other_hosts_never_naming_the_value():
    own = {"app.internal.test:8080"}
    clean = ('var ns = "http://www.w3.org/2000/svg"; fetch("/api/x"); var u = '
             '"https://app.internal.test/help"; var label = "Password";')  # fmt: skip
    assert pr.script_findings(clean, own) == []
    planted = {
        "key": "-----BEGIN RSA PRIVATE KEY-----\nMIIB...",
        "jwt": 'var t = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk";',
        "assign": 'var apiKey = "k3y-9f8e7d6c5b";',
        "pw": 'config = { password: "Harbour-Lamp-42" };',
        "host": 'fetch("https://tracker.other-site.example.org.uk/collect");',
        "aws": "var a = 'AKIAABCDEFGHIJKLMNOP';",
    }
    for name, text in planted.items():
        found = pr.script_findings(text, own)
        assert found, name
        assert all("Harbour" not in f and "k3y" not in f for f in found)
    # a header name in a credential-like key is not a secret
    assert pr.script_findings('var cookieName = "XSRF-TOKEN";', own) == []


def test_carrying_strips_source_maps_refuses_on_a_finding_and_is_checked_again(tmp_path):
    folder = tmp_path / "apps" / "a" / pr.SCRIPTS_DIR
    good = pr.Script("/static/app.js", "var x = 1;\n//# sourceMappingURL=app.js.map\n", True, None,
                     True)  # fmt: skip
    lib = pr.Script("/static/jquery.min.js", "/*! jQuery v3 */", True, "jQuery", True)
    other = pr.Script("/cdn/widget.js", "var w;", False, None, True)
    done = pr._carry(folder, [good, lib, other], {"app.test"}, None)
    assert [f["file"] for f in done["files"]] == ["scripts/app.js"]
    assert (folder / "app.js").read_text("utf-8") == "var x = 1;\n"
    bad = pr.Script("/static/cfg.js", 'var token = "a1b2c3d4e5f6g7";', True, None, True)
    refused = pr._carry(tmp_path / "other", [good, bad], {"app.test"}, None)
    assert refused["refused"] and not (tmp_path / "other").exists()
    assert "a1b2" not in json.dumps(refused)
    # the package step: a file changed after the check, a source map, a library
    (tmp_path / "apps" / "a" / pr.FILE).write_text(json.dumps({"scripts": {"carried": done}}))
    assert pr.package_check(tmp_path, "a") == []
    (folder / "app.js").write_text("var x = 2;", "utf-8")
    (folder / "app.js.map").write_text("{}", "utf-8")
    problems = "\n".join(pr.package_check(tmp_path, "a"))
    assert "not a file the check passed" in problems and "source map is never carried" in problems
    (tmp_path / "apps" / "a" / pr.FILE).write_text(json.dumps({"scripts": {"carried": refused}}))
    assert any("failed the check" in p for p in pr.package_check(tmp_path, "a"))


def test_the_package_index_names_the_rules_file():
    assert "validation rule" in complete.purpose("apps/demo/rules.json", "demo")
    assert "owner's request" in complete.purpose("apps/demo/scripts/app.js", "demo")


# =================================================================================================
# The permit form, recorded, through `labctl brief` and `labctl build package`
# =================================================================================================


def make_root(path: Path) -> Path:
    for d in ("apps", "automations", "lab", "flows", "docs"):
        (path / d).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "AGENTS.md", path / "AGENTS.md")
    shutil.copy(REPO / "docs" / "factory-playbook.md", path / "docs" / "factory-playbook.md")
    return path


def _zip(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode("utf-8", "replace") for n in zf.namelist()}


@pytest.fixture(scope="module")
def permit(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    from labctl import factory, recorder

    root = make_root(tmp_path_factory.mktemp("permit") / "repo")
    rec = root / "evidence" / "permit" / "apply-run"
    run = form_app.serve()
    try:
        recorder.record(f"{run.url}/apply", rec, headless=True, script=form_flow.run)
    finally:
        run.stop()
    factory.generate(root, "permit", force=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        path = pkg.build(root, "permit", root / "packages")
    texts = _zip(path)
    rules = json.loads(texts["apps/permit/rules.json"])
    screen = next(s for s in rules["screens"] if s["path"] == "/apply")
    return SimpleNamespace(root=root, texts=texts, rules=rules, screen=screen,
                           brief=texts["apps/permit/BRIEF.md"], task=texts["BUILD_TASK.md"])  # fmt: skip


def _rule(permit, selector: str, rule: str) -> dict:
    fld = next(f for f in permit.screen["fields"] if f["selector"] == selector)
    return next(r for r in fld["rules"] if r["rule"] == rule)


def test_rules_json_has_each_rule_at_its_level(permit):
    expected = [  # (field, rule, value, message, level): written from form_app.py
        ("input#fullName", "required", None, "Enter your full name.", pr.SEEN),
        ("input#fullName", "maxlength", 60, "Use at most 60 characters.", pr.DECLARED),
        ("input#email", "required", None, "Give an e-mail address.", pr.SEEN),
        ("input#email", "email", None, None, pr.SEEN),
        ("input#email", "type", "email", None, pr.DECLARED),
        ("input#phone", "pattern", r"/^\+?[0-9 ]{7,15}$/", "Give a phone number of 7 to 15 digits.",
         pr.CODE),
        ("input#refCode", "minlength", 6, "Use 6 to 10 characters.", pr.DECLARED),
        ("input#refCode", "maxlength", 10, "Use 6 to 10 characters.", pr.DECLARED),
        ("input#refCode", "pattern", "^[A-Z0-9]*$", "Use capital letters and digits only.",
         pr.DECLARED),
        ("input#startDate", "required", None, None, pr.DECLARED),
        ("input#endDate", "type", "date", None, pr.DECLARED),
        ("select#country", "required", None, None, pr.DECLARED),
        ("input#plate", "pattern", "/^[A-Z0-9-]{4,8}$/", "Give the plate as on the vehicle papers.",
         pr.CODE),
        ("textarea#notes", "maxlength", 200, None, pr.DECLARED),
    ]  # fmt: skip
    for sel, rule, value, message, level in expected:
        r = _rule(permit, sel, rule)
        assert r["level"] == level, (sel, rule, r)
        assert r.get("value") == value or value is None, (sel, rule, r)
        assert message is None or r.get("message") == message, (sel, rule, r)
        assert r["evidence"], (sel, rule)
    # the address message, seen: its example address masked like any address in a message
    assert _rule(permit, "input#email", "email")["message"].startswith(
        "Give a valid e-mail address"
    )
    # no rule the fixture does not have
    every = {(f["selector"], r["rule"]) for f in permit.screen["fields"] for r in f["rules"]}
    extra = (
        every
        - {(s, r) for s, r, *_ in expected}
        - {
            ("input#email", "pattern"),
            ("input#confirmEmail", "type"),
            ("input#phone", "type"),
            ("input#startDate", "type"),
            ("input#endDate", "required"),
            ("input#plate", "required"),
        }
    )
    assert extra == set(), extra


def test_cross_field_rules_behaviour_state_and_session(permit):
    cross = {(tuple(c["fields"]), c["rule"]): c for c in permit.screen["cross_field"]}
    assert cross[(("input#endDate", "input#startDate"), "not before")]["level"] == pr.SEEN
    assert cross[(("input#confirmEmail", "input#email"), "equals")]["message"] == (
        "The two e-mail addresses differ.")  # fmt: skip
    one = cross[(("input#phone", "input#email"), "one of")]
    assert one["level"] == pr.CODE and one["message"] == "Give a phone number or an e-mail address."
    beh = {(b["kind"], b.get("source") or b.get("field"), b.get("target"), b.get("what")): b
           for b in permit.screen["behaviour"]}  # fmt: skip
    dep = beh[("dependent", "select#country", "select#region", "options replaced")]
    assert dep["level"] == pr.SEEN and dep["api"] == "GET /api/regions"
    assert dep["api_file"] == "api/get-api-regions.json" and dep["step"]
    assert "apps/permit/" + dep["api_file"] in permit.texts
    assert beh[("toggle", "input#hasVehicle", "div#vehicleBlock", "shown")]["level"] == pr.SEEN
    assert beh[("toggle", "input#hasVehicle", "input#plate", "required or not")]["level"] == pr.CODE
    assert beh[("mask", "input#refCode", None, "upper-cases what is typed")]["level"] == pr.CODE
    assert beh[("mask", "input#phone", None, "replaces `/[^0-9+ ]/g`")]["level"] == pr.CODE
    assert beh[("counter", "textarea#notes", "span#notesCount", "counts the characters typed")]
    submit = beh[("submit", None, "button#submitBtn", "disabled until the form is valid")]
    assert submit["level"] == pr.SEEN  # the recording, and the code
    assert any(e.startswith("script") for e in submit["evidence"])
    assert any(e.startswith("recording") for e in submit["evidence"])
    state = {(s["selector"], s["what"]) for s in permit.screen["on_load"]}
    assert {("input#applicationNo", "read-only on load"), ("select#region", "disabled on load"),
            ("button#submitBtn", "disabled on load"), ("input#plate", "hidden on load")} <= state  # fmt: skip
    timers = {t["delay_ms"]: t for t in permit.rules["session"]}
    assert timers[840000]["messages"] == ["Your session ends in one minute. Save your work."]
    assert timers[900000]["goes_to"] == "/signed-out"
    scripts = permit.rules["scripts"]
    assert [s["script"] for s in scripts["analysed"]] == ["/static/apply.js"]
    assert scripts["libraries"] == [{"name": "jQuery Validation", "version": None,
                                     "files": ["jquery.validate-shim.js"],
                                     "origin": "this application"}]  # fmt: skip
    assert permit.rules["not_tied"] == [] and scripts["carried"]["opt_in"] is False


def test_the_brief_section_and_the_task_say_how_to_use_it(permit):
    part = permit.brief.split("## Field rules and page behaviour", 1)[1].split("\n## ", 1)[0]
    for words in ("| Field | Rule | Message | Level |",
                  "| `input#fullName` (Full name) | required | Enter your full name. | seen |",
                  "| `input#phone` (Phone) | matches `/^\\+?[0-9 ]{7,15}$/` | Give a phone number of "
                  "7 to 15 digits. | in code, not triggered |",
                  "replaces the options of `select#region` from `GET /api/regions`",
                  "**On load:**", "**Session timer**", "Named, not analysed: jQuery Validation",
                  "Implement every rule at level `seen` or `declared` exactly"):  # fmt: skip
        assert words in part, words
    assert permit.brief.index("## Field rules and page behaviour") < permit.brief.index(
        "## Behaviour seen in the recordings")  # fmt: skip
    assert "**Same field rules and page behaviour.**" in permit.task
    assert "Rules `in code, not triggered` are implemented too" in permit.task
    reading = permit.task.split("## Read in this order", 1)[1].split("\n## ", 1)[0]
    assert "`apps/permit/rules.json`" in reading
    assert "| `apps/permit/rules.json` |" in permit.texts["INDEX.md"]
    assert not [n for n in permit.texts if "/scripts/" in n]


def test_no_typed_value_and_no_script_is_in_the_package(permit):
    whole = "\n".join(permit.texts.values())
    for value in form_flow.TYPED.values():
        assert value not in whole, value
    assert "addEventListener" not in whole and "__validateOptions" not in whole


def test_the_opt_in_carries_the_own_script_and_a_planted_secret_refuses_the_package(
        permit, tmp_path, monkeypatch):  # fmt: skip
    root = tmp_path / "repo"
    shutil.copytree(permit.root, root, ignore=shutil.ignore_patterns("packages"))
    monkeypatch.setenv(pr.SCRIPTS_ENV, "carry")
    doc = pr.write(root, "permit")
    assert [f["file"] for f in doc["scripts"]["carried"]["files"]] == ["scripts/apply.js"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pkg.PackageWarning)
        texts = _zip(pkg.build(root, "permit", root / "packages"))
    assert "apps/permit/scripts/apply.js" in texts and "apps/permit/scripts/manifest.json" in texts
    assert not [n for n in texts if "shim" in n]
    assert "owner's request" in texts["INDEX.md"]
    carried = root / "apps" / "permit" / "scripts" / "apply.js"
    carried.write_text(
        carried.read_text("utf-8") + '\nvar apiKey = "live-9f8e7d6c5b4a";\n', "utf-8"
    )
    with pytest.raises(pkg.PackageError, match="scripts"):
        pkg.build(root, "permit", root / "packages2")


def test_a_desktop_application_has_no_rules_file(tmp_path):
    app = tmp_path / "apps" / "d"
    app.mkdir(parents=True)
    (app / pr.FILE).write_text("{}", "utf-8")
    model = SimpleNamespace(stack=SimpleNamespace(desktop={"kind": "wpf"}))
    assert pr.extend(tmp_path, "d", model, "brief") == "brief"
    assert not (app / pr.FILE).exists()


# =================================================================================================
# The reference applications: expected (from their source) against found (from the recording)
# =================================================================================================


def _load(folder: str, name: str):
    app = REPO / "reference" / "web" / folder
    full = f"pagerules_{folder.replace('-', '_')}_{name}"
    if full not in sys.modules:
        spec = importlib.util.spec_from_file_location(full, app / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
    return sys.modules[full]


def _found(doc: dict) -> set[tuple]:
    out = set()
    for s in doc["screens"]:
        for f in s["fields"]:
            for r in f["rules"]:
                out.add((s["path"], f["selector"], r["rule"], r.get("value"), r.get("message"),
                         r["level"]))  # fmt: skip
        for m in s["messages"]:
            out.add((s["path"], "(form)", m["rule"], None, m["message"], m["level"]))
    return out


# Written by hand from reference/web/caseboard-angular/src/app (case-edit.ts, sign-in.ts):
# what the application checks, on which screen, at the level a recording of flow.py can give.
ANGULAR_EXPECTED = {
    ("/cases/{id}", "input#title", "required", None, "A title is required.", pr.SEEN),
    ("/cases/{id}", "input#title", "maxlength", 80, None, pr.DECLARED),
    # never triggered by flow.py: the owner is never emptied
    ("/cases/{id}", "input#owner", "required", None, "An owner is required.", pr.CODE),
    ("/cases/{id}", "input#owner", "maxlength", 40, None, pr.DECLARED),
    ("/cases/{id}", "input#dueDate", "type", "date", None, pr.DECLARED),
    ("/cases/{id}", "(form)", "check", None, "The case was not saved: see the messages above.",
     pr.SEEN),
    ("/sign-in", "input#password", "type", "password", None, pr.DECLARED),
    ("/sign-in", "(form)", "check", None, "The user name or password is wrong.", pr.SEEN),
    ("/cases", "input#search", "type", "search", None, pr.DECLARED),
}  # fmt: skip


@pytest.fixture(scope="module")
def angular(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")
    root = make_root(tmp_path_factory.mktemp("ng") / "repo")
    rec = root / "evidence" / "caseboard" / "run"
    srv = _load("caseboard-angular", "serve").serve()
    try:
        _load("caseboard-angular", "certify_local").record(srv.base, rec, True)
    finally:
        srv.stop()
    return pr.build(root, "caseboard")


def test_angular_reference_rules_expected_against_found(angular):
    found = _found(angular)
    assert ANGULAR_EXPECTED - found == set(), "missing"
    assert found - ANGULAR_EXPECTED == set(), "found but not in the source"
    assert [s["script"] for s in angular["scripts"]["analysed"]] == [
        "/chunk-JWL3ULEW.js", "/main-JIGO3NH3.js", "/chunk-CSK2V5PL.js"]  # fmt: skip
    assert angular["not_tied"] == [] and angular["session"] == []


JSF = REPO / "reference" / "web" / "caseboard-jsf"


def _jsf_reason() -> str | None:
    try:
        return _load("caseboard-jsf", "build").unavailable()
    except Exception as exc:  # noqa: BLE001 - the reason is the skip's text
        return f"{type(exc).__name__}: {exc}"


# Written by hand from reference/web/caseboard-jsf/webapp (login.xhtml, edit.xhtml): JSF checks
# on the server, so only what flow.py triggered (its messages) and the partial update show. The
# label words in JSF's messages are masked by the sanitiser on these pages ("Xxxxx is required").
JSF_EXPECTED = {
    ("/login.xhtml", "input#loginForm:password", "type", "password", None, pr.DECLARED),
    ("/login.xhtml", "(form)", "check", None, "Wrong user name or password", pr.SEEN),
    ("/edit.xhtml", "(form)", "required", None, "Xxxxx is required", pr.SEEN),
    ("/edit.xhtml", "(form)", "number", None, "Xxxxx must be a whole number", pr.SEEN),
}
JSF_NOT_RECOVERABLE = [  # in the source, invisible to a recording that never triggered them
    "loginForm:username required (requiredMessage 'User name is required')",
    "edit:hours required (requiredMessage 'Hours is required')",
    "edit:hours f:validateLongRange 0..500 ('Hours must be between 0 and 500')",
]


@pytest.mark.skipif(_jsf_reason() is not None, reason=f"JSF reference cannot run: {_jsf_reason()}")
def test_jsf_reference_rules_expected_against_found(tmp_path):
    pytest.importorskip("playwright.sync_api")
    root = make_root(tmp_path / "repo")
    rec = root / "evidence" / "caseboard" / "run"
    srv = _load("caseboard-jsf", "run").serve()
    try:
        _load("caseboard-jsf", "certify_local").record(srv.base, rec)
    finally:
        srv.stop()
    doc = pr.build(root, "caseboard")
    found = _found(doc)
    assert JSF_EXPECTED - found == set(), "missing"
    assert found - JSF_EXPECTED == set(), "found but not in the source"
    edit = next(s for s in doc["screens"] if s["path"] == "/edit.xhtml")
    beh = {(b["kind"], b.get("source"), b.get("target")): b for b in edit["behaviour"]}
    assert beh[("partial update", "select#edit:status", "edit:statusNote")]["level"] == pr.DECLARED
    assert beh[("toggle", "select#edit:status", "input#edit:resolution")]["level"] == pr.SEEN
    assert {x["name"] for x in doc["scripts"]["libraries"]} == {"javax.faces"}
    assert len(JSF_NOT_RECOVERABLE) == 3
