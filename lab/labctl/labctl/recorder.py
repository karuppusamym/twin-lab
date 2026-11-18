"""Record a person (or a script) clicking through a web application, in the recording format.

A recording is a folder holding:

- `recording.har`: HAR 1.2 with content embedded, written by the browser;
- `dom/<seq>.html`: the page after a step, with the documents of its frames (`<tl-frame>`), its
  shadow roots open and closed (`<tl-shadow>`), and the OCR text of its canvases
  (`<tl-canvas-text>`) written in (password field values blanked);
- `dom/<seq>.before.html`: the page as the action met it (for an action inside a frame, that
  frame's document: `before_scope: "frame"`);
- `screens/<seq>.png`: what the window showed after the step; `screens/<seq>.cN.png`: each canvas;
- `docs/`: PDFs the pages showed, with their text and `documents.json` (form field names only);
- `steps.jsonl`: one JSON object per step: `{"seq", "ts", "url", "action":
  "goto|click|dblclick|type|select|submit|keypress|check|uncheck", "selector", "dom",
  "dom_before", "screenshot"}`, plus `frame` for an action inside a frame (its selector is then
  `iframe#x >>> button#y`, see labctl.locate), `locators` (ranked target candidates with the
  number of elements each matched), `key`/`modifiers` on a keypress, `nav` on a goto, `blank` on
  a type step whose value was empty or spaces, and `caused_by` on a submit or navigation that a
  click or key press caused (see labctl.botlog.recording).

Typing is one `type` step per field, written before the key press, click, submit, navigation or
blur that follows it, so "type, then Enter" is always two steps in that order. Only named keys
(Enter, Escape, Tab, arrows...) and chords (Ctrl+S) are key presses: the characters of typed text
never are. Two clicks and the double click they make are one `dblclick` step; a checkbox or radio
button is `check`/`uncheck`.

`record()` opens its own browser. `attach()` records a browser that is already running, such as
the one a robot drives, over the Chrome DevTools protocol.

The DOM is captured inside the page, after each navigation (full load or history change) and after
each click, change or submit, once the page has been quiet for `settle_ms`. Values typed are never
written: the recorder holds them in memory only, to take them out of snapshots, the HAR, pictures
(masked) and locator texts, and to tell which record (list item, table row) a later action is on:
a step on a record says "the record titled with what was typed at step N", and the replay takes
that title from the bindings (labctl.behaviour.bindings). The raw recording is evidence: it holds
whatever the application showed, so it belongs in `evidence/`, which Git ignores.

Playwright is needed (`pip install playwright` and `python -m playwright install chromium`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

LISTENER_JS = r"""
(() => {
  if (window.__tlInstalled) return;
  window.__tlInstalled = true;
  // Runs in every frame. The top page keeps the steps and takes the snapshots; a frame reports
  // its actions and changes to its parent with postMessage (which works across origins), each
  // parent adds the frame's selector, and the top page gets "iframe#x >>> button#save".
  const TOKEN = "__TOKEN__", TOP = window.top === window;
  const SETTLE = __SETTLE__, MAX_WAIT = 3000, JOIN = 150, NAV_JOIN = 1500, HOLD = 500;
  const SERIALIZE = __SERIALIZE__;
  // Installed after the page's own code ran (attaching to an open page): whether the page
  // listens for double clicks cannot be known, so clicks wait to see whether one follows.
  const LATE = document.readyState !== "loading";
  const FRAME_ID = Math.random().toString(36).slice(2, 10);
  let pending = [], timer = null, first = 0, lastAt = 0, tidSeq = 0;

  // Does the page listen for double clicks? Then a click waits a moment: two clicks and the
  // double click they make are one `dblclick` step, not three steps.
  let dblSeen = false;
  try {
    const add = EventTarget.prototype.addEventListener;
    EventTarget.prototype.addEventListener = function (type, fn, opts) {
      if (type === "dblclick") dblSeen = true;
      return add.call(this, type, fn, opts);
    };
  } catch (e) {}

  function selectorOf(el) {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    if (el.id) return tag + "#" + CSS.escape(el.id);
    const name = el.getAttribute("name");
    if (name) return tag + '[name="' + name + '"]';
    const classes = Array.from(el.classList).slice(0, 2).map((c) => "." + CSS.escape(c)).join("");
    return tag + classes;
  }
  function target(el) {
    const hit = el && el.closest && el.closest(
      "button, a, input, select, textarea, label, summary, [role=button], [role=link], [role=tab], [onclick]");
    return hit || el;
  }
  // Events from inside a shadow root reach the document retargeted to the host: the first
  // element of the composed path is the one actually used.
  function realTarget(e) {
    const path = e.composedPath ? e.composedPath() : [];
    return path.length && path[0].nodeType === 1 ? path[0] : e.target;
  }
  function snapshot() { return SERIALIZE({values: false}); }
  function up(msg) {
    try { window.parent.postMessage(Object.assign({__tl: TOKEN}, msg), "*"); } catch (e) {}
  }
  function frameOf(win) {
    for (const f of document.querySelectorAll("iframe, frame")) {
      try { if (f.contentWindow === win) return selectorOf(f); } catch (e) {}
    }
    return "iframe";
  }
  // A typed value goes to the recorder process only (each frame calls the binding itself, never
  // through postMessage, which the page's own scripts could read). It is kept in memory to take
  // typed values out of snapshots, the HAR and pictures, and is never written.
  function secret(tid, value) {
    try { if (window.__tlSecret) window.__tlSecret({tid, value}); } catch (e) {}
  }

  // --- which element: ranked locator candidates, each with how many elements it matched ------
  const STATE = /^(active|selected|completed|done|editing|checked|disabled|hover|focus|focused|open|opened|closed|show|shown|hidden|current|visible|expanded|collapsed|on|off|(is|has|ng|router-link)-.*)$/i;
  const ROW = "li, tr, [role=row], [role=listitem], [role=option], [role=treeitem]";
  const TESTID = ["data-testid", "data-test-id", "data-test", "data-cy", "data-qa"];
  const ROLE_SEL = {
    link: "a[href], [role=link]", button: "button, input[type=button], input[type=submit], input[type=reset], [role=button]",
    checkbox: "input[type=checkbox], [role=checkbox]", radio: "input[type=radio], [role=radio]",
    textbox: "input, textarea, [role=textbox]", searchbox: "input[type=search], [role=searchbox]",
    combobox: "select, [role=combobox]", listbox: "select, [role=listbox]", tab: "[role=tab]",
    menuitem: "[role=menuitem]", option: "[role=option]", switch: "[role=switch]",
  };
  const NAMED_BY_TEXT = new Set(["link", "button", "tab", "menuitem", "option", "checkbox", "radio", "switch"]);
  const norm = (t) => (t || "").replace(/\s+/g, " ").trim();
  const dataLike = (t) => (String(t).match(/\d/g) || []).length >= 3;  // a record number
  const hasDigit = (t) => /\d/.test(t);
  const q = (v) => JSON.stringify(String(v));
  function seg(el) {
    const cls = Array.from(el.classList).filter((c) => !STATE.test(c) && !dataLike(c)).slice(0, 2);
    return el.localName + cls.map((c) => "." + CSS.escape(c)).join("");
  }
  function shown(el) {
    if (!el.getClientRects().length) return false;
    return getComputedStyle(el).visibility !== "hidden";
  }
  function roleOf(el) {
    const r = el.getAttribute("role");
    if (r) return r.trim().split(/\s+/)[0];
    const tag = el.localName, type = (el.getAttribute("type") || "text").toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : null;
    if (tag === "button") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (tag !== "input") return null;
    if (["button", "submit", "reset"].includes(type)) return "button";
    if (type === "checkbox" || type === "radio") return type;
    if (type === "search") return "searchbox";
    if (["text", "email", "tel", "url"].includes(type)) return "textbox";
    return null;
  }
  function labelOf(el) {
    const al = norm(el.getAttribute("aria-label"));
    if (al) return al;
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = norm(lb.split(/\s+/).map((id) => {
        const n = document.getElementById(id); return n ? n.textContent : "";
      }).join(" "));
      if (t) return t;
    }
    if (el.labels && el.labels.length) {
      const t = norm(Array.from(el.labels).map((l) => l.textContent).join(" "));
      if (t) return t;
    }
    return "";
  }
  function nameOf(el, role) {
    const l = labelOf(el);
    if (l) return l;
    if (el.localName === "input" && role === "button") return norm(el.value);
    if (NAMED_BY_TEXT.has(role) && el.localName !== "input") {
      const t = norm(el.textContent);
      if (t) return t;
    }
    return norm(el.getAttribute("title"));
  }
  function count(sel, pred, visibleOnly) {
    let n = 0;
    for (const x of document.querySelectorAll(sel)) if ((!visibleOnly || shown(x)) && pred(x)) n++;
    return n;
  }
  function anchorOf(el) {  // the nearest ancestor with an id or test id of its own
    for (let n = el.parentElement; n; n = n.parentElement) {
      if (n === document.body || n === document.documentElement) break;
      if (n.id && !dataLike(n.id) && document.querySelectorAll("#" + CSS.escape(n.id)).length === 1) {
        return [n, "#" + CSS.escape(n.id)];
      }
      for (const a of TESTID) {
        const v = n.getAttribute(a);
        if (v && !dataLike(v)) {
          const s = "[" + a + "=" + q(v) + "]";
          if (document.querySelectorAll(s).length === 1) return [n, s];
        }
      }
    }
    return [document.body, "body"];
  }
  function pathFrom(stop, el) {
    const parts = [];
    for (let n = el; n && n !== stop && n.nodeType === 1; n = n.parentElement) parts.unshift(seg(n));
    return parts.join(" > ");
  }
  function rowOf(el) {  // the record (list item, table row) a control belongs to
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      if (n.matches(ROW)) return n;
      const p = n.parentElement;
      if (n !== el && p && p !== document.body) {
        const s = seg(n);
        if (s.includes(".") && Array.from(p.children).some((c) => c !== n && seg(c) === s)) return n;
      }
    }
    return null;
  }
  // A record's title: its text without the text of its controls (the same on replay).
  const rowTitle = __ROW_TITLE__;
  function locators(el) {
    if (!el || el.nodeType !== 1 || el.getRootNode() !== document) return {list: []};
    if (el === document.body || el === document.documentElement) return {list: []};
    const list = [];
    const row = rowOf(el);
    let strong = false;  // one candidate names this element alone, also among hidden ones
    const add = (c, all) => { list.push(c); if (c.count === 1 && all === 1) strong = true; };
    for (const a of TESTID) {
      const v = el.getAttribute(a);
      if (v && !dataLike(v)) {
        const n = document.querySelectorAll("[" + a + "=" + q(v) + "]").length;
        add({kind: "testid", attr: a, value: v, count: n}, n);
        break;
      }
    }
    if (el.id && !dataLike(el.id)) {
      const n = document.querySelectorAll("#" + CSS.escape(el.id)).length;
      add({kind: "id", value: el.id, count: n}, n);
    }
    const role = roleOf(el);
    const name = role ? nameOf(el, role) : "";
    if (role && name && !hasDigit(name) && name.length <= 80 && ROLE_SEL[role]) {
      const same = (x) => roleOf(x) === role && nameOf(x, role) === name;
      add({kind: "role", role, name, count: count(ROLE_SEL[role], same, true)}, count(ROLE_SEL[role], same, false));
    }
    const isField = /^(input|select|textarea)$/.test(el.localName);
    const label = isField ? labelOf(el) : "";
    if (label && !hasDigit(label) && label.length <= 80) {
      const n = count("input, select, textarea", (x) => labelOf(x) === label, false);
      add({kind: "label", text: label, count: n}, n);
    }
    const ph = isField ? norm(el.getAttribute("placeholder")) : "";
    if (ph && !hasDigit(ph) && ph.length <= 80) {
      const n = count("[placeholder]", (x) => norm(x.getAttribute("placeholder")) === ph, false);
      add({kind: "placeholder", text: ph, count: n}, n);
    }
    if (/^(a|button|summary)$/.test(el.localName)) {
      const t = norm(el.textContent);
      if (t && !hasDigit(t) && t.length <= 80) {
        const n = count(el.localName, (x) => norm(x.textContent) === t, false);
        add({kind: "text", tag: el.localName, text: t, count: n}, n);
      }
    }
    const [anchor, anchorSel] = anchorOf(el);
    const css = anchorSel + " > " + pathFrom(anchor, el);
    let matches = [];
    try { matches = Array.from(document.querySelectorAll(css)); } catch (e) {}
    const index = matches.indexOf(el);
    const structure = index >= 0 ? {kind: "structure", css, index, count: matches.length} : null;
    if (row && !strong) {
      // A control repeated once per record: only the record says which one. Candidates that
      // would pick one by position are left out, so a reordered list blocks instead.
      // The records: the row's list under the nearest identified ancestor, at any depth, so a
      // wrapper added around the list does not lose them.
      const raSel = anchorOf(row)[1], holder = row.parentElement;
      const rows = raSel + " " + (holder && holder !== document.body ? seg(holder) + " > " : "") + seg(row);
      const control = row === el ? "" : pathFrom(row, el);
      const title = rowTitle(row);
      let same = 0;
      try { for (const r of document.querySelectorAll(rows)) if (rowTitle(r) === title) same++; } catch (e) {}
      const ids = list.filter((c) => (c.kind === "id" || c.kind === "testid") && c.count === 1);
      return {list: ids.concat([{kind: "record", rows, control, count: same}]), title};
    }
    if (structure) list.push(structure);
    return {list};
  }

  // --- steps --------------------------------------------------------------------------------
  function flush() {
    clearTimeout(timer); timer = null;
    if (!TOP || !pending.length) return Promise.resolve();
    const events = pending; pending = []; first = 0;
    return window.__tlStep({events, url: location.href, ts: new Date().toISOString(),
                            html: snapshot()});
  }
  function quietAgain() {
    if (!TOP) { changed(); return; }
    if (!pending.length) return;
    clearTimeout(timer);
    if (Date.now() - first > MAX_WAIT) { flush(); return; }
    timer = setTimeout(flush, SETTLE);
  }
  let lastPing = 0;
  function changed() {  // a frame's page changed: the top page waits until it settles too
    if (Date.now() - lastPing > 100) { lastPing = Date.now(); up({kind: "mut"}); }
  }
  function joins(action) {
    // A click (or key press) and the submit or navigation it causes are one step; anything else
    // starts a new one, so what the page shows now is captured as the result of the previous step.
    const last = pending[pending.length - 1];
    if (!last) return false;
    const gap = Date.now() - lastAt;
    if (action === "submit") return (last.action === "click" || last.action === "keypress") && gap < JOIN;
    if (action === "goto") return ["click", "submit", "keypress"].includes(last.action) && gap < NAV_JOIN;
    return false;
  }
  function record(ev) {
    if (pending.length && !joins(ev.action)) flush();
    else if (pending.length) ev.caused = true;
    pending.push(ev);
    if (!first) first = Date.now();
    lastAt = Date.now();
    quietAgain();
  }
  // An event as the action met it: the time and (in the capture phase, before anything handled
  // it) the page; in a frame, the frame's own document.
  function make(action, el, extra) {
    const at = new Date().toISOString();  // before any work, so the action's requests come after
    const before = action === "goto" ? null : snapshot();
    const loc = action === "goto" ? {list: []} : locators(el);
    const ev = Object.assign({action, selector: el ? selectorOf(el) : null, before, at,
                              locators: loc.list, row_title: loc.title}, extra || {});
    return ev;
  }
  function emit(ev) {
    if (!TOP) {
      if (ev.action !== "goto") up(Object.assign({kind: "note", frames: []}, ev));
      return;
    }
    record(ev);
  }
  function note(action, el, extra) {
    releaseHeld();
    emit(make(action, el, extra));
  }
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || d.__tl !== TOKEN || !e.source || e.source === window) return;
    if (d.kind === "mut") { quietAgain(); return; }
    if (d.kind !== "note") return;
    const frames = [frameOf(e.source)].concat(d.frames || []);
    if (!TOP) { up(Object.assign({}, d, {frames})); return; }
    const ev = Object.assign({}, d, {selector: frames.join(" >>> ") + " >>> " + d.selector,
                                     frame: frames.join(" >>> "), before_scope: "frame"});
    delete ev.__tl; delete ev.kind; delete ev.frames;
    record(ev);
  });

  // --- clicks that may be the first half of a double click ---------------------------------
  let held = null;  // {el, events, timer}
  function releaseHeld() {
    if (!held) return;
    const h = held; held = null;
    clearTimeout(h.timer);
    for (const ev of h.events) emit(ev);
  }
  function holds(el) {
    if (!(dblSeen || LATE)) {
      let n = el, any = false;
      for (; n && n.nodeType === 1; n = n.parentElement) if (n.ondblclick || n.hasAttribute("ondblclick")) { any = true; break; }
      if (!any) return false;
    }
    const a = el.closest && el.closest("a[href]");
    if (a) {  // a link to another page is followed at once: the page would go before the wait ends
      try {
        const u = new URL(a.href, location.href);
        if (u.origin + u.pathname + u.search !== location.origin + location.pathname + location.search) return false;
      } catch (e) { return false; }
    }
    return true;
  }
  function onClick(el, e) {
    if (closedHost(el)) return;
    const t = target(el);
    if (!t || t.nodeType !== 1) return;
    // Enter in a field "clicks" the form's default button: the key press is the step.
    if (e && e.detail === 0 && lastKey && lastKey.key === "Enter" && Date.now() - lastKey.at < 300) return;
    const type = (t.getAttribute("type") || "").toLowerCase();
    if (t.localName === "label" && t.control && /^(checkbox|radio)$/.test(t.control.type)) return;  // its control's click follows
    if (t.localName === "input" && (type === "checkbox" || type === "radio")) {
      note(t.checked ? "check" : "uncheck", t);
      return;
    }
    if (!holds(t)) { note("click", t); return; }
    if (held && held.el === t && held.events.length < 2) {
      held.events.push(make("click", t));
      clearTimeout(held.timer);
      held.timer = setTimeout(releaseHeld, HOLD);
      return;
    }
    releaseHeld();
    if (TOP && pending.length) flush();  // the step before shows the page before this click
    held = {el: t, events: [make("click", t)], timer: setTimeout(releaseHeld, HOLD)};
  }
  function onDblClick(el) {
    const t = target(el);
    if (!t || t.nodeType !== 1) return;
    if (held && held.el === t) {
      const firstClick = held.events[0];
      clearTimeout(held.timer); held = null;
      emit(Object.assign(make("dblclick", t), {before: firstClick.before, at: firstClick.at,
                                               locators: firstClick.locators, row_title: firstClick.row_title}));
      return;
    }
    note("dblclick", t);
  }

  // --- typing: kept as one `type` step per field, written before whatever comes next --------
  // What a person typed, kept only as its shape (letters A, digits 9, runs collapsed), never the
  // value: a field that shows another shape than was typed has an input mask.
  const dirty = new Map();  // field -> {typed: text typed, or null when something was deleted}
  function shapeOf(text) {
    let out = "";
    for (const ch of text) {
      const c = /\p{L}/u.test(ch) ? "A" : /\d/.test(ch) ? "9" : ch;
      if (!out || out[out.length - 1] !== c || (c !== "A" && c !== "9")) out += c;
    }
    return out;
  }
  function typable(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.isContentEditable) return true;
    const tag = el.localName;
    if (tag === "textarea") return true;
    if (tag !== "input") return false;
    return !/^(checkbox|radio|button|submit|reset|image|hidden)$/.test((el.type || "").toLowerCase());
  }
  function onInput(e) {
    const el = realTarget(e);
    if (!typable(el)) return;
    const d = dirty.get(el) || {typed: ""};
    if (el.type === "password") d.typed = null;
    else if (e.inputType && e.inputType.startsWith("delete")) d.typed = null;
    else if (typeof e.data === "string" && d.typed !== null) d.typed += e.data;
    dirty.set(el, d);
  }
  const written = new WeakMap();  // field -> the value its last type step had (in memory only)
  const valueOf = (el) => el.isContentEditable ? el.innerText : String(el.value == null ? "" : el.value);
  function emitType(el) {
    const d = dirty.get(el);
    if (!d) return;
    dirty.delete(el);
    const value = valueOf(el);
    written.set(el, value);
    const tid = FRAME_ID + "-" + (++tidSeq);
    const extra = {tid};
    if (value.trim() === "") extra.blank = value.slice(0, 64);
    else {
      secret(tid, value);
      if (d.typed) extra.typed_shape = shapeOf(d.typed);
    }
    note("type", el, extra);
  }
  function emitAllTyped() { for (const el of Array.from(dirty.keys())) emitType(el); }
  function onChange(e) {
    const el = realTarget(e), tag = el.localName || "";
    if (tag === "select") { emitAllTyped(); note("select", el); return; }
    if (typable(el)) {
      // Written already at Enter or blur: nothing. A value set without input events (a robot's
      // simulated typing) is written now.
      // The change a browser fires after Enter carries what the page's own key handler made
      // of the value (a list clearing its input): that is no typing.
      const afterEnter = lastKey && lastKey.key === "Enter" && Date.now() - lastKey.at < 500;
      if (!dirty.has(el) && !afterEnter && written.get(el) !== valueOf(el)) dirty.set(el, {typed: null});
      emitType(el);
      return;
    }
    if (tag === "input" && /^(checkbox|radio)$/.test(el.type)) return;  // recorded as check/uncheck
    note("type", el);  // file, range, colour and other fields
  }
  function onSubmit(e) { emitAllTyped(); note("submit", realTarget(e)); }

  // --- keys: only named keys and chords, never the characters of typed text ---------------
  const KEYS = new Set(["Enter", "Escape", "Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                        "Home", "End", "PageUp", "PageDown"]);
  const EDITING_CHORDS = new Set(["a", "c", "v", "x", "z", "y"]);
  let lastKey = null;
  function onKey(e) {
    if (e.repeat || e.isComposing) return;
    const k = e.key;
    if (!k || /^(Shift|Control|Alt|Meta|AltGraph|CapsLock|Dead|Unidentified|Process)$/.test(k)) return;
    let key = k;
    const chord = (e.ctrlKey || e.metaKey) && !(e.ctrlKey && e.altKey);  // Ctrl+Alt is AltGr: text
    if (k.length === 1) {
      if (!chord) return;  // a character of typed text
      const code = e.code || "";
      key = code.startsWith("Key") ? code.slice(3).toLowerCase() : code.startsWith("Digit") ? code.slice(5) : null;
      if (!key) return;
    } else if (!KEYS.has(k) && !/^F\d{1,2}$/.test(k)) {
      return;  // Backspace, Delete and the like change the text: the type step has the result
    }
    const el = realTarget(e);
    if (chord && EDITING_CHORDS.has(key) && typable(el)) return;  // select all, copy, paste, undo
    const modifiers = [];
    if (e.ctrlKey) modifiers.push("Control");
    if (e.altKey) modifiers.push("Alt");
    if (e.metaKey) modifiers.push("Meta");
    if (e.shiftKey) modifiers.push("Shift");
    if (dirty.has(el)) emitType(el);
    lastKey = {key, at: Date.now()};
    note("keypress", el && el.nodeType === 1 ? el : null, {key, modifiers});
  }

  // A closed root hides its inside even from the event path: such a click reaches the document
  // as a click on the host, and the listener inside the root (below) records the real element.
  function closedHost(el) {
    return !!(el && el.nodeType === 1 && !el.shadowRoot && window.__tlShadows
              && window.__tlShadows.get(el));
  }
  document.addEventListener("click", (e) => onClick(realTarget(e), e), true);
  document.addEventListener("dblclick", (e) => { const el = realTarget(e); if (!closedHost(el)) onDblClick(el); }, true);
  document.addEventListener("input", onInput, true);
  document.addEventListener("keydown", onKey, true);
  document.addEventListener("focusout", (e) => { const el = realTarget(e); if (dirty.has(el)) emitType(el); }, true);
  document.addEventListener("change", onChange, true);
  document.addEventListener("submit", onSubmit, true);
  // change and submit do not leave a shadow root: listen inside every root (closed ones too, as
  // the shadow hook hands them over), and watch them for changes.
  const watched = new WeakSet();
  function watchRoot(root) {
    if (!root || watched.has(root)) return;
    watched.add(root);
    root.addEventListener("change", onChange, true);
    root.addEventListener("submit", onSubmit, true);
    if (root.mode === "closed") {
      root.addEventListener("click", (e) => onClick(e.target, e), true);
      root.addEventListener("dblclick", (e) => onDblClick(e.target), true);
      root.addEventListener("input", onInput, true);
      root.addEventListener("keydown", onKey, true);
    }
    new MutationObserver(quietAgain).observe(root, {subtree: true, childList: true,
                                                    attributes: true, characterData: true});
  }
  window.__tlOnShadow = watchRoot;
  (window.__tlShadowList || []).forEach(watchRoot);
  for (const el of document.querySelectorAll("*")) if (el.shadowRoot) watchRoot(el.shadowRoot);

  window.__tlFlush = () => { emitAllTyped(); releaseHeld(); return flush(); };
  window.addEventListener("pagehide", () => { emitAllTyped(); releaseHeld(); flush(); });
  if (TOP) {
    const NAV = {pushState: "push", replaceState: "replace"};
    for (const name of ["pushState", "replaceState"]) {
      const orig = history[name];
      history[name] = function (...args) {
        emitAllTyped();
        releaseHeld();
        // The old address still shows the old page: capture it before the address changes.
        if (pending.length && !joins("goto")) flush();
        const r = orig.apply(this, args);
        note("goto", null, {nav: NAV[name]});
        return r;
      };
    }
    window.addEventListener("popstate", () => { emitAllTyped(); note("goto", null, {nav: "pop"}); });
  }
  new MutationObserver(quietAgain).observe(document, {subtree: true, childList: true,
                                                      attributes: true, characterData: true});
  function loaded() {
    let nav = "navigate";
    try { nav = (performance.getEntriesByType("navigation")[0] || {}).type || nav; } catch (e) {}
    note("goto", null, {nav});
  }
  if (TOP) {
    if (document.readyState === "complete") loaded();
    else window.addEventListener("load", loaded);
  } else {
    changed();  // a frame that loads is a change of the page
  }
})();
"""


MODIFIERS = ("Control", "Alt", "Meta", "Shift")
LOCATOR_KINDS = {"testid", "id", "role", "label", "placeholder", "text", "structure", "record"}
LOCATOR_FIELDS = {"kind", "attr", "value", "role", "name", "text", "tag", "css", "index", "count",
                  "rows", "control"}  # fmt: skip

# Marks, in one frame, the fields and text that show a value typed there (for the picture mask).
MARK_TYPED_JS = r"""([values, check]) => {
  // check: count what shows a typed value and is not marked (the page changed since marking).
  if (!check) for (const el of document.querySelectorAll("[data-tl-mask]")) el.removeAttribute("data-tl-mask");
  const hit = (t) => {
    t = (t || "").toLowerCase();
    return values.some((v) => v.length >= 3 ? t.includes(v) : t.trim() === v);
  };
  let n = 0;
  const mark = (el) => {
    if (!check) { el.setAttribute("data-tl-mask", ""); n++; }
    else if (!el.closest("[data-tl-mask]") && el.getClientRects().length) n++;
  };
  for (const el of document.querySelectorAll("input, textarea")) if (hit(el.value)) mark(el);
  for (const el of document.querySelectorAll("[contenteditable]")) if (hit(el.innerText)) mark(el);
  if (document.body) {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) {
      const t = walk.currentNode;
      if (t.parentElement && hit(t.data)) mark(t.parentElement);
    }
  }
  return n;
}"""


def _origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") else ""


def _shape(text: str) -> str:
    from labctl.behaviour.dom import shape

    return shape(text) or ""


def _locators(raw) -> list[dict]:
    """The page's locator candidates, keeping only known kinds and fields."""
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict) or c.get("kind") not in LOCATOR_KINDS:
            continue
        cand = {k: v for k, v in c.items() if k in LOCATOR_FIELDS
                and isinstance(v, (str, int)) and not isinstance(v, bool)}  # fmt: skip
        out.append(cand)
    return out


class _Secrets:
    """Values typed while recording. Held in memory only; used to keep them out of everything
    written: snapshots, the HAR, pictures, locator texts. Blank values are not secrets."""

    def __init__(self) -> None:
        self.values: list[tuple[str, str, str]] = []  # (value, origin, type event id)
        self.steps: dict[str, int] = {}  # type event id -> seq

    def __bool__(self) -> bool:
        return bool(self.values)

    def add(self, value: str, origin: str, tid: str) -> None:
        if value.strip():
            self.values.append((value, origin, tid))

    def place(self, tid: str, seq: int) -> None:
        self.steps[tid] = seq

    def for_origin(self, origin: str) -> list[str]:
        return sorted({v.strip().lower() for v, o, _ in self.values if o == origin or not o})

    def typed_step(self, title: str) -> int | None:
        """The latest step whose typed value is this record title."""
        want = " ".join(title.split()).lower()
        if not want:
            return None
        for value, _, tid in reversed(self.values):
            if " ".join(value.split()).lower() == want and tid in self.steps:
                return self.steps[tid]
        return None

    def _forms(self) -> list[tuple[str, bool]]:
        """Each value in the forms it can take in markup, longest first; True when long enough
        to be taken out wherever it is (a short one only where it is the whole text)."""
        import html as html_mod

        seen: dict[str, bool] = {}
        for value, _, _ in self.values:
            for v in {value, value.strip(), html_mod.escape(value), html_mod.escape(value.strip())}:
                if v.strip():
                    seen[v] = len(v.strip()) >= 3
        return sorted(seen.items(), key=lambda kv: -len(kv[0]))

    def redact_text(self, text: str) -> str:
        import re

        for v, everywhere in self._forms():
            if everywhere:
                text = re.sub(re.escape(v), lambda m: _shape(m.group()), text, flags=re.I)
            elif text.strip().lower() == v.strip().lower():
                text = text.replace(text.strip(), _shape(text.strip()))
        return text

    def redact_html(self, html: str) -> str:
        """Typed values out of text and attribute values (tag and attribute names untouched)."""
        import re

        if not self.values or not html:
            return html
        html = re.sub(r">([^<]+)<", lambda m: ">" + self.redact_text(m.group(1)) + "<", html)
        return re.sub(r'="([^"]*)"', lambda m: '="' + self.redact_text(m.group(1)) + '"', html)

    def contains(self, text: str) -> bool:
        low = text.lower()
        for v, everywhere in self._forms():
            if (v.lower() in low) if everywhere else (low.strip() == v.strip().lower()):
                return True
        return False

    def clean_step(self, step: dict) -> dict:
        """Locator candidates whose text holds a typed value are dropped."""
        locs = step.get("locators")
        if not self.values or not locs:
            return step
        keep = [c for c in locs if not any(isinstance(v, str) and self.contains(v)
                                           for k, v in c.items() if k != "kind")]  # fmt: skip
        return {**step, "locators": keep}

    def scrub(self, value):
        """A HAR (parsed JSON) with typed values taken out of every string, also URL-encoded."""
        from urllib.parse import quote, quote_plus

        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if not isinstance(value, str) or not self.values:
            return value
        import re

        for raw, _, _ in self.values:
            for v in {raw, raw.strip(), quote(raw.strip()), quote_plus(raw.strip())}:
                if not v.strip():
                    continue
                if len(v.strip()) >= 3:
                    value = re.sub(re.escape(v), _shape(raw.strip()), value, flags=re.I)
                elif value == v:
                    value = _shape(v)
                else:  # a short value: only where it is a whole JSON string or form field
                    value = re.sub(r'(?<=")' + re.escape(v) + r'(?=")', _shape(v), value)
                    value = re.sub(r"(?<==)" + re.escape(v) + r"(?=&|$)", _shape(v), value)
        return value

    def scrub_har(self, path: Path) -> None:
        if not self.values or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        path.write_text(json.dumps(self.scrub(data)), encoding="utf-8")


class _Steps:
    """Receives captures from the page and writes `steps.jsonl`, `dom/` and `screens/`."""

    def __init__(self, out_dir: Path, screenshots: bool = True):
        self.out_dir = out_dir
        self.seq = 0
        self.screenshots = screenshots
        self.file = (out_dir / "steps.jsonl").open("w", encoding="utf-8")
        # Steps wait here for their picture: Playwright cannot be called from inside the binding
        # callback, so `drain()` takes the pictures when the recording loop gets control back.
        self.waiting: list[tuple[dict, object]] = []
        self.fetched: dict[str, bytes] = {}  # PDFs the pages embed: url -> bytes
        self.written: list[dict] = []  # steps as written, rewritten at close() with every secret
        # Typed values, in memory only (never written): they are taken out of snapshots, the HAR,
        # pictures and locator texts, and tell which record a later action is on.
        self.secrets = _Secrets()

    def on_secret(self, source, data: dict) -> None:
        """A typed value, from the frame it was typed in (`__tlSecret`)."""
        if not isinstance(data, dict) or not isinstance(data.get("value"), str):
            return
        frame = source.get("frame") if isinstance(source, dict) else None
        try:
            origin = _origin(frame.url) if frame is not None else ""
        except Exception:  # noqa: BLE001 - a frame that went away
            origin = ""
        self.secrets.add(data["value"], origin, str(data.get("tid") or ""))

    def drain(self, pictures: bool = True) -> None:
        """Finish the waiting steps: frames the page could not read, canvases, the picture.
        Called when the recording loop has control (never from inside the binding)."""
        from labctl.behaviour.dom import fill_frames

        waiting, self.waiting = self.waiting, []
        for step, page in waiting:
            html = step.pop("_html", None)
            live = pictures and page is not None
            if html is not None:
                if live:
                    try:
                        html = fill_frames(page, html, values=False)
                    except Exception:  # noqa: BLE001 - the page may have gone
                        pass
                    if self.screenshots:
                        html = self._canvases(page, html, step["seq"])
                    self._documents(page, html, step.get("url") or "")
                if "data-tl-pending=" in html:
                    step["frames_unread"] = html.count("data-tl-pending=")
                html = self.secrets.redact_html(html)
                (self.out_dir / step["dom"]).write_text(html, encoding="utf-8")
            if live and self.screenshots and step["dom"]:
                rel = f"screens/{step['seq']:04d}.png"
                try:
                    (self.out_dir / "screens").mkdir(exist_ok=True)
                    if self._picture(page, self.out_dir / rel):
                        step["screenshot"] = rel
                except Exception:  # noqa: BLE001 - a missing picture never loses the step
                    pass
            step = self.secrets.clean_step(step)
            self.written.append(step)
            self.file.write(json.dumps(step) + "\n")
        self.file.flush()

    def _picture(self, page, path: Path) -> bool:
        """The window, with what shows a typed value masked. The page may change between masking
        and the picture: then it is taken again, and kept only once nothing typed shows."""
        if not self.secrets:
            page.screenshot(path=str(path), timeout=5000)
            return True
        for _ in range(3):
            page.screenshot(path=str(path), timeout=5000, mask=self._masks(page))
            if not self._unmasked(page):
                return True
        path.unlink(missing_ok=True)
        return False

    def _unmasked(self, page) -> int:
        n = 0
        for frame in page.frames:
            try:
                values = self.secrets.for_origin(_origin(frame.url))
                n += frame.evaluate(MARK_TYPED_JS, [values, True]) if values else 0
            except Exception:  # noqa: BLE001 - a frame that went away
                continue
        return n

    def _masks(self, page) -> list:
        """Locators that cover what shows a typed value (a field, or text such as a new list
        item), so the picture never shows it. Each frame is given only the values typed in it."""
        if not self.secrets:
            return []
        out = []
        for frame in page.frames:
            try:
                values = self.secrets.for_origin(_origin(frame.url))
                if values and frame.evaluate(MARK_TYPED_JS, [values, False]):
                    out.append(frame.locator("[data-tl-mask]"))
            except Exception:  # noqa: BLE001 - a frame that went away
                continue
        return out

    def _documents(self, page, html: str, base: str) -> None:
        """PDFs the page embeds, fetched with the page's own sign-in (a browser without a PDF
        viewer, such as a headless one, never loads them itself)."""
        import re
        from urllib.parse import urljoin

        for tag in re.findall(r'<(?:embed|object|iframe)\b[^>]*data-tl-document="pdf"[^>]*>', html):
            m = re.search(r'\b(?:src|data)="([^"]+)"', tag)
            if not m:
                continue
            url = urljoin(base, m.group(1).replace("&amp;", "&"))
            if url in self.fetched:
                continue
            try:
                r = page.request.get(url, timeout=15000)
                if r.ok and r.body().startswith(b"%PDF"):
                    self.fetched[url] = r.body()
            except Exception:  # noqa: BLE001 - kept as a marked element without its document
                pass

    def _canvases(self, page, html: str, seq: int) -> str:
        """A picture of each canvas on the page, and the text OCR reads in it, written into the
        snapshot as <tl-canvas-text> after the canvas: drawn screens have no HTML of their own."""
        import re

        from labctl import ocr

        for cid in dict.fromkeys(re.findall(r'data-tl-canvas="(c\d+)"', html)):
            rel = f"screens/{seq:04d}.{cid}.png"
            try:
                (self.out_dir / "screens").mkdir(exist_ok=True)
                page.locator(f'[data-tl-canvas-id="{cid}"]').first.screenshot(
                    path=str(self.out_dir / rel), timeout=3000
                )
            except Exception:  # noqa: BLE001 - a canvas in a frame, or one that went away
                continue
            words = ocr.text(self.out_dir / rel) or ""
            from html import escape

            note = (f'<tl-canvas-text data-tl-for="{cid}" data-tl-image="{rel}" '
                    f'data-tl-ocr="{ocr.available() or "none"}">{escape(words)}</tl-canvas-text>')  # fmt: skip
            html = re.sub(rf'(<canvas[^>]*data-tl-canvas="{cid}"[^>]*>.*?</canvas>)',
                          lambda m, note=note: m.group(1) + note, html, count=1, flags=re.S)  # fmt: skip
        return html

    def on_capture(self, source, data: dict) -> None:
        # Playwright passes the binding's source as a dict: {"context", "page", "frame"}.
        page = source.get("page") if isinstance(source, dict) else None
        events = data.get("events") or []
        cause = None  # the seq of the click or key press a submit or navigation came from
        for n, event in enumerate(events):
            self.seq += 1
            dom = before = None
            if event.get("before"):
                before = f"dom/{self.seq:04d}.before.html"
                (self.out_dir / before).write_text(
                    self.secrets.redact_html(event["before"]), encoding="utf-8"
                )
            html = None
            if n == len(events) - 1:  # one snapshot, after the last event of the batch
                dom = f"dom/{self.seq:04d}.html"
                html = data.get("html", "")  # written by drain(), once frames are filled in
            # ts is when the action happened; the snapshot is taken once the page is quiet.
            step = {"seq": self.seq, "ts": event.get("at") or data.get("ts"), "url": data.get("url"),
                    "action": event.get("action"), "selector": event.get("selector"), "dom": dom,
                    "dom_before": before, "screenshot": None}  # fmt: skip
            if event.get("frame"):
                step["frame"] = str(event["frame"])  # the action happened inside this frame
                step["before_scope"] = "frame"  # its dom_before is that frame's document
            if event.get("typed_shape"):
                step["typed_shape"] = str(event["typed_shape"])[:64]
            if isinstance(event.get("blank"), str) and not event["blank"].strip():
                step["blank"] = event["blank"][:64]  # empty or spaces: not data, kept as it is
            if event.get("action") == "keypress" and isinstance(event.get("key"), str):
                step["key"] = event["key"][:32]
                step["modifiers"] = [m for m in event.get("modifiers") or [] if m in MODIFIERS]
            if event.get("action") == "goto" and isinstance(event.get("nav"), str):
                step["nav"] = event["nav"][:16]
            if event.get("caused") and cause is not None:
                step["caused_by"] = cause
            elif not event.get("caused"):
                cause = self.seq
            if event.get("tid"):
                self.secrets.place(str(event["tid"]), self.seq)
            locs = _locators(event.get("locators"))
            if locs:
                rec = next((c for c in locs if c["kind"] == "record"), None)
                if rec is not None:
                    title = event.get("row_title")
                    title = title if isinstance(title, str) else ""
                    # The record is the one whose title was typed at that step; its binding
                    # gives the title on replay. The title itself is never written.
                    rec["text_from"] = self.secrets.typed_step(title)
                    rec["title_shape"] = _shape(title)[:64]
                step["locators"] = locs
            if html is not None:
                step["_html"] = html
            self.waiting.append((step, page))

    def close(self) -> None:
        self.drain(pictures=False)
        self.file.close()
        if not self.secrets:
            return
        # A value typed late may have been on the page before it was typed: go over everything
        # once more with every value typed.
        steps = [self.secrets.clean_step(s) for s in self.written]
        (self.out_dir / "steps.jsonl").write_text(
            "".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8"
        )
        for path in (self.out_dir / "dom").glob("*.html"):
            text = path.read_text(encoding="utf-8")
            clean = self.secrets.redact_html(text)
            if clean != text:
                path.write_text(clean, encoding="utf-8")


class _Har:
    """A HAR 1.2 log built from network events, for a browser Twin Lab did not start (an
    existing context cannot be told to record a HAR). Bodies are embedded as text or base64."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def on_finished(self, request) -> None:
        from datetime import UTC, datetime

        try:
            response = request.response()
        except Exception:  # noqa: BLE001
            response = None
        timing = request.timing or {}
        total = max(0.0, float(timing.get("responseEnd", 0) or 0))
        start_ms = timing.get("startTime") or 0
        started = (
            datetime.fromtimestamp(start_ms / 1000, UTC) if start_ms > 0 else datetime.now(UTC)
        )
        entry = {
            "startedDateTime": started.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "time": total,
            "request": {
                "method": request.method,
                "url": request.url,
                "httpVersion": "HTTP/1.1",
                "headers": [{"name": k, "value": v} for k, v in request.headers.items()],
                "queryString": [],
                "cookies": [],
                "headersSize": -1,
                "bodySize": len(request.post_data or ""),
                **(
                    {
                        "postData": {
                            "mimeType": request.headers.get("content-type", ""),
                            "text": request.post_data,
                        }
                    }  # fmt: skip
                    if request.post_data
                    else {}
                ),
            },
            "response": {
                "status": response.status if response else 0,
                "statusText": response.status_text if response else "",
                "httpVersion": "HTTP/1.1",
                "headers": [
                    {"name": k, "value": v}
                    for k, v in (response.headers if response else {}).items()
                ],
                "cookies": [],
                "content": self._content(response),
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": -1,
            },
            "cache": {},
            "timings": {"send": 0, "wait": total, "receive": 0},
            "_resourceType": request.resource_type,
        }
        self.entries.append(entry)

    @staticmethod
    def _content(response) -> dict:
        import base64

        if response is None:
            return {"size": 0, "mimeType": ""}
        mime = response.headers.get("content-type", "")
        try:
            body = response.body()
        except Exception:  # noqa: BLE001 - redirects and aborted loads have no body
            return {"size": 0, "mimeType": mime}
        try:
            return {"size": len(body), "mimeType": mime, "text": body.decode("utf-8")}
        except UnicodeDecodeError:
            return {"size": len(body), "mimeType": mime, "text": base64.b64encode(body).decode(),
                    "encoding": "base64"}  # fmt: skip

    def write(self, path: Path) -> None:
        doc = {"log": {"version": "1.2", "creator": {"name": "twinlab-attach", "version": "1"},
                       "pages": [], "entries": self.entries}}  # fmt: skip
        path.write_text(json.dumps(doc), encoding="utf-8")


def _init_scripts(settle_ms: int) -> list[str]:
    """The shadow hook first (before the page's own code makes any shadow root), then the
    listener, with a fresh token so a page's own messages are never taken for a frame's."""
    import secrets

    from labctl.behaviour.dom import SERIALIZE_FN, SHADOW_HOOK_JS
    from labctl.locate import ROW_TITLE_FN

    listener = (LISTENER_JS.replace("__SETTLE__", str(int(settle_ms)))
                .replace("__TOKEN__", secrets.token_hex(8))
                .replace("__ROW_TITLE__", ROW_TITLE_FN)
                .replace("__SERIALIZE__", SERIALIZE_FN))  # fmt: skip
    return [SHADOW_HOOK_JS, listener]


class _Draining:
    """The page handed to a recording script: before each call, the steps recorded so far are
    finished (pictures, frames, canvases), so each step's picture shows the page as it settled,
    not the end of the script."""

    def __init__(self, page, steps: _Steps) -> None:
        self._page, self._steps = page, steps

    def __getattr__(self, name):
        attr = getattr(self._page, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self._steps.drain()
            return attr(*args, **kwargs)

        return call


PDF_TYPES = ("application/pdf", "application/x-pdf")


def save_documents(out_dir: Path, fetched: dict[str, bytes] | None = None) -> list[dict]:
    """PDFs the application showed, from the HAR (and any fetched while recording): each is kept
    as docs/NN.pdf with its text in docs/NN.txt, and docs/documents.json lists, per document, its
    address, page count and form field names (never the values typed into them)."""
    import base64

    found: dict[str, bytes] = dict(fetched or {})
    har_file = out_dir / "recording.har"
    try:
        entries = json.loads(har_file.read_text(encoding="utf-8"))["log"]["entries"]
    except (OSError, ValueError, KeyError, TypeError):
        entries = []
    for e in entries:
        content = (e.get("response") or {}).get("content") or {}
        url = (e.get("request") or {}).get("url") or ""
        mime = (content.get("mimeType") or "").split(";")[0].strip().lower()
        if mime not in PDF_TYPES and not url.lower().split("?")[0].endswith(".pdf"):
            continue
        text = content.get("text")
        if not text:
            continue
        body = (
            base64.b64decode(text)
            if content.get("encoding") == "base64"
            else text.encode("latin-1", "replace")
        )
        if body.startswith(b"%PDF"):
            found.setdefault(url, body)
    out = []
    for n, (url, body) in enumerate(found.items(), start=1):
        folder = out_dir / "docs"
        folder.mkdir(exist_ok=True)
        (folder / f"{n:02d}.pdf").write_bytes(body)
        info = {
            "url": url,
            "file": f"docs/{n:02d}.pdf",
            "pages": None,
            "fields": [],
            "text_chars": 0,
        }
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(body))
            info["pages"] = len(reader.pages)
            words = "\n".join((p.extract_text() or "") for p in reader.pages)
            (folder / f"{n:02d}.txt").write_text(words, encoding="utf-8")
            info["text_chars"] = len(words)
            fields = reader.get_fields() or {}
            info["fields"] = [{"name": k, "type": str(v.get("/FT", "")).lstrip("/")}
                              for k, v in fields.items()]  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - an unreadable PDF is kept, and said so
            info["problem"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        out.append(info)
    if out:
        (out_dir / "docs" / "documents.json").write_text(
            json.dumps(out, indent=1), encoding="utf-8"
        )
    return out


def attach(
    cdp_url: str,
    out_dir: Path,
    *,
    settle_ms: int = 400,
    max_seconds: float = 1800,
    screenshots: bool = True,
    stop: Callable[[], bool] | None = None,
) -> Path:
    """Record a browser that is already running (a robot's Chrome or Edge started with
    `--remote-debugging-port=9222`): pass its address, for example `http://127.0.0.1:9222`.

    Every page of its first context is recorded until the browser closes, `stop()` returns
    true, `max_seconds` pass, or Ctrl+C. Nothing is changed in the browser except the listener
    added to its pages; it is left running.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out_dir, screenshots)
    har = _Har()
    scripts = _init_scripts(settle_ms)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise RuntimeError(f"the browser at {cdp_url} has no open window")
            context = browser.contexts[0]
            context.expose_binding("__tlStep", steps.on_capture)
            context.expose_binding("__tlSecret", steps.on_secret)
            for s in scripts:
                context.add_init_script(s)
            context.on("requestfinished", har.on_finished)
            context.on("requestfailed", har.on_finished)
            # Pages already open get the listener now, in every frame. Closed shadow roots they
            # made before this moment cannot be reached; new ones are.
            for page in context.pages:
                for frame in page.frames:
                    for s in scripts:
                        try:
                            frame.evaluate(s)
                        except Exception:  # noqa: BLE001 - navigating: the init script runs
                            pass
            import time

            started = time.monotonic()
            try:
                while browser.is_connected() and time.monotonic() - started < max_seconds:
                    if stop is not None and stop():
                        break
                    pages = [p for p in context.pages if not p.is_closed()]
                    if not pages:
                        break
                    steps.drain()
                    pages[0].wait_for_timeout(500)
            except KeyboardInterrupt:
                pass
            for page in context.pages:
                try:
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                except Exception:  # noqa: BLE001
                    pass
            steps.drain()
            # Disconnect only: the robot's browser keeps running.
    finally:
        steps.close()
        har.entries = steps.secrets.scrub(har.entries)
        har.write(out_dir / "recording.har")
        save_documents(out_dir, steps.fetched)
    return out_dir


def record(
    url: str,
    out_dir: Path,
    script: Callable | None = None,
    headless: bool = False,
    *,
    settle_ms: int = 400,
    max_seconds: float = 1800,
    screenshots: bool = True,
    storage_state: dict | str | Path | None = None,
) -> Path:
    """Open `url` in Chromium and record until the page is closed (or `script(page)` returns).
    A window nobody closes is closed after `max_seconds`; what was recorded until then is kept.

    With `script`, the callback drives the page (for tests and scripted evidence runs); otherwise a
    person clicks through the flow in a headed browser and closes the window to finish. Returns
    the recording folder. `storage_state` (Playwright's format: cookies and localStorage per
    origin) is the browser data the recording starts from, such as a declared baseline.
    """
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    (out_dir / "dom").mkdir(parents=True, exist_ok=True)
    steps = _Steps(out_dir, screenshots)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                record_har_path=str(out_dir / "recording.har"),
                record_har_content="embed",
                record_har_mode="full",
                storage_state=str(storage_state)
                if isinstance(storage_state, Path)
                else storage_state,
            )
            context.expose_binding("__tlStep", steps.on_capture)
            context.expose_binding("__tlSecret", steps.on_secret)
            for s in _init_scripts(settle_ms):
                context.add_init_script(s)
            page = context.new_page()
            page.goto(url)
            if script is not None:
                # Each call the script makes first finishes the steps before it, so pictures show
                # each step as it settled, not the end of the script.
                script(_Draining(page, steps))
                page.wait_for_timeout(settle_ms + 200)
                if not page.is_closed():
                    page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                steps.drain()
            else:
                import time

                started = time.monotonic()
                while not page.is_closed():
                    steps.drain()  # the picture of each step, at most half a second late
                    if time.monotonic() - started > max_seconds:
                        page.evaluate("() => window.__tlFlush && window.__tlFlush()")
                        steps.drain()
                        print(
                            f"No one closed the window within {max_seconds / 60:.0f} min; stopped."
                        )
                        break
                    try:
                        page.wait_for_timeout(300)
                    except Exception:  # noqa: BLE001 - the window was closed while waiting
                        break
            context.close()  # writes the HAR
            browser.close()
    finally:
        steps.close()
        steps.secrets.scrub_har(out_dir / "recording.har")  # no typed value stays in the HAR
    save_documents(out_dir, steps.fetched)
    return out_dir
