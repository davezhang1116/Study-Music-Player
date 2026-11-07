const $ = (s) => document.querySelector(s);
const audio = $("#audio");
let current = null;
let loading = false;
let sleepAt = 0;

const fmt = (s) => {
  if (!isFinite(s)) return "0:00";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
};

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = `Error ${r.status}`;
    try { const j = await r.json(); msg = j.detail?.message || j.detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(text, action, onAction, ms = 6000) {
  const t = $("#toast");
  t.innerHTML = "";
  t.append(document.createTextNode(text));
  if (action) {
    const b = document.createElement("button");
    b.textContent = action;
    b.onclick = () => { onAction(); t.hidden = true; };
    t.append(b);
  }
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), ms);
}

function setPlaying(on) {
  $("#ico-play").style.display = on ? "none" : "";
  $("#ico-pause").style.display = on ? "" : "none";
  $("#btn-play").setAttribute("aria-label", on ? "Pause" : "Play");
}

function show(t) {
  $("#title").textContent = t.title || "Untitled";
  $("#artist").textContent = t.artist || "Unknown artist";
  $("#art").src = t.artwork || "";
  $("#bg").style.backgroundImage = t.artwork ? `url("${t.artwork}")` : "none";
  const link = $("#source-link");
  link.hidden = !t.url;
  link.href = t.url || "#";
  const chips = [[t.source, "src"], [t.genre], [t.mood], [t.bpm ? `${t.bpm_measured ? "~" : ""}${Math.round(t.bpm)} bpm` : ""],
    [t.duration > 900 ? "mix" : ""]];
  $("#chips").innerHTML = "";
  for (const [label, cls] of chips) {
    if (!label) continue;
    const c = document.createElement("span");
    c.className = "chip " + (cls || "");
    c.textContent = label;
    $("#chips").append(c);
  }
  $("#btn-like").classList.remove("on");
  document.title = `${t.title} · Slow Study Radio`;
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artist, album: "Slow Study Radio",
      artwork: t.artwork ? [{ src: t.artwork, sizes: "480x480" }] : [],
    });
  }
}

async function playNext() {
  if (loading) return;
  loading = true;
  $("#btn-play").classList.add("loading");
  try {
    current = await api("/api/next");
    show(current);
    audio.src = current.stream;
    audio.play().catch(() => toast("Press play to continue"));  // browser blocked autoplay
  } catch (e) {
    toast(`Couldn't get a track: ${e.message}. Retrying…`);
    setTimeout(() => { loading = false; playNext(); }, 5000);
    return;
  } finally {
    $("#btn-play").classList.remove("loading");
  }
  loading = false;
  refreshStats();
}

const feedback = (body) => current && api("/api/feedback", { method: "POST", body: JSON.stringify({ key: current.key, ...body }) });

// ---------- controls ----------
$("#btn-play").onclick = () => {
  if (!current) return playNext();
  audio.paused ? audio.play() : audio.pause();
};
$("#btn-skip").onclick = () => { feedback({ skipped: true }); playNext(); };
$("#btn-like").onclick = () => {
  const on = !$("#btn-like").classList.contains("on");
  $("#btn-like").classList.toggle("on", on);
  feedback({ rating: on ? 1 : 0 });
  if (on) toast("Saved to your liked list");
};
$("#btn-dislike").onclick = () => {
  const t = current;
  feedback({ rating: -1, skipped: true });
  playNext();
  if (t?.artist) toast(`Skipped. Block "${t.artist}" too?`, "Block artist",
    () => api("/api/feedback", { method: "POST", body: JSON.stringify({ key: t.key, ban_artist: true }) }));
};

audio.onplay = () => setPlaying(true);
audio.onpause = () => setPlaying(false);
audio.onended = () => playNext();
audio.onerror = () => {
  if (!current) return;
  toast("That track wouldn't load, moving on");
  feedback({ skipped: true });
  setTimeout(playNext, 800);
};
audio.ontimeupdate = () => {
  const d = audio.duration || current?.duration || 0;
  $("#cur").textContent = fmt(audio.currentTime);
  $("#dur").textContent = fmt(d);
  if (!seeking && d) $("#seek").value = (audio.currentTime / d) * 1000;
  if (sleepAt && Date.now() > sleepAt) {
    audio.pause(); sleepAt = 0; $("#sleep").value = "0"; toast("Sleep timer: paused");
  }
};
let seeking = false;
$("#seek").oninput = () => (seeking = true);
$("#seek").onchange = () => {
  const d = audio.duration || current?.duration || 0;
  if (d) audio.currentTime = ($("#seek").value / 1000) * d;
  seeking = false;
};

const savedVol = parseFloat(localStorage.getItem("vol") ?? "0.8");
audio.volume = $("#vol").value = isNaN(savedVol) ? 0.8 : savedVol;
$("#vol").oninput = () => { audio.volume = $("#vol").value; try { localStorage.setItem("vol", audio.volume); } catch {} };
$("#sleep").onchange = () => {
  const m = +$("#sleep").value;
  sleepAt = m ? Date.now() + m * 60000 : 0;
  if (m) toast(`Music will pause in ${m} minutes`);
};

if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => audio.play());
  navigator.mediaSession.setActionHandler("pause", () => audio.pause());
  navigator.mediaSession.setActionHandler("nexttrack", () => $("#btn-skip").click());
}
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  if (e.code === "Space") { e.preventDefault(); $("#btn-play").click(); }
  if (e.code === "ArrowRight" && e.shiftKey) $("#btn-skip").click();
});

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((b) => (b.onclick = () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + b.dataset.view));
  if (b.dataset.view === "history") loadHistory();
  if (b.dataset.view === "settings") loadSettings();
}));

// ---------- stats & history ----------
async function refreshStats() {
  try {
    const s = await api("/api/stats");
    const hrs = (s.seconds / 3600).toFixed(1);
    $("#stats").textContent = `${s.heard} tracks heard · ${hrs} h listened · ${s.liked} liked · none will repeat`;
  } catch {}
}

async function loadHistory() {
  const list = $("#history-list");
  const rows = await api(`/api/history?liked=${$("#liked-only").checked}`);
  list.innerHTML = rows.length ? "" : '<li class="hint">Nothing yet.</li>';
  for (const r of rows) {
    const li = document.createElement("li");
    const img = document.createElement("img");
    img.src = r.artwork || ""; img.alt = ""; img.loading = "lazy";
    const t = document.createElement("div");
    t.className = "t";
    const b = document.createElement("b");
    b.textContent = (r.rating === 1 ? "♥ " : "") + r.title;
    const sp = document.createElement("span");
    sp.textContent = `${r.artist} · ${r.source} · ${new Date(r.served_at * 1000).toLocaleDateString()}${r.skipped ? " · skipped" : ""}`;
    t.append(b, sp);
    li.append(img, t);
    if (r.url) {
      const a = document.createElement("a");
      a.href = r.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = "Open ↗";
      li.append(a);
    }
    list.append(li);
  }
}
$("#liked-only").onchange = loadHistory;

// ---------- settings ----------
let settings = null;
// keyword chip state, saved with the Save button
let kwOff = new Set(), kwExtra = [], blocked = [];

const markUnsaved = () => ($("#save-msg").textContent = "Unsaved changes");

function chip(label, { on = true, removable = false, add = false, onClick }) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "kwchip" + (on ? " on" : "") + (add ? " add" : "");
  b.textContent = (add ? "+ " : "") + label + (removable ? " ×" : "");
  b.setAttribute("aria-pressed", on);
  b.onclick = () => { onClick(); markUnsaved(); renderChips(); };
  return b;
}

function renderChips() {
  const kwA = $("#kw-active"), kwS = $("#kw-suggest"), bwA = $("#bw-active"), bwS = $("#bw-suggest");
  [kwA, kwS, bwA, bwS].forEach((el) => (el.innerHTML = ""));

  for (const k of settings.keyword_builtin)
    kwA.append(chip(k, { on: !kwOff.has(k), onClick: () => (kwOff.has(k) ? kwOff.delete(k) : kwOff.add(k)) }));
  for (const k of kwExtra)
    kwA.append(chip(k, { removable: true, onClick: () => (kwExtra = kwExtra.filter((x) => x !== k)) }));
  for (const k of settings.keyword_suggestions.filter((s) => !kwExtra.includes(s)))
    kwS.append(chip(k, { on: false, add: true, onClick: () => kwExtra.push(k) }));

  for (const w of blocked)
    bwA.append(chip(w, { removable: true, onClick: () => (blocked = blocked.filter((x) => x !== w)) }));
  if (!blocked.length) bwA.innerHTML = '<span class="hint">Nothing blocked yet.</span>';
  for (const w of settings.blocked_suggestions.filter((s) => !blocked.includes(s)))
    bwS.append(chip(w, { on: false, add: true, onClick: () => blocked.push(w) }));
}

document.querySelectorAll(".kw-add").forEach((f) => (f.onsubmit = (e) => {
  e.preventDefault();
  const input = f.querySelector("input");
  const v = input.value.trim().toLowerCase();
  if (!v) return;
  if (f.dataset.target === "kw") {
    if (settings.keyword_builtin.includes(v)) kwOff.delete(v);
    else if (!kwExtra.includes(v)) kwExtra.push(v);
  } else if (!blocked.includes(v)) blocked.push(v);
  input.value = "";
  markUnsaved();
  renderChips();
}));

async function loadSettings() {
  settings = await api("/api/settings");
  const stats = await api("/api/stats");
  document.querySelectorAll("[data-src]").forEach((c) => (c.checked = !!settings.sources[c.dataset.src]));
  if (!stats.jamendo_key) $("#jamendo-hint").textContent = "needs a free JAMENDO_CLIENT_ID on the server (see README)";
  $("#yt-mixes").checked = settings.youtube_mixes;
  $("#max-bpm").value = settings.max_bpm;
  $("#bpm-val").textContent = settings.max_bpm;
  $("#min-dur").value = settings.min_duration;
  $("#max-dur").value = settings.max_duration;
  kwOff = new Set(settings.keywords_off || []);
  kwExtra = [...settings.extra_queries];
  blocked = [...settings.blocked_words];
  renderChips();
  renderBanned(settings.banned_artists);
  const errs = Object.entries(stats.source_errors || {});
  $("#save-msg").textContent = errs.length ? "Source problems: " + errs.map(([k, v]) => `${k}: ${v}`).join("; ") : "";
}

function renderBanned(list) {
  const ul = $("#banned");
  ul.innerHTML = "";
  ul.classList.toggle("empty", !list.length);
  for (const a of list) {
    const li = document.createElement("li");
    li.textContent = a;
    const x = document.createElement("button");
    x.textContent = "×"; x.title = "Unblock"; x.setAttribute("aria-label", `Unblock ${a}`);
    x.onclick = async () => {
      settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ unban: [a] }) });
      renderBanned(settings.banned_artists);
    };
    li.append(x);
    ul.append(li);
  }
}

$("#max-bpm").oninput = () => ($("#bpm-val").textContent = $("#max-bpm").value);
$("#save").onclick = async () => {
  const sources = {};
  document.querySelectorAll("[data-src]").forEach((c) => (sources[c.dataset.src] = c.checked));
  if (!Object.values(sources).some(Boolean)) return toast("Turn on at least one source");
  if (kwOff.size === settings.keyword_builtin.length && !kwExtra.length)
    return toast("Turn on at least one search keyword");
  settings = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      sources, youtube_mixes: $("#yt-mixes").checked, max_bpm: +$("#max-bpm").value,
      min_duration: +$("#min-dur").value, max_duration: +$("#max-dur").value,
      extra_queries: kwExtra, keywords_off: [...kwOff], blocked_words: blocked,
    }),
  });
  $("#save-msg").textContent = "Saved. New rules apply from the next track.";
};

refreshStats();
