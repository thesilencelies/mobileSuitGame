// NetFrame playtest client -- bootstrap and glue.

// Everything that leaves this process goes through `api.js` -- see the note at
// the top of that file. No view in this client calls `fetch` itself.
import { api } from './api.js';
import * as C from './cards.js';
import { BoardView, abbrev } from './board.js';
import { ParamForm } from './params.js';
import { prettyKind, renderDecision } from './decisions.js';

const $ = (id) => document.getElementById(id);
const STORE_KEY = 'netframe.gameId';
const PREFS_KEY = 'netframe.prefs';
const ZONES = ['High', 'Mid', 'Low'];

// The AI's whole turn arrives in one response, so it is played back rather
// than shown. These are milliseconds per beat -- a card revealed, a frame
// moved, an attack landed. "Instant" skips the playback entirely and jumps to
// the end state: that is the behaviour this replaced, kept for anyone who
// finds the pacing tedious.
const SPEEDS = [
  { id: 'instant', label: 'Instant', ms: 0 },
  { id: 'brisk', label: 'Brisk', ms: 450 },
  { id: 'steady', label: 'Steady', ms: 900 },
  { id: 'slow', label: 'Slow', ms: 1600 },
];

// A revealed card is the beat you actually *read*, so it is held longer than a
// move or a hit, which you take in at a glance.
const CARD_BEAT_DWELL = 1.35;

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
  // The frame read-out opens only when you ask for it: a decision selects a
  // frame on the board without covering the board with its panel.
  panelOpen: false,
  // The floating "what is resolving" card, folded down out of the way. It
  // sits over the bottom-left of the board and that is sometimes the tile you
  // want; tapping it toggles this.
  actingHidden: false,
  commitSelection: [],
  // Tiles marked for an effect that places things -- barricades, portal ends,
  // drones. Held here until the player commits them, so a three-barricade card
  // is one decision on the board rather than three confirmations.
  placeSelection: [],
  orderPick: [],
  // The battlefield each side brings. Empty means "deal me one".
  terrain: { player: '', ai: '' },
  terrainDecks: [],
  lastPendingSig: null,
  threatCache: new Map(),
  currentView: 'board',
  options: { los: false, threat: true, cards: false, coords: false, art: true },
  // Resolution order is nearly always the same order twice running, so the
  // last one accepted is offered back as a single tap. Remembered per frame:
  // a brawler and a sniper want different habits.
  lastOrder: { any: null, byFrame: {} },
  speed: 'steady',
  autoFollow: true,
  // Replay your own frames too. Off by default: your cards resolve under your
  // own hand, and a delay between a tap and its result is worse than no
  // animation. On, it covers the actions that had no choices left in them --
  // one legal target, one legal tile -- which otherwise happen off screen.
  replayMine: false,
  replay: { queue: [], timer: null, base: null },
  // A tap on the board proposes; a second tap on the same thing commits. See
  // `proposeTap` -- movement cannot be taken back and a phone makes a misfire
  // far too easy.
  confirm: null,
  // Which frame the player is deploying next. The engine offers every legal
  // (frame, tile) pair at once, so the client has to hold the frame half.
  deployFrame: null,
};

function loadPrefs() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') || {}; } catch { saved = {}; }
  if (saved.options) Object.assign(app.options, saved.options);
  if (saved.speed && SPEEDS.some((s) => s.id === saved.speed)) app.speed = saved.speed;
  if (typeof saved.autoFollow === 'boolean') app.autoFollow = saved.autoFollow;
  if (typeof saved.replayMine === 'boolean') app.replayMine = saved.replayMine;
  if (saved.lastOrder && typeof saved.lastOrder === 'object') {
    app.lastOrder = {
      any: saved.lastOrder.any || null,
      byFrame: saved.lastOrder.byFrame || {},
    };
  }
}

function savePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({
      options: app.options,
      speed: app.speed,
      autoFollow: app.autoFollow,
      replayMine: app.replayMine,
      lastOrder: app.lastOrder,
    }));
  } catch { /* private browsing, or a full quota: preferences are not vital */ }
}

function speedMs() {
  return (SPEEDS.find((s) => s.id === app.speed) || SPEEDS[2]).ms;
}

// ---------------------------------------------------------------- boot

async function boot() {
  C.initModal();
  loadPrefs();
  bindChrome();
  try {
    const [catalogue, decks, aiSchema, frames, health] = await Promise.all([
      api.getCatalogue(), api.getDecks(), api.getAiParams(), api.getFrames(),
      api.getHealth(),
    ]);
    app.catalogue = catalogue;
    C.setCatalogue(catalogue);
    C.setFrames(frames);
    app.frames = frames;
    app.decks = decks.decks || [];
    app.aiSchema = aiSchema;
    app.health = health;
    app.terrainDecks = decks.terrain || [];
  } catch (err) {
    $('setup-error').hidden = false;
    $('setup-error').textContent = `Could not reach the server: ${err.message}`;
    return;
  }
  buildSetupScreen();
  showBuildMarker();
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

/** Which build this is, on the setup screen and in the drawer.
 *
 *  There is no packaging step -- the app runs out of a clone -- so "am I on
 *  the code I just pulled?" has no other answer. The id is a hash of every
 *  source file the app runs and every static file the browser loaded, so it
 *  is identical on two machines holding the same code and different the
 *  moment any of it changes. Static files are served `no-cache`, so what the
 *  server hashes really is what is on screen.
 */
function showBuildMarker() {
  const health = app.health || {};
  if (!health.build) return;
  const text = `build ${health.build}`
    + (health.commit ? ` · commit ${health.commit}` : '');
  for (const id of ['build-marker', 'drawer-build']) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.textContent = text;
    el.title = `${health.files} source files · tap to copy`;
    el.addEventListener('click', () => {
      if (navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {});
      toast(text);
    });
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
    ['opt-cards', 'cards'], ['opt-coords', 'coords'], ['opt-art', 'art']]) {
    const box = $(id);
    box.checked = app.options[key];
    box.addEventListener('change', () => {
      app.options[key] = box.checked;
      if (app.board) app.board.setOptions(app.options);
      if (key === 'art') $('tool-art').dataset.on = box.checked ? '1' : '0';
      if (key === 'los') refreshOverlays();
      savePrefs();
    });
  }
  const follow = $('opt-autoscroll');
  follow.checked = app.autoFollow;
  follow.addEventListener('change', () => {
    app.autoFollow = follow.checked;
    savePrefs();
  });
  const mine = $('opt-replay-mine');
  mine.checked = app.replayMine;
  mine.addEventListener('change', () => {
    app.replayMine = mine.checked;
    savePrefs();
  });
  buildSpeedRow();
  $('replay-skip').addEventListener('click', () => finishReplay());
  $('apply-params').addEventListener('click', () => guard(async () => {
    const params = app.drawerForm.payload();
    await api.setAiParams(app.gameId, params);
    $('apply-note').textContent = 'Applied. The AI uses these from its next decision.';
    toast('AI parameters applied');
  }));
  window.addEventListener('resize', syncSheetHeight);
}

function buildSpeedRow() {
  const host = $('speed-row');
  host.innerHTML = '';
  for (const speed of SPEEDS) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'preset';
    chip.textContent = speed.label;
    if (speed.id === app.speed) chip.dataset.on = '1';
    chip.addEventListener('click', () => {
      app.speed = speed.id;
      savePrefs();
      buildSpeedRow();
    });
    host.appendChild(chip);
  }
}

// ---------------------------------------------------------------- setup

// Fielding two of the same frame is legal -- one deck per frame, one faction
// per squad, and nothing anywhere says the frames must differ -- but the
// picker used to be a toggle, so tapping Kuwagata twice *removed* it. Now a
// tap adds a copy and the squad row below holds the slots, each removable, so
// two Kuwagatas is three taps and looks like what it is.
function buildSetupScreen() {
  const legal = app.decks;
  const pick = (host, side) => {
    host.innerHTML = '';
    for (const deck of legal) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'deck-chip';
      chip.innerHTML = `<b>${C.escapeHtml(deck.frame || deck.label)}</b>
        <small>${C.escapeHtml(deck.faction || '')} · ${deck.size} cards</small>
        <span class="chip-count"></span>`;
      if (!deck.legal) {
        chip.dataset.illegal = '1';
        chip.title = deck.errors.join('\n');
      }
      chip.addEventListener('click', () => {
        const chosen = app.selection[side];
        const limit = Number($('frames-per-side').value) || 3;
        if (chosen.length >= limit) {
          toast(`That is ${limit} frames already — remove one first`);
          return;
        }
        chosen.push(deck.name);
        syncDeckChips();
      });
      host.appendChild(chip);
    }
  };
  pick($('player-decks'), 'player');
  pick($('ai-decks'), 'ai');
  buildTerrainPicker();

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

/** The battlefield each side brings, as chips listing its five objectives.
 *
 *  "Random" is the default and is not a cop-out: it is the old behaviour, and
 *  the engine deals a pair from the game's own rng so the seed still
 *  reproduces the board. Naming one pins that seat's half of the map.
 */
function buildTerrainPicker() {
  for (const [side, host] of [['player', $('player-terrain')],
    ['ai', $('ai-terrain')]]) {
    host.innerHTML = '';
    const options = [{ name: '', label: 'Random', objectives: [] },
      ...app.terrainDecks];
    for (const deck of options) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'deck-chip';
      chip.innerHTML = `<b>${C.escapeHtml(deck.label)}</b>`
        + (deck.objectives.length
          ? `<span class="terrain-chip-objectives">${
            C.escapeHtml(deck.objectives.join(' · '))}</span>`
          : '<small>dealt from the shipped battlefields</small>');
      chip.addEventListener('click', () => {
        app.terrain[side] = deck.name;
        syncTerrainChips();
      });
      host.appendChild(chip);
    }
  }
  syncTerrainChips();
}

function syncTerrainChips() {
  for (const [side, host] of [['player', $('player-terrain')],
    ['ai', $('ai-terrain')]]) {
    const chosen = app.terrain[side] || '';
    const names = ['', ...app.terrainDecks.map((d) => d.name)];
    [...host.children].forEach((chip, i) => {
      if (names[i] === chosen) chip.dataset.on = '1';
      else delete chip.dataset.on;
    });
  }
}

function syncDeckChips() {
  const limit = Number($('frames-per-side').value) || 3;
  for (const [side, host, slots] of [
    ['player', $('player-decks'), $('player-squad')],
    ['ai', $('ai-decks'), $('ai-squad')]]) {
    const chosen = app.selection[side];
    while (chosen.length > limit) chosen.pop();
    const counts = new Map();
    for (const name of chosen) counts.set(name, (counts.get(name) || 0) + 1);
    [...host.children].forEach((chip, i) => {
      const deck = app.decks[i];
      const count = counts.get(deck.name) || 0;
      if (count) chip.dataset.on = '1';
      else delete chip.dataset.on;
      if (chosen.length >= limit && !count) chip.dataset.full = '1';
      else delete chip.dataset.full;
      const badge = chip.querySelector('.chip-count');
      if (badge) badge.textContent = count > 1 ? `×${count}` : '';
    });
    renderSquadSlots(slots, side, limit);
  }
  $('setup-squad-hint').textContent =
    `Pick ${limit} — the same frame more than once is allowed. `
    + `Chosen ${app.selection.player.length}/${limit}.`;
}

/** The chosen squad, one removable slot per frame, in build order. */
function renderSquadSlots(host, side, limit) {
  if (!host) return;
  host.innerHTML = '';
  const chosen = app.selection[side];
  const seen = new Map();
  for (let i = 0; i < limit; i++) {
    const name = chosen[i];
    const slot = document.createElement('button');
    slot.type = 'button';
    slot.className = 'squad-slot';
    if (!name) {
      slot.dataset.empty = '1';
      slot.innerHTML = `<span class="ss-name">empty</span>`;
      host.appendChild(slot);
      continue;
    }
    const deck = app.decks.find((d) => d.name === name);
    const frameName = (deck && deck.frame) || name;
    const copies = chosen.filter((n) => n === name).length;
    const index = (seen.get(name) || 0) + 1;
    seen.set(name, index);
    const img = document.createElement('img');
    img.src = api.frameImageUrl(frameName);
    img.alt = '';
    img.addEventListener('error', () => img.remove(), { once: true });
    slot.appendChild(img);
    const label = document.createElement('span');
    label.className = 'ss-name';
    // Say which copy this is here too, so the squad you built reads the same
    // way the battle will.
    label.textContent = copies > 1
      ? `${frameName} ${['I', 'II', 'III', 'IV', 'V'][index - 1] || index}`
      : frameName;
    slot.appendChild(label);
    const drop = document.createElement('span');
    drop.className = 'ss-drop';
    drop.textContent = '×';
    slot.appendChild(drop);
    slot.title = 'Remove from the squad';
    slot.addEventListener('click', () => {
      chosen.splice(i, 1);
      syncDeckChips();
    });
    host.appendChild(slot);
  }
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
      playerTerrain: app.terrain.player,
      aiTerrain: app.terrain.ai,
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
    savePrefs();
  });
  $('tool-art').dataset.on = app.options.art ? '1' : '0';
  $('tool-art').addEventListener('click', () => {
    app.options.art = !app.options.art;
    $('opt-art').checked = app.options.art;
    $('tool-art').dataset.on = app.options.art ? '1' : '0';
    app.board.setOptions(app.options);
    savePrefs();
  });
}

// ---------------------------------------------------------------- replay
//
// `POST /command` runs the AI until the human is on the clock again, so one
// tap can cover three frames moving, an attack, a compulsory block and a
// death. The server sends a snapshot per logged AI decision; this plays them
// through the board at the pace the player chose, then lands on the real view.
// It is skippable, and skipping always ends on exactly the same state.

function playReplay(finalView) {
  cancelReplay();
  const frames = (finalView.replay || []).filter(Boolean);
  const ms = speedMs();
  if (!frames.length || ms === 0) { setView(finalView); return; }

  // The board is static and the snapshots omit it, so each one is shown as an
  // overlay on the state we already have.
  app.replay = { queue: frames, timer: null, base: finalView, at: 0 };
  $('replay-bar').hidden = false;
  $('view-board').dataset.replay = '1';
  showView('board');
  step();

  function step() {
    const state = app.replay;
    if (!state.queue.length || state.at >= state.queue.length) { finishReplay(); return; }
    const snap = state.queue[state.at++];
    setView(mergeSnapshot(state.base, snap), { replaying: true });
    // The marks for *this* beat: who moved from where, what landed, what was
    // blocked. Set after `setView`, which clears them for the resting board.
    app.board.setBeat(snap.beat || null);
    const acting = (app.view.resolving || {}).frameId;
    const frame = (app.view.frames || []).find((f) => f.id === acting);
    // Follow the action rather than the actor: a shot from off screen is worth
    // watching at the end that takes the damage.
    const focus = beatFocus(snap, frame);
    if (app.autoFollow && focus) app.board.centreOn(focus.x, focus.y);
    $('replay-text').textContent = beatText(snap);
    $('replay-count').textContent = `${state.at}/${state.queue.length}`;
    const dwell = (snap.beat || {}).event === 'card' ? ms * CARD_BEAT_DWELL : ms;
    state.timer = setTimeout(step, dwell);
  }
}

/** One replay snapshot painted onto the last full view.
 *
 *  Snapshots carry no board (it does not change) and no card uids (a card that
 *  was face up while it resolved can be back in a hand by now), so the card
 *  rows are replaced by counts and the frame rows are merged field by field.
 */
function mergeSnapshot(base, snap) {
  const byId = new Map((snap.frames || []).map((f) => [f.id, f]));
  return {
    ...base,
    turn: snap.turn,
    phase: snap.phase,
    log: snap.log || base.log,
    vp: snap.vp || base.vp,
    tokens: snap.tokens || base.tokens,
    resolving: snap.resolving || null,
    beat: snap.beat || null,
    pending: null,
    // `over` belongs to the end of the turn, not to the middle of it: the
    // final view may be a finished game, but this snapshot is not it yet.
    over: snap.phase === 'finished',
    replaying: true,
    frames: (base.frames || []).map((frame) => {
      const live = byId.get(frame.id);
      if (!live) return frame;
      const merged = { ...frame, ...live };
      merged.committed = new Array(live.committedCount || 0).fill(null)
        .map(() => ({ faceDown: true, resolved: false }));
      merged.onField = new Array(live.onFieldCount || 0).fill(null)
        .map(() => ({ faceDown: false, resolved: true }));
      merged.hand = frame.hand;
      return merged;
    }),
  };
}

/** The tile the camera should be looking at for this beat. */
function beatFocus(snap, actor) {
  const beat = snap.beat || {};
  const hit = (beat.hits || [])[0] || (beat.dead || [])[0];
  if (hit) {
    const victim = (snap.frames || []).find((f) => f.id === hit.id);
    const at = (victim && victim.pos) || hit.pos;
    if (at) return at;
  }
  const move = (beat.moves || [])[0];
  if (move && move.to) return move.to;
  return actor && actor.pos ? actor.pos : null;
}

/** What the replay bar says, favouring the beat over the raw log tail. */
function beatText(snap) {
  const res = snap.resolving || {};
  const beat = snap.beat || {};
  if (res.key && beat.event === 'card') {
    return `${C.frameLabel(res.frameId, res.frameName)} plays ${C.displayName(res.key)}`;
  }
  const newest = (snap.log || [])[(snap.log || []).length - 1];
  if (newest && newest.text) return newest.text;
  return 'The AI is acting';
}

function cancelReplay() {
  if (app.replay.timer) clearTimeout(app.replay.timer);
  app.replay = { queue: [], timer: null, base: null, at: 0 };
  $('replay-bar').hidden = true;
  $('view-board').removeAttribute('data-replay');
}

function finishReplay() {
  const base = app.replay.base;
  cancelReplay();
  if (base) setView(base);
}

// ---------------------------------------------------------------- state

function setView(view, opts = {}) {
  app.view = view;
  // Rebuilt from every view. The engine already names each frame uniquely --
  // "Blue Kuwagata 2" -- so this only indexes those names, for abbreviating
  // them on a board marker and for picking them out of a log line.
  C.setRoster(view.frames || []);
  const replaying = !!opts.replaying;
  const pending = view.pending;
  const sig = pending
    ? `${pending.kind}:${pending.frameId}:${(pending.options || []).length}:${view.turn}:${view.log.length}`
    : `none:${view.turn}:${view.phase}`;
  if (!replaying && sig !== app.lastPendingSig) {
    app.lastPendingSig = sig;
    app.commitSelection = [];
    app.placeSelection = [];
    app.orderPick = [];
    // A proposed-but-unconfirmed tap belongs to the decision it was made
    // against. The decision has moved on, so the proposal is void.
    app.confirm = null;
    if (pending && !pending.waiting && pending.seat === view.seat) {
      if (pending.kind === 'commit_actions' || isMulligan(pending)) showView('plan');
      else if (['move', 'attack_target', 'choose_block', 'deploy',
        'choose_frame', ...TILE_DECISIONS].includes(pending.kind)) {
        showView('board');
      }
      $('sheet').dataset.open = '1';
    }
    if (pending && pending.kind === 'deploy') syncDeployChoice(pending);
    // Select the frame the decision is about so the board picks it out, but
    // do *not* open the readout panel: it would then be open almost always,
    // and the point of moving it off the board was to keep the board visible.
    if (pending && pending.frameId) app.selectedFrame = pending.frameId;
  }
  app.board.setView(view, view.seat);
  app.board.setActing(actingFrameId());
  app.board.setSelected(app.selectedFrame);
  // Marks belong to a replay beat; the live board is the state, not an event.
  // `playReplay` puts them back straight after this call.
  app.board.setBeat(null);
  renderHud();
  renderFrameStrip();
  renderFramePanel();
  renderActingCard();
  renderTableau();
  renderLog();
  renderPlan();
  renderLadder();
  renderObjectives();
  // A replay frame carries no `pending`, so the sheet falls through to its
  // "resolving" state. That matters: leaving the last decision on screen would
  // leave live buttons for a decision that has already been answered.
  renderSheet();
  if (replaying) app.board.setOverlays({});
  else refreshOverlays();
  syncSheetHeight();
  $('drawer-game-info').textContent =
    `Game ${view.gameId} · seat ${view.seat} · AI ${view.aiSource || 'unknown'}`;
}

function actingFrameId() {
  const view = app.view;
  // The server says outright which card is resolving and for whom, so this no
  // longer has to be inferred from which card happens to be face up.
  if (view.resolving && view.resolving.frameId) return view.resolving.frameId;
  const pending = view.pending;
  if (pending && pending.frameId
      && ['move', 'attack_target', 'resolve_order', 'effect_choice'].includes(pending.kind)) {
    return pending.frameId;
  }
  return null;
}

async function send(kind, payload) {
  await guard(async () => {
    playReplay(await api.sendCommand(app.gameId, kind, payload,
      { replayMine: app.replayMine }));
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
  if (app.view) renderActingCard();
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
      ? headlinePrompt(pending)
      : (pending ? 'The AI is deciding…' : 'Resolving…'));
}

/** The one-line "what now?" in the header, built from structure.
 *
 *  The engine's `prompt` is prose and names frames by model, which stops being
 *  an identity the moment two of them are on the table. So the headline is
 *  composed from the decision's own fields -- which frame, which kind -- and
 *  the engine's sentence is left to the sheet, where it reads as the
 *  explanation it is rather than as the label.
 */
function headlinePrompt(pending) {
  const id = pending.kind === 'deploy' ? app.deployFrame : pending.frameId;
  const frame = (app.view.frames || []).find((f) => f.id === id);
  if (!frame) return pending.prompt;
  return `${C.frameLabel(frame.id, frame.name)} · ${prettyKind(pending.kind)}`;
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
    chip.innerHTML = `<div class="fname">${C.escapeHtml(C.frameLabel(f.id, f.name))}</div>
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
  const same = app.selectedFrame === frameId && app.panelOpen;
  app.selectedFrame = same ? null : frameId;
  app.panelOpen = !same;
  app.board.setSelected(app.selectedFrame);
  renderFrameStrip();
  renderFramePanel();
  refreshOverlays();
  const frame = selectedFrame();
  if (frame && frame.pos) {
    if (app.currentView !== 'field') showView('board');
    app.board.centreOn(frame.pos.x, frame.pos.y);
  }
  $('tile-readout').hidden = true;
  syncSheetHeight();
}

function selectedFrame() {
  return (app.view.frames || []).find((f) => f.id === app.selectedFrame) || null;
}

/** The selected frame, in the ribbon above the board rather than over it.
 *
 *  Frame abilities are live rules and were invisible before this: Hector keeps
 *  its first block, Adam's pierce attacks go two initiative earlier, Kamikiri's
 *  first melee attack each turn hits harder. The frame card is one tap away and
 *  the ability text is on screen without one.
 */
function renderFramePanel() {
  const host = $('frame-panel');
  const frame = app.panelOpen ? selectedFrame() : null;
  $('screen-game').dataset.panel = frame ? '1' : '0';
  if (!frame) { host.hidden = true; host.innerHTML = ''; return; }
  const spec = C.frame(frame.name);
  const defence = (app.view.defence || {})[frame.id];
  host.hidden = false;
  host.innerHTML = '';

  const art = document.createElement('div');
  art.className = 'fp-art';
  const img = document.createElement('img');
  img.src = api.cardImageUrl(frame.name, 120);
  img.alt = frame.name;
  img.addEventListener('error', () => img.remove(), { once: true });
  img.addEventListener('click', () => C.showFrame(frame.name, frame));
  art.appendChild(img);
  const open = document.createElement('button');
  open.type = 'button';
  open.textContent = 'frame card';
  open.addEventListener('click', () => C.showFrame(frame.name, frame));
  art.appendChild(open);
  host.appendChild(art);

  const main = document.createElement('div');
  main.className = 'fp-main';
  const statuses = Object.entries(frame.statuses || {}).filter(([, n]) => n > 0)
    .map(([k, n]) => `${k} ${n}`).join(', ');
  main.innerHTML = `
    <div class="fp-name">${C.escapeHtml(C.frameLabel(frame.id, frame.name))}
      <small>${C.escapeHtml(frame.faction || '')} ·
        ${frame.seat === app.view.seat ? 'yours' : 'enemy'}${frame.alive ? '' : ' · destroyed'}</small>
    </div>
    ${spec && spec.ability
      ? `<div class="fp-ability">${C.escapeHtml(C.cleanText(spec.ability))}</div>` : ''}
    ${frame.cloaked ? `<div class="fp-ability">${C.escapeHtml(
      frame.seat === app.view.seat
        ? 'Hiding among its images. The enemy is shown three of them and is '
          + 'not told which one you are standing on.'
        : 'Hiding among its images. Its tile is not known — attack one of the '
          + 'images instead. Two of the three are decoys.')}</div>` : ''}
    <div class="zonebar">${ZONES.map((z) => {
      const armour = frame.armour[z] || 0;
      const dmg = frame.damage[z] || 0;
      const last = frame.lastHit && frame.lastHit[z];
      return `<span class="zb"${last ? ' data-last="1"' : ''}${
        armour && dmg >= armour - 1 ? ' data-open="1"' : ''}>
        <u>${z}${last ? ' · last hit' : ''}</u><b>${dmg}/${armour}</b></span>`;
    }).join('')}</div>
    <div class="fp-stats">
      <span>move <b>${frame.movement}</b></span>
      ${frame.shields ? `<span>shields <b>${frame.shields}</b></span>` : ''}
      <span>deck <b>${frame.deckCount}</b></span>
      <span>discard <b>${frame.discardCount}</b></span>
      ${defence ? `<span>blocks left <b>${defence.remaining}</b>${
        defence.faceDown ? ` (<em>${defence.faceDown} face down</em>)` : ''}</span>` : ''}
      ${statuses ? `<span>${C.escapeHtml(statuses)}</span>` : ''}
    </div>`;
  host.appendChild(main);

  const cards = cardRow(frame);
  if (cards) main.appendChild(cards);

  const close = document.createElement('button');
  close.className = 'icon-btn fp-close';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close');
  close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  close.addEventListener('click', () => selectFrame(frame.id));
  host.appendChild(close);
}

/** The cards standing in front of a frame, as a scrolling strip of art.
 *
 *  `aside` is the *persistence* pile -- a card that stays in play for its
 *  duration and neither resolves again nor blocks. An echo is something else
 *  entirely: a flag on a committed card, lent by a dead ally, which can only
 *  block. Tag them apart.
 */
function cardRow(frame, { showAside = true } = {}) {
  const rows = [];
  for (const card of frame.committed || []) {
    rows.push([card, card.echo ? 'echo — blocks only' : 'face down']);
  }
  for (const card of frame.onField || []) {
    rows.push([card, card.echo ? 'echo — blocks only' : 'on field']);
  }
  if (showAside) for (const card of frame.aside || []) rows.push([card, 'persisting']);
  if (!rows.length) return null;
  const strip = document.createElement('div');
  strip.className = 'cardrow';
  for (const [card, tag] of rows) {
    if (!card.key) {
      const back = C.faceDown(frame.seat === app.view.seat
        ? 'face down' : 'hidden');
      back.style.flex = "0 0 44px";
      back.style.width = "44px";
      strip.appendChild(back);
      continue;
    }
    const el = C.thumb(card.key, { width: 120, showName: false, tag });
    el.addEventListener('click', () => C.showCard(card.key));
    strip.appendChild(el);
  }
  return strip;
}

/** The corner card: which frame is acting, with what, at what initiative.
 *
 *  It floats over the bottom-left of the board, which is sometimes exactly
 *  where the tile you want to tap is. Tapping it folds it down to a stub, and
 *  tapping the stub brings it back -- the information stays reachable rather
 *  than being traded away for the tile underneath it.
 */
function renderActingCard() {
  const host = $('acting-card');
  const res = app.view.resolving;
  // With the frame panel open and a tall decision sheet the board can be
  // squeezed to a sliver; a floating card taller than the board it floats over
  // is worse than no card.
  const room = $('view-board').clientHeight;
  if (!res || room < 170) { host.hidden = true; host.innerHTML = ''; return; }
  host.hidden = false;
  host.dataset.mine = res.mine ? '1' : '0';
  host.dataset.collapsed = app.actingHidden ? '1' : '0';
  host.onclick = () => { app.actingHidden = !app.actingHidden; renderActingCard(); };
  if (app.actingHidden) {
    host.innerHTML = '';
    const stub = document.createElement('div');
    stub.className = 'ac-stub';
    stub.textContent = typeof res.initiative === 'number'
      ? String(res.initiative) : '\u25b8';
    stub.title = 'Show what is resolving';
    host.appendChild(stub);
    return;
  }
  // `step` is what is running; `steps` is what is still to come. Showing the
  // latter said "movement" while the card was asking where to put a gravity
  // well, which made the movement step that followed look like more of the
  // same question.
  const step = res.step || (res.steps || [])[0];
  const attack = res.attack;
  const bits = [];
  if (attack) {
    const zones = Object.entries(attack.zones || {}).map(([z, n]) => `${z} ${n}`);
    bits.push(`attacking · ${zones.join(' ') || 'no marks'}`);
    if (attack.pendingZones && attack.pendingZones.length) {
      bits.push(`${attack.pendingZones.join('/')} still open`);
    }
  } else if (step) {
    bits.push(step);
  }
  const toCome = (res.steps || []).filter((s) => s !== step);
  if (toCome.length) bits.push(`then ${toCome.join(', ')}`);
  if (res.reloading) bits.push('reloading — no effect or attack');
  host.innerHTML = '';
  if (res.key) {
    const img = document.createElement('img');
    img.src = api.cardImageUrl(res.key, 120);
    img.alt = C.displayName(res.key);
    img.addEventListener('error', () => img.remove(), { once: true });
    host.appendChild(img);
  }
  const text = document.createElement('div');
  text.className = 'ac-text';
  text.innerHTML = `<div class="ac-who">${res.mine ? 'your' : 'enemy'} frame acting</div>
    <b>${C.escapeHtml(C.frameLabel(res.frameId, res.frameName))}</b>
    <small>${C.escapeHtml(res.key ? C.displayName(res.key) : 'face down')}${
      bits.length ? ` · ${C.escapeHtml(bits.join(' · '))}` : ''}</small>`;
  host.appendChild(text);
  if (typeof res.initiative === 'number') {
    const init = document.createElement('div');
    init.className = 'ac-init';
    init.textContent = String(res.initiative);
    init.title = 'Initiative, as the engine is using it';
    host.appendChild(init);
  }
}

// ---------------------------------------------------------------- tableau

/** Every card standing in front of every frame -- what has been played.
 *
 *  Not a history: a resolved card stays on the field for the rest of the turn
 *  and can still be spent to block, which is exactly why you need to see it.
 */
function renderTableau() {
  const host = $('tableau');
  host.innerHTML = '';
  const view = app.view;
  const frames = [...(view.frames || [])].sort(
    (a, b) => (a.seat === view.seat ? 0 : 1) - (b.seat === view.seat ? 0 : 1));

  const note = document.createElement('p');
  note.className = 'hint';
  note.textContent = 'Everything still standing in front of each frame. A card '
    + 'that has already resolved stays on the field and can still be spent to '
    + 'block; a face-down card can do either.';
  host.appendChild(note);

  for (const frame of frames) {
    const box = document.createElement('div');
    box.className = 'tab-frame';
    box.dataset.seat = frame.seat === view.seat ? 'mine' : 'theirs';
    if (!frame.alive) box.dataset.dead = '1';
    const defence = (view.defence || {})[frame.id];

    const head = document.createElement('div');
    head.className = 'tab-head';
    head.innerHTML = `<b>${C.escapeHtml(C.frameLabel(frame.id, frame.name))}</b>
      <small>${ZONES.map((z) => `${z[0]} ${frame.damage[z]}/${frame.armour[z]}`).join(' · ')}</small>
      <span class="tab-counts">${(frame.committed || []).length} face down ·
        ${(frame.onField || []).length} on field<br>deck ${frame.deckCount} ·
        discard ${frame.discardCount}</span>`;
    head.addEventListener('click', () => selectFrame(frame.id));
    box.appendChild(head);

    if (defence) box.appendChild(blockSummary(defence, frame));

    // Four different things can be lying in front of a frame and only two of
    // them can block. The engine keeps them apart -- `aside` is persistence,
    // an echo is a flag on a committed card -- so this does too, because the
    // client used to call the persistence pile "Echoes of the fallen", which
    // is a different rule that does the opposite thing.
    const isEcho = (c) => !!c.echo;
    const groups = [
      ['Face down', (frame.committed || []).filter((c) => !isEcho(c)), ''],
      ['On the field — resolved, can still block',
        (frame.onField || []).filter((c) => !isEcho(c)), ''],
      ['Echoes of the fallen — can only block',
        [...(frame.committed || []), ...(frame.onField || [])].filter(isEcho),
        C.ECHO_HELP],
      ['Set aside — persisting', frame.aside || [], C.PERSISTENCE_HELP],
    ];
    let any = false;
    for (const [label, cards, help] of groups) {
      if (!cards.length) continue;
      any = true;
      const group = document.createElement('div');
      group.className = 'tab-group';
      const h = document.createElement('h4');
      h.textContent = `${label} (${cards.length})`;
      if (help) h.title = help;
      group.appendChild(h);
      if (help) {
        const explain = document.createElement('p');
        explain.className = 'tab-help';
        explain.textContent = help;
        group.appendChild(explain);
      }
      const strip = document.createElement('div');
      strip.className = 'cardrow';
      for (const card of cards) {
        if (!card.key) {
          const back = C.faceDown(frame.seat === view.seat
            ? 'face down — the AI cannot see it' : 'hidden');
          back.style.flex = "0 0 44px";
          back.style.width = "44px";
          strip.appendChild(back);
          continue;
        }
        const el = C.thumb(card.key, { width: 120, showName: false });
        el.addEventListener('click', () => C.showCard(card.key));
        strip.appendChild(el);
      }
      group.appendChild(strip);
      box.appendChild(group);
    }
    if (!any) {
      const empty = document.createElement('p');
      empty.className = 'tab-empty';
      empty.textContent = frame.alive
        ? 'Nothing in front of this frame.' : 'Destroyed.';
      box.appendChild(empty);
    }
    host.appendChild(box);
  }
}

/** Per-zone block cover, straight from the server's `defence` readout. */
function blockSummary(defence, frame) {
  const bar = document.createElement('div');
  bar.className = 'zonebar';
  for (const zone of ZONES) {
    const info = (defence.zones || {})[zone] || { cards: 0, super: 0 };
    const cell = document.createElement('span');
    cell.className = 'zb';
    if (!info.cards && !defence.faceDown) cell.dataset.open = '1';
    cell.innerHTML = `<u>${zone} block</u><b>${info.cards}${
      info.super ? `<i title="super block — kept, not discarded"> ★${info.super}</i>` : ''}</b>`;
    cell.title = info.cards
      ? `${info.cards} known card(s) block ${zone}`
      : `nothing this seat can see blocks ${zone}`;
    bar.appendChild(cell);
  }
  if (defence.faceDown) {
    const cell = document.createElement('span');
    cell.className = 'zb';
    cell.innerHTML = `<u>face down</u><b>${defence.faceDown}</b>`;
    cell.title = 'Any of these might block any zone';
    bar.appendChild(cell);
  }
  if (defence.keepsNextBlock) {
    bar.title = `${C.frameLabel(frame.id, frame.name)} keeps its next block`;
  }
  return bar;
}

// ---------------------------------------------------------------- confirm a tap
//
// The author's note, after playing on a real phone: "movement needs
// confirmation -- misclicks are too easy". They are, and a move cannot be
// taken back: the frame is somewhere else now and the card that carried it has
// resolved. So every board tap that spends something irreversible is two taps
// -- the first proposes a destination, target or deployment and shows it, the
// second, on the same thing, commits it.
//
// Taps that only *look* at something (selecting a frame, reading a tile) are
// still one tap: making those cost two would be the same disease.

function proposalKey(kind, payload) {
  if (kind === 'move') return `move:${payload.x},${payload.y}`;
  if (kind === 'deploy') return `deploy:${payload.frame}:${payload.x},${payload.y}`;
  if (kind === 'attack_target') return `attack:${payload.kind}:${payload.id}`;
  return `${kind}:${JSON.stringify(payload)}`;
}

// ------------------------------------------------------ effect_choice shapes
//
// One decision kind carries four different questions. Which one it is can be
// read straight off the options, and the board only wants two of them: a list
// of tiles to stand on, and a list of things to shoot at.

/** The tiles an `effect_choice` is offering, ignoring anything that is not one.
 *
 *  Barricade offers `{done: true}` alongside its tiles to stop early, and this
 *  used to demand that *every* option be a tile -- so one extra option turned
 *  the whole decision into a list of raw grid coordinates with no board
 *  overlay and no way to tap it out. Non-tile options are simply not tiles.
 */
/** The decision kinds whose answer is a tile, tapped on the board.
 *
 *  `effect_choice` is card text, `place_objective` is a token an objective
 *  hands you at setup, `move_token` is one of those tokens taking its move.
 *  All three are answered the same way, so they are listed once.
 */
const TILE_DECISIONS = ['effect_choice', 'place_objective', 'move_token'];

function effectTileOptions(pending) {
  if (!pending || !TILE_DECISIONS.includes(pending.kind)) {
    return [];
  }
  const tiles = (pending.options || []).filter(
    (o) => 'x' in o && 'y' in o && !('frame' in o));
  // A frame-and-tile option would be ambiguous -- is it a frame or a tile? --
  // so those are answered as a list. Nothing emits them any more.
  return (pending.options || []).some((o) => 'frame' in o && 'x' in o) ? [] : tiles;
}

/** Is this tile decision "where do I go" or "where does this go"?
 *
 *  The engine says (`pickKind`), because the two read completely differently
 *  and are coloured apart: movement green for sending something already on
 *  the board somewhere, placement orange for putting something new down.
 */
function isPlacement(pending) {
  return String((pending && pending.pickKind) || 'place') === 'place';
}

/** The "stop here" option, for effects that place *up to* so many. */
function effectDoneOption(pending) {
  if (!pending || pending.kind !== 'effect_choice') return null;
  return (pending.options || []).find((o) => o.done) || null;
}

/** How many tiles this effect still wants, as `{min, max}`.
 *
 *  The engine says so on the decision because it asks for one at a time: an
 *  effect that places three barricades would otherwise be three separate
 *  confirmations. With the range known, the player marks all three and
 *  commits once.
 */
function placeLimits(pending) {
  const offered = effectTileOptions(pending).length;
  const asked = Number(pending && pending.pickMax);
  const max = Math.max(1, Math.min(
    Number.isFinite(asked) && asked > 0 ? asked : 1, offered || 1));
  const floor = Number(pending && pending.pickMin);
  const min = Math.max(0, Math.min(Number.isFinite(floor) ? floor : max, max));
  return { min, max };
}

/** Mark or unmark a tile for a multi-placement effect. True if it changed. */
function togglePlace(x, y) {
  const pending = app.view.pending;
  const at = app.placeSelection.findIndex((p) => p.x === x && p.y === y);
  if (at >= 0) {
    app.placeSelection.splice(at, 1);
  } else {
    if (!effectTileOptions(pending).some((o) => o.x === x && o.y === y)) return false;
    const { max } = placeLimits(pending);
    // Full: the oldest mark makes way, so a tap always does something.
    while (app.placeSelection.length >= max) app.placeSelection.shift();
    app.placeSelection.push({ x, y });
  }
  refreshOverlays();
  renderSheet();
  return true;
}

/** What to call putting something on a tile, from the decision's own prompt. */
function placeLabel(pending, x, y) {
  const prompt = String((pending && pending.prompt) || '');
  // The bit before the colon is the card's own name for what it is doing.
  // Without one, do not paste a whole sentence in front of a coordinate.
  const head = prompt.includes(':') ? prompt.split(':')[0].trim() : '';
  const what = head.length <= 22 ? head : '';
  // A move says where, and the sheet's own title and prompt already say who
  // and why -- "Set the trap to (13, 11)" reads like nonsense.
  if (!isPlacement(pending)) return `Move to (${x}, ${y})`;
  return `${what || 'Place'} at (${x}, ${y})`;
}

/** Send every marked tile, then close the effect out if it will let us.
 *
 *  The engine asks for one tile at a time, so this walks the exchange: send a
 *  tile, read the decision that comes back, send the next. A tile it stops
 *  offering ends the run rather than being forced through -- the engine's
 *  answer is the authority on what is legal, never this list.
 */
async function commitPlacements() {
  const queue = app.placeSelection.slice();
  if (!queue.length) return;
  await guard(async () => {
    let view = app.view;
    for (const spot of queue) {
      const option = effectTileOptions(view.pending)
        .find((o) => o.x === spot.x && o.y === spot.y);
      if (!option) break;
      view = await api.sendCommand(app.gameId, view.pending.kind, { ...option },
        { replayMine: app.replayMine });
    }
    // "Up to three" and the player laid out two: say so rather than leaving
    // them staring at a decision they have already answered.
    const done = effectDoneOption(view.pending);
    if (done && view.pending.frameId === (app.view.pending || {}).frameId) {
      view = await api.sendCommand(app.gameId, 'effect_choice', { ...done },
        { replayMine: app.replayMine });
    }
    app.placeSelection = [];
    playReplay(view);
  });
}

function effectTargetOptions(pending) {
  if (!pending || pending.kind !== 'effect_choice') return [];
  const options = pending.options || [];
  const targets = options.every(
    (o) => ('frame' in o && !('x' in o)) || 'token' in o);
  return options.length && targets ? options : [];
}

/** Propose it, or -- if this exact thing is already proposed -- do it. */
function tapToConfirm(kind, payload, { label, detail }) {
  const key = proposalKey(kind, payload);
  if (app.confirm && app.confirm.key === key) {
    const proposal = app.confirm;
    app.confirm = null;
    send(proposal.kind, proposal.payload);
    return;
  }
  app.confirm = { kind, key, payload, label, detail };
  app.board.setOverlays({ ...app.board.overlays, confirm: confirmTile() });
  renderSheet();
  refreshOverlays();
  toast(`${label} — tap again to confirm`);
}

/** Where on the board the pending proposal is, for the board's own marker. */
function confirmTile() {
  const proposal = app.confirm;
  if (!proposal) return null;
  if (typeof proposal.payload.x === 'number') {
    return { x: proposal.payload.x, y: proposal.payload.y };
  }
  const id = proposal.payload.id || proposal.payload.frame || proposal.payload.token;
  const frame = (app.view.frames || []).find((f) => f.id === id);
  if (frame && frame.pos) return { x: frame.pos.x, y: frame.pos.y };
  const token = (app.view.tokens || []).find((t) => t.id === id);
  if (token && token.pos) return { x: token.pos.x, y: token.pos.y };
  return null;
}

function commitProposal() {
  const proposal = app.confirm;
  if (!proposal) return;
  app.confirm = null;
  send(proposal.kind, proposal.payload);
}

function cancelProposal() {
  app.confirm = null;
  renderSheet();
  refreshOverlays();
}

// ---------------------------------------------------------------- deployment
//
// The engine offers every legal (frame, tile) pair in one decision, so the
// player is choosing two things at once: which frame to put down and where.
// The sheet holds the first choice and the board the second.

/** The frames still waiting to be deployed, in the engine's own option list. */
function deployableFrames(pending) {
  const seen = [];
  for (const option of pending.options || []) {
    if (option.frame && !seen.some((f) => f.id === option.frame)) {
      seen.push({ id: option.frame, name: option.name || option.frame });
    }
  }
  return seen;
}

/** Keep the chosen frame on a frame the engine will still accept. */
function syncDeployChoice(pending) {
  const frames = deployableFrames(pending);
  if (!frames.length) { app.deployFrame = null; return; }
  if (!frames.some((f) => f.id === app.deployFrame)) {
    app.deployFrame = frames[0].id;
  }
  app.selectedFrame = app.deployFrame;
}

function setDeployFrame(frameId) {
  app.deployFrame = frameId;
  app.selectedFrame = frameId;
  app.confirm = null;
  app.board.setSelected(frameId);
  renderFrameStrip();
  renderSheet();
  refreshOverlays();
}

/** The deploy options for the frame currently chosen. */
function deployOptions(pending) {
  return (pending.options || []).filter((o) => o.frame === app.deployFrame);
}

// ---------------------------------------------------------------- board glue

function onTapFrame(frame) {
  const pending = app.view.pending;
  if (pending && !pending.waiting && pending.seat === app.view.seat) {
    if (pending.kind === 'attack_target') {
      const option = (pending.options || []).find((o) => o.id === frame.id);
      if (option) {
        tapToConfirm('attack_target', { kind: option.kind, id: option.id }, {
          label: `Attack ${C.frameLabel(frame.id, frame.name)}`,
          detail: attackDetail(option),
        });
        return;
      }
    }
    if (pending.kind === 'move') {
      // Tapping an occupied tile during a move is a selection, not a move.
    }
    const option = effectTargetOptions(pending).find((o) => o.frame === frame.id);
    if (option) {
      // "Target" is wrong for a shove, where the frame being picked is
      // usually an ally you are about to reposition. The engine says which
      // kind of question it is asking.
      const verb = pending.pickKind === 'frame' ? 'Choose' : 'Target';
      tapToConfirm(pending.kind, { ...option }, {
        label: `${verb} ${C.frameLabel(frame.id, frame.name)}`,
        detail: pending.prompt || 'card effect',
      });
      return;
    }
  }
  selectFrame(frame.id);
}

// The engine's token `kind` is a bare slug. Only the ones that do not read as
// English on their own need a word here.
const { tokenWord, tokenLabel } = C;

function attackDetail(option) {
  const zones = Object.entries(option.zones || {}).map(([z, n]) => `${z} ${n}`);
  const defence = (app.view.defence || {})[option.id];
  const bits = [zones.join(' · ') || 'no marks'];
  if (defence) {
    bits.push(`${defence.remaining} card${defence.remaining === 1 ? '' : 's'} left`);
  }
  return bits.join(' · ');
}

function onTapTile(x, y) {
  const pending = app.view.pending;
  if (pending && !pending.waiting && pending.seat === app.view.seat) {
    if (pending.kind === 'move') {
      const option = (pending.options || []).find((o) => o.x === x && o.y === y);
      if (option) {
        tapToConfirm('move', { x, y, cost: option.cost }, {
          label: option.cost === 0 ? 'Stay put' : `Move to (${x}, ${y})`,
          detail: option.cost === 0
            ? 'Spend no movement'
            : `${option.cost} movement · elevation ${
              (app.board.tiles.get(`${x},${y}`) || {}).elev || 0}`,
        });
        return;
      }
      toast('That tile is out of range');
      return;
    }
    if (pending.kind === 'deploy') {
      const option = deployOptions(pending).find((o) => o.x === x && o.y === y);
      if (option) {
        const name = C.frameLabel(option.frame, option.name || option.frame);
        tapToConfirm('deploy', { ...option }, {
          label: `Deploy ${name} at (${x}, ${y})`,
          detail: 'Where it starts the battle',
        });
        return;
      }
      toast('Not a deployment tile for this frame');
      return;
    }
    if (pending.kind === 'attack_target') {
      const token = (app.view.tokens || []).find(
        (t) => t.pos && t.pos.x === x && t.pos.y === y && t.alive !== false);
      if (token) {
        const option = (pending.options || []).find((o) => o.id === token.id);
        if (option) {
          tapToConfirm('attack_target', { kind: option.kind, id: option.id }, {
            label: `Attack ${tokenLabel(token)}`,
            detail: attackDetail(option),
          });
          return;
        }
      }
    }
    const tiles = effectTileOptions(pending);
    if (tiles.length) {
      const { max } = placeLimits(pending);
      const placing = isPlacement(pending);
      const option = tiles.find((o) => o.x === x && o.y === y);
      if (!option) {
        // Tapping a marked tile again takes it back off the list.
        if (placing && togglePlace(x, y)) return;
        toast('That tile is not one of the options');
        return;
      }
      // One answer keeps the tap-twice confirm that movement uses. Several
      // means the player is laying out a set: mark them all on the board
      // first, and commit the lot with one button.
      if (placing && max > 1) { togglePlace(x, y); return; }
      tapToConfirm(pending.kind, { ...option }, {
        label: placeLabel(pending, x, y),
        detail: pending.prompt || 'card effect',
      });
      return;
    }
    const targets = effectTargetOptions(pending);
    if (targets.length) {
      const token = (app.view.tokens || []).find(
        (t) => t.pos && t.pos.x === x && t.pos.y === y && t.alive !== false);
      const option = token && targets.find((o) => o.token === token.id);
      if (option) {
        tapToConfirm('effect_choice', { token: option.token }, {
          label: `Attack ${tokenLabel(token)}`,
          detail: pending.prompt || 'card effect',
        });
        return;
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
    ${tile.hazard ? `<br><b class="hazard">${C.escapeHtml(tile.hazard)}</b>` : ''}
    ${aurasOver(x, y).map((a) => `<br><b class="hazard">inside the ${
      C.escapeHtml(a.name)}</b> — ${C.escapeHtml(a.text)}`).join('')}
    ${token ? `<br>${C.escapeHtml(tokenReadout(token))}` : ''}`;
}

/** What a tile is standing inside, in the engine's own words.
 *
 *  A gravity well or a storm reaches five tiles in every direction and the
 *  token itself may be off the edge of the phone's view, so the tile has to be
 *  able to say what is acting on it. The radius and the wording are the
 *  engine's (`token.aura`); this only measures the square.
 */
function aurasOver(x, y) {
  return (app.view.tokens || [])
    .filter((t) => t.pos && t.alive !== false && t.aura
      && Math.max(Math.abs(x - t.pos.x), Math.abs(y - t.pos.y))
         <= Number(t.aura.radius || 0))
    .map((t) => t.aura);
}

/** One line about a token under the finger. */
function tokenReadout(token) {
  if (token.kind === 'image') {
    const owner = token.frame ? C.frameLabel(token.frame) : 'a frame';
    return token.real
      ? `${owner} is really standing here — the other images are decoys`
      : `an image of ${owner}. One of the three is the frame; the fakes `
        + 'vanish when struck';
  }
  if (token.kind === 'drone') {
    const owner = token.frame ? C.frameLabel(token.frame) : 'a frame';
    return `${owner}'s drone · ${token.hp}/${token.maxHp}`;
  }
  return `token: ${token.kind} ${token.hp}/${token.maxHp}`;
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
  const overlays = {
    reach: new Map(), beyond: new Map(), los: new Set(), targets: new Set(),
    deploy: new Set(), place: new Set(), picked: [], confirm: confirmTile(),
  };
  const mine = pending && !pending.waiting && pending.seat === view.seat;

  if (mine && pending.kind === 'move') {
    for (const o of pending.options) overlays.reach.set(`${o.x},${o.y}`, o.cost);
    // Tiles the engine priced but did not offer. Drawn so a climb the frame
    // cannot afford says what it would have cost instead of simply not being
    // there -- an elevation-2 tile is 3 to step into, which is a whole rule a
    // player should not have to infer from an absence.
    for (const o of ((pending.context || {}).outOfReach) || []) {
      overlays.beyond.set(`${o.x},${o.y}`, o.cost);
    }
  } else if (mine && pending.kind === 'deploy') {
    for (const o of deployOptions(pending)) overlays.deploy.add(`${o.x},${o.y}`);
  } else if (mine && pending.kind === 'attack_target') {
    for (const o of pending.options) {
      const frame = (view.frames || []).find((f) => f.id === o.id);
      const token = (view.tokens || []).find((t) => t.id === o.id);
      const pos = (frame && frame.pos) || (token && token.pos);
      if (pos) overlays.targets.add(`${pos.x},${pos.y}`);
    }
  } else if (mine && TILE_DECISIONS.includes(pending.kind)) {
    // Two kinds of tile question, coloured apart. Green for sending something
    // that is already on the board somewhere -- a drone's move, a reflex step,
    // a Teleport, a shove. Orange for putting something new down.
    const placing = isPlacement(pending);
    if (!placing) app.placeSelection = [];
    const picked = new Set(app.placeSelection.map((p) => `${p.x},${p.y}`));
    for (const o of effectTileOptions(pending)) {
      const key = `${o.x},${o.y}`;
      if (!placing) {
        // A decision that does not price its tiles gets no numbers on them.
        const cost = Number(o.cost);
        overlays.reach.set(key, Number.isFinite(cost) ? cost : null);
      }
      else if (!picked.has(key)) overlays.place.add(key);
    }
    overlays.picked = placing ? app.placeSelection.slice() : [];
    for (const o of effectTargetOptions(pending)) {
      const frame = (view.frames || []).find((f) => f.id === o.frame);
      const token = (view.tokens || []).find((t) => t.id === o.token);
      const pos = (frame && frame.pos) || (token && token.pos);
      if (pos) overlays.targets.add(`${pos.x},${pos.y}`);
    }
  }

  // A destination that has been proposed but not committed: show what the
  // frame would *see* from there. That is the whole question being asked at
  // that moment -- half of movement is where you can shoot from next -- and
  // it needs no toggle, because it only appears in answer to a deliberate
  // tap and disappears with it.
  const preview = movePreview();
  const selected = (view.frames || []).find((f) => f.id === app.selectedFrame);
  // A frame hiding behind its images has no position to draw an overlay from,
  // and the server refuses to invent one -- so do not ask.
  const readable = selected && selected.alive && selected.pos && !selected.cloaked;
  const wantThreat = readable && selected.seat !== view.seat && app.options.threat;
  const source = preview || ((app.options.los || wantThreat) && readable
    ? { frame: selected.id, at: null } : null);
  if (source) {
    const at = source.at;
    const key = `${view.gameId}:${source.frame}:${view.log.length}`
      + (at ? `:${at.x},${at.y}` : '');
    let data = app.threatCache.get(key);
    if (!data) {
      try {
        data = await api.getThreat(app.gameId, source.frame, at);
        app.threatCache.set(key, data);
        if (app.threatCache.size > 24) {
          app.threatCache.delete(app.threatCache.keys().next().value);
        }
      } catch { data = null; }
    }
    if (data) {
      if (preview || app.options.los) {
        for (const [x, y] of data.los) overlays.los.add(`${x},${y}`);
      }
      if (!preview && wantThreat && !overlays.reach.size) {
        for (const [x, y, cost] of data.reach) overlays.reach.set(`${x},${y}`, cost);
      }
    }
  }
  overlays.losFrom = preview ? preview.at : null;
  app.board.setOverlays(overlays);
  renderLegend(overlays, mine ? pending : null);
}

/** The move being proposed right now, as `{frame, at}`, or null.
 *
 *  Only a real destination counts: a proposal on the frame's own tile ("stay
 *  put") would draw the sight lines it already has, and a placement is not a
 *  question about what anything can see.
 */
function movePreview() {
  const proposal = app.confirm;
  const pending = app.view.pending;
  if (!proposal || !pending || pending.waiting) return null;
  // A frame's own move only: a gang being shuffled a tile does not shoot.
  if (proposal.kind !== 'move') return null;
  const at = confirmTile();
  const frame = (app.view.frames || []).find((f) => f.id === pending.frameId);
  if (!at || !frame || !frame.pos) return null;
  if (at.x === frame.pos.x && at.y === frame.pos.y) return null;
  return { frame: frame.id, at };
}

function renderLegend(overlays, pending) {
  const host = $('board-legend');
  const bits = [];
  if (app.confirm) bits.push('gold ring = tap again to confirm');
  if (overlays.deploy && overlays.deploy.size) {
    bits.push('blue = where this frame may deploy');
  }
  if (overlays.reach.size) {
    const choosing = pending
      && ['move', 'effect_choice'].includes(pending.kind);
    bits.push(choosing
      ? 'green = legal destination · tap twice'
      : 'green = selected frame\'s reach (base movement)');
  }
  if (overlays.beyond && overlays.beyond.size) {
    const budget = (pending && (pending.context || {}).budget);
    bits.push(budget === undefined
      ? 'grey = costs more than you have'
      : `grey = costs more than the ${budget} you have`);
  }
  // Why a step might cost more than the terrain says. Named whenever the frame
  // being asked about is standing inside a ring, not only when it bites.
  if (pending && pending.kind === 'move') {
    const acting = (app.view.frames || []).find((f) => f.id === pending.frameId);
    if (acting && acting.pos) {
      for (const aura of aurasOver(acting.pos.x, acting.pos.y)) {
        bits.push(`in the ${aura.name} — ${aura.text}`);
      }
    }
  }
  if (overlays.place && overlays.place.size) {
    const many = pending && Number(pending.pickMax) > 1;
    bits.push(many
      ? 'orange = where it can go · tap to mark, then commit'
      : 'orange = where it can go · tap twice');
  }
  if (overlays.targets.size) bits.push('red pulse = legal target · tap twice');
  if (overlays.los.size) {
    bits.push(overlays.losFrom
      ? 'red wash = what you would see from the proposed tile'
      : 'red wash = line of sight');
  }
  if (!bits.length) bits.push('pinch to zoom · double tap = fit');
  host.innerHTML = bits.map((b) => `<span>${C.escapeHtml(b)}</span>`).join('');
}

// ---------------------------------------------------------------- plan view

/** Kuwagata's mulligan: an `effect_choice` whose options are keep-or-redraw.
 *
 *  Recognised by its option shape, like every other effect the sheet has to
 *  tell apart -- there is no decision kind of its own to check.
 */
function isMulligan(pending) {
  const options = (pending && pending.options) || [];
  return pending && pending.kind === 'effect_choice' && options.length > 0
    && options.every((o) => 'mulligan' in o);
}

function renderPlan() {
  const view = app.view;
  const pending = view.pending;
  const mine = pending && !pending.waiting && pending.seat === view.seat;
  const committing = mine && pending.kind === 'commit_actions';
  // A mulligan is answered against the hand, so the Plan tab shows that hand
  // rather than the field -- the same seven cards the sheet is asking about.
  const mulliganing = mine && isMulligan(pending);
  const frame = (view.frames || []).find(
    (f) => f.id === (committing || mulliganing ? pending.frameId : app.selectedFrame))
    || (view.frames || []).find((f) => f.seat === view.seat && f.alive);

  $('plan-frame').textContent = frame ? C.frameLabel(frame.id, frame.name) : '';
  // How many slots there are is the engine's answer, not a constant: Hyper
  // ("next turn: play 1 extra action") makes it three, so the row is rebuilt
  // to the size the pending decision asks for.
  const limits = commitLimits(committing ? pending : null);
  const slots = layoutPlanSlots(limits.max);

  const hand = $('hand-grid');
  hand.innerHTML = '';

  if (committing) {
    const short = limits.max - app.commitSelection.length;
    $('plan-count').textContent =
      `${frame ? frame.hand.length : 0} in hand · commit ${
        limits.min === limits.max ? limits.max : `${limits.min}-${limits.max}`
      } face down`;
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
        dim: !selected && short <= 0,
        onTap: () => toggleCommit(option.uid),
      });
      attachLongPress(el, option.key);
      hand.appendChild(el);
    }
    return;
  }

  if (mulliganing) {
    const cards = (frame && frame.hand) || [];
    $('plan-count').textContent =
      `${cards.length} in hand · keep them or draw ${cards.length} new ones`;
    for (const card of cards) {
      const el = C.thumb(card.key, { width: 240 });
      attachLongPress(el, card.key);
      el.addEventListener('click', () => C.showCard(card.key));
      hand.appendChild(el);
    }
    return;
  }

  // Not planning: show what is on the field for the chosen frame.
  const rows = [];
  if (frame) {
    for (const card of frame.committed || []) {
      rows.push([card, card.echo ? 'echo — blocks only' : 'committed']);
    }
    for (const card of frame.onField || []) {
      rows.push([card, card.echo ? 'echo — blocks only' : 'resolved']);
    }
    // Persisting, not echoing: set aside for its duration, and while it is
    // there it neither resolves again nor blocks.
    for (const card of frame.aside || []) rows.push([card, 'persisting']);
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
    const el = C.thumb(card.key, { width: 240, tag, dim: tag === 'persisting' });
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
    const { max } = commitLimits(app.view.pending);
    // Full: the oldest pick makes way, so a tap always does something.
    while (app.commitSelection.length >= max) app.commitSelection.shift();
    app.commitSelection.push(uid);
  }
  renderPlan();
  renderSheet();
}

/** How many cards this commit takes, as `{min, max}`.
 *
 *  The engine says so on the decision (`pickMin`/`pickMax`) because the answer
 *  is not a constant -- Hyper raises the ceiling to three, and a client that
 *  assumed two made the card's whole effect unspendable. Both ends are clamped
 *  to the number of cards actually offered, for the frame down to its last one.
 */
function commitLimits(pending) {
  const offered = pending ? (pending.options || []).length : 0;
  const asked = Number(pending && pending.pickMax);
  const max = Math.max(1, Math.min(Number.isFinite(asked) && asked > 0 ? asked : 2,
    offered || 2));
  const floor = Number(pending && pending.pickMin);
  const min = Math.max(1, Math.min(Number.isFinite(floor) && floor > 0 ? floor : max, max));
  return { min, max };
}

/** Rebuild the action-slot row to `count` slots and return them. */
function layoutPlanSlots(count) {
  const row = document.querySelector('.plan-slots');
  row.style.setProperty('--slots', String(count));
  while (row.children.length > count) row.lastElementChild.remove();
  while (row.children.length < count) {
    const slot = document.createElement('div');
    slot.className = 'slot';
    row.appendChild(slot);
  }
  return Array.from(row.children).map((slot, i) => {
    slot.dataset.slot = String(i);
    slot.innerHTML = `<span class="slot-label">Action ${i + 1}</span>`;
    delete slot.dataset.filled;
    return slot;
  });
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
  // The engine's own numbers, for every card whose identity this seat has:
  // printed initiative after Stunned/Stimmed, a High zone on its last hit and
  // frame abilities. This is the order the queue will actually run in, not the
  // one on the card faces.
  const effective = view.initiative || {};
  const initOf = (entry) => {
    if (Object.prototype.hasOwnProperty.call(effective, entry.card.uid)) {
      return effective[entry.card.uid];
    }
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
  head.textContent = `Turn ${view.turn} — initiative order`;
  host.appendChild(head);

  const note = document.createElement('p');
  note.className = 'hint';
  note.textContent = 'Highest initiative resolves first; ties alternate from the '
    + 'priority marker. The numbers are the engine\'s own — printed value after '
    + '−1 for a High zone on its last hit, ∓2 for Stunned/Stimmed and any frame '
    + 'ability. A card you cannot see shows "?".';
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
    const resolving = view.resolving;
    const isNow = resolving && resolving.uid
      ? entry.card.uid === resolving.uid
      : entry.state === 'pending' && entry.frame.id === acting;
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
    const tags = [C.frameLabel(entry.frame.id, entry.frame.name)];
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

/** The objective cards on this battlefield, in their own printed words.
 *
 *  Objectives are about half the victory points on offer and every one of
 *  them is a card sitting somewhere on the table -- which the player cannot
 *  pick up and read. So this is the card: who brought it (and therefore who
 *  defends it), what it is worth to each side, what it says, how it stands
 *  right now, and a button that goes and finds it on the map.
 */
function renderObjectives() {
  const host = $('goals');
  host.innerHTML = '';
  const view = app.view;
  const objectives = ((view.board || {}).objectives) || [];
  if (!objectives.length) {
    const empty = document.createElement('p');
    empty.className = 'hint';
    empty.textContent = 'No objectives on this battlefield.';
    host.appendChild(empty);
    return;
  }

  const note = document.createElement('p');
  note.className = 'hint';
  note.textContent = 'Whoever brought a card defends it and scores the first '
    + 'number; the other side scores the second. Tap "Find it" to centre the '
    + 'board on the card.';
  host.appendChild(note);

  for (const obj of objectives) {
    const mine = obj.owner === view.seat;
    const box = document.createElement('div');
    box.className = 'goal';
    box.dataset.mine = mine ? '1' : '0';

    const head = document.createElement('div');
    head.className = 'goal-head';
    const name = document.createElement('b');
    name.textContent = obj.name;
    const who = document.createElement('small');
    who.textContent = mine ? 'yours to defend' : 'theirs to defend';
    const points = document.createElement('span');
    points.className = 'goal-points';
    points.textContent = mine
      ? `${obj.defend} to you · ${obj.attack} to them`
      : `${obj.attack} to you · ${obj.defend} to them`;
    head.append(name, who, points);
    box.appendChild(head);

    const status = document.createElement('p');
    status.className = 'goal-status';
    status.dataset.settled = obj.settled ? '1' : '0';
    status.textContent = objectiveStatusText(obj);
    box.appendChild(status);

    if (obj.text) {
      const text = document.createElement('p');
      text.className = 'goal-text';
      text.textContent = C.cleanText(obj.text);
      box.appendChild(text);
    }

    const tokens = (view.tokens || []).filter((t) => t.objective === obj.name);
    if (tokens.length) {
      const line = document.createElement('p');
      line.className = 'goal-tokens';
      line.textContent = objectiveTokenText(tokens, obj);
      box.appendChild(line);
    }

    // A running count the objective keeps: the Solar Farm's charge. Nobody
    // can read it off the board, so it goes beside the card that spends it.
    const tally = objectiveTallyText(obj);
    if (tally) {
      const line = document.createElement('p');
      line.className = 'goal-tokens';
      line.textContent = tally;
      box.appendChild(line);
    }

    const spot = objectiveSpot(obj);
    if (spot) {
      const actions = document.createElement('div');
      actions.className = 'goal-actions';
      const go = document.createElement('button');
      go.className = 'btn ghost sm';
      go.textContent = 'Find it on the board';
      go.addEventListener('click', () => {
        showView('board');
        if (app.board) app.board.centreOn(spot.x, spot.y, app.board.tacticalZoom());
      });
      actions.appendChild(go);
      box.appendChild(actions);
    }
    host.appendChild(box);
  }
}

/** "Nobody has it yet" beats "unscored" when the word has to carry the rule. */
function objectiveStatusText(obj) {
  const view = app.view;
  const side = (seat) => (seat === view.seat ? 'you' : 'the enemy');
  if (obj.settled) return `Scored by ${side(obj.scorer)} — locked in`;
  if (obj.scorer === null || obj.scorer === undefined) {
    return 'Nobody is scoring this yet';
  }
  return `As it stands: ${side(obj.scorer)} score${obj.scorer === view.seat ? '' : 's'
    } ${obj.value}`;
}

/** The objective's running count, from the reader's side. */
function objectiveTallyText(obj) {
  if (!obj.tally) return '';
  const mine = Number(obj.tally[String(app.view.seat)] || 0);
  const theirs = Object.entries(obj.tally)
    .filter(([seat]) => Number(seat) !== app.view.seat)
    .reduce((sum, [, n]) => sum + Number(n || 0), 0);
  const label = obj.tallyLabel || 'progress';
  return `${label}: you ${mine} · the enemy ${theirs}`;
}

/** How the objective's tokens are doing, in one line.
 *
 *  A set reads as a count ("2 of 4 reactors left"); a single token reads as
 *  its own state, because "1 of 1 tower left" says nothing a player wanted.
 */
function objectiveTokenText(tokens, obj = {}) {
  const alive = tokens.filter((t) => t.alive !== false);
  const kind = tokenWord(tokens[0].kind);
  const held = alive.find((t) => t.carrier);
  const parts = [];
  // A token that is gone because its own side scored was not destroyed --
  // it got out. Only extraction settles an objective *for the side that
  // brought it* and takes the token off the board, so the two cannot be
  // confused with a reactor being blown up.
  const away = obj.settled && obj.scorer === obj.owner && !alive.length;
  if (tokens.length === 1) {
    const one = tokens[0];
    if (away) parts.push(`the ${kind} got away`);
    else if (one.alive === false) parts.push(`the ${kind} is destroyed`);
    else if (!one.pos) parts.push(`the ${kind} is not on the board yet`);
    else if (one.maxHp > 1) parts.push(`the ${kind} is on ${one.hp} of ${one.maxHp} hit points`);
    else parts.push(`the ${kind} is on the board`);
  } else if (away) {
    parts.push(`all ${tokens.length} ${kind}s got away`);
  } else {
    parts.push(`${alive.length} of ${tokens.length} ${kind}s left`);
    if (alive.length && !alive.some((t) => t.pos)) parts.push('not on the board yet');
  }
  if (held) {
    const holder = (app.view.frames || []).find((f) => f.id === held.carrier);
    parts.push(`carried by ${holder ? C.frameLabel(holder.id, holder.name) : held.carrier}`);
  }
  return parts.join(' · ');
}

/** A tile to centre on: the objective's own cells, else anywhere on its card. */
function objectiveSpot(obj) {
  const tiles = (obj.tiles && obj.tiles.length) ? obj.tiles : (obj.cardTiles || []);
  if (!tiles.length) return null;
  const mid = tiles[Math.floor(tiles.length / 2)];
  return { x: mid[0], y: mid[1] };
}

function renderLog() {
  const host = $('log');
  host.innerHTML = '';
  const byTurn = new Map();
  for (const entry of app.view.log || []) {
    if (!byTurn.has(entry.turn)) byTurn.set(entry.turn, []);
    byTurn.get(entry.turn).push(entry);
  }
  const turns = [...byTurn.keys()].sort((a, b) => b - a);
  for (const turn of turns) {
    const head = document.createElement('div');
    head.className = 'turnhead';
    head.textContent = `Turn ${turn}`;
    host.appendChild(head);
    for (const entry of byTurn.get(turn).slice().reverse()) {
      const text = entry.text || '';
      const p = document.createElement('p');
      p.innerHTML = logLineHtml(entry);
      if (/hits|destroyed|damage/i.test(text)) p.className = 'hit';
      else if (/block/i.test(text)) p.className = 'block';
      else if (/^---|game over|scored/i.test(text)) p.className = 'big';
      host.appendChild(p);
    }
  }
}

/** A log line with every frame it names picked out and tinted by team.
 *
 *  The engine writes its log naming frames by their id, and an id is already
 *  a full identity -- team, model and, where a team fields two of a model, an
 *  ordinal. So there is nothing here to infer: the ids in the game are known,
 *  and matching the longest first means "Blue Kuwagata 2" is never mistaken
 *  for a "Blue Kuwagata" followed by a stray 2.
 */
function logLineHtml(entry) {
  const text = entry.text || '';
  const ids = C.frameIds();
  if (!ids.length) return C.escapeHtml(text);
  const pattern = ids.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  const parts = text.split(new RegExp(`(${pattern})`, 'g'));
  return parts.map((part, i) => {
    if (i % 2 === 0) return C.escapeHtml(part);
    const seat = C.frameSeat(part);
    return `<b class="logframe" data-mine="${
      seat === app.view.seat ? '1' : '0'}">${C.escapeHtml(part)}</b>`;
  }).join('');
}

// ---------------------------------------------------------------- sheet

function renderSheet() {
  renderDecision($('sheet-body'), {
    view: app.view,
    commitSelection: app.commitSelection,
    orderPick: app.orderPick,
    rememberedOrder: rememberedOrder(),
    confirm: app.confirm,
    commitProposal,
    cancelProposal,
    deployFrame: app.deployFrame,
    deployableFrames,
    setDeployFrame,
    uidKey,
    toggleCommit,
    commitLimits,
    placeSelection: app.placeSelection,
    placeLimits,
    effectTileOptions,
    effectDoneOption,
    isPlacement,
    togglePlace,
    commitPlacements,
    toggleOrder,
    setOrder: (order) => { app.orderPick = order; renderSheet(); },
    sendOrder,
    send,
    showView,
    focusActive,
    selectFrame,
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

/** The order this frame (or, failing that, anyone) was last resolved in.
 *
 *  The author's note on this: "it feels like a lot of clicks when often you
 *  want to do things in the same order each time". So the last order accepted
 *  comes back as a single tap. It is only offered when it still matches the
 *  steps the engine is asking about -- a remembered `movement, attack` is not
 *  an answer to a `movement, effect` question.
 */
function rememberedOrder() {
  const pending = app.view && app.view.pending;
  if (!pending || pending.kind !== 'resolve_order') return null;
  const legal = (pending.options || []).map((o) => (o.order || []).join('>'));
  const name = pendingFrameName();
  for (const remembered of [app.lastOrder.byFrame[name], app.lastOrder.any]) {
    if (remembered && legal.includes(remembered.join('>'))) return remembered;
  }
  return null;
}

// Keyed by frame name, not id: ids are per-game, and the habit is the frame's
// (a brawler and a sniper want different ones), not this particular battle's.
function pendingFrameName() {
  const pending = app.view && app.view.pending;
  const frame = (app.view.frames || []).find(
    (f) => pending && f.id === pending.frameId);
  return frame ? frame.name : '';
}

function sendOrder(order) {
  const name = pendingFrameName();
  app.lastOrder.any = order.slice();
  if (name) app.lastOrder.byFrame[name] = order.slice();
  savePrefs();
  send('resolve_order', { order });
}

boot();
