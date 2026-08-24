// NetFrame playtest client -- bootstrap and glue.

// Everything that leaves this process goes through `api.js` -- see the note at
// the top of that file. No view in this client calls `fetch` itself.
import { api } from './api.js';
import * as C from './cards.js';
import { BoardView, abbrev } from './board.js';
import { ParamForm } from './params.js';
import { renderDecision } from './decisions.js';

const $ = (id) => document.getElementById(id);
const STORE_KEY = 'netframe.gameId';
const ZONES = ['High', 'Mid', 'Low'];

const app = {
  catalogue: {},
  decks: [],
  gameId: null,
  view: null,
  board: null,
  setupForm: null,
  drawerForm: null,
  aiSchema: null,
  selection: { player: [], ai: [] },
  selectedFrame: null,
  commitSelection: [],
  orderPick: [],
  lastPendingSig: null,
  threatCache: new Map(),
  currentView: 'board',
  options: { los: false, threat: true, cards: false, coords: false },
};

// ---------------------------------------------------------------- boot

async function boot() {
  C.initModal();
  bindChrome();
  try {
    const [catalogue, decks, aiSchema] = await Promise.all([
      api.getCatalogue(), api.getDecks(), api.getAiParams(),
    ]);
    app.catalogue = catalogue;
    C.setCatalogue(catalogue);
    app.decks = decks.decks || [];
    app.aiSchema = aiSchema;
  } catch (err) {
    $('setup-error').hidden = false;
    $('setup-error').textContent = `Could not reach the server: ${err.message}`;
    return;
  }
  buildSetupScreen();
  const saved = localStorage.getItem(STORE_KEY);
  if (saved) {
    $('resume-game').hidden = false;
    $('resume-game').addEventListener('click', () => resume(saved));
  }
  // `/?game=<id>` opens a running game straight away -- start one on the
  // laptop, send yourself the link, carry on playing it on the phone.
  const params = new URLSearchParams(location.search);
  const deepLink = params.get('game');
  if (deepLink) {
    await resume(deepLink);
    const wanted = params.get('view');
    if (wanted && document.getElementById(`view-${wanted}`)) showView(wanted);
  }
}

function bindChrome() {
  $('open-drawer').addEventListener('click', () => setDrawer(true));
  $('close-drawer').addEventListener('click', () => setDrawer(false));
  $('drawer-scrim').addEventListener('click', () => setDrawer(false));
  $('sheet-grip').addEventListener('click', () => {
    const sheet = $('sheet');
    sheet.dataset.open = sheet.dataset.open === '1' ? '0' : '1';
    syncSheetHeight();
  });
  for (const tab of document.querySelectorAll('#tabs .tab')) {
    tab.addEventListener('click', () => showView(tab.dataset.view));
  }
  $('drawer-undo').addEventListener('click', () => guard(async () => {
    setView(await api.undo(app.gameId));
    toast('Stepped back one decision');
  }));
  $('drawer-refresh').addEventListener('click', () => guard(async () => {
    setView(await api.getState(app.gameId));
  }));
  $('drawer-new').addEventListener('click', () => {
    setDrawer(false);
    $('screen-game').removeAttribute('data-active');
    $('screen-setup').dataset.active = '1';
  });
  for (const [id, key] of [['opt-los', 'los'], ['opt-threat', 'threat'],
    ['opt-cards', 'cards'], ['opt-coords', 'coords']]) {
    const box = $(id);
    box.checked = app.options[key];
    box.addEventListener('change', () => {
      app.options[key] = box.checked;
      if (app.board) app.board.setOptions(app.options);
      if (key === 'los') refreshOverlays();
    });
  }
  $('apply-params').addEventListener('click', () => guard(async () => {
    const params = app.drawerForm.payload();
    await api.setAiParams(app.gameId, params);
    $('apply-note').textContent = 'Applied. The AI uses these from its next decision.';
    toast('AI parameters applied');
  }));
  window.addEventListener('resize', syncSheetHeight);
}

// ---------------------------------------------------------------- setup

function buildSetupScreen() {
  const legal = app.decks;
  const pick = (host, side) => {
    host.innerHTML = '';
    for (const deck of legal) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'deck-chip';
      chip.innerHTML = `<b>${C.escapeHtml(deck.frame || deck.label)}</b>
        <small>${C.escapeHtml(deck.faction || '')} · ${deck.size} cards</small>`;
      if (!deck.legal) {
        chip.dataset.illegal = '1';
        chip.title = deck.errors.join('\n');
      }
      chip.addEventListener('click', () => {
        const chosen = app.selection[side];
        const at = chosen.indexOf(deck.name);
        if (at >= 0) chosen.splice(at, 1);
        else chosen.push(deck.name);
        const limit = Number($('frames-per-side').value) || 3;
        while (chosen.length > limit) chosen.shift();
        syncDeckChips();
      });
      host.appendChild(chip);
    }
  };
  pick($('player-decks'), 'player');
  pick($('ai-decks'), 'ai');

  const names = legal.map((d) => d.name);
  app.selection.player = names.slice(0, 3);
  app.selection.ai = names.slice(3, 6);
  syncDeckChips();

  $('randomise-ai').addEventListener('click', () => {
    const pool = names.filter((n) => !app.selection.player.includes(n));
    const shuffled = pool.sort(() => Math.random() - 0.5);
    app.selection.ai = shuffled.slice(0, Number($('frames-per-side').value) || 3);
    syncDeckChips();
  });
  $('frames-per-side').addEventListener('change', syncDeckChips);

  app.setupForm = new ParamForm($('setup-params'), $('preset-row'));
  app.setupForm.setSchema(app.aiSchema);
  $('ai-source-note').textContent = app.aiSchema.source === 'fallback'
    ? (app.aiSchema.note || 'Using the server\'s stand-in AI.')
    : `Parameters served by ${app.aiSchema.source}.`;

  $('start-game').addEventListener('click', startGame);
}

function syncDeckChips() {
  const limit = Number($('frames-per-side').value) || 3;
  for (const [side, host] of [['player', $('player-decks')], ['ai', $('ai-decks')]]) {
    const chosen = app.selection[side];
    [...host.children].forEach((chip, i) => {
      const deck = app.decks[i];
      if (chosen.includes(deck.name)) chip.dataset.on = '1';
      else delete chip.dataset.on;
    });
  }
  $('setup-squad-hint').textContent =
    `Pick ${limit}. Chosen ${app.selection.player.length}/${limit}.`;
}

async function startGame() {
  const limit = Number($('frames-per-side').value) || 3;
  const err = $('setup-error');
  err.hidden = true;
  if (app.selection.player.length !== limit || app.selection.ai.length !== limit) {
    err.hidden = false;
    err.textContent = `Choose exactly ${limit} decks for each side.`;
    return;
  }
  const seedRaw = $('seed').value.trim();
  await guard(async () => {
    const result = await api.newGame({
      seed: seedRaw === '' ? null : Number(seedRaw),
      playerDecks: app.selection.player,
      aiDecks: app.selection.ai,
      framesPerSide: limit,
      aiParams: app.setupForm.payload(),
    });
    app.gameId = result.gameId;
    localStorage.setItem(STORE_KEY, app.gameId);
    enterGame(result.view);
  });
}

async function resume(gameId) {
  await guard(async () => {
    const view = await api.getState(gameId);
    app.gameId = gameId;
    localStorage.setItem(STORE_KEY, gameId);
    enterGame(view);
  });
}

function enterGame(view) {
  $('screen-setup').removeAttribute('data-active');
  $('screen-game').dataset.active = '1';
  if (!app.board) {
    app.board = new BoardView($('board-canvas'));
    app.board.onTapTile = onTapTile;
    app.board.onTapFrame = onTapFrame;
    app.board.setOptions(app.options);
    bindBoardTools();
  }
  if (!app.drawerForm) {
    app.drawerForm = new ParamForm($('drawer-params'), $('drawer-preset-row'));
    app.drawerForm.setSchema(app.aiSchema);
    $('drawer-ai-note').textContent = app.aiSchema.source === 'fallback'
      ? (app.aiSchema.note || '')
      : `Served by ${app.aiSchema.source}.`;
  }
  setView(view);
  requestAnimationFrame(() => { app.board.fit(); });
}

function bindBoardTools() {
  $('tool-fit').addEventListener('click', () => app.board.fit());
  $('tool-in').addEventListener('click', () => app.board.zoomBy(1.35));
  $('tool-out').addEventListener('click', () => app.board.zoomBy(0.74));
  $('tool-focus').addEventListener('click', focusActive);
  $('tool-los').addEventListener('click', () => {
    app.options.los = !app.options.los;
    $('opt-los').checked = app.options.los;
    $('tool-los').dataset.on = app.options.los ? '1' : '0';
    app.board.setOptions(app.options);
    refreshOverlays();
  });
}

// ---------------------------------------------------------------- state

function setView(view) {
  app.view = view;
  const pending = view.pending;
  const sig = pending
    ? `${pending.kind}:${pending.frameId}:${(pending.options || []).length}:${view.turn}:${view.log.length}`
    : `none:${view.turn}:${view.phase}`;
  if (sig !== app.lastPendingSig) {
    app.lastPendingSig = sig;
    app.commitSelection = [];
    app.orderPick = [];
    if (pending && !pending.waiting && pending.seat === view.seat) {
      if (pending.kind === 'commit_actions') showView('plan');
      else if (['move', 'attack_target', 'choose_block'].includes(pending.kind)) showView('board');
      $('sheet').dataset.open = '1';
    }
    if (pending && pending.frameId) app.selectedFrame = pending.frameId;
  }
  app.board.setView(view, view.seat);
  app.board.setActing(actingFrameId());
  app.board.setSelected(app.selectedFrame);
  renderHud();
  renderFrameStrip();
  renderPlan();
  renderLadder();
  renderLog();
  renderSheet();
  refreshOverlays();
  syncSheetHeight();
  $('drawer-game-info').textContent =
    `Game ${view.gameId} · seat ${view.seat} · AI ${view.aiSource || 'unknown'}`;
}

function actingFrameId() {
  const view = app.view;
  const pending = view.pending;
  if (pending && pending.frameId
      && ['move', 'attack_target', 'resolve_order', 'effect_choice'].includes(pending.kind)) {
    return pending.frameId;
  }
  // The card mid-resolution is face up but not yet resolved.
  for (const frame of view.frames || []) {
    for (const card of frame.committed || []) {
      if (!card.faceDown && !card.resolved && card.key) return frame.id;
    }
  }
  return null;
}

async function send(kind, payload) {
  await guard(async () => {
    setView(await api.sendCommand(app.gameId, kind, payload));
  });
}

async function guard(fn) {
  $('busy').hidden = false;
  try {
    await fn();
  } catch (err) {
    toast(err.status === 400 ? `Illegal: ${err.message}` : err.message);
    if (err.status === 404) {
      localStorage.removeItem(STORE_KEY);
    }
  } finally {
    $('busy').hidden = true;
  }
}

let toastTimer = null;
function toast(text) {
  const el = $('toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3600);
}

function setDrawer(open) {
  $('drawer').dataset.open = open ? '1' : '0';
  $('drawer').setAttribute('aria-hidden', open ? 'false' : 'true');
  $('drawer-scrim').hidden = !open;
}

function showView(name) {
  app.currentView = name;
  for (const view of document.querySelectorAll('#stage .view')) {
    if (view.id === `view-${name}`) view.dataset.active = '1';
    else view.removeAttribute('data-active');
  }
  for (const tab of document.querySelectorAll('#tabs .tab')) {
    if (tab.dataset.view === name) tab.dataset.active = '1';
    else tab.removeAttribute('data-active');
  }
  if (name === 'board' && app.board) app.board.invalidate();
}

function syncSheetHeight() {
  const sheet = $('sheet');
  document.documentElement.style.setProperty('--sheet-h', `${sheet.offsetHeight}px`);
}

// ---------------------------------------------------------------- hud

function renderHud() {
  const view = app.view;
  $('hud-turn').textContent = view.over ? 'Final' : `Turn ${view.turn}`;
  $('hud-phase').textContent = view.phase;
  const prio = $('hud-priority');
  prio.textContent = view.priority === view.seat ? 'priority: you' : 'priority: AI';
  prio.dataset.mine = view.priority === view.seat ? '1' : '0';
  const vp = view.vp || {};
  const mine = Number(vp[String(view.seat)] || 0);
  const theirs = Object.entries(vp).filter(([s]) => Number(s) !== view.seat)
    .reduce((a, [, v]) => a + Number(v), 0);
  $('hud-vp').innerHTML = `<b>${mine}</b><i>–</i><b>${theirs}</b>`;
  const pending = view.pending;
  $('hud-prompt').textContent = view.over ? 'Game over'
    : (pending && !pending.waiting && pending.seat === view.seat
      ? pending.prompt
      : (pending ? 'The AI is deciding…' : 'Resolving…'));
}

function renderFrameStrip() {
  const host = $('frame-strip');
  host.innerHTML = '';
  const frames = [...(app.view.frames || [])].sort(
    (a, b) => (a.seat === app.view.seat ? 0 : 1) - (b.seat === app.view.seat ? 0 : 1));
  for (const f of frames) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'fchip';
    chip.dataset.seat = f.seat === app.view.seat ? 'mine' : 'theirs';
    if (f.id === app.selectedFrame) chip.dataset.sel = '1';
    if (!f.alive) chip.dataset.dead = '1';
    const bars = ZONES.map((z) => {
      const armour = f.armour[z] || 1;
      const pct = Math.min(100, Math.round(100 * (f.damage[z] || 0) / armour));
      const last = f.lastHit && f.lastHit[z] ? ' data-last="1"' : '';
      return `<span class="zbar"${last} title="${z} ${f.damage[z]}/${f.armour[z]}"><i style="width:${pct}%"></i></span>`;
    }).join('');
    const statuses = Object.entries(f.statuses || {})
      .filter(([, n]) => n > 0).map(([k, n]) => `${k.slice(0, 4)}${n}`);
    chip.innerHTML = `<div class="fname">${C.escapeHtml(f.name)}</div>
      <div class="fbars">${bars}</div>
      <div class="fmeta"><span>mv ${f.movement}</span>
        <span>${(f.committed || []).length}c</span>
        ${f.shields ? `<span>sh ${f.shields}</span>` : ''}
        ${statuses.length ? `<em>${C.escapeHtml(statuses.join(' '))}</em>` : ''}</div>`;
    chip.addEventListener('click', () => selectFrame(f.id));
    host.appendChild(chip);
  }
}

function selectFrame(frameId) {
  app.selectedFrame = app.selectedFrame === frameId ? null : frameId;
  app.board.setSelected(app.selectedFrame);
  renderFrameStrip();
  refreshOverlays();
  const frame = (app.view.frames || []).find((f) => f.id === app.selectedFrame);
  if (frame && frame.pos) {
    showView('board');
    app.board.centreOn(frame.pos.x, frame.pos.y);
  }
  renderTileReadout(frame);
}

function renderTileReadout(frame) {
  const el = $('tile-readout');
  if (!frame) { el.hidden = true; return; }
  const zones = ZONES.map((z) =>
    `${z} ${frame.damage[z]}/${frame.armour[z]}${frame.lastHit && frame.lastHit[z] ? '!' : ''}`).join('  ');
  const statuses = Object.entries(frame.statuses || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `${k} ${n}`).join(', ');
  el.hidden = false;
  el.innerHTML = `<b>${C.escapeHtml(frame.name)}</b> · ${frame.faction} · move ${frame.movement}
    <br>${zones}${frame.shields ? ` · shields ${frame.shields}` : ''}
    ${statuses ? `<br>${C.escapeHtml(statuses)}` : ''}
    <br>${(frame.committed || []).length} committed · ${(frame.onField || []).length} on field
    · deck ${frame.deckCount} · discard ${frame.discardCount}`;
}

// ---------------------------------------------------------------- board glue

function onTapFrame(frame) {
  const pending = app.view.pending;
  if (pending && !pending.waiting && pending.seat === app.view.seat) {
    if (pending.kind === 'attack_target') {
      const option = (pending.options || []).find((o) => o.id === frame.id);
      if (option) { send('attack_target', { kind: option.kind, id: option.id }); return; }
    }
    if (pending.kind === 'move') {
      // Tapping an occupied tile during a move is a selection, not a move.
    }
  }
  selectFrame(frame.id);
}

function onTapTile(x, y) {
  const pending = app.view.pending;
  if (pending && !pending.waiting && pending.seat === app.view.seat) {
    if (pending.kind === 'move') {
      const option = (pending.options || []).find((o) => o.x === x && o.y === y);
      if (option) { send('move', { x, y, cost: option.cost }); return; }
      toast('That tile is out of range');
      return;
    }
    if (pending.kind === 'attack_target') {
      const token = (app.view.tokens || []).find(
        (t) => t.pos && t.pos.x === x && t.pos.y === y && t.alive !== false);
      if (token) {
        const option = (pending.options || []).find((o) => o.id === token.id);
        if (option) { send('attack_target', { kind: option.kind, id: option.id }); return; }
      }
    }
  }
  const tile = app.board.tiles.get(`${x},${y}`);
  const el = $('tile-readout');
  if (!tile) { el.hidden = true; return; }
  const obj = app.board.objectiveTiles.get(`${x},${y}`);
  const token = (app.view.tokens || []).find((t) => t.pos && t.pos.x === x && t.pos.y === y);
  el.hidden = false;
  el.innerHTML = `<b>(${x}, ${y})</b> elevation ${tile.elev}
    ${tile.impassable ? ' · impassable' : ''}${tile.obstacle ? ' · obstacle' : ''}
    <br>${C.escapeHtml(tile.card || 'open ground')}
    ${obj ? `<br>objective: <b>${C.escapeHtml(obj.name)}</b> — ${C.escapeHtml(obj.status)}` : ''}
    ${token ? `<br>token: ${C.escapeHtml(token.kind)} ${token.hp}/${token.maxHp}` : ''}`;
}

function focusActive() {
  const id = actingFrameId() || app.selectedFrame;
  const frame = (app.view.frames || []).find((f) => f.id === id);
  if (frame && frame.pos) {
    showView('board');
    app.board.centreOn(frame.pos.x, frame.pos.y, app.board.tacticalZoom());
  } else {
    app.board.fit();
  }
}

async function refreshOverlays() {
  const view = app.view;
  const pending = view.pending;
  const overlays = { reach: new Map(), los: new Set(), targets: new Set() };
  const mine = pending && !pending.waiting && pending.seat === view.seat;

  if (mine && pending.kind === 'move') {
    for (const o of pending.options) overlays.reach.set(`${o.x},${o.y}`, o.cost);
  } else if (mine && pending.kind === 'attack_target') {
    for (const o of pending.options) {
      const frame = (view.frames || []).find((f) => f.id === o.id);
      const token = (view.tokens || []).find((t) => t.id === o.id);
      const pos = (frame && frame.pos) || (token && token.pos);
      if (pos) overlays.targets.add(`${pos.x},${pos.y}`);
    }
  }

  const selected = (view.frames || []).find((f) => f.id === app.selectedFrame);
  const wantThreat = selected && selected.alive && selected.seat !== view.seat && app.options.threat;
  if ((app.options.los || wantThreat) && selected && selected.alive) {
    const key = `${view.gameId}:${selected.id}:${view.log.length}`;
    let data = app.threatCache.get(key);
    if (!data) {
      try {
        data = await api.getThreat(app.gameId, selected.id);
        app.threatCache.set(key, data);
        if (app.threatCache.size > 24) {
          app.threatCache.delete(app.threatCache.keys().next().value);
        }
      } catch { data = null; }
    }
    if (data) {
      if (app.options.los) for (const [x, y] of data.los) overlays.los.add(`${x},${y}`);
      if (wantThreat && !overlays.reach.size) {
        for (const [x, y, cost] of data.reach) overlays.reach.set(`${x},${y}`, cost);
      }
    }
  }
  app.board.setOverlays(overlays);
  renderLegend(overlays, mine ? pending : null);
}

function renderLegend(overlays, pending) {
  const host = $('board-legend');
  const bits = [];
  if (overlays.reach.size) {
    bits.push(pending && pending.kind === 'move'
      ? 'green = legal destination'
      : 'green = selected frame\'s reach (base movement)');
  }
  if (overlays.targets.size) bits.push('red pulse = legal target');
  if (overlays.los.size) bits.push('red wash = line of sight');
  if (!bits.length) bits.push('pinch to zoom · double tap = fit');
  host.innerHTML = bits.map((b) => `<span>${C.escapeHtml(b)}</span>`).join('');
}

// ---------------------------------------------------------------- plan view

function renderPlan() {
  const view = app.view;
  const pending = view.pending;
  const committing = pending && !pending.waiting && pending.seat === view.seat
    && pending.kind === 'commit_actions';
  const frame = (view.frames || []).find(
    (f) => f.id === (committing ? pending.frameId : app.selectedFrame))
    || (view.frames || []).find((f) => f.seat === view.seat && f.alive);

  $('plan-frame').textContent = frame ? frame.name : '';
  const slots = document.querySelectorAll('.plan-slots .slot');
  slots.forEach((slot, i) => {
    slot.innerHTML = '<span class="slot-label">Action ' + (i + 1) + '</span>';
    delete slot.dataset.filled;
  });

  const hand = $('hand-grid');
  hand.innerHTML = '';

  if (committing) {
    $('plan-count').textContent =
      `${frame ? frame.hand.length : 0} in hand · commit 2 face down`;
    app.commitSelection.forEach((uid, i) => {
      const slot = slots[i];
      if (!slot) return;
      slot.dataset.filled = '1';
      const key = uidKey(uid);
      const thumb = C.thumb(key, { width: 240, onTap: () => toggleCommit(uid) });
      thumb.style.position = 'absolute';
      thumb.style.inset = '0';
      thumb.style.border = '0';
      slot.appendChild(thumb);
    });
    for (const option of pending.options) {
      const selected = app.commitSelection.includes(option.uid);
      const el = C.thumb(option.key, {
        width: 240,
        selected,
        dim: !selected && app.commitSelection.length >= 2,
        onTap: () => toggleCommit(option.uid),
      });
      attachLongPress(el, option.key);
      hand.appendChild(el);
    }
    return;
  }

  // Not planning: show what is on the field for the chosen frame.
  const rows = [];
  if (frame) {
    for (const card of frame.committed || []) rows.push([card, 'committed']);
    for (const card of frame.onField || []) rows.push([card, 'resolved']);
    for (const card of frame.aside || []) rows.push([card, 'set aside']);
  }
  $('plan-count').textContent = rows.length
    ? `${rows.length} card(s) in play`
    : 'Nothing committed yet';
  for (const [card, tag] of rows) {
    if (!card.key) {
      const back = C.faceDown('face down');
      hand.appendChild(back);
      continue;
    }
    const el = C.thumb(card.key, { width: 240, tag, dim: tag === 'set aside' });
    attachLongPress(el, card.key);
    el.addEventListener('click', () => C.showCard(card.key));
    hand.appendChild(el);
  }
}

function attachLongPress(el, key) {
  let timer = null;
  const start = () => { timer = setTimeout(() => C.showCard(key), 480); };
  const stop = () => { clearTimeout(timer); };
  el.addEventListener('pointerdown', start);
  el.addEventListener('pointerup', stop);
  el.addEventListener('pointerleave', stop);
  el.addEventListener('pointercancel', stop);
}

function uidKey(uid) {
  const pending = app.view.pending;
  const option = pending && (pending.options || []).find((o) => o.uid === uid);
  if (option) return option.key;
  for (const frame of app.view.frames || []) {
    for (const card of [...(frame.hand || []), ...(frame.committed || []),
      ...(frame.onField || []), ...(frame.aside || [])]) {
      if (card.uid === uid && card.key) return card.key;
    }
  }
  return uid;
}

function toggleCommit(uid) {
  const at = app.commitSelection.indexOf(uid);
  if (at >= 0) app.commitSelection.splice(at, 1);
  else {
    if (app.commitSelection.length >= 2) app.commitSelection.shift();
    app.commitSelection.push(uid);
  }
  renderPlan();
  renderSheet();
}

// ---------------------------------------------------------------- ladder

function renderLadder() {
  const host = $('ladder');
  host.innerHTML = '';
  const view = app.view;
  const entries = [];
  for (const frame of view.frames || []) {
    const seat = frame.seat === view.seat ? 'mine' : 'theirs';
    for (const card of frame.committed || []) {
      entries.push({ frame, seat, card, state: card.faceDown ? 'hidden' : 'pending' });
    }
    for (const card of frame.onField || []) {
      entries.push({ frame, seat, card, state: 'resolved' });
    }
  }
  const initOf = (entry) => {
    if (!entry.card.key) return null;
    const info = C.card(entry.card.key);
    if (!info || !info.initiative || !info.initiative.length) return null;
    return Math.max(...info.initiative);
  };
  entries.sort((a, b) => {
    const ai = initOf(a);
    const bi = initOf(b);
    if (ai === null && bi === null) return 0;
    if (ai === null) return 1;
    if (bi === null) return -1;
    return bi - ai;
  });

  const head = document.createElement('h3');
  head.textContent = `Turn ${view.turn} — initiative order (printed values)`;
  host.appendChild(head);

  const note = document.createElement('p');
  note.className = 'hint';
  note.textContent = 'Highest initiative resolves first; ties alternate from the '
    + 'priority marker. The engine also applies −1 for a High zone on its last '
    + 'hit and ∓2 for Stunned/Stimmed, which are not shown here.';
  host.appendChild(note);

  if (!entries.length) {
    const empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = 'Nothing is committed yet.';
    host.appendChild(empty);
  }

  const acting = actingFrameId();
  for (const entry of entries) {
    const row = document.createElement('div');
    row.className = 'lrow';
    row.dataset.seat = entry.seat;
    if (entry.state === 'resolved') row.dataset.done = '1';
    const isNow = entry.state === 'pending' && entry.frame.id === acting;
    if (isNow) row.dataset.now = '1';

    const init = document.createElement('div');
    init.className = 'linit';
    const value = initOf(entry);
    if (value === null) { init.textContent = '?'; init.dataset.hidden = '1'; }
    else init.textContent = String(value);
    row.appendChild(init);

    const mid = document.createElement('div');
    mid.className = 'lmid';
    const name = document.createElement('b');
    name.textContent = entry.card.key ? C.displayName(entry.card.key) : 'face down';
    mid.appendChild(name);
    const sub = document.createElement('small');
    const tags = [entry.frame.name];
    if (entry.card.echo) tags.push('echo — can only block');
    if (entry.state === 'resolved') tags.push('resolved · can still block');
    if (entry.state === 'hidden') {
      tags.push(entry.seat === 'mine'
        ? 'face down — the AI cannot see it'
        : 'face down — hidden until it resolves');
    }
    if (isNow) tags.push('RESOLVING NOW');
    if (entry.card.key && C.isNotImplemented(entry.card.key)) tags.push('text not implemented');
    sub.textContent = tags.join(' · ');
    mid.appendChild(sub);
    if (entry.card.key) mid.appendChild(C.zonePips(entry.card.key));
    row.appendChild(mid);

    if (entry.card.key) {
      const wrap = document.createElement('div');
      wrap.className = 'lthumb';
      wrap.appendChild(C.thumb(entry.card.key, { width: 120, showName: false, initiative: false }));
      wrap.addEventListener('click', () => C.showCard(entry.card.key));
      row.appendChild(wrap);
    }
    host.appendChild(row);
  }
}

// ---------------------------------------------------------------- log

function renderLog() {
  const host = $('log');
  host.innerHTML = '';
  const byTurn = new Map();
  for (const entry of app.view.log || []) {
    if (!byTurn.has(entry.turn)) byTurn.set(entry.turn, []);
    byTurn.get(entry.turn).push(entry.text);
  }
  const turns = [...byTurn.keys()].sort((a, b) => b - a);
  for (const turn of turns) {
    const head = document.createElement('div');
    head.className = 'turnhead';
    head.textContent = `Turn ${turn}`;
    host.appendChild(head);
    for (const text of byTurn.get(turn).slice().reverse()) {
      const p = document.createElement('p');
      p.textContent = text;
      if (/hits|destroyed|damage/i.test(text)) p.className = 'hit';
      else if (/block/i.test(text)) p.className = 'block';
      else if (/^---|game over|scored/i.test(text)) p.className = 'big';
      host.appendChild(p);
    }
  }
}

// ---------------------------------------------------------------- sheet

function renderSheet() {
  renderDecision($('sheet-body'), {
    view: app.view,
    commitSelection: app.commitSelection,
    orderPick: app.orderPick,
    uidKey,
    toggleCommit,
    toggleOrder,
    setOrder: (order) => { app.orderPick = order; renderSheet(); },
    send,
    showView,
    focusActive,
    newGame: () => {
      $('screen-game').removeAttribute('data-active');
      $('screen-setup').dataset.active = '1';
    },
  });
  syncSheetHeight();
}

function toggleOrder(step) {
  const at = app.orderPick.indexOf(step);
  if (at >= 0) app.orderPick.splice(at, 1);
  else app.orderPick.push(step);
  renderSheet();
}

boot();
