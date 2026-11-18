# Certification matrix

Written by `labctl certify`. Do not edit by hand. The gates and how to add a technology
are in [10-certification.md](10-certification.md).

A gate is PASS, FAIL or NOT RUN. NOT RUN is never a pass. CERTIFIED needs all four gates.
CAPTURE VERIFIED means detect, capture and self-replay passed and no rebuild was judged.

| Technology | Version | Surface | Detect | Capture | Self-replay | Rebuild | Result | Date | Not covered |
|---|---|---|---|---|---|---|---|---|---|
| Server-rendered HTML forms | - | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | no server technology is declared (the fixture is Python's http.server and shows no marker); file upload and download; a rebuild by a coding agent |
| React | 16.7.0 | web | PASS | PASS | PASS | NOT RUN | **CAPTURE VERIFIED** | 2026-11-18 | React 17 and later (other bundle markers); a router, a data API, a component library; the files are served as static files, not by the app's own server.py; a rebuild by a coding agent |
| Angular | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| Vue | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| JSF / PrimeFaces | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| ASP.NET Web Forms | - | web | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | everything; nothing has run |
| WinForms | .NET 6 | desktop | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | .NET Framework 4.x builds; third-party grids and custom-drawn controls; a rebuild by a coding agent |
| WPF | .NET 6 | desktop | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | the expected actions and values assume the same flow and control ids as CaseDesk; data templates, virtualised lists, third-party controls; a rebuild by a coding agent |
| Java Swing | Java 11 | desktop | NOT RUN | NOT RUN | NOT RUN | NOT RUN | **NOT RUN** | 2026-11-18 | the Java version is not detected, so it is not compared; JavaFX and SWT; a rebuild by a coding agent |

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

### Angular (`angular`)

Result: **NOT RUN**. framework package not on this machine; needs a download the user has not approved

### Vue (`vue`)

Result: **NOT RUN**. framework package not on this machine; needs a download the user has not approved

### JSF / PrimeFaces (`jsf-primefaces`)

Result: **NOT RUN**. framework package not on this machine; needs a download the user has not approved

### ASP.NET Web Forms (`aspnet-webforms`)

Result: **NOT RUN**. framework package not on this machine; needs a download the user has not approved

### WinForms .NET 6 (`winforms`)

Result: **NOT RUN**. set TWINLAB_DESKTOP_GUI=1

- rebuild: NOT RUN. no rebuild replay report is named in the entry

### WPF .NET 6 (`wpf`)

Result: **NOT RUN**. reference app not present

- rebuild: NOT RUN. no rebuild replay report is named in the entry

### Java Swing Java 11 (`java-swing`)

Result: **NOT RUN**. set TWINLAB_DESKTOP_GUI=1

- rebuild: NOT RUN. no rebuild replay report is named in the entry
