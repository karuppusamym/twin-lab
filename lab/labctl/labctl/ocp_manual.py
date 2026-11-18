"""Run a lab on OpenShift without a pipeline: plain files, committed, reviewed, applied by hand.

`labctl ocp render <app> --manual --out deploy/ocp/<app>` writes one folder per app:

    README.md             what to do, in order, for this app (generated; read it first)
    NN-<kind>-<name>.yaml one object per file, numbered in apply order
    kustomization.yaml    the same files, so `oc apply -k <folder>` works too

The images are built INSIDE the cluster from a branch of the repository (ImageStream +
BuildConfig, Docker strategy), so no registry outside the cluster is needed; with `--registry` the
BuildConfigs are left out and the Deployments pull prebuilt images instead. No file holds a secret
value: the Secret is created by hand (the README gives the exact command and its keys), and
`check_dir` (`labctl ocp check <folder>`) tells a reviewer whether a folder is sound before it is
applied. Nothing here talks to a cluster.

Deployments roll out when a build finishes through the `image.openshift.io/triggers` annotation.
A DeploymentConfig image-change trigger would do the same, but DeploymentConfig is deprecated;
the annotation works on the ordinary Deployments the rest of Twin Lab already renders.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labctl import dbservers, ocp
from labctl.bindings import load_app
from labctl.ocp import OcpError, name

AGENT_DOCKERFILE = "lab/lab-agent/Dockerfile"
DEFAULT_REF = "main"
GENERATED = re.compile(r"^\d\d-[a-z0-9.-]+\.yaml$")
SECRET_WORDS = re.compile(r"(?i)(password|passwd|token|secret|api[-_]?key|private[-_]?key)")
URL_WITH_PASSWORD = re.compile(r"://[^:/@\s$]+:(?!\$\()[^@\s/]+@")
SQL_PASSWORD_LITERAL = re.compile(r"(?i)\bpassword\b\s*=?\s*'[^']+'")  # not :'variable'
DNS1123 = ocp.DNS1123


# --- the Git source ----------------------------------------------------------------------------


def https_url(url: str) -> str:
    """`git@github.com:owner/repo.git` as `https://github.com/owner/repo.git` (a build can use the
    first without a key); other URLs are returned as they are."""
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+)$", url.strip())
    return f"https://{m.group(1)}/{m.group(2)}" if m else url.strip()


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=20, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def git_defaults(root: Path) -> tuple[str | None, str | None]:
    """(the origin remote as an https URL, the current branch) of the checkout; None if unknown."""
    url = _git(root, "remote", "get-url", "origin")
    return (https_url(url) if url else None), _git(root, "branch", "--show-current")


# --- what is rendered --------------------------------------------------------------------------


@dataclass
class Plan:
    """What an app's lab becomes, for the README and the index."""

    app_id: str
    title: str
    namespace: str
    kind: str  # postgres | sqlite | browser | external
    summary: str
    git_url: str | None
    git_ref: str | None
    registry: str | None
    tag: str | None
    health_path: str
    app_port: int
    secret_keys: tuple[str, ...]
    ext: dbservers.DbServer | None = None
    ephemeral: bool = False
    network_policy: bool = False
    source_secret: str | None = None
    expose_db: bool = False
    workload: str = ""  # the Deployment that holds the app: "agent" and "app" or "lab"
    base_images: list[str] = field(default_factory=list)


@dataclass
class Bundle:
    plan: Plan
    docs: list[dict]
    files: dict[str, str]  # file name -> text, README.md and kustomization.yaml included

    @property
    def manifests(self) -> list[str]:
        return [f for f in self.files if GENERATED.match(f)]


def _check_manual_names(app_id: str) -> None:
    if len(f"twinlab-{app_id}-agent-9999") > 63:  # a build is named after its BuildConfig
        raise OcpError(f"app id {app_id!r} is too long for build names (63 characters at most)")


def build_objects(
    app_id: str, namespace: str, git_url: str, git_ref: str, source_secret: str | None = None
) -> list[dict]:
    """ImageStreams, then BuildConfigs: the app's image from `apps/<app>`, the lab agent's from the
    repository root (its Dockerfile copies `lab/labctl` too, so its context cannot be
    `lab/lab-agent`). Builds are started by hand; no webhook, so no webhook secret."""
    docs: list[dict] = []
    for which in ("agent", "app"):
        docs.append({
            "apiVersion": "image.openshift.io/v1",
            "kind": "ImageStream",
            "metadata": ocp._meta(app_id, f"{which}-image", name(app_id, which), namespace),
            "spec": {"lookupPolicy": {"local": False}},
        })  # fmt: skip
    for which, context, dockerfile in (
        ("agent", None, AGENT_DOCKERFILE),
        ("app", f"apps/{app_id}", "Dockerfile"),
    ):
        source: dict = {"type": "Git", "git": {"uri": git_url, "ref": git_ref}}
        if context:
            source["contextDir"] = context
        if source_secret:
            source["sourceSecret"] = {"name": source_secret}
        big = which == "app"
        docs.append({
            "apiVersion": "build.openshift.io/v1",
            "kind": "BuildConfig",
            "metadata": ocp._meta(app_id, f"{which}-image", name(app_id, which), namespace),
            "spec": {
                "runPolicy": "Serial",
                "source": source,
                "strategy": {"type": "Docker", "dockerStrategy": {"dockerfilePath": dockerfile}},
                "output": {"to": {"kind": "ImageStreamTag", "name": f"{name(app_id, which)}:latest"}},
                "resources": ocp._resources(
                    "250m", "1Gi" if big else "512Mi", "2" if big else "1", "3Gi" if big else "2Gi"
                ),
                "successfulBuildsHistoryLimit": 3,
                "failedBuildsHistoryLimit": 3,
                "triggers": [],
            },
        })  # fmt: skip
    return docs


def _from_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    stages: set[str] = set()
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(?i)^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", line)
        if m:
            if m.group(1) not in stages and m.group(1) not in found:
                found.append(m.group(1))
            if m.group(2):
                stages.add(m.group(2))
    return found


def _kind_of(app, ext) -> str:
    if ext is not None:
        return "external"
    if dbservers.app_choice(app) == "sqlite":
        return "browser" if app.browser_only else "sqlite"
    return "postgres"


def _summary(kind: str, ext, ephemeral: bool) -> str:
    keep = "emptyDir volumes (data is lost when the pod restarts)" if ephemeral else "PVCs"
    return {
        "postgres": f"a Postgres pod and its login-setup Job, the lab agent and the app in "
        f"separate pods, with {keep}",
        "sqlite": f"ONE pod with an init step, the lab agent and the app sharing the SQLite file's "
        f"volume ({'emptyDir' if ephemeral else 'PVC'}); no database pod",
        "browser": "ONE pod with the lab agent and the app (the data lives in the browser: no "
        "database, no tables); the agent still gives web conditions and sessions",
        "external": f"no database pod: the tables are made on {ext.label if ext else ''} server "
        "your DBA provides, whose address comes from a Secret you create",
    }[kind]


def render_manual(
    root: Path,
    app_id: str,
    *,
    namespace: str = "twinlab",
    git_url: str | None = None,
    git_ref: str | None = None,
    source_secret: str | None = None,
    registry: str | None = None,
    tag: str | None = None,
    postgres_image: str = ocp.POSTGRES_IMAGE,
    pull_secret: str | None = None,
    expose_db: bool = False,
    ephemeral: bool = False,
    storage_class: str | None = None,
    network_policy: bool = False,
) -> Bundle:
    """The folder for one app: manifests, kustomization.yaml and README.md, as text."""
    ocp._check_names(app_id)
    _check_manual_names(app_id)
    if registry:
        images = ocp.Images.registry(registry, app_id, tag or "latest")
        git_url = git_ref = None
    else:
        default_url, default_ref = git_defaults(root)
        git_url, git_ref = git_url or default_url, git_ref or default_ref
        if not git_url:
            raise OcpError(
                "the Git URL for the builds is not known: pass --git-url (the checkout has no "
                "`origin` remote)"
            )
        if not git_ref:
            raise OcpError("the branch to build is not known: pass --git-ref")
        images = ocp.Images.streams(app_id, namespace)
    docs = ocp.render(
        root, app_id, namespace=namespace, postgres_image=postgres_image, pull_secret=pull_secret,
        expose_db=expose_db, images=images, tag=git_ref or tag, ephemeral=ephemeral,
        storage_class=storage_class, network_policy=network_policy,
    )  # fmt: skip
    if not registry:
        docs = build_objects(app_id, namespace, git_url, git_ref, source_secret) + docs
    app = load_app(root, app_id)
    ext = ocp._external_server(root, app)
    kind = _kind_of(app, ext)
    health = app.run.api.health if app.run.api else "/"
    keys = ocp.SECRET_KEYS if kind in ("postgres", "external") else ("lab-token",)
    plan = Plan(
        app_id=app_id, title=app.name, namespace=namespace, kind=kind,
        summary=_summary(kind, ext, ephemeral), git_url=git_url, git_ref=git_ref,
        registry=registry, tag=(tag or "latest") if registry else None, health_path=health,
        app_port=(app.run.web or app.run.api).port, secret_keys=keys, ext=ext,
        ephemeral=ephemeral, network_policy=network_policy, source_secret=source_secret,
        expose_db=expose_db, workload="lab" if kind in ("sqlite", "browser") else "agent",
        base_images=list(dict.fromkeys(_from_lines(root / "apps" / app_id / "Dockerfile")
                                       + _from_lines(root / AGENT_DOCKERFILE))),
    )  # fmt: skip
    files: dict[str, str] = {}
    for i, doc in enumerate(docs, 1):
        files[f"{i:02d}-{doc['kind'].lower()}-{doc['metadata']['name']}.yaml"] = ocp.dump(doc)
    files["kustomization.yaml"] = kustomization(list(files))
    files["README.md"] = readme(plan, docs, list(files)[:-1])
    files = {k: files[k] for k in sorted(files)}
    return Bundle(plan, docs, files)


def kustomization(manifests: list[str]) -> str:
    body = "".join(f"  - {m}\n" for m in manifests)
    return (
        "# `oc apply -k .` applies the same files, in this order. The Secret is not here: the\n"
        "# README says how to create it by hand.\n"
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\n"
        f"resources:\n{body}"
    )


def write_bundle(bundle: Bundle, out: Path) -> list[Path]:
    """Write a folder, replacing the files an earlier render made there (and only those)."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        if old.is_file() and (
            GENERATED.match(old.name) or old.name in ("kustomization.yaml", "README.md")
        ):
            old.unlink()
    paths = []
    for file_name, text in bundle.files.items():
        path = out / file_name
        path.write_bytes(text.encode("utf-8"))  # LF on every platform: the files are compared
        paths.append(path)
    return paths


def lab_apps(root: Path) -> list[str]:
    """Apps that can run in a lab: a Dockerfile and a `lab:` section in app.yaml."""
    found = []
    for folder in sorted((root / "apps").iterdir()):
        spec = folder / "app.yaml"
        if folder.is_dir() and (folder / "Dockerfile").is_file() and spec.is_file():
            try:
                data = yaml.safe_load(spec.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if isinstance(data, dict) and data.get("lab"):
                found.append(folder.name)
    return found


def render_all(root: Path, out: Path, **options) -> tuple[list[Bundle], dict[str, str]]:
    """Every app that has a `lab:` section, each in `out/<app>`, plus `out/README.md`. Apps that
    cannot run on OpenShift (the SQL Server container) are skipped, with the reason returned."""
    bundles: list[Bundle] = []
    skipped: dict[str, str] = {}
    for app_id in lab_apps(root):
        try:
            bundle = render_manual(root, app_id, **options)
        except (OcpError, dbservers.DbServerError) as exc:
            skipped[app_id] = str(exc)
            continue
        write_bundle(bundle, out / app_id)
        bundles.append(bundle)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_bytes(index_readme(bundles, skipped).encode("utf-8"))
    return bundles, skipped


# --- README ------------------------------------------------------------------------------------


def _n(plan: Plan, part: str) -> str:
    return name(plan.app_id, part)


def _block(*lines: str) -> str:
    return "```bash\n" + "\n".join(lines) + "\n```\n"


def _job_file(docs: list[dict]) -> str:
    i, doc = next((i, d) for i, d in enumerate(docs, 1) if d["kind"] == "Job")
    return f"{i:02d}-job-{doc['metadata']['name']}.yaml"


def _objects_table(docs: list[dict], files: list[str]) -> str:
    rows = ["| File | Object |", "|---|---|"]
    for file_name in files:
        if GENERATED.match(file_name):
            doc = docs[int(file_name[:2]) - 1]
            rows.append(f"| `{file_name}` | {doc['kind']} `{doc['metadata']['name']}` |")
    return "\n".join(rows) + "\n"


def _conn_secret_text(plan: Plan) -> str:
    """The Secret of a DBA-provided server: keys and shapes, with placeholders in the values."""
    ext = plan.ext
    ph = dbservers.DbServer(
        name=ext.name, engine=ext.engine, host=ext.host, port=ext.port, database=ext.database,
        service=ext.service, owner_user=ext.owner_user, owner_password="OWNERPW",
        bot_user=ext.bot_user, bot_password="BOTPW", schema=ext.schema,
    )  # fmt: skip
    agent, bot = _n(plan, "agent"), bool(ph.bot_user)

    def fill(text: str) -> str:
        return text.replace("OWNERPW", "<owner-password>").replace("BOTPW", "<bot-password>")

    pw = "<bot-password>" if bot else "<owner-password>"
    return _block(
        f"oc create secret generic {ocp.conn_secret_name(plan.app_id)} -n {plan.namespace} \\",
        f"  --from-literal=admin-url='{fill(ph.url())}' \\",
        f"  --from-literal=app-url='{fill(ph.url(bot=bot, host=agent, port=ocp.AGENT_DB_PORT))}' \\",
        f"  --from-literal=app-password='{pw}' \\",
        "  --from-literal=app-connection-string='"
        + fill(ph.connection_string(bot=True, host=agent, port=ocp.AGENT_DB_PORT))
        + "'",
        f"oc label secret {ocp.conn_secret_name(plan.app_id)} -n {plan.namespace} "
        f"app.kubernetes.io/part-of=twinlab app.kubernetes.io/instance={plan.app_id}",
    )


def readme(plan: Plan, docs: list[dict], files: list[str]) -> str:
    a, ns = plan.app_id, plan.namespace
    secret = ocp.secret_name(a)
    wl = _n(plan, plan.workload)
    builds = plan.registry is None
    out: list[str] = []

    def add(text: str) -> None:
        out.append(text.rstrip("\n") + "\n")

    add(f"# {plan.title} on OpenShift (apply by hand)\n")
    add(
        "Generated by `labctl ocp render --manual`. Do not edit these files by hand: change the "
        "app or the options and render again, so the folder stays what `labctl ocp check` "
        "approved. Nothing here holds a password or token.\n"
    )
    add(f"- **App:** `{a}`, listening on port {plan.app_port}; health path `{plan.health_path}`")
    add(
        f"- **OpenShift project:** `{ns}` (named inside every file; to use another, render again "
        "with `--namespace`)"
    )
    if builds:
        add(
            f"- **Images:** built in the cluster from `{plan.git_url}`, branch/ref `{plan.git_ref}`"
        )
    else:
        add(
            f"- **Images:** pulled from `{plan.registry}` (`lab-agent:{plan.tag}` and "
            f"`{a}:{plan.tag}`); they must exist there before step 3"
        )
    add(f"- **The lab here is:** {plan.summary}\n")

    add("## What is in this folder\n")
    add(_objects_table(docs, files))
    add(
        "`kustomization.yaml` lists the same files, so `oc apply -k .` works too. Every object "
        f"carries `app.kubernetes.io/part-of=twinlab` and `app.kubernetes.io/instance={a}`, so "
        "one selector finds (and removes) all of it.\n"
    )

    add("## Before you start\n")
    add(
        "- Someone with rights on the project (`edit`) and the `oc` command line, or the web "
        "console (**Import YAML**, the `+` at the top right)."
    )
    add(f"- The project exists: `oc new-project {ns}` (a cluster administrator may have to).")
    if builds:
        add(
            "- The project may run Docker-strategy builds (`oc auth can-i create "
            "builds/docker`; ask the administrator if it says `no`) and the cluster's internal "
            "image registry is on."
        )
        add(
            f"- The build pods can reach `{plan.git_url}` and the image sources the Dockerfiles "
            f"use ({', '.join(f'`{i}`' for i in plan.base_images) or 'none'}), and the package "
            "registries `pip` (and `npm`, for apps with a front end) read. In an office that "
            "usually means a mirror: ask for the cluster's proxy or mirror settings (they are "
            "set cluster-wide, not in these files)."
        )
        if plan.source_secret:
            add(
                f"- The repository is private: create the source secret `{plan.source_secret}` "
                "first (below). The BuildConfigs name it and nothing more."
            )
        else:
            add(
                "- The repository must be readable without a login. If it is private, render "
                "again with `--source-secret <name>` and create that secret (see "
                "the private repository note below)."
            )
    if not plan.ephemeral:
        add(
            "- A default storage class that can make `ReadWriteOnce` volumes (or render again with "
            "`--storage-class <name>`, or `--ephemeral` to use no volumes at all)."
        )
    if plan.network_policy:
        add(
            "- The cluster enforces NetworkPolicy (OpenShift does); the router namespace has the "
            "label `policy-group.network.openshift.io/ingress`."
        )
    add("")

    add("## 1. Create the Secret by hand (once)\n")
    add(
        f"The Deployments read `{secret}`; it is not in these files so no value is ever in Git. "
        "Make up the values (here they are random); keep them for whoever needs the admin API.\n"
    )
    parts = " \\\n  ".join(
        f'--from-literal={k}="$(openssl rand -hex 24)"' for k in plan.secret_keys
    )
    add(
        _block(
            f"oc create secret generic {secret} -n {ns} \\\n  {parts}",
            f"oc label secret {secret} -n {ns} app.kubernetes.io/part-of=twinlab "
            f"app.kubernetes.io/instance={a}",
        )
    )
    keys = ", ".join(f"`{k}`" for k in plan.secret_keys)
    add(
        f"Keys it needs: {keys}. In the console: **Workloads > Secrets > Create > Key/value "
        "secret**, one entry per key. Never create it again on a running lab: "
        + (
            "the data would no longer match its passwords."
            if len(plan.secret_keys) > 1
            else "everyone using the admin API would need the new token."
        )
    )
    if plan.kind == "postgres":
        add(
            "`db-password` becomes the Postgres password on its first start and `bot-password` "
            "the password of the `bot` login that bots use."
        )
    if plan.kind == "external":
        add(
            f"\nThe DBA's server (`{plan.ext.name}`, {plan.ext.label} at `{plan.ext.host}:"
            f"{plan.ext.port}`) needs a second Secret with its logins; replace each `<...>`:\n"
        )
        add(_conn_secret_text(plan))
        add(
            "The manifests contain the server's address (in the `DB_UPSTREAM` variable) but no "
            "login. The cluster must be allowed to reach that port."
        )
    if builds:
        add("\n### Private repository\n")
        add(
            "The BuildConfigs name a source secret only when rendered with `--source-secret "
            "<name>`"
            + (" (this folder does)" if plan.source_secret else " (this folder does not)")
            + "; the secret is yours to create:"
        )
        sec = plan.source_secret or "<name>"
        add(
            _block(
                f"oc create secret generic {sec} -n {ns} --type=kubernetes.io/basic-auth \\",
                "  --from-literal=username=<git user> --from-literal=password=<access token>",
            )
        )
    add("")

    add("## 2. Apply the files, in order\n")
    add(f"From this folder, as a user who can edit project `{ns}`:\n")
    add(
        _block(
            f"oc apply -f . -n {ns}      # the numbered files apply in name order",
            f"# or: oc apply -k . -n {ns}",
        )
    )
    add(
        "In the console, open **Import YAML** and paste the files one after another in number "
        "order (the page accepts several documents separated by `---`). Objects that need the "
        "Secret wait for it; they start when it exists.\n"
    )
    if plan.kind == "postgres":
        add(
            f"The Job `{_n(plan, 'db-setup')}` sets the database logins. It waits for the "
            "database, so it may restart a few times at first. A Job cannot be edited: to run it "
            f"again, `oc delete job {_n(plan, 'db-setup')} -n {ns}` and apply its file again.\n"
        )
    if plan.kind == "external":
        add(
            f"The Job `{_n(plan, 'db-setup')}` creates the app's tables on the DBA's server where "
            "they are missing (it needs the second Secret). To run it again, delete it and apply "
            "its file again.\n"
        )

    n = 3
    if builds:
        add(f"## {n}. Build the images\n")
        add(
            _block(
                f"oc start-build {_n(plan, 'agent')} -n {ns} --follow",
                f"oc start-build {_n(plan, 'app')} -n {ns} --follow",
                f"oc get builds -n {ns} -l app.kubernetes.io/instance={a}",
            )
        )
        add(
            "The first builds take several minutes. In the console: **Builds > BuildConfigs** > "
            "the build > **Start build**, then the **Logs** tab. When a build pushes its image, "
            "the Deployment that uses it rolls out by itself: its `image.openshift.io/triggers` "
            "annotation names the ImageStream tag (`latest`) to follow. (The annotation was "
            "chosen over a DeploymentConfig trigger, which is deprecated.) Start the builds again "
            "whenever the branch changes; nothing starts them for you. Until the first builds finish the pods show `ImagePullBackOff` or `Init:ImagePullBackOff`: that is expected."
        )
        if plan.kind == "external":
            add(
                f"The Job `{_n(plan, 'db-setup')}` runs in the agent's image, so it waits for "
                "the first agent build; delete it and apply its file again if it gave up."
            )
        add("")
    else:
        add(f"## {n}. Images\n")
        add(
            f"Make sure `{plan.registry}/lab-agent:{plan.tag}` and `{plan.registry}/{a}:"
            f"{plan.tag}` can be pulled (a pull secret: render again with `--pull-secret`).\n"
        )
    n += 1

    add(f"## {n}. Watch it come up\n")
    add(
        _block(
            f"oc get pods -n {ns} -l app.kubernetes.io/instance={a} -w",
            f"oc logs deployment/{wl} -c agent -n {ns}",
        )
    )
    if plan.kind in ("sqlite", "browser"):
        add(
            f"Everything is in the one pod of `{wl}`: its init container `sqlite-init` runs "
            "first (it makes the database file and the tables), then the `agent` and `app` "
            "containers. `oc logs deployment/" + wl + " -c app` shows the app.\n"
        )
    else:
        add(f"Logs of the app: `oc logs deployment/{_n(plan, 'app')} -n {ns}`.\n")
    if plan.kind == "postgres":
        add(
            "When the app pod is Ready (it has made its tables), run the login Job once more so "
            "the `bot` login gets rows in them:\n"
        )
        add(
            _block(
                f"oc delete job {_n(plan, 'db-setup')} -n {ns}",
                f"oc apply -f {_job_file(docs)} -n {ns}",
            )
        )
    n += 1

    add(f"## {n}. Check health through the Route\n")
    add(
        _block(
            f"HOST=$(oc get route {_n(plan, 'web')} -n {ns} -o jsonpath='{{.spec.host}}')",
            f"TOKEN=$(oc get secret {secret} -n {ns} -o jsonpath='{{.data.lab-token}}' | base64 -d)",
            'curl -s -H "X-Lab-Token: $TOKEN" https://$HOST/_lab/health    # the lab agent: app'
            + ("" if plan.kind in ("sqlite", "browser") else " and database")
            + " up",
            f"curl -s -o /dev/null -w '%{{http_code}}\\n' https://$HOST{plan.health_path}    # the app itself",
        )
    )
    add(
        f"No `curl`? Open `https://<route host>{plan.health_path}` in a browser (the host is on "
        "**Networking > Routes**); the app answers without the token. Add `-k` to `curl` if "
        "the router's certificate is self-signed. `401` from `/_lab/health` means the token is "
        "wrong; `503` means the agent runs but the app or database is not up yet.\n"
    )
    n += 1

    add(f"## {n}. Load data, take the baseline, reset\n")
    add("The lab agent's admin API (header `X-Lab-Token`) is what the portal uses locally:\n")
    add(
        _block(
            "# once the lab is healthy: record the starting data, so Reset has something to go back to",
            'curl -s -X POST -H "X-Lab-Token: $TOKEN" -H "Content-Type: application/json" '
            '-d \'{"label": "start"}\' https://$HOST/_lab/data/baseline',
            '# load rows ({"rows": {"<table>": [{...}, ...]}, "label": "..."}) from a file',
            'curl -s -X POST -H "X-Lab-Token: $TOKEN" -H "Content-Type: application/json" '
            "--data @rows.json https://$HOST/_lab/data/load",
            "# put the data back to the baseline",
            'curl -s -X POST -H "X-Lab-Token: $TOKEN" https://$HOST/_lab/reset',
            "# what the lab holds; conditions (slow pages) and sessions have their own paths",
            'curl -s -H "X-Lab-Token: $TOKEN" https://$HOST/_lab/info',
        )
    )
    if plan.kind == "browser":
        add(
            "This app keeps its data in the browser, so there are no tables to load or reset; "
            "web conditions and sessions work.\n"
        )
    n += 1

    add(f"## {n}. Roll back\n")
    if builds:
        add(
            "Every build keeps its image in the ImageStream. Point `latest` at an older one and "
            "the Deployment follows:\n"
        )
        add(
            _block(
                f"oc describe imagestream {_n(plan, 'app')} -n {ns}     # every pushed image, newest first",
                f"oc tag {_n(plan, 'app')}@sha256:<older digest> {_n(plan, 'app')}:latest -n {ns}",
                f"# the agent the same way: {_n(plan, 'agent')}",
            )
        )
        add(
            "For a quick undo of the last rollout: "
            f"`oc rollout undo deployment/{wl} -n {ns}`"
            + (f" (and `deployment/{_n(plan, 'app')}`)" if plan.workload == "agent" else "")
            + ". The next build moves `latest` forward again. The data stays on its volumes "
            "either way.\n"
        )
    else:
        add(
            f"Apply the files again after rendering with the previous `--tag`, or `oc rollout "
            f"undo deployment/{wl} -n {ns}`. The data stays on its volumes.\n"
        )
    n += 1

    add(f"## {n}. Remove everything\n")
    add(
        _block(
            f"oc delete all,configmap,pvc,secret,job,networkpolicy,imagestream,buildconfig,route "
            f"-n {ns} -l app.kubernetes.io/instance={a}",
        )
    )
    add(
        "This also deletes the data (the volumes) and, if you labelled them as above, the "
        "Secrets. Check first with the same selector and `oc get` in place of `oc delete`.\n"
    )

    add("## What differs from the local lab\n")
    add(
        "- **One copy per app** in the project, shared by everyone who uses it: there are no "
        "copies per run (`labctl lab copy`), no per-run ports, and the portal does not manage it "
        "yet; use the admin API above."
    )
    add(
        "- **Bots** reach the app through the Route (HTTPS). "
        + (
            "There is no database link: SQLite is a file inside the pod, so bots get no database "
            "address and database slowdowns (fault conditions) cannot apply; web conditions and "
            "sessions do."
            if plan.kind in ("sqlite", "browser")
            else "The database link (port 15432, through the agent so conditions apply) is inside the "
            "cluster only"
            + (
                ", plus a NodePort Service `" + _n(plan, "db-external") + "` you asked for."
                if plan.expose_db
                else "."
            )
        )
    )
    if plan.kind == "postgres":
        add(
            "- **The database owner is not a superuser** in the OpenShift Postgres image; the "
            "setup Job signs in as `postgres` to create the `bot` login. A data pack whose tables "
            "refer to each other in a circle may load differently."
        )
    if plan.kind == "external":
        add("- The server is the DBA's, not ours: its logins and firewall are theirs to change.")
    if plan.ephemeral:
        add(
            "- **Volumes are emptyDir** (`--ephemeral`): a pod restart loses the data and the "
            "baselines."
        )
    if plan.kind in ("sqlite", "browser") and not plan.ephemeral:
        add(
            "- The SQLite file sits on a `ReadWriteOnce` volume mounted by one pod only, in WAL "
            "mode. A network file system that does not support shared-memory mapping can break "
            "WAL: use block storage."
        )
    add(
        "- **Everything runs as an arbitrary user** the cluster picks (the `restricted-v2` "
        "security context constraint): no root, no extra capabilities. Nothing needs more."
    )
    add(
        "- Nothing here was tried on a real cluster when it was generated: the first apply is the "
        "real test. `labctl ocp check <this folder>` verifies the files offline before you do.\n"
    )
    return "\n".join(out)


def index_readme(bundles: list[Bundle], skipped: dict[str, str]) -> str:
    out = [
        "# Twin Lab on OpenShift: manifests to apply by hand\n",
        "Generated by `labctl ocp render --all --manual`. One folder per app; each has its own "
        "`README.md` with the exact steps. The images are built inside the cluster from the "
        "branch named in each BuildConfig, so nothing comes from an outside registry. No file "
        "holds a secret value.\n",
        "| App | Lab on OpenShift | Folder |",
        "|---|---|---|",
    ]
    for b in bundles:
        out.append(
            f"| `{b.plan.app_id}` | {b.plan.summary} | [{b.plan.app_id}/]({b.plan.app_id}/README.md) |"
        )
    out.append("")
    if skipped:
        out.append("Not rendered:\n")
        out.extend(f"- `{a}`: {why}" for a, why in skipped.items())
        out.append("")
    out += [
        "Check a folder before applying it (offline, no cluster needed):\n",
        "```bash\nlabctl ocp check deploy/ocp\n```\n",
        "Then follow the folder's README: create the Secret by hand, `oc apply -f <folder>`, "
        "start the builds. To change the project, branch or options, render again; the test "
        "suite fails when these files no longer match what `labctl` renders.\n",
    ]
    return "\n".join(out)


# --- offline validation ------------------------------------------------------------------------


def _subset(selector: dict, labels: dict) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def _pod_specs(doc: dict):
    if doc.get("kind") in ("Deployment", "Job"):
        yield (
            doc["spec"]["template"]["metadata"].get("labels") or {},
            doc["spec"]["template"]["spec"],
        )


def check_dir(folder: Path) -> list[str]:
    """What is wrong with a rendered app folder, as one line per problem (empty: sound). Checks
    the files only: YAML, required fields, names, that every reference resolves between objects,
    that no secret value is present, and the restricted-SCC rules."""
    folder = Path(folder)
    problems: list[str] = []

    def bad(where: str, msg: str) -> None:
        problems.append(f"{where}: {msg}")

    if not folder.is_dir():
        return [f"{folder}: not a folder"]
    names = sorted(p.name for p in folder.iterdir() if p.is_file())
    manifests = [n for n in names if GENERATED.match(n)]
    if not manifests:
        return [f"{folder}: no manifest files (NN-kind-name.yaml)"]
    numbers = [int(n[:2]) for n in manifests]
    if numbers != list(range(1, len(numbers) + 1)):
        bad(folder.name, "file numbers are not 01, 02, 03... in a row")
    docs: list[tuple[str, dict]] = []
    for file_name in manifests:
        try:
            loaded = list(yaml.safe_load_all((folder / file_name).read_text(encoding="utf-8")))
        except (OSError, yaml.YAMLError) as exc:
            bad(file_name, f"cannot be read as YAML: {str(exc).splitlines()[0]}")
            continue
        if len([d for d in loaded if d]) != 1:
            bad(file_name, "must hold exactly one object")
        for d in loaded:
            if not isinstance(d, dict):
                bad(file_name, "is not a Kubernetes object")
                continue
            docs.append((file_name, d))

    index: dict[tuple[str, str], dict] = {}
    namespaces, instances = set(), set()
    for file_name, d in docs:
        kind, meta = d.get("kind"), d.get("metadata") or {}
        obj = meta.get("name")
        if not (d.get("apiVersion") and kind and obj):
            bad(file_name, "needs apiVersion, kind and metadata.name")
            continue
        if file_name[3:] != f"{kind.lower()}-{obj}.yaml":
            bad(file_name, f"should be named NN-{kind.lower()}-{obj}.yaml")
        if not DNS1123.match(obj) or len(obj) > 63:
            bad(file_name, f"the name {obj!r} is not a DNS-1123 name of at most 63 characters")
        if (kind, obj) in index:
            bad(file_name, f"{kind}/{obj} appears twice")
        index[(kind, obj)] = d
        if kind == "Secret":
            bad(file_name, "a Secret does not belong in the folder (create it by hand, see README)")
        if meta.get("namespace"):
            namespaces.add(meta["namespace"])
        else:
            bad(file_name, "metadata.namespace is missing")
        labels = meta.get("labels") or {}
        if labels.get("app.kubernetes.io/part-of") != "twinlab" or not labels.get(
            "app.kubernetes.io/instance"
        ):
            bad(file_name, "needs the labels app.kubernetes.io/part-of=twinlab and /instance=<app>")
        instances.add(labels.get("app.kubernetes.io/instance"))
        for value in labels.values():
            if len(str(value)) > 63:
                bad(file_name, f"label value {value!r} is longer than 63 characters")
    if len(namespaces) > 1:
        bad(folder.name, f"objects name different namespaces: {', '.join(sorted(namespaces))}")
    if len(instances) > 1:
        bad(
            folder.name,
            f"objects belong to different apps: {', '.join(sorted(map(str, instances)))}",
        )
    app_id = next(iter(instances), None) or ""

    by_kind = lambda k: {n: d for (kk, n), d in index.items() if kk == k}  # noqa: E731
    configmaps, pvcs, services = (
        by_kind("ConfigMap"),
        by_kind("PersistentVolumeClaim"),
        by_kind("Service"),
    )
    streams = by_kind("ImageStream")
    pods: list[tuple[str, str, str, dict, dict]] = []  # file, object, kind, labels, pod spec
    for file_name, d in docs:
        for pod_labels, pod in _pod_specs(d):
            pods.append((file_name, d["metadata"]["name"], d["kind"], pod_labels, pod))
    allowed_secrets = {ocp.secret_name(app_id), ocp.conn_secret_name(app_id)}
    known_keys = set(ocp.SECRET_KEYS) | {
        "admin-url",
        "app-url",
        "app-password",
        "app-connection-string",
    }
    secret_refs: set[tuple[str, str]] = set()

    for file_name, _obj, pod_kind, _labels, pod in pods:
        where = f"{file_name}"
        spec = pod
        sc = spec.get("securityContext") or {}
        if sc.get("runAsNonRoot") is not True:
            bad(where, "pod securityContext.runAsNonRoot must be true")
        for key in ("runAsUser", "runAsGroup", "fsGroup", "supplementalGroups"):
            if key in sc:
                bad(where, f"pod securityContext.{key} must not be set (OpenShift assigns it)")
        for key in ("hostNetwork", "hostPID", "hostIPC"):
            if spec.get(key):
                bad(where, f"{key} is not allowed")
        volumes = {v.get("name"): v for v in spec.get("volumes") or []}
        for vname, v in volumes.items():
            if "configMap" in v and v["configMap"].get("name") not in configmaps:
                bad(
                    where,
                    f"volume {vname}: ConfigMap {v['configMap'].get('name')!r} is not in the folder",
                )
            if (
                "persistentVolumeClaim" in v
                and v["persistentVolumeClaim"].get("claimName") not in pvcs
            ):
                bad(
                    where,
                    f"volume {vname}: PVC {v['persistentVolumeClaim'].get('claimName')!r} is not in the folder",
                )
            if "hostPath" in v:
                bad(where, f"volume {vname}: hostPath is not allowed")
        for ref in spec.get("imagePullSecrets") or []:
            secret_refs.add((ref.get("name", ""), ""))
        every = [("container", c) for c in spec.get("containers") or []]
        every += [("init container", c) for c in spec.get("initContainers") or []]
        seen: set[str] = set()
        for role, c in every:
            cname = c.get("name", "?")
            tag = f"{where}: {role} {cname}"
            if cname in seen:
                bad(where, f"container name {cname!r} used twice")
            seen.add(cname)
            csc = c.get("securityContext") or {}
            if csc.get("runAsNonRoot") is not True:
                bad(tag, "securityContext.runAsNonRoot must be true")
            if "runAsUser" in csc or "runAsGroup" in csc:
                bad(
                    tag,
                    "must not set runAsUser/runAsGroup (a fixed UID fails under the restricted SCC)",
                )
            if csc.get("allowPrivilegeEscalation") is not False or csc.get("privileged"):
                bad(tag, "must set allowPrivilegeEscalation: false and not be privileged")
            if (csc.get("capabilities") or {}).get("drop") != ["ALL"] or (
                csc.get("capabilities") or {}
            ).get("add"):
                bad(tag, "must drop ALL capabilities and add none")
            if (csc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
                bad(tag, "seccompProfile must be RuntimeDefault")
            res = c.get("resources") or {}
            if set(res.get("requests") or {}) != {"cpu", "memory"} or set(
                res.get("limits") or {}
            ) != {"cpu", "memory"}:
                bad(tag, "needs cpu and memory requests and limits")
            if role == "container" and pod_kind == "Deployment":
                for probe in ("readinessProbe", "livenessProbe"):
                    if not c.get(probe):
                        bad(tag, f"has no {probe}")
            for m in c.get("volumeMounts") or []:
                if m.get("name") not in volumes:
                    bad(tag, f"mounts volume {m.get('name')!r}, which the pod does not define")
            image = c.get("image", "")
            m = re.match(rf"^{re.escape(ocp.INTERNAL_REGISTRY)}/([^/]+)/([^:@]+)[:@]", image)
            if m and m.group(2) not in streams:
                bad(tag, f"image {image!r} names an ImageStream that is not in the folder")
            if not image:
                bad(tag, "has no image")
            for e in c.get("env") or []:
                ename, value = e.get("name", ""), e.get("value")
                ref = (e.get("valueFrom") or {}).get("secretKeyRef")
                if ref:
                    secret_refs.add((ref.get("name", ""), ref.get("key", "")))
                    if ref.get("name") not in allowed_secrets:
                        bad(
                            tag,
                            f"env {ename} reads Secret {ref.get('name')!r}, expected one of {sorted(allowed_secrets)}",
                        )
                    if ref.get("key") not in known_keys:
                        bad(tag, f"env {ename} reads unknown Secret key {ref.get('key')!r}")
                cm = (e.get("valueFrom") or {}).get("configMapKeyRef")
                if cm and cm.get("name") not in configmaps:
                    bad(tag, f"env {ename}: ConfigMap {cm.get('name')!r} is not in the folder")
                if value is not None:
                    text = str(value)
                    if SECRET_WORDS.search(ename) and not text.startswith("$("):
                        bad(tag, f"env {ename} holds a value; secrets must come from a Secret")
                    if URL_WITH_PASSWORD.search(text):
                        bad(tag, f"env {ename} has a password in a URL")
        # Deployment selector and Service reachability are checked below.

    for file_name, d in docs:
        kind, obj = d.get("kind"), (d.get("metadata") or {}).get("name")
        if kind == "Deployment":
            sel = (d["spec"].get("selector") or {}).get("matchLabels") or {}
            tlabels = d["spec"]["template"]["metadata"].get("labels") or {}
            if not _subset(sel, tlabels):
                bad(file_name, "spec.selector.matchLabels does not match the pod template's labels")
        if kind == "ConfigMap":
            for key, text in (d.get("data") or {}).items():
                if SQL_PASSWORD_LITERAL.search(str(text)) and key != "schema.sql":
                    bad(file_name, f"{key} seems to hold a literal password")
                if "PRIVATE KEY-----" in str(text):
                    bad(file_name, f"{key} holds a private key")
        if kind == "Service":
            sel = d["spec"].get("selector") or {}
            targets = [(pl, p) for _, _, _, pl, p in pods if _subset(sel, pl)]
            if not targets:
                bad(file_name, "its selector matches no pod in the folder")
            for port in d["spec"].get("ports") or []:
                tp = port.get("targetPort", port.get("port"))
                found = any(
                    (cp.get("name") == tp if isinstance(tp, str) else cp.get("containerPort") == tp)
                    for _, p in targets
                    for c in p.get("containers", [])
                    for cp in c.get("ports") or []
                )
                if targets and not found:
                    bad(file_name, f"targetPort {tp!r} is no container port of the pods it selects")
        if kind == "Route":
            to = (d["spec"].get("to") or {}).get("name")
            if to not in services:
                bad(file_name, f"routes to Service {to!r}, which is not in the folder")
            else:
                tp = (d["spec"].get("port") or {}).get("targetPort")
                if tp not in {p.get("name") for p in services[to]["spec"].get("ports") or []}:
                    bad(file_name, f"targetPort {tp!r} is no port name of Service {to}")
            if (d["spec"].get("tls") or {}).get("termination") != "edge":
                bad(file_name, "needs edge TLS termination")
            if "database" in str(d["spec"]) or "15432" in str(d["spec"]):
                bad(file_name, "the Route must serve the agent's HTTP port only")
        if kind == "NetworkPolicy":
            sel = (d["spec"].get("podSelector") or {}).get("matchLabels") or {}
            if not any(_subset(sel, pl) for _, _, _, pl, _ in pods):
                bad(file_name, "its podSelector matches no pod in the folder")
        if kind == "BuildConfig":
            git = (d["spec"].get("source") or {}).get("git") or {}
            if not git.get("uri") or not git.get("ref"):
                bad(file_name, "source.git needs uri and ref")
            if (d["spec"].get("strategy") or {}).get("type") != "Docker":
                bad(file_name, "strategy must be Docker")
            to = (d["spec"].get("output") or {}).get("to") or {}
            if (
                to.get("kind") != "ImageStreamTag"
                or to.get("name", "").split(":")[0] not in streams
            ):
                bad(
                    file_name, "output must go to an ImageStreamTag of an ImageStream in the folder"
                )
            for key in ("triggers",):
                for t in d["spec"].get(key) or []:
                    if t.get("type") in ("GitHub", "Generic", "Bitbucket", "GitLab"):
                        bad(
                            file_name,
                            "webhook triggers are not allowed (no webhook secrets in files)",
                        )
            ssec = (d["spec"].get("source") or {}).get("sourceSecret")
            if ssec:
                secret_refs.add((ssec.get("name", ""), ""))
        trig = ((d.get("metadata") or {}).get("annotations") or {}).get(ocp.TRIGGER_ANNOTATION)
        if kind == "Deployment" and trig:
            try:
                items = json.loads(trig)
            except ValueError:
                bad(file_name, f"annotation {ocp.TRIGGER_ANNOTATION} is not JSON")
                items = []
            spec = d["spec"]["template"]["spec"]
            for item in items:
                stream = (item.get("from") or {}).get("name", "").split(":")[0]
                if stream not in streams:
                    bad(
                        file_name,
                        f"trigger follows ImageStream {stream!r}, which is not in the folder",
                    )
                m = re.search(
                    r"spec\.template\.spec\.(\w+)\[\?\(@\.name==\"([^\"]+)\"\)\]\.image",
                    item.get("fieldPath", ""),
                )
                if not m or m.group(2) not in {c.get("name") for c in spec.get(m.group(1), [])}:
                    bad(
                        file_name, f"trigger fieldPath {item.get('fieldPath')!r} names no container"
                    )

    # kustomization and README
    kfile = folder / "kustomization.yaml"
    if not kfile.is_file():
        bad(folder.name, "kustomization.yaml is missing")
    else:
        try:
            k = yaml.safe_load(kfile.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            bad("kustomization.yaml", f"cannot be read: {exc}")
            k = {}
        if k.get("resources") != manifests:
            bad("kustomization.yaml", "resources do not list the numbered files, in order")
    readme_file = folder / "README.md"
    if not readme_file.is_file():
        bad(folder.name, "README.md is missing")
    else:
        text = readme_file.read_text(encoding="utf-8")
        for secret_name, key in sorted(secret_refs):
            if secret_name in allowed_secrets and (
                secret_name not in text or (key and f"`{key}`" not in text and key not in text)
            ):
                bad("README.md", f"does not explain Secret {secret_name!r} / key {key!r}")
    return problems


def check_tree(path: Path) -> list[str]:
    """`check_dir` for one app folder, or for every app folder under a folder (deploy/ocp)."""
    path = Path(path)
    if (path / "kustomization.yaml").is_file() or any(
        GENERATED.match(p.name) for p in path.glob("*.yaml")
    ):
        return check_dir(path)
    children = (
        sorted(p for p in path.iterdir() if p.is_dir() and (p / "kustomization.yaml").is_file())
        if path.is_dir()
        else []
    )
    if not children:
        return [
            f"{path}: no rendered app folder here (expected NN-*.yaml files, or folders holding them)"
        ]
    return [f"{p.name}/{line}" for p in children for line in check_dir(p)]
