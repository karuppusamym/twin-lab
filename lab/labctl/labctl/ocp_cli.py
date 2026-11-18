"""`labctl ocp ...`: render an app's lab as OpenShift manifests, deploy it, smoke-check it."""

from __future__ import annotations

from pathlib import Path

import typer

from labctl import dbservers, ocp, ocp_manual, repo
from labctl.bindings import BindingError

app = typer.Typer(no_args_is_help=True, help="Run an app's lab on OpenShift (render, deploy).")

REGISTRY = typer.Option(
    ..., envvar="TWINLAB_REGISTRY", help="Image registry and path, e.g. ghcr.io/OWNER/REPO."
)
TAG = typer.Option(..., envvar="TWINLAB_TAG", help="Image tag (the pipeline uses the commit SHA).")
NAMESPACE = typer.Option("twinlab", envvar="OCP_NAMESPACE", help="OpenShift project.")
POSTGRES = typer.Option(ocp.POSTGRES_IMAGE, help="Postgres image (restricted-SCC friendly).")
PULL_SECRET = typer.Option(None, help="Image pull secret name, for a private registry.")
EXPOSE_DB = typer.Option(
    False, help="Also put the database link on a NodePort, for bots outside the cluster."
)


def _root() -> Path:
    try:
        return repo.find_root()
    except repo.NotARepo as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(1)


@app.command("render")
def render_cmd(
    app_id: str = typer.Argument(None, help="Folder name under apps/ (or use --all)."),
    all_apps: bool = typer.Option(False, "--all", help="Every app that has a `lab:` section."),
    manual: bool = typer.Option(
        False,
        "--manual",
        help="Files to commit and apply by hand: images built in the cluster from a Git branch, "
        "numbered manifests, a kustomization.yaml and a README.md per app. Needs --out.",
    ),
    registry: str = typer.Option(
        None,
        envvar="TWINLAB_REGISTRY",
        help="Image registry and path, e.g. ghcr.io/OWNER/REPO. With --manual: pull prebuilt "
        "images instead of building them in the cluster.",
    ),
    tag: str = typer.Option(
        None,
        envvar="TWINLAB_TAG",
        help="Image tag (the pipeline uses the commit SHA). With --manual and --registry: "
        "default `latest`.",
    ),
    namespace: str = NAMESPACE,
    out: Path = typer.Option(None, help="Folder for the files. Default: stdout (not --manual)."),
    postgres_image: str = POSTGRES,
    pull_secret: str = PULL_SECRET,
    expose_db: bool = EXPOSE_DB,
    git_url: str = typer.Option(
        None, help="--manual: the repository the builds clone. Default: this checkout's origin."
    ),
    git_ref: str = typer.Option(
        None, help="--manual: the branch (or tag) the builds clone. Default: the current branch."
    ),
    source_secret: str = typer.Option(
        None, help="--manual: name of the Secret holding the Git credentials (private repository)."
    ),
    ephemeral: bool = typer.Option(
        False, help="emptyDir instead of PersistentVolumeClaims: data is lost on a pod restart."
    ),
    storage_class: str = typer.Option(None, help="storageClassName of the PVCs."),
    network_policy: bool = typer.Option(
        False, "--network-policy", help="Also render NetworkPolicies (router and the lab only)."
    ),
) -> None:
    """Write the manifests (no secret values in them). Apply with `oc apply -f DIR`."""
    root = _root()
    if all_apps == bool(app_id):
        typer.echo("error: give one app, or --all", err=True)
        raise typer.Exit(2)
    if manual or all_apps:
        if not manual or out is None:
            typer.echo("error: --all needs --manual and --out", err=True)
            raise typer.Exit(2)
        _render_manual(root, app_id, all_apps, out, locals())
        return
    if not registry or not tag:
        typer.echo("error: --registry and --tag are needed (or use --manual)", err=True)
        raise typer.Exit(2)
    try:
        docs = ocp.render(
            root, app_id, registry=registry, tag=tag, namespace=namespace,
            postgres_image=postgres_image, pull_secret=pull_secret, expose_db=expose_db,
            ephemeral=ephemeral, storage_class=storage_class, network_policy=network_policy,
        )  # fmt: skip
    except (ocp.OcpError, BindingError) as exc:
        raise _fail(exc) from exc
    if out is None:
        typer.echo(ocp.to_yaml(docs), nl=False)
        return
    for path in ocp.write(docs, out):
        typer.echo(str(path))
    typer.echo(
        f"{len(docs)} object(s). The Secret {ocp.secret_name(app_id)} is not among them: "
        "`labctl ocp deploy` creates it once, or see docs/ocp.md."
    )


def _render_manual(root: Path, app_id, all_apps: bool, out: Path, o: dict) -> None:
    options = {
        "namespace": o["namespace"], "git_url": o["git_url"], "git_ref": o["git_ref"],
        "source_secret": o["source_secret"], "registry": o["registry"], "tag": o["tag"],
        "postgres_image": o["postgres_image"], "pull_secret": o["pull_secret"],
        "expose_db": o["expose_db"], "ephemeral": o["ephemeral"],
        "storage_class": o["storage_class"], "network_policy": o["network_policy"],
    }  # fmt: skip
    try:
        if all_apps:
            bundles, skipped = ocp_manual.render_all(root, out, **options)
            for b in bundles:
                typer.echo(
                    f"{b.plan.app_id}: {len(b.manifests)} manifest(s) in {out / b.plan.app_id}"
                )
            for name_, why in skipped.items():
                typer.echo(f"skipped {name_}: {why}", err=True)
            typer.echo(f"{len(bundles)} app(s); index at {out / 'README.md'}")
            return
        bundle = ocp_manual.render_manual(root, app_id, **options)
    except (ocp.OcpError, BindingError, dbservers.DbServerError) as exc:
        raise _fail(exc) from exc
    for path in ocp_manual.write_bundle(bundle, out):
        typer.echo(str(path))
    typer.echo(
        f"{len(bundle.manifests)} manifest(s). Next: read {out / 'README.md'}; "
        f"check them with `labctl ocp check {out}`."
    )


@app.command("check")
def check_cmd(
    folder: Path = typer.Argument(
        ..., help="A rendered app folder, or deploy/ocp for all of them."
    ),
) -> None:
    """Check rendered manifests offline: YAML, names, references between objects, no secret
    values, restricted-SCC rules. Exit 1 on any problem."""
    problems = ocp_manual.check_tree(folder)
    for line in problems:
        typer.echo(f"problem: {line}", err=True)
    if problems:
        typer.echo(f"{len(problems)} problem(s) in {folder}", err=True)
        raise typer.Exit(1)
    typer.echo(f"{folder}: ok")


@app.command("deploy")
def deploy_cmd(
    app_id: str = typer.Argument(..., help="Folder name under apps/."),
    registry: str = REGISTRY,
    tag: str = TAG,
    namespace: str = NAMESPACE,
    postgres_image: str = POSTGRES,
    pull_secret: str = PULL_SECRET,
    expose_db: bool = EXPOSE_DB,
    timeout: int = typer.Option(600, help="Seconds to wait for each rollout."),
    smoke: bool = typer.Option(True, help="Check the Route's health at the end."),
    insecure_tls: bool = typer.Option(
        False, help="Do not verify the Route's certificate in the smoke check (self-signed)."
    ),
) -> None:
    """Render, create the Secret if missing, `oc apply`, wait for rollouts, smoke-check the Route.
    Needs `oc` on PATH and a login (`oc login`)."""
    try:
        url = ocp.deploy(
            _root(), app_id, registry=registry, tag=tag, namespace=namespace,
            postgres_image=postgres_image, pull_secret=pull_secret, timeout=timeout,
            smoke_check=smoke, verify_tls=not insecure_tls, expose_db=expose_db, log=typer.echo,
        )  # fmt: skip
    except (ocp.OcpError, BindingError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"deployed {app_id}" + (f": {url}" if url else ""))


@app.command("smoke")
def smoke_cmd(
    app_id: str = typer.Argument(..., help="Folder name under apps/."),
    namespace: str = NAMESPACE,
    wait: int = typer.Option(180, help="Seconds to wait for a healthy answer."),
    insecure_tls: bool = typer.Option(
        False, help="Do not verify the Route's certificate (self-signed)."
    ),
) -> None:
    """Check a deployed lab through its Route: the agent's health and the app's health path."""
    try:
        url = ocp.smoke(
            _root(), app_id, namespace=namespace, verify_tls=not insecure_tls, wait=wait,
            log=typer.echo,
        )  # fmt: skip
    except (ocp.OcpError, BindingError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"{app_id} is healthy at {url}")
