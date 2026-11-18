# Build notes: casedesk (desktop demo app)

Built from the package `twinlab-casedesk-3b54897d.zip` (brief digest `sha256:b470b13ad64a3bc4`),
following its `BUILD_TASK.md`.

**Caveat, stated plainly:** the agent that wrote this app also wrote the reference application
the recording came from (`reference/desktop/CaseDesk`). It worked from the brief and the model,
but it could not forget the original, so this is not a blind test of the brief. Where the brief
was silent, the choices below say what a blind builder would have had to guess.

## What was built

- `src/`: a .NET 6 WinForms app (the toolkit and runtime "Stack to pin" names), three windows
  with every AutomationId, Name and ControlType the brief lists, all directly on their form (Path
  column "(window)"): `LoginForm` "Sign in", `MainForm` "CaseDesk - Cases" with `menuMain`
  (File > Exit; Case > Open case Ctrl+O, Refresh F5; Help), `txtSearch`, `btnSearch`, `btnOpen`,
  `dgvCases` (columns Case No, Customer, Status, Opened), `statusMain`; `CaseDetailForm`
  "Case <case no>" with `txtCaseNo` (read-only), `txtCustomer`, `txtSummary`, `cboStatus`
  (drop-down list: Open, In progress, Resolved, Closed, the brief's code list), `chkUrgent`,
  `btnSave`, `btnCancel`. Access keys as listed (Alt+u, Alt+p, Alt+n, Alt+c, Alt+m).
- Message boxes R1 ("Sign in": "Enter your user name and password.") and R2/R3 ("Case": a
  resolved or closed case needs a summary of at least 10 characters) after the same actions.
- Data: SQLite through Windows' own `winsqlite3.dll` (P/Invoke), so no NuGet package. Tables
  from `db/schema.sql` (copied next to the executable) on first start, four synthetic rows of the
  recorded shapes (`X-9999`, `9999-99-99`). Location from `DATABASE_URL` (`sqlite:///C:/...`),
  default `casedesk.db` next to the executable.
- `build.ps1`: `dotnet publish -c Release -r win-x64 --self-contained true` into
  `publish/casedesk/` and `publish/casedesk-win-x64.zip` (not committed). No MSI. `app.yaml`
  `run.desktop.exe` = `publish/casedesk/CaseDesk.exe`.

## Versions

.NET SDK 6.0.201, target `net6.0-windows`, self-contained runtime 6.0.x from the SDK's runtime
packs. No other dependency.

## Checks

| Check | Command | Result |
|---|---|---|
| Build | `powershell -File apps/casedesk/build.ps1` | pass |
| Contracts | `python -m labctl validate` | 28 file(s) checked, 0 problem(s) |
| Desktop verify | `python -m labctl desktop verify casedesk --exe apps/casedesk/publish/casedesk/CaseDesk.exe --value txtPassword=wrong --value txtPassword=demo --value txtSearch=C-100` against `evidence/casedesk/casedesk-flow` | **pass** (36 steps; focus, menu_open, expand and collapse steps skipped as covered by the steps around them) |
| Unit tests | `python -m pytest -q` | see the pull request |

## Assumptions (the brief does not say)

- **Credentials.** The recording shows a failed then a successful sign-in, but not which
  credentials are valid (they are never recorded). The demo accepts any user name and the
  synthetic password from `CASEDESK_DEMO_PASSWORD` (default `demo`); verify is given the values.
- **Status items.** The code list (Open, In progress, Resolved, Closed) is taken as complete.
- **Search** matches the case number or the customer, contains, case-insensitive; Enter
  searches (`AcceptButton`), from the Enter key seen in the main window.
- **Help menu**: its items were never opened; it is built empty.
- **Layout**: positions follow the recorded rectangles roughly; fonts and colours are the
  toolkit's defaults.
- **Cancel** buttons close their window (never pressed in the recording).

## Not checked

- Tab order (not observed in the recording); keyboard shortcuts Ctrl+O and F5 were seen as menu
  accelerators only, not pressed.
- A UiPath robot against the app (no robot here); the generated UiPath selectors are in
  `model.yaml` and were not tried in UI Explorer.
