"use strict";

/* DownloadRescue window. Python finds downloads and runs the rescue; this
 * page shows them and reports each check as it happens. */

const pending = new Map();
let nextId = 1;
function call(cmd, ...args) {
  const id = nextId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    window.webkit.messageHandlers.app.postMessage({ id, cmd, args });
  });
}
const on = {};
window.App = {
  resolve(id, result) { const r = pending.get(id); pending.delete(id); if (r) r(result); },
  emit(name, data) { if (on[name]) on[name](data); },
};

const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function show(id) {
  for (const s of document.querySelectorAll("body > section")) s.hidden = s.id !== id;
}

function bytes(n) {
  if (n == null) return "unknown size";
  const units = ["bytes", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return i === 0 ? `${n} bytes` : `${v.toFixed(v < 10 ? 2 : 1)} ${units[i]}`;
}

function ago(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today at ${time}`;
  const yesterday = new Date(now - 86400000);
  if (d.toDateString() === yesterday.toDateString()) return `yesterday at ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "long" });
}

const FILE = '<svg class="icon" viewBox="0 0 16 16" fill="currentColor"><path d="M4 1.5A1.5 1.5 0 0 0 2.5 3v10A1.5 1.5 0 0 0 4 14.5h8a1.5 1.5 0 0 0 1.5-1.5V5.6L9.4 1.5H4zM4 3h4.5v3.5H12V13H4V3zm6 .6L11.4 5H10V3.6z"/><path d="M8 7v3.3L6.7 9 6 9.7l2 2 2-2-.7-.7L8.5 10.3V7z" opacity=".9"/></svg>';

// ---- the list --------------------------------------------------------------------

let downloads = [];
let current = null;

function summary(d) {
  const amount = d.total ? `${bytes(d.have)} of ${bytes(d.total)}` : `${bytes(d.have)} so far`;
  const parts = [amount];
  if (d.browser) parts.push(d.browser);
  if (d.when) parts.push(`stopped ${ago(d.when)}`);
  return parts.join(" · ");
}

function renderList() {
  if (!downloads.length) { show("empty"); return; }
  show("list");
  $("downloads").replaceChildren(...downloads.map((d) => {
    const li = el("li", "row link");
    const icon = document.createElement("template");
    icon.innerHTML = FILE;
    const text = el("div", "text");
    text.append(el("div", "title", d.name), el("div", "subtitle",
      summary(d) + (d.resuming ? " · rescue in progress" : "")));
    li.append(icon.content.firstChild, text, el("span", "chev", "›"));
    li.addEventListener("click", () => openDetail(d));
    return li;
  }));
}

async function choose() {
  const d = await call("choose");
  if (!d) return;
  if (!downloads.some((x) => x.id === d.id)) downloads.unshift(d);
  openDetail(d);
}

// ---- one download ------------------------------------------------------------------

function drawCoverage(d) {
  const bar = $("d-bar");
  const total = d.total || d.have || 1;
  bar.replaceChildren(...d.good.map(([s, e]) => {
    const span = el("span");
    span.style.left = `${(s / total) * 100}%`;
    span.style.width = `${Math.max(0.3, ((e - s) / total) * 100)}%`;
    return span;
  }));
  const pct = d.total ? Math.floor((d.have / d.total) * 100) : null;
  const pieces = d.good.length > 1 ? ` in ${d.good.length} pieces (the browser downloaded it in parallel)` : "";
  $("d-bar-text").textContent = pct != null
    ? `You have ${pct}% of it${pieces}. Only the missing parts will be downloaded.`
    : "Your browser didn't record the full size; the new link will say.";
}

function openDetail(d) {
  current = d;
  show("detail");
  $("d-name").textContent = d.name;
  $("d-sub").textContent = summary(d);
  drawCoverage(d);
  $("url").value = "";
  $("sha").value = "";
  $("d-error").hidden = true;
  $("d-hint").textContent = d.old_host
    ? `It came from ${d.old_host}. Get a fresh link for the same file there, or from a mirror, and paste it here.`
    : "Get a fresh link for the same file, from the same site or a mirror, and paste it here.";
  $("url").focus();
}

async function paste() {
  const text = await call("paste");
  if (text) $("url").value = text;
}

async function rescue() {
  const url = $("url").value.trim();
  const sha = $("sha").value.trim();
  if (!url) { error("Paste the new link first."); return; }
  if (sha && !/^[0-9a-fA-F]{64}$/.test(sha)) { error("A SHA-256 checksum is 64 letters and numbers (0-9, a-f)."); return; }
  const r = await call("rescue", current.id, url, sha);
  if (r && r.error) { error(r.error); return; }
  startWork();
}

function error(text) {
  $("d-error").textContent = text;
  $("d-error").hidden = false;
}

// ---- the rescue, step by step ----------------------------------------------------------

const STEP_ORDER = ["source", "checksum", "overlap", "spots", "checks", "download", "verify", "finish", "stop"];
const steps = new Map();

const MARK = {
  ok: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3 3 7-7"/></svg>',
  bad: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  note: '<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor"><circle cx="8" cy="8" r="2"/></svg>',
};

function startWork() {
  show("work");
  $("w-name").textContent = current.name;
  $("w-sub").textContent = "Rescuing";
  steps.clear();
  $("steps").replaceChildren();
  $("meter").hidden = true;
  $("meter-text").textContent = "";
  $("result").hidden = true;
  $("w-actions").hidden = false;
}

function setStep(name, state, text) {
  let row = steps.get(name);
  if (!row) {
    row = el("li", "row");
    row.append(el("span", "mark"), el("div", "text"));
    steps.set(name, row);
    const ordered = [...steps.entries()].sort((a, b) => STEP_ORDER.indexOf(a[0]) - STEP_ORDER.indexOf(b[0]));
    $("steps").replaceChildren(...ordered.map(([, r]) => r));
  }
  const mark = row.querySelector(".mark");
  mark.className = `mark ${state}`;
  if (state === "doing") mark.innerHTML = '<span class="spinner"></span>';
  else mark.innerHTML = MARK[state] || "";
  row.querySelector(".text").textContent = text;
}

let lastTick = { t: 0, done: 0 }, speed = 0;
on.step = (d) => setStep(d.name, d.state, d.text);
on.progress = (d) => {
  $("meter").hidden = false;
  $("fill").style.width = `${(d.done / Math.max(1, d.total)) * 100}%`;
  const now = performance.now();
  if (now - lastTick.t > 700) {
    if (lastTick.t && d.done >= lastTick.done) speed = (d.done - lastTick.done) / ((now - lastTick.t) / 1000);
    lastTick = { t: now, done: d.done };
  }
  const what = d.what === "verify" ? "Checked" : "Fetched";
  $("meter-text").textContent = `${what} ${bytes(d.done)} of ${bytes(d.total)}` +
    (speed && d.what !== "verify" ? ` · ${bytes(speed)}/s` : "");
};

function stopSpinners() {
  for (const [name, row] of steps) {
    if (row.querySelector(".mark").classList.contains("doing")) setStep(name, "bad", row.querySelector(".text").textContent);
  }
}

function finish(title, text, buttons) {
  $("w-sub").textContent = title;
  $("r-text").textContent = text;
  $("r-actions").replaceChildren(...buttons);
  $("result").hidden = false;
  $("w-actions").hidden = true;
}

function button(label, cls, onclick) {
  const b = el("button", `button ${cls || ""}`, label);
  b.addEventListener("click", onclick);
  return b;
}

const backToList = () => load();

on.done = (d) => {
  $("fill").style.width = "100%";
  finish("Rescued", `Saved as ${d.name}, checked by ${d.how}.`, [
    button("Show in Folder", "suggested", () => call("show", d.path)),
    button("Open", "", () => call("open", d.path)),
    button("Done", "flat", backToList),
  ]);
};
on.refused = (d) => {
  stopSpinners();
  setStep("stop", "bad", d.text);
  finish("Not Rescued", "Your download wasn't changed. Try a link to exactly the same file.", [
    button("Try Another Link", "suggested", () => openDetail(current)),
    button("Back", "flat", backToList),
  ]);
};
on.failed = (d) => {
  stopSpinners();
  setStep("stop", "bad", d.text);
  finish("Stopped", d.kept ? "What was downloaded so far is kept. Run the rescue again to carry on."
                           : "Your download is as it was before.", [
    button("Try Again", "suggested", () => openDetail(current)),
    button("Back", "flat", backToList),
  ]);
};

// ---- start ----------------------------------------------------------------------------

async function load() {
  downloads = await call("list");
  renderList();
}

for (const b of document.querySelectorAll('[data-action="choose"]')) b.addEventListener("click", choose);
$("back").addEventListener("click", renderList);
$("paste").addEventListener("click", paste);
$("go").addEventListener("click", rescue);
$("stop").addEventListener("click", () => call("cancel"));
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") rescue(); });

(async () => { downloads = await call("ready"); renderList(); })();
