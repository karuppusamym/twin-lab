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
from labctl import copies as copies_mod
from labctl import factory, repo
from labctl import lab as lab_mod
from labctl import outcome as outcome_mod
from labctl import packs as packs_mod
from labctl import verdict as verdict_mod
from labctl.analyse import run as analysis
from labctl.analyse import uipath
from labctl.appmodel import Flow
from labctl.botlog import runs as botruns
from labctl.models import App, Automation, Condition
from labctl.portal import guide
from labctl.portal.jobs import JobRunner
from labctl.portal.labreads import AGENT_TIMEOUT_S, LabReads, Reading

HERE = Path(__file__).parent
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_UPLOAD = 200 * 1024 * 1024
md = MarkdownIt("commonmark", {"html": False}).enable("table")


def _host_name(host: str) -> str:
    """`127.0.0.1:8790` -> `127.0.0.1`, `[::1]:8790` -> `::1`, lower case."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def allowed_hosts() -> set[str]:
    """Names the portal answers to without a password: this machine's own, plus any listed in
    TWINLAB_PORTAL_HOSTS (comma separated) and TWINLAB_HOST."""
    import socket

    names = {"127.0.0.1", "localhost", "::1", "testserver"}
    try:
        name = socket.gethostname().lower()
        names |= {name, socket.getfqdn().lower()}
    except OSError:
        pass
    for var in ("TWINLAB_PORTAL_HOSTS", "TWINLAB_HOST"):
        names |= {n.strip().lower() for n in os.environ.get(var, "").split(",") if n.strip()}
    return names


ME_COOKIE = "tl_me"


def _me(request: Request) -> str | None:
    """The name this browser gave, if any (letters, digits, spaces, . - ' only)."""
    from urllib.parse import unquote

    raw = unquote(request.cookies.get(ME_COOKIE, ""))
    name = " ".join(raw.split())[:60]
    return name if name and re.fullmatch(r"[\w .'-]+", name) else None


def create_app(root: Path) -> FastAPI:
    root = root.resolve()
    state = lab_mod.state_dir(root)
    jobs = JobRunner(state / "portal-jobs.db")
    portal = FastAPI(title="Twin Lab", docs_url=None, redoc_url=None)
    portal.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["tojson_pretty"] = lambda v: json.dumps(v, indent=2, default=str)
    password = os.environ.get("TWINLAB_PORTAL_PASSWORD")
    hosts = allowed_hosts()
    reads = LabReads()  # what the pages know about each lab, for a few seconds

    def forget_reads(kind: str, subject: str | None) -> None:
        """A job that may have changed a lab (start, stop, copies, loads) has ended."""
        reads.invalidate(subject)
        if kind == "lab" and subject:  # the image may be new: one Docker call, in the job's thread
            image = conf.image_id(subject)
            if image:
                reads.images[subject] = image

    jobs.on_done = forget_reads

    @portal.middleware("http")
    async def forget_after_actions(request: Request, call_next):
        """Anything this portal posts about an app (load, reset, reserve, condition, session,
        database choice) may change what its lab says: the next page reads it afresh."""
        response = await call_next(request)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            parts = request.url.path.split("/")
            if len(parts) > 2 and parts[1] == "apps":
                reads.invalidate(parts[2])
            elif parts[1:2] == ["settings"]:  # git pull, database servers: files may differ
                reads.invalidate()
        return response

    @portal.middleware("http")
    async def known_host_only(request: Request, call_next):
        """Without a password, answer only to this machine's own names. A page on another site
        could otherwise point its own domain at 127.0.0.1 (DNS rebinding) and drive the portal.
        With a password the browser never sends it to that domain, so any name is fine."""
        if not password:
            name = _host_name(request.headers.get("host", ""))
            if name not in hosts:
                return Response(
                    f"Refused: unknown host name '{name}'. Add it to TWINLAB_PORTAL_HOSTS.",
                    status_code=421,
                )
        return await call_next(request)

    @portal.middleware("http")
    async def same_site_only(request: Request, call_next):
        """A form posted from another site (a page the user happens to have open) is refused."""
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            from urllib.parse import urlsplit as _split

            source = request.headers.get("origin") or request.headers.get("referer")
            if source and source != "null" and _split(source).netloc != request.headers.get("host"):
                return Response("Refused: this request came from another site.", status_code=403)
        return await call_next(request)

    @portal.middleware("http")
    async def who_is_this(request: Request, call_next):
        """The name a person gave (cookie) goes with every lab admin call they cause, so a lab
        they reserved accepts their changes and refuses others'."""
        name = _me(request)
        token = lab_mod.current_holder.set(name)
        try:
            return await call_next(request)
        finally:
            lab_mod.current_holder.reset(token)

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
        ctx.setdefault("me", _me(request))
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
            newline="\n",
        )

    def app_model(app_id: str) -> App | None:
        path = root / "apps" / app_id / "app.yaml"
        model, _ = repo.load(path, "app") if path.exists() else (None, [])
        return model

    NA_TEXT = (
        "Not applicable: this app keeps its data in the browser, so it has no database to load, "
        "reset, baseline or save as a pack."
    )

    def browser_only(app_id: str) -> bool:
        model = app_model(app_id)
        return bool(model and model.browser_only)

    def not_applicable(app_id: str):
        """A refusal for a data control on an app whose data is in the browser (R07)."""
        if browser_only(app_id):
            return back(f"/apps/{app_id}#data", NA_TEXT, "error")
        return None

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
    builds_lock = __import__("threading").Lock()

    def devin_jobs():
        """One build tracker for the portal's lifetime; unfinished builds resume polling."""
        with builds_lock:
            return _devin_jobs()

    def own_job(app_id: str, job_id: str):
        job = devin_jobs().get(job_id)
        if job is None or job.app_id != app_id:
            raise ValueError(f"no build {job_id} for {app_id}")
        return job

    def _devin_jobs():
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
                fingerprint=lambda: (lambda c: (c.api_key, c.org_id, c.base_url))(devin()),
            )  # fmt: skip
            if cfg.configured:
                tracker.resume_unfinished()
            builds["jobs"] = tracker
        return builds["jobs"]

    # --- overview --------------------------------------------------------------------------------

    def load_lab(app_id: str) -> Reading:
        """One lab agent's metadata and sessions, with a short timeout of its own."""
        agent = lab_mod.agent(root, app_id)
        if getattr(agent, "http", None) is not None:
            agent.http.timeout = AGENT_TIMEOUT_S
        info, sessions = agent.info(), agent.sessions()
        listed = sessions if isinstance(sessions, list) else sessions.get("sessions", [])
        return Reading(True, info or {}, listed or [])

    def run_progress(app_id, status):
        """Use recorded facts, never configuration, as evidence that a run happened.
        Read through the short-lived cache: a lab that is slow or dead reads as nothing."""
        if not status or not status.running:
            return {}, []
        reading = reads.get(app_id, load_lab)
        return (reading.info, reading.sessions) if reading.ok else ({}, [])

    def lab_unreachable(app_id, status) -> bool:
        """A running lab whose agent could not be read just now (failed or timed out)."""
        return bool(status and status.running and not reads.get(app_id, load_lab).ok)

    def freshness_for(
        models: list[Automation], known: dict | None = None
    ) -> dict[str, conf.Freshness]:
        """For each automation: is its latest saved readiness result still true? `known` maps an
        app id to a baseline already read this request. Reads only the labs of results that
        recorded a baseline; a lab that cannot be read leaves the answer 'unconfirmed'."""
        results = latest_verdicts()
        known = dict(known or {})
        wanted = sorted({
            app_id
            for m in models
            for app_id, then in ((results.get(m.id) or {}).get("fingerprint") or {}).get("apps", {}).items()
            if then.get("baseline_read") and then.get("lab", app_id) == app_id and app_id not in known
        })  # fmt: skip
        if wanted:
            for app_id, reading in reads.get_many(wanted, load_lab).items():
                if reading.ok:
                    known[app_id] = conf.baseline_of(reading.info)

        def facts(root_, app_id):
            return reads.facts(root_, app_id, conf.app_facts)

        return {
            m.id: conf.assess(root, results.get(m.id), m, known, facts, reads.images)
            for m in models
        }

    def recheck_reasons(users: list[Automation], fresh: dict) -> dict[str, str]:
        return {a.id: fresh[a.id].reason for a in users if fresh[a.id].needs_recheck}

    @portal.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        apps = []
        app_ids = sorted(
            set(factory.needed_apps(root))
            | {p.parent.name for p in (root / "apps").glob("*/app.yaml")}
        )

        def has_lab(app_id: str) -> bool:
            model = app_model(app_id)
            return bool((root / "apps" / app_id / "Dockerfile").exists() and model and model.lab)

        # Every app's lab is asked at the same time, each with a short limit, so one slow or dead
        # lab cannot hold the page up.
        statuses = reads.run_all(
            [a for a in app_ids if has_lab(a)], lambda a: lab_mod.status(root, a), wait=8
        )
        running = [a for a, st in statuses.items() if st.running]
        reads.get_many(running, load_lab)  # fills the cache the loop below reads
        users_of = {a: factory.automations(root, a) for a in app_ids}
        models = {u.id: u for us in users_of.values() for u in us}
        fresh = freshness_for(list(models.values()))
        for app_id in app_ids:
            folder = root / "apps" / app_id
            ev = factory.discover(root, app_id)
            built = (folder / "Dockerfile").exists()
            users = users_of[app_id]
            timed_out = has_lab(app_id) and app_id not in statuses
            lab = statuses.get(app_id) or (
                lab_mod.Status(app_id, False, {}, error="the lab did not answer in time")
                if timed_out
                else None
            )
            ready = [a.id for a in users if fresh[a.id].ready]
            declared = [a.id for a in users if a.expects is not None]
            info, recorded = run_progress(app_id, lab)
            steps = guide.app_steps(
                app_id,
                has_evidence=bool(ev.kinds() or users or factory.flows(root, app_id)),
                has_brief=(folder / "BRIEF.md").exists(),
                built=built,
                running=bool(lab and lab.running),
                automations=[a.id for a in users],
                ready=ready,
                has_baseline=bool(info.get("baseline")),
                sessions=len(recorded),
                recorded_automations=guide.completed_automations(recorded),
                declared=declared,
                recheck=recheck_reasons(users, fresh),
            )
            apps.append({
                "id": app_id,
                "evidence": sum(len(getattr(ev, k)) for k in ("recordings", "ddl", "openapi", "screenshots")),
                "brief": (folder / "BRIEF.md").exists(),
                "built": built,
                "automations": [a.id for a in users],
                "ready": ready,
                "out_of_date": [a.id for a in users if fresh[a.id].needs_recheck],
                "lab": lab,
                "unreachable": timed_out or lab_unreachable(app_id, lab),
                "steps": steps,
                "next": guide.next_step(steps),
            })  # fmt: skip
        return page(request, "overview.html", apps=apps, jobs=jobs.list(limit=8))

    @portal.get("/start", response_class=HTMLResponse)
    def start(request: Request):
        app_ids = sorted({p.parent.name for p in (root / "apps").glob("*/app.yaml")}
                         | set(factory.needed_apps(root)))  # fmt: skip
        selected = request.query_params.get("app")
        if selected and selected not in app_ids:
            raise HTTPException(404, "Choose an application from the list")
        first = selected or next(
            (a for a in app_ids if (root / "apps" / a / "Dockerfile").exists()),
            app_ids[0] if app_ids else None,
        )
        folder = root / "apps" / first if first else None
        model = app_model(first) if first else None
        built = bool(folder and (folder / "Dockerfile").exists())
        status = lab_mod.status(root, first) if built and model and model.lab else None
        info, recorded = run_progress(first, status)
        users = factory.automations(root, first) if first else []
        completed = guide.completed_automations(recorded)
        fresh = freshness_for(users)
        again = recheck_reasons(users, fresh)
        steps = guide.onboarding(
            named=bool(_me(request)),
            automations=len(users),
            flows=len(factory.flows(root, first)) if first else 0,
            evidence=bool(
                first
                and (factory.discover(root, first).kinds() or users or factory.flows(root, first))
            ),
            briefs=bool(folder and (folder / "BRIEF.md").exists()),
            built=built,
            running=bool(status and status.running),
            ready=bool(users) and all(fresh[a.id].ready for a in users),
            declared=bool(users)
            and all(a.expects is not None and a.id in completed for a in users),
            first_app=first,
            has_baseline=bool(info.get("baseline")),
            recheck="; ".join(f"{a} ({why})" for a, why in again.items()),
        )
        return page(request, "start.html", steps=steps, next=guide.next_step(steps),
                    glossary=guide.GLOSSARY, app_ids=app_ids, selected_app=first,
                    readiness=[(a.id, fresh[a.id]) for a in users])  # fmt: skip

    @portal.post("/me")
    def set_me(request: Request, name: str = Form("")):
        from urllib.parse import quote, urlsplit

        referrer = urlsplit(request.headers.get("referer") or "/")
        target = referrer.path or "/"
        if referrer.query:
            target += "?" + referrer.query
        clean = " ".join(name.split())[:60]
        if clean and not re.fullmatch(r"[\w .'-]+", clean):
            return back(target, "Use letters, digits, spaces, dots, dashes or apostrophes", "error")
        resp = back(target, f"Hello {clean}" if clean else "Name cleared")
        if clean:
            resp.set_cookie(ME_COOKIE, quote(clean), max_age=365 * 24 * 3600, httponly=True,
                            samesite="lax")  # fmt: skip
        else:
            resp.delete_cookie(ME_COOKIE)
        return resp

    # --- automations -----------------------------------------------------------------------------

    @portal.get("/automations", response_class=HTMLResponse)
    def automations_page(request: Request):
        listed = all_automations()
        return page(
            request,
            "automations.html",
            automations=listed,
            verdicts=latest_verdicts(),
            fresh=freshness_for([m for _, m, _ in listed if m is not None]),
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
        try:
            (folder / "project").mkdir()
            if project is not None and project.filename:
                _unzip(await project.read(), folder / "project")
            save_automation(model)
        except (ValueError, OSError) as exc:
            # Nothing half-registered may stay: it would block a retry and show nowhere.
            shutil.rmtree(folder, ignore_errors=True)
            return back("/automations", _file_problem(exc), "error")
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
        if kind == "python" and any((folder / "project").rglob("*.py")):
            return _analyse(automation_id)  # fills in the fields each declared route uses
        return back(f"/automations/{automation_id}", "Automation registered")

    def _analyse(automation_id: str):
        model = get_automation(automation_id)
        try:
            result = analysis.analyse(root / "automations" / automation_id, model)
        except analysis.AnalysisError as exc:
            return back(f"/automations/{automation_id}", f"Analysis refused: {exc}", "error")
        try:
            save_automation(Automation.model_validate(result.data))
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
            freshness=freshness_for([model])[model.id],
            botruns=botruns.uploads(state, model.id),
            lab_sessions=_sessions_for(model),
        )  # fmt: skip

    def _sessions_for(model: Automation) -> list[dict]:
        """Lab sessions of this automation's apps (newest first), for a parity check."""
        out = []
        for app_id in model.apps():
            try:
                for s in lab_mod.agent(root, app_id).sessions() or []:
                    out.append({**s, "app": app_id})
            except (lab_mod.LabError, bnd.BindingError):
                continue
        return sorted(out, key=lambda s: s.get("started") or "", reverse=True)[:20]

    @portal.post("/automations/{automation_id}/botruns")
    async def add_botrun(
        automation_id: str,
        log: UploadFile = File(...),
        netlog: UploadFile | None = File(None),
        source: str = Form("production"),
    ):
        get_automation(automation_id)
        data = await log.read()
        net = await netlog.read() if netlog is not None and netlog.filename else None
        if len(data) > MAX_UPLOAD or (net and len(net) > MAX_UPLOAD):
            return back(
                f"/automations/{automation_id}#runs", "File too large (200 MB at most)", "error"
            )
        try:
            meta = botruns.import_log(root, state, automation_id, data, log.filename or "robot.log",
                                      net, source)  # fmt: skip
        except (botruns.RunsError, OSError, ValueError) as exc:
            return back(f"/automations/{automation_id}#runs", str(exc), "error")
        return back(f"/automations/{automation_id}#runs",
                    f"Read {meta['runs']} robot run(s) from {meta['file']}")  # fmt: skip

    @portal.post("/automations/{automation_id}/botruns/{upload}/condition")
    def botrun_condition(automation_id: str, upload: str):
        get_automation(automation_id)
        try:
            path = botruns.save_condition(
                root, botruns.calibrated_condition(state, automation_id, upload)
            )
        except (botruns.RunsError, ValidationError) as exc:
            return back(f"/automations/{automation_id}#runs", f"No condition made: {exc}", "error")
        return back(f"/automations/{automation_id}#runs",
                    f"Saved {path.relative_to(root).as_posix()}: apply it on the app page (Conditions)")  # fmt: skip

    @portal.post("/automations/{automation_id}/parity", response_class=HTMLResponse)
    def botrun_parity(
        request: Request, automation_id: str, base: str = Form(...), other: str = Form(""),
        session: str = Form(""),
    ):  # fmt: skip
        """A run (production) against another run of the bot (its log from a lab run), with that
        run's lab session for the row changes."""
        model = get_automation(automation_id)
        try:
            data = None
            if session:
                app_id, _, session_id = session.partition("/")
                if app_id not in model.apps():
                    raise botruns.RunsError(f"{app_id} is not an app of {automation_id}")
                data = lab_mod.agent(root, slug(app_id, "app id")).session(session_id)
            status, report = botruns.parity(state, automation_id, base, other or None, data)
            if session and other:
                # The lab run and its session: where each written value came from (I-11, I-12).
                from labctl import provenance

                agent = lab_mod.agent(root, app_id)
                detail = agent.session_detail(session_id) or {}
                prov = provenance.bind(botruns.run(state, automation_id, other),
                                       detail.get("changes") or [], agent.data())  # fmt: skip
                report += "\n" + prov.markdown()
        except (botruns.RunsError, lab_mod.LabError, ValidationError) as exc:
            return back(f"/automations/{automation_id}#runs", f"No comparison: {exc}", "error")
        title = f"Parity {status.upper()}: {base.split('/')[-1]} against {(other or session).split('/')[-1]}"
        return page(request, "report.html", title=title, html=md.render(report),
                    back=f"/automations/{automation_id}#runs")  # fmt: skip

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
        baselines: list = []
        if status and status.running:
            try:
                agent = lab_mod.agent(root, app_id)
                lab_info, sessions, current_condition = (
                    agent.info(),
                    agent.sessions(),
                    agent.condition(),
                )
                baselines = agent.baselines()
            except lab_mod.LabError as exc:
                lab_info = lab_info or {"error": str(exc)}
        lab_read = bool(lab_info) and "error" not in lab_info  # the lab answered just now
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
        users = factory.automations(root, app_id)
        fresh = freshness_for(users, {app_id: conf.baseline_of(lab_info)} if lab_read else None)
        listed = sessions if isinstance(sessions, list) else (sessions or {}).get("sessions", [])
        ready_now = [a.id for a in users if fresh[a.id].ready]
        steps = guide.app_steps(
            app_id,
            has_evidence=bool(ev.kinds() or users or factory.flows(root, app_id)),
            has_brief=(folder / "BRIEF.md").exists(), built=(folder / "Dockerfile").exists(),
            running=bool(status and status.running), automations=[a.id for a in users],
            ready=ready_now,
            has_baseline=bool((lab_info or {}).get("baseline")), sessions=len(listed or []),
            recorded_automations=guide.completed_automations(listed or []),
            declared=[a.id for a in users if a.expects is not None],
            recheck=recheck_reasons(users, fresh),
        )  # fmt: skip
        # Guided until a named session has completed for a bot whose readiness is current.
        first_done = bool(set(guide.completed_automations(listed or [])) & set(ready_now))
        lease = (lab_info or {}).get("lease")
        me = _me(request)
        from labctl import dbservers

        db_choices = [("postgres", "PostgreSQL, in a container Twin Lab runs"),
                      ("sqlserver", "SQL Server 2022, in a container Twin Lab runs"),
                      ("sqlite", "SQLite, one file; no slow-database conditions")]  # fmt: skip
        try:
            db_choices += [(s.name, f"{s.label} at {s.host} ({s.name})")
                           for s in dbservers.load(root).values()]  # fmt: skip
        except dbservers.DbServerError:
            pass
        db_current = dbservers.choice(model.lab.database if model and model.lab else None)
        is_browser = bool(model and model.browser_only)
        # Conditions that only slow or break the database link do nothing here (R07).
        db_only = {
            c.id
            for c in conditions()
            if (t := condition_for(c, app_id)) and t["db"] and not t["http"] and not t["faults"]
        }
        return page(
            request,
            "app.html",
            app_id=app_id,
            app=model,
            steps=steps,
            next=guide.next_step(steps),
            guided=not first_done,
            readiness=[(a.id, fresh[a.id]) for a in users],
            lab_unreachable=bool(status and status.running and not lab_read),
            browser_only=is_browser,
            data_info=(model.stack.data if model and model.stack else None),
            db_only_conditions=sorted(db_only) if is_browser else [],
            db_choices=db_choices,
            db_current=db_current,
            db_label=dict(db_choices).get(db_current, f"{db_current} (not configured)"),
            lab_copies=copies_mod.listing(root, app_id),
            lease=lease,
            lease_mine=bool(
                lease
                and me
                and " ".join(me.split()).casefold() == " ".join(lease["holder"].split()).casefold()
            ),  # fmt: skip
            evidence=ev,
            replayable=[p.name for p in ev.recordings if (p / "steps.jsonl").exists()],
            has_brief=(folder / "BRIEF.md").exists(),
            built=(folder / "Dockerfile").exists(),
            needed_by=[a.id for a in factory.automations(root, app_id)],
            flows=[f.id for f in factory.flows(root, app_id)],
            status=status,
            lab_info=lab_info,
            sessions=sessions,
            baselines=baselines,
            packs=[p.to_dict() for p in packs_mod.listing(root, app_id)],
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
        app_id: str,
        kind: str = Form(...),
        file: UploadFile = File(...),
        name: str = Form(""),
        replace: str = Form(""),
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
                _put(base / f"{stem}.sql", data, replace)
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
                _put(base / target_name, data, replace)
            elif kind == "screenshot":
                shots = base / "screenshots"
                shots.mkdir(exist_ok=True)
                if not filename.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")
                ):
                    return back(
                        f"/apps/{app_id}#evidence", "Screenshots must be images or PDF", "error"
                    )
                _put(shots / filename, data, replace)
            else:
                return back(f"/apps/{app_id}#evidence", f"Unknown evidence kind {kind}", "error")
        except (ValueError, OSError) as exc:
            if kind == "recording" and target.exists() and not list(target.rglob("*.har")):
                shutil.rmtree(target, ignore_errors=True)  # a half-unpacked recording shows nowhere
            return back(f"/apps/{app_id}#evidence", _file_problem(exc), "error")
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
    def record(
        request: Request,
        app_id: str,
        name: str = Form(...),
        url: str = Form(""),
        cdp: str = Form(""),
        minutes: float = Form(30),
    ):
        app_id = slug(app_id, "app id")
        cdp = cdp.strip()
        if cdp and not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\]):\d{2,5}/?$", cdp):
            return back(
                f"/apps/{app_id}#evidence",
                "Attach only to a browser on this machine, e.g. http://127.0.0.1:9222",
                "error",
            )
        if not cdp and not url.strip():
            return back(f"/apps/{app_id}#evidence", "Give a start address", "error")
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return back(
                f"/apps/{app_id}#evidence",
                "Recording opens a browser window on the Twin Lab machine itself, where you cannot "
                "see it. Record there, or record in your own browser (developer tools, Save all as "
                "HAR) and upload the .har here.",
                "error",
            )
        target = factory.evidence_dir(root, app_id) / slug(name, "recording name")
        if target.exists():
            return back(f"/apps/{app_id}#evidence", f"{target.name} already exists", "error")

        stop_flag = state / "record-stop" / f"{app_id}--{target.name}"

        def job(log):
            from labctl import recorder

            limit = max(1.0, min(minutes, 240.0)) * 60
            if cdp:
                print(
                    f"Recording the browser at {cdp}. Run the flow (or the robot) now. "
                    "Press Stop recording on the app page, or close that browser, to finish."
                )
                stop_flag.unlink(missing_ok=True)
                try:
                    recorder.attach(cdp, target, max_seconds=limit, stop=stop_flag.exists)
                finally:
                    stop_flag.unlink(missing_ok=True)
            else:
                print(
                    f"A browser window opens on this machine at {url}. Go through the flow, then close the window."
                )
                recorder.record(url, target, headless=False, max_seconds=limit)
            print(f"Saved to evidence/{app_id}/{target.name}")
            return {"recording": target.name}

        job_id = jobs.submit("record", f"Record a flow of {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.post("/apps/{app_id}/record/stop")
    def record_stop(app_id: str, name: str = Form(...)):
        app_id = slug(app_id, "app id")
        flag = state / "record-stop" / f"{app_id}--{slug(name, 'recording name')}"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
        return back(
            f"/apps/{app_id}#evidence", f"Stopping the recording {name}; it is saved in a moment"
        )

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
            if src_path is not None and root not in src_path.resolve().parents:
                raise PermissionError("the source folder must be inside the repository")
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
    def lab_settings(app_id: str, port: int = Form(...), db_port: int | None = Form(None),
                     username: str = Form(""), password: str = Form("")):  # fmt: skip
        app_id = slug(app_id, "app id")
        path = root / "apps" / app_id / "app.yaml"
        if not path.exists():
            return back(f"/apps/{app_id}", "No app.yaml yet: generate the brief first", "error")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        creds = {k: v for k, v in {"username": username.strip(), "password": password}.items() if v}
        lab = {**(data.get("lab") or {}), "port": port, "credentials": creds}
        lab.pop("db_port", None)
        if db_port:
            lab["db_port"] = db_port
        data["lab"] = lab  # keeps the other settings, such as the database choice
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
        # A download changes nothing: the build is recorded when its pull request is entered.
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @portal.post("/apps/{app_id}/devin/manual")
    def devin_manual(app_id: str, pr_url: str = Form(...)):
        app_id = slug(app_id, "app id")
        if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$", pr_url.strip()):
            return back(f"/apps/{app_id}#build", "That is not a GitHub pull request URL", "error")
        own = re.compile(rf"^twinlab-{re.escape(app_id)}-[0-9a-f]{{8}}\.zip$")  # not qtt-extra's
        packages = sorted(
            (p for p in (state / "packages").glob("twinlab-*.zip") if own.match(p.name)),
            key=lambda q: q.stat().st_mtime,
        )
        try:
            job = devin_jobs().record_manual(
                app_id, packages[-1] if packages else Path("unknown.zip")
            )
            devin_jobs().attach_pr(job.id, pr_url.strip())
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not recorded: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Build by hand recorded with its pull request")

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
            devin_jobs().message(own_job(app_id, job_id).id, text)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not sent: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Message sent to Devin")

    @portal.post("/apps/{app_id}/devin/{job_id}/cancel")
    def devin_cancel(app_id: str, job_id: str):
        try:
            devin_jobs().cancel(own_job(app_id, job_id).id)
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not cancelled: {exc}", "error")
        return back(f"/apps/{app_id}#build", "Devin session stopped")

    @portal.post("/apps/{app_id}/devin/{job_id}/pr")
    def devin_pr(app_id: str, job_id: str, pr_url: str = Form(...)):
        if not re.match(r"^https://github\.com/[\w.-]+/[\w.-]+/pull/\d+$", pr_url.strip()):
            return back(f"/apps/{app_id}#build", "That is not a GitHub pull request URL", "error")
        try:
            devin_jobs().attach_pr(own_job(app_id, job_id).id, pr_url.strip())
        except Exception as exc:  # noqa: BLE001
            return back(f"/apps/{app_id}#build", f"Not recorded: {exc}", "error")
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

    # copies of a lab, one per run

    @portal.post("/apps/{app_id}/copies")
    def copy_start(app_id: str, data: str = Form("baseline")):
        app_id = slug(app_id, "app id")
        if data not in ("baseline", "current", "app"):
            data = slug(data, "data pack")

        def job(log):
            print(f"Starting a copy of {app_id}'s lab (data: {data}) ...")
            rec = copies_mod.create(root, app_id, data=data, build=False)
            print(f"{rec['lab']}: running on http://127.0.0.1:{rec['port']}, database port "
                  f"{rec['db_port']}, data: {rec['data']}")  # fmt: skip
            print(f"Bindings for a bot: labctl bindings <automation> --on {rec['lab']}")
            return rec

        job_id = jobs.submit("lab", f"Copy of {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.post("/apps/{app_id}/copies/{name}/drop")
    def copy_drop(app_id: str, name: str):
        app_id, name = slug(app_id, "app id"), slug(name, "copy name")

        def job(log):
            copies_mod.drop(root, f"{app_id}.{name}")
            print(f"{app_id}.{name}: dropped")
            return {"dropped": f"{app_id}.{name}"}

        job_id = jobs.submit("lab", f"Drop {app_id}.{name}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # fidelity replay: a recording of the real application, replayed against the demo app

    def fidelity_dir(app_id: str) -> Path:
        return state / "fidelity" / app_id

    @portal.post("/apps/{app_id}/fidelity")
    def fidelity_start(app_id: str, recording: str = Form(...), values: str = Form("")):
        app_id = slug(app_id, "app id")
        model = app_model(app_id)
        if model is None or model.lab is None:
            return back(f"/apps/{app_id}#lab", "The app has no lab ports yet", "error")
        folder = factory.evidence_dir(root, app_id) / slug(recording, "recording")
        if not (folder / "steps.jsonl").exists():
            return back(
                f"/apps/{app_id}#lab", f"{recording} is not a recording with steps", "error"
            )
        typed: dict[str, str] = {}
        for line in values.splitlines():
            sel, sep, val = line.partition("=")
            if sep and sel.strip():
                typed[sel.strip()] = val.strip()
        creds = model.lab.credentials
        base_url = f"http://127.0.0.1:{model.lab.port}"

        def job(log):
            from labctl.behaviour import fidelity

            given = dict(typed)
            for sel in ("input[type=password]", "input#password", 'input[name="password"]'):
                if creds.get("password"):
                    given.setdefault(sel, creds["password"])
            print(f"Replaying {folder.name} against {base_url} ...")
            report = fidelity.replay(folder, base_url, given)
            name = fidelity.save(root, app_id, report)
            failed = sum(1 for s in report.steps if s.status == "fail")
            print(f"Result: {report.status.upper()} ({len(report.steps)} steps, {failed} failed)")
            print(f"Report: /apps/{app_id}/fidelity/{name}")
            return {"status": report.status, "report": f"/apps/{app_id}/fidelity/{name}"}

        job_id = jobs.submit("fidelity", f"Fidelity replay of {folder.name}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @portal.get("/apps/{app_id}/fidelity/{name}", response_class=HTMLResponse)
    def fidelity_report(request: Request, app_id: str, name: str):
        app_id = slug(app_id, "app id")
        if not re.match(r"^[a-z0-9][a-z0-9-]*--\d{8}T\d{6}Z$", name):
            raise HTTPException(404)
        path = fidelity_dir(app_id) / f"{name}.md"
        if not path.exists():
            raise HTTPException(404)
        return page(request, "report.html", title=f"Fidelity: {name.split('--')[0]}",
                    html=md.render(path.read_text(encoding="utf-8")), back=f"/apps/{app_id}#lab")  # fmt: skip

    # data

    @portal.get("/apps/{app_id}/data/template")
    def data_template(app_id: str, current: bool = False):
        from labctl.data import workbook

        app_id = slug(app_id, "app id")
        if (na := not_applicable(app_id)) is not None:
            return na
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
        if (na := not_applicable(app_id)) is not None:
            return na
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
        if (na := not_applicable(app_id)) is not None:
            return na
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
        if (na := not_applicable(app_id)) is not None:
            return na
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

    @portal.post("/apps/{app_id}/packs/save")
    def pack_save(app_id: str, name: str = Form(...)):
        app_id = slug(app_id, "app id")
        if (na := not_applicable(app_id)) is not None:
            return na
        try:
            info = packs_mod.save_from_lab(root, app_id, name.strip().lower())
        except (packs_mod.PackError, lab_mod.LabError) as exc:
            return back(f"/apps/{app_id}#data", str(exc), "error")
        return back(
            f"/apps/{app_id}#data", f"Saved pack {info.name}: {_counts({'counts': info.counts})}"
        )

    @portal.post("/apps/{app_id}/packs/{name}/{action}")
    def pack_action(app_id: str, name: str, action: str):
        app_id = slug(app_id, "app id")
        if (na := not_applicable(app_id)) is not None:
            return na
        try:
            if action == "use":
                reply = packs_mod.load_into_lab(root, app_id, name)
                return back(f"/apps/{app_id}#data", f"Loaded pack {name}: {_counts(reply)}. It is now the baseline.")  # fmt: skip
            if action == "delete":
                packs_mod.delete(root, app_id, name)
                return back(f"/apps/{app_id}#data", f"Deleted pack {name}")
        except (packs_mod.PackError, lab_mod.LabError) as exc:
            return back(f"/apps/{app_id}#data", str(exc), "error")
        raise HTTPException(404)

    @portal.post("/apps/{app_id}/lease/{action}")
    def lease_action(
        request: Request, app_id: str, action: str, minutes: int = Form(60), purpose: str = Form("")
    ):
        app_id = slug(app_id, "app id")
        me = _me(request)
        agent = lab_mod.agent(root, app_id)
        try:
            if action == "take":
                if not me:
                    return back(f"/apps/{app_id}#lab", "Say who you are first (top right)", "error")
                lease = agent.take_lease(me, minutes, purpose.strip() or None)
                return back(
                    f"/apps/{app_id}#lab", f"Reserved for you until {lease['until'][:16]} UTC"
                )
            if action in ("release", "force"):
                agent.release_lease(force=action == "force")
                return back(f"/apps/{app_id}#lab", "The reservation is released")
        except lab_mod.LabError as exc:
            return back(f"/apps/{app_id}#lab", str(exc), "error")
        raise HTTPException(404)

    @portal.post("/apps/{app_id}/behaviour-compare", response_class=HTMLResponse)
    def behaviour_compare(request: Request, app_id: str, a: list[str] = Form(...),
                          b: list[str] = Form(...), name_a: str = Form("UAT"),
                          name_b: str = Form("production")):  # fmt: skip
        """Does the app behave the same in two places (I-22b)? Recordings of each, compared."""
        from labctl.behaviour import compare as compare_mod

        app_id = slug(app_id, "app id")
        base = factory.evidence_dir(root, app_id)

        def folders(names: list[str]) -> list[Path]:
            out = [base / slug(n, "recording") for n in names]
            if not all((p / "steps.jsonl").exists() for p in out):
                raise HTTPException(400, "choose recordings that have steps")
            return out

        result = compare_mod.compare_recordings(folders(a), folders(b), name_a.strip() or "A",
                                                name_b.strip() or "B")  # fmt: skip
        return page(request, "report.html", title=f"Behaviour: {result.status}",
                    html=md.render(result.markdown()), back=f"/apps/{app_id}#evidence")  # fmt: skip

    @portal.post("/apps/{app_id}/baselines/{baseline_id}/{action}")
    def baseline_action(
        app_id: str, baseline_id: str, action: str, pinned: str = Form(""), reason: str = Form("")
    ):
        app_id = slug(app_id, "app id")
        if (na := not_applicable(app_id)) is not None:
            return na
        agent = lab_mod.agent(root, app_id)
        try:
            if action == "restore":
                reply = agent.restore_baseline(baseline_id)
                return back(f"/apps/{app_id}#data", f"Restored the baseline: {_counts(reply)}")
            if action == "pin":
                agent.pin_baseline(baseline_id, bool(pinned))
                return back(f"/apps/{app_id}#data", "Pinned" if pinned else "Unpinned")
            if action == "quarantine":
                agent.quarantine_baseline(baseline_id, reason.strip() or None)
                return back(
                    f"/apps/{app_id}#data", "Quarantined: it cannot be restored or reset to"
                )
            if action == "release":
                agent.quarantine_baseline(baseline_id, None, release=True)
                return back(f"/apps/{app_id}#data", "Out of quarantine")
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
            if browser_only(app_id) and body["db"] and not body["http"] and not body["faults"]:
                return back(
                    f"/apps/{app_id}#conditions",
                    f"Not applicable: {condition_id} only slows the database, and this app keeps "
                    "its data in the browser",
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
        if browser_only(app_id):
            reset = ""  # nothing in a lab database to reset
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
    def session_page(request: Request, app_id: str, session_id: str, case: str = ""):
        app_id = slug(app_id, "app id")
        try:
            verdict, session, auto = verdict_mod.for_session(root, app_id, session_id)
        except lab_mod.LabError as exc:
            raise HTTPException(404, str(exc)) from exc
        others = [s for s in _session_list(app_id) if s.get("session_id") != session_id]
        unexpected = {(c["table"], c["op"], c["key"]): c["why"] for c in verdict.unexpected}
        try:
            tables_used = (lab_mod.agent(root, app_id).session_detail(session_id) or {}).get(
                "tables_used"
            )
        except lab_mod.LabError:
            tables_used = None
        try:
            outcome = outcome_mod.for_session(root, app_id, session_id, auto, case or None)[0]
            outcome_info = {**outcome.to_dict(), "line": outcome.summary()}
        except (lab_mod.LabError, ValueError) as exc:
            outcome_info = {
                "status": "CANNOT_CHECK",
                "line": f"the outcome could not be judged: {exc}",
                "unmet": [], "unchecked": [], "notes": [],
            }  # fmt: skip
        return page(
            request, "session.html", app_id=app_id, s=session, others=others,
            verdict=verdict.to_dict(), verdict_automation=auto, unexpected=unexpected,
            tables_used=tables_used, outcome=outcome_info, browser_only=browser_only(app_id),
        )  # fmt: skip

    @portal.post("/apps/{app_id}/sessions/{session_id}/accept")
    def session_accept(app_id: str, session_id: str, replace: str = Form("")):
        app_id = slug(app_id, "app id")
        url = f"/apps/{app_id}/sessions/{session_id}"
        try:
            session, changes = verdict_mod.session_changes(lab_mod.agent(root, app_id), session_id)
            auto = session.get("automation")
            if not auto:
                return back(url, "The session names no automation", "error")
            expects = verdict_mod.accept(root, slug(auto, "automation"), changes, bool(replace))
        except (lab_mod.LabError, ValueError) as exc:
            return back(url, str(exc), "error")
        said = "; ".join(f"{e.table}: {', '.join(e.ops)}" for e in expects) or "no row changes"
        return back(url, f"automations/{auto}/automation.yaml now expects {said}")

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
            request,
            "mapping.html",
            automations=autos,
            apps=app_ids,
            verdicts=latest_verdicts(),
            fresh=freshness_for(autos),
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
        from labctl import dbservers

        try:
            servers, db_error = [{**s.described(), "problems": s.problems()}
                                 for s in dbservers.load(root).values()], None  # fmt: skip
        except dbservers.DbServerError as exc:
            servers, db_error = [], str(exc)
        return page(request, "settings.html", cfg=cfg, cfg_error=error, git=git,
                    checks=jobs.list("devin-check", limit=3), updates=jobs.list("git", limit=3),
                    servers=servers, db_error=db_error)  # fmt: skip

    @portal.post("/settings/db-check")
    def db_check(name: str = Form(...)):
        from labctl import dbservers

        try:
            said = dbservers.check(dbservers.server(root, name))
        except dbservers.DbServerError as exc:
            return back("/settings#databases", str(exc), "error")
        return back("/settings#databases", said)

    @portal.post("/apps/{app_id}/database")
    def app_database(app_id: str, name: str = Form("postgres")):
        from labctl import dbservers

        app_id = slug(app_id, "app id")
        path = root / "apps" / app_id / "app.yaml"
        model = app_model(app_id)
        if model is None or model.lab is None:
            return back(f"/apps/{app_id}#lab", "Set the lab ports first", "error")
        if name not in dbservers.BUILT_IN:
            try:
                dbservers.server(root, name)
            except dbservers.DbServerError as exc:
                return back(f"/apps/{app_id}#lab", str(exc), "error")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if name == "postgres":
            data["lab"].pop("database", None)
        else:
            data["lab"]["database"] = name
        path.write_text(yaml.safe_dump(data, sort_keys=False, width=100), encoding="utf-8",
                        newline="\n")  # fmt: skip
        return back(f"/apps/{app_id}#lab", f"{app_id} now uses the database {name}. Press "
                    "Rebuild and restart: the tables are created there.")  # fmt: skip

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
            code, out = _git_run(["pull", "--ff-only"])
            print(out)
            if code != 0:  # judged by git's exit code, never by words in its output
                raise RuntimeError(f"git pull did not complete (exit {code}); see the log")
            return {"ok": True}

        job_id = jobs.submit("git", "Get the latest from GitHub", job, "settings")
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    def _git_run(args: list[str]) -> tuple[int, str]:
        """git that never waits for a password or passphrase nobody can type (no terminal)."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, encoding="utf-8", errors="replace",
                timeout=120, check=False, env=env,
            )  # fmt: skip
        except FileNotFoundError:
            return 127, "git is not installed or not on PATH"
        except subprocess.TimeoutExpired:
            return 124, "git did not finish within 120 s (network or credentials?)"
        return out.returncode, ((out.stdout or "") + (out.stderr or "")).strip()

    def _git(args: list[str]) -> str:
        return _git_run(args)[1]

    @portal.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.endswith(".json") or "application/json" in request.headers.get(
            "accept", ""
        ):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return templates.TemplateResponse(request, "error.html", {"code": exc.status_code, "detail": exc.detail},
                                          status_code=exc.status_code)  # fmt: skip

    portal.state.jobs = jobs
    portal.state.reads = reads
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


def _put(path: Path, data: bytes, replace: str) -> None:
    """Write an uploaded file; an existing one is replaced only when the person said so."""
    if path.exists() and not replace:
        raise ValueError(f"{path.name} already exists; tick 'Replace' to overwrite it")
    path.write_bytes(data)


def _file_problem(exc: Exception) -> str:
    """A plain sentence for an upload that could not be written."""
    if isinstance(exc, ValueError):
        return str(exc)
    if os.name == "nt" and isinstance(exc, OSError):
        return (
            f"Could not write the files ({exc.__class__.__name__}: {exc}). On Windows this is "
            "usually a path longer than 260 characters: move the repository to a shorter folder "
            "(for example C:\\tl) or turn on long paths (LongPathsEnabled)."
        )
    return f"Could not write the files: {exc}"


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
