/* Notified UI. Talks to Python through window.pywebview.api; Python pushes events via RW.emit(). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_LABEL = { "To review": "To review", "Relevant - action needed": "Action", "Reviewed / noted": "Noted", "Not relevant": "Not relevant" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

window.RW = {
  handlers: {},
  on(name, fn) { (this.handlers[name] ||= []).push(fn); },
  emit(name, payload) { (this.handlers[name] || []).forEach((fn) => fn(payload)); },
};

const S = {
  boot: null, api: null, rows: [], byUid: new Map(), sel: null,
  scope: "run", showNoise: false, running: false, regulatorsInHistory: [],
};

/* ---------------- helpers ---------------- */
const isDark = () => document.documentElement.dataset.theme === "dark";
function tabColor(reg) {
  const t = S.boot.fileTabs[reg];
  return t ? t[isDark() ? 1 : 0] : "var(--ink-3)";
}
function parseISO(s) { if (!s) return null; const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
function iso(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function shortDate(d) { return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; }
function dayHeading(d) {
  const today = parseISO(S.boot.today);
  const diff = Math.round((today - d) / 86400000);
  const label = `${d.getDate()} ${MONTHS_LONG[d.getMonth()]}${d.getFullYear() !== today.getFullYear() ? " " + d.getFullYear() : ""}`;
  if (diff === 0) return `Today, ${label}`;
  if (diff === 1) return `Yesterday, ${label}`;
  return `${DAYS_LONG[d.getDay()]}, ${label}`;
}
function snippetHtml(s) { return esc(s).replace(/«/g, "<mark>").replace(/»/g, "</mark>"); }
function plural(n, one, many) { return `${n.toLocaleString("en-IN")} ${n === 1 ? one : (many || one + "s")}`; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

let toastTimer;
function toast(html, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

/* ---------------- theme ---------------- */
const mq = window.matchMedia("(prefers-color-scheme: dark)");
function applyTheme(choice) {
  const t = choice === "system" ? (mq.matches ? "dark" : "light") : choice;
  document.documentElement.dataset.theme = t;
  $$("#themeSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.theme === choice)));
  if (S.rows.length) renderList();
  if (S.boot) renderSources();
}
mq.addEventListener("change", () => S.boot && S.boot.settings.theme === "system" && applyTheme("system"));

/* ---------------- views ---------------- */
function showView(name) {
  $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.id === `view-${name}`));
  if (name !== "register") closeDrawer();
  if (name === "digest") prepDigest();
}

/* ---------------- period & fetch ---------------- */
function sinceFor(days) {
  const today = parseISO(S.boot.today);
  if (days === -1) return parseISO(S.boot.fyStart);
  const d = new Date(today); d.setDate(d.getDate() - days); return d;
}
function setPeriod(days, save = true) {
  $$(".period button").forEach((b) => b.setAttribute("aria-checked", String(Number(b.dataset.days) === days)));
  if (days !== 0) $("#fromDate").value = iso(sinceFor(days));
  if (save && days !== 0) S.api.set_setting("days", days);
}
function setBusy(on) {
  S.running = on;
  const btn = $("#fetchBtn");
  btn.setAttribute("aria-busy", String(on));
  $("#stopBtn").hidden = !on;
  if (!on) { $("#fetchFill").style.width = "0"; $("#fetchText").textContent = "Fetch updates"; }
}
async function startFetch() {
  if (S.running) return;
  const since = $("#fromDate").value;
  if (!since) { toast("Pick a start date first."); return; }
  const on = S.boot.groups.flatMap((g) => g.sources).filter((s) => s.on).length;
  setBusy(true);
  $("#fetchText").textContent = `Checking 0 of ${on}`;
  $("#fetchMeta").textContent = `Since ${shortDate(parseISO(since))}`;
  const ok = await S.api.fetch(since, false);
  if (!ok) setBusy(false);
}
RW.on("fetch:started", (p) => { setBusy(true); $("#fetchText").textContent = "Scheduled check…"; });
RW.on("fetch:progress", (p) => {
  if (!S.running) setBusy(true);
  if (p.phase === "sources") {
    $("#fetchFill").style.width = `${Math.round((p.done / p.total) * 88)}%`;
    $("#fetchText").textContent = `Checking ${p.done} of ${p.total}`;
  } else {
    $("#fetchFill").style.width = "94%";
    const l = p.line.replace(/\.\.\.$/, "…");
    $("#fetchText").textContent = /PDF/.test(l) ? (/Index/.test(l) ? "Indexing PDFs…" : "Saving PDFs…") : "Writing tracker…";
  }
});
RW.on("fetch:done", async (p) => {
  $("#fetchFill").style.width = "100%";
  setTimeout(() => setBusy(false), 350);
  let msg = p.stopped ? "<b>Stopped.</b> " : "";
  msg += p.new ? `<b>${plural(p.new, "new entry", "new entries")}</b> in the register.` : "No new entries since the last fetch.";
  if (p.pdfs) msg += ` ${plural(p.pdfs, "PDF")} saved.`;
  if (p.failures.length) msg += ` ${plural(p.failures.length, "source")} couldn't be read: ${esc(p.failures.slice(0, 3).join(", "))}${p.failures.length > 3 ? "…" : ""}.`;
  if (p.trackerCopy) msg += " The tracker was open in Excel, so a copy was saved.";
  toast(msg, 9000);
  $("#fetchMeta").textContent = "Fetched just now";
  S.scope = "run";
  syncScope();
  showView("register");
  await loadRegister();
});
RW.on("fetch:error", (msg) => { setBusy(false); toast(`<b>Fetch failed.</b> ${esc(msg)}`, 10000); });

/* ---------------- register ---------------- */
function syncScope() { $$(".filters .seg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.scope === S.scope))); }

async function loadRegister() {
  const res = await S.api.results($("#q").value.trim(), $("#fReg").value, $("#fTopic").value, S.scope, S.showNoise);
  S.rows = res.rows;
  S.byUid = new Map(res.rows.map((r) => [r.uid, r]));
  S.hasHistory = res.hasHistory;
  S.regulatorsInHistory = res.regulators;
  fillSelect($("#fReg"), res.regulators, "All regulators");
  fillSelect($("#fTopic"), res.topics, "All topics");
  const newCount = res.rows.filter((r) => r.new).length;
  $("#navRegister").textContent = newCount ? `${newCount} new` : "";
  const last = S.boot.lastRun;
  if (!res.hasHistory) $("#regSub").textContent = "Nothing fetched yet.";
  else if (S.scope === "run" && res.lastRunCount) $("#regSub").textContent = `${plural(res.rows.length, "entry", "entries")} from the latest fetch`;
  else $("#regSub").textContent = `${plural(res.rows.length, "entry", "entries")} across everything fetched so far`;
  const note = $("#noiseNote");
  if (res.hidden) {
    note.hidden = false;
    note.innerHTML = `${plural(res.hidden, "event, course, student or admin notice")} hidden. <button class="linkish" id="showNoise">Show them</button>`;
    $("#showNoise").onclick = () => { S.showNoise = true; loadRegister(); };
  } else if (S.showNoise) {
    note.hidden = false;
    note.innerHTML = `Showing events, courses, student and admin notices. <button class="linkish" id="hideNoise">Hide them</button>`;
    $("#hideNoise").onclick = () => { S.showNoise = false; loadRegister(); };
  } else note.hidden = true;
  renderList();
}

function fillSelect(sel, values, allLabel) {
  const cur = sel.value;
  sel.innerHTML = `<option value="">${allLabel}</option>` + values.map((v) => `<option>${esc(v)}</option>`).join("");
  sel.value = values.includes(cur) ? cur : "";
}

function stampFor(r) {
  const label = STATUS_LABEL[r.status];
  if (label === "Action") return `<span class="stamp is-action">Action</span>`;
  if (label === "Noted") return `<span class="stamp is-noted">Noted</span>`;
  if (label === "Not relevant") return `<span class="stamp is-irrelevant">Not relevant</span>`;
  if (r.new) return `<span class="stamp">New</span>`;
  if (r.late) return `<span class="stamp is-late">Posted late</span>`;
  return "";
}

function entryHtml(r) {
  const c = tabColor(r.regulator);
  const topics = r.tags.filter((t) => t !== "Deadline / Extension").slice(0, 2).join(", ");
  const deadline = r.tags.includes("Deadline / Extension") ? `<span>Deadline or extension</span>` : "";
  const also = r.also.length ? `<span>Also via ${esc([...new Set(r.also.map((a) => a.regulator || a.source))].join(", "))}</span>` : "";
  return `<article class="entry${S.sel === r.uid ? " is-selected" : ""}" data-uid="${r.uid}" style="--tab:${c};--tab-text:${c}">
    <span class="tab"></span>
    <div class="who"><div class="reg">${esc(r.regulator)}</div><div class="type">${esc(r.type)}</div></div>
    <div class="what">
      <div class="title">${esc(r.title)}</div>
      <div class="sub">${r.ref ? `<span class="ref">${esc(r.ref)}</span>` : ""}${topics ? `<span>${esc(topics)}</span>` : ""}${deadline}${also}</div>
      ${r.snippet ? `<div class="snip">${snippetHtml(r.snippet)}</div>` : ""}
    </div>
    <div class="mark-col">${stampFor(r)}</div>
  </article>`;
}

function renderList() {
  const list = $("#list"), empty = $("#empty");
  if (!S.rows.length) {
    list.innerHTML = "";
    empty.hidden = false;
    const on = S.boot.groups.flatMap((g) => g.sources).filter((s) => s.on).length;
    empty.innerHTML = !S.hasHistory
      ? `<h3>Nothing in the register yet</h3><p>Choose how far back to look in the sidebar, then fetch updates from the ${on} sources that are switched on.</p><div><button class="btn" id="emptyFetch">Fetch updates</button></div>`
      : `<h3>No entries match</h3><p>Try a different search, regulator or topic, or look at everything fetched so far.</p><div><button class="btn btn-quiet" id="emptyClear">Clear filters</button></div>`;
    const f = $("#emptyFetch"); if (f) f.onclick = startFetch;
    const c = $("#emptyClear"); if (c) c.onclick = () => { $("#q").value = ""; $("#fReg").value = ""; $("#fTopic").value = ""; S.scope = "all"; syncScope(); loadRegister(); };
    return;
  }
  empty.hidden = true;
  const groups = new Map();
  for (const r of S.rows) {
    const k = r.date || "undated";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  let html = "";
  for (const [k, rows] of groups) {
    const head = k === "undated" ? "No date given" : dayHeading(parseISO(k));
    html += `<h2 class="day">${esc(head)}<small>${rows.length}</small></h2>` + rows.map(entryHtml).join("");
  }
  list.innerHTML = html;
}

function selectUid(uid, open = true, scroll = false) {
  S.sel = uid;
  $$(".entry.is-selected").forEach((e) => e.classList.remove("is-selected"));
  const el = $(`.entry[data-uid="${uid}"]`);
  if (el) { el.classList.add("is-selected"); if (scroll) el.scrollIntoView({ block: "nearest" }); }
  if (open) openDrawer(S.byUid.get(uid));
}

function moveSel(delta) {
  const uids = S.rows.map((r) => r.uid);
  if (!uids.length) return;
  const i = Math.max(-1, uids.indexOf(S.sel));
  const n = Math.min(uids.length - 1, Math.max(0, i + delta));
  selectUid(uids[n], $("#drawer").classList.contains("is-open"), true);
}

/* ---------------- drawer ---------------- */
let saveTimer;
function openDrawer(r) {
  if (!r) return;
  const c = tabColor(r.regulator);
  const d = $("#drawer");
  d.style.setProperty("--tab-text", c);
  $("#dMeta").innerHTML = `<b>${esc(r.regulator)}</b><span>${esc(r.type || "Update")}</span><span>${r.date ? shortDate(parseISO(r.date)) : "No date given"}</span>${r.late ? "<span>Posted late</span>" : ""}`;
  $("#dTitleTxt").textContent = r.title;
  $("#dRef").textContent = r.ref || "";
  $("#dRef").hidden = !r.ref;
  $("#dPdf").disabled = !r.pdf;
  $("#dCopy").disabled = !r.ref;
  const snip = $("#dSnip");
  snip.hidden = !r.snippet;
  snip.innerHTML = r.snippet ? `Found in the PDF: ${snippetHtml(r.snippet)}` : "";
  $("#stamps").innerHTML = S.boot.statuses.map((s) =>
    `<button role="radio" data-s="${esc(s)}" aria-checked="${r.status === s}">${STATUS_LABEL[s] || esc(s)}</button>`).join("");
  $("#dOwner").value = r.owner || "";
  $("#dNotes").value = r.notes || "";
  $("#dSaved").textContent = "";
  $("#dTagsBlock").hidden = !r.tags.length;
  $("#dTags").innerHTML = r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  $("#dAlsoBlock").hidden = !r.also.length;
  $("#dAlso").innerHTML = r.also.map((a, i) => `<li><span>${esc(a.source)}</span>${a.url ? `<button data-i="${i}">Open</button>` : ""}</li>`).join("");
  $$("#dAlso button").forEach((b) => (b.onclick = () => S.api.open_url(r.also[Number(b.dataset.i)].url)));
  $("#dSource").textContent = `Source: ${r.source}`;
  d.classList.add("is-open");
  d.setAttribute("aria-hidden", "false");
}
function closeDrawer() {
  const d = $("#drawer");
  d.classList.remove("is-open");
  d.setAttribute("aria-hidden", "true");
}
async function saveMeta(immediate = false) {
  const r = S.byUid.get(S.sel);
  if (!r) return;
  const status = $("#stamps button[aria-checked='true']")?.dataset.s || "";
  const owner = $("#dOwner").value, notes = $("#dNotes").value;
  const run = async () => {
    await S.api.save_meta(r.uid, status, owner, notes);
    Object.assign(r, { status, owner, notes });
    const el = $(`.entry[data-uid="${r.uid}"] .mark-col`);
    if (el) el.innerHTML = stampFor(r);
    $("#dSaved").textContent = "Saved. The Excel tracker picks this up next time you open it.";
  };
  clearTimeout(saveTimer);
  if (immediate) await run(); else saveTimer = setTimeout(run, 600);
}
async function copyRef() {
  const r = S.byUid.get(S.sel);
  if (!r?.ref) return;
  try { await navigator.clipboard.writeText(r.ref); }
  catch {
    const ta = document.createElement("textarea"); ta.value = r.ref; document.body.append(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  $("#dSaved").textContent = `Copied ${r.ref}`;
}

/* ---------------- sources ---------------- */
function srcLabel(s) { return s.name.replace(new RegExp(`^${s.regulator}\\s*-\\s*`), "").replace(/\s*\(via [^)]*\)/, ""); }
function renderSources() {
  const q = $("#srcQ").value.trim().toLowerCase();
  const pdf = new Set(S.boot.pdfRegulators);
  let html = "";
  for (const g of S.boot.groups) {
    const rows = g.sources.filter((s) => !q || `${s.name} ${s.regulator} ${g.name}`.toLowerCase().includes(q));
    if (!rows.length) continue;
    const on = g.sources.filter((s) => s.on).length;
    const all = on === g.sources.length;
    html += `<section class="group">
      <div class="group-head"><h2>${esc(g.name)}</h2><span class="count">${on} of ${g.sources.length} on</span>
        <input type="checkbox" class="switch${on && !all ? " is-mixed" : ""}" data-group="${esc(g.name)}" ${all ? "checked" : ""} aria-label="All ${esc(g.name)} sources"></div>
      ${rows.map((s) => `<div class="src" style="--tab:${tabColor(s.regulator)}">
        <span class="tab"></span>
        <label class="name" for="s_${s.id}"><span>${esc(srcLabel(s))}</span><small>${esc(s.regulator)}${s.name.includes("(via") ? ", via " + esc(s.name.match(/\(via ([^)]*)\)/)[1]) : ""}</small></label>
        <span>${s.highVolume ? `<span class="hv">High volume</span>` : ""}</span>
        <input type="checkbox" class="switch" id="s_${s.id}" data-src="${s.id}" ${s.on ? "checked" : ""}>
      </div>`).join("")}
      <div class="pdfs"><span>Save PDFs for</span>${g.regulators.map((r) =>
        `<label><input type="checkbox" class="switch switch-sm" data-pdf="${esc(r)}" ${pdf.has(r) ? "checked" : ""}>${esc(r)}</label>`).join("")}</div>
    </section>`;
  }
  $("#sources").innerHTML = html || `<p class="head-sub">No source matches “${esc(q)}”.</p>`;
  const all = S.boot.groups.flatMap((g) => g.sources);
  const nOn = all.filter((s) => s.on).length;
  $("#navSources").textContent = `${nOn}/${all.length}`;
  $("#srcSub").textContent = `${nOn} of ${all.length} sources switched on.` +
    (S.boot.pdfRegulators.length ? ` PDFs are saved for ${S.boot.pdfRegulators.join(", ")}.` : " No PDFs are being saved.");
}
function saveSources() {
  const all = S.boot.groups.flatMap((g) => g.sources);
  S.api.save_selection(all.filter((s) => s.on).map((s) => s.id), S.boot.pdfRegulators);
  renderSources();
}

/* ---------------- digest ---------------- */
function prepDigest() {
  if (!$("#dFrom").value) $("#dFrom").value = $("#fromDate").value || S.boot.today;
  if (!$("#dTo").value) $("#dTo").value = S.boot.today;
  const regs = S.regulatorsInHistory;
  const box = $("#dRegs");
  const picked = new Set($$("#dRegs .chip[aria-pressed='true']").map((b) => b.dataset.r));
  box.innerHTML = regs.length ? regs.map((r) => `<button type="button" class="chip" data-r="${esc(r)}" aria-pressed="${picked.has(r)}">${esc(r)}</button>`).join("")
    : `<span class="hint">Fetch updates first; regulators with entries appear here.</span>`;
}

/* ---------------- settings ---------------- */
function renderSettings() {
  const s = S.boot.settings;
  $("#outDir").textContent = s.outputDir;
  $("#outDir").title = s.outputDir;
  applyTheme(s.theme);
  $$("#autoSeg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.auto === s.autoCheck)));
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  $("#autoDay").innerHTML = days.map((d, i) => `<option value="${i}">${d}</option>`).join("");
  $("#autoDay").value = String(s.autoWeekday);
  $("#autoTime").innerHTML = Array.from({ length: 17 }, (_, i) => `${String(i + 6).padStart(2, "0")}:00`).map((t) => `<option>${t}</option>`).join("");
  $("#autoTime").value = s.autoTime;
  $("#autoDay").hidden = s.autoCheck !== "weekly";
  $("#autoTime").hidden = s.autoCheck === "off";
  $("#schedText").textContent = S.boot.schedule;
  $$("[data-setting]").forEach((el) => (el.checked = !!s[el.dataset.setting]));
  $("#about").innerHTML = `Notified ${esc(S.boot.version)}. Free and open source under the MIT licence. Reads public regulator websites; titles and dates are as published, so rely on the official document before acting. <button class="linkish" id="ghLink">Source code and releases</button>`;
  $("#ghLink").onclick = () => S.api.open_url("https://github.com/aiforseries/notified");
}
async function setSetting(key, value) {
  S.boot.settings[key] = value;
  const res = await S.api.set_setting(key, value);
  if (res?.schedule) { S.boot.schedule = res.schedule; $("#schedText").textContent = res.schedule; }
}

/* ---------------- wiring ---------------- */
function bind() {
  $$(".nav-item").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
  $$(".period button").forEach((b) => (b.onclick = () => setPeriod(Number(b.dataset.days))));
  $("#fromDate").addEventListener("change", () => setPeriod(0, false));
  $("#fetchBtn").onclick = startFetch;
  $("#stopBtn").onclick = () => { S.api.cancel(); $("#fetchText").textContent = "Stopping…"; };

  const reload = debounce(loadRegister, 250);
  $("#q").addEventListener("input", reload);
  $("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadRegister(); if (e.key === "Escape") { e.target.value = ""; loadRegister(); } });
  $("#fReg").onchange = loadRegister;
  $("#fTopic").onchange = loadRegister;
  $$(".filters .seg button").forEach((b) => (b.onclick = () => { S.scope = b.dataset.scope; syncScope(); loadRegister(); }));
  $("#openTracker").onclick = async () => { if (!(await S.api.open_tracker())) toast("No Excel tracker yet. Fetch updates first."); };
  $("#openFolder").onclick = () => S.api.open_folder();

  $("#list").addEventListener("click", (e) => { const el = e.target.closest(".entry"); if (el) selectUid(el.dataset.uid); });
  $("#list").addEventListener("dblclick", (e) => { const el = e.target.closest(".entry"); if (el) S.api.open_url(S.byUid.get(el.dataset.uid).url); });
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
    if (e.key === "Escape" && $("#drawer").classList.contains("is-open")) { closeDrawer(); return; }
    if (typing || !$("#view-register").classList.contains("is-active")) return;
    if (e.key === "/") { e.preventDefault(); $("#q").focus(); }
    else if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); moveSel(1); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); moveSel(-1); }
    else if (e.key === "Enter" && S.sel) openDrawer(S.byUid.get(S.sel));
    else if (e.key === "o" && S.sel) S.api.open_url(S.byUid.get(S.sel).url);
  });

  $("#drawerClose").onclick = closeDrawer;
  $("#dOpen").onclick = () => S.sel && S.api.open_url(S.byUid.get(S.sel).url);
  $("#dPdf").onclick = () => S.sel && S.api.open_pdf(S.sel);
  $("#dCopy").onclick = copyRef;
  $("#stamps").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const was = b.getAttribute("aria-checked") === "true";
    $$("#stamps button").forEach((x) => x.setAttribute("aria-checked", "false"));
    b.setAttribute("aria-checked", String(!was));
    saveMeta(true);
  });
  $("#dOwner").addEventListener("input", () => saveMeta());
  $("#dNotes").addEventListener("input", () => saveMeta());

  $("#sources").addEventListener("change", (e) => {
    const t = e.target;
    const all = S.boot.groups.flatMap((g) => g.sources);
    if (t.dataset.src) all.find((s) => s.id === t.dataset.src).on = t.checked;
    else if (t.dataset.group) S.boot.groups.find((g) => g.name === t.dataset.group).sources.forEach((s) => (s.on = t.checked));
    else if (t.dataset.pdf) {
      const set = new Set(S.boot.pdfRegulators);
      t.checked ? set.add(t.dataset.pdf) : set.delete(t.dataset.pdf);
      S.boot.pdfRegulators = [...set];
    }
    saveSources();
  });
  $("#srcQ").addEventListener("input", renderSources);
  $("#srcAll").onclick = () => { S.boot.groups.forEach((g) => g.sources.forEach((s) => (s.on = true))); saveSources(); };
  $("#srcNone").onclick = () => { S.boot.groups.forEach((g) => g.sources.forEach((s) => (s.on = false))); saveSources(); };

  $("#dRegs").addEventListener("click", (e) => {
    const b = e.target.closest(".chip"); if (b) b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true"));
  });
  $("#digestForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const regs = $$("#dRegs .chip[aria-pressed='true']").map((b) => b.dataset.r);
    $("#dGo").disabled = true;
    $("#dStatus").textContent = "Creating the digest…";
    const res = await S.api.make_digest($("#dFrom").value, $("#dTo").value, $("#dTitle").value, $("#dSub").value, regs);
    $("#dGo").disabled = false;
    $("#dStatus").textContent = res.ok ? `Saved ${res.path.split(/[\\/]/).pop()}` : res.cancelled ? "" : res.error;
  });

  $("#outChange").onclick = async () => { const p = await S.api.pick_folder(); if (p) { S.boot.settings.outputDir = p; $("#outDir").textContent = p; loadRegister(); } };
  $("#outOpen").onclick = () => S.api.open_folder();
  $$("#themeSeg button").forEach((b) => (b.onclick = () => { setSetting("theme", b.dataset.theme); applyTheme(b.dataset.theme); }));
  $$("#autoSeg button").forEach((b) => (b.onclick = async () => {
    await setSetting("autoCheck", b.dataset.auto);
    renderSettings();
  }));
  $("#autoDay").onchange = (e) => setSetting("autoWeekday", Number(e.target.value));
  $("#autoTime").onchange = (e) => setSetting("autoTime", e.target.value);
  $$("[data-setting]").forEach((el) => (el.onchange = () => setSetting(el.dataset.setting, el.checked)));
}

async function checkUpdate() {
  const u = await S.api.check_update();
  if (!u) return;
  $("#updateText").textContent = `Notified ${u.version} is available. You have ${S.boot.version}.`;
  $("#updateBanner").hidden = false;
  $("#updateNotes").onclick = () => S.api.open_url(u.page);
  $("#updateGet").hidden = !u.canDownload;
  $("#updateGet").onclick = async () => {
    $("#updateGet").disabled = true; $("#updateGet").textContent = "Downloading…";
    const r = await S.api.download_update();
    $("#updateText").textContent = r.ok ? "Downloaded to your Downloads folder. Close Notified and run the new file." : `Download failed: ${r.error}`;
    $("#updateGet").hidden = r.ok; $("#updateGet").disabled = false; $("#updateGet").textContent = "Download";
  };
}

async function init() {
  S.api = window.pywebview.api;
  S.boot = await S.api.bootstrap();
  const days = S.boot.settings.days;
  setPeriod([7, 30, 90, -1].includes(days) ? days : 30, false);
  if (S.boot.lastRun?.at) {
    const d = new Date(S.boot.lastRun.at);
    $("#fetchMeta").textContent = `Last fetched ${d.getDate()} ${MONTHS[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  renderSettings();
  renderSources();
  bind();
  await loadRegister();
  if (!S.hasHistory) S.scope = "all";
  syncScope();
  setTimeout(checkUpdate, 2500);
}
window.addEventListener("pywebviewready", init);
