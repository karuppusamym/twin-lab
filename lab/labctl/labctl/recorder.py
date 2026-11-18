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
  click or key press caused (see labctl.botlog.recording);
- every step also says where it happened: `tab` (t1, t2, ... in the order the tabs opened),
  `opener` (the tab that opened it, for a popup or a link with target=_blank) and `origin` (of
  the top-level page). A recording that used more than one tab has steps of its own for that:
  `tab-open`, `tab-switch` (the next action happened in another tab) and `tab-close`; all steps
  of all tabs are on one sequence, ordered by when they happened. `tabs.json` lists the tabs
  with their HAR page id (`pageref` of the HAR entries each tab made);
- `storage` on a step after which the page's browser storage changed: `localStorage` and
  `sessionStorage` as {key: masked shape} (every stored value parsed and each leaf replaced by a
  placeholder of its type; a credential-like key keeps no shape), `indexedDB` as database and
  store names, `cookies` as names only (and `cookies_by_domain`: the names of every cookie the
  browser holds, per domain), and `origin`. No stored value or cookie value is ever written;
- `mask_boxes` on a step whose picture had what shows a typed value painted over: each
  rectangle as {x, y, width, height} in CSS pixels of the viewport;
- `recording.json`: the browser and its version, the viewport (1280x720 for `record()`, the
  robot's own for `attach()`) and the device scale factor.

Typing is one `type` step per field, written before the key press, click, submit, navigation or
blur that follows it, so "type, then Enter" is always two steps in that order. Only named keys
(Enter, Escape, Tab, arrows...) and chords (Ctrl+S) are key presses: the characters of typed text
never are. Two clicks and the double click they make are one `dblclick` step; a checkbox or radio
button is `check`/`uncheck`.

`record()` opens its own browser. `attach()` records a browser that is already running, such as
the one a robot drives, over the Chrome DevTools protocol. Both follow every tab and popup of the
browser context; `attach()` cannot see what a tab did before it attached.

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

  // --- browser storage: key names and masked shapes, never a stored value -------------------
  // A value is parsed when it is JSON and every leaf replaced by a placeholder of its type (text
  // by its shape: letters A, digits 9), so only the record's structure leaves the page. A key that
  // looks like a credential keeps no shape at all. Cookies: names only.
  const SECRETISH = /token|secret|passw|api[-_]?key|session|cookie|auth|credential|jwt/i;
  const FIELDLIKE = /^[A-Za-z_$][\w$.-]{0,63}$/;
  function maskValue(v, depth) {
    if (v === null || v === undefined) return null;
    if (Array.isArray(v)) return depth > 6 ? [] : v.slice(0, 50).map((x) => maskValue(x, depth + 1));
    if (typeof v === "object") {
      if (depth > 6) return {};
      const out = {};
      for (const k of Object.keys(v).slice(0, 100)) {
        out[FIELDLIKE.test(k) && !dataLike(k) ? k : "*"] = maskValue(v[k], depth + 1);
      }
      return out;
    }
    if (typeof v === "number") return Number.isInteger(v) ? 0 : 0.5;  // the kind, not the number
    if (typeof v === "boolean") return false;
    return shapeOf(String(v)).slice(0, 32);
  }
  function maskStored(key, raw) {
    if (SECRETISH.test(key)) return "(credential-like key: value not kept)";
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) { return shapeOf(String(raw)).slice(0, 32); }
    return maskValue(parsed, 0);
  }
  let idb = null;  // [{db, stores}] from the last look (indexedDB answers asynchronously)
  function lookIdb() {
    try {
      if (!window.indexedDB || !indexedDB.databases) return;
      indexedDB.databases().then((list) => Promise.all(list.slice(0, 20).map((d) => new Promise((res) => {
        const done = (stores) => res({db: d.name, stores: stores || []});
        try {
          const r = indexedDB.open(d.name);
          r.onsuccess = () => {
            const db = r.result, stores = Array.from(db.objectStoreNames).slice(0, 20);
            db.close(); done(stores);
          };
          r.onerror = () => done();
          r.onblocked = () => done();
        } catch (e) { done(); }
      })))).then((x) => { idb = x.filter((d) => d.db); }, () => {});
    } catch (e) {}
  }
  function storageInfo() {
    const out = {};
    for (const kind of ["localStorage", "sessionStorage"]) {
      try {
        const s = window[kind], items = {};
        for (let i = 0; i < Math.min(s.length, 200); i++) {
          const k = s.key(i);
          if (k === null) continue;
          items[dataLike(k) ? shapeOf(k) : k] = maskStored(k, s.getItem(k));
        }
        if (Object.keys(items).length) out[kind] = items;
      } catch (e) {}
    }
    if (idb && idb.length) out.indexedDB = idb;
    try {
      const names = document.cookie.split(";").map((c) => c.split("=")[0].trim()).filter(Boolean);
      if (names.length) out.cookies = Array.from(new Set(names)).slice(0, 50);
    } catch (e) {}
    lookIdb();
    return out;
  }

  // --- steps --------------------------------------------------------------------------------
  function flush(leaving) {
    clearTimeout(timer); timer = null;
    if (!TOP || !pending.length) return Promise.resolve();
    const events = pending; pending = []; first = 0;
    // Leaving: the page is being replaced, so what it shows now is not what the action led to
    // (the next page is); its load is the next step, with its own snapshot.
    return window.__tlStep({events, url: location.href, ts: new Date().toISOString(),
                            html: snapshot(), storage: storageInfo(), leaving: !!leaving});
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
  window.addEventListener("pagehide", () => { emitAllTyped(); releaseHeld(); flush(true); });
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


STORAGE_KINDS = ("localStorage", "sessionStorage")
_SHAPE_TEXT = None  # compiled lazily: a text that is already a shape (A, 9 and punctuation)


def _mask_json(value, depth: int = 0):
    """The page masked stored values already; this keeps it so whatever a page sent: every text
    leaf that is not already a shape becomes its shape, numbers 0, booleans false."""
    import re

    global _SHAPE_TEXT
    if _SHAPE_TEXT is None:
        _SHAPE_TEXT = re.compile(r"^[A9\W_]*$|^\(credential-like key: value not kept\)$")
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {str(k)[:120]: _mask_json(v, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return [_mask_json(v, depth + 1) for v in value[:50]]
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0 if value.is_integer() else 0.5
    if isinstance(value, str):
        return value[:64] if _SHAPE_TEXT.match(value) else _shape(value)[:32]
    return None


def _storage(raw) -> dict | None:
    """Browser storage as the page reported it, keeping only the expected structure."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for kind in STORAGE_KINDS:
        items = raw.get(kind)
        if isinstance(items, dict) and items:
            out[kind] = {str(k)[:120]: _mask_json(v) for k, v in list(items.items())[:200]}
    dbs = []
    found = raw.get("indexedDB")
    for d in found if isinstance(found, list) else []:
        if isinstance(d, dict) and isinstance(d.get("db"), str) and d["db"]:
            stores = [str(s)[:120] for s in d.get("stores") or [] if isinstance(s, str)][:20]
            dbs.append({"db": d["db"][:120], "stores": stores})
    if dbs:
        out["indexedDB"] = dbs[:20]
    names = raw.get("cookies")
    if isinstance(names, list):
        clean = sorted({str(n)[:80] for n in names if isinstance(n, str) and n})[:50]
        if clean:
            out["cookies"] = clean
    return out or None


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


VIEWPORT = {"width": 1280, "height": 720}  # explicit, so pictures compare at a known size
DEVICE_SCALE_FACTOR = 1


def _boxes(masks: list) -> list[dict]:
    """The rectangles painted over in a picture (x, y, width, height in CSS pixels of the
    viewport), so a visual comparison can leave exactly those out."""
    out = []
    for loc in masks:
        try:
            for el in loc.all():
                box = el.bounding_box(timeout=1000)
                if box and box["width"] > 0 and box["height"] > 0:
                    out.append({k: round(float(box[k]), 1) for k in ("x", "y", "width", "height")})
        except Exception:  # noqa: BLE001 - an element that went away is not painted either
            continue
    return out[:200]


def write_meta(out_dir: Path, browser, viewport: dict | None, mode: str) -> None:
    """`recording.json`: how the recording was made (browser, viewport, scale), for replays and
    visual comparison. Optional: older recordings have none."""
    meta = {"recorder": "twinlab", "mode": mode, "viewport": viewport,
            "device_scale_factor": DEVICE_SCALE_FACTOR if viewport else None}  # fmt: skip
    try:
        meta["browser"] = browser.browser_type.name
        meta["browser_version"] = browser.version
    except Exception:  # noqa: BLE001
        pass
    (Path(out_dir) / "recording.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def _safe_url(page) -> str:
    try:
        url = page.url
    except Exception:  # noqa: BLE001 - a page that went away
        return ""
    return url if isinstance(url, str) and url.startswith(("http:", "https:")) else ""


def _guid(page) -> str:
    """The page's Playwright id: the `pageref` of the HAR entries it made."""
    impl = getattr(page, "_impl_obj", None)
    return str(getattr(impl, "_guid", None) or f"page-{id(page)}")


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
        """Locator candidates whose text holds a typed value are dropped; a typed value in the
        page address (its query, fragment or a whole path segment) or in a storage key is
        replaced by its shape."""
        if not self.values:
            return step
        out = dict(step)
        locs = step.get("locators")
        if locs:
            out["locators"] = [c for c in locs if not any(isinstance(v, str) and self.contains(v)
                                                         for k, v in c.items() if k != "kind")]  # fmt: skip
        if isinstance(step.get("url"), str):
            out["url"] = self.redact_url(step["url"])
        store = step.get("storage")
        if isinstance(store, dict):
            store = dict(store)
            for kind in STORAGE_KINDS:
                if isinstance(store.get(kind), dict):
                    store[kind] = {self.redact_text(k): v for k, v in store[kind].items()}
            out["storage"] = store
        return out

    def redact_url(self, url: str) -> str:
        """Typed values out of an address: from its query and fragment wherever they are, from
        its path only where a whole segment is one (a word typed somewhere must not change the
        address of a page that happens to contain it)."""
        from urllib.parse import unquote, urlsplit, urlunsplit

        if not self.values or not url:
            return url
        p = urlsplit(url)
        wanted = {" ".join(v.split()).lower() for v, _, _ in self.values}
        segs = [_shape(unquote(s)) if " ".join(unquote(s).split()).lower() in wanted else s
                for s in p.path.split("/")]  # fmt: skip
        query = self.scrub(p.query) if p.query else p.query
        frag = self.scrub(p.fragment) if p.fragment else p.fragment
        return urlunsplit((p.scheme, p.netloc, "/".join(segs), query, frag))

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
        # Every tab and popup of the context: page id -> {"tab", "opener", "har_page", ...}.
        self.tabs: dict[str, dict] = {}
        self.pages: dict[str, object] = {}  # page id -> the page, to find openers
        self.closing = False  # set before the browser is closed: those closes are not steps
        self.last_storage: dict[str, str] = {}  # tab -> the storage last written for it
        self.last_boxes: list[dict] = []  # the mask rectangles of the last picture taken
        self.tab_last: dict[str, tuple] = {}  # tab -> (seq, action, caused_by, ts) of its last step

    LOAD_CAUSES = ("click", "submit", "keypress", "dblclick")

    def _loaded_by(self, tab: str | None, event: dict) -> int | None:
        """The step that loaded a new document in this tab: a page load that comes within a few
        seconds of a click, key press or submit in the same tab (a form posted, a link followed)
        was caused by it, as the listener says for a change of address without a load."""
        from labctl.botlog.recording import _ts

        if event.get("action") != "goto" or event.get("nav") != "navigate":
            return None
        last = self.tab_last.get(tab or "")
        if not last or last[1] not in self.LOAD_CAUSES:
            return None
        before, now = _ts(last[3]), _ts(event.get("at"))
        if before is None or now is None or not 0 <= (now - before).total_seconds() < 10:
            return None
        return last[2] if isinstance(last[2], int) else last[0]

    # --- tabs ------------------------------------------------------------------------------------

    def tab_of(self, page) -> str | None:
        """The tab id of a page (t1, t2... in the order they opened); registers it if new."""
        if page is None:
            return None
        key = _guid(page)
        if key not in self.tabs:
            self.on_page(page, quiet=True)
        return self.tabs[key]["tab"]

    def on_page(self, page, quiet: bool = False) -> None:
        """A new tab or popup (`context.on("page")`). Only attributes are read here: Playwright
        is not called from inside an event handler."""
        key = _guid(page)
        if key in self.tabs:
            return
        opener_impl = getattr(getattr(page, "_impl_obj", None), "_opener", None)
        opener_key = str(getattr(opener_impl, "_guid", "")) if opener_impl is not None else ""
        opener = self.tabs.get(opener_key, {}).get("tab")
        info = {"tab": f"t{len(self.tabs) + 1}", "opener": opener, "har_page": key,
                "opened": _now(), "closed": None, "first_url": None}  # fmt: skip
        self.tabs[key] = info
        self.pages[key] = page
        try:
            page.on("close", lambda _p=None, page=page: self.on_page_close(page))
        except Exception:  # noqa: BLE001 - a page that is already gone
            pass
        if len(self.tabs) > 1 and not quiet:
            self._tab_step("tab-open", info, url=_safe_url(page))

    def on_page_close(self, page) -> None:
        info = self.tabs.get(_guid(page))
        if info is None or info["closed"]:
            return
        info["closed"] = _now()
        if not self.closing:
            self._tab_step("tab-close", info, url=info.get("last_url") or "")

    def _tab_step(self, action: str, info: dict, url: str = "") -> None:
        self.seq += 1
        step = {"seq": self.seq, "ts": _now(), "url": url, "action": action, "selector": None,
                "dom": None, "dom_before": None, "screenshot": None, "tab": info["tab"]}  # fmt: skip
        if info.get("opener"):
            step["opener"] = info["opener"]
        if url:
            step["origin"] = _origin(url)
        self.waiting.append((step, None))

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
            if live and isinstance(step.get("storage"), dict) and step.get("url"):
                try:  # HttpOnly cookies too, by name; a cookie's value is never read
                    names = {c["name"] for c in page.context.cookies([step["url"]])}
                    names |= set(step["storage"].get("cookies") or [])
                    if names:
                        step["storage"]["cookies"] = sorted(names)[:50]
                    # every site's cookies the browser holds, by name, per domain
                    per: dict[str, set] = {}
                    for c in page.context.cookies():
                        per.setdefault(str(c.get("domain") or ""), set()).add(str(c["name"]))
                    if per:
                        step["storage"]["cookies_by_domain"] = {
                            d: sorted(n)[:50] for d, n in sorted(per.items())[:20]
                        }
                except Exception:  # noqa: BLE001 - the page may have gone
                    pass
            if live and self.screenshots and step["dom"]:
                rel = f"screens/{step['seq']:04d}.png"
                try:
                    (self.out_dir / "screens").mkdir(exist_ok=True)
                    if self._picture(page, self.out_dir / rel):
                        step["screenshot"] = rel
                        if self.last_boxes:  # painted over: visual comparison leaves them out
                            step["mask_boxes"] = self.last_boxes
                except Exception:  # noqa: BLE001 - a missing picture never loses the step
                    pass
            step = self.secrets.clean_step(step)
            self.written.append(step)
            self.file.write(json.dumps(step) + "\n")
        self.file.flush()

    def _picture(self, page, path: Path) -> bool:
        """The window, with what shows a typed value masked. The page may change between masking
        and the picture: then it is taken again, and kept only once nothing typed shows."""
        self.last_boxes = []
        if not self.secrets:
            page.screenshot(path=str(path), timeout=5000)
            return True
        for _ in range(3):
            masks = self._masks(page)
            page.screenshot(path=str(path), timeout=5000, mask=masks)
            if not self._unmasked(page):
                self.last_boxes = _boxes(masks)
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
        tab = self.tab_of(page)
        info = self.tabs.get(_guid(page)) if page is not None else None
        if info is not None and isinstance(data.get("url"), str):
            info["first_url"] = info["first_url"] or data["url"]
            info["last_url"] = data["url"]
        storage = _storage(data.get("storage"))
        if storage is not None:
            storage["origin"] = _origin(data.get("url") or "")
            seen = json.dumps(storage, sort_keys=True)
            storage = None if self.last_storage.get(tab or "") == seen else storage
            self.last_storage[tab or ""] = seen
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
            # One snapshot, after the last event of the batch; none when the page was leaving
            # (it shows the page the action left, not the one it led to: that is the next step's).
            if n == len(events) - 1 and not data.get("leaving"):
                dom = f"dom/{self.seq:04d}.html"
                html = data.get("html", "")  # written by drain(), once frames are filled in
            # ts is when the action happened; the snapshot is taken once the page is quiet.
            step = {"seq": self.seq, "ts": event.get("at") or data.get("ts"), "url": data.get("url"),
                    "action": event.get("action"), "selector": event.get("selector"), "dom": dom,
                    "dom_before": before, "screenshot": None}  # fmt: skip
            if tab:
                step["tab"] = tab
                if info and info.get("opener"):
                    step["opener"] = info["opener"]
            if isinstance(data.get("url"), str) and _origin(data["url"]):
                step["origin"] = _origin(data["url"])
            if n == len(events) - 1 and storage is not None:
                step["storage"] = storage  # the storage once the page had settled after it
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
            elif n == 0 and self._loaded_by(tab, event) is not None:
                # A new document: its page's listener cannot know what loaded it, the tab can.
                step["caused_by"] = cause = self._loaded_by(tab, event)
            elif not event.get("caused"):
                cause = self.seq
            self.tab_last[tab or ""] = (step["seq"], step["action"], step.get("caused_by"),
                                        step["ts"])  # fmt: skip
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
        steps = self.written
        several = len(self.tabs) > 1
        if several:
            steps = self._in_order(steps)
        else:  # one tab: no tab steps (a single page's recording reads as it always did)
            steps = [s for s in steps if not str(s.get("action") or "").startswith("tab-")]
        renumbered = self._renumber(steps)
        if several or renumbered or len(steps) != len(self.written) or self.secrets:
            # A value typed late may have been on the page before it was typed: go over
            # everything once more with every value typed.
            steps = [self.secrets.clean_step(s) for s in steps]
            (self.out_dir / "steps.jsonl").write_text(
                "".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8"
            )
        self.written = steps
        if several:
            rows = []
            for info in self.tabs.values():
                row = {k: info.get(k) for k in ("tab", "opener", "har_page", "opened", "closed")}
                row["first_url"] = self.secrets.redact_url(info.get("first_url") or "")
                rows.append(row)
            (self.out_dir / "tabs.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        if not self.secrets:
            return
        for path in (self.out_dir / "dom").glob("*.html"):
            text = path.read_text(encoding="utf-8")
            clean = self.secrets.redact_html(text)
            if clean != text:
                path.write_text(clean, encoding="utf-8")

    def _in_order(self, steps: list[dict]) -> list[dict]:
        """All tabs on one sequence: each tab's steps stay in the order that tab made them, and
        the tabs are merged by when each step happened (a tab's batch can reach the recorder
        after a later batch of another tab). Where the next action is in another tab than the
        one before, a `tab-switch` step says so."""
        from labctl.botlog.recording import _ts

        queues: dict[str, list[tuple[int, dict]]] = {}
        for n, s in enumerate(steps):
            queues.setdefault(str(s.get("tab") or ""), []).append((n, s))
        last_time: dict[str, object] = {}

        def when(tab: str):
            n, s = queues[tab][0]
            t = _ts(s.get("ts")) or last_time.get(tab)
            return (t is None, t or 0, n)

        merged: list[dict] = []
        while any(queues.values()):
            tab = min((t for t in queues if queues[t]), key=when)
            _, s = queues[tab].pop(0)
            if _ts(s.get("ts")):
                last_time[tab] = _ts(s.get("ts"))
            merged.append(s)
        out: list[dict] = []
        current, seen = None, set()
        for s in merged:
            action, tab = str(s.get("action") or ""), s.get("tab")
            if action.startswith("tab-"):
                if action == "tab-open":
                    seen.add(tab)  # the new tab has the focus: no switch to it
                    current = tab
                out.append(s)
                continue
            if current is not None and tab and tab != current and tab in seen:
                out.append({"seq": None, "ts": s.get("ts"), "url": s.get("url"),
                            "action": "tab-switch", "selector": None, "dom": None,
                            "dom_before": None, "screenshot": None, "tab": tab,
                            "from_tab": current, **({"origin": s["origin"]} if s.get("origin") else {})})  # fmt: skip
            if tab:
                seen.add(tab)
                current = tab
            out.append(s)
        return out

    def _renumber(self, steps: list[dict]) -> bool:
        """Number the steps 1, 2, 3 in their final order, renaming their files and changing every
        reference to a step number (`caused_by`, a record's `text_from`). True if any changed."""
        import re

        mapping: dict[int, int] = {}
        for n, s in enumerate(steps, 1):
            if isinstance(s.get("seq"), int):
                mapping[s["seq"]] = n
        if all(old == new for old, new in mapping.items()) and all(
            isinstance(s.get("seq"), int) for s in steps
        ):
            return False
        moves: list[tuple[Path, Path]] = []
        for n, s in enumerate(steps, 1):
            old = s.get("seq")
            s["seq"] = n
            if isinstance(old, int) and old != n:
                for key in ("dom", "dom_before", "screenshot"):
                    rel = s.get(key)
                    if isinstance(rel, str) and rel:
                        new = re.sub(rf"(^|/){old:04d}\.", rf"\g<1>{n:04d}.", rel)
                        moves.append((self.out_dir / rel, self.out_dir / new))
                        s[key] = new
                for pic in (self.out_dir / "screens").glob(f"{old:04d}.c*.png"):
                    moves.append((pic, pic.with_name(f"{n:04d}." + pic.name.split(".", 1)[1])))
                if isinstance(s.get("dom"), str):
                    s["_canvas_from"] = old
            if isinstance(s.get("caused_by"), int):
                if s["caused_by"] in mapping:
                    s["caused_by"] = mapping[s["caused_by"]]
                else:
                    s.pop("caused_by")
            for cand in s.get("locators") or []:
                if isinstance(cand.get("text_from"), int):
                    cand["text_from"] = mapping.get(cand["text_from"])
        staged = []
        for src, dst in moves:  # in two moves, so no file is overwritten by another
            if src.exists():
                tmp = src.with_name(src.name + ".tlmove")
                src.replace(tmp)
                staged.append((tmp, dst))
        for tmp, dst in staged:
            tmp.replace(dst)
        for s in steps:
            old = s.pop("_canvas_from", None)
            path = self.out_dir / s["dom"] if old is not None else None
            if path is not None and path.exists():
                text = path.read_text(encoding="utf-8")
                fixed = text.replace(f"screens/{old:04d}.", f"screens/{s['seq']:04d}.")
                if fixed != text:
                    path.write_text(fixed, encoding="utf-8")
        return True


class _Har:
    """A HAR 1.2 log built from network events, for a browser Twin Lab did not start (an
    existing context cannot be told to record a HAR). Bodies are embedded as text or base64."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.pages: list[dict] = []  # one HAR page per tab, as Playwright's own HAR has

    def on_finished(self, request) -> None:
        from datetime import UTC, datetime

        try:
            response = request.response()
        except Exception:  # noqa: BLE001
            response = None
        try:  # the tab that made it (a service worker's request has no page)
            pageref = _guid(request.frame.page)
        except Exception:  # noqa: BLE001
            pageref = None
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
        if pageref:
            entry["pageref"] = pageref
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
                       "pages": self.pages, "entries": self.entries}}  # fmt: skip
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

    def wrap(self, page) -> _Draining:
        """Another tab (a popup the script waited for), handed out the same way."""
        return page if isinstance(page, _Draining) else _Draining(page, self._steps)

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
            # The tabs already open are t1, t2...; each tab or popup opened later is recorded
            # from its first page, with a tab-open step.
            for page in context.pages:
                steps.on_page(page, quiet=True)
            context.on("page", steps.on_page)
            try:  # the robot's own window size, as it is (not set by Twin Lab)
                size = context.pages[0].viewport_size if context.pages else None
            except Exception:  # noqa: BLE001
                size = None
            write_meta(out_dir, browser, size, "attach")
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
            steps.closing = True
            # Disconnect only: the robot's browser keeps running.
    finally:
        steps.close()
        har.entries = steps.secrets.scrub(har.entries)
        har.pages = [{"startedDateTime": t["opened"], "id": t["har_page"], "title": t["tab"],
                      "pageTimings": {}} for t in steps.tabs.values()]  # fmt: skip
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
                viewport=VIEWPORT,
                device_scale_factor=DEVICE_SCALE_FACTOR,
            )
            write_meta(out_dir, browser, VIEWPORT, "record")
            # Every tab and popup the flow opens is recorded, on the same sequence.
            context.on("page", steps.on_page)
            context.expose_binding("__tlStep", steps.on_capture)
            context.expose_binding("__tlSecret", steps.on_secret)
            for s in _init_scripts(settle_ms):
                context.add_init_script(s)
            page = context.new_page()
            page.goto(url)

            def open_pages():
                return [p for p in context.pages if not p.is_closed()]

            def flush_all():
                for p in open_pages():
                    try:
                        p.evaluate("() => window.__tlFlush && window.__tlFlush()")
                    except Exception:  # noqa: BLE001 - a page navigating or closing
                        pass

            if script is not None:
                # Each call the script makes first finishes the steps before it, so pictures show
                # each step as it settled, not the end of the script.
                script(_Draining(page, steps))
                live = open_pages()
                if live:
                    live[0].wait_for_timeout(settle_ms + 200)
                flush_all()
                steps.drain()
            else:
                import time

                started = time.monotonic()
                while open_pages():  # until the person closed the last window
                    steps.drain()  # the picture of each step, at most half a second late
                    if time.monotonic() - started > max_seconds:
                        flush_all()
                        steps.drain()
                        print(
                            f"No one closed the window within {max_seconds / 60:.0f} min; stopped."
                        )
                        break
                    try:
                        open_pages()[0].wait_for_timeout(300)
                    except Exception:  # noqa: BLE001 - the window was closed while waiting
                        continue
            steps.closing = True
            context.close()  # writes the HAR
            browser.close()
    finally:
        steps.close()
        steps.secrets.scrub_har(out_dir / "recording.har")  # no typed value stays in the HAR
    save_documents(out_dir, steps.fetched)
    return out_dir
