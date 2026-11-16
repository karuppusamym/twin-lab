"""OpenShift manifests (labctl.ocp), the `labctl ocp` commands and the deploy-ocp workflow.

No cluster here: these check what is rendered. The Docker test runs the lab agent image as an
arbitrary UID with group 0, as OpenShift's restricted SCC does (skipped without Docker)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from labctl import lab, ocp
from labctl.bindings import load_app
from labctl.cli import app as cli_app

ROOT = Path(__file__).resolve().parents[3]
APPS = ["showcase", "qtt"]
REGISTRY, TAG, NS = "ghcr.io/example/twin-lab", "0123abcd", "twinlab-test"
FAKE = {
    "lab-token": "FAKE-lab-token-6d1f0c",
    "db-password": "FAKE-db-password-93b2aa",
    "bot-password": "FAKE-bot-password-4e7c11",
}
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-ocp.yml"
DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


@pytest.fixture(autouse=True)
def _known_secrets(monkeypatch, tmp_path):
    """Make known fake values the ones labctl would use anywhere (the local lab's secret files and
    any generated value), so the tests can prove none of them reaches the rendered output."""
    monkeypatch.setenv("TWINLAB_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("TWINLAB_LAB_TOKEN", FAKE["lab-token"])
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "lab-db-password").write_text(FAKE["db-password"], encoding="utf-8")
    (tmp_path / "state" / "lab-bot-password").write_text(FAKE["bot-password"], encoding="utf-8")
    values = iter(FAKE.values())
    monkeypatch.setattr(ocp.secrets, "token_urlsafe", lambda n=24: next(values))
    assert lab.db_password(ROOT) == FAKE["db-password"]


def _render(app_id: str) -> list[dict]:
    return ocp.render(ROOT, app_id, registry=REGISTRY, tag=TAG, namespace=NS)


def _by(docs, kind, name):
    found = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} found"
    return found[0]


def _pod_specs(docs):
    for d in docs:
        if d["kind"] in ("Deployment", "Job"):
            yield d, d["spec"]["template"]["spec"]


def _containers(docs):
    for d, pod in _pod_specs(docs):
        for c in pod.get("initContainers", []) + pod["containers"]:
            yield d, c


def _env(container) -> dict:
    return {e["name"]: e for e in container.get("env", [])}


@pytest.mark.parametrize("app_id", APPS)
def test_render_has_every_object(app_id):
    docs = _render(app_id)
    n = lambda part: f"twinlab-{app_id}-{part}"  # noqa: E731
    for kind, name in [
        ("Deployment", n("db")), ("PersistentVolumeClaim", n("db")), ("Service", n("db")),
        ("Job", n("db-setup")), ("ConfigMap", n("db-setup")),
        ("Deployment", n("agent")), ("Service", n("agent")),
        ("PersistentVolumeClaim", n("agent-state")), ("ConfigMap", n("schema")),
        ("Deployment", n("app")), ("Service", n("app")), ("Route", n("web")),
    ]:  # fmt: skip
        _by(docs, kind, name)
    assert not [d for d in docs if d["kind"] == "Secret"], "the Secret is never rendered"
    assert _by(docs, "Deployment", n("db"))["spec"]["strategy"]["type"] == "Recreate"
    schema = (ROOT / "apps" / app_id / "db" / "schema.sql").read_text(encoding="utf-8")
    assert _by(docs, "ConfigMap", n("schema"))["data"]["schema.sql"] == schema


@pytest.mark.parametrize("app_id", APPS)
def test_names_labels_namespace(app_id):
    for d in _render(app_id):
        meta = d["metadata"]
        assert DNS1123.match(meta["name"]) and len(meta["name"]) <= 63, meta["name"]
        assert meta["namespace"] == NS
        assert meta["labels"]["app.kubernetes.io/part-of"] == "twinlab"
        assert meta["labels"]["app.kubernetes.io/instance"] == app_id
    for d, _pod in _pod_specs(_render(app_id)):
        labels = d["spec"]["template"]["metadata"]["labels"]
        assert labels["app.kubernetes.io/part-of"] == "twinlab"
        assert labels["app.kubernetes.io/instance"] == app_id
        if d["kind"] == "Deployment":
            assert d["spec"]["selector"]["matchLabels"].items() <= labels.items()


def test_bad_names_are_refused():
    with pytest.raises(ocp.OcpError):
        ocp.render(ROOT, "showcase", registry=REGISTRY, tag=TAG, namespace="Not_A_Name")
    with pytest.raises(ocp.OcpError):
        ocp.render(ROOT, "x" * 60, registry=REGISTRY, tag=TAG, namespace=NS)


@pytest.mark.parametrize("app_id", APPS)
def test_every_container_is_restricted(app_id):
    docs = _render(app_id)
    seen = 0
    for _d, pod in _pod_specs(docs):
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert "runAsUser" not in pod["securityContext"]
        assert "fsGroup" not in pod["securityContext"]  # OpenShift assigns it
        assert not pod.get("hostNetwork") and not pod.get("hostPID")
    for _d, c in _containers(docs):
        seen += 1
        sc = c["securityContext"]
        assert sc["runAsNonRoot"] is True, c["name"]
        assert "runAsUser" not in sc and "runAsGroup" not in sc, c["name"]
        assert sc["allowPrivilegeEscalation"] is False, c["name"]
        assert sc["capabilities"]["drop"] == ["ALL"] and "add" not in sc["capabilities"]
        assert sc["seccompProfile"]["type"] == "RuntimeDefault"
        assert not sc.get("privileged")
        res = c["resources"]
        assert set(res["requests"]) == {"cpu", "memory"} == set(res["limits"]), c["name"]
    assert seen == 4  # db, db-setup, agent, app


@pytest.mark.parametrize("app_id", APPS)
def test_no_secret_value_in_output(app_id, tmp_path):
    docs = _render(app_id)
    text = ocp.to_yaml(docs) + json.dumps(docs)
    out = CliRunner().invoke(
        cli_app,
        ["ocp", "render", app_id, "--registry", REGISTRY, "--tag", TAG, "--namespace", NS,
         "--out", str(tmp_path / "out")],
        env={"LABCTL_ROOT": str(ROOT)},
    )  # fmt: skip
    assert out.exit_code == 0, out.output
    files = sorted((tmp_path / "out").glob("*.yaml"))
    assert len(files) == len(docs)
    text += out.output + "".join(f.read_text(encoding="utf-8") for f in files)
    for value in FAKE.values():
        assert value not in text
    assert lab.lab_token(ROOT) not in text and lab.bot_password(ROOT) not in text
    # Every secret reaches a container only by secretKeyRef to the app's one Secret.
    refs = set()
    for _, c in _containers(docs):
        for e in c.get("env", []):
            if "valueFrom" in e:
                ref = e["valueFrom"]["secretKeyRef"]
                assert ref["name"] == f"twinlab-{app_id}-secrets"
                refs.add(ref["key"])
            else:
                assert not re.search(r"(?i)(password|token)$", e["name"]), e["name"]
    assert refs == set(ocp.SECRET_KEYS)
    # The generated Secret is only made by `ensure_secret`; its values are the generated ones.
    secret = ocp.secret_manifest(app_id, NS)
    assert secret["metadata"]["name"] == f"twinlab-{app_id}-secrets"
    assert set(secret["stringData"]) == set(ocp.SECRET_KEYS)


@pytest.mark.parametrize("app_id", APPS)
def test_ports_and_health_match_app_yaml(app_id):
    app = load_app(ROOT, app_id)
    docs = _render(app_id)
    n = lambda part: f"twinlab-{app_id}-{part}"  # noqa: E731
    port = (app.run.web or app.run.api).port
    health = app.run.api.health

    app_c = _by(docs, "Deployment", n("app"))["spec"]["template"]["spec"]["containers"][0]
    assert app_c["image"] == f"{REGISTRY}/{app_id}:{TAG}"
    assert {p["containerPort"] for p in app_c["ports"]} >= {port}
    for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
        assert app_c[probe]["httpGet"] == {"path": health, "port": app.run.api.port}
    db_url = _env(app_c)[app.run.env.database_url]["value"]
    assert db_url == f"postgresql://lab:$(DB_PASSWORD)@{n('agent')}:15432/{app_id}"
    names = [e["name"] for e in app_c["env"]]
    assert names.index("DB_PASSWORD") < names.index(app.run.env.database_url)  # $(VAR) order
    assert {p["port"] for p in _by(docs, "Service", n("app"))["spec"]["ports"]} >= {port}

    agent_c = _by(docs, "Deployment", n("agent"))["spec"]["template"]["spec"]["containers"][0]
    env = _env(agent_c)
    assert agent_c["image"] == f"{REGISTRY}/lab-agent:{TAG}"
    assert env["APP_UPSTREAM"]["value"] == f"http://{n('app')}:{port}"
    assert env["APP_HEALTH_PATH"]["value"] == health
    assert env["DB_UPSTREAM"]["value"] == f"{n('db')}:5432"
    assert env["SCHEMA_PATH"]["value"] == "/lab/schema.sql"
    assert env["STATE_DIR"]["value"] == "/var/lib/twinlab"
    mounts = {m["mountPath"]: m["name"] for m in agent_c["volumeMounts"]}
    assert mounts == {"/lab": "schema", "/var/lib/twinlab": "state"}
    for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
        command = agent_c[probe]["exec"]["command"]
        assert command[:2] == ["python", "-c"] and "X-Lab-Token" in command[2]
    ports = {p["name"]: p["port"] for p in _by(docs, "Service", n("agent"))["spec"]["ports"]}
    assert ports == {"http": 9000, "db": 15432}

    route = _by(docs, "Route", n("web"))
    assert route["spec"]["to"] == {"kind": "Service", "name": n("agent"), "weight": 100}
    assert route["spec"]["port"]["targetPort"] == "http"
    assert route["spec"]["tls"]["termination"] == "edge"
    assert [d for d in docs if d["kind"] == "Route"] == [route]  # nothing else, never 15432


@pytest.mark.parametrize("app_id", APPS)
def test_db_setup_job_reuses_lab_sql(app_id):
    docs = _render(app_id)
    n = lambda part: f"twinlab-{app_id}-{part}"  # noqa: E731
    sql = _by(docs, "ConfigMap", n("db-setup"))["data"]["setup.sql"]
    marks = (ocp._OWNER_MARK, ocp._BOT_MARK)
    expected = lab._roles_sql(app_id, *marks).replace(f"'{marks[0]}'", ":'owner_pw'")
    assert sql == expected.replace(f"'{marks[1]}'", ":'bot_pw'").lstrip("\n")
    assert lab._grants_sql() in sql
    job = _by(docs, "Job", n("db-setup"))
    c = job["spec"]["template"]["spec"]["containers"][0]
    assert _env(c)["PGHOST"]["value"] == n("db")
    assert {"PGPASSWORD", "BOT_PASSWORD"} <= set(_env(c))
    assert "IF NOT EXISTS" in sql  # safe to run again


def test_workflow_parses_and_has_expected_jobs():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    assert on["push"]["branches"] == ["main"]
    assert set(on["push"]["paths"]) >= {"apps/**", "lab/**", ".github/workflows/deploy-ocp.yml"}
    assert "app" in on["workflow_dispatch"]["inputs"]
    jobs = wf["jobs"]
    assert set(jobs) == {"plan", "build-agent", "build-app", "deploy"}
    for job in ("build-agent", "build-app"):
        assert jobs[job]["permissions"]["packages"] == "write"
        text = json.dumps(jobs[job])
        assert "ghcr.io" in text and "${{ github.sha }}" in text
    deploy = jobs["deploy"]
    assert deploy["environment"] == "ocp"
    assert set(deploy["needs"]) == {"plan", "build-agent", "build-app"}
    assert "vars.OCP_NAMESPACE" in deploy["env"]["OCP_NAMESPACE"]
    steps = deploy["steps"]
    check = steps[0]
    assert "secrets.OCP_SERVER" in check["env"]["OCP_SERVER"]
    assert "secrets.OCP_TOKEN" in check["env"]["OCP_TOKEN"]
    assert "::notice::" in check["run"] and "exit 1" not in check["run"]
    for step in steps[1:]:  # everything after the check is skipped without a cluster
        assert "steps.ocp.outputs.ready == 'true'" in step["if"], step
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "labctl ocp deploy" in runs
    assert "labctl ocp smoke" in steps[-2]["run"]  # the last real step; then logs on failure
    uses = [s["uses"] for j in jobs.values() for s in j["steps"] if "uses" in s]
    assert any(u.startswith("redhat-actions/") for u in uses)
    for u in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@v\d+", u), f"pin a major version: {u}"


def test_cli_registered_and_deploy_needs_oc(monkeypatch):
    runner = CliRunner()
    out = runner.invoke(cli_app, ["ocp", "--help"])
    assert out.exit_code == 0 and "render" in out.output and "deploy" in out.output
    monkeypatch.setattr(ocp.shutil, "which", lambda name: None)
    out = runner.invoke(
        cli_app,
        ["ocp", "deploy", "showcase", "--registry", REGISTRY, "--tag", TAG, "--namespace", NS],
        env={"LABCTL_ROOT": str(ROOT)},
    )
    assert out.exit_code == 1
    assert "`oc`" in out.output and "PATH" in out.output


def test_ensure_secret_never_overwrites(monkeypatch):
    calls: list[tuple[list[str], str | None]] = []

    def fake_oc(args, *, stdin=None, check=True, timeout=900):
        calls.append((args, stdin))
        exists = args[:2] == ["get", "secret"] and fake_oc.exists
        rc = 0 if exists or args[0] != "get" else 1
        err = "" if rc == 0 else 'Error from server (NotFound): secrets "x" not found'
        return subprocess.CompletedProcess(args, rc, "secret/x" if rc == 0 else "", err)

    monkeypatch.setattr(ocp, "_oc", fake_oc)
    fake_oc.exists = True
    assert ocp.ensure_secret("qtt", NS, log=lambda *_: None) is False
    assert [c[0][0] for c in calls] == ["get"]
    calls.clear()
    fake_oc.exists = False
    assert ocp.ensure_secret("qtt", NS, log=lambda *_: None) is True
    (get, _), (create, stdin) = calls
    assert create[0] == "create" and "-f" in create and "-" in create
    for value in FAKE.values():
        assert value not in " ".join(create)  # never on a command line
    assert json.loads(stdin)["metadata"]["name"] == "twinlab-qtt-secrets"


# --- Docker: the agent image as an arbitrary UID with group 0 ----------------------------------


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _docker(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False,
    )  # fmt: skip


@pytest.mark.skipif(not _docker_ok(), reason="needs Docker")
def test_agent_image_runs_as_arbitrary_uid():
    image = "twinlab/lab-agent:ocp-test"
    built = _docker(
        "build", "-q", "-t", image, "-f", str(ROOT / "lab/lab-agent/Dockerfile"), str(ROOT)
    )
    assert built.returncode == 0, built.stderr[-2000:]
    name = f"tl-ocp-uid-{uuid.uuid4().hex[:8]}"
    token = "ocp-uid-test-token"
    # No database and no app: the agent still starts, answers its health with 503 (app and db
    # down) and must be able to write its state as UID 1000770000, group 0.
    run = _docker(
        "run", "-d", "--name", name, "--user", "1000770000:0", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "-e", "APP_ID=uidtest",
        "-e", "APP_UPSTREAM=http://127.0.0.1:1", "-e", "STATE_DIR=/var/lib/twinlab",
        "-e", f"LAB_TOKEN={token}", image,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr
    try:
        probe = None
        for _ in range(30):
            probe = _docker("exec", name, "python", "-c", ocp._AGENT_PROBE, timeout=30)
            if probe.returncode == 0:
                break
            time.sleep(1)
        logs = _docker("logs", name).stderr[-2000:]
        assert probe is not None and probe.returncode == 0, logs
        ids = _docker("exec", name, "id").stdout
        assert "uid=1000770000" in ids and "gid=0" in ids
        write = _docker(
            "exec", name, "sh", "-c", "touch /var/lib/twinlab/w && ls -ln /var/lib/twinlab/w"
        )
        assert write.returncode == 0, write.stderr
        assert " 1000770000 0 " in write.stdout
        bad = _docker(
            "exec", "-e", "LAB_TOKEN=wrong", name, "python", "-c", ocp._AGENT_PROBE, timeout=30
        )
        assert bad.returncode == 1  # the probe really checks the token
    finally:
        _docker("rm", "-f", name)
