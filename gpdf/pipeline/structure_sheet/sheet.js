// SPDX-License-Identifier: AGPL-3.0-or-later
// g-pdf structure review sheet: review the detected parts, sections and texts of each
// exported entry offline and save the owner's decisions (structure.decisions.json,
// schema 1). Plain JS, no libraries, no network. The decisions are replayed here over
// the detected sections with the same rules as gpdf/pipeline/structure_decisions.py:
// keep the two in step. Unsaved work is kept in localStorage when the browser allows it.
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("gpdf-data").textContent);
  const ENTRIES = DATA.entries;
  const BY_ID = {};
  ENTRIES.forEach((e) => { BY_ID[e.id] = e; });
  const ROLES = DATA.roles;
  const FILE_NAME = DATA.decisions_name;
  const STORE_KEY = "gpdf-structure:" + DATA.folder;
  const META = [["title", "Title"], ["author", "Author / editor"], ["year", "Year"], ["publisher", "Publisher"]];
  const KINDS = { h: "heading", p: "prose", v: "verse", q: "quote", n: "note" };
  const OPS = ["edit", "move", "merge", "split"];
  const MOVE_WINDOW = 12;                  // units offered on each side of a boundary
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ state
  // state: entry id -> its decisions record, exactly as it will be saved
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const loaded = DATA.decisions && DATA.decisions.entries ? DATA.decisions.entries : {};
  let state = clone(loaded);
  let filter = "all";
  const opened = new Set();
  const splitting = {};                    // entry id -> section key with the split form open
  const moving = {};                       // entry id -> section key with the move list open

  function stable(o) {
    if (Array.isArray(o)) return o.map(stable);
    if (o && typeof o === "object") {
      const out = {};
      Object.keys(o).sort().forEach((k) => { out[k] = stable(o[k]); });
      return out;
    }
    return o;
  }
  const snap = (s) => JSON.stringify(stable(s));
  const baseSnap = snap(loaded);
  let lastSaved = baseSnap;
  const dirty = () => snap(state) !== lastSaved;

  const rec = (e) => (state[e.id] ? clone(state[e.id]) : {});
  const hasContent = (r) => !!r && (!!(r.ops && r.ops.length) || !!r.metadata || !!r.role || !!r.reviewed);

  function unit(e, seq) {
    const u = e.units[seq - 1];
    return u ? { seq: seq, kind: KINDS[u[0]] || u[0], p1: u[1], p2: u[2], l1: u[3], l2: u[4], text: u[5], hash: u[6] } : null;
  }

  // ------------------------------------------------------------------ replay (mirror of structure_decisions.py)
  function ends(secs, n) {
    secs.sort((a, b) => a.start - b.start || a.level - b.level);
    secs.forEach((x, i) => {
      let end = n;
      for (let j = i + 1; j < secs.length; j++) {
        if (secs[j].level <= x.level) { end = secs[j].start; break; }
      }
      x.end = end;
    });
  }

  function okHash(e, seq, want) {
    return Number.isInteger(seq) && seq >= 1 && seq <= e.units.length && e.units[seq - 1][6] === want;
  }

  const clean = (v) => (v === null || v === undefined ? "" : String(v).trim());

  function applyOp(e, secs, op) {
    const kind = op.op, seq = op.seq, level = op.level, n = e.units.length;
    if (OPS.indexOf(kind) < 0) return ["unmatched", "unknown decision " + JSON.stringify(kind)];
    if (!okHash(e, seq, op.hash)) return ["stale", "unit " + seq + " is not the text it was when the decision was made (the export changed)"];
    if (!(Number.isInteger(level) && level >= 1)) return ["unmatched", "level " + JSON.stringify(level) + " is not a whole number from 1 up"];
    const start = seq - 1;
    const hasRole = Object.prototype.hasOwnProperty.call(op, "role");
    if (hasRole && ROLES.indexOf(op.role) < 0) return ["unmatched", "role " + JSON.stringify(op.role) + " is not one of " + ROLES.join(", ")];
    if (kind === "split") {
      if (secs.some((x) => x.start === start && x.level === level)) return ["unmatched", "a level-" + level + " section already starts at unit " + seq];
      let parent = null;
      secs.forEach((x) => { if (x.start <= start && start < x.end && x.level < level && (!parent || x.level > parent.level)) parent = x; });
      if (parent && level > parent.level + 1) {
        return ["unmatched", "«" + (clean(op.title) || "(untitled)").slice(0, 40) + "» (level " + level + ") would be " + (level - parent.level) +
          " levels below its parent «" + parent.title.slice(0, 40) + "» (level " + parent.level + "): a section is at most one level deeper than its parent"];
      }
      let inner = null;
      secs.forEach((x) => { if (x.start <= start && start < x.end && (!inner || x.level > inner.level)) inner = x; });
      secs.push({ start: start, level: level, title: clean(op.title) || "(untitled)", author: clean(op.author) || null,
        role: op.role || inner.role, confidence: "high", evidence: ["owner: split at unit " + seq], sources: ["owner"], owner: true });
      return ["applied", ""];
    }
    const target = secs.find((x) => x.start === start && x.level === level);
    if (!target) return ["unmatched", "no level-" + level + " section starts at unit " + seq];
    if (kind === "edit") {
      const changed = [];
      if (Object.prototype.hasOwnProperty.call(op, "title")) { target.title = clean(op.title) || target.title; changed.push("title"); }
      if (Object.prototype.hasOwnProperty.call(op, "author")) { target.author = clean(op.author) || null; changed.push("author"); }
      if (hasRole) { target.role = op.role; changed.push("role"); }
      target.evidence = target.evidence.concat(["owner: " + (changed.join(", ") || "nothing") + " edited"]);
      target.owner = true;
      return ["applied", ""];
    }
    if (kind === "merge") {
      if (start === 0 && level === 1) return ["unmatched", "the first part has nothing before it to merge into"];
      secs.splice(secs.indexOf(target), 1);
      return ["applied", ""];
    }
    const to = op.to;                                           // move
    if (!okHash(e, to, op.to_hash)) return ["stale", "unit " + to + " is not the text it was when the decision was made (the export changed)"];
    const nw = to - 1;
    if (start === 0) return ["unmatched", "the file's first section starts at the first unit"];
    if (nw === start) return ["unmatched", "it already starts at unit " + to];
    if (nw < start) {
      const before = secs.filter((x) => x.start < start).map((x) => x.start);
      if (nw <= Math.max.apply(null, before)) return ["unmatched", "moving it to unit " + to + " would empty the section before it"];
      const parent = secs.find((x) => x.start === start && x.level < level);
      if (parent) return ["unmatched", "its parent «" + parent.title.slice(0, 40) + "» starts at the same unit: move the parent"];
    } else {
      const after = secs.filter((x) => x.start > start).map((x) => x.start);
      if (nw >= (after.length ? Math.min.apply(null, after) : n)) return ["unmatched", "moving it to unit " + to + " would empty it"];
    }
    secs.forEach((x) => {
      if (x.start === start && x.level >= level) {
        x.start = nw;
        if (x === target || x.level > level) {
          x.evidence = x.evidence.concat(["owner: start moved from unit " + seq + " to " + to]);
          x.owner = true;
        }
      }
    });
    return ["applied", ""];
  }

  function replay(e, extra) {
    // the entry's sections after its decisions (plus ``extra``, an op being tried)
    const r = state[e.id] || {};
    const n = e.units.length;
    const secs = e.detected.map((d) => ({ start: d.first - 1, level: d.level, title: d.title, author: d.author, role: d.role,
      confidence: d.confidence, evidence: d.evidence.slice(), sources: d.sources.slice(), owner: false }));
    const results = [];
    if (r.role) {
      if (ROLES.indexOf(r.role) >= 0) { secs.forEach((x) => { x.role = r.role; }); results.push({ op: "role", status: "applied", why: "" }); }
      else results.push({ op: "role", status: "unmatched", why: "role " + JSON.stringify(r.role) + " is not one of " + ROLES.join(", ") });
    }
    if (r.metadata) results.push({ op: "metadata", status: "applied", why: "" });
    ends(secs, n);
    const ops = (r.ops || []).concat(extra ? [extra] : []);
    ops.forEach((op, i) => {
      const [status, why] = applyOp(e, secs, op);
      results.push({ op: String(op.op), seq: op.seq, level: op.level, status: status, why: why, index: i });
      ends(secs, n);
    });
    // ids and parents, as sections._nest
    const stack = [];
    secs.forEach((x, k) => {
      x.id = "s" + String(k + 1).padStart(3, "0");
      while (stack.length && (stack[stack.length - 1].level >= x.level || stack[stack.length - 1].end <= x.start)) stack.pop();
      x.parent = stack.length ? stack[stack.length - 1].id : null;
      stack.push(x);
    });
    return { secs: secs, results: results };
  }

  function entryStatus(e) {
    const r = state[e.id];
    if (!hasContent(r)) return ["pending", "no decisions yet"];
    const bad = replay(e).results.filter((x) => x.status !== "applied").length;
    if (bad) return ["problems", bad + " stale or unmatched decision" + (bad === 1 ? "" : "s")];
    return ["reviewed", "decisions made"];
  }
  const STATUS_TEXT = { pending: "not reviewed", problems: "stale / unmatched", reviewed: "reviewed" };

  function metaNow(e) {
    const r = state[e.id] || {};
    const out = {};
    META.forEach(([k]) => { out[k] = e.metadata[k] === undefined ? null : e.metadata[k]; });
    out.confirmed = false;
    if (r.metadata) {
      META.forEach(([k]) => { if (Object.prototype.hasOwnProperty.call(r.metadata, k)) out[k] = r.metadata[k]; });
      out.confirmed = !!r.metadata.confirmed;
    }
    return out;
  }

  // ------------------------------------------------------------------ changes
  function setRec(e, r) {
    if (hasContent(r)) state[e.id] = r; else delete state[e.id];
    autosave();
    renderAll();
  }

  function addOp(e, op) {
    // tried first: an op that would not apply is refused here, with the reason
    const res = replay(e, op).results;
    const last = res[res.length - 1];
    if (last.status !== "applied") { say("Not added: " + last.why + ".", true); return false; }
    const r = rec(e);
    r.ops = (r.ops || []).concat([op]);
    setRec(e, r);
    say("");
    return true;
  }

  function editSection(e, x, field, value) {
    const r = rec(e);
    r.ops = r.ops || [];
    const seq = x.start + 1, last = r.ops[r.ops.length - 1];
    if (last && last.op === "edit" && last.seq === seq && last.level === x.level) last[field] = value;
    else {
      const op = { op: "edit", seq: seq, level: x.level, hash: e.units[x.start][6] };
      op[field] = value;
      r.ops.push(op);
    }
    setRec(e, r);
  }

  function setMeta(e, field, value, confirm) {
    const r = rec(e);
    const m = metaNow(e);
    if (field) m[field] = value;
    m.confirmed = !!confirm;
    r.metadata = m;
    setRec(e, r);
  }

  // ------------------------------------------------------------------ storage
  function storage() {
    try { return window.localStorage || null; } catch (err) { return null; }
  }

  function autosave() {
    const s = storage();
    if (!s) return;
    try { s.setItem(STORE_KEY, JSON.stringify({ base: baseSnap, state: state })); } catch (err) { /* full or blocked */ }
  }

  function forget() {
    const s = storage();
    if (!s) return;
    try { s.removeItem(STORE_KEY); } catch (err) { /* blocked */ }
  }

  function validEntries(entries) {
    return entries && typeof entries === "object" && !Array.isArray(entries) &&
      Object.keys(entries).every((k) => entries[k] && typeof entries[k] === "object" && !Array.isArray(entries[k]) &&
        (entries[k].ops === undefined || Array.isArray(entries[k].ops)));
  }

  function readSaved() {
    const s = storage();
    if (!s) return null;
    try {
      const v = JSON.parse(s.getItem(STORE_KEY) || "null");
      return v && validEntries(v.state) ? v : null;
    } catch (err) {
      return null;
    }
  }

  // ------------------------------------------------------------------ messages
  function say(text, isError) {
    const m = $("msg");
    m.textContent = text;
    m.className = isError ? "error" : "";
  }

  // ------------------------------------------------------------------ decisions file
  function decisionsText() {
    const entries = {};
    Object.keys(state).sort().forEach((k) => { if (hasContent(state[k])) entries[k] = state[k]; });
    return JSON.stringify({ schema: 1, saved: new Date().toISOString(), entries: entries }, null, 1) + "\n";
  }

  function checkLoaded(text) {
    let obj;
    try { obj = JSON.parse(text); } catch (err) { return [null, "it is not JSON (" + err.message + ")"]; }
    if (!obj || typeof obj !== "object" || obj.schema !== 1 || !validEntries(obj.entries)) {
      return [null, "it is not a schema 1 structure decisions file"];
    }
    return [obj, null];
  }

  async function save() {
    const text = decisionsText();
    if (typeof window.showSaveFilePicker === "function") {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: FILE_NAME,
          types: [{ description: "g-pdf structure decisions", accept: { "application/json": [".json"] } }],
        });
        const w = await handle.createWritable();
        await w.write(text);
        await w.close();
        return saved(handle.name, "picker");
      } catch (err) {
        if (err && err.name === "AbortError") { say("Save cancelled: nothing was written."); return; }
        // any other failure (file:// restrictions, policy): fall back to a download
      }
    }
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = FILE_NAME;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    saved(FILE_NAME, "download");
  }

  function saved(fileName, via) {
    lastSaved = snap(state);
    say(via === "download"
      ? "Saved " + fileName + " to the browser's downloads folder: move it to " + DATA.folder +
        " (as " + FILE_NAME + ") and run gpdf-cli structure again to apply it."
      : "Saved " + fileName + ". Keep it in " + DATA.folder + " as " + FILE_NAME + " and run gpdf-cli structure again.");
  }

  async function load(file) {
    let text;
    try { text = await file.text(); } catch (err) { say("Could not read " + file.name + ".", true); return; }
    const [obj, problem] = checkLoaded(text);
    if (!obj) { say("Not loaded: " + file.name + ": " + problem + ". Nothing was changed.", true); return; }
    state = clone(obj.entries);
    lastSaved = snap(state);
    autosave();
    say("Loaded " + file.name + ".");
    renderAll();
  }

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs, text) {
    const n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach((k) => {
        if (k === "class") n.className = attrs[k];
        else n.setAttribute(k, attrs[k]);
      });
    }
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  const stop = (n) => { n.addEventListener("click", (ev) => ev.stopPropagation()); return n; };
  const badge = (cls, text, title) => el("span", { class: "badge " + cls, title: title || "" }, text);

  function range(a, b) {
    if (a === null || a === undefined) return "";
    return a === b || b === null || b === undefined ? String(a) : a + "-" + b;
  }

  function spanOf(e, x) {
    // pages (or lines) and printed labels of units start..end-1
    let lo = null, hi = null;
    for (let i = x.start; i < x.end; i++) {
      const u = e.units[i];
      if (u[1] !== null && (lo === null || u[1] < lo)) lo = u[1];
      if (u[2] !== null && (hi === null || u[2] > hi)) hi = u[2];
    }
    let l1 = null, l2 = null;
    for (let i = x.start; i < x.end && l1 === null; i++) if (e.units[i][3]) l1 = e.units[i][3];
    for (let i = x.end - 1; i >= x.start && l2 === null; i--) if (e.units[i][4]) l2 = e.units[i][4];
    const where = (e.kind === "txt" ? "lines " : "pdf ") + range(lo, hi);
    return l1 ? where + " · p. " + range(l1, l2) : where;
  }

  function headText(e, start, end, n) {
    let out = "";
    for (let i = start; i < end && out.length < n; i++) out += (out ? " ¶ " : "") + e.units[i][5];
    return out.length > n ? out.slice(0, n) + "…" : out;
  }

  function tailText(e, before, n) {
    let out = "";
    for (let i = before - 1; i >= 0 && out.length < n; i--) out = e.units[i][5] + (out ? " ¶ " + out : "");
    return out.length > n ? "…" + out.slice(out.length - n) : out;
  }

  function unitLabel(e, seq) {
    const u = unit(e, seq);
    const t = u.text.length > 70 ? u.text.slice(0, 70) + "…" : u.text;
    return "unit " + seq + " · " + (e.kind === "txt" ? "l. " : "p. ") + u.p1 + " · " + u.kind + " · " + t.replace(/\n/g, " / ");
  }

  function opText(op) {
    const at = op.seq !== undefined ? " at unit " + op.seq + " (level " + op.level + ")" : "";
    if (op.op === "edit") {
      const parts = ["title", "author", "role"].filter((k) => Object.prototype.hasOwnProperty.call(op, k))
        .map((k) => k + " → " + (op[k] === null || op[k] === "" ? "(none)" : op[k]));
      return "edit" + at + ": " + parts.join(", ");
    }
    if (op.op === "move") return "move" + at + " to unit " + op.to;
    if (op.op === "merge") return "merge" + at + " into the section before";
    if (op.op === "split") return "split" + at + ": «" + (op.title || "") + "»" + (op.author ? " by " + op.author : "") + ", " + op.role;
    return String(op.op) + at;
  }

  // ------------------------------------------------------------------ table
  function visible(e) {
    const st = entryStatus(e)[0];
    switch (filter) {
      case "low": return e.low > 0;
      case "pending": return st === "pending";
      case "problems": return st === "problems";
      default: return true;
    }
  }

  function entryRow(e) {
    const tr = el("tr", { class: "entry" + (opened.has(e.id) ? " open" : ""), "data-id": e.id, tabindex: "0" });
    const view = replay(e);
    const m = metaNow(e);
    tr.appendChild(el("td", { class: "file", title: e.stem }, e.file));
    tr.appendChild(el("td", null, e.kind + " · " + e.book_kind));
    tr.appendChild(el("td", { class: "num" }, view.secs.length));
    tr.appendChild(el("td", { class: "num" }, e.low || ""));
    const mtd = el("td");
    mtd.appendChild(badge(m.confirmed ? "confirmed" : "unconfirmed", m.confirmed ? "confirmed" : "unconfirmed"));
    mtd.appendChild(document.createTextNode(" " + [m.title, m.author, m.year].filter(Boolean).join(" · ")));
    tr.appendChild(mtd);
    const r = state[e.id] || {};
    const res = view.results.filter((x) => x.op !== "metadata");
    const bad = res.filter((x) => x.status !== "applied").length;
    tr.appendChild(el("td", null, res.length ? res.length + (bad ? " (" + bad + " stale / unmatched)" : "") : (r.reviewed ? "reviewed, no changes" : "")));
    const [st, why] = entryStatus(e);
    const std = el("td");
    std.appendChild(badge(st, STATUS_TEXT[st], why));
    tr.appendChild(std);
    const toggle = () => {
      if (opened.has(e.id)) opened.delete(e.id); else opened.add(e.id);
      renderRows();
    };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter" && ev.target === tr) { ev.preventDefault(); toggle(); } });
    return tr;
  }

  function renderRows() {
    const body = $("rows");
    const y = window.scrollY;
    body.textContent = "";
    for (const e of ENTRIES) {
      if (!visible(e)) continue;
      body.appendChild(entryRow(e));
      if (opened.has(e.id)) {
        const dtr = el("tr", { class: "detail", "data-id": e.id });
        const td = el("td", { colspan: "7" });
        td.appendChild(detail(e));
        dtr.appendChild(td);
        body.appendChild(dtr);
      }
    }
    window.scrollTo(0, y);
  }

  // ------------------------------------------------------------------ detail
  function metaEditor(e) {
    const m = metaNow(e);
    const box = el("div");
    box.appendChild(el("h3", null, "Entry metadata"));
    const form = el("div", { class: "editor meta" });
    META.forEach(([k, label]) => {
      const inp = stop(el("input", { type: "text", "data-field": k, value: m[k] === null || m[k] === undefined ? "" : m[k] }));
      inp.addEventListener("change", () => setMeta(e, k, inp.value.trim() || null, false));
      form.append(el("label", null, label), inp);
    });
    const line = el("div", { class: "wide buttons" });
    line.appendChild(badge(m.confirmed ? "confirmed" : "unconfirmed", m.confirmed ? "confirmed" : "unconfirmed",
      m.confirmed ? "confirmed by you" : (e.metadata.evidence || "prefilled; not confirmed")));
    const ok = stop(el("button", { type: "button", class: "primary confirm-meta" }, "Confirm metadata"));
    ok.addEventListener("click", () => { setMeta(e, null, null, true); say("Metadata confirmed for " + e.file + "."); });
    line.appendChild(ok);
    form.appendChild(line);
    if (!m.confirmed) form.appendChild(el("p", { class: "wide small muted" }, "Detected: " +
      (e.metadata.evidence || "from the file") + ". Fill in what is missing, then confirm."));
    box.appendChild(form);
    return box;
  }

  function entryEditor(e, view) {
    const r = state[e.id] || {};
    const box = el("div");
    box.appendChild(el("h3", null, "Whole entry"));
    const form = el("div", { class: "editor" });
    const role = stop(el("select", { class: "entry-role" }));
    role.appendChild(el("option", { value: "" }, "(as detected, per section)"));
    ROLES.forEach((x) => { const o = el("option", { value: x }, x); if (x === r.role) o.selected = true; role.appendChild(o); });
    role.addEventListener("change", () => {
      const nr = rec(e);
      if (role.value) nr.role = role.value; else delete nr.role;
      setRec(e, nr);
    });
    form.append(el("label", null, "Role of every section"), role);
    const lab = el("label", { class: "wide check" });
    const chk = stop(el("input", { type: "checkbox", class: "reviewed" }));
    chk.checked = !!r.reviewed;
    chk.addEventListener("change", () => {
      const nr = rec(e);
      if (chk.checked) nr.reviewed = true; else delete nr.reviewed;
      setRec(e, nr);
    });
    lab.append(chk, document.createTextNode(" Structure reviewed (also when nothing needed changing)"));
    form.appendChild(stop(lab));
    box.appendChild(form);
    const ops = (r.ops || []);
    box.appendChild(el("h3", null, "Decisions (" + ops.length + ", applied in this order)"));
    if (!ops.length) box.appendChild(el("p", { class: "muted small" }, "none: edit the tree below"));
    const ul = el("ul", { class: "ops" });
    const results = view.results.filter((x) => x.index !== undefined);
    ops.forEach((op, i) => {
      const li = el("li");
      const res = results[i];
      li.appendChild(badge(res.status, res.status, res.why));
      li.appendChild(document.createTextNode(" " + opText(op) + (res.why ? " — " + res.why : "")));
      const rm = stop(el("button", { type: "button", class: "remove", title: "remove this decision" }, "Remove"));
      rm.addEventListener("click", () => {
        const nr = rec(e);
        nr.ops.splice(i, 1);
        if (!nr.ops.length) delete nr.ops;
        setRec(e, nr);
      });
      li.appendChild(rm);
      ul.appendChild(li);
    });
    box.appendChild(ul);
    return box;
  }

  const keyOf = (x) => x.start + ":" + x.level;

  function moveSelect(e, view, x) {
    // the units the start may move to (same bounds as applyOp)
    const secs = view.secs, n = e.units.length;
    const before = secs.filter((y) => y.start < x.start).map((y) => y.start);
    const after = secs.filter((y) => y.start > x.start).map((y) => y.start);
    const parentSame = secs.some((y) => y.start === x.start && y.level < x.level);
    const lo = parentSame ? x.start : Math.max.apply(null, before) + 1;
    const hi = (after.length ? Math.min.apply(null, after) : n) - 1;
    const sel = stop(el("select", { class: "move-to", "aria-label": "move the start to" }));
    sel.appendChild(el("option", { value: "" }, "move the start to…"));
    for (let i = Math.max(lo, x.start - MOVE_WINDOW); i <= Math.min(hi, x.start + MOVE_WINDOW); i++) {
      const o = el("option", { value: String(i + 1) }, (i === x.start ? "• " : "") + unitLabel(e, i + 1));
      if (i === x.start) o.disabled = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => {
      if (!sel.value) return;
      const to = Number(sel.value);
      delete moving[e.id];
      addOp(e, { op: "move", seq: x.start + 1, level: x.level, hash: e.units[x.start][6], to: to, to_hash: e.units[to - 1][6] });
    });
    return sel;
  }

  function splitForm(e, view, x) {
    const tr = el("tr", { class: "splitrow" });
    const td = el("td", { colspan: "9" });
    const form = el("div", { class: "splitform" });
    // default: the next heading inside the section, else the unit after its start
    let seq = x.start + 2;
    for (let i = x.start + 1; i < x.end; i++) if (e.units[i][0] === "h") { seq = i + 1; break; }
    if (seq > x.end) seq = x.start + 1;
    const inner = (s) => {
      let best = null;
      view.secs.forEach((y) => { if (y.start <= s - 1 && s - 1 < y.end && (!best || y.level > best.level)) best = y; });
      return best;
    };
    const seqIn = stop(el("input", { type: "number", class: "seq", min: "1", max: String(e.units.length), value: String(seq) }));
    const level = stop(el("select", { class: "split-level" }));
    const u0 = unit(e, seq);
    const title = stop(el("input", { type: "text", class: "split-title", placeholder: "title", value: u0 && u0.kind === "heading" ? u0.text.slice(0, 120) : "" }));
    const author = stop(el("input", { type: "text", class: "split-author", placeholder: "author", value: x.author || "" }));
    const role = stop(el("select", { class: "split-role" }));
    ROLES.forEach((r) => { const o = el("option", { value: r }, r); if (r === x.role) o.selected = true; role.appendChild(o); });
    const preview = el("div", { class: "preview" });
    const showPreview = () => {
      const s = Number(seqIn.value);
      const u = unit(e, s);
      preview.textContent = u ? unitLabel(e, s) : "no unit " + seqIn.value;
      // levels 1 to one below the deepest section at the unit, that one's level chosen
      const at = u ? inner(s) : x;
      level.textContent = "";
      for (let l = 1; l <= at.level + 1; l++) level.appendChild(el("option", { value: String(l) }, "level " + l));
      level.value = String(at.level);
    };
    seqIn.addEventListener("input", showPreview);
    showPreview();
    const add = stop(el("button", { type: "button", class: "primary add-split" }, "Add split"));
    add.addEventListener("click", () => {
      const s = Number(seqIn.value);
      if (!unit(e, s)) { say("Split: no unit " + seqIn.value + ".", true); return; }
      const op = { op: "split", seq: s, level: Number(level.value), hash: e.units[s - 1][6], title: title.value.trim(),
        author: author.value.trim(), role: role.value };
      if (addOp(e, op)) delete splitting[e.id];
    });
    const cancel = stop(el("button", { type: "button" }, "Cancel"));
    cancel.addEventListener("click", () => { delete splitting[e.id]; renderRows(); });
    form.append(el("span", null, "Split at"), seqIn, level, title, author, role, add, cancel, preview);
    td.appendChild(form);
    tr.appendChild(td);
    return tr;
  }

  function tree(e, view) {
    const tab = el("table", { class: "tree" });
    const head = el("tr");
    ["Title", "Author", "Role", "Lvl", "Pages", "Conf.", "Evidence", "Text (end of the previous ‖ start of this one)", ""]
      .forEach((h) => head.appendChild(el("th", null, h)));
    tab.appendChild(head);
    view.secs.forEach((x, i) => {
      const tr = el("tr", { class: "sec" + (x.confidence === "low" ? " lowconf" : ""), "data-key": keyOf(x), "data-first": String(x.start + 1),
        "data-level": String(x.level), style: "--depth: " + (x.level - 1) });
      const ttd = el("td", { class: "title" });
      const t = stop(el("input", { type: "text", class: "t", value: x.title, "aria-label": "title" }));
      t.addEventListener("change", () => editSection(e, x, "title", t.value.trim()));
      ttd.appendChild(t);
      if (x.owner) { ttd.appendChild(document.createTextNode(" ")); ttd.appendChild(badge("owner", "yours")); }
      tr.appendChild(ttd);
      const atd = el("td");
      const a = stop(el("input", { type: "text", class: "a", value: x.author || "", "aria-label": "author" }));
      a.addEventListener("change", () => editSection(e, x, "author", a.value.trim()));
      atd.appendChild(a);
      tr.appendChild(atd);
      const rtd = el("td");
      const role = stop(el("select", { class: "r", "aria-label": "role" }));
      ROLES.forEach((r) => { const o = el("option", { value: r }, r); if (r === x.role) o.selected = true; role.appendChild(o); });
      role.addEventListener("change", () => editSection(e, x, "role", role.value));
      rtd.appendChild(role);
      tr.appendChild(rtd);
      tr.appendChild(el("td", null, x.level));
      tr.appendChild(el("td", { class: "pages" }, spanOf(e, x) + " · units " + (x.start + 1) + "-" + x.end));
      const ctd = el("td");
      ctd.appendChild(badge(x.confidence, x.confidence));
      tr.appendChild(ctd);
      const etd = el("td");
      const det = stop(el("details"));
      det.appendChild(el("summary", null, (x.sources || []).join(", ") || "evidence"));
      det.appendChild(el("div", null, x.evidence.join("; ")));
      etd.appendChild(det);
      tr.appendChild(etd);
      const xtd = el("td", { class: "text" });
      if (x.start > 0) xtd.appendChild(el("span", { class: "prev" }, tailText(e, x.start, 100)));
      xtd.appendChild(el("span", { class: "head" }, headText(e, x.start, x.end, 200)));
      tr.appendChild(xtd);
      const act = el("td", { class: "acts" });
      if (x.start > 0) {
        if (moving[e.id] === keyOf(x)) act.appendChild(moveSelect(e, view, x));
        else {
          const mv = stop(el("button", { type: "button", class: "move", title: "move where this section starts" }, "Move…"));
          mv.addEventListener("click", () => { moving[e.id] = keyOf(x); renderRows(); });
          act.appendChild(mv);
        }
      }
      if (i > 0) {
        const mg = stop(el("button", { type: "button", class: "merge", title: "merge into the section before (its units join that one)" }, "Merge"));
        mg.addEventListener("click", () => addOp(e, { op: "merge", seq: x.start + 1, level: x.level, hash: e.units[x.start][6] }));
        act.appendChild(mg);
      }
      const sp = stop(el("button", { type: "button", class: "split", title: "start a new section at a unit inside this one" }, "Split…"));
      sp.addEventListener("click", () => { splitting[e.id] = keyOf(x); renderRows(); });
      act.appendChild(sp);
      tr.appendChild(act);
      tab.appendChild(tr);
      if (splitting[e.id] === keyOf(x)) tab.appendChild(splitForm(e, view, x));
    });
    return tab;
  }

  function detail(e) {
    const view = replay(e);
    const box = el("div", { class: "detail" });
    const grid = el("div", { class: "top-grid" });
    grid.append(stop(metaEditor(e)), stop(entryEditor(e, view)));
    box.appendChild(grid);
    box.appendChild(el("h3", null, "Sections (" + view.secs.length + ")" + (e.low ? ", " + e.low + " detected with low confidence" : "")));
    box.appendChild(stop(tree(e, view)));
    return box;
  }

  // ------------------------------------------------------------------ top
  function renderTop() {
    let done = 0, bad = 0;
    for (const e of ENTRIES) {
      const st = entryStatus(e)[0];
      if (st === "reviewed") done++;
      if (st === "problems") bad++;
    }
    $("progress").textContent = "Reviewed " + done + " / " + ENTRIES.length + (bad ? " (" + bad + " with stale or unmatched decisions)" : "");
    document.querySelectorAll("[data-filter]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.filter === filter));
    });
    $("where").textContent = "Structure folder: " + DATA.folder + " · save decisions there as " + FILE_NAME +
      " · generated " + DATA.generated;
  }

  function renderAll() {
    renderTop();
    renderRows();
  }

  // ------------------------------------------------------------------ wiring
  $("save").addEventListener("click", save);
  $("load").addEventListener("click", () => $("load-input").click());
  $("load-input").addEventListener("change", (ev) => {
    const f = ev.target.files && ev.target.files[0];
    ev.target.value = "";
    if (f) load(f);
  });
  document.querySelectorAll("[data-filter]").forEach((b) => {
    b.addEventListener("click", () => { filter = b.dataset.filter; renderAll(); });
  });
  window.addEventListener("beforeunload", (ev) => {
    if (!dirty()) return;
    ev.preventDefault();
    ev.returnValue = "";
  });

  const savedState = readSaved();
  if (savedState && snap(savedState.state) !== snap(state)) {
    const ids = new Set(Object.keys(savedState.state).concat(Object.keys(state)));
    let n = 0;
    ids.forEach((k) => { if (snap(savedState.state[k] || null) !== snap(state[k] || null)) n++; });
    $("restore-text").textContent = "Restore your unsaved decisions (" + n + (n === 1 ? " entry" : " entries") +
      " changed)?" + (savedState.base !== baseSnap ? " The decisions file has changed since." : "");
    $("restore").hidden = false;
  }
  $("restore-yes").addEventListener("click", () => {
    state = clone(savedState.state);
    $("restore").hidden = true;
    say("Restored your unsaved decisions.");
    renderAll();
  });
  $("restore-no").addEventListener("click", () => {
    forget();
    $("restore").hidden = true;
    say("Discarded the unsaved decisions.");
  });

  renderAll();

  // read-only hook for tests and debugging
  window.gpdfStructure = Object.freeze({
    decisionsText: decisionsText,
    tree: (id) => replay(BY_ID[id]).secs.map((x) => [x.start + 1, x.end, x.level, x.title, x.author, x.role]),
    results: (id) => replay(BY_ID[id]).results.map((r) => r.status),
    status: (id) => entryStatus(BY_ID[id])[0],
    dirty: dirty,
  });
})();
