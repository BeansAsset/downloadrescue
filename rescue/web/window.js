"use strict";

/* DownloadRescue window. Python finds downloads and runs the rescue; this
 * page shows them, with the file drawn as a block map that fills in as the
 * missing parts arrive. */

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

function mb(n) {
  if (n == null) return "?";
  if (n < 1e6) return `${(n / 1e3).toFixed(0)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}
function when(seconds) {
  if (!seconds) return "";
  const d = new Date(seconds * 1000), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today at ${time}`;
  if (d.toDateString() === new Date(now - 86400000).toDateString()) return `yesterday at ${time}`;
  return `on ${d.toLocaleDateString([], { day: "numeric", month: "long" })}`;
}
function seconds(s) {
  if (!isFinite(s) || s <= 0) return "";
  if (s < 60) return `about ${Math.max(1, Math.round(s))} second${Math.round(s) === 1 ? "" : "s"} left`;
  if (s < 3600) return `about ${Math.round(s / 60)} minute${Math.round(s / 60) === 1 ? "" : "s"} left`;
  return `about ${(s / 3600).toFixed(1)} hours left`;
}
const sum = (ranges) => ranges.reduce((n, [s, e]) => n + e - s, 0);
const label = (d) => d.name || d.id.split("/").pop();

// ---- views and the breadcrumb ---------------------------------------------------------------

function show(id, here) {
  for (const s of document.querySelectorAll("body > section")) s.hidden = s.id !== id;
  const deep = Boolean(here);
  $("crumb-root").textContent = deep ? "Failed downloads" : "DownloadRescue";
  $("crumb-root").classList.toggle("back", deep);
  $("crumb-sep").hidden = !deep;
  $("crumb-here").hidden = !deep;
  $("crumb-here").textContent = here || "";
}

// ---- the block map -------------------------------------------------------------------------------

function cover(ranges, s, e) {
  let n = 0;
  for (const [a, b] of ranges) n += Math.max(0, Math.min(b, e) - Math.max(a, s));
  return n / (e - s);
}

/* Each square is one slice of the file: dark if you have it, green if it
 * was just rescued, light if it's missing. A square that's partly there is
 * filled in proportion. */
function drawMap(map, total, have, fresh = [], cells = 192) {
  map.classList.toggle("unknown", !total);
  if (map.children.length !== cells) map.replaceChildren(...Array.from({ length: cells }, () => el("i")));
  if (!total) { for (const c of map.children) c.style.background = ""; return; }
  for (let i = 0; i < cells; i++) {
    const s = (i * total) / cells, e = ((i + 1) * total) / cells;
    const h = cover(have, s, e), n = cover(fresh, s, e);
    const c = map.children[i];
    if (h >= 0.999) c.style.background = "var(--have)";
    else if (n + h >= 0.999 && n > 0) c.style.background = h > 0
      ? `linear-gradient(90deg, var(--have) ${h * 100}%, var(--new) ${h * 100}%)` : "var(--new)";
    else if (h + n > 0) {
      const a = h * 100, b = (h + n) * 100;
      c.style.background = `linear-gradient(90deg, var(--have) ${a}%, var(--new) ${a}% ${b}%, var(--missing) ${b}%)`;
    } else c.style.background = "";
  }
}

/* In the list, a slim bar of the pieces: a block map would be too small to read. */
function strip(d) {
  const bar = el("div", "strip");
  if (!d.total) { bar.classList.add("unknown"); return bar; }
  for (const [s, e] of d.good) {
    const piece = el("span");
    piece.style.left = `${(s / d.total) * 100}%`;
    piece.style.width = `${Math.max(1, ((e - s) / d.total) * 100)}%`;
    bar.append(piece);
  }
  return bar;
}

function shape(d) {
  if (!d.total) return "Full size not known yet";
  const gaps = Math.max(0, d.good.length - 1);
  const endMissing = !d.good.length || d.good[d.good.length - 1][1] < d.total;
  if (d.good.length <= 1) return endMissing ? "The end is missing" : "";
  return `${d.good.length} pieces, ${gaps} gap${gaps === 1 ? "" : "s"}${endMissing ? " and the end" : ""} missing`;
}

function legend(box, items) {
  box.replaceChildren(...items.map(([cls, text]) => el("span", cls, text)));
}

// ---- the list ---------------------------------------------------------------------------------------

let downloads = [];
let current = null;

function amount(d) {
  return d.total ? `${mb(d.have)} of ${mb(d.total)}` : `${mb(d.have)} so far`;
}

function renderList() {
  if (!downloads.length) { show("empty"); return; }
  show("list");
  $("count").textContent = downloads.length === 1
    ? "One download stopped part way."
    : `${downloads.length} downloads stopped part way.`;
  $("downloads").replaceChildren(...downloads.map((d) => {
    const li = el("li");
    const text = el("div");
    const bits = [amount(d), d.browser, d.when && `stopped ${when(d.when)}`].filter(Boolean);
    text.append(el("div", "name", label(d)), el("p", "meta", bits.join("  ·  ") + (d.resuming ? "  ·  rescue in progress" : "")));
    li.append(text, strip(d));
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

// ---- one download ----------------------------------------------------------------------------------

function openDetail(d) {
  current = d;
  show("detail", label(d));
  $("d-name").textContent = label(d);
  $("d-meta").textContent = [d.browser && `From ${d.browser}`, d.when && `stopped ${when(d.when)}`]
    .filter(Boolean).join(", ") || "An unfinished file";
  $("d-amount").textContent = amount(d);
  $("d-shape").textContent = shape(d);
  drawMap($("d-map"), d.total, d.good);
  legend($("d-legend"), d.total ? [["h", "Downloaded"], ["m", "Missing"]] : [["m", "Size known once the new link answers"]]);
  $("url").value = "";
  $("sha").value = "";
  $("sha-box").hidden = true;
  $("sha-toggle").hidden = false;
  $("d-error").hidden = true;
  $("d-hint").textContent = d.old_host
    ? `A fresh link to the same file, from ${d.old_host} or a mirror.`
    : "A fresh link to the same file, from the same site or a mirror.";
  $("d-note").textContent = d.total ? `Only the missing ${mb(d.total - d.have)} will be downloaded.` : "Only the missing part will be downloaded.";
  $("url").focus();
}

async function paste() {
  const text = await call("paste");
  if (text) $("url").value = text;
}

function error(text) {
  $("d-error").textContent = text;
  $("d-error").hidden = false;
}

async function rescue() {
  const url = $("url").value.trim();
  const sha = $("sha").value.trim();
  if (!url) { error("Paste the new link first."); return; }
  if (sha && !/^[0-9a-fA-F]{64}$/.test(sha)) { error("A SHA-256 checksum is 64 characters, using 0-9 and a-f."); return; }
  const r = await call("rescue", current.id, url, sha);
  if (r && r.error) { error(r.error); return; }
  startWork(url);
}

// ---- rescuing ----------------------------------------------------------------------------------

const ORDER = ["source", "checksum", "overlap", "spots", "checks", "download", "verify", "finish", "stop"];
const steps = new Map();
let total = null, had = [], fresh = [], live = new Map();
let rate = 0, tick = { t: 0, done: 0 };

function host(url) {
  try { return new URL(url).hostname; } catch { return url; }
}

function startWork(url) {
  show("work", label(current));
  $("w-name").textContent = label(current);
  $("w-meta").textContent = `Rescuing from ${host(url)}`;
  total = current.total;
  had = current.good.slice();
  fresh = [];
  live = new Map();
  rate = 0;
  tick = { t: 0, done: 0 };
  steps.clear();
  $("steps").replaceChildren();
  $("progress").hidden = true;
  $("progress-fill").style.width = "0";
  drawWork();
  $("w-note").textContent = "Checking the new link";
  $("w-buttons").replaceChildren(button("Stop", "btn plain", () => call("cancel")));
}

function drawWork() {
  drawMap($("w-map"), total, had, fresh);
  const got = sum(had) + sum(fresh);
  $("w-amount").textContent = total ? `${mb(got)} of ${mb(total)}` : `${mb(got)} so far`;
  const gaps = total ? Math.max(0, had.length - 1) + (had.length && had[had.length - 1][1] < total ? 1 : 0) : 0;
  $("w-shape").textContent = fresh.length ? `Filling ${gaps === 1 ? "the gap" : `${gaps} gaps`}` : shape({ total, good: had });
  legend($("w-legend"), total ? [["h", "You had"], ["n", "Rescued"], ["m", "Still missing"]] : [["m", "Waiting for the new link"]]);
}

function setStep(name, state, text) {
  let li = steps.get(name);
  if (!li) {
    li = el("li");
    li.append(el("span", "g"), el("span", "t"));
    steps.set(name, li);
    const sorted = [...steps.entries()].sort((a, b) => ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]));
    $("steps").replaceChildren(...sorted.map(([, x]) => x));
  }
  li.className = state;
  li.querySelector(".g").textContent = { ok: "✓", bad: "✕", note: "–" }[state] || "";
  li.querySelector(".t").textContent = text;
}

on.step = (d) => setStep(d.name, d.state, d.text);

on.plan = (d) => {
  total = d.total;
  had = d.good;
  drawWork();
};

on.progress = (d) => {
  if (d.what === "download" && d.start != null) {
    live.set(d.start, d.at);
    fresh = [...live.entries()].map(([s, e]) => [s, e]);
    drawWork();
  }
  $("progress").hidden = false;
  $("progress-fill").style.width = `${(d.done / Math.max(1, d.total)) * 100}%`;
  const now = performance.now();
  if (now - tick.t > 900) {
    if (tick.t && d.done >= tick.done) rate = (d.done - tick.done) / ((now - tick.t) / 1000);
    tick = { t: now, done: d.done };
  }
  const left = rate ? seconds((d.total - d.done) / rate) : "";
  $("w-note").textContent = d.what === "verify"
    ? `Verifying ${mb(d.done)} of ${mb(d.total)}`
    : [`${mb(d.done)} of ${mb(d.total)}`, rate && `${mb(rate)}/s`, left].filter(Boolean).join("  ·  ");
};

function stopSpinners() {
  for (const [name, li] of steps) if (li.className === "doing") setStep(name, "bad", li.querySelector(".t").textContent);
}

function button(text, cls, onclick) {
  const b = el("button", cls, text);
  b.addEventListener("click", onclick);
  return b;
}

on.done = (d) => {
  fresh = total ? [[0, total]] : fresh;
  had = [];
  drawMap($("w-map"), total, [], [[0, total]]);
  $("w-amount").textContent = `${mb(total)} of ${mb(total)}`;
  $("w-shape").textContent = "Complete";
  legend($("w-legend"), [["n", "Complete and verified"]]);
  $("w-name").textContent = d.name;
  const folder = d.path.split("/").slice(-2, -1)[0] || "your files";
  $("w-meta").textContent = `Saved as ${d.name} in ${folder}`;
  $("progress").hidden = true;
  $("w-note").textContent = `Checked by ${d.how}.`;
  $("w-buttons").replaceChildren(
    button("Done", "btn plain", load),
    button("Show in Folder", "btn", () => call("show", d.path)),
    button("Open", "btn primary", () => call("open", d.path)));
};

on.refused = (d) => {
  stopSpinners();
  if (!total) legend($("w-legend"), [["m", "Left unchanged"]]);
  setStep("stop", "bad", d.text);
  $("progress").hidden = true;
  $("w-meta").textContent = "Not rescued";
  $("w-note").textContent = "Your download wasn't changed.";
  $("w-buttons").replaceChildren(
    button("Back", "btn plain", load),
    button("Try Another Link", "btn primary", () => openDetail(current)));
};

on.failed = (d) => {
  stopSpinners();
  setStep("stop", "bad", d.text);
  $("progress").hidden = true;
  $("w-meta").textContent = "Stopped";
  $("w-note").textContent = d.kept ? "What was fetched is kept. Rescue it again to carry on." : "Your download is as it was before.";
  $("w-buttons").replaceChildren(
    button("Back", "btn plain", load),
    button("Try Again", "btn primary", () => openDetail(current)));
};

// ---- start ---------------------------------------------------------------------------------------

async function load() {
  downloads = await call("list");
  renderList();
}

for (const b of document.querySelectorAll('[data-action="choose"]')) b.addEventListener("click", choose);
$("crumb-root").addEventListener("click", () => { if ($("crumb-root").classList.contains("back")) load(); });
$("paste").addEventListener("click", paste);
$("sha-toggle").addEventListener("click", () => { $("sha-box").hidden = false; $("sha-toggle").hidden = true; $("sha").focus(); });
$("go").addEventListener("click", rescue);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") rescue(); });

(async () => {
  const start = await call("ready");
  document.documentElement.classList.toggle("dark", start.dark);
  downloads = start.downloads;
  renderList();
})();
