# Caseboard (JSF)

A small real JavaServer Faces application, the reference for JSF in the certification matrix:
Twin Lab records a session on it, must name its technology, and must replay the recording against
a fresh instance. Synthetic data only, held in memory; no request leaves the machine at run time.

| | |
|---|---|
| Framework | JSF 2.3, implementation Mojarra 2.3.9 (`org.glassfish:javax.faces`) |
| Pages | Facelets (`webapp/*.xhtml`), one template (`WEB-INF/template.xhtml`) |
| Beans | classic managed beans declared in `WEB-INF/faces-config.xml` (no CDI container) |
| State | server-side view state (`javax.faces.ViewState`), session by cookie, `;jsessionid=` in the first form's address |
| Server | embedded Jetty 9.4.57 (`src/caseboard/Main.java`), `127.0.0.1` only |
| Java | built with `--release 11`; run here on Java 17 |

## Screens

- `/login.xhtml`: user name (required) and password. Any user name signs in with the password
  `demo`; another password comes back with "Wrong user name or password".
- `/cases.xhtml`: twelve cases, five to a page (`h:dataTable`, Previous / Next), a search field
  over number, title and owner. A title opens the case (POST, then a redirect).
- `/edit.xhtml?id=1003`: title (required), hours (a whole number from 0 to 500: converter and
  validator messages), status (`h:selectOneMenu` with `f:ajax`: "Closed" re-renders a part of
  the form and shows a Resolution field), priority (`h:selectOneRadio`), urgent
  (`h:selectBooleanCheckbox`), Save, Cancel.
- `/saved.xhtml?id=1003`: the confirmation after Save (POST, then a redirect), and back to the
  list, which shows the saved title.
- Sign out (a command link in a form without an id: JSF names it `j_idtN`).

## Fetch, build, run

Needs a JDK 11 or newer (`java`, `javac` on PATH or under `JAVA_HOME`) and Python 3.11+. No
Maven or Gradle.

```bash
python reference/web/caseboard-jsf/fetch_libs.py     # 14 jars from Maven Central into lib/
python reference/web/caseboard-jsf/build.py          # javac into classes/
python reference/web/caseboard-jsf/run.py --port 8137
```

Then open <http://127.0.0.1:8137/>. `lib/` and `classes/` are Git-ignored: no jar and no class
file is committed. `fetch_libs.py` downloads only from `https://repo1.maven.org/maven2/` and
checks every jar against the SHA-1 Maven Central publishes beside it (pinned in the script);
`--check` only says what is there.

## The certification gates

```bash
python reference/web/caseboard-jsf/certify_local.py [--keep FOLDER]
python -m pytest lab/labctl/tests/test_reference_jsf.py -q
```

`flow.py` is the scripted session (`flow(page, base_url)`, Playwright's sync API),
`expected-actions.yaml` the actions it performs, written from the flow. `certify_local.py`
records the flow on one instance with the lab's recorder, then prints the three gates: DETECT
(framework, implementation, version, markers), CAPTURE (expected actions captured / missing) and
SELF-REPLAY against a fresh instance (steps, replayed, failed, the first failing step), with
the acceptance cases `flow.acceptance_for` declares from the application's rules. The test skips
where there is no JDK or `lib/` is not filled (CI).

## The downloaded jars

All from Maven Central; 14 files, 6,261,562 bytes together. The licence is the one each jar's POM
on Maven Central declares.

| Jar | Version | Licence | Why |
|---|---|---|---|
| `org.glassfish:javax.faces` | 2.3.9 | CDDL 1.1, or GPL 2.0 with the Classpath Exception | Mojarra: the JSF API and implementation |
| `org.glassfish:javax.el` | 3.0.0 | CDDL 1.1, or GPL 2.0 with the Classpath Exception | Expression Language API and implementation |
| `javax.servlet:javax.servlet-api` | 3.1.0 | CDDL 1.0, or GPL 2.0 with the Classpath Exception | Servlet API |
| `javax.servlet.jsp:javax.servlet.jsp-api` | 2.3.3 | CDDL 1.1, or GPL 2.0 with the Classpath Exception | API only: Mojarra asks for `JspFactory` when it starts |
| `javax.enterprise:cdi-api` | 1.2 | Apache 2.0 | API only: Mojarra 2.3 components name CDI types (no container runs) |
| `javax.inject:javax.inject` | 1 | Apache 2.0 | API only: named by `cdi-api` |
| `org.eclipse.jetty:jetty-server`, `-servlet`, `-webapp`, `-util`, `-http`, `-io`, `-security`, `-xml` | 9.4.57.v20241219 | Apache 2.0, or EPL 1.0 | the embedded server |

## Notes for whoever changes it

- Mojarra 2.3 demands a CDI `BeanManager` when `faces-config.xml` says `version="2.3"`; the
  descriptor says 2.2 on purpose. The pages and the script are those of JSF 2.3 all the same.
- Without annotation scanning, JSF is started by `com.sun.faces.config.ConfigureListener`,
  named in `web.xml`, and the beans are declared in `faces-config.xml`.
- `run.py` ends the whole process tree on Windows: `java` on PATH can be a launcher whose child
  is the real JVM.
- No header or page names the implementation's release (2.3.9): `jsf.js` says the specification
  (`jsf.specversion = 23000`) and the implementation by its namespace (`mojarra.*`).
