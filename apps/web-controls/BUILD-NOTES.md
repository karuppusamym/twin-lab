# web-controls: multi-application recording trial

## Scope and implementation

Built from Twin Lab's generated BRIEF.md and one combined public-web recording. The requested
flow is preserved in requested-flow.yaml. The recorded page HTML, inline behavior and required
CSS/JS/fonts are reused from the HAR; this is evidence-based frontend replication, not a newly
inferred backend implementation. asset-manifest.json lists captured assets and hashes.

The generator ran in an isolated staging repository so shared flows, automations and judging
code were not changed. Generated BRIEF.md, schema and model were left unchanged. Only run ports
and health route were set in app.yaml. No verification labels were raised.

- web-controls retains jQuery 3.7.1 and jQuery UI 1.11.4.
- web-form retains jQuery 3.6.0, Bootstrap 5.1.0 and Bootstrap Datepicker 1.9.0.
- FastAPI serves the recorded pages. No server database was observed or added. Ruby was inferred
  for the first source from a cookie, but reproducing that server implementation is not claimed.
- Required CDN resources are local. Analytics is omitted. Public source credits remain in the
  captured pages; original licenses are under licenses/ and vendor headers are retained.
- Home/index links pointing outside recorded scope were removed. Strict full-page comparison
  correctly flags those differences. The root URL is a local convenience redirect.

## Validation and limits

Docker image built successfully. Both containers pass 23 paired business checks in one browser
session across both apps and all five pages, including returning to the first app, form submission
query values and Back navigation. Replica runtime requests were restricted to the two local
origins: zero external attempts and zero JavaScript page errors. Desktop screenshots were
inspected; mobile width and health/404 checks passed.

Strict unmodified split-recording fidelity replay does NOT pass. Read validation/raw-replay.md
and ../../docs/multi-app-recording-trial.md before treating this as a fidelity proof. The original
sites also fail two submit snapshot comparisons during source-against-source replay.

The brief infers browser storage from the absence of data requests. That is too strong here:
these pages mostly hold transient DOM/form state, not demonstrated durable storage. No storage
persistence guarantee is claimed. Passwords, file uploads, date selection, multi-tab sessions,
SSO, business APIs and server writes were not exercised in this trial.

## Run and reproduce

From the repository root, build with `docker build -t twinlab/web-controls:dev apps/web-controls`.
The running containers are twinlab-web-controls-trial (port 8737) and twinlab-web-form-trial
(port 8738), bound only to 127.0.0.1.

Run `python apps/web-controls/verify_multi.py` for the paired checks. With PYTHONPATH=lab/labctl,
record_multi.py re-records the public sequence and replay_parts.py / replay_combined.py reproduce
the replay checks. Record into a new evidence directory when preserving a previous trial.

This app is one half of a single-session experiment, not an assertion of full-site replication.
