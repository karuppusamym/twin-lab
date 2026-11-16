# Setting up Twin Lab in the office

One machine runs Twin Lab: the portal, and the demo apps in Docker. People use it from their
browsers. Bots (UiPath, agents, Python scripts) run wherever they normally run and point at the
lab's addresses.

## 1. What the machine needs

| Need | Why | Check |
|---|---|---|
| Docker (Docker Desktop on Windows, Docker Engine on Linux) with Compose v2 | every demo app, its database and its lab agent run in containers | `docker compose version` |
| Python 3.11 or later | the `labctl` tool and the portal | `python --version` |
| Git, with access to the GitHub repository | demo apps arrive as pull requests | `git --version` |
| About 4 GB free memory per running demo app, 20 GB disk | images and databases | |
| Free ports: 8790 (portal), and per app the two ports in its `app.yaml` `lab:` | the app's front door and its database | |

Nothing here needs internet access at run time except Docker pulling base images the first time,
`pip install`, the browser download in step 2, and Devin.

## 2. Install

```bash
git clone git@github.com:karuppusamym/twin-lab.git
```

```bash
cd twin-lab
```

```bash
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`; Linux: `source .venv/bin/activate`), then:

```bash
pip install -e ".[dev]"
```

```bash
python -m playwright install chromium
```

Check everything is in place:

```bash
labctl validate
```

```bash
python -m pytest -q
```

## 3. Start the portal

For yourself only:

```bash
labctl portal
```

Open http://127.0.0.1:8790.

For colleagues on the network, set a password first (any user name works with it), then listen on
all interfaces:

```bash
export TWINLAB_PORTAL_PASSWORD='choose-a-long-one'
```

```bash
labctl portal --host 0.0.0.0
```

On Windows PowerShell use `$env:TWINLAB_PORTAL_PASSWORD = 'choose-a-long-one'`. The portal refuses
to listen beyond the machine without a password.

The lab apps listen on this machine only (`127.0.0.1`). When bots run on other machines:

- set `TWINLAB_LAB_BIND=0.0.0.0` so the lab ports listen on the network (add a firewall rule
  that allows only the bot machines), and
- set `TWINLAB_HOST` to this machine's name so the addresses handed to bots point here.

Set them once, in any shell that runs `labctl` or the portal. They are remembered in
`.lab-state/lab.yaml`, so the portal started later from another shell uses the same values.
Admin calls always go to `127.0.0.1`.

The lab agents' admin API (data, reset, conditions, sessions) needs a token. One is generated on
first use in `.lab-state/lab-token`; the portal and `labctl` send it, bots never need it. Set
`TWINLAB_LAB_TOKEN` only to choose your own; it is written to the same file. After changing any
of these, run `labctl lab up <app>` again so the lab picks it up.

Without a portal password, the portal answers only to this machine's own names (`127.0.0.1`,
`localhost` and its host name). This stops a web page on another site from driving it. If
people open it under another name, list that name in `TWINLAB_PORTAL_HOSTS`, or set a password.

Addresses handed to bots use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6
first and each connection then waits about 20 seconds, which looks like a slow app.

## 4. Devin

Without Devin access, use the manual path: each app page has **Download the Devin package**. Upload
it to Devin, paste `DEVIN_TASK.md` as the task, and record the pull request when Devin opens it.

With Devin access, put the key in `twinlab.local.yaml` next to the repository (never committed):

```yaml
DEVIN_API_KEY: cog_...        # Devin Settings > Devin API > Service users
DEVIN_ORG_ID: org-...
GITHUB_REPO: karuppusamym/twin-lab
```

Connect GitHub inside Devin (Settings > Connections > GitHub) so Devin can push. Then
**Settings > Check the connection** in the portal. Builds started from an app page run in Devin;
the portal follows them and shows the pull request. Details: [devin.md](devin.md).

## 5. The everyday flow

1. **Automations**: register each bot. For UiPath, upload the project as a zip and give the app
   it runs against; its screens, selectors, API calls and SQL are read from the XAML.
2. **Flows**: for anything with no bot code, write the flow down.
3. **App > Evidence**: add what you have. A recording is the most useful: **Record a flow** opens
   a browser on this machine against the real application (UAT or production, as your rules
   allow); go through the flow once and close the window. Add the DBA's DDL and any API
   description. Raw evidence stays in `evidence/` on this machine and is never committed.
4. **App > Brief**: generate. Read it. Every item says what it is based on.
5. **App > Build**: Devin builds the app and opens a pull request. Review and merge it on GitHub,
   then **Settings > Get the latest from GitHub**.
6. **App > Lab**: Start. The first start builds images and takes a few minutes.
7. **Readiness**: check each automation. READY, or BLOCKED with what is missing.
8. **App > Data**: load your own Excel data or synthetic data. Reset before each run.
9. **App > Conditions**: pick normal, peak-hour, slow-database, intermittent-errors or timeouts.
10. **App > Sessions**: start recording, run the bot as you normally would (bindings for UiPath
    are on the automation page), stop. Compare two sessions side by side.
11. **Automation > Robot runs**: upload the bot's log from production (Orchestrator export or NLog
    JSON lines). You see each run step by step with its waits. **Make a condition from these
    timings** turns production's slowness into a condition you can apply in the lab.
    **Compare** checks that the bot does the same in the lab as in production: screens, fields,
    values, requests and row changes (parity).
12. **Real data** (only when synthetic data is not enough): a masked slice of a replica, loaded
    into the lab with `labctl realdata load`. See [realdata.md](realdata.md).

The bots' database address uses the login `bot`. It may read and change rows, but it is not the
database owner, so a bot (or anyone who reaches the port) cannot run programs on the database,
read its files or drop tables. The owner's password is generated per machine
(`.lab-state/lab-db-password`) and only the app and the lab agent use it.

## 6. Command line equivalents

Everything in the portal is also a `labctl` command: `labctl --help`. The most used:

```bash
labctl automation analyse <automation> --write
```

```bash
labctl brief <app> --system "<real system name>" --db-engine postgres --force
```

```bash
labctl lab up <app>
```

```bash
labctl conformance <automation>
```

```bash
labctl bindings <automation> --form uipath
```

```bash
labctl realdata load <app> --in masked.json --map map.yaml --dry-run
```

```bash
labctl botlog trace robot.log.jsonl --automation automations/<automation> --out traces.json
```

## 7. When something is wrong

| Symptom | Look at |
|---|---|
| An app does not start | the job log on the app page; `docker compose -f .lab-state/compose/<app>.yml logs` |
| Readiness is BLOCKED on a screen step | the record in `test_inputs` may look different in the loaded data (a closed record hides its form); reset the data, or choose another record |
| A bot times out | a condition may be active (App > Conditions shows it) |
| Devin build stuck in `waiting_for_user` | open the session link and answer, or send a message from the app page |
| A bot is slow even with no condition | its address says `localhost`; use `127.0.0.1` (bindings do) |
| Reset says `LAB_BASELINE_MISMATCH` | the saved baseline does not match its label; take a baseline again (App > Data) |
| `labctl devin start` says a build is unfinished | follow or cancel that build first, or add `--force` |
| The portal says "unknown host name" | open it as http://127.0.0.1:8790, or add the name people use to `TWINLAB_PORTAL_HOSTS` (or set a portal password) |
| "Record a flow" says the window opens on the Twin Lab machine | record there, or save a HAR in your own browser (developer tools > Network > Save all as HAR) and upload it |
| A Devin build failed with 401 after you fixed the key | start it again: the new key is read at once, no restart needed |
| An upload fails with "path longer than 260 characters" | move the repository to a short folder such as `C:	l`, or turn on Windows long paths |
| A bot's database login fails after updating Twin Lab | run `labctl lab up <app>` once: it sets the generated passwords; the bindings now use the login `bot` |
| Port already in use | change `lab:` ports on the app page (Lab > Ports and test sign-in) |

## 8. What it does not do

- It does not run the bots; people trigger them as usual.
- A demo app is a copy built from evidence. A bot that is READY against it has been shown to find
  what it needs there, which is not the same as the real application. Each app and screen says
  how it was built (copy, reconstruction, skeleton, backend only, sample).
- One shared copy per app: people using the same app at the same time share its data and
  condition.
- The lab's databases are Postgres. Bots that query SQL Server or Oracle directly need their
  connection pointed at the lab's Postgres address (the bindings give it).
- Nothing fails a session on an unexpected change yet: read the row changes on the session page.
- Gaps still open, and how large they are: [07-prod-echo-coverage.md](07-prod-echo-coverage.md).
