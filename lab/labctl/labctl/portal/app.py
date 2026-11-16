"""Twin Lab portal (T-15): one web page to register automations, add evidence, build demo apps,
run them in the lab, load data, slow them down, and record sessions.

Run with `labctl portal` (default http://127.0.0.1:8790). Set TWINLAB_PORTAL_PASSWORD to require a
password (HTTP Basic, any user name) when the portal is reachable by others.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import zipfile
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from pydantic import ValidationError

from labctl import bindings as bnd
from labctl import conformance as conf
from labctl import factory, repo
from labctl import lab as lab_mod
from labctl.analyse import uipath
from labctl.appmodel import Flow
from labctl.models import App, Automation, Condition
from labctl.portal.jobs import JobRunner

HERE = Path(__file__).parent
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_UPLOAD = 200 * 1024 * 1024
md = MarkdownIt("commonmark", {"html": False}).enable("table")


def create_app(root: Path) -> FastAPI:
    root = root.resolve()
    state = lab_mod.state_dir(root)
    jobs = JobRunner(state / "portal-jobs.db")
    portal = FastAPI(title="Twin Lab", docs_url=None, redoc_url=None)
    portal.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, indent=2, default=str)
    password = os.environ.get("TWINLAB_PORTAL_PASSWORD")

    @portal.middleware("http")
    async def require_password(request: Request, call_next):
        if password and not request.url.path.startswith("/static"):
            header = request.headers.get("authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    _, given = base64.b64decode(header[6:]).decode().split(":", 1)
                    ok = secrets.compare_digest(given, password)
                except (ValueError, UnicodeDecodeError):
                    ok = False
            if not ok:
                return Response(
                    status_code=401, headers={"WWW-Authenticate": 'Basic realm="Twin Lab"'}
                )
        return await call_next(request)

    # --- helpers ---------------------------------------------------------------------------------

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("flash", request.query_params.get("msg"))
        ctx.setdefault("flash_kind", request.query_params.get("kind", "ok"))
        return templates.TemplateResponse(request, name, ctx)

    def back(url: str, msg: str, kind: str = "ok") -> RedirectResponse:
        from urllib.parse import urlencode

        path, _, fragment = url.partition("#")  # the message must come before the #fragment
        sep = "&" if "?" in path else "?"
        query = urlencode({"msg": msg, "kind": kind})
        return RedirectResponse(
            f"{path}{sep}{query}" + (f"#{fragment}" if fragment else ""), status_code=303
        )

    def slug(value: str, what: str) -> str:
        value = value.strip().lower()
        if not SLUG.match(value):
            raise HTTPException(400, f"{what} must be lower-case letters, digits and dashes")
        return value

    def all_automations() -> list[tuple[Path, Automation | None, list]]:
        out = []
        for path in repo.files_of(root, "automation"):
            model, problems = repo.load(path)
            out.append((path, model, problems))
        return out

    def get_automation(automation_id: str) -> Automation:
        path = root / "automations" / slug(automation_id, "automation id") / "automation.yaml"
        model, problems = repo.load(path, "automation")
        if model is None:
            raise HTTPException(
                404, f"automation {automation_id}: " + "; ".join(map(str, problems))
            )
        return model

    def save_automation(model: Automation) -> None:
        path = root / "automations" / model.id / "automation.yaml"
        path.write_text(
            yaml.safe_dump(
                model.model_dump(mode="json", exclude_none=True), sort_keys=False, width=100
            ),
            encoding="utf-8",
        )

    def app_model(app_id: str) -> App | None:
        path = root / "apps" / app_id / "app.yaml"
        model, _ = repo.load(path, "app") if path.exists() else (None, [])
        return model

    def schema_tables(app_id: str):
        from labctl.data import ddl

        path = root / "apps" / app_id / "db" / "schema.sql"
        if not path.exists():
            raise HTTPException(
                409, f"apps/{app_id} has no db/schema.sql yet: generate the brief first"
            )
        return ddl.parse(path.read_text(encoding="utf-8"))

    def conditions() -> list[Condition]:
        out = []
        for path in repo.files_of(root, "condition"):
            model, _ = repo.load(path)
            if model is not None:
                out.append(model)
        return out

    def condition_for(cond: Condition, app_id: str) -> dict | None:
        target = next((t for t in cond.targets if t.app == app_id), None)
        target = target or next((t for t in cond.targets if t.app == "any"), None)
        if target is None:
            return None
        return {
            "id": cond.id,
            "seed": cond.seed,
            "http": [r.model_dump() for r in target.http],
            "db": target.db.model_dump() if target.db else None,
            "faults": [f.model_dump() for f in target.faults],
        }

    def latest_verdicts() -> dict[str, dict]:
        out = {}
        for _path, model, _ in all_automations():
            if model is None:
                continue
            job = jobs.latest("conformance", model.id)
            if job and job["status"] == "done" and job["result"]:
                out[model.id] = job["result"]
        return out

    def devin():
        from labctl.devin import config as devin_config

        return devin_config.load_config(root)

    builds: dict = {}

    def devin_jobs():
        """One build tracker for the portal's lifetime; unfinished builds resume polling."""
        if "jobs" not in builds:
            from labctl.devin import jobs as devin_jobs_mod
            from labctl.devin.client import DevinClient

            def client():
                cfg = devin()  # read each time, so a key added later is picked up
                return DevinClient(cfg.api_key, cfg.org_id, cfg.base_url)

            cfg = devin()
            tracker = devin_jobs_mod.BuildJobs(
                devin_jobs_mod.JobStore(Path(cfg.state_dir)), client, root,
                packages_dir=state / "packages",
                poll_interval=float(os.environ.get("TWINLAB_DEVIN_POLL_SECONDS", "30")),
            )  # fmt: skip
            if cfg.configured:
                tracker.resume_unfinished()
            builds["jobs"] = tracker
        return builds["jobs"]

    # --- overview --------------------------------------------------------------------------------

    @portal.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        apps = []
        verdicts = latest_verdicts()
        for app_id in sorted(
            set(factory.needed_apps(root))
            | {p.parent.name for p in (root / "apps").glob("*/app.yaml")}
        ):
            folder = root / "apps" / app_id
            ev = factory.discover(root, app_id)
            built = (folder / "Dockerfile").exists()
            users = factory.automations(root, app_id)
            apps.append({
                "id": app_id,
                "evidence": sum(len(getattr(ev, k)) for k in ("recordings", "ddl", "openapi", "screenshots")),
                "brief": (folder / "BRIEF.md").exists(),
                "built": built,
                "automations": [a.id for a in users],
                "ready": [a.id for a in users if verdicts.get(a.id, {}).get("verdict") == "READY"],
                "lab": lab_mod.status(root, app_id) if built and app_model(app_id) and app_model(app_id).lab else None,
            })  # fmt: skip
        return page(request, "overview.html", apps=apps, jobs=jobs.list(limit=8))

    # --- automations -----------------------------------------------------------------------------

    @portal.get("/automations", response_class=HTMLResponse)
    def automations_page(request: Request):
        return page(
            request, "automations.html", automations=all_automations(), verdicts=latest_verdicts()
        )

    @portal.post("/automations")
    async def add_automation(
        automation_id: str = Form(...),
        name: str = Form(...),
        kind: str = Form(...),
        surface: str = Form(...),
        project: UploadFile | None = File(None),
        touches: str = Form(""),
        app_id: str = Form(""),
    ):
        automation_id = slug(automation_id, "automation id")
        folder = root / "automations" / automation_id
        if folder.exists():
            return back("/automations", f"{automation_id} already exists", "error")
        data = {"id": automation_id, "name": name.strip(), "type": kind, "surface": surface,
                "source": {"path": "./project"}}  # fmt: skip
        if kind != "uipath":
            try:
                data["touches"] = yaml.safe_load(touches) or [] if touches.strip() else []
                data["analysis"] = {"by": "manual"}
            except yaml.YAMLError as exc:
                return back("/automations", f"touches are not valid YAML: {exc}", "error")
        try:
            model = Automation.model_validate(data)
        except ValidationError as exc:
            return back("/automations", _problems(exc), "error")
        folder.mkdir(parents=True)
        (folder / "project").mkdir()
        if project is not None and project.filename:
            try:
                _unzip(await project.read(), folder / "project")
            except ValueError as exc:
                shutil.rmtree(folder)
                return back("/automations", str(exc), "error")
        save_automation(model)
        if kind == "uipath":
            if app_id.strip():
                try:
                    model.bindings = uipath.suggest_bindings(
                        folder / "project", slug(app_id, "app id")
                    )
                except uipath.AnalysisError as exc:
                    return back(
                        f"/automations/{automation_id}", f"Analysis refused: {exc}", "error"
                    )
                save_automation(model)
            return _analyse(automation_id)
        return back(f"/automations/{automation_id}", "Automation registered")

    def _analyse(automation_id: str):
        model = get_automation(automation_id)
        project = root / "automations" / automation_id / (model.source.path or "project")
        try:
            result = uipath.analyse(project, model.source.entry, model.bindings)
        except uipath.AnalysisError as exc:
            return back(f"/automations/{automation_id}", f"Analysis refused: {exc}", "error")
        data = model.model_dump(mode="json", exclude_none=True)
        data["touches"] = result.touches
        data["analysis"] = {
            "by": uipath.VERSION,
            "gaps": [g.model_dump() for g in result.gaps],
            "notes": [n.model_dump() for n in result.notes],
            "reviewed_by": None,
        }
        try:
            save_automation(Automation.model_validate(data))
        except ValidationError as exc:
            return back(f"/automations/{automation_id}", _problems(exc), "error")
        return back(f"/automations/{automation_id}",
                    f"Analysed {', '.join(result.files)}: {len(result.gaps)} gap(s), {len(result.notes)} note(s)")  # fmt: skip

    @portal.post("/automations/{automation_id}/analyse")
    def analyse(automation_id: str):
        return _analyse(automation_id)

    @portal.get("/automations/{automation_id}", response_class=HTMLResponse)
    def automation_page(request: Request, automation_id: str):
        model = get_automation(automation_id)
        try:
            b = bnd.for_automation(root, model)
            args, binding_error = bnd.arguments(model, b), None
        except bnd.BindingError as exc:
            args, binding_error = {}, str(exc)
        return page(
            request,
            "automation.html",
            a=model,
            args=args,
            binding_error=binding_error,
            settings_yaml=yaml.safe_dump({"bindings": model.bindings, "test_inputs": model.test_inputs}, sort_keys=False),
            conformance=jobs.list("conformance", model.id, 5),
        )  # fmt: skip

    @portal.post("/automations/{automation_id}/settings")
    def automation_settings(automation_id: str, settings: str = Form(...)):
        model = get_automation(automation_id)
        try:
            data = yaml.safe_load(settings) or {}
            merged = model.model_dump(mode="json", exclude_none=True) | {
                "bindings": data.get("bindings") or {},
                "test_inputs": {k: str(v) for k, v in (data.get("test_inputs") or {}).items()},
            }
            save_automation(Automation.model_validate(merged))
        except (yaml.YAMLError, ValidationError, AttributeError) as exc:
            return back(f"/automations/{automation_id}", f"Not saved: {exc}", "error")
        return back(f"/automations/{automation_id}", "Saved")

    @portal.post("/automations/{automation_id}/review")
    def automation_review(automation_id: str, reviewer: str = Form(...)):
        model = get_automation(automation_id)
        model.analysis.reviewed_by = reviewer.strip() or None
        save_automation(model)
        return back(f"/automations/{automation_id}", "Review recorded")

    @portal.get("/automations/{automation_id}/bindings")
    def automation_bindings(automation_id: str, form: str = "json"):
        model = get_automation(automation_id)
        try:
            text = bnd.render(model, bnd.for_automation(root, model), form)
        except bnd.BindingError as exc:
            raise HTTPException(409, str(exc)) from exc
        ext = {"json": "json", "env": "env", "uipath": "json", "yaml": "yaml"}[form]
        name = f"{automation_id}-{'uipath-arguments' if form == 'uipath' else 'bindings'}.{ext}"
        return Response(
            text,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @portal.post("/automations/{automation_id}/conformance")
    def run_conformance(automation_id: str):
        model = get_automation(automation_id)

        def job(log):
            report = conf.check(root, model)
            print(conf.render(report))
            return report.to_dict()

        job_id = jobs.submit("conformance", f"Conformance: {automation_id}", job, automation_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # --- flows -----------------------------------------------------------------------------------

    @portal.get("/flows", response_class=HTMLResponse)
    def flows_page(request: Request):
        items = []
        for path in repo.files_of(root, "flow"):
            model, problems = repo.load(path)
            items.append((path, model, problems))
        example = (
            (root / "specs" / "flow.example.yaml").read_text(encoding="utf-8")
            if (root / "specs" / "flow.example.yaml").exists()
            else ""
        )
        return page(request, "flows.html", flows=items, example=example)  # fmt: skip

    @portal.post("/flows")
    def save_flow(text: str = Form(...)):
        try:
            model = Flow.model_validate(yaml.safe_load(text))
        except (yaml.YAMLError, ValidationError, TypeError) as exc:
            return back(
                "/flows",
                f"Not saved: {_problems(exc) if isinstance(exc, ValidationError) else exc}",
                "error",
            )
        path = root / "flows" / f"{model.id}.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return back("/flows", f"Flow {model.id} saved")

    # --- apps ------------------------------------------------------------------------------------

    @portal.get("/apps", response_class=HTMLResponse)
    def apps_page(request: Request):
        return RedirectResponse("/", status_code=303)

    @portal.get("/apps/{app_id}", response_class=HTMLResponse)
    def app_page(request: Request, app_id: str):
        app_id = slug(app_id, "app id")
        folder = root / "apps" / app_id
        model = app_model(app_id)
        ev = factory.discover(root, app_id)
        status = (
            lab_mod.status(root, app_id)
            if model and model.lab and (folder / "Dockerfile").exists()
            else None
        )
        lab_info = sessions = current_condition = None
        if status and status.running:
            try:
                agent = lab_mod.agent(root, app_id)
                lab_info, sessions, current_condition = (
                    agent.info(),
                    agent.sessions(),
                    agent.condition(),
                )
            except lab_mod.LabError as exc:
                lab_info = {"error": str(exc)}
        try:
            cfg = devin()
            devin_ok, devin_list = (
                cfg.configured,
                [j.to_dict() for j in devin_jobs().list(app_id)],
            )
        except Exception as exc:  # noqa: BLE001 - the page must render even if Devin config is broken
            cfg, devin_ok, devin_list = None, False, []
            lab_info = lab_info or {}
            lab_info["devin_error"] = str(exc)
        return page(
            request,
            "app.html",
            app_id=app_id,
            app=model,
            evidence=ev,
            has_brief=(folder / "BRIEF.md").exists(),
            built=(folder / "Dockerfile").exists(),
            needed_by=[a.id for a in factory.automations(root, app_id)],
            flows=[f.id for f in factory.flows(root, app_id)],
            status=status,
            lab_info=lab_info,
            sessions=sessions,
            condition=current_condition,
            conditions=[c for c in conditions() if condition_for(c, app_id)],
            automations=factory.automations(root, app_id),
            devin_ok=devin_ok,
            devin_jobs=devin_list,
            repo_name=(cfg.github_repo if cfg else None),
            jobs=jobs.list(subject=app_id, limit=10),
        )

    @portal.post("/apps/{app_id}/evidence")
    async def add_evidence(
        app_id: str, kind: str = Form(...), file: UploadFile = File(...), name: str = Form("")
    ):
        app_id = slug(app_id, "app id")
        base = factory.evidence_dir(root, app_id)
        base.mkdir(parents=True, exist_ok=True)
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            return back(f"/apps/{app_id}#evidence", "File too large (200 MB at most)", "error")
        filename = Path(file.filename or "upload").name
        stem = slug(
            name or Path(filename).stem.replace("_", "-").replace(".", "-")[:40] or "upload", "name"
        )
        try:
            if kind == "recording":
                target = base / stem
                if target.exists():
                    return back(f"/apps/{app_id}#evidence", f"{stem} already exists", "error")
                if filename.lower().endswith(".har"):
                    target.mkdir()
                    (target / "recording.har").write_bytes(data)
                else:
                    _unzip(data, target)
                    if not list(target.rglob("*.har")):
                        shutil.rmtree(target)
                        return back(f"/apps/{app_id}#evidence", "The zip has no .har file", "error")
            elif kind == "ddl":
                (base / f"{stem}.sql").write_bytes(data)
            elif kind == "openapi":
                ext = ".json" if filename.lower().endswith(".json") else ".yaml"
                prefix = (
                    ""
                    if stem.startswith(("openapi", "swagger"))
                    or filename.endswith(".postman_collection.json")
                    else "openapi-"
                )
                target_name = (
                    filename
                    if filename.endswith(".postman_collection.json")
                    else f"{prefix}{stem}{ext}"
                )
                (base / target_name).write_bytes(data)
            elif kind == "screenshot":
                shots = base / "screenshots"
                shots.mkdir(exist_ok=True)
                if not filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")
                ):
                    return back(
                        f"/apps/{app_id}#evidence", "Screenshots must be images or PDF", "error"
                    )
                (shots / filename).write_bytes(data)
            else:
                return back(f"/apps/{app_id}#evidence", f"Unknown evidence kind {kind}", "error")
        except ValueError as exc:
            return back(f"/apps/{app_id}#evidence", str(exc), "error")
        return back(f"/apps/{app_id}#evidence", f"Added {kind}: {filename}")  # fmt: skip

    @portal.post("/apps/{app_id}/evidence/delete")
    def delete_evidence(app_id: str, path: str = Form(...)):
        app_id = slug(app_id, "app id")
        base = factory.evidence_dir(root, app_id).resolve()
        target = (base / path).resolve()
        if base not in target.parents:
            raise HTTPException(400, "not an evidence file")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        return back(f"/apps/{app_id}#evidence", f"Removed {path}")

    @portal.post("/apps/{app_id}/record")
    def record(app_id: str, url: str = Form(...), name: str = Form(...)):
        app_id = slug(app_id, "app id")
        target = factory.evidence_dir(root, app_id) / slug(name, "recording name")
        if target.exists():
            return back(f"/apps/{app_id}#evidence", f"{target.name} already exists", "error")

        def job(log):
            from labctl import recorder

            print(
                f"A browser window opens on this machine at {url}. Go through the flow, then close the window."
            )
            recorder.record(url, target, headless=False)
            print(f"Saved to evidence/{app_id}/{target.name}")
            return {"recording": target.name}

        job_id = jobs.submit("record", f"Record a flow of {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.post("/apps/{app_id}/brief")
    def make_brief(
        app_id: str, system: str = Form(""), source: str = Form(""), db_engine: str = Form("")
    ):
        app_id = slug(app_id, "app id")

        def job(log):
            src_path = Path(source.strip()) if source.strip() else None
            if src_path is not None and not src_path.is_absolute():
                src_path = root / src_path
            if src_path is not None and not src_path.is_dir():
                raise FileNotFoundError(f"source folder not found: {src_path}")
            result = factory.generate(
                root, app_id, system.strip() or None, src_path, db_engine or None, force=True
            )
            b = result.brief
            print(
                f"{app_id}: {len(b.screens)} screen(s), {len(b.routes)} route(s), {len(b.tables)} table(s) in scope"
            )
            for gap in b.gaps:
                print(f"  OPEN  {gap}")
            print("wrote " + ", ".join(str(p.relative_to(root)) for p in result.files))
            if result.kept_app_yaml:
                print("kept the existing app.yaml")
            return {
                "screens": len(b.screens),
                "routes": len(b.routes),
                "tables": len(b.tables),
                "open": len(b.gaps),
            }

        job_id = jobs.submit("brief", f"Brief for {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.get("/apps/{app_id}/brief", response_class=HTMLResponse)
    def show_brief(request: Request, app_id: str):
        path = root / "apps" / slug(app_id, "app id") / "BRIEF.md"
        if not path.exists():
            raise HTTPException(404, "no brief yet")
        return page(
            request, "brief.html", app_id=app_id, html=md.render(path.read_text(encoding="utf-8"))
        )

    @portal.post("/apps/{app_id}/lab-settings")
    def lab_settings(app_id: str, port: int = Form(...), db_port: int = Form(...),
                     username: str = Form(""), password: str = Form("")):  # fmt: skip
        app_id = slug(app_id, "app id")
        path = root / "apps" / app_id / "app.yaml"
        if not path.exists():
            return back(f"/apps/{app_id}", "No app.yaml yet: generate the brief first", "error")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        creds = {k: v for k, v in {"username": username.strip(), "password": password}.items() if v}
        data["lab"] = {"port": port, "db_port": db_port, "credentials": creds}
        try:
            App.model_validate(data)
        except ValidationError as exc:
            return back(f"/apps/{app_id}", _problems(exc), "error")
        path.write_text(yaml.safe_dump(data, sort_keys=False, width=100), encoding="utf-8")
        return back(f"/apps/{app_id}#lab", "Lab settings saved")

    # build: Devin

    @portal.get("/apps/{app_id}/package")
    def package(app_id: str):
        from labctl.devin import package as devin_package

        app_id = slug(app_id, "app id")
        cfg = devin()
        try:
            path = devin_package.build(root, app_id, state / "packages", cfg.github_repo)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Package not built: {exc}", "error")
        try:
            devin_jobs().record_manual(app_id, path)
        except Exception:  # noqa: BLE001 - tracking is a convenience; the download must still work
            pass
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @portal.post("/apps/{app_id}/devin/start")
    def devin_start(app_id: str, max_acu: str = Form("")):
        app_id = slug(app_id, "app id")
        cfg = devin()
        if not cfg.configured:
            return back(f"/apps/{app_id}#build", "Devin is not configured: see Settings", "error")
        if not cfg.github_repo:
            return back(f"/apps/{app_id}#build", "GITHUB_REPO is not set: see Settings", "error")
        try:
            devin_jobs().start(app_id, cfg.github_repo, int(max_acu) if max_acu.strip() else None)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Devin build not started: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Devin build started; this page shows its progress")

    @portal.post("/apps/{app_id}/devin/{job_id}/message")
    def devin_message(app_id: str, job_id: str, text: str = Form(...)):
        try:
            devin_jobs().message(job_id, text)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not sent: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Message sent to Devin")

    @portal.post("/apps/{app_id}/devin/{job_id}/cancel")
    def devin_cancel(app_id: str, job_id: str):
        try:
            devin_jobs().cancel(job_id)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not cancelled: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Devin session stopped")

    @portal.post("/apps/{app_id}/devin/{job_id}/pr")
    def devin_pr(app_id: str, job_id: str, pr_url: str = Form(...)):
        if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$", pr_url.strip()):
            return back(f"/apps/{app_id}#build", "That is not a GitHub pull request URL", "error")
        devin_jobs().attach_pr(job_id, pr_url.strip())
        return back(f"/apps/{app_id}#build", "Pull request recorded")

    # lab

    @portal.post("/apps/{app_id}/lab/{action}")
    def lab_action(app_id: str, action: str):
        app_id = slug(app_id, "app id")
        if action not in ("up", "down", "rebuild"):
            raise HTTPException(404)

        def job(log):
            if action == "down":
                lab_mod.down(root, app_id)
            else:
                print(
                    "Building images and starting the database, lab agent and app. First time takes a few minutes."
                )
                lab_mod.up(root, app_id, build=True)
            state_now = lab_mod.status(root, app_id)
            print(f"{app_id}: {'running' if state_now.running else 'stopped'} {state_now.services}")
            return {"running": state_now.running, "services": state_now.services}

        title = {"up": "Start", "down": "Stop", "rebuild": "Rebuild and start"}[action]
        job_id = jobs.submit("lab", f"{title} {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # data

    @portal.get("/apps/{app_id}/data/template")
    def data_template(app_id: str, current: bool = False):
        from labctl.data import workbook

        app_id = slug(app_id, "app id")
        rows = None
        if current:
            try:
                rows = lab_mod.agent(root, app_id).data()
            except lab_mod.LabError as exc:
                return back(f"/apps/{app_id}#data", f"Current data not available: {exc}", "error")
        content = workbook.template(schema_tables(app_id), rows)
        name = f"{app_id}-{'current-data' if current else 'template'}.xlsx"
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip

    @portal.post("/apps/{app_id}/data/upload")
    async def data_upload(
        request: Request, app_id: str, file: UploadFile = File(...), label: str = Form("")
    ):
        from labctl.data import workbook

        app_id = slug(app_id, "app id")
        agent = lab_mod.agent(root, app_id)
        try:
            existing = agent.data()
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#data", f"The app is not running: {exc}", "error")
        rows, problems = workbook.read(await file.read(), schema_tables(app_id), existing)
        if problems:
            return page(request, "problems.html", app_id=app_id, title=f"{file.filename}: nothing was loaded",
                        problems=[str(p) for p in problems])  # fmt: skip
        if not rows:
            return back(f"/apps/{app_id}#data", "The workbook has no data rows", "error")
        try:
            reply = agent.load(rows, label.strip() or Path(file.filename or "workbook").stem)
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#data", f"Not loaded: {exc}", "error")
        loaded = ", ".join(f"{t} {len(r)}" for t, r in rows.items())
        cleared = reply.get("cleared") or []
        extra = f"; also cleared {', '.join(cleared)}" if cleared else ""
        return back(f"/apps/{app_id}#data", f"Loaded {loaded}{extra}. This is now the baseline.")

    @portal.post("/apps/{app_id}/data/generate")
    def data_generate(
        app_id: str, seed: int = Form(...), counts: str = Form(...), label: str = Form("")
    ):
        from labctl.data import synthetic

        app_id = slug(app_id, "app id")
        wanted = {}
        for part in re.split(r"[,\n]", counts):
            if part.strip():
                table, _, n = part.partition("=")
                if not n.strip().isdigit():
                    return back(
                        f"/apps/{app_id}#data", f"'{part.strip()}' is not table=count", "error"
                    )
                wanted[table.strip()] = int(n)
        tables = schema_tables(app_id)
        unknown = set(wanted) - {t.name for t in tables}
        if unknown:
            return back(
                f"/apps/{app_id}#data", f"Unknown table(s): {', '.join(sorted(unknown))}", "error"
            )
        agent = lab_mod.agent(root, app_id)
        try:
            samples = agent.data()
            rows = synthetic.generate(tables, seed, wanted, samples)
            agent.load(rows, label.strip() or f"synthetic seed {seed}")
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#data", f"Not loaded: {exc}", "error")
        return back(
            f"/apps/{app_id}#data",
            "Generated and loaded " + ", ".join(f"{t} {len(r)}" for t, r in rows.items()),
        )

    @portal.post("/apps/{app_id}/data/{action}")
    def data_action(app_id: str, action: str):
        app_id = slug(app_id, "app id")
        agent = lab_mod.agent(root, app_id)
        try:
            if action == "reset":
                reply = agent.reset()
                return back(f"/apps/{app_id}#data", "Reset to the baseline: " + _counts(reply))
            if action == "baseline":
                agent.baseline()
                return back(f"/apps/{app_id}#data", "The current data is now the baseline")
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#data", str(exc), "error")
        raise HTTPException(404)

    # conditions

    @portal.post("/apps/{app_id}/condition")
    def set_condition(app_id: str, condition_id: str = Form("")):
        app_id = slug(app_id, "app id")
        agent = lab_mod.agent(root, app_id)
        try:
            if not condition_id:
                agent.set_condition(None)
                return back(
                    f"/apps/{app_id}#conditions", "Normal conditions: no added delay or faults"
                )
            cond = next((c for c in conditions() if c.id == condition_id), None)
            body = condition_for(cond, app_id) if cond else None
            if body is None:
                return back(
                    f"/apps/{app_id}#conditions",
                    f"{condition_id} has nothing for {app_id}",
                    "error",
                )
            agent.set_condition(body)
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#conditions", str(exc), "error")
        return back(f"/apps/{app_id}#conditions", f"Condition {condition_id} applied")

    # sessions

    @portal.post("/apps/{app_id}/session/start")
    def session_start(
        app_id: str, automation: str = Form(""), label: str = Form(""), reset: str = Form("")
    ):
        app_id = slug(app_id, "app id")
        agent = lab_mod.agent(root, app_id)
        try:
            if reset:
                agent.reset()
            agent.start_session(automation or None, label.strip() or None)
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#sessions", str(exc), "error")
        return back(
            f"/apps/{app_id}#sessions", "Recording. Run the automation now, then press Stop."
        )

    @portal.post("/apps/{app_id}/session/stop")
    def session_stop(app_id: str):
        app_id = slug(app_id, "app id")
        try:
            session = lab_mod.agent(root, app_id).stop_session()
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#sessions", str(exc), "error")
        return RedirectResponse(f"/apps/{app_id}/sessions/{session['session_id']}", status_code=303)

    @portal.get("/apps/{app_id}/sessions/{session_id}", response_class=HTMLResponse)
    def session_page(request: Request, app_id: str, session_id: str):
        try:
            session = lab_mod.agent(root, slug(app_id, "app id")).session(session_id)
        except lab_mod.LabError as exc:
            raise HTTPException(404, str(exc)) from exc
        others = [s for s in _session_list(app_id) if s.get("session_id") != session_id]
        return page(request, "session.html", app_id=app_id, s=session, others=others)

    @portal.get("/apps/{app_id}/compare", response_class=HTMLResponse)
    def compare(request: Request, app_id: str, a: str, b: str):
        agent = lab_mod.agent(root, slug(app_id, "app id"))
        try:
            left, right = agent.session(a), agent.session(b)
        except lab_mod.LabError as exc:
            raise HTTPException(404, str(exc)) from exc
        return page(
            request, "compare.html", app_id=app_id, a=left, b=right, diff=_session_diff(left, right)
        )

    def _session_list(app_id: str) -> list[dict]:
        try:
            listed = lab_mod.agent(root, app_id).sessions()
        except lab_mod.LabError:
            return []
        return listed if isinstance(listed, list) else listed.get("sessions", [])

    # --- mapping, jobs, settings -----------------------------------------------------------------

    @portal.get("/mapping", response_class=HTMLResponse)
    def mapping(request: Request):
        autos = [m for _, m, _ in all_automations() if m is not None]
        app_ids = sorted({app for a in autos for app in a.apps()})
        return page(
            request, "mapping.html", automations=autos, apps=app_ids, verdicts=latest_verdicts()
        )

    @portal.post("/mapping/run")
    def mapping_run():
        for _, model, _ in all_automations():
            if model is not None and model.touches:
                m = model

                def job(log, m=m):
                    report = conf.check(root, m)
                    print(conf.render(report))
                    return report.to_dict()

                jobs.submit("conformance", f"Conformance: {m.id}", job, m.id)
        return back("/mapping", "Conformance runs started; refresh in a minute")

    @portal.get("/jobs/{job_id}.json")
    def job_json(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return JSONResponse(job)

    @portal.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return page(request, "job.html", job=job)

    @portal.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        try:
            cfg, error = devin(), None
        except Exception as exc:  # noqa: BLE001
            cfg, error = None, str(exc)
        git = _git(["status", "--short", "--branch"])
        return page(request, "settings.html", cfg=cfg, cfg_error=error, git=git,
                    checks=jobs.list("devin-check", limit=3), updates=jobs.list("git", limit=3))  # fmt: skip

    @portal.post("/settings/devin-check")
    def devin_check():
        def job(log):
            from labctl.devin.client import DevinClient

            cfg = devin()
            if not cfg.configured:
                raise RuntimeError(
                    "DEVIN_API_KEY and DEVIN_ORG_ID are not set (twinlab.local.yaml or environment)"
                )
            client = DevinClient(cfg.api_key, cfg.org_id, cfg.base_url)
            print(f"Calling {cfg.base_url} for organization {cfg.org_id} ...")
            result = client.list_sessions(first=1)
            print("Devin answered: the key and organization work.")
            return {"ok": True, "detail": str(result)[:200]}

        job_id = jobs.submit("devin-check", "Check the Devin connection", job, "settings")
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.post("/settings/git-pull")
    def git_pull():
        def job(log):
            out = _git(["pull", "--ff-only"])
            print(out)
            if "fatal" in out.lower() or "error" in out.lower():
                raise RuntimeError("git pull did not complete; see the log")
            return {"ok": True}

        job_id = jobs.submit("git", "Get the latest from GitHub", job, "settings")
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    def _git(args: list[str]) -> str:
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=120, check=False
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return f"git failed: {exc}"
        return (out.stdout + out.stderr).strip()

    @portal.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.endswith(".json") or "application/json" in request.headers.get(
            "accept", ""
        ):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"code": exc.status_code, "detail": exc.detail},
                                          status_code=exc.status_code)  # fmt: skip

    portal.state.jobs = jobs
    portal.state.root = root
    return portal


def _counts(reply) -> str:
    if isinstance(reply, dict):
        counts = reply.get("counts", reply)
        if isinstance(counts, dict):
            return ", ".join(f"{k} {v}" for k, v in counts.items() if isinstance(v, int)) or "done"
    return "done"


def _problems(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '-'}: {e['msg']}" for e in exc.errors()
    )[:600]


def _unzip(data: bytes, target: Path) -> None:
    """Extract a zip safely: no paths outside the target, no huge archives."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a zip archive") from exc
    total = sum(i.file_size for i in archive.infolist())
    if total > 1024 * 1024 * 1024:
        raise ValueError("The archive unpacks to more than 1 GB")
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    names = [i for i in archive.infolist() if not i.is_dir()]
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]:
            raise ValueError(f"Unsafe path in archive: {info.filename}")
    # a zip of a folder: drop the single top folder so files land directly in the target
    tops = {Path(i.filename).parts[0] for i in names if Path(i.filename).parts}
    strip = len(tops) == 1 and all(len(Path(i.filename).parts) > 1 for i in names)
    for info in names:
        parts = Path(info.filename.replace("\\", "/")).parts[1 if strip else 0 :]
        if not parts:
            continue
        dest = (base / Path(*parts)).resolve()
        if base not in dest.parents:
            raise ValueError(f"Unsafe path in archive: {info.filename}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def _session_diff(a: dict, b: dict) -> dict:
    def summary(s):
        return s.get("summary") or {}

    def changes(s):
        return {(c["app"], c["table"], c["op"], c["key"]) for c in s.get("changes", [])}

    return {
        "summary": {k: (summary(a).get(k), summary(b).get(k)) for k in ("requests", "wall_s", "waiting_on_app_s", "errors")},
        "only_a": sorted(changes(a) - changes(b)),
        "only_b": sorted(changes(b) - changes(a)),
        "both": len(changes(a) & changes(b)),
    }  # fmt: skip
