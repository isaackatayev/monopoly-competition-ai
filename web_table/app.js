"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const board = window.MonopolyBoard;
  const blankPlayers = [0, 1].map(seat => ({seat, name: null, kind: "open"}));
  const money = n => "$" + Number(n).toLocaleString("en-US");
  let id = new URL(location.href).searchParams.get("table")?.toUpperCase() || null;
  let room = null, credential = null, actionKey = "", rosterKey = "", logKey = "";
  let boardInfo = [], host = {}, busy = false, busySubmit = false;
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);
  const ngrokDomain = hostname => /\.ngrok(?:-free)?\.(?:app|dev|pizza)$|\.ngrok\.io$/.test(hostname);
  function publicOrigin() {
    return ngrokDomain(location.hostname) ? location.origin : host.public_url;
  }
  const memory = {};
  function load(key) {
    try { return JSON.parse(localStorage.getItem("monopoly-club:" + key)) || memory[key]; } catch { return memory[key]; }
  }
  function save(key, value) {
    memory[key] = value;
    try { localStorage.setItem("monopoly-club:" + key, JSON.stringify(value)); } catch { /* This tab still holds the seat. */ }
  }
  function element(tag, text, cls) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }
  function button(text, fn, primary = false) {
    const el = element("button", text, primary ? "btn primary" : "btn secondary");
    el.type = "button";
    el.addEventListener("click", fn);
    return el;
  }
  function choice(value, label) {
    const opt = element("option", label);
    opt.value = value;
    return opt;
  }
  function field(form, title, control) {
    const label = element("label", null, "form-field");
    label.append(element("span", title), control);
    form.append(label);
    return control;
  }
  function inlineError(container, message) {
    let notice = container.querySelector(".inline-error");
    if (!notice) {
      notice = element("p", null, "inline-error");
      notice.setAttribute("role", "alert");
      container.append(notice);
    }
    notice.textContent = message;
  }
  async function api(path, {body, token} = {}) {
    const headers = {Accept: "application/json"};
    if (token) headers.Authorization = "Bearer " + token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {headers, method: body === undefined ? "GET" : "POST",
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
    if (!response.ok) {
      const message = await response.json().catch(() => ({}));
      const error = new Error(message.error || "The host could not complete that request.");
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  function modal(title) {
    $("dialog-title").textContent = title;
    $("dialog-body").replaceChildren();
    if (!$("dialog").open) $("dialog").showModal();
    return $("dialog-body");
  }
  $("dialog-close").onclick = () => $("dialog").close();

  function switchTable(newId) {
    id = newId;
    const url = new URL(location.href);
    url.searchParams.set("table", id);
    history.replaceState(null, "", url);
    credential = load("seat-" + id) || null;
    room = null;
    actionKey = rosterKey = logKey = "";
  }
  function randomKey() {
    return Array.from(crypto.getRandomValues(new Uint8Array(24)), v => v.toString(16).padStart(2, "0")).join("");
  }
  async function takeSeat(name) {
    const table = id;
    const key = "claim-" + table;
    const saved = load(key);
    const join = saved && saved.name === name ? saved : {kind:"human", name, join_key:randomKey()};
    save(key, join);
    let result;
    try {
      result = await api("/api/tables/" + table + "/join", {body: join});
    } catch (err) {
      if ([400, 404, 409].includes(err.status)) save(key, null);
      throw err;
    }
    save("seat-" + table, result);
    if (id === table) credential = result;
    save("player-name", name);
  }
  function submitForm(form, title, run) {
    const submit = element("button", title, "btn primary");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (submit.disabled) return;
      submit.disabled = true;
      try { await run(); } catch (err) { inlineError(form, err.message); }
      finally { submit.disabled = false; }
    });
    return submit;
  }
  function nameInput() {
    const name = element("input");
    name.required = true;
    name.maxLength = 32;
    name.value = load("player-name") || "Player One";
    name.autocomplete = "nickname";
    return name;
  }
  function setup(inviting = false) {
    const box = modal(inviting ? "Set up an invite" : "New table");
    const form = element("form");
    const select = element("select");
    [
      ["friend", "Take a seat and invite a player"], ["host", "Host two guest players"]
    ].forEach(([value, label]) => select.append(choice(value, label)));
    select.value = inviting ? "host" : "friend";
    field(form, "Table setup", select);
    const name = field(form, "Your name", nameInput());
    const help = element("p");
    form.append(help);
    const messages = {
      friend:"Take a seat and share the other with a person or an AI.",
      host:"Share one invite with two players. You can watch from this browser."
    };
    function update() {
      const human = select.value === "friend";
      name.closest("label").hidden = !human;
      name.disabled = !human;
      help.textContent = messages[select.value];
    }
    update();
    select.onchange = update;
    submitForm(form, "Create table", async () => {
      const mode = select.value;
      const created = await api("/api/tables", {body:{}});
      switchTable(created.id);
      room = created;
      if (mode === "friend") {
        try { await takeSeat(name.value.trim()); }
        catch (err) { $("dialog").close(); render(); showJoin(false, err.message); return; }
      }
      $("dialog").close();
      await refresh();
      invite();
    });
    box.append(form);
  }

  function showJoin(enterCode, message) {
    const box = modal(enterCode ? "Join a table" : "Take a seat");
    const form = element("form");
    let code;
    if (enterCode) {
      code = element("input");
      code.placeholder = "e.g. AB3D7X";
      code.required = true;
      code.maxLength = 6;
      code.pattern = "[A-Za-z0-9]{6}";
      code.style.textTransform = "uppercase";
      field(form, "Six-character table code", code);
    }
    const name = field(form, "Your name", nameInput());
    submitForm(form, enterCode ? "Open table" : "Join as a player", async () => {
      if (enterCode) {
        const requested = code.value.trim().toUpperCase();
        const found = await api("/api/tables/" + requested);
        switchTable(requested);
        room = found;
        if (credential || !found.players.some(p => p.kind === "open")) {
          $("dialog").close(); render(); return;
        }
      }
      await takeSeat(name.value.trim());
      $("dialog").close();
      await refresh();
    });
    if (message) inlineError(form, message);
    box.append(form);
  }

  async function copyText(value, input, status) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("fallback");
      await navigator.clipboard.writeText(value);
      status.textContent = "Copied to clipboard.";
    } catch {
      input.focus(); input.select();
      status.textContent = document.execCommand("copy") ? "Copied to clipboard." : "Selected. Use your device’s Copy command.";
    }
  }
  function invite() {
    if (!room) { setup(true); return; }
    const box = modal("Invite players");
    const available = room.players.filter(p => p.kind === "open").length;
    box.append(element("p", available ? `Table ${id} has ${available} open ${available === 1 ? "seat" : "seats"}. Share this link to join or watch.` :
      `Both seats at table ${id} are taken. Share the link to watch the game.`));
    const publicUrl = publicOrigin();
    const origin = publicUrl || (loopback && host.lan_urls?.length ? host.lan_urls[0] : location.origin);
    const viaNgrok = ngrokDomain(new URL(origin).hostname);
    const form = element("div", null, "invite-fields");
    const url = element("input");
    url.readOnly = true;
    url.value = origin + "/?table=" + id;
    field(form, "Browser invite", url);
    const status = element("p", "", "copy-status");
    status.setAttribute("aria-live", "polite");
    form.append(button("Copy link", () => copyText(url.value, url, status), true));
    if (available) {
      const prompt = element("textarea");
      prompt.rows = 4; prompt.readOnly = true;
      prompt.value = `Join my Monopoly game at ${url.value} as an AI player. Read the connection spec at ${origin}/protocol, claim an open seat, and play until the result. You can use ${origin}/example-agent.py as a working client.`;
      if (viaNgrok) prompt.value += " If your HTTP tool receives the ngrok welcome page, retry with the ngrok-skip-browser-warning: 1 request header.";
      field(form, "Invitation for an AI", prompt);
      form.append(button("Copy AI invitation", () => copyText(prompt.value, prompt, status)));
    }
    if (publicUrl) {
      const hint = viaNgrok
        ? "Players can open this link from any network while the ngrok tunnel is running. If ngrok shows a welcome page, choose Visit Site."
        : "Players can open this link over the internet while the public address is online.";
      form.append(element("p", hint));
    } else if (loopback && !host.lan_enabled) form.append(element("p", "This invite works on the host computer. Use ngrok for internet play or launch ./monopoly table --lan for players on the same network."));
    else if (loopback && !host.lan_urls?.length) form.append(element("p", "For another computer, replace localhost with this host’s LAN address."));
    else form.append(element("p", "Players need a connection to this host’s local network."));
    form.append(status);
    box.append(form);
  }

  function renderPlayers(players, state) {
    const key = JSON.stringify([players, credential?.seat, state?.cash, state?.owner, state?.position, state?.jail, state?.level, state?.mortgaged, state?.active, room?.status]);
    if (key === rosterKey) return;
    rosterKey = key;
    const rows = players.map(p => {
      const row = element("div", null, "player-row seat-" + p.seat);
      const token = element("span", null, "player-token");
      token.innerHTML = board.token(p.seat);
      const info = element("div", null, "player-info");
      const you = p.seat === credential?.seat;
      info.append(element("strong", p.name || "Open seat"));
      const role = {ai:"Guest AI", human:you ? "You" : "Human player", open:"Available to join"}[p.kind];
      const deeds = state?.owner?.filter(o => o === p.seat).length || 0;
      info.append(element("small", state ? role + " · " + deeds + (deeds === 1 ? " deed" : " deeds") : role));
      row.append(token, info, element("span", p.kind === "open" ? "—" : money(state?.cash[p.seat] ?? 1500), "player-cash"));
      const card = element("section", null, "seat-card seat-" + p.seat);
      card.append(row);
      if (p.kind === "open") return card;
      const position = state?.position[p.seat] ?? 0;
      const location = state?.jail[p.seat] >= 0 ? "In jail" : board.name(position);
      card.append(element("p", "On " + location + " · Space " + position, "player-location"));
      if (state) {
        const deeds = state.owner.flatMap((owner, i) => owner === p.seat ? [i] : []);
        const list = element("ul", null, "portfolio");
        for (const i of deeds) {
          const level = state.level[i];
          const description = state.mortgaged[i] ? "Mortgaged" : level === 5 ? "Hotel" :
            level > 0 ? level + (level === 1 ? " house" : " houses") : "Unimproved";
          const item = element("li");
          item.append(element("span", board.name(i)), element("strong", description));
          list.append(item);
        }
        card.append(deeds.length ? list : element("small", "No properties yet"));
      }
      return card;
    });
    $("players").replaceChildren(...rows);
    $("seat-count").textContent = players.filter(p => p.kind !== "open").length + " / 2";
  }

  function renderEvents() {
    const entries = (room?.events || []).slice(-28).reverse();
    if (!entries.length) {
      if (room) {
        const joined = room.players.filter(p => p.name);
        joined.reverse().forEach(p => entries.push({text:p.name + " joined the table.", seat:p.seat}));
        entries.push({text:"Table " + id + " opened."});
      } else entries.push({text:"Game activity will appear here."});
    }
    const key = JSON.stringify(entries);
    if (key === logKey) return;
    logKey = key;
    $("events").replaceChildren(...entries.map(e => {
      const row = element("li", null, e.seat == null || e.seat < 0 ? "" : "seat-" + e.seat);
      row.append(element("span", null, "event-dot"), element("span", e.text, "event-content"));
      if (e.turn) row.append(element("time", "T" + e.turn));
      return row;
    }));
  }

  function tradeForm(container, spec, send) {
    const details = element("details");
    details.append(element("summary", "Make a trade offer"));
    const form = element("form");
    function deeds(label, squares) {
      const list = element("select");
      list.append(choice(-1, "None"));
      squares.forEach(s => list.append(choice(s, board.name(s))));
      return field(form, label, list);
    }
    const give = deeds("Deed you give", spec.give), take = deeds("Deed you receive", spec.take);
    const cash = element("input");
    cash.type = "number"; cash.min = spec.min_cash; cash.max = spec.max_cash; cash.value = 0; cash.required = true;
    field(form, "Cash you pay (negative to receive)", cash);
    submitForm(form, "Offer trade", async () => {
      if (+give.value < 0 && +take.value < 0) throw new Error("Choose at least one deed.");
      await send({give:+give.value, take:+take.value, cash:+cash.value});
    });
    details.append(form);
    container.append(details);
  }
  function renderDecision(container, decision) {
    const {query:q, options, trade, bid} = decision;
    const space = q.square >= 0 ? board.name(q.square) : "";
    const headings = {roll:"Your roll", buy:space, bid:q.amount < 0 ? "Building auction" : "Property auction",
      manage:"Manage your deeds", liquidate:"Raise cash", jail:"You’re in jail",
      offer:"Trade window", accept:"Trade offer"};
    container.append(element("h3", headings[q.kind] || "Your decision"));
    let detail = {roll:"Roll the dice to move your token.", buy:`Buy for ${money(q.amount)} or send it to auction.`,
      bid:`Set your maximum bid for ${space}.`, manage:"Choose a property action or continue.",
      liquidate:`Raise funds for a payment of ${money(q.amount)}.`, jail:"Choose how to leave jail.",
      offer:"Propose a deal or continue your turn."}[q.kind];
    if (q.kind === "accept") {
      const receive = [q.give >= 0 && board.name(q.give), q.cash > 0 && money(q.cash)].filter(Boolean);
      const give = [q.take >= 0 && board.name(q.take), q.cash < 0 && money(-q.cash)].filter(Boolean);
      detail = `You receive ${receive.join(" and ") || "nothing"}. You give ${give.join(" and ") || "nothing"}.`;
    }
    if (detail) container.append(element("p", detail));
    const gameId = id;
    async function send(action) {
      if (busySubmit || gameId !== id) return;
      busySubmit = true;
      const controls = [...container.querySelectorAll("button,input,select")];
      controls.forEach(c => c.disabled = true);
      try {
        await api(credential.submit_url, {token:credential.token, body:{
          protocol:"monopoly.duel.v1", game_id:gameId, request_id:decision.request_id, action}});
        await refresh();
      } catch (err) {
        inlineError(container, err.message);
      } finally {
        busySubmit = false;
        controls.forEach(c => c.disabled = false);
      }
    }
    if (bid && bid.max >= bid.min) {
      const form = element("form");
      const amount = element("input");
      amount.type = "number"; amount.required = true;
      amount.min = bid.min; amount.max = bid.max;
      amount.value = Math.min(bid.max, Math.max(bid.min, q.amount > 0 ? q.amount : 10));
      field(form, `Maximum bid (${money(bid.min)}–${money(bid.max)})`, amount);
      submitForm(form, "Submit bid", () => send({value:+amount.value}));
      container.append(form);
    }
    if (options.length > 5) {
      const form = element("form");
      const select = element("select");
      options.forEach((o,i) => select.append(choice(i, o.label)));
      field(form, "Available actions", select);
      submitForm(form, "Confirm action", () => send(options[+select.value].action));
      container.append(form);
    } else options.forEach((o,i) => container.append(button(o.label, () => send(o.action), !bid && i === 0)));
    if (trade) tradeForm(container, trade, send);
  }
  function renderActions() {
    const container = $("actions");
    const key = JSON.stringify([id, room?.status, room?.decision?.request_id, room?.decision?.player,
      room?.state?.active, room?.players, room?.winner, credential?.seat, room?.reason]);
    if (key === actionKey) return;
    actionKey = key;
    container.replaceChildren();
    if (!room) {
      container.append(element("h3", "Ready to play?"), element("p", "Open a two-player table for friends or guest AIs, then share the invite."),
        button("Start a game", () => setup(), true), button("Join a table", () => showJoin(true)));
    } else if (room.status === "waiting") {
      const count = room.players.filter(p => p.kind === "open").length;
      container.append(element("h3", count === 2 ? "The table is open" : "One seat to fill"),
        element("p", "The game starts when both players take their seats."));
      if (!credential) container.append(button("Take a seat", () => showJoin(false), true));
      container.append(button("Invite players", invite, !!credential));
    } else if (room.status === "complete" || room.status === "error") {
      const winner = room.players[room.winner];
      container.append(element("h3", room.status === "error" ? "Game interrupted" : winner ? winner.name + " wins!" : "The game is a draw"),
        element("p", room.status === "error" ? "The host stopped this game." : winner ?
          "The other player went bankrupt." : `The table reached its limit of ${room.turn_cap} turns.`),
        button("New game", () => setup(), true));
    } else if (credential && room.decision?.player === credential.seat) {
      renderDecision(container, room.decision);
    } else {
      const who = room.players[room.decision?.player ?? room.state?.active ?? 0];
      container.append(element("h3", who.name + " is playing"),
        element("p", credential ? "Your controls appear when there’s a decision for you." : "You’re watching live. Moves appear on the board as the game unfolds."));
    }
  }
  function render() {
    const state = room?.state;
    const players = room?.players || blankPlayers;
    $("table-title").textContent = "Game room";
    $("connection-status").textContent = id && room ? "Table " + id : publicOrigin() ? "Online table" : "Local table";
    $("turn-number").textContent = state?.turn ? state.turn + (room.status === "running" ? " · " + players[state.active].name + "’s turn" : " · Final") : "—";
    $("room-label").textContent = room ? "Table " + id + " · Two-player classic" : "Two-player classic";
    $("game-status").textContent = ({waiting:"Lobby", running:"In play", complete:"Finished", error:"Stopped"})[room?.status] || "Lobby";
    board.render({board:boardInfo, players:players.filter(p => p.kind !== "open").map(p => ({...p, position:state?.position[p.seat] ?? 0})),
      owners:state?.owner || [], levels:state?.level || [], mortgaged:state?.mortgaged || [], dice:room?.dice || null});
    renderPlayers(players, state); renderActions(); renderEvents();
  }
  function failed(error) {
    $("connection-status").textContent = error.status === 404 ? "Table unavailable" : "Host offline";
    if (error.status === 404 || !room) {
      $("actions").replaceChildren(element("p", error.message), button("New table", () => setup(), true), button("Join a table", () => showJoin(true)));
      actionKey = "";
    }
  }
  let nextRefresh = 0;
  async function refresh() {
    if (!id) return;
    const table = id, generation = ++nextRefresh;
    try {
      const claim = !credential && load("claim-" + table);
      if (claim) {
        try { await takeSeat(claim.name); }
        catch (err) { if (![400, 404, 409].includes(err.status)) throw err; }
      }
      const found = await api("/api/tables/" + table);
      if (table !== id || generation !== nextRefresh) return;
      room = found;
      render();
    } catch (err) { if (table === id && generation === nextRefresh) failed(err); }
  }
  $("new-table-button").onclick = () => setup();
  $("invite-button").onclick = invite;
  async function init() {
    if (id) credential = load("seat-" + id) || null;
    const [boardResult, hostResult] = await Promise.allSettled([api("/api/board"), api("/api/host")]);
    if (boardResult.status === "fulfilled") boardInfo = boardResult.value;
    if (hostResult.status === "fulfilled") host = hostResult.value;
    render();
    await refresh();
    setInterval(async () => {
      if (busy || !id || room?.status === "complete" || room?.status === "error") return;
      busy = true;
      try { await refresh(); } finally { busy = false; }
    }, 800);
  }
  init();
})();
