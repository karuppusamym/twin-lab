"""Manual OpenShift deployment: plain files, committed, reviewed, applied by hand (labctl.ocp_manual).

No cluster here: the tests render, check the files structurally (`labctl ocp check`) and compare
the committed folders under deploy/ocp with what a fresh render produces. Set TWINLAB_REFRESH_OCP=1
to rewrite those folders instead of failing (then commit them).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import dbservers, ocp, ocp_manual
from labctl.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "ocp"
URL, REF, NS = "https://git.example.test/team/twin-lab.git", "wp/demo", "twinlab-test"
FAKE = {"owner": "FAKE-owner-pw-71c2d9", "bot": "FAKE-bot-pw-3a90e4"}
SERVER = "qtt-sqlserver"


def _copy_app(tmp_path: Path, source: str, new_id: str, edit) -> Path:
    """A scratch repository holding one app, copied from the real one and edited."""
    root = tmp_path / "repo"
    dest = root / "apps" / new_id
    for part in ("db",):
        shutil.copytree(ROOT / "apps" / source / part, dest / part)
    (root / "lab").mkdir(parents=True, exist_ok=True)
    (root / "automations").mkdir(exist_ok=True)
    (dest / "Dockerfile").write_text(
        (ROOT / "apps" / source / "Dockerfile").read_text(encoding="utf-8"), encoding="utf-8"
    )
    spec = yaml.safe_load((ROOT / "apps" / source / "app.yaml").read_text(encoding="utf-8"))
    spec["id"] = new_id
    edit(spec)
    (dest / "app.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return root


def _render(app_id="showcase", root=ROOT, **kw) -> ocp_manual.Bundle:
    kw = {"namespace": NS, "git_url": URL, "git_ref": REF, **kw}
    return ocp_manual.render_manual(root, app_id, **kw)


@pytest.fixture
def variants(tmp_path, monkeypatch):
    """One bundle per database choice: postgres, sqlite, browser-only and a DBA's server."""
    monkeypatch.setenv(
        "TWINLAB_DATABASES",
        yaml.safe_dump({"databases": {SERVER: {
            "engine": "mssql", "host": "sqldemo01.example.test", "database": "TWINLAB_QTT",
            "owner_user": "tl_owner", "owner_password": FAKE["owner"],
            "bot_user": "tl_bot", "bot_password": FAKE["bot"],
        }}}),
    )  # fmt: skip

    def browser(spec):
        spec["stack"]["data"] = {"kind": "browser"}
        spec["lab"].pop("database", None)

    def external(spec):
        spec["lab"]["database"] = SERVER

    browser_root = _copy_app(tmp_path / "b", "loandesk", "todo", browser)
    external_root = _copy_app(tmp_path / "e", "qtt", "qttdba", external)
    return {
        "postgres": _render("showcase"),
        "sqlite": _render("loandesk"),
        "browser": _render("todo", browser_root),
        "external": _render("qttdba", external_root),
    }


def _by(docs, kind, obj=None):
    found = [d for d in docs if d["kind"] == kind and (obj is None or d["metadata"]["name"] == obj)]
    assert len(found) == 1, f"{kind}/{obj}: {len(found)}"
    return found[0]


def _write(bundle, tmp_path, sub="out") -> Path:
    out = tmp_path / sub
    ocp_manual.write_bundle(bundle, out)
    return out


# --- every database choice ---------------------------------------------------------------------


@pytest.mark.parametrize("choice", ["postgres", "sqlite", "browser", "external"])
def test_each_choice_renders_and_validates(variants, tmp_path, choice):
    bundle = variants[choice]
    assert bundle.plan.kind == choice
    out = _write(bundle, tmp_path)
    assert ocp_manual.check_dir(out) == []
    names = sorted(p.name for p in out.iterdir())
    assert "README.md" in names and "kustomization.yaml" in names
    numbered = [n for n in names if re.match(r"^\d\d-", n)]
    assert numbered == [f"{i:02d}" + n[2:] for i, n in enumerate(numbered, 1)]
    kinds = [d["kind"] for d in bundle.docs]
    assert kinds[:4] == ["ImageStream", "ImageStream", "BuildConfig", "BuildConfig"]
    assert kinds[-1] == "Route"
    assert (out / "kustomization.yaml").read_text(encoding="utf-8").count("  - ") == len(numbered)


def test_what_each_choice_becomes(variants):
    n = lambda b, part: f"twinlab-{b.plan.app_id}-{part}"  # noqa: E731
    pg = variants["postgres"]
    for kind, part in [("Deployment", "db"), ("Job", "db-setup"), ("Deployment", "agent"),
                       ("Deployment", "app")]:  # fmt: skip
        _by(pg.docs, kind, n(pg, part))
    assert pg.plan.secret_keys == ocp.SECRET_KEYS

    for choice in ("sqlite", "browser"):
        b = variants[choice]
        assert not [d for d in b.docs if d["kind"] == "Job"]
        assert [d["metadata"]["name"] for d in b.docs if d["kind"] == "Deployment"] == [n(b, "lab")]
        assert b.plan.secret_keys == ("lab-token",)

    ext = variants["external"]
    assert not [
        d for d in ext.docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-db")
    ]
    job = _by(ext.docs, "Job")
    assert job["spec"]["template"]["spec"]["containers"][0]["image"].endswith("-agent:latest")
    assert "twinlab-qttdba-dbconn" in ext.files["README.md"]
    assert (
        "--from-literal=admin-url='mssql+pymssql://tl_owner:<owner-password>@"
        in ext.files["README.md"]
    )


def test_sql_server_container_is_refused(tmp_path):
    root = _copy_app(
        tmp_path, "qtt", "qttmssql", lambda spec: spec["lab"].update(database="sqlserver")
    )
    with pytest.raises(ocp.OcpError, match="SQL Server container"):
        _render("qttmssql", root)


def test_all_options_work_with_every_choice(variants, tmp_path):
    for choice in ("postgres", "sqlite", "browser", "external"):
        root = ROOT
        app_id = variants[choice].plan.app_id
        if choice in ("browser", "external"):
            continue  # their scratch repositories are covered by the other tests
        for opts in ({"ephemeral": True}, {"network_policy": True}, {"storage_class": "fast"},
                     {"source_secret": "git-creds"}, {"expose_db": True}):  # fmt: skip
            bundle = _render(app_id, root, **opts)
            assert (
                ocp_manual.check_dir(
                    _write(bundle, tmp_path, f"o{app_id}{len(opts)}{list(opts)[0]}")
                )
                == []
            )


# --- no secret values --------------------------------------------------------------------------


def test_no_secret_value_anywhere(variants, tmp_path):
    for choice, bundle in variants.items():
        text = "".join(bundle.files.values()) + json.dumps(bundle.docs)
        for value in FAKE.values():
            assert value not in text, choice
        assert not [d for d in bundle.docs if d["kind"] == "Secret"]
        assert "stringData" not in text and "\ndata:\n  password" not in text
        assert "openssl rand" in bundle.files["README.md"]  # the values are made up by hand
        for container in _containers(bundle.docs):
            for e in container.get("env", []):
                if "value" in e:
                    assert not re.search(r"(?i)(password|token)$", e["name"]), (choice, e["name"])
        refs = {e["valueFrom"]["secretKeyRef"]["name"]
                for c in _containers(bundle.docs) for e in c.get("env", []) if "valueFrom" in e}  # fmt: skip
        assert refs <= {
            ocp.secret_name(bundle.plan.app_id),
            ocp.conn_secret_name(bundle.plan.app_id),
        }


def _containers(docs):
    for d in docs:
        if d["kind"] in ("Deployment", "Job"):
            spec = d["spec"]["template"]["spec"]
            yield from spec.get("initContainers", []) + spec["containers"]


# --- the builds --------------------------------------------------------------------------------


@pytest.mark.parametrize("app_id", ["showcase", "qtt", "loandesk"])
def test_buildconfigs_point_at_the_branch_and_contexts(app_id):
    bundle = _render(app_id)
    app_bc = _by(bundle.docs, "BuildConfig", f"twinlab-{app_id}-app")
    agent_bc = _by(bundle.docs, "BuildConfig", f"twinlab-{app_id}-agent")
    for bc in (app_bc, agent_bc):
        assert bc["spec"]["source"]["git"] == {"uri": URL, "ref": REF}
        assert bc["spec"]["strategy"]["type"] == "Docker"
        assert bc["spec"]["triggers"] == []  # no webhooks: no webhook secrets
        assert "sourceSecret" not in bc["spec"]["source"]
        stream = bc["spec"]["output"]["to"]["name"].split(":")[0]
        _by(bundle.docs, "ImageStream", stream)
    assert app_bc["spec"]["source"]["contextDir"] == f"apps/{app_id}"
    assert app_bc["spec"]["strategy"]["dockerStrategy"]["dockerfilePath"] == "Dockerfile"
    # The agent's Dockerfile copies lab/labctl too, so its context is the repository root.
    assert "contextDir" not in agent_bc["spec"]["source"]
    assert (
        agent_bc["spec"]["strategy"]["dockerStrategy"]["dockerfilePath"]
        == "lab/lab-agent/Dockerfile"
    )
    assert (ROOT / "lab/lab-agent/Dockerfile").is_file() and (
        ROOT / "apps" / app_id / "Dockerfile"
    ).is_file()


def test_source_secret_is_a_name_only():
    bundle = _render("qtt", source_secret="git-creds")
    for bc in [d for d in bundle.docs if d["kind"] == "BuildConfig"]:
        assert bc["spec"]["source"]["sourceSecret"] == {"name": "git-creds"}
    assert "git-creds" in bundle.files["README.md"]


def test_deployments_follow_the_image_streams():
    bundle = _render("showcase")
    for part, which in (("agent", "agent"), ("app", "app")):
        dep = _by(bundle.docs, "Deployment", f"twinlab-showcase-{part}")
        triggers = json.loads(dep["metadata"]["annotations"][ocp.TRIGGER_ANNOTATION])
        assert [t["from"]["name"] for t in triggers] == [f"twinlab-showcase-{which}:latest"]
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert image == f"{ocp.INTERNAL_REGISTRY}/{NS}/twinlab-showcase-{which}:latest"
    lab = _by(_render("loandesk").docs, "Deployment")
    assert len(json.loads(lab["metadata"]["annotations"][ocp.TRIGGER_ANNOTATION])) == 3


def test_registry_alternative_has_no_builds():
    bundle = _render("qtt", registry="registry.example.test/team", tag="v3")
    assert not [d for d in bundle.docs if d["kind"] in ("BuildConfig", "ImageStream")]
    images = {c["image"] for c in _containers(bundle.docs) if "registry.example.test" in c["image"]}
    assert images == {
        "registry.example.test/team/lab-agent:v3",
        "registry.example.test/team/qtt:v3",
    }
    for d in bundle.docs:
        assert ocp.TRIGGER_ANNOTATION not in d["metadata"].get("annotations", {})
    assert _render("qtt", registry="r.example.test/t").plan.tag == "latest"


# --- the sqlite pod ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "choice,volume", [("sqlite", "persistentVolumeClaim"), ("browser", "emptyDir")]
)
def test_sqlite_pod_shares_the_volume(variants, choice, volume):
    bundle = variants[choice]
    app_id = bundle.plan.app_id
    dep = _by(bundle.docs, "Deployment")
    pod = dep["spec"]["template"]["spec"]
    init, (agent, app) = pod["initContainers"][0], pod["containers"]
    assert [c["name"] for c in pod["containers"]] == ["agent", "app"]
    assert init["command"] == ["python", "-m", "lab_agent.sqlite_init"]
    assert init["image"] == agent["image"]
    path = f"/var/lib/twinlab-db/{app_id}.db"
    for c in (init, agent):
        assert {e["name"]: e["value"] for e in c["env"] if "value" in e}[
            "ADMIN_DATABASE_URL"
        ] == f"sqlite:///{path}"
    mounts = [
        next(m for m in c["volumeMounts"] if m["mountPath"] == "/var/lib/twinlab-db")
        for c in (init, agent, app)
    ]
    assert {m["name"] for m in mounts} == {"sqlite"}
    assert next(v for v in pod["volumes"] if v["name"] == "sqlite").keys() == {"name", volume}
    app_env = {e["name"]: e["value"] for e in app["env"]}
    assert app_env["DB_PATH"] == path and app_env["DB_ENGINE"] == "sqlite"
    assert {e["name"]: e["value"] for e in agent["env"] if "value" in e}["APP_UPSTREAM"].startswith(
        "http://127.0.0.1:"
    )
    assert dep["spec"]["strategy"]["type"] == "Recreate"
    assert not [
        d for d in bundle.docs if d["kind"] == "Service" and d["metadata"]["name"].endswith("-db")
    ]
    svc = _by(bundle.docs, "Service")
    assert [p["port"] for p in svc["spec"]["ports"]] == [9000]  # no database link on SQLite
    assert _by(bundle.docs, "Route")["spec"]["to"]["name"] == svc["metadata"]["name"]
    for path_, claim in (("agent-state", "state"),):
        vol = next(v for v in pod["volumes"] if v["name"] == claim)
        assert "persistentVolumeClaim" in vol, path_  # baselines and sessions survive a restart


def test_ephemeral_uses_no_pvc(tmp_path):
    for app_id in ("showcase", "loandesk"):
        bundle = _render(app_id, ephemeral=True)
        assert not [d for d in bundle.docs if d["kind"] == "PersistentVolumeClaim"]
        assert ocp_manual.check_dir(_write(bundle, tmp_path, app_id)) == []
    pvc = [
        d
        for d in _render("loandesk", storage_class="fast").docs
        if d["kind"] == "PersistentVolumeClaim"
    ]
    assert len(pvc) == 2 and all(d["spec"]["storageClassName"] == "fast" for d in pvc)


# --- restricted SCC ----------------------------------------------------------------------------


@pytest.mark.parametrize("choice", ["postgres", "sqlite", "browser", "external"])
def test_arbitrary_uid_friendly(variants, choice):
    bundle = variants[choice]
    for d in bundle.docs:
        if d["kind"] not in ("Deployment", "Job"):
            continue
        pod = d["spec"]["template"]["spec"]
        assert pod["securityContext"] == {
            "runAsNonRoot": True,
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        assert not (set(pod) & {"hostNetwork", "hostPID", "hostIPC", "serviceAccountName"})
        assert not [v for v in pod.get("volumes", []) if "hostPath" in v]
    for c in _containers(bundle.docs):
        sc = c["securityContext"]
        assert "runAsUser" not in sc and "runAsGroup" not in sc and "privileged" not in sc
        assert sc["runAsNonRoot"] is True and sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"] == {"drop": ["ALL"]}
    text = "".join(bundle.files.values())
    assert "runAsUser" not in text and "fsGroup" not in text and "hostPort" not in text


def test_check_flags_a_fixed_uid(tmp_path):
    out = _write(_render("loandesk"), tmp_path)
    path = next(out.glob("*-deployment-*.yaml"))
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["spec"]["template"]["spec"]["containers"][0]["securityContext"]["runAsUser"] = 0
    doc["spec"]["template"]["spec"]["securityContext"]["fsGroup"] = 1000
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    problems = "\n".join(ocp_manual.check_dir(out))
    assert "runAsUser" in problems and "fsGroup" in problems


# --- network policy, names, labels -------------------------------------------------------------


@pytest.mark.parametrize("app_id", ["showcase", "loandesk"])
def test_network_policy_option(app_id, tmp_path):
    plain = _render(app_id)
    assert not [d for d in plain.docs if d["kind"] == "NetworkPolicy"]
    bundle = _render(app_id, network_policy=True)
    pols = [d for d in bundle.docs if d["kind"] == "NetworkPolicy"]
    assert {p["metadata"]["name"] for p in pols} == {
        f"twinlab-{app_id}-allow-router",
        f"twinlab-{app_id}-allow-same-lab",
    }
    router = next(p for p in pols if p["metadata"]["name"].endswith("allow-router"))
    assert router["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 9000}]
    assert ocp_manual.check_dir(_write(bundle, tmp_path)) == []


def test_every_object_is_tied_to_the_app_and_named_validly():
    for app_id in ("showcase", "qtt", "loandesk"):
        for d in _render(app_id, network_policy=True).docs:
            meta = d["metadata"]
            assert meta["namespace"] == NS
            assert meta["labels"]["app.kubernetes.io/instance"] == app_id
            assert meta["labels"]["app.kubernetes.io/part-of"] == "twinlab"
            assert ocp.DNS1123.match(meta["name"]) and len(meta["name"]) <= 63


def test_bad_names_and_missing_git_are_refused(tmp_path):
    with pytest.raises(ocp.OcpError):
        _render("showcase", namespace="Not_A_Name")
    with pytest.raises(ocp.OcpError):
        _render("x" * 45)
    root = _copy_app(tmp_path, "loandesk", "plain", lambda spec: None)  # not a Git checkout
    with pytest.raises(ocp.OcpError, match="Git URL"):
        ocp_manual.render_manual(root, "plain", git_ref=REF)


def test_git_url_from_ssh_remote():
    assert ocp_manual.https_url("git@github.com:o/r.git") == "https://github.com/o/r.git"
    assert (
        ocp_manual.https_url("ssh://git@git.example.test/o/r.git")
        == "https://git.example.test/o/r.git"
    )
    assert (
        ocp_manual.https_url("https://git.example.test/o/r.git")
        == "https://git.example.test/o/r.git"
    )
    url, ref = ocp_manual.git_defaults(ROOT)  # the checkout's origin and branch, when it has them
    assert url is None or url.startswith("https://") or "://" in url
    assert ref is None or ref.strip()


# --- the README --------------------------------------------------------------------------------


@pytest.mark.parametrize("choice", ["postgres", "sqlite", "browser", "external"])
def test_readme_gives_the_steps(variants, choice):
    bundle = variants[choice]
    text = bundle.files["README.md"]
    a, ns = bundle.plan.app_id, NS
    for needle in (
        f"oc create secret generic twinlab-{a}-secrets -n {ns}", "oc apply -f .", "oc apply -k .",
        f"oc start-build twinlab-{a}-agent", f"oc start-build twinlab-{a}-app", URL, REF,
        f"oc get route twinlab-{a}-web", "/_lab/health", "/_lab/data/load", "/_lab/data/baseline",
        "/_lab/reset", "oc tag ", "oc rollout undo", f"-l app.kubernetes.io/instance={a}",
        "What differs from the local lab", "restricted-v2",
    ):  # fmt: skip
        assert needle in text, needle
    for key in bundle.plan.secret_keys:
        assert f"--from-literal={key}=" in text
    for name in bundle.manifests:
        assert name in text
    if choice in ("sqlite", "browser"):
        assert "database slowdowns" in text and "sqlite-init" in text
    if choice == "postgres":
        assert "db-setup" in text and "once more" in text


# --- --all, the CLI, determinism ---------------------------------------------------------------


def test_render_all_and_the_cli(tmp_path):
    runner = CliRunner()
    env = {"LABCTL_ROOT": str(ROOT)}
    out = tmp_path / "all"
    res = runner.invoke(cli_app, ["ocp", "render", "--all", "--manual", "--out", str(out),
                                  "--git-url", URL, "--git-ref", REF], env=env)  # fmt: skip
    assert res.exit_code == 0, res.output
    apps = ocp_manual.lab_apps(ROOT)
    assert {"showcase", "qtt", "loandesk"} <= set(apps)
    assert sorted(p.name for p in out.iterdir() if p.is_dir()) == sorted(apps)
    index = (out / "README.md").read_text(encoding="utf-8")
    assert all(f"[{a}/]({a}/README.md)" in index for a in apps)
    assert runner.invoke(cli_app, ["ocp", "check", str(out)], env=env).exit_code == 0
    # One app, to a folder; the existing options still work without --manual.
    one = tmp_path / "one"
    res = runner.invoke(cli_app, ["ocp", "render", "loandesk", "--manual", "--out", str(one),
                                  "--git-url", URL, "--git-ref", REF, "--network-policy"], env=env)  # fmt: skip
    assert res.exit_code == 0, res.output
    assert ocp_manual.check_dir(one) == []
    old = runner.invoke(cli_app, ["ocp", "render", "loandesk", "--registry", "r.example.test/t",
                                  "--tag", "1", "--namespace", NS], env=env)  # fmt: skip
    assert old.exit_code == 0 and "kind: Deployment" in old.output and "sqlite-init" in old.output
    # Mistakes exit non-zero with a message, not a traceback.
    assert runner.invoke(cli_app, ["ocp", "render", "--all"], env=env).exit_code == 2
    assert runner.invoke(cli_app, ["ocp", "render", "qtt", "--manual"], env=env).exit_code == 2
    assert runner.invoke(cli_app, ["ocp", "render", "qtt"], env=env).exit_code == 2


def test_render_all_skips_what_cannot_run(tmp_path):
    root = _copy_app(
        tmp_path, "qtt", "qttmssql", lambda spec: spec["lab"].update(database="sqlserver")
    )
    bundles, skipped = ocp_manual.render_all(
        root, tmp_path / "x", namespace=NS, git_url=URL, git_ref=REF
    )
    assert not bundles and "SQL Server container" in skipped["qttmssql"]
    assert "Not rendered" in (tmp_path / "x" / "README.md").read_text(encoding="utf-8")


def test_rendering_is_deterministic(tmp_path):
    for app_id in ("showcase", "loandesk"):
        first = _render(app_id, network_policy=True).files
        second = _render(app_id, network_policy=True).files
        assert first == second
        out_a, out_b = (
            _write(_render(app_id), tmp_path, "a"),
            _write(_render(app_id), tmp_path, "b"),
        )
        assert {p.name: p.read_bytes() for p in out_a.iterdir()} == {
            p.name: p.read_bytes() for p in out_b.iterdir()
        }
        assert b"\r" not in b"".join(p.read_bytes() for p in out_a.iterdir())


def test_rewriting_a_folder_removes_stale_files(tmp_path):
    out = tmp_path / "f"
    ocp_manual.write_bundle(_render("showcase"), out)
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    ocp_manual.write_bundle(_render("loandesk"), out)
    names = {p.name for p in out.iterdir()}
    assert "notes.txt" in names and not [n for n in names if "showcase" in n]


# --- ocp check ---------------------------------------------------------------------------------


def _mutate(out: Path, pattern: str, change) -> None:
    path = next(out.glob(pattern))
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize(
    "pattern,change,expect",
    [
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "API_TOKEN", "value": "abc"}),
         "holds a value"),
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["spec"]["template"]["spec"]["containers"][0]["env"].append(
             {"name": "X", "value": "postgresql://u:hunter2@h/db"}),
         "password in a URL"),
        ("*-deployment-twinlab-showcase-agent.yaml",
         lambda d: d["spec"]["template"]["spec"]["volumes"][0]["configMap"].update(name="nope"),
         "ConfigMap 'nope' is not in the folder"),
        ("*-deployment-twinlab-showcase-agent.yaml",
         lambda d: d["spec"]["template"]["spec"]["volumes"][1]["persistentVolumeClaim"].update(claimName="nope"),
         "PVC 'nope'"),
        ("*-service-twinlab-showcase-agent.yaml",
         lambda d: d["spec"]["selector"].update({"app.kubernetes.io/component": "ghost"}),
         "matches no pod"),
        ("*-route-twinlab-showcase-web.yaml",
         lambda d: d["spec"]["to"].update(name="ghost"), "Service 'ghost'"),
        ("*-route-twinlab-showcase-web.yaml",
         lambda d: d["spec"]["tls"].update(termination="passthrough"), "edge TLS"),
        ("*-buildconfig-twinlab-showcase-app.yaml",
         lambda d: d["spec"]["output"]["to"].update(name="ghost:latest"), "ImageStreamTag"),
        ("*-buildconfig-twinlab-showcase-app.yaml",
         lambda d: d["spec"]["triggers"].append({"type": "GitHub", "github": {"secret": "x"}}), "webhook"),
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["metadata"]["annotations"].update({ocp.TRIGGER_ANNOTATION: json.dumps([
             {"from": {"kind": "ImageStreamTag", "name": "ghost:latest"},
              "fieldPath": 'spec.template.spec.containers[?(@.name=="app")].image'}])}),
         "ImageStream 'ghost'"),
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["metadata"]["labels"].pop("app.kubernetes.io/instance"), "labels"),
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["spec"]["template"]["spec"]["containers"][0].pop("readinessProbe"), "readinessProbe"),
        ("*-deployment-twinlab-showcase-app.yaml",
         lambda d: d["metadata"].update(namespace="elsewhere"), "different namespaces"),
        ("*-pvc-*.yaml" if False else "*-persistentvolumeclaim-twinlab-showcase-db.yaml",
         lambda d: d["metadata"].update(name="x" * 64), "DNS-1123"),
    ],
)  # fmt: skip
def test_check_finds_problems(tmp_path, pattern, change, expect):
    out = _write(_render("showcase"), tmp_path)
    assert ocp_manual.check_dir(out) == []
    _mutate(out, pattern, change)
    problems = "\n".join(ocp_manual.check_dir(out))
    assert expect in problems, problems


def test_check_finds_structural_problems(tmp_path):
    out = _write(_render("showcase"), tmp_path)
    (out / "17-secret-twinlab-showcase-secrets.yaml").write_text(
        yaml.safe_dump({"apiVersion": "v1", "kind": "Secret", "metadata": {
            "name": "twinlab-showcase-secrets", "namespace": NS, "labels": {
                "app.kubernetes.io/part-of": "twinlab", "app.kubernetes.io/instance": "showcase"}},
            "stringData": {"lab-token": "FAKE"}}), encoding="utf-8")  # fmt: skip
    problems = "\n".join(ocp_manual.check_dir(out))
    assert "Secret does not belong" in problems and "kustomization.yaml" in problems
    (out / "17-secret-twinlab-showcase-secrets.yaml").unlink()
    (out / "README.md").unlink()
    assert "README.md is missing" in "\n".join(ocp_manual.check_dir(out))
    (out / "README.md").write_text("empty", encoding="utf-8")
    assert "does not explain Secret" in "\n".join(ocp_manual.check_dir(out))
    (out / "02-imagestream-twinlab-showcase-app.yaml").write_text(
        ": : not yaml [", encoding="utf-8"
    )
    assert "cannot be read as YAML" in "\n".join(ocp_manual.check_dir(out))
    assert ocp_manual.check_dir(tmp_path / "missing") and ocp_manual.check_dir(
        tmp_path / "empty-nope"
    )


def test_check_cli_exit_codes(tmp_path):
    runner, env = CliRunner(), {"LABCTL_ROOT": str(ROOT)}
    out = _write(_render("loandesk"), tmp_path)
    ok = runner.invoke(cli_app, ["ocp", "check", str(out)], env=env)
    assert ok.exit_code == 0 and "ok" in ok.output
    next(out.glob("*-route-*.yaml")).unlink()
    bad = runner.invoke(cli_app, ["ocp", "check", str(out)], env=env)
    assert bad.exit_code == 1
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert runner.invoke(cli_app, ["ocp", "check", str(empty)], env=env).exit_code == 1


# --- the committed manifests -------------------------------------------------------------------


def _committed_source(app_dir: Path) -> tuple[str, str]:
    bc = yaml.safe_load(next(app_dir.glob("*-buildconfig-*-app.yaml")).read_text(encoding="utf-8"))
    git = bc["spec"]["source"]["git"]
    return git["uri"], git["ref"]


def test_committed_manifests_are_current(tmp_path):
    """deploy/ocp is what `labctl ocp render --all --manual --out deploy/ocp --git-ref main` writes.
    A change to an app, the lab agent, the lab or this renderer that changes the output must be
    committed with it: run this test with TWINLAB_REFRESH_OCP=1 to rewrite the folders."""
    apps = ocp_manual.lab_apps(ROOT)
    assert {"showcase", "qtt", "loandesk"} <= set(apps)
    refresh = os.environ.get("TWINLAB_REFRESH_OCP") == "1"
    if refresh or not (DEPLOY / "loandesk").is_dir():
        origin = ocp_manual.git_defaults(ROOT)[0]
        known = (
            _committed_source(DEPLOY / "loandesk")[0] if (DEPLOY / "loandesk").is_dir() else None
        )
        ocp_manual.render_all(ROOT, DEPLOY, git_url=origin or known or URL, git_ref="main")
    url, ref = _committed_source(DEPLOY / "loandesk")
    fresh = tmp_path / "fresh"
    ocp_manual.render_all(ROOT, fresh, git_url=url, git_ref=ref)

    def snapshot(folder: Path) -> dict[str, bytes]:
        return {str(p.relative_to(folder)).replace("\\", "/"): p.read_bytes().replace(b"\r\n", b"\n")
                for p in sorted(folder.rglob("*")) if p.is_file()}  # fmt: skip

    committed, rendered = snapshot(DEPLOY), snapshot(fresh)
    stale = sorted(
        [f for f in rendered if committed.get(f) != rendered[f]]
        + [f for f in committed if f not in rendered]
    )
    assert not stale, (
        "deploy/ocp is out of date; run `TWINLAB_REFRESH_OCP=1 python -m pytest "
        f"tests/test_ocp_manual.py -k committed` and commit the result. Differs: {stale[:6]}"
    )
    assert ref == "main", "the committed folders are built from `main`"
    assert ocp_manual.check_tree(DEPLOY) == []


def test_committed_folders_hold_no_secret_values():
    secrets_seen = re.compile(
        r"(?i)(password|token)[\"']?\s*(?:=|:\s)\s*[\"'][^\"'$<{\s][^\"']{5,}"
    )
    for path in DEPLOY.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "kind: Secret" not in text, path
            assert not secrets_seen.search(text), path
            assert dbservers.LOCAL_FILE not in path.name
