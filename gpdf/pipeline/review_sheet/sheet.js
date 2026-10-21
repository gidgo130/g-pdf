// SPDX-License-Identifier: AGPL-3.0-or-later
// g-pdf review sheet: review the flagged pages of a split dry run offline and save a
// schema-1 decisions file for `gpdf-cli split --decisions`. Plain JS, no libraries, no
// network. Unsaved work is kept in localStorage when the browser allows it.
(function () {
  "use strict";

  const DATA = JSON.parse(document.getElementById("gpdf-data").textContent);
  const DEC = DATA.decisions;              // the dry-run decisions file, as written
  const VIEW = DATA.pages;                 // per page: size, splittable, images
  const N = DEC.pages.length;
  const MIN_CUT = 0.05, MAX_CUT = 0.95, WARN_SLANT_DEG = 3;
  const FILE_NAME = DATA.stem + "_split.decisions.json";
  const STORE_KEY = "gpdf-review:" + DATA.stem + ":" + DATA.source_size_text + ":" +
    DATA.source_mtime_ns_text;
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ state
  const r6 = (v) => Math.round(v * 1e6) / 1e6;
  const clamp = (v) => Math.min(MAX_CUT, Math.max(MIN_CUT, v));
  const flagged = (i) => DEC.pages[i].detected.flagged;
  const splittable = (i) => VIEW[i].splittable;

  function defaultCut(i) {
    const c = DEC.pages[i].detected.cut;
    return c ? [c[0], c[1]] : [0.5, 0.5];
  }

  function fromDecisions(pages) {
    // cut always holds the line shown; it is written only when the action is split
    return pages.map((p, i) => ({
      action: p.action,
      reviewed: p.reviewed,
      cut: p.cut ? [p.cut[0], p.cut[1]] : defaultCut(i),
    }));
  }

  let state = fromDecisions(DEC.pages);
  let cur = 0;
  let filter = "all";
  let savedName = null;
  let savedVia = null;

  function key(s) {
    return s.action + (s.reviewed ? "+" : "-") +
      (s.action === "split" ? r6(s.cut[0]) + "," + r6(s.cut[1]) : "");
  }
  const snapshot = (st) => st.map(key).join("|");
  const initialSnapshot = snapshot(state);
  let lastSaved = initialSnapshot;
  const dirty = () => snapshot(state) !== lastSaved;

  function changed(i) {
    const s = state[i], d = DEC.pages[i].detected;
    if (s.action !== (d.is_spread ? "split" : "keep")) return true;
    return s.action === "split" && !!d.cut &&
      (r6(s.cut[0]) !== r6(d.cut[0]) || r6(s.cut[1]) !== r6(d.cut[1]));
  }

  function slantDeg(i) {
    const c = state[i].cut, v = VIEW[i];
    return Math.atan((c[1] - c[0]) * v.width / v.height) * 180 / Math.PI;
  }

  // ------------------------------------------------------------------ storage
  function storage() {
    try { return window.localStorage || null; } catch (e) { return null; }
  }

  function autosave() {
    const s = storage();
    if (!s) return;
    try { s.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* full or blocked */ }
  }

  function forget() {
    const s = storage();
    if (!s) return;
    try { s.removeItem(STORE_KEY); } catch (e) { /* blocked */ }
  }

  function validEntry(e) {
    return e && (e.action === "split" || e.action === "keep") && typeof e.reviewed === "boolean" &&
      Array.isArray(e.cut) && e.cut.length === 2 &&
      e.cut.every((v) => typeof v === "number" && v > 0 && v < 1);
  }

  function readSaved() {
    const s = storage();
    if (!s) return null;
    try {
      const v = JSON.parse(s.getItem(STORE_KEY) || "null");
      return Array.isArray(v) && v.length === N && v.every(validEntry) ? v : null;
    } catch (e) {
      return null;
    }
  }

  // ------------------------------------------------------------------ messages
  function say(text, isError) {
    const m = $("msg");
    m.textContent = text;
    m.className = isError ? "error" : "";
    fitTop();
  }

  // ------------------------------------------------------------------ decisions file
  function decisionsText() {
    // Same shape as Decisions.to_dict(); the stamp's integers are spliced in as text
    // because source_mtime_ns does not fit a JS number exactly.
    const pages = DEC.pages.map((p, i) => {
      const s = state[i];
      return {
        page: p.page,
        detected: p.detected,
        action: s.action,
        cut: s.action === "split" ? [r6(s.cut[0]), r6(s.cut[1])] : null,
        reviewed: s.reviewed,
      };
    });
    const obj = {
      schema: DEC.schema, source: DEC.source,
      source_size: "@@gpdf-size@@", source_mtime_ns: "@@gpdf-mtime@@", pages: pages,
    };
    return JSON.stringify(obj, null, 1)
      .replace('"@@gpdf-size@@"', DATA.source_size_text)
      .replace('"@@gpdf-mtime@@"', DATA.source_mtime_ns_text) + "\n";
  }

  function rawInt(text, name) {
    const m = new RegExp('"' + name + '"\\s*:\\s*(-?\\d+)').exec(text);
    return m ? m[1] : null;
  }

  function checkLoaded(text) {
    let obj;
    try { obj = JSON.parse(text); } catch (e) { return [null, "it is not JSON (" + e.message + ")"]; }
    if (!obj || typeof obj !== "object" || obj.schema !== DEC.schema) {
      return [null, "it is not a schema " + DEC.schema + " decisions file"];
    }
    if (!Array.isArray(obj.pages) || obj.pages.length !== N) {
      const n = Array.isArray(obj.pages) ? obj.pages.length : 0;
      return [null, "it has " + n + " pages; this sheet has " + N];
    }
    if (obj.source !== DEC.source) {
      return [null, "it was made for a different source file (" + obj.source + ")"];
    }
    if (rawInt(text, "source_size") !== DATA.source_size_text) {
      return [null, "the source size differs (it was made for another version of the file)"];
    }
    if (rawInt(text, "source_mtime_ns") !== DATA.source_mtime_ns_text) {
      return [null, "the source modification time differs (it was made for another version of the file)"];
    }
    for (let i = 0; i < N; i++) {
      const p = obj.pages[i];
      const ok = p && p.page === i + 1 && (p.action === "split" || p.action === "keep") &&
        typeof p.reviewed === "boolean" &&
        (p.cut === null || p.cut === undefined || (Array.isArray(p.cut) && p.cut.length === 2 &&
          p.cut.every((v) => typeof v === "number" && v > 0 && v < 1)));
      if (!ok) return [null, "entry " + (i + 1) + " is malformed"];
    }
    return [obj, null];
  }

  async function save() {
    const text = decisionsText();
    if (typeof window.showSaveFilePicker === "function") {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: FILE_NAME,
          types: [{ description: "g-pdf decisions", accept: { "application/json": [".json"] } }],
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

  function saved(name, via) {
    lastSaved = snapshot(state);
    savedName = name;
    savedVia = via;
    say(via === "download"
      ? "Saved " + name + " to the browser's downloads folder."
      : "Saved " + name + ".");
    renderTop();
  }

  async function load(file) {
    let text;
    try { text = await file.text(); } catch (e) { say("Could not read " + file.name + ".", true); return; }
    const [obj, problem] = checkLoaded(text);
    if (!obj) { say("Not loaded: " + file.name + ": " + problem + ". Nothing was changed.", true); return; }
    state = obj.pages.map((p, i) => ({
      action: p.action, reviewed: p.reviewed,
      cut: p.cut ? [p.cut[0], p.cut[1]] : state[i].cut,
    }));
    lastSaved = snapshot(state);
    savedName = file.name;
    savedVia = "loaded";
    autosave();
    say("Loaded " + file.name + ".");
    renderAll();
  }

  // ------------------------------------------------------------------ actions
  function nextUnreviewed(from) {
    for (let k = 1; k <= N; k++) {
      const i = (from + k) % N;
      if (flagged(i) && !state[i].reviewed) return i;
    }
    return -1;
  }

  function decide(action) {
    const s = state[cur];
    if (action === "split" && !splittable(cur)) {
      say("Page " + VIEW[cur].page + " cannot be split; it can only be kept whole.", true);
      return;
    }
    s.action = action;
    s.reviewed = true;
    autosave();
    const nxt = nextUnreviewed(cur);
    if (nxt >= 0) {
      say("Page " + VIEW[cur].page + ": " + (action === "split" ? "split" : "kept whole") + ".");
      show(nxt);
    } else {
      say("All flagged pages are reviewed. Save decisions next.");
      renderAll();
    }
  }

  function resetCut() {
    state[cur].cut = defaultCut(cur);
    autosave();
    renderAll();
  }

  // ------------------------------------------------------------------ moving the cut
  const NUDGE = 0.001, NUDGE_BIG = 0.01;   // fractions of the page width
  const TURN = 0.1, TURN_BIG = 1;          // degrees, in display geometry

  const inRange = (c) => c.every((v) => v >= MIN_CUT - 1e-9 && v <= MAX_CUT + 1e-9);

  function setCut(c) {
    state[cur].cut = c;
    autosave();
    renderOverlays();
  }

  function shifted(c, d) {
    // the whole line moved by d, stopping where an end meets the clamp
    const lo = MIN_CUT - Math.min(c[0], c[1]), hi = MAX_CUT - Math.max(c[0], c[1]);
    const dd = d > 0 ? Math.min(d, Math.max(0, hi)) : Math.max(d, Math.min(0, lo));
    return [c[0] + dd, c[1] + dd];
  }

  function rotated(c, deg, t) {
    // the line turned clockwise by deg about its point at height t (0 top, 1 bottom);
    // null if an end would pass the clamp
    const v = VIEW[cur], k = v.width / v.height;
    const a = Math.atan((c[1] - c[0]) * k) - deg * Math.PI / 180;
    const d = Math.tan(a) / k, p = c[0] + t * (c[1] - c[0]);
    const n = [p - t * d, p + (1 - t) * d];
    return inRange(n) ? n : null;
  }

  function vertical() {
    const c = state[cur].cut, m = (c[0] + c[1]) / 2;
    setCut([m, m]);
  }

  function cutKey(e) {
    // Ctrl+Left/Right move the line, Ctrl+Up/Down turn it (clockwise / counter-clockwise)
    // about its midpoint; Shift takes bigger steps. True if the key was one of these.
    if (!e.ctrlKey || e.altKey || e.metaKey) return false;
    const c = state[cur].cut, big = e.shiftKey;
    switch (e.key) {
      case "ArrowLeft": setCut(shifted(c, -(big ? NUDGE_BIG : NUDGE))); return true;
      case "ArrowRight": setCut(shifted(c, big ? NUDGE_BIG : NUDGE)); return true;
      case "ArrowUp": case "ArrowDown": {
        const n = rotated(c, (e.key === "ArrowUp" ? 1 : -1) * (big ? TURN_BIG : TURN), 0.5);
        if (n) setCut(n);
        return true;
      }
      default: return false;
    }
  }

  function toggleKeys() {
    const box = $("keys");
    box.hidden = !box.hidden;
    $("keys-toggle").setAttribute("aria-expanded", String(!box.hidden));
  }

  function visible(i) {
    if (filter === "flagged") return flagged(i);
    if (filter === "changed") return changed(i);
    return true;
  }

  function step(dir) {
    for (let i = cur + dir; i >= 0 && i < N; i += dir) {
      if (visible(i)) { show(i); return; }
    }
  }

  function show(i) {
    cur = i;
    renderAll();
  }

  // ------------------------------------------------------------------ rendering
  function setLine(line, cut, x0, x1, w, h) {
    const sx = w / (x1 - x0);
    line.setAttribute("x1", (cut[0] - x0) * sx);
    line.setAttribute("y1", 0);
    line.setAttribute("x2", (cut[1] - x0) * sx);
    line.setAttribute("y2", h);
  }

  function drawOverlay(frame, x0, x1) {
    const v = VIEW[cur], s = state[cur], det = DEC.pages[cur].detected.cut;
    const w = (x1 - x0) * v.width, h = v.height;
    const svg = frame.querySelector("svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    svg.setAttribute("preserveAspectRatio", "none");
    const detLine = svg.querySelector(".detected");
    detLine.style.display = det ? "" : "none";
    if (det) setLine(detLine, det, x0, x1, w, h);
    const curLine = svg.querySelector(".current");
    setLine(curLine, s.cut, x0, x1, w, h);
    const inactive = s.action !== "split";
    curLine.classList.toggle("inactive", inactive);
    frame.querySelectorAll(".handle").forEach((hd) => {
      const end = Number(hd.dataset.end);
      hd.style.left = ((s.cut[end] - x0) / (x1 - x0)) * 100 + "%";
      hd.classList.toggle("inactive", inactive);
    });
  }

  function closeRange() {
    const c = VIEW[cur].close;
    return c ? c.x : [0, 1];
  }

  function renderOverlays() {
    drawOverlay($("main-frame"), 0, 1);
    if (VIEW[cur].close) drawOverlay($("close-frame"), closeRange()[0], closeRange()[1]);
    renderFacts();
    updateTile(cur);
  }

  function renderReview() {
    const v = VIEW[cur];
    const img = $("main-img");
    const pic = v.full || v.thumb;
    if (img.getAttribute("src") !== pic.src) img.src = pic.src;
    img.alt = "Page " + v.page;
    $("main-caption").textContent = v.full ? "" :
      "low-resolution preview (only flagged pages are embedded at full resolution)";
    const close = $("closeup");
    close.hidden = !v.close;
    if (v.close) {
      const ci = $("close-img");
      if (ci.getAttribute("src") !== v.close.src) ci.src = v.close.src;
      const dpr = window.devicePixelRatio || 1;       // one image pixel per screen pixel
      ci.style.width = v.close.w / dpr + "px";
      ci.style.height = v.close.h / dpr + "px";
    }
    const unsplittable = !splittable(cur);
    $("act-split").disabled = unsplittable;
    $("act-accept").innerHTML = "<kbd>Enter</kbd> Accept as shown (" +
      (unsplittable ? "keep" : state[cur].action) + ")";
    $("unsplittable").hidden = !unsplittable;
    $("prev").disabled = !Array.from({ length: cur }, (_, i) => i).some(visible);
    $("next").disabled = !Array.from({ length: N - cur - 1 }, (_, k) => cur + 1 + k).some(visible);
    const reasons = $("reasons");
    reasons.textContent = "";
    for (const r of DEC.pages[cur].detected.reasons) {
      const li = document.createElement("li");
      li.textContent = r;
      reasons.appendChild(li);
    }
    renderOverlays();
  }

  function renderFacts() {
    const v = VIEW[cur], s = state[cur], d = DEC.pages[cur].detected;
    $("page-title").textContent = "Page " + v.page + " of " + N;
    const slant = slantDeg(cur);
    const facts = [
      ["Detector", (d.is_spread ? "spread" : "single") + ", confidence " + d.confidence.toFixed(2)],
      ["Flagged", d.flagged ? "yes" : "no"],
      ["Action", s.action === "split" ? "split" : "keep whole"],
      ["Reviewed", s.reviewed ? (d.flagged ? "yes" : "yes (detector sure)") : "not yet"],
      ["Cut", r6(s.cut[0]).toFixed(3) + " / " + r6(s.cut[1]).toFixed(3) + " of the width"],
      ["Slant", (slant >= 0 ? "+" : "") + slant.toFixed(2) + " deg"],
    ];
    if (changed(cur)) facts.push(["Changed", "differs from the detector"]);
    const dl = $("facts");
    dl.textContent = "";
    for (const [k, val] of facts) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k;
      dd.textContent = val;
      dl.append(dt, dd);
    }
    const warn = $("slant-warning");
    warn.hidden = Math.abs(slant) <= WARN_SLANT_DEG;
    warn.textContent = "The cut slants " + Math.abs(slant).toFixed(1) +
      " deg (more than " + WARN_SLANT_DEG + "): check it.";
  }

  function commandText() {
    const sep = DATA.outdir.indexOf("\\") >= 0 ? "\\" : "/";
    const dec = savedName ? DATA.outdir + sep + savedName : "<saved decisions file>";
    return 'gpdf-cli split "' + DEC.source + '" -o "' + DATA.outdir + '" --decisions "' + dec + '"';
  }

  function renderTop() {
    $("file-name").textContent = DATA.source_name;
    let total = 0, done = 0;
    for (let i = 0; i < N; i++) {
      if (flagged(i)) { total++; if (state[i].reviewed) done++; }
    }
    $("progress").textContent = "Flagged reviewed: " + done + " / " + total;
    document.querySelectorAll("[data-filter]").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.filter === filter));
    });
    $("command").value = commandText();
    $("command-note").textContent = !savedName ? "" : savedVia === "download"
      ? "(move the file from your downloads folder to the output folder, or change the path)"
      : "(change the path if the file is not in the output folder)";
  }

  // contact sheet: built once, tiles updated in place
  const tiles = [];

  function buildTiles() {
    const box = $("tiles");
    const frag = document.createDocumentFragment();
    for (let i = 0; i < N; i++) {
      const v = VIEW[i];
      const b = document.createElement("button");
      b.type = "button";
      b.className = "tile";
      b.dataset.i = i;
      b.innerHTML = '<span class="tw"><img alt=""><svg class="overlay" preserveAspectRatio="none" ' +
        'viewBox="0 0 ' + v.width + " " + v.height + '"><line></line></svg></span>' +
        '<span class="meta"><span class="pno"></span></span>';
      b.querySelector("img").src = v.thumb.src;
      b.querySelector("img").alt = "Page " + v.page;
      b.querySelector(".pno").textContent = "p. " + v.page;
      b.addEventListener("click", () => { show(i); b.blur(); window.scrollTo({ top: 0 }); });
      tiles.push(b);
      frag.appendChild(b);
    }
    box.appendChild(frag);
  }

  function badge(meta, cls, text) {
    const s = document.createElement("span");
    s.className = "badge " + cls;
    s.textContent = text;
    meta.appendChild(s);
  }

  function updateTile(i) {
    const t = tiles[i], s = state[i], v = VIEW[i];
    t.hidden = !visible(i);
    t.classList.toggle("current", i === cur);
    const line = t.querySelector("line");
    line.style.display = s.action === "split" ? "" : "none";
    setLine(line, s.cut, 0, 1, v.width, v.height);
    const meta = t.querySelector(".meta");
    meta.querySelectorAll(".badge").forEach((x) => x.remove());
    if (flagged(i) && !s.reviewed) badge(meta, "amber", "flagged");
    if (flagged(i) && s.reviewed) badge(meta, "green", "reviewed");
    if (changed(i)) badge(meta, "blue", "changed");
    if (s.action !== "split") badge(meta, "grey", DEC.pages[i].detected.is_spread ? "kept whole" : "single");
  }

  function renderAll() {
    renderTop();
    renderReview();
    for (let i = 0; i < N; i++) updateTile(i);
  }

  // ------------------------------------------------------------------ dragging
  function attachDrag(frame, range) {
    frame.querySelectorAll(".handle").forEach((hd) => {
      const end = Number(hd.dataset.end);
      hd.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        hd.setPointerCapture(e.pointerId);
        const img = frame.querySelector("img").getBoundingClientRect();
        const [x0, x1] = range();
        const start = state[cur].cut.slice();
        const startX = e.clientX;
        const whole = e.shiftKey;
        const move = (ev) => {
          const d = (ev.clientX - startX) / img.width * (x1 - x0);
          if (whole || ev.shiftKey) {
            const lo = MIN_CUT - Math.min(start[0], start[1]);
            const hi = MAX_CUT - Math.max(start[0], start[1]);
            const dd = Math.min(hi, Math.max(lo, d));
            state[cur].cut = [start[0] + dd, start[1] + dd];
          } else {
            const c = start.slice();
            c[end] = clamp(start[end] + d);
            state[cur].cut = c;
          }
          renderOverlays();
        };
        const up = () => {
          hd.removeEventListener("pointermove", move);
          hd.removeEventListener("pointerup", up);
          hd.removeEventListener("pointercancel", up);
          autosave();
          renderAll();
        };
        hd.addEventListener("pointermove", move);
        hd.addEventListener("pointerup", up);
        hd.addEventListener("pointercancel", up);
      });
    });
  }

  // ------------------------------------------------------------------ wiring
  function typing(t) {
    return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
  }

  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || typing(e.target)) return;
    if (cutKey(e)) { e.preventDefault(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target && e.target.tagName === "BUTTON" && (e.key === "Enter" || e.key === " ")) return;
    switch (e.key) {
      case "s": case "S": decide("split"); break;
      case "k": case "K": decide("keep"); break;
      case "Enter": decide(splittable(cur) ? state[cur].action : "keep"); break;
      case "r": case "R": resetCut(); break;
      case "v": case "V": vertical(); break;
      case "?": toggleKeys(); break;
      case "ArrowLeft": step(-1); break;
      case "ArrowRight": step(1); break;
      default: return;
    }
    e.preventDefault();
  });

  $("act-split").addEventListener("click", () => decide("split"));
  $("act-keep").addEventListener("click", () => decide("keep"));
  $("act-accept").addEventListener("click", () => decide(splittable(cur) ? state[cur].action : "keep"));
  $("act-reset").addEventListener("click", resetCut);
  $("keys-toggle").addEventListener("click", toggleKeys);
  $("prev").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  $("save").addEventListener("click", save);
  $("load").addEventListener("click", () => $("load-input").click());
  $("load-input").addEventListener("change", (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (f) load(f);
  });
  $("copy").addEventListener("click", async () => {
    const box = $("command");
    try {
      await navigator.clipboard.writeText(box.value);
    } catch (e) {
      box.select();
      try { document.execCommand("copy"); } catch (e2) { say("Select the command and copy it by hand.", true); return; }
    }
    say("Command copied.");
  });
  document.querySelectorAll("[data-filter]").forEach((b) => {
    b.addEventListener("click", () => {
      filter = b.dataset.filter;
      if (!visible(cur)) {
        const first = state.findIndex((_, i) => visible(i));
        if (first >= 0) cur = first;
      }
      renderAll();
    });
  });
  window.addEventListener("beforeunload", (e) => {
    if (!dirty()) return;
    e.preventDefault();
    e.returnValue = "";
  });

  function fitTop() {
    document.documentElement.style.setProperty("--top-h", $("top").offsetHeight + "px");
  }
  window.addEventListener("resize", () => { fitTop(); renderReview(); });

  attachDrag($("main-frame"), () => [0, 1]);
  attachDrag($("close-frame"), closeRange);
  buildTiles();

  const savedState = readSaved();
  if (savedState) {
    const n = savedState.filter((s, i) => key(s) !== key(state[i])).length;
    if (n > 0) {
      $("restore-text").textContent = "Restore your unsaved review (" + n +
        (n === 1 ? " page" : " pages") + " changed)?";
      $("restore").hidden = false;
    }
  }
  $("restore-yes").addEventListener("click", () => {
    state = savedState.map((s) => ({ action: s.action, reviewed: s.reviewed, cut: s.cut.slice() }));
    $("restore").hidden = true;
    say("Restored your unsaved review.");
    const nxt = nextUnreviewed(-1);
    show(nxt >= 0 ? nxt : cur);
    fitTop();
  });
  $("restore-no").addEventListener("click", () => {
    forget();
    $("restore").hidden = true;
    say("Discarded the unsaved review.");
    fitTop();
  });

  const first = nextUnreviewed(-1);
  cur = first >= 0 ? first : 0;
  fitTop();
  renderAll();

  // read-only hook for tests and debugging
  window.gpdfSheet = Object.freeze({
    decisionsText: decisionsText,
    current: () => VIEW[cur].page,
    dirty: dirty,
  });
})();
