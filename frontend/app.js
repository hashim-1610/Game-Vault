// ===========================================================
// 28 — Irupathiyettu — frontend
// Pure vanilla JS. No build step, no framework. Talks to the
// FastAPI backend over REST (auth, rooms) and WebSocket (live game state).
// ===========================================================

const RED_SUITS = new Set(["♥", "♦"]);
let TOKEN = localStorage.getItem("k28_token");
let ME = null;                 // {username, display_name, profile_pic}
let CONFIG = null;             // /api/config: min_bid, max_bid, bid_confirm_threshold, suits
let ws = null;
let roomCode = null;
let lastSeenEventSeq = 0;
let currentState = null;
// Bumped on every incoming state; in-flight animations compare their own
// captured value against this before painting anything, so a slow deal/play
// animation from an old snapshot can never overwrite a newer render — this
// is what was causing the UI to get stuck showing stale phases forever.
let animationEpoch = 0;

// ---------------- API helpers ----------------
async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (TOKEN) headers["Authorization"] = `Bearer ${TOKEN}`;
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) {
    const msg = (data && (data.detail || data.message)) || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

// ---------------- View switching ----------------
function showView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.add("hidden"));
  document.getElementById(id).classList.remove("hidden");
}
function showPanel(id) {
  document.querySelectorAll(".panel-view").forEach(v => v.classList.add("hidden"));
  document.getElementById(id).classList.remove("hidden");
  document.querySelectorAll(".nav-btn[data-view]").forEach(b => b.classList.remove("active"));
}

function tabSwitch(container, tabName) {
  container.querySelectorAll(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));
}

// ---------------- Avatars ----------------
// Bots don't have real photos, so they get a distinct colorful
// gradient + emoji "avatar" instead of a plain letter — assigned by the
// server per-bot (avatar_seed) so it stays consistent for that bot.
const BOT_AVATAR_STYLES = [
  { gradient: "linear-gradient(135deg,#FF9966,#FF5E62)", emoji: "🛺" },
  { gradient: "linear-gradient(135deg,#36D1DC,#5B86E5)", emoji: "☔" },
  { gradient: "linear-gradient(135deg,#F7971E,#FFD200)", emoji: "🍵" },
  { gradient: "linear-gradient(135deg,#8E2DE2,#4A00E0)", emoji: "🥥" },
  { gradient: "linear-gradient(135deg,#11998E,#38EF7D)", emoji: "🌴" },
  { gradient: "linear-gradient(135deg,#FC5C7D,#6A82FB)", emoji: "🎭" },
  { gradient: "linear-gradient(135deg,#F857A6,#FF5858)", emoji: "🎩" },
  { gradient: "linear-gradient(135deg,#00C9FF,#92FE9D)", emoji: "🚌" },
  { gradient: "linear-gradient(135deg,#DA22FF,#9733EE)", emoji: "🥁" },
  { gradient: "linear-gradient(135deg,#FF512F,#F09819)", emoji: "🎣" },
];

function avatarHtml(name, picUri, sizeClass = "avatar-md", botSeed = null) {
  if (picUri) {
    return `<img class="avatar ${sizeClass}" src="${picUri}" alt="${escapeHtml(name)}">`;
  }
  if (botSeed !== null && botSeed !== undefined) {
    const style = BOT_AVATAR_STYLES[botSeed % BOT_AVATAR_STYLES.length];
    return `<div class="avatar-fallback ${sizeClass}" style="background:${style.gradient}">${style.emoji}</div>`;
  }
  const initial = (name || "?").trim().charAt(0).toUpperCase();
  // Mid-tone swatches only — light text (see .avatar-fallback) needs to stay
  // readable against every one of these, so nothing near-black or near-white.
  const palette = ["#B8863B", "#7A2048", "#2B4C7E", "#5C3A8E", "#1F6F63"];
  let hash = 0;
  for (const c of (name || "")) hash += c.charCodeAt(0);
  const color = palette[hash % palette.length];
  return `<div class="avatar-fallback ${sizeClass}" style="background:${color}">${initial}</div>`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s == null ? "" : String(s);
  return div.innerHTML;
}

// ---------------- Cards ----------------
// Real illustrated playing cards via <use> references into cards-v2.svg —
// Chris Aguilar's Vector Playing Card Graphics Set, LGPL, see
// assets/DECK-LICENSE.txt. Symbols are indexed numerically: suit 0-3
// (spade/heart/club/diamond), rank 1-13 (ace..king).
const SUIT_INDEX = { "♠": 0, "♥": 1, "♣": 2, "♦": 3 };
const RANK_NUMBER = { "A": 1, "J": 11, "Q": 12, "K": 13 }; // 7-10 map to themselves

function cardSvgId(card) {
  const rank = card.slice(0, -1);
  const suit = card.slice(-1);
  return `${SUIT_INDEX[suit]}_${RANK_NUMBER[rank] || rank}`;
}

function cardHtml(card, { dim = false, big = false, playable = false, mini = false, style = "" } = {}) {
  const classes = ["pcard-svg"];
  if (dim) classes.push("dim");
  if (big) classes.push("big");
  if (playable) classes.push("playable");
  if (mini) classes.push("mini");
  // cards-v2.svg's own card faces already carry a corner pip in each
  // corner — no need to draw our own index on top of it.
  return `<svg class="${classes.join(" ")}" style="${escapeHtml(style)}" viewBox="0 0 227 315" data-card="${escapeHtml(card)}">
    <use href="/assets/cards-v2.svg#${cardSvgId(card)}"></use>
  </svg>`;
}

function cardBackHtml({ mini = false } = {}) {
  const classes = ["pcard-back"];
  if (mini) classes.push("mini");
  return `<div class="${classes.join(" ")}"></div>`;
}

// ---------------- Sound ----------------
function playSound(name) {
  if (!name) return;
  try {
    const audio = new Audio(`/sounds/${name}.wav`);
    audio.volume = 0.5;
    audio.play().catch(() => {});
  } catch (e) { /* ignore */ }
}

// ===========================================================
// Auth
// ===========================================================
document.querySelectorAll("#view-auth .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#view-auth .seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("login-form").classList.toggle("hidden", btn.dataset.tab !== "login");
    document.getElementById("register-form").classList.toggle("hidden", btn.dataset.tab !== "register");
  });
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("login-error");
  errEl.textContent = "";
  const form = new FormData(e.target);
  try {
    const data = await api("/api/login", { method: "POST", json: {
      username: form.get("username"), password: form.get("password"),
    }});
    TOKEN = data.token;
    localStorage.setItem("k28_token", TOKEN);
    await bootAfterLogin();
  } catch (err) {
    errEl.textContent = err.message;
  }
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("register-error");
  const okEl = document.getElementById("register-success");
  errEl.textContent = ""; okEl.textContent = "";
  const form = new FormData(e.target);
  try {
    await api("/api/register", { method: "POST", body: form });
    okEl.textContent = "Account created — you can log in now.";
    e.target.reset();
  } catch (err) {
    errEl.textContent = err.message;
  }
});

document.getElementById("logout-btn").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST" }); } catch (e) {}
  TOKEN = null;
  localStorage.removeItem("k28_token");
  ME = null;
  if (ws) { ws.close(); ws = null; }
  showView("view-auth");
});

async function bootAfterLogin() {
  const data = await api("/api/me");
  ME = data.user;
  document.getElementById("nav-avatar").outerHTML = avatarHtml(ME.display_name, ME.profile_pic, "avatar-sm").replace("<div", '<div id="nav-avatar"').replace("<img", '<img id="nav-avatar"');
  document.getElementById("nav-name").textContent = ME.display_name;
  showView("view-app");
  showPanel("panel-play");
  document.querySelector('.nav-btn[data-view="play"]').classList.add("active");
  renderProfile(data.stats);
}

// ===========================================================
// Top nav
// ===========================================================
document.querySelectorAll(".nav-btn[data-view]").forEach(btn => {
  btn.addEventListener("click", async () => {
    document.querySelectorAll(".nav-btn[data-view]").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    const view = btn.dataset.view;
    showPanel(`panel-${view}`);
    if (view === "profile") {
      const data = await api("/api/me");
      renderProfile(data.stats);
    } else if (view === "leaderboard") {
      const data = await api("/api/leaderboard");
      renderLeaderboard(data.rows);
    }
  });
});

// ===========================================================
// Profile
// ===========================================================
function renderProfile(stats) {
  document.getElementById("profile-avatar").outerHTML = avatarHtml(ME.display_name, ME.profile_pic, "avatar-xl").replace("<div", '<div id="profile-avatar"').replace("<img", '<img id="profile-avatar"');
  document.getElementById("profile-name-input").value = ME.display_name;
  const winRate = stats.games_played ? Math.round(100 * stats.games_won / stats.games_played) : 0;
  const bidRate = stats.bids_made ? Math.round(100 * stats.bids_won / stats.bids_made) : 0;
  const fastest = stats.fastest_win_seconds ? (stats.fastest_win_seconds / 60).toFixed(1) + " min" : "—";
  const boxes = [
    ["Games played", stats.games_played], ["Games won", stats.games_won], ["Win rate", winRate + "%"],
    ["Best margin", stats.best_margin], ["Fastest win", fastest], ["Current streak", stats.current_streak],
    ["Best streak", stats.best_streak], ["Bid success", `${bidRate}% (${stats.bids_won}/${stats.bids_made})`],
    ["Playtime", (stats.total_playtime_seconds / 60).toFixed(1) + " min"],
  ];
  document.getElementById("profile-stats").innerHTML = boxes.map(([label, val]) =>
    `<div class="stat-box"><div class="stat-value">${val}</div><div class="stat-label">${label}</div></div>`
  ).join("");
}

document.getElementById("profile-name-save").addEventListener("click", async () => {
  const name = document.getElementById("profile-name-input").value;
  const form = new FormData();
  form.append("display_name", name);
  await api("/api/profile", { method: "PATCH", body: form });
  ME.display_name = name;
  document.getElementById("nav-name").textContent = name;
});

document.getElementById("avatar-edit-trigger").addEventListener("click", () => {
  document.getElementById("profile-pic-input").click();
});

document.getElementById("profile-pic-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("profile_pic", file);
  const hint = document.getElementById("profile-pic-hint");
  hint.textContent = "Uploading…";
  try {
    await api("/api/profile/picture", { method: "POST", body: form });
    const data = await api("/api/me");
    ME = data.user;
    renderProfile(data.stats);
    hint.textContent = "Updated!";
    setTimeout(() => { hint.textContent = ""; }, 2000);
  } catch (err) {
    hint.textContent = err.message;
  }
});

// ===========================================================
// Leaderboard
// ===========================================================
function renderLeaderboard(rows) {
  document.getElementById("leaderboard-body").innerHTML = rows.map((r, i) => `
    <tr>
      <td>#${i + 1}</td>
      <td class="lb-row-name">${avatarHtml(r.display_name, r.profile_pic, "avatar-sm")} ${escapeHtml(r.display_name)}</td>
      <td>${r.games_played}</td>
      <td>${r.games_won}</td>
      <td>${Math.round(r.win_rate * 100)}%</td>
      <td>${r.best_streak}</td>
    </tr>
  `).join("") || `<tr><td colspan="6">No completed games yet — be the first!</td></tr>`;
}

// ===========================================================
// Play: game hub -> join / create room
// ===========================================================
document.getElementById("game-tile-28").addEventListener("click", () => {
  document.getElementById("game-hub").classList.add("hidden");
  document.getElementById("game-28-panel").classList.remove("hidden");
});
document.getElementById("back-to-hub-btn").addEventListener("click", () => {
  document.getElementById("game-28-panel").classList.add("hidden");
  document.getElementById("game-hub").classList.remove("hidden");
});

// ---------------- Rules modal ----------------
document.getElementById("rules-btn").addEventListener("click", () => {
  document.getElementById("rules-modal").classList.remove("hidden");
});
document.getElementById("rules-close-btn").addEventListener("click", () => {
  document.getElementById("rules-modal").classList.add("hidden");
});
document.getElementById("rules-modal").addEventListener("click", (e) => {
  if (e.target.id === "rules-modal") e.target.classList.add("hidden");
});

document.querySelectorAll("#panel-play .seg-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#panel-play .seg-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("join-form").classList.toggle("hidden", btn.dataset.tab !== "join");
    document.getElementById("create-form").classList.toggle("hidden", btn.dataset.tab !== "create");
  });
});

// ---------------- Mode selection cards (game mode / deal type) ----------------
document.querySelectorAll(".mode-grid").forEach(grid => {
  const hiddenInput = grid.parentElement.querySelector(`input[name="${grid.dataset.field}"]`);
  grid.querySelectorAll(".mode-card").forEach(card => {
    card.addEventListener("click", () => {
      grid.querySelectorAll(".mode-card").forEach(c => c.classList.remove("active"));
      card.classList.add("active");
      if (hiddenInput) hiddenInput.value = card.dataset.value;
    });
  });
});

document.getElementById("join-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("join-error");
  errEl.textContent = "";
  const code = new FormData(e.target).get("code").toUpperCase().trim();
  try {
    await api(`/api/rooms/${code}/join`, { method: "POST" });
    enterRoom(code);
  } catch (err) {
    errEl.textContent = err.message;
  }
});

document.getElementById("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("create-error");
  errEl.textContent = "";
  const form = new FormData(e.target);
  try {
    const data = await api("/api/rooms", { method: "POST", json: {
      game_mode: form.get("game_mode"), deal_type: form.get("deal_type"),
      target_score: parseInt(form.get("target_score"), 10),
    }});
    enterRoom(data.code);
  } catch (err) {
    errEl.textContent = err.message;
  }
});

function enterRoom(code) {
  roomCode = code;
  showPanel("panel-game");
  document.querySelectorAll(".nav-btn[data-view]").forEach(b => b.classList.remove("active"));
  connectWebSocket(code);
}

document.getElementById("leave-room-btn").addEventListener("click", leaveRoom);
document.getElementById("leave-after-game-btn").addEventListener("click", leaveRoom);

function leaveRoom() {
  wsIntentionalClose = true;
  roomCode = null;
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  if (ws) { ws.close(); ws = null; }
  hideReconnectBanner();
  currentState = null;
  // Safety net: an in-flight card/trick animation appends elements
  // straight to <body> (so they can fly anywhere on screen) — if one of
  // those ever fails to clean itself up, this stops it from lingering on
  // every screen after leaving the table.
  document.querySelectorAll(".flying-card").forEach(el => el.remove());
  document.getElementById("room-lobby").classList.add("hidden");
  document.getElementById("table-stage").classList.add("hidden");
  document.getElementById("round-over-panel").classList.add("hidden");
  document.getElementById("game-over-panel").classList.add("hidden");
  document.getElementById("game-28-panel").classList.add("hidden");
  document.getElementById("game-hub").classList.remove("hidden");
  showPanel("panel-play");
  document.querySelector('.nav-btn[data-view="play"]').classList.add("active");
}

// ===========================================================
// WebSocket — auto-reconnects on unexpected drops, so a hiccup never
// leaves the player permanently "stuck" watching a stale board.
// ===========================================================
let wsIntentionalClose = false;
let wsReconnectAttempts = 0;
let wsReconnectTimer = null;

function connectWebSocket(code) {
  if (ws) { wsIntentionalClose = true; ws.close(); }
  wsIntentionalClose = false;
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }

  ws = new WebSocket(wsUrl(`/ws/rooms/${code}?token=${encodeURIComponent(TOKEN)}`));

  ws.onopen = () => {
    wsReconnectAttempts = 0;
    hideReconnectBanner();
  };

  ws.onmessage = (event) => {
    const state = JSON.parse(event.data);
    const prev = currentState;
    currentState = state;
    handleIncomingState(prev, state);
  };

  ws.onclose = () => {
    if (wsIntentionalClose || !roomCode) return;
    showReconnectBanner();
    wsReconnectAttempts += 1;
    const delay = Math.min(1000 * wsReconnectAttempts, 5000);
    wsReconnectTimer = setTimeout(() => {
      if (roomCode) connectWebSocket(roomCode);
    }, delay);
  };
}

function showReconnectBanner() {
  let el = document.getElementById("reconnect-banner");
  if (!el) {
    el = document.createElement("div");
    el.id = "reconnect-banner";
    el.className = "reconnect-banner";
    el.textContent = "Reconnecting…";
    document.body.appendChild(el);
  }
  el.classList.remove("hidden");
}

function hideReconnectBanner() {
  const el = document.getElementById("reconnect-banner");
  if (el) el.classList.add("hidden");
}

function sendAction(action) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(action));
}

// ===========================================================
// Table animations — dealing, playing, and collecting tricks.
// The server pushes full-state snapshots (real-time-authoritative), so all
// of this is client-side interpolation between two consecutive snapshots:
// diff prev -> state, animate the difference, then hand off to the plain
// render() for the exact final layout. Never blocks on animation failure —
// anything unexpected falls back to an instant render().
// ===========================================================
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

// Backgrounded/throttled tabs can stall or never fire the Web Animations
// API's `finished` promise — race it against a timeout so a flight
// animation can never wedge the whole render pipeline.
function animFinished(anim, timeoutMs = 700) {
  return Promise.race([anim.finished.catch(() => {}), sleep(timeoutMs)]);
}

function rectCenter(el) {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function htmlToEl(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function seatPositions(mySeat) {
  return { bottom: mySeat, right: (mySeat + 1) % 4, top: (mySeat + 2) % 4, left: (mySeat + 3) % 4 };
}

function seatToPosMap(mySeat) {
  const positions = seatPositions(mySeat);
  const map = {};
  for (const [pos, seat] of Object.entries(positions)) map[seat] = pos;
  return map;
}

function sumCounts(counts) {
  if (!counts) return 0;
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

// Sound + toast side effects for a newly-seen server event. Deliberately
// called unconditionally for every incoming message, BEFORE any animation
// dispatch — those animations can be epoch-cancelled (see animationEpoch)
// when a newer message supersedes them, and render() itself may never run
// for a given message. Tying event side-effects to render() meant a
// cancelled animation could silently drop its event (or worse, let a
// later, unrelated event's render call consume the bookkeeping and fire
// with mismatched details, e.g. an old trump_reveal toast never showing
// while a newer event's render steals its "seen" slot).
function processEvent(state) {
  if (state.last_event_seq <= lastSeenEventSeq) return;
  lastSeenEventSeq = state.last_event_seq;
  const myTeam = state.my_seat % 2;
  if (state.last_event === "round_result" && state.round_bidder_team !== null && state.round_bidder_team !== undefined) {
    const myTeamMadeIt = (state.round_bidder_team === myTeam) === state.round_bid_made;
    playSound(myTeamMadeIt ? "round_won" : "round_lost");
  } else if (state.last_event === "game_result" && state.game_winner_team !== null && state.game_winner_team !== undefined) {
    playSound(state.game_winner_team === myTeam ? "game_won" : "game_lost");
  } else {
    playSound(state.last_event);
  }
  if (state.last_event === "trump_reveal" && state.trump_suit) {
    showTrumpToast(state.trump_suit);
  }
}

function handleIncomingState(prev, state) {
  animationEpoch += 1;
  const myEpoch = animationEpoch;
  processEvent(state);
  try {
    if (!prev || !["bidding", "choose_trump", "playing"].includes(state.phase)) {
      render(state);
      return;
    }

    const oldTrickLen = (prev.current_trick || []).length;
    const newTrickLen = (state.current_trick || []).length;

    // Server delayed the clear of a completed trick — sweep it toward the winner.
    if (oldTrickLen === 4 && newTrickLen === 0 &&
        prev.trick_winner !== null && prev.trick_winner !== undefined &&
        document.querySelectorAll("#stage-center .trick-pile-card").length === 4) {
      animateTrickCollect(prev, state, myEpoch).catch(e => { console.error(e); render(state); });
      return;
    }

    // A single new card landed in the trick (including the 4th).
    if (newTrickLen === oldTrickLen + 1 && newTrickLen >= 1) {
      animateCardPlay(state, myEpoch).catch(e => { console.error(e); render(state); });
      return;
    }

    // Hand sizes grew — a fresh deal or the half-deal top-up.
    if (sumCounts(state.hand_counts) > sumCounts(prev.hand_counts)) {
      animateDeal(prev, state, myEpoch).catch(e => { console.error(e); render(state); });
      return;
    }

    render(state);
  } catch (e) {
    console.error("Animation dispatch failed, falling back to plain render:", e);
    render(state);
  }
}

// ---- Dealing: cards fly out from the table center to all 4 seats, one at
// a time, in real dealing order, with a sound per card. ----
async function flyCard({ toEl, isMine, mini }) {
  const deckEl = document.querySelector("#stage-center .center-deck") || document.getElementById("stage-center");
  const from = rectCenter(deckEl);
  const to = toEl ? rectCenter(toEl) : from;
  const w = mini ? 73 : 125;
  const h = w * (315 / 227);
  const wrap = document.createElement("div");
  wrap.className = "flying-card";
  wrap.style.cssText = `position:fixed; left:0; top:0; width:${w}px; height:${h}px; pointer-events:none; z-index:9999;`;
  // Set the starting transform synchronously, matching the animation's
  // first keyframe, so the element never has a frame at native (0,0) —
  // that showed up as a brief flash in the viewport's top-left corner.
  wrap.style.transform = `translate(${from.x - w / 2}px, ${from.y - h / 2}px) scale(0.6)`;
  wrap.style.opacity = "0";
  wrap.appendChild(htmlToEl(cardBackHtml({ mini })));
  document.body.appendChild(wrap);
  try {
    const rot = (Math.random() * 14 - 7).toFixed(1);
    playSound("deal");
    const anim = wrap.animate([
      { transform: `translate(${from.x - w / 2}px, ${from.y - h / 2}px) scale(0.6)`, opacity: 0 },
      { transform: `translate(${from.x - w / 2}px, ${from.y - h / 2}px) scale(0.85) rotate(${rot}deg)`, opacity: 1, offset: 0.15 },
      { transform: `translate(${to.x - w / 2}px, ${to.y - h / 2}px) scale(${isMine ? 1 : 0.7}) rotate(0deg)`, opacity: 1 },
    ], { duration: 220, easing: "cubic-bezier(0.16,1,0.3,1)" });
    await animFinished(anim, 500);
  } finally {
    // Always clean up, even if something above throws — an orphaned
    // position:fixed div is invisible-until-it-isn't and would otherwise
    // sit on top of every screen (including the lobby) forever.
    wrap.remove();
  }
}

// A riffle of the center deck stack before cards start flying out — each
// card flies out to a random offset then snaps back to its resting spot,
// staggered per card. Adapted from deck-of-cards.js.org's shuffle module
// (fisherYates + per-card two-phase animateTo), reimplemented with WAAPI.
async function shuffleFlourish() {
  const deck = document.querySelector("#stage-center .center-deck");
  if (!deck) return;
  playSound("shuffle");
  const cards = Array.from(deck.children);
  const anims = cards.map((el, i) => {
    const dx = (Math.random() * 2 - 1) * 55 + (i - 1) * 8;
    const dy = -8 - i * 4;
    const rot = (Math.random() * 2 - 1) * 22;
    return el.animate([
      { transform: "translate(0,0) rotate(0deg)", offset: 0 },
      { transform: `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${rot.toFixed(1)}deg)`, offset: 0.5 },
      { transform: "translate(0,0) rotate(0deg)", offset: 1 },
    ], { duration: 460, delay: i * 50, easing: "cubic-bezier(0.4,0,0.2,1)" });
  });
  await Promise.all(anims.map(a => animFinished(a, 750)));
}

async function animateDeal(prevState, state, myEpoch) {
  const mySeat = state.my_seat;
  const seatToPos = seatToPosMap(mySeat);

  const oldHand = prevState.hand || [];
  const oldCounts = {}, newCounts = {}, delta = {};
  let rounds = 0;
  for (let s = 0; s < 4; s++) {
    oldCounts[s] = parseInt((prevState.hand_counts || {})[String(s)] || 0, 10);
    newCounts[s] = parseInt((state.hand_counts || {})[String(s)] || 0, 10);
    delta[s] = Math.max(0, newCounts[s] - oldCounts[s]);
    rounds = Math.max(rounds, delta[s]);
  }

  // Show the "before" picture (old hand / old cardback counts) so the deal
  // animation has somewhere real to land instead of spoiling the result.
  const shellState = { ...state, hand: oldHand, hand_counts: {} };
  for (let s = 0; s < 4; s++) shellState.hand_counts[String(s)] = oldCounts[s];
  renderSeats(shellState);
  document.getElementById("hand-row").innerHTML = oldHand.map(c => cardHtml(c, { big: true })).join("");
  document.getElementById("stage-center").innerHTML = `
    <div class="center-deck">${cardBackHtml()}${cardBackHtml()}${cardBackHtml()}</div>
    <div class="dealing-banner">Shuffling…</div>
  `;

  await shuffleFlourish();
  if (myEpoch !== animationEpoch) return;
  const banner = document.querySelector("#stage-center .dealing-banner");
  if (banner) banner.textContent = "Dealing…";

  const newOwnCards = state.hand.filter(c => !oldHand.includes(c));
  let ownIdx = 0;
  const dealOrder = [(state.dealer + 1) % 4, (state.dealer + 2) % 4, (state.dealer + 3) % 4, state.dealer % 4];

  for (let r = 0; r < rounds; r++) {
    for (const seat of dealOrder) {
      if (myEpoch !== animationEpoch) return;
      if (delta[seat] <= r) continue;
      const pos = seatToPos[seat];
      const isMine = seat === mySeat;
      const targetEl = pos === "bottom" ? document.getElementById("hand-row") : document.getElementById(`seat-${pos}`);
      await flyCard({ toEl: targetEl, isMine, mini: !isMine });
      if (myEpoch !== animationEpoch) return;
      if (isMine) {
        const card = newOwnCards[ownIdx++];
        if (card) document.getElementById("hand-row").insertAdjacentHTML("beforeend", cardHtml(card, { big: true }));
      } else {
        const container = document.querySelector(`#seat-${pos} .seat-cardbacks`);
        if (container) container.appendChild(htmlToEl(cardBackHtml({ mini: true })));
      }
      await sleep(35);
    }
  }

  if (myEpoch !== animationEpoch) return;
  render(state);
}

// ---- A card being played: flies from the player's seat to the trick area. ----
async function animateCardPlay(state, myEpoch) {
  const mySeat = state.my_seat;
  const seatToPos = seatToPosMap(mySeat);
  const [seat, card] = state.current_trick[state.current_trick.length - 1];
  const pos = seatToPos[seat];
  const isMine = seat === mySeat;

  let sourceEl = null;
  if (isMine) {
    sourceEl = document.querySelector(`#hand-row .pcard-svg[data-card="${CSS.escape(card)}"]`);
  } else {
    sourceEl = document.getElementById(`seat-${pos}`);
  }
  const centerEl = document.getElementById("stage-center");
  const from = sourceEl ? rectCenter(sourceEl) : rectCenter(centerEl);
  const to = rectCenter(centerEl);
  if (sourceEl && isMine) sourceEl.style.visibility = "hidden";

  const w = 125, h = w * (315 / 227);
  const wrap = document.createElement("div");
  wrap.className = "flying-card";
  wrap.style.cssText = `position:fixed; left:0; top:0; width:${w}px; height:${h}px; pointer-events:none; z-index:9999;`;
  // Same fix as flyCard: avoid a one-frame flash at native (0,0).
  wrap.style.transform = `translate(${from.x - w / 2}px, ${from.y - h / 2}px) scale(0.95)`;
  wrap.appendChild(htmlToEl(cardHtml(card, { big: true })));
  document.body.appendChild(wrap);
  try {
    playSound("card_play");
    const rot = (Math.random() * 10 - 5).toFixed(1);
    const anim = wrap.animate([
      { transform: `translate(${from.x - w / 2}px, ${from.y - h / 2}px) scale(0.95)`, opacity: 1 },
      { transform: `translate(${to.x - w / 2}px, ${to.y - h / 2}px) scale(1) rotate(${rot}deg)`, opacity: 1 },
    ], { duration: 260, easing: "cubic-bezier(0.16,1,0.3,1)" });
    await animFinished(anim);
  } finally {
    wrap.remove();
  }
  if (myEpoch !== animationEpoch) return;
  render(state);
}

// ---- A resolved trick sweeping off the table toward the winner. ----
async function animateTrickCollect(prevState, state, myEpoch) {
  const mySeat = state.my_seat;
  const seatToPos = seatToPosMap(mySeat);
  const winPos = seatToPos[prevState.trick_winner];
  const targetEl = winPos === "bottom" ? document.getElementById("hand-row") : document.getElementById(`seat-${winPos}`);
  const to = targetEl ? rectCenter(targetEl) : rectCenter(document.getElementById("stage-center"));

  const slots = Array.from(document.querySelectorAll("#stage-center .trick-pile-card"));
  const anims = slots.map((slot, i) => {
    const r = slot.getBoundingClientRect();
    slot.style.position = "fixed";
    slot.style.left = `${r.left}px`;
    slot.style.top = `${r.top}px`;
    slot.style.width = `${r.width}px`;
    slot.style.margin = "0";
    slot.style.zIndex = "9999";
    slot.classList.add("flying-card");
    document.body.appendChild(slot);
    const dx = to.x - (r.left + r.width / 2);
    const dy = to.y - (r.top + r.height / 2);
    const anim = slot.animate([
      { transform: "translate(0,0) scale(1)", opacity: 1 },
      { transform: `translate(${dx}px, ${dy}px) scale(0.5)`, opacity: 0 },
    ], { duration: 380, delay: i * 45, easing: "cubic-bezier(0.4,0,0.7,1)", fill: "forwards" });
    return animFinished(anim, 900);
  });
  try {
    await Promise.all(anims);
  } finally {
    slots.forEach(s => s.remove());
  }
  if (myEpoch !== animationEpoch) return;
  render(state);
}

// ===========================================================
// Rendering — the big one. Dispatches on state.phase.
// ===========================================================
function showTrumpToast(suit) {
  document.querySelectorAll(".trump-toast").forEach(el => el.remove());
  const toast = document.createElement("div");
  toast.className = "trump-toast";
  toast.textContent = `Trump revealed: ${suit}`;
  document.getElementById("table-stage").appendChild(toast);
  setTimeout(() => {
    toast.classList.add("fade-out");
    setTimeout(() => toast.remove(), 400);
  }, 2000);
}

function render(state) {
  document.getElementById("room-lobby").classList.add("hidden");
  document.getElementById("table-stage").classList.add("hidden");
  document.getElementById("round-over-panel").classList.add("hidden");
  document.getElementById("game-over-panel").classList.add("hidden");

  if (state.phase === "lobby") { renderLobby(state); return; }

  document.getElementById("table-stage").classList.remove("hidden");
  renderSeats(state);
  renderScores(state);
  renderInfoCorner(state);
  renderCenter(state);
  renderHand(state);

  if (state.phase === "round_over") renderRoundOver(state);
  if (state.phase === "game_over") renderGameOver(state);
}

function lobbySeatCardHtml(seat, state) {
  const p = state.players[String(seat)];
  if (p) {
    const mine = p.is_me ? '<span class="lobby-seat-you">YOU</span>' : "";
    const bot = p.bot ? '<span class="lobby-seat-bot-tag">BOT</span>' : "";
    return `<div class="lobby-seat-card filled">
      ${avatarHtml(p.display_name, p.profile_pic, "avatar-lg", p.avatar_seed)}
      <div class="lobby-seat-name">${escapeHtml(p.display_name)}</div>
      <div class="lobby-seat-tags">${mine}${bot}</div>
    </div>`;
  }
  return `<div class="lobby-seat-card empty" data-add-bot="${seat}">
    <div class="lobby-seat-empty-icon">+</div>
    <div class="lobby-seat-empty-label">Add bot</div>
  </div>`;
}

function renderLobby(state) {
  const panel = document.getElementById("room-lobby");
  panel.classList.remove("hidden");
  document.getElementById("lobby-room-code").textContent = roomCode;
  document.getElementById("lobby-badges").innerHTML = [
    state.game_mode.replace("_", " ").replace(/\b\w/g, c => c.toUpperCase()),
    state.deal_type.charAt(0).toUpperCase() + state.deal_type.slice(1) + " Deal",
    `Target ${state.target_score}`,
  ].map(b => `<span class="badge">${b}</span>`).join("");

  document.getElementById("lobby-team-a").innerHTML = [0, 2].map(s => lobbySeatCardHtml(s, state)).join("");
  document.getElementById("lobby-team-b").innerHTML = [1, 3].map(s => lobbySeatCardHtml(s, state)).join("");
  document.querySelectorAll("[data-add-bot]").forEach(el => {
    el.addEventListener("click", () => sendAction({ action: "add_bot", seat: parseInt(el.dataset.addBot, 10) }));
  });

  const filled = Object.values(state.players).filter(Boolean).length;
  const startBtn = document.getElementById("start-game-btn");
  const hint = document.getElementById("lobby-hint");
  if (filled === 4) {
    startBtn.classList.remove("hidden");
    startBtn.onclick = () => sendAction({ action: "start_game" });
    hint.textContent = "";
  } else {
    startBtn.classList.add("hidden");
    hint.textContent = "Need 4 seats filled (players or bots) to start.";
  }
}

function teamLabel(seat, mySeat) {
  const myTeam = mySeat % 2;
  const seatTeam = seat % 2;
  return seatTeam === myTeam ? "Us" : "Them";
}

function renderSeats(state) {
  const mySeat = state.my_seat;
  const positions = {
    bottom: mySeat,
    right: (mySeat + 1) % 4,
    top: (mySeat + 2) % 4,
    left: (mySeat + 3) % 4,
  };
  for (const [pos, seat] of Object.entries(positions)) {
    const el = document.getElementById(`seat-${pos}`);
    const p = state.players[String(seat)];
    if (!p) { el.innerHTML = ""; continue; }
    const isDealer = seat === state.dealer;
    const classes = ["seat-tag"];
    if (seat === state.turn) classes.push("turn");
    if (seat === mySeat) classes.push("me");
    if (isDealer) classes.push("dealer");
    const dealerChip = isDealer ? `<span class="dealer-chip">🃏 Dealer</span>` : "";
    const botTag = p.stand_in ? " 🤖 (away)" : (p.bot ? " 🤖" : "");
    let cardbacks = "";
    if (pos !== "bottom") {
      const n = parseInt(state.hand_counts[String(seat)] || 0, 10);
      const orient = pos === "left" ? "seat-cardbacks-v seat-cardbacks-left"
        : pos === "right" ? "seat-cardbacks-v seat-cardbacks-right"
        : "seat-cardbacks-h";
      cardbacks = `<div class="seat-cardbacks ${orient}">${Array.from({ length: n }).map(() => cardBackHtml({ mini: true })).join("")}</div>`;
    }
    el.innerHTML = `
      <div class="${classes.join(" ")}">
        ${avatarHtml(p.display_name, p.profile_pic, "avatar-md", p.avatar_seed)}
        <span class="seat-name">${escapeHtml(p.display_name)}${botTag}</span>
      </div>
      ${dealerChip}
      ${cardbacks}
    `;
  }
}

function renderScores(state) {
  const mySeat = state.my_seat;
  const myTeam = mySeat % 2 === 0 ? 0 : 1;
  const otherTeam = 1 - myTeam;
  document.getElementById("score-match-us").textContent = state.match_score[myTeam];
  document.getElementById("score-match-them").textContent = state.match_score[otherTeam];
  // Once a bid is on the table, show progress toward it (points/bid) —
  // easier to track than a bare point count.
  const hasBid = state.bid_seat !== null && state.bid_seat !== undefined;
  const bidSuffix = hasBid ? `/${state.bid_value}` : "";
  document.getElementById("score-round-us").textContent = `${state.round_points[myTeam]}${bidSuffix}`;
  document.getElementById("score-round-them").textContent = `${state.round_points[otherTeam]}${bidSuffix}`;
}

function renderInfoCorner(state) {
  document.getElementById("info-trump").textContent = state.trump_suit ? state.trump_suit : "🔒 Hidden";
  if (state.bid_seat !== null && state.bid_seat !== undefined) {
    const bidderName = state.players[String(state.bid_seat)]?.display_name || `Seat ${state.bid_seat + 1}`;
    document.getElementById("info-bid").textContent = `${state.bid_value} by ${bidderName}`;
  } else {
    document.getElementById("info-bid").textContent = "—";
  }
  const logEl = document.getElementById("stage-log");
  logEl.innerHTML = (state.log || []).map(l => `<div>• ${escapeHtml(l)}</div>`).join("");
}

document.getElementById("log-toggle-btn").addEventListener("click", () => {
  document.getElementById("stage-log").classList.toggle("hidden");
});

function renderCenter(state) {
  const center = document.getElementById("stage-center");
  const mySeat = state.my_seat;

  if (state.phase === "bidding") {
    const myTurn = state.turn === mySeat && state.active_bidders.includes(mySeat);
    if (!myTurn) {
      center.innerHTML = `<div class="bid-panel"><div class="bid-waiting">Waiting for other players to bid…</div></div>`;
      return;
    }
    const minNext = Math.max(state.bid_value + 1, state.min_bid);
    const values = [];
    for (let v = minNext; v <= state.max_bid; v++) values.push(v);
    const BID_CAP = 20; // most bids never go past this — hide the rest behind a toggle
    const hasExtra = values.some(v => v > BID_CAP);
    center.innerHTML = `
      <div class="bid-panel">
        <div class="bid-panel-header">Your Bid</div>
        <div class="bid-panel-sub">${state.bid_seat !== null ? `To beat: ${state.bid_value}` : "No bids yet"}</div>
        <div class="stake-row"><span class="stake-pill win">Win +1</span><span class="stake-pill lose">Lose −1</span></div>
        <div class="bid-grid">
          ${values.map(v => `<button class="bid-grid-btn${v > BID_CAP ? " hidden bid-grid-extra" : ""}" data-bid="${v}">${v}</button>`).join("")}
        </div>
        ${hasExtra ? `<button type="button" class="bid-more-btn" id="bid-more-btn">Show bids above ${BID_CAP} ▾</button>` : ""}
        <button class="bid-pass-btn" id="bid-pass-btn">Pass</button>
      </div>
    `;
    const moreBtn = document.getElementById("bid-more-btn");
    if (moreBtn) {
      moreBtn.addEventListener("click", () => {
        center.querySelectorAll(".bid-grid-extra").forEach(el => el.classList.remove("hidden"));
        moreBtn.remove();
      });
    }
    center.querySelectorAll("[data-bid]").forEach(btn => {
      btn.addEventListener("click", () => {
        const val = parseInt(btn.dataset.bid, 10);
        if (val > state.bid_confirm_threshold) {
          if (!confirm(`Bid ${val} points? High bids carry more risk.`)) return;
        }
        sendAction({ action: "bid", value: val });
      });
    });
    document.getElementById("bid-pass-btn").addEventListener("click", () => sendAction({ action: "pass" }));
    return;
  }

  if (state.phase === "choose_trump") {
    if (state.bid_seat !== mySeat) {
      const bidderName = state.players[String(state.bid_seat)]?.display_name || "Bidder";
      center.innerHTML = `<div class="trump-panel">Waiting for ${escapeHtml(bidderName)} to choose trump…</div>`;
      return;
    }
    center.innerHTML = `
      <div class="trump-panel">
        <div class="bid-panel-header">Choose Trump</div>
        <div>${CONFIG.suits.map(s => `<button class="trump-suit-btn ${RED_SUITS.has(s) ? "red" : ""}" data-suit="${s}">${s}</button>`).join("")}</div>
      </div>
    `;
    center.querySelectorAll("[data-suit]").forEach(btn => {
      btn.addEventListener("click", () => sendAction({ action: "choose_trump", suit: btn.dataset.suit }));
    });
    return;
  }

  if (state.phase === "playing") {
    if (!state.current_trick.length) {
      center.innerHTML = `<div class="trick-pile"></div>`;
      return;
    }
    // A clean 2x2 grid, zero overlap — every card fully visible, placed
    // by play order (top-left, top-right, bottom-left, bottom-right).
    const gridOffsets = [[-70, -96], [70, -96], [-70, 96], [70, 96]];
    center.innerHTML = `<div class="trick-pile">${state.current_trick.map(([seat, card], i) => {
      const [jx, jy] = gridOffsets[i % gridOffsets.length];
      const style = `--pile-rot:0deg;--pile-x:${jx}px;--pile-y:${jy}px;`;
      return `<div class="trick-pile-card" style="${style}">${cardHtml(card)}</div>`;
    }).join("")}</div>`;
    return;
  }

  center.innerHTML = "";
}

function renderHand(state) {
  const row = document.getElementById("hand-row");
  const mySeat = state.my_seat;
  const legal = new Set(state.legal_moves || []);
  const myTurn = state.phase === "playing" && state.turn === mySeat;
  row.innerHTML = state.hand.map(card => {
    const playable = myTurn && legal.has(card);
    return cardHtml(card, { big: true, dim: myTurn && !playable, playable });
  }).join("");
  if (myTurn) {
    row.querySelectorAll(".pcard-svg.playable").forEach(el => {
      el.addEventListener("click", () => sendAction({ action: "play_card", card: el.dataset.card }));
    });
  }
}

function renderRoundOver(state) {
  document.getElementById("round-over-panel").classList.remove("hidden");
  document.getElementById("round-summary-text").textContent = state.round_summary || "";
  const btn = document.getElementById("next-round-btn");
  const hint = document.getElementById("round-over-hint");
  if (state.can_control_pacing) {
    btn.classList.remove("hidden");
    btn.onclick = () => sendAction({ action: "next_round" });
    hint.textContent = "";
  } else {
    btn.classList.add("hidden");
    hint.textContent = "Waiting for the room host to deal the next round…";
  }
}

function renderGameOver(state) {
  document.getElementById("game-over-panel").classList.remove("hidden");
  const s = state.game_over_summary;
  if (s) {
    document.getElementById("game-over-text").textContent =
      `${s.winner} wins by ${s.margin} match point(s)! Final score: Team A ${s.score_a} — Team B ${s.score_b}. ` +
      `Duration: ${(s.duration / 60).toFixed(1)} min.`;
  }
  const boxes = [0, 1, 2, 3].map(seat => {
    const p = state.players[String(seat)];
    if (!p) return "";
    return `<div class="stat-box">${avatarHtml(p.display_name, p.profile_pic, "avatar-sm", p.avatar_seed)}<div>${escapeHtml(p.display_name)}</div></div>`;
  }).join("");
  document.getElementById("game-over-stats").innerHTML = boxes;

  const btn = document.getElementById("play-again-btn");
  if (state.can_control_pacing) {
    btn.classList.remove("hidden");
    btn.onclick = () => sendAction({ action: "play_again" });
  } else {
    btn.classList.add("hidden");
  }
}

// ===========================================================
// Boot
// ===========================================================
(async function boot() {
  try {
    CONFIG = await api("/api/config");
  } catch (e) {
    console.error("Failed to load config", e);
  }
  if (TOKEN) {
    try {
      await bootAfterLogin();
      return;
    } catch (e) {
      TOKEN = null;
      localStorage.removeItem("k28_token");
    }
  }
  showView("view-auth");
})();
