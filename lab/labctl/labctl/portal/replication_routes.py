"""Routes of the replication workflow: declarations, replay, report, coverage, pictures (R09, R10).

Registered by `create_app`; every helper the page needs is passed in, so this module holds no
global state.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from labctl import coverage as coverage_mod
from labctl import multiapp
from labctl.portal import replication as rep


def register(portal, ctx: SimpleNamespace) -> None:
    root, state, jobs = ctx.root, ctx.state, ctx.jobs

    # --- which recording ----------------------------------------------------------------------

    def app_rec(app_id: str, name: str):
        app_id = ctx.slug(app_id, "app id")
        path = rep.recording_path(root, app_id, ctx.slug(name, "recording"))
        if path is None:
            raise HTTPException(404, f"{app_id} has no recording {name} with steps")
        return app_id, path, f"/apps/{app_id}/recordings/{path.name}"

    def combined(name: str):
        name = ctx.slug(name, "recording name")
        info = next((i for i in multiapp.recordings(root) if i["name"] == name), None)
        if info is None:
            raise HTTPException(404, f"No recording {name} across applications")
        return info, f"/recordings/{info['name']}/files"

    def declarations_page(request: Request, *, rec, prefix, title, back, app_id, replay_note):
        decls = rep.declarations(root, rec)
        blocked = [f"{d['name']}: {p}" for d in decls for p in d["hard"] + d["problems"]]
        return ctx.page(
            request, "declarations.html", title=title, back=back, prefix=prefix, decls=decls,
            blocked=blocked, notes=rep.notes(root, rec), guard_note=rep.GUARD_NOTE, app_id=app_id,
            rec=rec.name, where=rec.relative_to(root).as_posix() if root in rec.parents else rec.name,
            replay_note=replay_note, lab_up=rep.lab_running(root, app_id) if app_id else None,
            kinds=rep.KINDS)  # fmt: skip

    def save(prefix: str, rec, kind: str, text: str):
        if kind not in rep.FILES:
            raise HTTPException(404, "no such file kind")
        try:
            check = rep.save_declaration(root, rec, kind, text)
        except rep.Refused as exc:
            return ctx.back(prefix + f"#{kind}", f"Refused: {exc}", "error")
        name = rep.FILES[kind][0]
        if check.hard:
            return ctx.back(
                prefix + f"#{kind}", f"{name} was not saved: " + "; ".join(check.hard), "error"
            )
        if check.problems:
            return ctx.back(prefix + f"#{kind}", f"{name} saved, but it blocks a run: "
                            + "; ".join(check.problems[:3]), "error")  # fmt: skip
        return ctx.back(prefix + f"#{kind}", f"{name} saved and valid")

    def template(prefix: str, rec, kind: str):
        if kind not in rep.FILES:
            raise HTTPException(404, "no such file kind")
        try:
            path = rep.write_template(root, rec, kind)
        except rep.Refused as exc:
            return ctx.back(prefix + f"#{kind}", f"Refused: {exc}", "error")
        except FileExistsError:
            return ctx.back(prefix + f"#{kind}", f"{rep.FILES[kind][0]} exists: it is not "
                            "overwritten. Edit it here, or delete it first.", "error")  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the template reads the recording
            return ctx.back(prefix + f"#{kind}", f"Could not write the template: {exc}", "error")
        return ctx.back(prefix + f"#{kind}", f"Template written to {path.name}: replace every TODO")

    # --- an application's recording -------------------------------------------------------------

    @portal.get("/apps/{app_id}/recordings/{name}", response_class=HTMLResponse)
    def app_recording(request: Request, app_id: str, name: str):
        app_id, rec, prefix = app_rec(app_id, name)
        return declarations_page(request, rec=rec, prefix=prefix, app_id=app_id,
                                 title=f"{rec.name}: values, checks and replay",
                                 back=f"/apps/{app_id}#replication", replay_note=None)  # fmt: skip

    @portal.post("/apps/{app_id}/recordings/{name}/files/{kind}")
    def app_save(app_id: str, name: str, kind: str, text: str = Form("")):
        _, rec, prefix = app_rec(app_id, name)
        return save(prefix, rec, kind, text)

    @portal.post("/apps/{app_id}/recordings/{name}/files/{kind}/template")
    def app_template(app_id: str, name: str, kind: str):
        _, rec, prefix = app_rec(app_id, name)
        return template(prefix, rec, kind)

    @portal.post("/apps/{app_id}/recordings/{name}/replay")
    def app_replay(app_id: str, name: str, viewport: str = Form(""), anyway: str = Form("")):
        app_id, rec, prefix = app_rec(app_id, name)
        model = ctx.app_model(app_id)
        if model is None or model.lab is None:
            return ctx.back(prefix, "The app has no lab ports yet", "error")
        if not rep.lab_running(root, app_id):
            return ctx.back(prefix, f"Start the lab for {app_id} first: a replay needs the running "
                            "replica.", "error")  # fmt: skip
        stop = rep.blockers(root, rec)
        if stop and anyway != "1":
            return ctx.back(prefix, "Not started: " + "; ".join(stop[:4])
                            + (f" (and {len(stop) - 4} more)" if len(stop) > 4 else ""), "error")  # fmt: skip
        creds = model.lab.credentials
        given = {}
        for sel in ("input[type=password]", "input#password", 'input[name="password"]'):
            if creds.get("password"):
                given.setdefault(sel, creds["password"])
        base_url = f"http://127.0.0.1:{model.lab.port}"
        view = viewport if viewport in ("desktop", "mobile") else None

        def job(log):
            return rep.run_replay(root, state, app_id, rec, base_url, given=given, viewport=view)

        job_id = jobs.submit("replay", f"Replay {rec.name} against {app_id}", job, app_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # --- a recording across applications -------------------------------------------------------

    @portal.get("/recordings/{name}/files", response_class=HTMLResponse)
    def multi_recording(request: Request, name: str):
        info, prefix = combined(name)
        mapping, apps = rep.replica_map(root, info)
        from labctl.behaviour import fidelity

        takes = rep.replica_param(fidelity.replay)
        note = None
        if len(mapping) < 2:
            note = ("Create the applications first, and give each a lab port: a replay across "
                    "applications needs every included application's replica.")  # fmt: skip
        elif takes is None:
            note = ("Replaying the whole flow across applications is not available in this build "
                    "(the replay takes one base address). Replay each application's own recording "
                    "from its page.")  # fmt: skip
        page = declarations_page(request, rec=info["path"], prefix=prefix, app_id=None,
                                 title=f"{info['name']}: values and checks across applications",
                                 back=f"/recordings/{info['name']}", replay_note=note)  # fmt: skip
        return page

    @portal.post("/recordings/{name}/files/{kind}")
    def multi_save(name: str, kind: str, text: str = Form("")):
        info, prefix = combined(name)
        return save(prefix, info["path"], kind, text)

    @portal.post("/recordings/{name}/files/{kind}/template")
    def multi_template(name: str, kind: str):
        info, prefix = combined(name)
        return template(prefix, info["path"], kind)

    @portal.post("/recordings/{name}/replay")
    def multi_replay(name: str, viewport: str = Form(""), anyway: str = Form("")):
        info, prefix = combined(name)
        from labctl.behaviour import fidelity

        mapping, apps = rep.replica_map(root, info)
        if rep.replica_param(fidelity.replay) is None or len(mapping) < 2:
            return ctx.back(prefix, "Replay across applications is not available here: it needs "
                            "every application's lab port and a replay that takes a replica map. "
                            "Replay each application's own recording instead.", "error")  # fmt: skip
        down = [a for a in apps if not rep.lab_running(root, a)]
        if down:
            return ctx.back(prefix, "Start the lab of: " + ", ".join(down), "error")
        stop = rep.blockers(root, info["path"])
        if stop and anyway != "1":
            return ctx.back(prefix, "Not started: " + "; ".join(stop[:4]), "error")
        first = apps[0]
        view = viewport if viewport in ("desktop", "mobile") else None

        def job(log):
            return rep.run_replay(root, state, first, info["path"], mapping[next(iter(mapping))],
                                  viewport=view, replicas=mapping, apps=apps)  # fmt: skip

        job_id = jobs.submit(
            "replay", f"Replay {info['name']} across {', '.join(apps)}", job, first
        )
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # --- reports and pictures --------------------------------------------------------------------

    @portal.get("/apps/{app_id}/reports/{name}", response_class=HTMLResponse)
    def report_page(request: Request, app_id: str, name: str):
        app_id = ctx.slug(app_id, "app id")
        view = rep.report_view(root, state, app_id, name)
        if view is None:
            raise HTTPException(404, "no such report")
        return ctx.page(request, "replay_report.html", app_id=app_id, v=view,
                        back=f"/apps/{app_id}#replication")  # fmt: skip

    @portal.get("/apps/{app_id}/artifacts/{rel:path}")
    def artifact(app_id: str, rel: str):
        app_id = ctx.slug(app_id, "app id")
        path = rep.resolve_artifact(state, app_id, rel)
        if path is None:
            raise HTTPException(404, "no such picture")
        return FileResponse(path, media_type=rep.ARTIFACT_TYPES[path.suffix.lower()],
                            headers={"X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "default-src 'none'"})  # fmt: skip

    # --- coverage --------------------------------------------------------------------------------

    @portal.get("/apps/{app_id}/coverage", response_class=HTMLResponse)
    def coverage_page(request: Request, app_id: str):
        app_id = ctx.slug(app_id, "app id")
        saved = coverage_mod.load_saved(root, app_id)
        state_, why = ("none", [])
        if saved is not None:
            state_, why = coverage_mod.freshness(root, app_id, saved)
        return ctx.page(request, "coverage.html", app_id=app_id, c=saved, fresh=state_, why=why,
                        statuses=coverage_mod.STATUSES, back=f"/apps/{app_id}#replication")  # fmt: skip

    @portal.post("/apps/{app_id}/coverage")
    def coverage_build(app_id: str):
        app_id = ctx.slug(app_id, "app id")
        try:
            report = coverage_mod.build(root, app_id)
        except coverage_mod.CoverageError as exc:
            return ctx.back(f"/apps/{app_id}/coverage", str(exc), "error")
        coverage_mod.save(root, report)
        return ctx.back(f"/apps/{app_id}/coverage", "Coverage matrix built")

    @portal.get("/apps/{app_id}/coverage.json")
    def coverage_json(app_id: str):
        saved = coverage_mod.load_saved(root, ctx.slug(app_id, "app id"))
        if saved is None:
            raise HTTPException(404, "no coverage matrix yet")
        return JSONResponse(saved.model_dump(mode="json"))

    @portal.get("/apps/{app_id}/coverage.md")
    def coverage_md(app_id: str):
        saved = coverage_mod.load_saved(root, ctx.slug(app_id, "app id"))
        if saved is None:
            raise HTTPException(404, "no coverage matrix yet")
        return Response(saved.markdown(), media_type="text/markdown; charset=utf-8")
