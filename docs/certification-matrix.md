# Certification matrix

Written by `labctl certify`. Do not edit by hand. The gates and how to add a technology
are in [10-certification.md](10-certification.md).

A gate is PASS, FAIL or NOT RUN. NOT RUN is never a pass. CERTIFIED needs all four gates.
CAPTURE VERIFIED means detect, capture and self-replay passed and no rebuild was judged.

| Technology | Version | Surface | Detect | Capture | Self-replay | Rebuild | Result | Date | Not covered |
|---|---|---|---|---|---|---|---|---|---|
| Server-rendered HTML forms | - | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | no server technology is declared (the fixture is Python's http.server and shows no marker); file upload and download; a rebuild by a coding agent |
| React | 16.7.0 | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | React 17 and later (other bundle markers); a router, a data API, a component library; the files are served as static files, not by the app's own server.py; a rebuild by a coding agent |
| Angular | 21.2.26 | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | other Angular versions; zone.js change detection (the build is zoneless); server-side rendering and hydration, Angular Material, CDK overlays; HttpClient calls (the state is in memory, so no request is replayed); the route guard's redirect from / is recorded, not checked again by the replay; a rebuild by a coding agent |
| Vue | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| JSF (Mojarra) | 2.3 | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | PrimeFaces, RichFaces and other component libraries (plain h: components only); MyFaces; Jakarta Faces 3 and later; client-side state saving; the implementation's release (2.3.9) is in no page or header, so it is not compared; the acceptance cases flow.acceptance_for declares are run by the application's own test, not here; a rebuild by a coding agent |
| ASP.NET Web Forms | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| WinForms | .NET 6 | desktop | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | .NET Framework 4.x builds; third-party grids and custom-drawn controls; a rebuild by a coding agent |
| WPF | .NET 6 | desktop | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | .NET Framework 4.x builds; data templates, virtualised lists, third-party controls; a rebuild by a coding agent |
| Java Swing | Java 11 | desktop | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | the Java version is not detected, so it is not compared; JavaFX and SWT; a rebuild by a coding agent |

## Details

### Server-rendered HTML forms (`html-forms`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected framework server-rendered, rendering server
- capture: PASS. 62 expected action(s), all in the recording (107 action(s) recorded)
- self-replay: PASS. 107 of 107 step(s) replayed against a fresh instance, 63 compared, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### React 16.7.0 (`react-16`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected version 16.7.0, framework react, rendering client
- capture: PASS. 20 expected action(s), all in the recording (24 action(s) recorded)
- self-replay: PASS. 24 of 24 step(s) replayed against a fresh instance, 21 compared, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### Angular 21.2.26 (`angular`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected version 21.2.26, framework angular, rendering client
- capture: PASS. 34 expected action(s), all in the recording (34 action(s) recorded)
- self-replay: PASS. 34 of 34 step(s) replayed against a fresh instance, 26 compared, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### Vue (`vue`)

Result: **NOT RUN**. no reference application has been built for it yet

### JSF (Mojarra) 2.3 (`jsf-mojarra`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected version 2.3, framework JSF, rendering server, server.technology Java, server.framework JSF, server.framework_version 2.3, server.implementation Mojarra
- capture: PASS. 24 expected action(s), all in the recording (48 action(s) recorded)
- self-replay: PASS. 48 of 48 step(s) replayed against a fresh instance, 25 compared, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### ASP.NET Web Forms (`aspnet-webforms`)

Result: **NOT RUN**. no reference application has been built for it yet

### WinForms .NET 6 (`winforms`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected toolkit WinForms, runtime.name .NET, runtime.version 6.0.3
- capture: PASS. 20 expected action(s), all in the recording (36 action(s) recorded)
- self-replay: PASS. 29 of 36 step(s) verified against a fresh start, 7 covered by the steps around them, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### WPF .NET 6 (`wpf`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected toolkit WPF, runtime.name .NET, runtime.version 6.0.3
- capture: PASS. 20 expected action(s), all in the recording (36 action(s) recorded)
- self-replay: PASS. 29 of 36 step(s) verified against a fresh start, 7 covered by the steps around them, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry

### Java Swing Java 11 (`java-swing`)

Result: **CAPTURE VERIFIED**. rebuild not run: no rebuild replay report is named in the entry

- detect: PASS. detected toolkit Java Swing / AWT
- capture: PASS. 20 expected action(s), all in the recording (32 action(s) recorded)
- self-replay: PASS. 29 of 32 step(s) verified against a fresh start, 3 covered by the steps around them, 0 failed
- rebuild: NOT RUN. no rebuild replay report is named in the entry
