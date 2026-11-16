"""Keyed deterministic masking, sanitize, the draft manifest and the CLI, on synthetic data."""

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl.realdata import closure, demo, inventory
from labctl.realdata import mask as m
from labctl.realdata.cli import app

KEY = b"synthetic-test-key-not-a-secret!"  # 32 bytes, tests only
KEY_ENV = "TWINLAB_TEST_MASK_KEY"
EXAMPLES = Path(m.__file__).parent / "specs"
runner = CliRunner()

PEOPLE = [
    ("Avery", "Quill", "201-11-1001", "1971-01-11", "avery.quill@example.test", "(555) 011-2001"),
    ("Blake", "Rowan", "202-12-1002", "1972-02-12", "blake.rowan@example.test", "555-012-2002"),
    ("Casey", "Sable", "203-13-1003", "1973-03-13", "casey@example.test", "5550132003"),
    ("Devon", "Thorn", "204-14-1004", "1974-04-14", "devon.t@example.test", "+1 555 014 2004"),
    ("Emery", "Umber", "205-15-1005", "1975-05-15", "emery@example.test", "555.015.2005"),
]


def rows():
    customers = [
        {"id": i, "first_name": f, "last_name": last, "ssn": s, "birth_date": b, "email": e,
         "phone": p, "status": "active"}
        for i, (f, last, s, b, e, p) in enumerate(PEOPLE, start=1)
    ]  # fmt: skip
    accounts = []
    for c in customers:
        for k in range(2):
            body = f"4000{c['id']:04d}{k:04d}000"
            accounts.append(
                {
                    "id": c["id"] * 10 + k,
                    "customer_id": c["id"],
                    "account_no": f"7{c['id']:05d}{k:06d}",
                    "card_no": body + m.luhn_check_digit(body),
                    "opened_on": f"20{10 + k}-0{c['id']}-0{k + 1}",
                    "updated": f"2026-10-0{k + 1}T09:30:00",
                }
            )
    crm = [
        {"id": 900 + c["id"], "given_name": c["first_name"], "surname": c["last_name"],
         "tax_ssn": c["ssn"].replace("-", "")}
        for c in customers
    ]  # fmt: skip
    return {"customers": customers, "accounts": accounts, "crm_contacts": crm}


def manifest(**review):
    cols = {
        "customers.id": {"class": "none", "technique": "keep"},
        "customers.first_name": {"class": "first_name", "technique": "dict_hmac",
                                 "dict": "first_names_v1"},
        "customers.last_name": {"class": "last_name", "technique": "dict_hmac",
                                "dict": "last_names_v1"},
        "customers.ssn": {"class": "ssn", "technique": "ff1_ssn_valid"},
        "customers.birth_date": {"class": "date", "technique": "entity_shift",
                                 "entity": "customer", "entity_column": "id"},
        "customers.email": {"class": "email", "technique": "hmac_local_part"},
        "customers.phone": {"class": "phone", "technique": "ff1_keep_area"},
        "customers.status": {"class": "none", "technique": "keep"},
        "accounts.id": {"class": "none", "technique": "keep"},
        "accounts.customer_id": {"class": "none", "technique": "keep"},
        "accounts.account_no": {"class": "account", "technique": "ff1_digits"},
        "accounts.card_no": {"class": "card", "technique": "luhn"},
        "accounts.opened_on": {"class": "date", "technique": "entity_shift",
                               "entity": "customer", "entity_column": "customer_id"},
        "accounts.updated": {"class": "date", "technique": "entity_shift",
                             "entity": "customer", "entity_column": "customer_id"},
        "crm_contacts.id": {"class": "none", "technique": "keep"},
        "crm_contacts.given_name": {"class": "first_name", "technique": "dict_hmac",
                                    "dict": "first_names_v1"},
        "crm_contacts.surname": {"class": "last_name", "technique": "dict_hmac",
                                 "dict": "last_names_v1"},
        "crm_contacts.tax_ssn": {"class": "ssn", "technique": "ff1_ssn_valid"},
    }  # fmt: skip
    rv = {"approved_by": "Data Owner (synthetic)", "date": "2026-11-15", **review}
    return m.MaskManifest.model_validate({"key_env": KEY_ENV, "columns": cols, "review": rv})


MASKED_COLUMNS = [
    (t, c.split(".", 1)[1])
    for t in ("customers", "accounts", "crm_contacts")
    for c, r in manifest().columns.items()
    if c.startswith(t + ".") and r.technique != "keep"
]


def test_deterministic_across_runs_and_keeps_formats():
    a, b = m.mask(rows(), manifest(), KEY), m.mask(rows(), manifest(), KEY)
    assert a == b
    c = a["customers"][0]
    assert c["ssn"][3] == "-" and c["ssn"][6] == "-" and m.ssn_valid(c["ssn"].replace("-", ""))
    assert c["phone"].startswith("(555) ") and len(c["phone"]) == len("(555) 011-2001")
    assert c["email"].endswith("@test.invalid") and len(c["email"].split("@")[0]) == 12
    assert c["first_name"] != "Avery" and c["status"] == "active" and c["id"] == 1
    for acc in a["accounts"]:
        assert m.luhn_valid(acc["card_no"]) and len(acc["card_no"]) == 16
        assert len(acc["account_no"]) == 12 and acc["account_no"].isdigit()


def test_flipping_one_key_bit_changes_every_masked_value():
    base = m.mask(rows(), manifest(), KEY)
    for byte, bit in ((0, 0), (17, 3), (31, 7)):
        flipped = bytearray(KEY)
        flipped[byte] ^= 1 << bit
        other = m.mask(rows(), manifest(), bytes(flipped))
        for table, col in MASKED_COLUMNS:
            for x, y in zip(base[table], other[table], strict=True):
                assert x[col] != y[col], (table, col, byte, bit)


def test_same_person_masks_the_same_in_two_tables():
    out = m.mask(rows(), manifest(), KEY)
    for cust, crm in zip(out["customers"], out["crm_contacts"], strict=True):
        assert cust["first_name"] == crm["given_name"]
        assert cust["last_name"] == crm["surname"]
        assert cust["ssn"].replace("-", "") == crm["tax_ssn"]


def test_dates_on_one_entity_shift_together():
    src, out = rows(), m.mask(rows(), manifest(), KEY)

    def delta(a, b):
        return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days

    shifts = {}
    for s, d in zip(src["customers"], out["customers"], strict=True):
        shifts[s["id"]] = delta(s["birth_date"], d["birth_date"])
    for s, d in zip(src["accounts"], out["accounts"], strict=True):
        assert delta(s["opened_on"], d["opened_on"]) == shifts[s["customer_id"]]
        assert delta(s["updated"], d["updated"]) == shifts[s["customer_id"]]
        assert d["updated"][10:] == s["updated"][10:]  # the time of day is kept
    assert all(-365 <= v <= 365 and v != 0 for v in shifts.values())
    assert len(set(shifts.values())) > 1  # different people, different shifts


def test_sanitize_passes_on_masked_rows_and_catches_an_unmasked_column():
    src = rows()
    out = m.mask(src, manifest(), KEY)
    assert m.sanitize(src, out, manifest(), [], key=KEY) == []
    leaked = json.loads(json.dumps(out))
    leaked["customers"][2]["email"] = src["customers"][2]["email"]
    problems = m.sanitize(src, leaked, manifest(), [], key=KEY)
    assert any("customers.email: 1 of 5 values left unmasked" in p for p in problems)
    assert any("customers row 0: re-masking" in p for p in problems) is False


def test_sanitize_catches_invalid_ssn_luhn_broken_join_and_unclassified(tmp_path):
    path = demo.build(tmp_path / "replica.db")
    tables = inventory.pull("sqlite:///" + path.as_posix())
    src = {
        "customers": [{"id": 1, "ssn": "201-11-1001"}],
        "accounts": [{"id": 5, "customer_id": 1, "card_no": "4000000000000002", "extra": "x"}],
    }
    out = json.loads(json.dumps(src))
    out["customers"][0].update(id=2, ssn="666-11-1001")  # invalid area; join to accounts lost
    out["accounts"][0]["card_no"] = "4000000000000003"
    man = m.MaskManifest.model_validate(
        {"columns": {
            "customers.id": {"class": "id", "technique": "ff1_digits"},
            "customers.ssn": {"class": "ssn", "technique": "ff1_ssn_valid"},
            "accounts.id": {"class": "none", "technique": "keep"},
            "accounts.customer_id": {"class": "none", "technique": "keep"},
            "accounts.card_no": {"class": "card", "technique": "luhn"},
        }, "review": {"approved_by": "synthetic owner"}}
    )  # fmt: skip
    problems = "\n".join(m.sanitize(src, out, man, tables, key=KEY))
    assert "customers.ssn: 1 masked values are not valid SSNs" in problems
    assert "accounts.card_no: 1 masked values fail the Luhn check" in problems
    assert "accounts.customer_id -> customers: 1 rows joined in the source, 0" in problems
    assert "accounts.extra: not in the manifest" in problems
    assert "re-masking gives a different result" in problems


def test_unapproved_manifest_is_refused():
    with pytest.raises(m.MaskError, match="not approved"):
        m.mask(rows(), manifest(approved_by=None), KEY)
    with pytest.raises(m.MaskError, match="not approved"):
        m.mask(rows(), manifest(approved_by="<data owner>"), KEY)
    assert m.mask(rows(), manifest(approved_by=None), KEY, allow_unreviewed=True)


def test_unclassified_column_blocks_and_short_key_is_refused():
    data = rows()
    data["customers"][0]["nickname"] = "Ave"
    with pytest.raises(m.MaskError, match="customers.nickname"):
        m.mask(data, manifest(), KEY)
    assert m.mask(data, manifest(unclassified_policy="keep"), KEY)["customers"][0]["nickname"]
    with pytest.raises(m.MaskError):
        m.mask(rows(), manifest(), b"short")


def test_key_comes_from_the_environment(monkeypatch):
    man = manifest()
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(m.MaskError, match=KEY_ENV):
        m.key_from_env(man)
    monkeypatch.setenv(KEY_ENV, "hex:" + KEY.hex())
    assert m.key_from_env(man) == KEY
    with pytest.raises(ValueError):
        m.MaskManifest.model_validate({"key_env": "not a name!", "columns": {}})


def test_draft_manifest_guesses_classes_and_is_unapproved(tmp_path):
    path = demo.build(tmp_path / "replica.db")
    draft = m.draft_manifest(inventory.pull("sqlite:///" + path.as_posix()), app="showcase")
    tech = {k: r.technique for k, r in draft.columns.items()}
    assert tech["customers.ssn"] == "ff1_ssn_valid"
    assert tech["crm_contacts.tax_ssn"] == "ff1_ssn_valid"
    assert tech["customers.birth_date"] == "entity_shift"
    assert draft.columns["customers.birth_date"].entity_column == "id"
    assert tech["crm_contacts.dob"] == "entity_shift"
    assert tech["customers.email"] == "hmac_local_part"
    assert tech["customers.phone"] == "ff1_keep_area"
    assert draft.columns["customers.first_name"].dictionary == "first_names_v1"
    assert draft.columns["crm_contacts.given_name"].dictionary == "first_names_v1"
    assert draft.columns["crm_contacts.surname"].dictionary == "last_names_v1"
    assert draft.columns["addresses.line1"].dictionary == "streets_v1"
    assert tech["accounts.card_no"] == "luhn"
    assert tech["accounts.account_no"] == "ff1_digits"
    # surrogate keys and foreign keys to them stay as they are
    assert tech["accounts.id"] == tech["accounts.customer_id"] == "keep"
    assert tech["customers.primary_account_id"] == tech["transactions.account_id"] == "keep"
    assert draft.review.approved_by is None
    assert m.load_manifest(m.manifest_yaml(draft)) == draft
    with pytest.raises(m.MaskError, match="not approved"):
        m.mask({}, draft, KEY)


def test_draft_passes_a_masked_key_to_the_columns_that_reference_it():
    from labctl.source.sqlalchemy_models import Column, Table

    parent = Table("cards", "", None, 0, [Column("card_no", "VARCHAR(19)", False, True)])
    child = Table(
        "payments", "", None, 0,
        [Column("id", "INTEGER", False, True, autoincrement=True),
         Column("paid_with", "VARCHAR(19)", False, references=("cards", "card_no"))],
    )  # fmt: skip
    draft = m.draft_manifest([parent, child])
    assert draft.columns["payments.paid_with"].technique == "luhn"


def test_example_manifest_validates():
    man = m.load_manifest((EXAMPLES / "mask.example.yaml").read_text(encoding="utf-8"))
    assert man.review.approved_by is None and man.key_env == "TWINLAB_MASK_KEY"


# -- end to end on the synthetic replica, through the CLI ---------------------------------------


def test_cli_extract_mask_sanitize(tmp_path, monkeypatch):
    path = demo.build(tmp_path / "replica.db")
    monkeypatch.setenv("TL_TEST_REPLICA_URL", "sqlite:///" + path.as_posix())
    monkeypatch.setenv("TWINLAB_MASK_KEY", KEY.decode())
    spec = EXAMPLES / "subset.example.yaml"
    approved = tmp_path / "mask.yaml"
    data = yaml.safe_load((EXAMPLES / "mask.example.yaml").read_text(encoding="utf-8"))
    data["review"]["approved_by"] = "synthetic data owner"
    approved.write_text(yaml.safe_dump(data), encoding="utf-8")

    res = runner.invoke(app, ["inventory", "TL_TEST_REPLICA_URL", "--counts"])
    assert res.exit_code == 0, res.output
    assert {t["name"] for t in json.loads(res.output)} >= {"customers", "addresses"}
    assert runner.invoke(app, ["inventory", "sqlite:///x.db"]).exit_code == 2

    rows_json, masked_json = tmp_path / "rows.json", tmp_path / "masked.json"
    res = runner.invoke(
        app,
        ["extract", "--url-env", "TL_TEST_REPLICA_URL", "--spec", str(spec), "--out",
         str(rows_json)],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "truncated: accounts" in res.output

    res = runner.invoke(
        app, ["mask", "--manifest", str(EXAMPLES / "mask.example.yaml"), "--in", str(rows_json),
              "--out", str(masked_json)],
    )  # fmt: skip
    assert res.exit_code == 1 and "not approved" in res.output

    res = runner.invoke(
        app, ["mask", "--manifest", str(approved), "--in", str(rows_json), "--out",
              str(masked_json)],
    )  # fmt: skip
    assert res.exit_code == 0, res.output

    sanitize = ["sanitize", "--manifest", str(approved), "--source", str(rows_json), "--masked",
                str(masked_json), "--url-env", "TL_TEST_REPLICA_URL"]  # fmt: skip
    res = runner.invoke(app, sanitize)
    assert res.exit_code == 0, res.output

    masked = json.loads(masked_json.read_text(encoding="utf-8"))
    masked["customers"][0]["ssn"] = json.loads(rows_json.read_text())["customers"][0]["ssn"]
    masked_json.write_text(json.dumps(masked), encoding="utf-8")
    res = runner.invoke(app, sanitize)
    assert res.exit_code == 1 and "customers.ssn" in res.output

    one_step = tmp_path / "one-step.json"
    res = runner.invoke(
        app, ["extract", "--url-env", "TL_TEST_REPLICA_URL", "--spec", str(spec), "--out",
              str(one_step), "--manifest", str(approved)],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    two_step = m.mask(json.loads(rows_json.read_text()), m.load_manifest(approved.read_text()), KEY)
    assert json.loads(one_step.read_text()) == two_step

    res = runner.invoke(app, ["draft-manifest", "--url-env", "TL_TEST_REPLICA_URL"])
    assert res.exit_code == 0 and "approved_by: null" in res.output
    res = runner.invoke(
        app, ["trace-diff", "--url-env", "TL_TEST_REPLICA_URL", "--spec", str(spec),
              "--observed", "customers", "--observed", "addresses"],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "replica.db" not in res.output  # the URL is never printed


def test_extracted_rows_mask_and_sanitize_with_a_draft(tmp_path):
    path = demo.build(tmp_path / "replica.db")
    url = "sqlite:///" + path.as_posix()
    tables = inventory.pull(url)
    spec = closure.SubsetSpec(
        seeds=[{"table": "customers", "values": [1, 2, 3]}],
        closure={"associations": [{"child": "addresses", "column": "customer_ref",
                                   "parent": "customers", "column_parent": "id"}]},
    )  # fmt: skip
    src = closure.extract(url, spec)
    draft = m.draft_manifest(tables)
    out = m.mask(src, draft, KEY, allow_unreviewed=True)
    assert m.sanitize(src, out, draft, tables, key=KEY) == []


# -- one-to-one masking ---------------------------------------------------------------------------


def synthetic_emails(count: int = 10_000) -> list[str]:
    """Realistic synthetic addresses; many share a local part across domains."""
    from labctl.realdata.dictionaries import DICTIONARIES

    firsts, lasts = DICTIONARIES["first_names_v1"], DICTIONARIES["last_names_v1"]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "example.org", "mail.example.net"]
    out: list[str] = []
    for i in range(count):
        f, last = firsts[i % len(firsts)].lower(), lasts[(i // len(firsts)) % len(lasts)].lower()
        local = [f"{f}.{last}", f"{f}{last}", f"{f[0]}{last}{i % 7}"][i % 3]
        out.append(f"{local}@{domains[(i // 3) % len(domains)]}")
    return out


def test_email_masking_keeps_the_domain_apart():
    masker = m.Masker(KEY)
    gmail = masker.hmac_local_part("john.smith@gmail.com")
    yahoo = masker.hmac_local_part("john.smith@yahoo.com")
    assert gmail != yahoo
    assert gmail.endswith("@test.invalid") and yahoo.endswith("@test.invalid")
    assert masker.hmac_local_part("john.smith@gmail.com") == gmail  # deterministic
    assert masker.hmac_local_part(" John.Smith@GMAIL.com ") == gmail  # same address
    # Length-prefixed: moving the "@" never gives the same input to the hash.
    assert masker.hmac_local_part("a@b.c") != masker.hmac_local_part("a@bc")


def test_ten_thousand_synthetic_emails_mask_without_a_collision():
    emails = synthetic_emails()
    assert len({e.casefold() for e in emails}) == len(emails) == 10_000
    masker = m.Masker(KEY)
    masked = [masker.hmac_local_part(e) for e in emails]
    assert len(set(masked)) == len(emails)
    assert all(x.endswith("@test.invalid") and len(x.split("@")[0]) == 12 for x in masked)


@pytest.mark.parametrize("length", [1, 2, 3, 4, 5])
def test_every_short_digit_domain_maps_one_to_one(length):
    masker = m.Masker(KEY)
    values = [str(v).zfill(length) for v in range(10**length)]
    out = [masker.ff1_digits(v) for v in values]
    assert len(set(out)) == 10**length  # a bijection of the whole domain
    assert all(len(o) == length and o.isdigit() for o in out)
    assert all(o != v for o, v in zip(out, values, strict=True))  # nothing left as it was
    assert [m.Masker(KEY).ff1_digits(v) for v in values[:50]] == out[:50]  # deterministic


def test_six_digits_and_up_use_ff1():
    masker = m.Masker(KEY)
    assert m.FF1_MIN_DIGITS == 6
    assert masker.ff1_digits("123456") == masker._fpe("ff1_digits").encrypt("123456")
    sample = [str(v).zfill(6) for v in range(0, 10**6, 97)]
    assert len({masker.ff1_digits(v) for v in sample}) == len(sample)


def test_valid_ssns_stay_one_to_one():
    masker = m.Masker(KEY)
    ssns = [
        f"{a:03d}{g:02d}{s:04d}" for a in (1, 123, 665, 899) for g in (1, 50) for s in range(1, 400)
    ]
    out = [masker.ff1_ssn_valid(s) for s in ssns]
    assert all(m.ssn_valid(o) for o in out)
    assert len(set(out)) == len(ssns)


def test_sanitize_reports_distinct_values_that_became_equal():
    src = rows()
    out = m.mask(src, manifest(), KEY)
    assert m.sanitize(src, out, manifest(), [], key=KEY) == []
    merged = json.loads(json.dumps(out))
    merged["customers"][1]["email"] = merged["customers"][0]["email"]
    merged["accounts"][3]["account_no"] = merged["accounts"][2]["account_no"]
    problems = "\n".join(m.sanitize(src, merged, manifest(), [], key=KEY))
    assert "customers.email: 1 masked values each stand for more than one" in problems
    assert "accounts.account_no: 1 masked values each stand for more than one" in problems
    # The same source value masking to the same value twice is fine, and so are dictionaries.
    same = {"customers": src["customers"] + [dict(src["customers"][0], id=99)]}
    man = manifest()
    out2 = m.mask(same, man, KEY)
    assert not [p for p in m.sanitize(same, out2, man, [], key=KEY) if "more than one" in p]
