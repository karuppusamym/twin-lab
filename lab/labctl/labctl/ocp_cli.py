"""`labctl ocp ...`: render an app's lab as OpenShift manifests, deploy it, smoke-check it."""

from __future__ import annotations

from pathlib import Path

import typer

from labctl import ocp, repo
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
    app_id: str = typer.Argument(..., help="Folder name under apps/."),
    registry: str = REGISTRY,
    tag: str = TAG,
    namespace: str = NAMESPACE,
    out: Path = typer.Option(None, help="Folder for one YAML file per object. Default: stdout."),
    postgres_image: str = POSTGRES,
    pull_secret: str = PULL_SECRET,
    expose_db: bool = EXPOSE_DB,
) -> None:
    """Write the manifests (no secret values in them). Apply with `oc apply -f DIR`."""
    try:
        docs = ocp.render(
            _root(), app_id, registry=registry, tag=tag, namespace=namespace,
            postgres_image=postgres_image, pull_secret=pull_secret, expose_db=expose_db,
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
