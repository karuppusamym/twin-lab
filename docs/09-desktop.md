# Desktop applications

Twin Lab records a person using a Windows desktop application, turns the recording into the app
model and a brief, packages a build task for a coding agent, and checks the rebuilt demo app
against the recording, the same pipeline as for web apps (`06-evidence.md`). Bots (UiPath,
pywinauto, WinAppDriver) find desktop controls by **UI Automation properties**: AutomationId,
Name, ControlType, ClassName, FrameworkId and the ancestor path. A desktop demo app must
reproduce those. It is built and installed on a Windows machine or VM: no container, no
OpenShift.

Code: `lab/labctl/labctl/desktop/`. Commands: `labctl desktop record | record-java | attach |
analyse | split | verify`. Reference targets: `reference/desktop/CaseDesk/` (.NET 6 WinForms),
`reference/desktop/CaseDeskWpf/` (the same application in .NET 6 WPF, with the same control
names) and `reference/desktop/CaseDeskSwing/` (Java 11 Swing).

## What a recording captures

A recording is a folder (under `evidence/<app>/`, Git-ignored):

| File | Holds |
|---|---|
| `desktop.json` | format, times, each process recorded (executable name, the toolkit detected and the signals behind it), what was listened to, polled or hooked, notes |
| `steps.jsonl` | one line per user action (below) |
| `trees/<seq>.json` | the value-free control tree of the step's window after the action |
| `screens/<seq>.png` | a picture of that window only, when asked for (`--screenshots`; off by default: pictures show data) |

A step:

```json
{"seq": 13, "ts": "2026-11-18T03:43:41.112Z", "surface": "desktop", "action": "type",
 "process": "CaseDesk.exe", "pid": 22796,
 "window": {"selector": "uia:AutomationId=MainForm;ControlType=Window", "title": "CaseDesk - Cases"},
 "target": {"selector": "uia:AutomationId=txtSearch;ControlType=Edit", "matches": 1,
            "path": ["uia:AutomationId=MainForm;ControlType=Window"],
            "candidates": [{"selector": "uia:Name=Case or name;ControlType=Edit", "matches": 1}]},
 "selector": "uia:AutomationId=txtSearch;ControlType=Edit", "typed_shape": "X-999",
 "via": "uia-value-event", "tree": "trees/0013.json", "url": null, "dom": null}
```

Actions: `click`, `double_click`, `right_click`, `type`, `select`, `toggle`, `expand`, `collapse`,
`menu`, `key`, `window_open`, `window_close`, `menu_open`, `focus`. A step can carry `effects`:
what the application did by itself because of it (a selection it made; see Limits), which is
not a step. `url` and `dom` are the web
recorder's fields, left empty, so desktop and web steps share one shape and one timeline
(`labctl.desktop.split.interleave`).

How each action is seen, and where UI Automation events were not enough:

| Action | UI Automation path (WinForms, WPF, UWP/WinUI, Win32/MFC, JavaFX) | Java path (Swing/AWT) |
|---|---|---|
| click, double/right click, menu | low-level mouse hook (`WH_MOUSE_LL`); the element is read **in the hook**, before the click is delivered (a dialog it opens would cover the point). The innermost element is often the caption (in WPF the TextBlock inside a Button, CheckBox or MenuItem): the control it belongs to is recorded | the same hook, which also notes where the window is (a click on OK closes a message box before the click is handled); the control under the point is found in the window's last snapshot, else asked from the bridge |
| click by a pattern (script, accessibility tool) | `Invoke_Invoked`, **only when the toolkit raises it**: WPF does (also for a mouse click, which is then recorded once), WinForms (.NET 6) does not. An Invoke within 0.4 s after a key press is what the key did (Enter on the default button): no click step, the key step names the control as `invoked` | not seen |
| type | `ValueProperty` change events while the control has keyboard focus (shape computed in the handler, value dropped), else the value is read once when focus leaves. A WPF PasswordBox does neither: there a character key pressed while it has the focus makes the `type` step (`via: keyboard-hook`; which key, and how many, is not kept) | the focused text field polled through the bridge every 300 ms |
| select | `SelectionItem_ElementSelected`, when the user's last input was on that item or in its container (else it is an effect of the step before it); a Win32 drop-down's items are read when its list window opens | the window tree compared with the one read before (a combo box shows another item): when a click or a key arrives (the tree is asked for in the hook; a change it already shows came before that input), after each batch of actions, and every 1.5 s when idle |
| toggle, expand, collapse | `ToggleState` / `ExpandCollapseState` change events; a click on a check box becomes a toggle with its state after | click on a check box; tree comparison for other changes |
| key | low-level keyboard hook: Enter, Tab, Esc, Delete, F-keys and chords with Ctrl or Alt. Plain characters are **never** recorded | same |
| window open / close / focus | polled every 250 ms (EnumWindows, GetForegroundWindow). A WPF `Popup` window (a menu's drop-down, a combo box's list) is not a window of the application: it gives `menu_open` or the combo box's items, and its steps belong to the window that owns it | same |

Only the recorded processes are read: the hooks check the process of the window under the mouse
or in the foreground first, and every event whose element belongs to another process is dropped.
Stop a recording with Ctrl+C or by closing the application.

## Privacy rule

The same rule as the web recorder: **typed values and data shown in controls are never stored**,
only their shape (letters `X`/`x`, digits `9`); structural text is kept. The raw tree exists only
in memory; `labctl.desktop.privacy.clean` reduces it before anything is written.

| Kept as structure | Kept only as shape |
|---|---|
| Names of Button, CheckBox, RadioButton, MenuItem, Menu, MenuBar, TabItem, Tab, Header / HeaderItem (column headers), Group, ToolBar, TitleBar, ScrollBar, SplitButton | every Value (ValuePattern); a password field keeps not even its shape |
| Text that is a label (AutomationId `lbl…`/`label…`, ends with `:`, or an input is named after it) | any other Text |
| Edit / ComboBox / List / Table … Names equal to a label or their own AutomationId | other Names of those (they may be their content) |
| the text of a message box (`#32770`, a Java `alert`/`option pane`): the application's own words | DataItem, ListItem, TreeItem, Hyperlink, Custom, Image; everything inside a row, list, tree or status bar |
| items of a drop-down list (no edit field): at most 30, each ≤ 40 characters, no digit | items of an editable combo box (the usual picker for people or accounts) |
| window titles, with every token holding a digit, everything after `:` and any word already seen as data masked (`Case C-1001` → `Case X-9999`) | |

In kept text, tokens with three or more digits are still masked. Accepted risk: a short
drop-down of names without digits would be kept as a code list. Module names are kept only when
a detection rule names them; folder paths never.

## Selectors

One canonical syntax, scoped to the window:

```
uia:AutomationId=txtCase;ControlType=Edit
uia:Name=Save;ControlType=Button
uia:ControlType=Edit;ClassName=WindowsForms10.Edit.app.0.*;Index=2      (with its ancestor path)
jab:Name=Save;Role=push button                                           (Java, through the Access Bridge)
```

Properties in the order AutomationId, Name, ControlType (or Role), ClassName, FrameworkId, Has,
Index; `;`, `=` and `\` in values are escaped with `\`. ClassName's per-build suffix is normalised
to `*` (`WindowsForms10.Edit.app.0.*`, `HwndWrapper[App;;*]`, `Afx:*`). Index counts siblings of
the same kind and is used only when nothing else identifies a control, always with the ancestor
path (window first). Numeric, GUID-like and masked AutomationIds are not used (they change per
run). The recorder picks the shortest locator that is unique in the window and keeps the others
as `candidates`; a control that no flat locator identifies is recorded with `ambiguous: true`.
`Has=<role>` (windows only) anchors a window on what it holds, for Java dialogs whose title and
class repeat (`jab:Role=dialog;Has=alert` is a message box).

| Canonical | UiPath | pywinauto (`backend="uia"`) | WinAppDriver |
|---|---|---|---|
| window `uia:AutomationId=MainForm;ControlType=Window` | `<wnd app='casedesk.exe' ctrlname='MainForm' />` | `app.window(auto_id="MainForm")` | top-level session |
| `uia:AutomationId=txtCase;ControlType=Edit` | `<uia automationid='txtCase' role='editable text' />` | `child_window(auto_id="txtCase", control_type="Edit")` | `accessibility id = txtCase` |
| `uia:Name=Save;ControlType=Button` | `<uia name='Save' role='push button' />` | `child_window(title="Save", control_type="Button")` | `//Button[@Name='Save']` |
| `…;Index=2` | `idx='3'` (UiPath counts from 1) | `found_index=2` | `[3]` |
| `jab:Name=Save;Role=push button` | `<wnd app='java.exe' cls='SunAwtDialog' title='…' /><java role='push button' name='Save' />` | not reachable | not reachable |

Each element in `model.yaml` carries all forms under `uia` (`uipath`, `pywinauto`,
`winappdriver`). Check a generated UiPath selector once in UI Explorer: UiPath may prefer
`<ctrl>` (MSAA) or `<wnd ctrlname>` on some WinForms controls.

## Toolkits

`labctl.desktop.detect` reads FrameworkIds, window classes, loaded module names, the .NET shared
runtime folders and the executable's version resource, and reports the toolkit with its marker
and confidence (certain / likely / guess), other toolkits seen, and every rule that fired, in
the shape of the web detection.

| Toolkit | Status |
|---|---|
| WinForms | **recorded and verified live** (CaseDesk) |
| WPF | **recorded and verified live** (CaseDeskWpf, .NET 6): sign-in with a PasswordBox, message boxes, menu, DataGrid, ComboBox, CheckBox, and a deliberately wrong build found by `verify`. Limits below |
| Java Swing / AWT | **recorded and verified live** through the Java Access Bridge (CaseDeskSwing) |
| UWP / WinUI, Win32 / MFC | same UI Automation path; detection and model proven with fixtures only, not on a live app |
| JavaFX | UI Automation (JavaFX is its own provider from 8u40); fixtures only |
| Electron / CEF | detected (fixtures only); **use the web recorder** (`labctl evidence attach` to `--remote-debugging-port`) |
| Delphi/VCL, Qt, VB6, PowerBuilder | detected and reported **not supported** (out of scope by decision) |
| Citrix / RDP / remote windows | detected: **no control tree — not supported by this recorder** (only pixels arrive; out of scope by decision) |

**Java Access Bridge.** The JVM must load it: for every Java program of the user with
`jabswitch -enable` (then restart the application; a person decides that, it changes their
profile), or for one run with
`-Djavax.accessibility.assistive_technologies=com.sun.java.accessibility.AccessBridge`, which
`labctl desktop record-java` and the tests use, so nothing persistent changes.
`WindowsAccessBridge-64.dll` is found in System32 or a JDK's `bin` (`TWINLAB_JAVA_HOME`). Java
gives no AutomationId: locators use Role and Name (from labels' `labelFor`), and Index.

## Model, brief, build task

`labctl brief <app>` finds desktop recordings under `evidence/<app>/` and builds the model
(`labctl.desktop.model`): each window or dialog is a Screen with `surface: desktop`; a message
box is its own screen, in scope when the window that opens it is; each control an Element with
the canonical selector and its UIA properties; menus with items, shortcuts and access keys;
grids as **candidate tables** (`assumed`, weakest, as the web HTML-grid inference), with a detail
window named after the table adding its fields as columns; drop-down items as code lists;
message boxes after actions as rules. The brief gets "Desktop windows and controls" (toolkit,
windows, controls with their UIA properties and paths, menus, tab order and shortcuts seen, what
was not observed). The database defaults to **SQLite**. The build task tells the agent to rebuild
in the same toolkit with the same AutomationIds, Names, ControlTypes, ClassNames and hierarchy,
write `build.ps1` producing a self-contained `dotnet publish` folder and a zip under
`apps/<app>/publish/` (MSI/MSIX optional), and set `run.desktop.exe` in `app.yaml`. No
Dockerfile, no app pipeline workflow.

## Recording and verifying

```powershell
pip install -e ".[desktop]"                       # comtypes; Windows only
labctl desktop record path\to\App.exe --app caseapp
labctl desktop attach --process App.exe --app caseapp
labctl desktop record-java --cp app.jar --main com.example.Main --app caseapp
labctl desktop analyse evidence\caseapp\desktop-<time> --app caseapp
labctl desktop split <recording>                   # several applications in one recording
labctl desktop verify caseapp --exe apps\caseapp\publish\caseapp\CaseApp.exe `
    --value txtPassword=wrong --value txtPassword=demo --value txtSearch=C-100
```

`verify` starts the app, replays the steps by their locators (UI Automation patterns, `BM_CLICK`
posted to buttons, keys posted to the focused control, or to its top-level window when the
control has no window of its own, as in WPF; for Java the bridge's actions, focus and
a posted space key: no mouse, no keyboard reaches another application), compares each step's
window with the recorded tree (missing, extra, changed, ambiguous, enabled, visibility, options,
value shape; grid rows by presence), closes the app, and writes the report in the web fidelity
format where the readiness gate reads it. Values the app checks (passwords, search keys) are
given with `--value`; others are synthesised from the recorded shape. An ambiguous locator is
reported, never resolved by taking the first match; one that was already ambiguous in the real
application counts only if the demo app has another number of matches.

The scripted evidence runs (`reference/desktop/*/flow.py`) stand in for a person: they type
through UI Automation / the bridge and press buttons with **real mouse clicks** (SendInput, after
checking the app owns the foreground window and the point; the cursor is put back), because
WinForms raises no Invoke event a recorder could hear for a pattern call. The WPF run types the
password with real key presses (`Driver.keys`, only while the password box has the keyboard
focus), because a PasswordBox set through its Value pattern raises nothing. The live tests run
only on request: `TWINLAB_DESKTOP_GUI=1 python -m pytest -m desktop_gui`.
A run prints `Windows fatal exception: code 0x8001010d` thread dumps: UI Automation raises and
handles that error itself and pytest's fault handler reports it. The lookups succeed; add
`-p no:faulthandler` for a quiet log.

## Limits

- **Event coverage differs by toolkit.** WinForms raises no `Invoke_Invoked`; clicks are
  therefore seen only through the mouse hook (a person's click is; a script's pattern call is
  not). Value events fire for WinForms and WPF edits; other toolkits may need the focus-change
  read.
- **WPF** (found on CaseDeskWpf):
  - A PasswordBox gives no value, no shape and no value event. Typing in it is seen only as
    key presses (a person's, or `Driver.keys`); a password set by a pattern call or pasted with
    the mouse is not recorded, and `verify` then has no step to type the password at.
  - WPF raises `SelectionItem_ElementSelected` also when the application selects a row itself
    (after Save the grid is filled again and the saved row selected). That is no user step: a
    selection is the user's only when the user's last input was on the item or in its list,
    grid, tree, combo box or tab control (a mouse press there, a key pressed while the focus
    was there, or the pattern action recorded just before, such as a script expanding a combo
    box). Any other selection is written as an `effects` entry of the step that caused it
    (the Save click), with its place and never its content; `verify` does not replay effects
    and does not count them. When the step that caused it was already written, the selection is
    only counted in the notes of `desktop.json`. The cost: a script that selects a row by a
    pattern call alone, with no input and no other action in that list before it, is taken
    for the application's doing. A row the user selects is still a `select` step whose item
    is data; `verify` does not replay it and reports it skipped.
  - The items of a drop-down menu are in the window's tree only while the menu is open.
    `verify` compares them only when the recorded and the live tree both show the menu open;
    an item is not reported missing or extra against a closed menu.
  - A window that is closing gives a tree without its content; a snapshot read while the
    window went away is dropped, so the step that closed a window may have no tree.
  - A value set within the recorder's window poll (250 ms) of a window opening is taken as the
    application filling the window in and is not recorded (any toolkit; a script meets it,
    a person does not). The scripted run waits after each window opens.
  - A grid row's Name is the row object's text (`ToString()`); it is kept as its shape, like
    every row.
- **Order within a batch** is by time: a mouse click has the time of the mouse press (also
  when its Invoke event arrives after the window poll saw the dialog it opened), a window the
  time the poll saw it. A click made by a pattern call has no mouse press; its Invoke event is
  put before a window it is the owner of when that window was seen up to 0.8 s earlier.
- **Snapshots come after a batch** (0.4 s of quiet): when actions follow each other faster, the
  after-tree may show a later state (for example a message box already answered). `verify`
  therefore does not compare enabled states when a modal dialog disables a window in one tree
  and not in the other.
- Grid and list rows beyond 25 per control are left out of snapshots and counted.
- Java: typing and selection are polled, so they are seen up to a second late. A selection is
  still written where it happened: the tree read when the next click or key arrives shows it,
  and it goes before that step. The exception: when that click itself changes the controls of
  the window before the tree is read (it opens a menu), the tree is not used and a selection
  made without mouse or keyboard just before it is written after the click. A combo box change
  made and undone within one poll is missed; keys are not replayed into Java windows by
  `verify`. A window's tree is read control by control while the application runs: a reading
  in which a child could not be read is read again (three times at most), a tree read while
  the window closes is dropped, and a tree read when idle is dropped when a click or a key
  arrived meanwhile, so an input is always located in a whole tree of the window.
- Window titles are masked by rule (digits, after `:`, words seen as data); a title that holds a
  name never seen elsewhere in the recording is kept.
- UWP/WinUI, Win32/MFC and JavaFX run through the same UI Automation code but were not
  recorded live here; they, and the Electron hand-off, are tested on fixtures only. Proven live:
  WinForms, WPF and Swing.
