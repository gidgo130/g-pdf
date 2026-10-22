// SPDX-License-Identifier: AGPL-3.0-or-later
// g-pdf catalogue review sheet: review the survey's suggestions offline and save the
// owner's decisions (catalogue.decisions.json, schema 1). Plain JS, no libraries, no
// network. Unsaved work is kept in localStorage when the browser allows it.
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("gpdf-data").textContent);
  const ENTRIES = DATA.entries;
  const BY_ID = {};
  ENTRIES.forEach((e) => { BY_ID[e.id] = e; });
  const FIELDS = DATA.row_fields;
  const CLASSES = DATA.classes;
  const FILE_NAME = DATA.decisions_name;
  const STORE_KEY = "gpdf-catalogue:" + DATA.folder;
  const ACTIONS = ["keep_text", "ocr_missing", "rebuild_layer", "exclude", "review"];
  const REASONS = ["duplicate", "other_language", "off_topic", "other", "trial not run"];
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ state
  // state: id -> decision, exactly as it will be saved (the loaded file plus edits)
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const loaded = DATA.decisions && DATA.decisions.files ? DATA.decisions.files : {};
  let state = clone(loaded);
  let filter = "all";
  let sort = "root";
  const opened = new Set();
  const selected = {};                     // id -> page shown in the page line

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

  const sugg = (e) => e.suggestion || {};
  const name = (e) => e.rel.split(/[\\/]/).pop();

  function previousDecision(e) {
    // the decision made for an earlier version of this file (it changed since)
    let prev = e.previous_id;
    const seen = {};
    while (prev && !seen[prev]) {
      seen[prev] = true;
      if (state[prev]) return state[prev];
      prev = BY_ID[prev] ? BY_ID[prev].previous_id : null;
    }
    return null;
  }

  function defaults(e) {
    const s = sugg(e), m = e.measured || {};
    return {
      path: e.path, action: s.action || "review", pages: s.pages === undefined ? null : s.pages,
      reason: s.reason || null, duplicate_of: s.duplicate_of || null,
      title: m.suggested_title || name(e), author: m.author || "", year: null,
      page_offset: null, notes: "", confirmed: false, suggestion_action: null,
    };
  }

  function current(e) {
    if (state[e.id]) return state[e.id];
    const prev = previousDecision(e);
    if (prev) return Object.assign(defaults(e), clone(prev), { confirmed: false, path: e.path });
    return defaults(e);
  }

  function status(e) {
    const d = state[e.id];
    if (d) {
      if (!d.confirmed) return ["pending", "not confirmed"];
      const was = d.suggestion_action, now = sugg(e).action;
      if (was && now && was !== now) return ["reconfirm", "suggestion changed (" + was + " -> " + now + ")"];
      return ["confirmed", ""];
    }
    if (previousDecision(e)) return ["reconfirm", "file changed since the decision"];
    return ["pending", "no decision"];
  }
  const STATUS_TEXT = { confirmed: "confirmed", reconfirm: "needs re-confirm", pending: "pending" };

  function samePages(a, b) { return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b); }

  function differs(e) {
    const d = current(e), s = sugg(e);
    return d.action !== (s.action || "review") || !samePages(d.pages, s.pages) ||
      (d.reason || null) !== (s.reason || null) || (d.duplicate_of || null) !== (s.duplicate_of || null);
  }

  function setField(e, field, value) {
    const d = Object.assign({}, current(e));
    d[field] = value;
    d.confirmed = false;
    d.path = e.path;
    state[e.id] = d;
    autosave();
    updateRow(e);
    renderTop();
  }

  function confirmEntry(e) {
    const d = Object.assign({}, current(e));
    d.confirmed = true;
    d.suggestion_action = sugg(e).action || null;
    d.path = e.path;
    state[e.id] = d;
  }

  // ------------------------------------------------------------------ pages field
  function pagesText(p) {
    if (p === null || p === undefined) return "";
    if (!Array.isArray(p)) return String(p);
    const out = [];
    for (let i = 0; i < p.length; i++) {
      let j = i;
      while (j + 1 < p.length && p[j + 1] === p[j] + 1) j++;
      out.push(j > i ? p[i] + "-" + p[j] : String(p[i]));
      i = j;
    }
    return out.join(", ");
  }

  function parsePages(text) {
    // "" -> null, "all" -> "all", "4, 7-9" -> [4, 7, 8, 9]; undefined if malformed
    const t = text.trim().toLowerCase();
    if (!t) return null;
    if (t === "all") return "all";
    const pages = new Set();
    for (const part of t.split(",")) {
      const m = /^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(part);
      if (!m) return undefined;
      const a = Number(m[1]), b = m[2] ? Number(m[2]) : a;
      if (a < 1 || b < a || b - a > 100000) return undefined;
      for (let k = a; k <= b; k++) pages.add(k);
    }
    return Array.from(pages).sort((x, y) => x - y);
  }

  function intOrNull(text) {
    const t = text.trim();
    if (!t) return null;
    return /^-?\d+$/.test(t) ? Number(t) : undefined;
  }

  // ------------------------------------------------------------------ storage
  function storage() {
    try { return window.localStorage || null; } catch (e) { return null; }
  }

  function autosave() {
    const s = storage();
    if (!s) return;
    try { s.setItem(STORE_KEY, JSON.stringify({ base: baseSnap, state: state })); } catch (e) { /* full or blocked */ }
  }

  function forget() {
    const s = storage();
    if (!s) return;
    try { s.removeItem(STORE_KEY); } catch (e) { /* blocked */ }
  }

  function validFiles(files) {
    return files && typeof files === "object" && !Array.isArray(files) &&
      Object.keys(files).every((k) => files[k] && typeof files[k] === "object" && !Array.isArray(files[k]));
  }

  function readSaved() {
    const s = storage();
    if (!s) return null;
    try {
      const v = JSON.parse(s.getItem(STORE_KEY) || "null");
      return v && validFiles(v.state) ? v : null;
    } catch (e) {
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
    const files = {};
    Object.keys(state).sort().forEach((k) => { files[k] = state[k]; });
    return JSON.stringify({ schema: 1, saved: new Date().toISOString(), files: files }, null, 1) + "\n";
  }

  function checkLoaded(text) {
    let obj;
    try { obj = JSON.parse(text); } catch (e) { return [null, "it is not JSON (" + e.message + ")"]; }
    if (!obj || typeof obj !== "object" || obj.schema !== 1 || !validFiles(obj.files)) {
      return [null, "it is not a schema 1 catalogue decisions file"];
    }
    return [obj, null];
  }

  async function save() {
    const text = decisionsText();
    if (typeof window.showSaveFilePicker === "function") {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: FILE_NAME,
          types: [{ description: "g-pdf catalogue decisions", accept: { "application/json": [".json"] } }],
        });
        const w = await handle.createWritable();
        await w.write(text);
        await w.close();
        return saved(handle.name, "picker");
      } catch (e) {
        if (e && e.name === "AbortError") { say("Save cancelled: nothing was written."); return; }
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
    const where = DATA.folder;
    say(via === "download"
      ? "Saved " + fileName + " to the browser's downloads folder: move it to " + where +
        " (as " + FILE_NAME + ") so the next survey reads it."
      : "Saved " + fileName + ". Keep it in " + where + " as " + FILE_NAME + ".");
  }

  async function load(file) {
    let text;
    try { text = await file.text(); } catch (e) { say("Could not read " + file.name + ".", true); return; }
    const [obj, problem] = checkLoaded(text);
    if (!obj) { say("Not loaded: " + file.name + ": " + problem + ". Nothing was changed.", true); return; }
    state = clone(obj.files);
    lastSaved = snap(state);
    autosave();
    say("Loaded " + file.name + ".");
    renderAll();
  }

  // ------------------------------------------------------------------ filtering
  function visible(e) {
    const st = status(e)[0], a = sugg(e).action, d = state[e.id];
    switch (filter) {
      case "pending": return e.status !== "missing" && st === "pending";
      case "reconfirm": return st === "reconfirm";
      case "rebuild": return a === "rebuild_layer";
      case "excluded": return a === "exclude" || (d && d.action === "exclude");
      case "missing": return e.status === "missing";
      default: return true;
    }
  }

  function weighted(e) {
    const s = e.measured && e.measured.score;
    return s && s.weighted !== null && s.weighted !== undefined ? s.weighted : -1;
  }

  function sorted() {
    const list = ENTRIES.slice();
    const by = (f) => (a, b) => (f(a) < f(b) ? -1 : f(a) > f(b) ? 1 : 0);
    if (sort === "name") list.sort(by((e) => name(e).toLowerCase()));
    else if (sort === "suspect") list.sort((a, b) => weighted(b) - weighted(a));
    else list.sort(by((e) => e.root.toLowerCase() + "\u0000" + e.rel.toLowerCase()));
    return list;
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

  const pct = (v) => (v === null || v === undefined ? "n/a" : Number(v).toFixed(1));
  const base = (p) => (p ? String(p).split(/[\\/]/).pop() : "");

  function suggestionText(s) {
    if (!s || !s.action) return "";
    if (s.action === "ocr_missing" && Array.isArray(s.pages)) return s.action + " p. " + pagesText(s.pages);
    return s.reason ? s.action + " (" + s.reason + ")" : s.action;
  }

  function decisionText(d) {
    if (!d) return "";
    if (d.action === "ocr_missing" && Array.isArray(d.pages)) return d.action + " p. " + pagesText(d.pages);
    return d.reason ? d.action + " (" + d.reason + ")" : d.action;
  }

  // ------------------------------------------------------------------ table
  const rowEls = {};                       // id -> tr.entry

  function cells(e) {
    const m = e.measured || {}, s = m.score || {}, lang = m.language || {};
    const shares = lang.shares ? Object.keys(lang.shares).map((k) => k + " " + lang.shares[k] + "%").join(", ") : "";
    const pages = m.kind === "txt" ? (m.pseudo_pages || 0) + " pp." : (m.pages === undefined ? "" : m.pages);
    const scan = m.kind === "txt" ? "" : m.scan ? "scan" + (m.dpi ? " " + m.dpi + " dpi" : "") : (e.measured ? "born-digital" : "");
    const wf = e.working_file ? base(e.working_file.path) : "";
    const group = e.duplicates && e.duplicates.group ? e.duplicates.group : "";
    return [
      [base(e.root), e.root], [e.rel, e.path], [e.kind], [pages, null, "num"],
      [m.text_pct === undefined || m.text_pct === null ? "n/a" : m.text_pct, null, "num"],
      [scan], [(lang.lang || "") + (shares ? " (" + shares + ")" : "")],
      [e.measured ? pct(s.median) + " / " + pct(s.weighted) : "", null, "num"],
      [e.measured ? (s.fair || 0) + " / " + (s.poor || 0) : "", null, "num"],
      [e.measured ? pct(s.cap_unknown_pct) : "", null, "num"],
      [wf, e.working_file ? e.working_file.path : null],
      [group],
    ];
  }

  function updateRow(e) {
    const tr = rowEls[e.id];
    if (!tr) return;
    tr.textContent = "";
    cells(e).forEach(([text, title, cls], i) => {
      const td = el("td", cls ? { class: cls } : null, text);
      if (i === 1) td.className = "file";
      if (title) td.title = title;
      tr.appendChild(td);
    });
    const s = sugg(e);
    const td = el("td");
    td.appendChild(el("span", { class: "act" }, e.status === "missing" ? "missing" : suggestionText(s)));
    if (s.reasons && s.reasons.length) td.title = s.reasons.join("\n");
    tr.appendChild(td);
    const [st, why] = status(e);
    const dtd = el("td");
    dtd.appendChild(el("span", { class: "badge " + st, title: why }, STATUS_TEXT[st]));
    if ((state[e.id] || previousDecision(e)) && differs(e)) {
      dtd.appendChild(document.createTextNode(" "));
      dtd.appendChild(el("span", { class: "badge differs", title: "the decision differs from the suggestion" },
        "-> " + decisionText(current(e))));
    }
    tr.appendChild(dtd);
    tr.classList.toggle("missing", e.status === "missing");
    tr.classList.toggle("open", opened.has(e.id));
  }

  function renderRows() {
    const body = $("rows");
    body.textContent = "";
    for (const e of sorted()) {
      if (!visible(e)) continue;
      const tr = el("tr", { class: "entry", "data-id": e.id, tabindex: "0" });
      rowEls[e.id] = tr;
      updateRow(e);
      const toggle = () => {
        if (opened.has(e.id)) opened.delete(e.id); else opened.add(e.id);
        renderRows();
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); toggle(); } });
      body.appendChild(tr);
      if (opened.has(e.id)) {
        const dtr = el("tr", { class: "detail" });
        const td = el("td", { colspan: "14" });
        td.appendChild(detail(e));
        dtr.appendChild(td);
        body.appendChild(dtr);
      }
    }
  }

  // ------------------------------------------------------------------ detail
  function list(items) {
    const ul = el("ul");
    items.forEach((t) => ul.appendChild(el("li", null, t)));
    return ul;
  }

  function rowObj(r) {
    const o = {};
    FIELDS.forEach((f, i) => { o[f] = r[i]; });
    o.cls = CLASSES[o.cls] || "unscored";
    return o;
  }

  function pageLine(e, p) {
    const r = (e.rows || []).find((x) => x[0] === p);
    if (!r) return "p. " + p;
    const o = rowObj(r);
    return "p. " + o.page + " · " + (o.lang || "?") + " · " + o.words + " words (" + o.lower +
      " lowercase) · suspect " + pct(o.pct) + "% (" + o.cls + ") · merged " + o.merged +
      ", letter-spaced " + o.spaced + ", digit " + o.digit + ", case chaos " + o.chaos +
      " · junk " + pct(o.junk_pct) + "% · cap-unknown " + pct(o.cap_unknown_pct) + "%";
  }

  function strip(e, box) {
    const wrap = el("div");
    const s = el("div", { class: "strip", role: "list", "aria-label": "suspect % per page" });
    const info = el("div", { class: "page-info small" });
    (e.rows || []).forEach((r) => {
      const o = rowObj(r);
      const h = o.pct === null ? 3 : Math.max(2, Math.min(60, (o.pct / 20) * 60));
      const bar = el("div", { class: "bar " + o.cls, title: "p. " + o.page + ": " + pct(o.pct) + "%", "data-page": o.page });
      bar.style.height = h + "px";
      if (selected[e.id] === o.page) bar.classList.add("sel");
      bar.addEventListener("click", (ev) => {
        ev.stopPropagation();
        selected[e.id] = o.page;
        s.querySelectorAll(".bar.sel").forEach((b) => b.classList.remove("sel"));
        bar.classList.add("sel");
        info.textContent = pageLine(e, o.page);
        box.querySelectorAll(".sample").forEach((x) => x.classList.toggle("sel", Number(x.dataset.page) === o.page));
        const hit = box.querySelector('.sample[data-page="' + o.page + '"]');
        if (hit) hit.scrollIntoView({ block: "nearest" });
        else info.textContent += " (no picture embedded for this page)";
      });
      s.appendChild(bar);
    });
    if (selected[e.id]) info.textContent = pageLine(e, selected[e.id]);
    wrap.append(s, info);
    return wrap;
  }

  function samples(e) {
    const box = el("div", { class: "samples" });
    for (const im of e.images || []) {
      const fig = el("div", { class: "sample", "data-page": im.page });
      const img = el("img", { alt: "page " + im.page, width: im.w, height: im.h });
      img.src = im.src;
      const right = el("div");
      right.appendChild(el("div", { class: "cap" }, "p. " + im.page + " (" + im.label + ")"));
      right.appendChild(el("pre", null, im.text ? im.text : "(no text on this page)"));
      if (im.trial_text) {
        right.appendChild(el("div", { class: "cap" }, "trial OCR:"));
        right.appendChild(el("pre", null, im.trial_text));
      }
      fig.append(img, right);
      box.appendChild(fig);
    }
    return box;
  }

  function trialTable(t) {
    const wrap = el("div");
    if (!t.ran) { wrap.appendChild(el("p", null, t.note || "trial not run")); return wrap; }
    const tab = el("table", { class: "mini small" });
    const head = el("tr");
    ["page", "kind", "layer %", "trial %", "cap-unknown % layer / trial", "words layer / trial", ""].forEach((h) => head.appendChild(el("th", null, h)));
    tab.appendChild(head);
    for (const p of t.pages || []) {
      const tr = el("tr");
      [p.page, p.kind, pct(p.layer_pct), pct(p.trial_pct), pct(p.layer_cap) + " / " + pct(p.trial_cap),
        (p.layer_words || 0) + " / " + (p.trial_words || 0),
        p.error ? "failed: " + p.error : ""].forEach((v) => tr.appendChild(el("td", null, v)));
      tab.appendChild(tr);
    }
    wrap.appendChild(tab);
    const lines = [];
    if (t.verdict) lines.push(t.verdict.why);
    lines.push("Tesseract " + (t.tesseract || "") + ", languages " + (t.langs || ""));
    if (t.note) lines.push(t.note);
    wrap.appendChild(list(lines));
    return wrap;
  }

  function dupList(e) {
    const d = e.duplicates || {};
    const items = [];
    (d.same_work || []).forEach((x) => items.push("same work: " + (BY_ID[x.id] ? BY_ID[x.id].rel : x.id) + " (containment " + x.containment.toFixed(3) + ")"));
    (d.related || []).forEach((x) => items.push("related: " + (BY_ID[x.id] ? BY_ID[x.id].rel : x.id) + " (containment " + x.containment.toFixed(3) + ")"));
    return items.length ? list(items) : el("p", { class: "muted" }, "none");
  }

  function editor(e) {
    const d = current(e), s = sugg(e);
    const form = el("div", { class: "editor" });
    const stop = (n) => { n.addEventListener("click", (ev) => ev.stopPropagation()); return n; };
    const add = (label, input) => { form.append(el("label", null, label), stop(input)); return input; };

    const action = add("Action", el("select"));
    ACTIONS.forEach((a) => { const o = el("option", { value: a }, a); if (a === d.action) o.selected = true; action.appendChild(o); });
    action.addEventListener("change", () => setField(e, "action", action.value));

    const pages = add("Pages", el("input", { type: "text", placeholder: "all, or e.g. 4, 7-9", value: pagesText(d.pages) }));
    pages.addEventListener("change", () => {
      const v = parsePages(pages.value);
      if (v === undefined) { say("Pages: use \"all\" or a list like 4, 7-9.", true); pages.value = pagesText(current(e).pages); return; }
      setField(e, "pages", v);
    });

    const reason = add("Reason", el("input", { type: "text", list: "gpdf-reasons", value: d.reason || "" }));
    reason.addEventListener("change", () => setField(e, "reason", reason.value.trim() || null));

    const dup = add("Duplicate of", el("select"));
    const choices = [""].concat(((e.duplicates || {}).same_work || []).map((x) => x.id));
    if (d.duplicate_of && choices.indexOf(d.duplicate_of) < 0) choices.push(d.duplicate_of);
    choices.forEach((id) => {
      const o = el("option", { value: id }, id ? (BY_ID[id] ? BY_ID[id].rel : id) : "(none)");
      if (id === (d.duplicate_of || "")) o.selected = true;
      dup.appendChild(o);
    });
    dup.addEventListener("change", () => setField(e, "duplicate_of", dup.value || null));

    for (const [label, field] of [["Title", "title"], ["Author", "author"]]) {
      const inp = add(label, el("input", { type: "text", value: d[field] || "" }));
      inp.addEventListener("change", () => setField(e, field, inp.value));
    }
    for (const [label, field] of [["Year", "year"], ["Page offset", "page_offset"]]) {
      const inp = add(label, el("input", { type: "text", inputmode: "numeric", value: d[field] === null || d[field] === undefined ? "" : d[field] }));
      inp.addEventListener("change", () => {
        const v = intOrNull(inp.value);
        if (v === undefined) { say(label + ": a whole number, or empty.", true); inp.value = current(e)[field] === null ? "" : current(e)[field]; return; }
        setField(e, field, v);
      });
    }
    const notes = el("textarea", { class: "wide", "aria-label": "Notes", placeholder: "notes" });
    notes.value = d.notes || "";
    notes.addEventListener("change", () => setField(e, "notes", notes.value));
    form.appendChild(stop(notes));

    const buttons = el("div", { class: "buttons wide" });
    const ok = stop(el("button", { type: "button", class: "primary confirm" }, "Confirm"));
    ok.addEventListener("click", () => { confirmEntry(e); autosave(); say("Confirmed " + e.rel + "."); renderAll(); });
    const reset = stop(el("button", { type: "button" }, "Reset to suggestion"));
    reset.addEventListener("click", () => {
      const keep = current(e);
      const fresh = defaults(e);
      ["title", "author", "year", "page_offset", "notes"].forEach((k) => { fresh[k] = keep[k]; });
      state[e.id] = fresh;
      autosave();
      renderAll();
    });
    buttons.append(ok, reset);
    form.appendChild(buttons);

    const box = el("div");
    box.appendChild(el("h3", null, "Decision"));
    const [st, why] = status(e);
    box.appendChild(el("p", null, STATUS_TEXT[st] + (why ? ": " + why : "")));
    if (differs(e)) {
      box.appendChild(el("p", { class: "warn" }, "Suggested: " + suggestionText(s) + "; decision: " + decisionText(d) + "."));
    }
    box.appendChild(form);
    return box;
  }

  function detail(e) {
    const grid = el("div", { class: "detail-grid" });
    const left = el("div"), right = el("div");
    const s = sugg(e), m = e.measured || {};
    left.appendChild(el("h3", null, "Suggestion: " + (e.status === "missing" ? "missing file (last suggestion) " : "") + suggestionText(s)));
    left.appendChild(list(s.reasons || []));
    if (e.error) left.appendChild(el("p", { class: "err" }, "Could not read: " + e.error));
    const facts = [];
    if (m.title || m.author) facts.push("metadata: " + [m.title, m.author].filter(Boolean).join(" / "));
    if (m.producer || m.creator) facts.push("producer: " + [m.producer, m.creator].filter(Boolean).join(" / "));
    if (m.bookmarks !== undefined) facts.push("bookmarks: " + m.bookmarks);
    if (m.encoding) facts.push("encoding: " + m.encoding);
    if (m.boilerplate) facts.push("boilerplate lines removed: " + Object.keys(m.boilerplate).map((k) => k + " " + m.boilerplate[k]).join(", "));
    if (m.no_text) facts.push("pages without text: blank " + m.no_text.blank.length + ", ink " + m.no_text.ink.length + ", empty " + m.no_text.empty.length);
    if (m.score) facts.push("per 1000 words: merged " + m.score.per_1000.merged + ", letter-spaced " + m.score.per_1000.spaced +
      ", digit " + m.score.per_1000.digit + ", case chaos " + m.score.per_1000.chaos + "; junk " + m.score.junk_pct + "%; p95 " + pct(m.score.p95) + "%");
    if (e.working_file) facts.push("working file: " + e.working_file.path);
    if (e.source_layer && e.source_layer.score) facts.push("source's own layer: median " + pct(e.source_layer.score.median) +
      "%, weighted " + pct(e.source_layer.score.weighted) + "%, poor pages " + e.source_layer.score.poor);
    if (e.previous_id) facts.push("replaces an earlier version of this file (" + e.previous_id.slice(0, 12) + "...)");
    facts.push("id " + e.id.slice(0, 16) + "..., first seen " + e.first_seen + ", last seen " + e.last_seen);
    left.appendChild(el("h3", null, "Facts"));
    left.appendChild(list(facts));
    if ((e.rows || []).length) {
      left.appendChild(el("h3", null, "Suspect % per page (click a bar)"));
      const box = samples(e);
      left.appendChild(strip(e, box));
      if ((e.images || []).length) {
        left.appendChild(el("h3", null, "Worst pages and pages without text"));
        left.appendChild(box);
      }
    }
    if (e.trial) {
      left.appendChild(el("h3", null, "Trial"));
      left.appendChild(trialTable(e.trial));
    }
    left.appendChild(el("h3", null, "Duplicates and related files"));
    left.appendChild(dupList(e));
    right.appendChild(editor(e));
    grid.append(left, right);
    return grid;
  }

  // ------------------------------------------------------------------ top
  function renderTop() {
    let total = 0, done = 0, again = 0;
    for (const e of ENTRIES) {
      if (e.status === "missing") continue;
      total++;
      const st = status(e)[0];
      if (st === "confirmed") done++;
      if (st === "reconfirm") again++;
    }
    $("progress").textContent = "Confirmed " + done + " / " + total + (again ? " (" + again + " need re-confirm)" : "");
    document.querySelectorAll("[data-filter]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.filter === filter));
    });
    $("where").textContent = "Catalogue: " + DATA.folder + " · save decisions there as " + FILE_NAME +
      " · generated " + DATA.generated;
  }

  function renderAll() {
    renderTop();
    renderRows();
  }

  // ------------------------------------------------------------------ wiring
  const datalist = el("datalist", { id: "gpdf-reasons" });
  REASONS.forEach((r) => datalist.appendChild(el("option", { value: r })));
  document.body.appendChild(datalist);

  $("save").addEventListener("click", save);
  $("load").addEventListener("click", () => $("load-input").click());
  $("load-input").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) load(f);
  });
  $("sort").addEventListener("change", () => { sort = $("sort").value; renderRows(); });
  $("confirm-shown").addEventListener("click", () => {
    let n = 0;
    for (const e of ENTRIES) {
      if (e.status === "missing" || !visible(e)) continue;
      confirmEntry(e);
      n++;
    }
    autosave();
    say("Confirmed " + n + (n === 1 ? " file." : " files."));
    renderAll();
  });
  document.querySelectorAll("[data-filter]").forEach((b) => {
    b.addEventListener("click", () => { filter = b.dataset.filter; renderAll(); });
  });
  window.addEventListener("beforeunload", (e) => {
    if (!dirty()) return;
    e.preventDefault();
    e.returnValue = "";
  });

  const savedState = readSaved();
  if (savedState && snap(savedState.state) !== snap(state)) {
    const ids = new Set(Object.keys(savedState.state).concat(Object.keys(state)));
    let n = 0;
    ids.forEach((k) => { if (snap(savedState.state[k] || null) !== snap(state[k] || null)) n++; });
    $("restore-text").textContent = "Restore your unsaved decisions (" + n + (n === 1 ? " file" : " files") +
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
  window.gpdfCatalogue = Object.freeze({
    decisionsText: decisionsText,
    status: (id) => status(BY_ID[id])[0],
    dirty: dirty,
  });
})();
