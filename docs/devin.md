# Building demo apps with Devin

Twin Lab writes a build brief for each demo app (`apps/<app>/BRIEF.md`). Devin, an AI coding
agent, builds the app from it and opens a pull request in our GitHub repository. A person reviews
that pull request as the playbook describes (`docs/factory-playbook.md`, "For the reviewer").

There are two ways to hand a brief to Devin:

| | Manual path | Direct path |
|---|---|---|
| Needs | A Devin login | A Devin service-user API key and the organisation id |
| Who starts the session | A person, in the Devin web app | Twin Lab, through the Devin API |
| Who follows it | The person | Twin Lab polls it and records the pull request |
| Command | `labctl devin package <app>` | `labctl devin start <app>` |

Both use the same package and the same task text, and both are tracked as jobs in
`labctl devin status`. Start with the manual path; switch to the direct path once there is a key.

## 1. One-time setup

### 1.1 Get Devin and connect GitHub (both paths)

1. Get a Devin account for the office organisation at https://app.devin.ai.
2. In Devin, open **Settings > Connections > GitHub** and install the Devin GitHub App on the
   GitHub organisation or account that owns the Twin Lab repository. Give it access to that
   repository. Devin pushes branches and opens pull requests through this app.
3. This step cannot be done through the API, and `labctl devin check` cannot test it. If it is
   missing, Devin will say it cannot clone or push; the task tells it to stop and say so.

### 1.2 Create a service user and key (direct path only)

1. In Devin, open **Settings > Devin API > Service users** and create a service user for Twin Lab
   (for example `twinlab`). It needs permission to create and manage sessions and to upload
   attachments.
2. Create a key for it. It starts with `cog_`. Copy it once; Devin will not show it again.
3. The **organisation id** is shown on the same page. Copy it too.

Treat the key like a password. Do not paste it into chat, tickets, briefs or commits.

### 1.3 Put the settings where Twin Lab reads them

Either a file or the environment. The environment wins when both are set.

**File** (preferred on a shared office machine): copy `twinlab.local.example.yaml` at the
repository root to `twinlab.local.yaml` and fill it in. That file is Git-ignored.

```yaml
DEVIN_API_KEY: cog_...                # the service-user key
DEVIN_ORG_ID: ...                     # the organisation id
DEVIN_BASE_URL: https://api.devin.ai  # leave as is
GITHUB_REPO: your-org/twin-lab        # owner/name of the repository Devin works in
STATE_DIR: .lab-state                 # jobs and packages; Git-ignored
```

**Environment**: set the same names, e.g. in PowerShell `$env:DEVIN_API_KEY = "cog_..."`, or in
Bash `export DEVIN_API_KEY=cog_...`.

`GITHUB_REPO` is also used by the manual path, so set it even before you have a key.

### 1.4 Check

```bash
labctl devin check
```

It shows where each setting came from (the key masked), then makes one cheap authenticated call
(lists one session). It ends with `Devin API: key and organisation accepted.` or names the
problem; see [Troubleshooting](#5-troubleshooting).

## 2. The manual path, step by step

1. **Build the package.**

   ```bash
   labctl devin package <app> --repo your-org/twin-lab
   ```

   It writes `twinlab-<app>-<digest8>.zip` into `.lab-state/packages/` (or `--out <folder>`),
   records a *manual job* and prints its id. The zip holds:

   - `BUILD_TASK.md`: the task for Devin, step by step;
   - `apps/<app>/BRIEF.md`, `model.yaml`, `db/schema.sql`, `app.yaml`, `pages/*.html`;
   - `apps/<app>/look/`: the original style sheets, `theme.json`, a rendered picture per screen,
     the interface's images and `texts.json` (see [06-evidence.md](06-evidence.md));
   - `evidence/<app>/screenshots/*` if there are any;
   - `AGENTS.md` and `docs/factory-playbook.md`;
   - `MANIFEST.json`: every file with its sha256.

   It never contains HAR files, other evidence, `.env` files or `twinlab.local.yaml`. It refuses
   to build when `BRIEF.md` or `db/schema.sql` is missing: run `labctl brief` first.
   `<digest8>` changes whenever any packaged file changes, so an old zip is easy to spot.

2. **Open Devin** at https://app.devin.ai and start a new session.
3. **Attach the zip** to the first message (paperclip or drag and drop).
4. **Paste `BUILD_TASK.md`** from the zip, the whole file and unchanged, as the message text, and
   send it. It tells Devin which repository and branch to use, what to read, what not to touch,
   which checks to run and how to name the pull request.
5. **Wait for the pull request.** It usually takes a while. If Devin asks a question (for example
   because the brief contradicts itself), answer it in the session. Do not tell it to skip checks.
6. **Record the pull request in Twin Lab:**

   ```bash
   labctl devin attach-pr <job> https://github.com/your-org/twin-lab/pull/123 --session <Devin session URL>
   ```

   The job becomes `finished`. `labctl devin status` lists it with the others.

The pull request is titled `<app>: demo app from brief <digest>`; the reviewer checks it as the
playbook says and writes the outcome in `apps/<app>/BUILD-LOG.md`.

## 3. The direct path

```bash
labctl devin start <app> --repo your-org/twin-lab [--max-acu 50] [--wait]
```

Twin Lab builds the same package, uploads it to Devin, starts a session titled
`Twin Lab: <app> demo app (<digest>)` with tags `twinlab` and `app:<app>`, and records the job.
The task text is the same `BUILD_TASK.md`, plus an `ATTACHMENT:"<url>"` line pointing at the
uploaded zip.

- `--max-acu n` caps the session's spend (Devin ACUs).
- `--wait` keeps the command running and prints each change until the job stops. Without it the
  command returns at once; `labctl devin status <job>` asks Devin for the latest state.
- A long-running Twin Lab process (the portal) follows jobs in the background, every 30 seconds,
  and picks unfinished jobs up again after a restart.

Other commands:

```bash
labctl devin status [job] [--json]   # one job in detail, or all jobs
labctl devin message <job> "text"    # answer Devin, or resume a suspended session
labctl devin cancel <job>            # terminate the Devin session
```

A job id can be shortened to any unique prefix.

## 4. What a job's state means

| State | Meaning | What to do |
|---|---|---|
| `preparing` | Building the package | Wait |
| `uploading` | Sending the zip to Devin | Wait |
| `starting` | Creating the Devin session | Wait |
| `running` | Devin is working (Devin `new`, `claimed`, `resuming`, `running/working`); for a manual job, a person has the package | Wait |
| `waiting_for_user` | Devin needs a person: it asked a question, wants an approval, or paused for inactivity | Open the session URL, or `labctl devin message` |
| `finished` | Devin finished (`running/finished` or `exit`), or a manual job's pull request was recorded | Review the pull request. If `last error` says no pull request was opened, read the session |
| `failed` | Devin reported `error`, suspended the session for usage, credit or quota reasons, the session disappeared, or the hand-off failed before a session existed | Read `last error`; fix and start again |
| `cancelled` | Someone ran `labctl devin cancel` | Nothing |

`labctl devin status <job>` also shows Devin's own `status/status_detail`, the session URL, the
pull request URLs, ACUs used and timestamps.

If Twin Lab stopped while a job was `preparing`, `uploading` or `starting`, the job is marked
`failed` on the next start because it cannot tell whether Devin created the session. Look in the
Devin web app for a session with that title before starting again, so you do not pay twice.

## 5. Troubleshooting

| HTTP status | Usually means | Fix |
|---|---|---|
| no answer (shown as `0`) | Cannot reach `DEVIN_BASE_URL` | Check the URL, the office proxy and firewall |
| 400, 422 | Request rejected | Read the detail; report it with the job id |
| 401 | Key rejected | Use a current service-user key (`cog_...`); check for a copied space |
| 403 | Not allowed | Give the service user session permissions; check `DEVIN_ORG_ID` belongs to the key |
| 404 | Organisation or session not found | Check `DEVIN_ORG_ID`; the session may have been deleted |
| 409 | Session state does not allow it | e.g. messaging a terminated session; start a new job |
| 429 | Rate limited | Twin Lab waits and retries (up to 5 tries, honouring `Retry-After`); retry later if it still fails |
| 5xx | Devin server error | Retried automatically; try again later |

Other problems:

- **Devin cannot clone or push / no pull request appears.** The GitHub App is not connected to
  the repository (step 1.1). Connect it in Devin and send the session a message to continue.
- **`labctl devin start` says Devin is not configured.** `DEVIN_API_KEY` or `DEVIN_ORG_ID` is
  missing; run `labctl devin check`. Use the manual path meanwhile.
- **Suspended for credits or usage limits.** The job is `failed`. Top up or raise the limit in
  Devin, then `labctl devin message <job> "continue"` resumes it.
- **A create timed out.** Twin Lab does not retry session creation after a lost connection,
  because Devin has no idempotency key and a retry could start a second, paid session. Check the
  Devin web app first.

## 6. Trying it offline with the mock

A local stand-in for the Devin API runs the whole direct path without an account:

```bash
MOCK_API_KEY=cog_mock_key MOCK_STEP_SECONDS=5 python -m labctl.devin.mock --port 8740
```

Then set `DEVIN_BASE_URL=http://127.0.0.1:8740`, `DEVIN_API_KEY=cog_mock_key` and any
`DEVIN_ORG_ID`. Sessions go `new` -> `running/working` -> `running/finished` with a fake pull
request URL. Put `[mock:ask]` in the prompt or anywhere in the packaged files (for example a
scratch copy of a brief) to see `waiting_for_user`. The mock's pull request URLs
do not exist on GitHub.
