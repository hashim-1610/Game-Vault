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
  const palette = ["#C9A227", "#0F5C46", "#9B2242", "#E7CB6B", "#10584A"];
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
// Real illustrated playing cards via <use> references into the SVG-cards
// deck (David Bellot / htdebeer, LGPL 2.1 — see assets/SVG-CARDS-LICENSE.txt).
const SUIT_ID = { "♠": "spade", "♥": "heart", "♦": "diamond", "♣": "club" };
const RANK_ID = { "A": "1", "J": "jack", "Q": "queen", "K": "king" }; // 7-10 map to themselves

function cardSvgId(card) {
  const rank = card.slice(0, -1);
  const suit = card.slice(-1);
  return `${SUIT_ID[suit]}_${RANK_ID[rank] || rank}`;
}

function cardHtml(card, { dim = false, big = false, playable = false, mini = false } = {}) {
  const classes = ["pcard-svg"];
  if (dim) classes.push("dim");
  if (big) classes.push("big");
  if (playable) classes.push("playable");
  if (mini) classes.push("mini");
  return `<svg class="${classes.join(" ")}" viewBox="0 0 169.075 244.640" data-card="${escapeHtml(card)}">
    <use href="/assets/svg-cards.svg#${cardSvgId(card)}"></use>
  </svg>`;
}

function cardBackHtml({ mini = false } = {}) {
  const classes = ["pcard-svg", "card-back"];
  if (mini) classes.push("mini");
  return `<svg class="${classes.join(" ")}" viewBox="0 0 169.075 244.640">
    <use href="/assets/svg-cards.svg#back"></use>
  </svg>`;
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
    currentState = state;
    render(state);
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
// Rendering — the big one. Dispatches on state.phase.
// ===========================================================
function render(state) {
  if (state.last_event_seq > lastSeenEventSeq) {
    playSound(state.last_event);
    lastSeenEventSeq = state.last_event_seq;
  }

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
    const classes = ["seat-tag"];
    if (seat === state.turn) classes.push("turn");
    if (seat === mySeat) classes.push("me");
    const dealerDot = seat === state.dealer ? `<span class="seat-dealer-dot">D</span>` : "";
    const botTag = p.bot ? " 🤖" : "";
    let cardbacks = "";
    if (pos !== "bottom") {
      const n = parseInt(state.hand_counts[String(seat)] || 0, 10);
      cardbacks = `<div class="seat-cardbacks">${Array.from({ length: n }).map(() => cardBackHtml({ mini: true })).join("")}</div>`;
    }
    el.innerHTML = `
      <div class="${classes.join(" ")}">
        ${avatarHtml(p.display_name, p.profile_pic, "avatar-md", p.avatar_seed)}
        <span class="seat-name">${escapeHtml(p.display_name)}${botTag}</span>
        ${dealerDot}
      </div>
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
  document.getElementById("score-round-us").textContent = state.round_points[myTeam];
  document.getElementById("score-round-them").textContent = state.round_points[otherTeam];
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
    center.innerHTML = `
      <div class="bid-panel">
        <div class="bid-panel-header">Your Bid</div>
        <div class="bid-panel-sub">${state.bid_seat !== null ? `To beat: ${state.bid_value}` : "No bids yet"}</div>
        <div class="stake-row"><span class="stake-pill win">Win +1</span><span class="stake-pill lose">Lose −1</span></div>
        <div class="bid-grid">
          ${values.map(v => `<button class="bid-grid-btn" data-bid="${v}">${v}</button>`).join("")}
        </div>
        <button class="bid-pass-btn" id="bid-pass-btn">Pass</button>
      </div>
    `;
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
      center.innerHTML = `<div class="trick-row"></div>`;
      return;
    }
    center.innerHTML = `<div class="trick-row">${state.current_trick.map(([seat, card]) => {
      const name = state.players[String(seat)]?.display_name || `Seat ${seat + 1}`;
      return `<div class="trick-slot">${cardHtml(card)}<div class="trick-label">${escapeHtml(name)}</div></div>`;
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
