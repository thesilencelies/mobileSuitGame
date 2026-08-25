// The decision sheet -- one renderer per `PendingDecision.kind`.
//
// Two things this has to get right, because they are the game:
//   * blocking is compulsory, and whether the blocking card survives depends on
//     it being a super block. Both are stated on the option, not implied.
//   * initiative order decides everything, so every card option carries its
//     printed initiative.

import * as C from './cards.js';
import { frameImageUrl } from './api.js';

const STEP_HELP = {
  movement: 'Move up to your movement allowance. Movement cannot be split.',
  effect: 'Resolve the card text.',
  attack: 'Declare a target and resolve the attack.',
};

export { prettyKind };

export function renderDecision(host, ctx) {
  host.innerHTML = '';
  const { view } = ctx;
  const pending = view.pending;

  if (view.over) return gameOver(host, ctx);
  if (!pending) return waiting(host, 'The engine is resolving...');
  if (pending.waiting || pending.seat !== view.seat) {
    return waiting(host,
      `Seat ${pending.seat} is deciding (${pending.kind.replace(/_/g, ' ')})`);
  }

  const renderer = {
    commit_actions: commitActions,
    resolve_order: resolveOrder,
    move: movement,
    attack_target: attackTarget,
    choose_block: chooseBlock,
    effect_choice: effectChoice,
    echo_card: echoCard,
    deploy: deploy,
  }[pending.kind] || genericDecision;

  // Who this decision is about comes from `pending.frameId` (or, for a deploy,
  // from the frame the player picked), never from the prompt: the prompt is
  // prose written by the engine and "Kuwagata must block Low" is not an
  // instruction when you are fielding two Kuwagatas. The prose is still shown
  // -- it is the engine's own explanation -- but nothing is *identified* by it.
  title(host, prettyKind(pending.kind), pending.prompt, subjectOf(ctx, pending));
  // A tap has proposed something irreversible and is waiting to be meant. It
  // goes above the decision itself, because until it is answered it *is* the
  // decision -- and it is answerable here as well as on the board, for anyone
  // who would rather press a button than tap a tile twice.
  if (ctx.confirm) confirmBanner(host, ctx);
  renderer(host, ctx, pending);
}

/** "You tapped X. Do it, or don't." */
function confirmBanner(host, ctx) {
  const box = document.createElement('div');
  box.className = 'confirm-box';
  const head = document.createElement('div');
  head.className = 'confirm-head';
  head.innerHTML = `<b>${C.escapeHtml(ctx.confirm.label)}</b>`
    + (ctx.confirm.detail ? `<small>${C.escapeHtml(ctx.confirm.detail)}</small>` : '');
  box.appendChild(head);
  const row = document.createElement('div');
  row.className = 'sheet-actions';
  const cancel = document.createElement('button');
  cancel.className = 'btn ghost';
  cancel.textContent = 'Cancel';
  cancel.addEventListener('click', () => ctx.cancelProposal());
  const go = document.createElement('button');
  go.className = 'btn primary';
  go.textContent = 'Confirm';
  go.addEventListener('click', () => ctx.commitProposal());
  row.append(cancel, go);
  box.appendChild(row);
  const hint = document.createElement('p');
  hint.className = 'sheet-sub';
  hint.textContent = 'Or tap the same tile on the board again.';
  box.appendChild(hint);
  host.appendChild(box);
}

// ---------------------------------------------------------------- helpers

function title(host, text, sub, subject) {
  const h = document.createElement('p');
  h.className = 'sheet-title';
  h.textContent = text;
  host.appendChild(h);
  if (subject) {
    const who = document.createElement('p');
    who.className = 'sheet-subject';
    who.dataset.seat = subject.mine ? 'mine' : 'theirs';
    who.textContent = subject.label;
    host.appendChild(who);
  }
  if (sub) {
    const s = document.createElement('p');
    s.className = 'sheet-sub';
    s.textContent = sub;
    host.appendChild(s);
  }
}

/** The frame a decision is about, from structured fields only.
 *
 *  `pending.frameId` names it for every kind that has one. A `deploy` is the
 *  exception: it offers the whole frame x tile cross product and its
 *  `frameId` is null, so the subject is whichever frame the player has picked
 *  in the sheet -- which is also structure, not prose.
 */
export function subjectOf(ctx, pending) {
  const id = pending.kind === 'deploy'
    ? ctx.deployFrame
    : pending.frameId;
  if (!id) return null;
  const frame = (ctx.view.frames || []).find((f) => f.id === id);
  if (!frame) return null;
  return {
    id,
    label: C.frameLabel(id, frame.name),
    mine: frame.seat === ctx.view.seat,
  };
}

function banner(host, text, kind = '') {
  const b = document.createElement('div');
  b.className = `sheet-banner ${kind}`;
  b.textContent = text;
  host.appendChild(b);
  return b;
}

function waiting(host, text) {
  const row = document.createElement('div');
  row.className = 'waiting';
  row.innerHTML = '<span class="dotpulse"></span>';
  row.append(document.createTextNode(text));
  host.appendChild(row);
}

function optionRow(host, { thumbKey, main, sub, go, onTap }) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'opt';
  if (thumbKey) {
    const wrap = document.createElement('div');
    wrap.className = 'opt-thumb';
    wrap.appendChild(C.thumb(thumbKey, { width: 120, showName: false }));
    btn.appendChild(wrap);
  }
  const mid = document.createElement('div');
  mid.className = 'opt-main';
  const b = document.createElement('b');
  b.textContent = main;
  mid.appendChild(b);
  if (sub) {
    const s = document.createElement('small');
    s.textContent = sub;
    mid.appendChild(s);
  }
  btn.appendChild(mid);
  if (go) {
    const g = document.createElement('span');
    g.className = 'opt-go';
    g.textContent = go;
    btn.appendChild(g);
  }
  btn.addEventListener('click', onTap);
  host.appendChild(btn);
  return btn;
}

function list(host) {
  const el = document.createElement('div');
  el.className = 'opt-list';
  host.appendChild(el);
  return el;
}

function prettyKind(kind) {
  return {
    commit_actions: 'Commit two actions',
    resolve_order: 'Order of resolution',
    move: 'Move',
    attack_target: 'Choose a target',
    choose_block: 'Block',
    effect_choice: 'Card effect',
    echo_card: 'Echoes of the fallen',
    deploy: 'Deploy your squad',
  }[kind] || kind.replace(/_/g, ' ');
}

// ---------------------------------------------------------------- kinds

function commitActions(host, ctx, pending) {
  const chosen = ctx.commitSelection;
  banner(host,
    chosen.length === 2 ? 'Ready to commit' : `Pick ${2 - chosen.length} more from the hand`,
    chosen.length === 2 ? 'info' : 'info');

  const strip = document.createElement('div');
  strip.className = 'opt-list';
  for (const uid of chosen) {
    const key = ctx.uidKey(uid);
    optionRow(strip, {
      thumbKey: key,
      main: C.displayName(key),
      sub: C.summaryLine(key),
      go: 'remove',
      onTap: () => ctx.toggleCommit(uid),
    });
  }
  host.appendChild(strip);

  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  const go = document.createElement('button');
  go.className = 'btn primary';
  go.textContent = 'Commit face down';
  go.disabled = chosen.length !== Math.min(2, pending.options.length);
  go.addEventListener('click', () => ctx.send('commit_actions', { uids: chosen.slice() }));
  const open = document.createElement('button');
  open.className = 'btn ghost';
  open.textContent = 'Open hand';
  open.addEventListener('click', () => ctx.showView('plan'));
  actions.append(open, go);
  host.appendChild(actions);
}

// Ordering the steps was three taps for what is usually the same answer every
// time, so the previous order comes back as one tap, then every order the
// engine offers as one tap each. The step-by-step picker is still there below
// them -- the shortcut never removes the choice, it just stops charging for it.
function resolveOrder(host, ctx, pending) {
  const steps = [...new Set(pending.options.flatMap((o) => o.order))];
  const picked = ctx.orderPick.filter((s) => steps.includes(s));
  const orders = pending.options.map((o) => o.order).filter(Boolean);
  const send = (order) => (ctx.sendOrder || ((o) => ctx.send('resolve_order', { order: o })))(order);

  const remembered = ctx.rememberedOrder;
  if (remembered) {
    const repeat = document.createElement('button');
    repeat.className = 'btn primary big';
    repeat.innerHTML = `Same as last time<br><small>${orderWords(remembered)}</small>`;
    repeat.addEventListener('click', () => send(remembered.slice()));
    host.appendChild(repeat);
  }

  const list = document.createElement('div');
  list.className = 'opt-list';
  list.style.marginTop = remembered ? '8px' : '0';
  for (const order of orders) {
    if (remembered && order.join('>') === remembered.join('>')) continue;
    optionRow(list, {
      main: orderWords(order),
      sub: order.map((s) => STEP_HELP[s] || s).join(' '),
      go: 'go',
      onTap: () => send(order.slice()),
    });
  }
  host.appendChild(list);

  const note = document.createElement('p');
  note.className = 'sheet-sub';
  note.style.marginTop = '10px';
  note.textContent = 'Movement cannot be split around the other steps.';
  host.appendChild(note);

  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.className = 'sheet-sub';
  summary.textContent = 'Build the order step by step';
  details.appendChild(summary);

  const row = document.createElement('div');
  row.className = 'step-row';
  row.style.marginTop = '8px';
  for (const step of steps) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'step';
    const index = picked.indexOf(step);
    if (index >= 0) {
      btn.dataset.picked = '1';
      btn.innerHTML = `<span class="ord">${index + 1}</span>`;
    }
    btn.append(document.createTextNode(step));
    btn.title = STEP_HELP[step] || '';
    btn.addEventListener('click', () => ctx.toggleOrder(step));
    row.appendChild(btn);
  }
  details.appendChild(row);

  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  const reset = document.createElement('button');
  reset.className = 'btn ghost';
  reset.textContent = 'Clear';
  reset.addEventListener('click', () => ctx.setOrder([]));
  const go = document.createElement('button');
  go.className = 'btn primary';
  go.textContent = 'Resolve in this order';
  go.disabled = picked.length !== steps.length;
  go.addEventListener('click', () => send(picked.slice()));
  actions.append(reset, go);
  details.appendChild(actions);
  host.appendChild(details);
}

function orderWords(order) {
  return (order || []).map((s) => s.charAt(0).toUpperCase() + s.slice(1)).join(' → ');
}

// Setup, before turn 1: each player puts their frames on the near edge of
// their own terrain, one at a time, alternating (rules.tex:337).
//
// The engine offers the whole (frame x free tile) cross product in one
// decision -- 36 options at the start of a 3v3 -- because the seat is choosing
// which frame to place as well as where. Thirty-six undifferentiated rows is
// not a decision anyone can read, so the sheet holds the *frame* half (a chip
// each, with its art) and the board holds the *tile* half (the blue tiles are
// the engine's own options for the chosen frame). Placing is irreversible for
// the rest of the battle, so it is a tap to propose and a tap to confirm, like
// movement.
function deploy(host, ctx, pending) {
  const frames = ctx.deployableFrames(pending);
  banner(host, frames.length === 1
    ? 'One frame left to place'
    : `Choose a frame, then tap a blue tile — ${frames.length} still to place`,
  'info');

  const row = document.createElement('div');
  row.className = 'deploy-row';
  for (const entry of frames) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'deploy-chip';
    if (entry.id === ctx.deployFrame) chip.dataset.on = '1';
    const img = document.createElement('img');
    img.src = frameImageUrl(entry.name);
    img.alt = '';
    img.addEventListener('error', () => img.remove(), { once: true });
    chip.appendChild(img);
    const label = document.createElement('span');
    label.textContent = C.frameLabel(entry.id, entry.name);
    chip.appendChild(label);
    const spec = C.frame(entry.name);
    if (spec) {
      const sub = document.createElement('small');
      sub.textContent = `move ${spec.movement}`;
      chip.appendChild(sub);
    }
    chip.addEventListener('click', () => ctx.setDeployFrame(entry.id));
    row.appendChild(chip);
  }
  host.appendChild(row);

  const tiles = (pending.options || []).filter((o) => o.frame === ctx.deployFrame);
  const note = document.createElement('p');
  note.className = 'sheet-sub';
  note.textContent = `${tiles.length} legal tile${tiles.length === 1 ? '' : 's'} `
    + 'on your own edge, shown in blue. You and the AI place one frame each in '
    + 'turn, and whoever placed first takes priority.';
  host.appendChild(note);

  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  const show = document.createElement('button');
  show.className = 'btn primary';
  show.textContent = 'Show me on the board';
  show.addEventListener('click', () => { ctx.showView('board'); });
  actions.appendChild(show);
  host.appendChild(actions);
}

function movement(host, ctx, pending) {
  const stay = pending.options.find((o) => o.cost === 0);
  const max = pending.options.reduce((m, o) => Math.max(m, o.cost || 0), 0);
  banner(host, `Tap a green tile on the board — up to ${max} movement`, 'info');
  const p = document.createElement('p');
  p.className = 'sheet-sub';
  p.textContent = `${pending.options.length} destinations. `
    + 'Climbing costs one extra per elevation and you cannot stop part-way up; '
    + 'descending is free.';
  host.appendChild(p);

  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  if (stay) {
    const btn = document.createElement('button');
    btn.className = 'btn ghost';
    btn.textContent = 'Stay put';
    btn.addEventListener('click', () => ctx.send('move', { x: stay.x, y: stay.y, cost: 0 }));
    actions.appendChild(btn);
  }
  const show = document.createElement('button');
  show.className = 'btn primary';
  show.textContent = 'Show me on the board';
  show.addEventListener('click', () => { ctx.showView('board'); ctx.focusActive(); });
  actions.appendChild(show);
  host.appendChild(actions);
}

// The one read the whole game turns on: what can this target still cover, and
// how many cards has it got left to cover it with?
//
// Every part comes off the server's `defence` readout, which is built with the
// engine's own `combat.block_options` -- so Close Quarters barring resolved
// cards, and Guard Break letting one wide card cover several zones at once,
// are the engine's answers and not this file's guesses. What is *not* here is
// how many of the face-down cards block each zone: that is the hidden half of
// the game, and the count is deliberately given whole rather than per zone.
function attackTarget(host, ctx, pending) {
  const guardBreak = isGuardBreak(ctx);
  banner(host, guardBreak
    ? 'Guard Break — every zone must be blocked separately'
    : 'One matching block stops the whole attack', 'info');

  for (const option of pending.options) {
    const zones = option.zones || {};
    const total = Object.values(zones).reduce((a, b) => a + b, 0);
    const target = (ctx.view.frames || []).find((f) => f.id === option.id);
    const defence = (ctx.view.defence || {})[option.id];

    const box = document.createElement('div');
    box.className = 'target';

    const head = document.createElement('div');
    head.className = 'target-head';
    // Two frames of the same model are two different targets with two
    // different damage tracks; the list has to say which one this is.
    const token = option.kind === 'token'
      ? (ctx.view.tokens || []).find((t) => t.id === option.id) : null;
    const targetName = option.kind === 'frame'
      ? C.frameLabel(option.id, option.name || option.id)
      : tokenName(ctx, token, option);
    head.innerHTML = `<b>${C.escapeHtml(targetName)}</b>
      ${option.kind === 'token' ? '<small>token</small>' : ''}
      <span class="t-cards">${defence
        ? `${defence.remaining} card${defence.remaining === 1 ? '' : 's'} left`
          + (defence.faceDown ? ` · <em>${defence.faceDown} face down</em>` : '')
        : ''}</span>`;
    box.appendChild(head);

    // Three images of the same frame are three identical options -- which is
    // the card working. The one thing that does tell them apart is where they
    // are standing, and that is not a secret, so the list says it.
    if (token && token.kind === 'image') {
      const note = document.createElement('p');
      note.className = 'target-note';
      note.textContent = `at (${token.pos.x}, ${token.pos.y}). `
        + 'One of the three is the frame itself and will block; the other two '
        + 'vanish when struck and cost you the action.';
      box.appendChild(note);
    }

    let openZones = 0;
    if (target && defence) {
      const grid = document.createElement('div');
      grid.className = 'zgrid';
      for (const zone of C.ZONES) {
        const damage = zones[zone] || 0;
        const info = (defence.zones || {})[zone] || { cards: 0, super: 0, known: [] };
        const row = document.createElement('div');
        row.className = 'zrow';
        if (damage) row.dataset.hit = '1';
        const through = damage > 0 && info.cards === 0;
        if (through && !defence.faceDown) row.dataset.through = '1';
        if (through) openZones += 1;

        const covers = info.known.map((c) => C.displayName(c.key)
          + (c.super ? ' ★' : '') + (c.resolved ? '' : ' (face up)'));
        const blockText = info.cards
          ? `<b>${info.cards}</b> can block${info.super ? ` · <i>${info.super} super</i>` : ''}`
          : (defence.faceDown ? 'nothing visible blocks it' : '<span>no block at all</span>');

        row.innerHTML = `<span class="zname">${zone}</span>
          <span class="zdmg"${damage ? '' : ' data-none="1"'}>${damage || '–'}</span>
          <span class="zblock">${blockText}</span>`;
        row.title = covers.length
          ? `${zone}: ${covers.join(', ')}`
          : `${zone}: no card this seat can see blocks it`;
        grid.appendChild(row);
      }
      box.appendChild(grid);

      const note = document.createElement('p');
      note.className = 'target-note';
      const armour = C.ZONES.map((z) => `${z[0]} ${target.damage[z]}/${target.armour[z]}`);
      const bits = [`damage ${armour.join(' ')}`];
      // A shield counter absorbs a whole attack, every zone of it, for one
      // counter -- so "3 zones uncovered" against a shielded frame is not
      // three hits. The count is the engine's; the rule is stated, not
      // recomputed.
      if (target.shields) {
        bits.push(`${target.shields} shield counter${target.shields === 1 ? '' : 's'}`
          + ' — one absorbs this whole attack, every zone of it');
      }
      if (defence.keepsNextBlock) bits.push('keeps its next block (frame ability)');
      if (defence.faceDown) {
        bits.push(`${defence.faceDown} face-down card${defence.faceDown === 1 ? '' : 's'}`
          + ' could cover anything');
      }
      note.textContent = bits.join(' · ');
      box.appendChild(note);
    }

    const go = document.createElement('button');
    go.className = 'btn primary';
    go.textContent = `Attack — ${total} mark${total === 1 ? '' : 's'}`
      + (target && target.shields ? ' · shielded'
        : (openZones ? ` · ${openZones} zone${openZones === 1 ? '' : 's'} uncovered` : ''));
    go.addEventListener('click',
      () => ctx.send('attack_target', { kind: option.kind, id: option.id }));
    box.appendChild(go);
    if (openZones) box.dataset.open = '1';

    const look = document.createElement('button');
    look.className = 'btn ghost sm';
    look.style.width = '100%';
    look.style.marginTop = '6px';
    look.textContent = 'Show me on the board';
    look.addEventListener('click', () => {
      if (ctx.selectFrame) ctx.selectFrame(option.id);
      ctx.showView('board');
    });
    if (option.kind === 'frame') box.appendChild(look);

    host.appendChild(box);
  }
}

/** Whether the card that is attacking has Guard Break, from the resolving card. */
/** A token's name in the target list -- images and drones belong to a frame. */
function tokenName(ctx, token, option) {
  if (token && token.kind === 'image') {
    return token.frame
      ? `An image of ${C.frameLabel(token.frame)}` : 'An image';
  }
  if (token && token.kind === 'drone') {
    return token.frame ? `${C.frameLabel(token.frame)}'s drone` : 'A drone';
  }
  return option.name || option.id;
}

function isGuardBreak(ctx) {
  const res = ctx.view.resolving;
  if (!res || !res.key) return false;
  const info = C.card(res.key);
  return !!(info && (info.keywords || []).includes('guardbreak'));
}

function chooseBlock(host, ctx, pending) {
  return blockChoices(host, ctx, pending, 'choose_block');
}

/** The compulsory-block sheet. A drone's attack raises the same decision
 *  through `effect_choice`, and it deserves the same read-out: which zones,
 *  what the card costs you, and whether it survives. */
function blockChoices(host, ctx, pending, kind) {
  const zones = [...new Set(pending.options.flatMap((o) => o.zones || []))];
  const attack = (ctx.view.resolving || {}).attack || null;
  const guardBreak = !!(attack && attack.guardBreak);
  banner(host, `Blocking is compulsory — ${zones.join(' / ') || 'attack'} must be blocked`);
  const note = document.createElement('p');
  note.className = 'sheet-sub';
  note.textContent = guardBreak
    ? 'Guard Break: a card covers every one of these zones it blocks, and the '
      + 'zones it does not cover come back for another block. Spending a card '
      + 'that covers two of them costs you one card instead of two.'
    : 'One matching zone stops the whole attack. A normal block is discarded '
      + 'and, if it had not resolved yet, its own action is forfeit. A super '
      + 'block is kept.';
  host.appendChild(note);

  if (attack) {
    const state = document.createElement('p');
    state.className = 'sheet-sub';
    const marks = Object.entries(attack.zones || {}).map(([z, n]) => `${z} ${n}`);
    state.textContent = `Incoming: ${marks.join(' · ') || 'no marks'}`
      + (attack.blocked && attack.blocked.length
        ? ` · already blocked ${attack.blocked.join('/')}` : '');
    host.appendChild(state);
  }

  const el = list(host);
  // Under Guard Break the widest card is the cheapest answer, so lead with it.
  const covered = (option) => {
    const info = C.card(option.key);
    if (!info) return [];
    return (option.zones || []).filter((z) => (info.blocks[z] || 0) > 0);
  };
  const options = guardBreak
    ? [...pending.options].sort((a, b) => covered(b).length - covered(a).length)
    : pending.options;

  for (const option of options) {
    const info = C.card(option.key);
    const mine = covered(option);
    const superBlock = info && mine.some((z) => (info.blocks[z] || 0) >= 2);
    const committed = (ctx.view.frames || [])
      .flatMap((f) => f.committed || [])
      .some((c) => c.uid === option.uid && !c.resolved);
    const bits = [];
    if (guardBreak && mine.length > 1) {
      bits.push(`covers ${mine.join(' + ')} in one card`);
    }
    bits.push(superBlock ? 'SUPER BLOCK — card is kept' : 'normal block — card is discarded');
    if (committed && !superBlock) bits.push('its own action is forfeit');
    if (info && info.initiative && info.initiative.length) {
      bits.push(`initiative ${info.initiative.join('/')}`);
    }
    optionRow(el, {
      thumbKey: option.key,
      main: C.displayName(option.key),
      sub: bits.join(' · '),
      go: mine.join('/') || (option.zones || []).join('/'),
      onTap: () => ctx.send(kind, { uid: option.uid }),
    });
  }
}

// Card text asks four different shapes of question through one decision kind.
// Rendering them all as a list of raw payloads is what made a drone's turn feel
// like filling in a form: thirty rows reading "x 7, y 5" for what is obviously
// a tap on the board. So the option shape picks the renderer.
function effectChoice(host, ctx, pending) {
  const options = pending.options || [];
  const every = (fn) => options.length > 0 && options.every(fn);
  if (every((o) => 'x' in o && 'y' in o && !('frame' in o))) {
    return effectTiles(host, ctx, pending);
  }
  if (every((o) => 'uid' in o && 'key' in o)) {
    return blockChoices(host, ctx, pending, 'effect_choice');
  }
  if (every((o) => ('frame' in o && !('x' in o)) || 'token' in o)) {
    return effectTargets(host, ctx, pending);
  }
  const el = list(host);
  for (const option of options) {
    optionRow(el, {
      main: describePayload(option),
      sub: '',
      go: 'choose',
      onTap: () => ctx.send('effect_choice', option),
    });
  }
}

/** "Somewhere on the board" -- a drone's move, a reflex step, a Teleport. */
function effectTiles(host, ctx, pending) {
  banner(host, 'Tap a green tile on the board', 'info');
  const p = document.createElement('p');
  p.className = 'sheet-sub';
  p.textContent = `${pending.options.length} places to choose from. `
    + 'Tap once to propose it, again to commit.';
  host.appendChild(p);
  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  const show = document.createElement('button');
  show.className = 'btn primary';
  show.textContent = 'Show me on the board';
  show.addEventListener('click', () => { ctx.showView('board'); ctx.focusActive(); });
  actions.appendChild(show);
  host.appendChild(actions);
}

/** "Which of them" -- a drone's target, including an enemy's images. */
function effectTargets(host, ctx, pending) {
  banner(host, 'Choose a target — on the board or from the list', 'info');
  const el = list(host);
  for (const option of pending.options) {
    if (option.token) {
      const owner = imageOwner(ctx, option.token);
      optionRow(el, {
        main: owner ? `One of ${C.frameLabel(owner)}'s images` : 'An image',
        sub: 'Only one of them is the frame; the rest vanish when struck',
        go: 'guess',
        onTap: () => ctx.send('effect_choice', { token: option.token }),
      });
      continue;
    }
    const target = (ctx.view.frames || []).find((f) => f.id === option.frame);
    const defence = (ctx.view.defence || {})[option.frame];
    optionRow(el, {
      main: C.frameLabel(option.frame, option.name || option.frame),
      sub: target
        ? `${target.name}${defence ? ` · ${defence.remaining} card${
          defence.remaining === 1 ? '' : 's'} left` : ''}`
        : '',
      go: 'target',
      onTap: () => ctx.send('effect_choice', { frame: option.frame }),
    });
  }
}

function imageOwner(ctx, tokenId) {
  const token = (ctx.view.tokens || []).find((t) => t.id === tokenId);
  return token ? token.frame : null;
}

function echoCard(host, ctx, pending) {
  banner(host, 'A destroyed frame grants a surviving ally one bonus block', 'info');
  const note = document.createElement('p');
  note.className = 'sheet-sub';
  note.textContent = C.ECHO_HELP;
  host.appendChild(note);
  const el = list(host);
  for (const option of pending.options) {
    if (option.decline) {
      optionRow(el, { main: 'Decline', sub: 'Keep the card in the dead frame\'s deck', go: '', onTap: () => ctx.send('echo_card', option) });
    } else {
      const hostName = C.frameLabel(option.host, option.hostName || option.host);
      const deadName = C.frameLabel(option.dead, option.deadName || option.dead);
      optionRow(el, {
        main: `Set it sideways with ${hostName}'s actions`,
        sub: `Revealed from ${deadName}'s deck. It can block for that frame; `
          + 'it never resolves and the rest of its text is ignored.',
        go: 'echo',
        onTap: () => ctx.send('echo_card', { dead: option.dead, host: option.host }),
      });
    }
  }
}

function genericDecision(host, ctx, pending) {
  const el = list(host);
  for (const option of pending.options) {
    optionRow(el, {
      main: describePayload(option),
      sub: '',
      go: 'choose',
      onTap: () => ctx.send(pending.kind, option),
    });
  }
}

function gameOver(host, ctx) {
  const vp = ctx.view.vp || {};
  const mine = Number(vp[String(ctx.view.seat)] || 0);
  const theirs = Object.entries(vp)
    .filter(([s]) => Number(s) !== ctx.view.seat)
    .reduce((a, [, v]) => a + Number(v), 0);
  title(host, mine > theirs ? 'You win' : (mine < theirs ? 'You lose' : 'Draw'),
    `Victory points ${mine} – ${theirs} after ${ctx.view.turn - 1} turns`);
  const objectives = (ctx.view.board && ctx.view.board.objectives) || [];
  const el = list(host);
  for (const obj of objectives) {
    optionRow(el, {
      main: obj.name,
      sub: `${obj.status} · defend ${obj.defend} / attack ${obj.attack}`,
      go: obj.value ? `+${obj.value}` : '',
      onTap: () => {},
    });
  }
  const actions = document.createElement('div');
  actions.className = 'sheet-actions';
  const again = document.createElement('button');
  again.className = 'btn primary';
  again.textContent = 'New game';
  again.addEventListener('click', () => ctx.newGame());
  actions.appendChild(again);
  host.appendChild(actions);
}

function describePayload(payload) {
  const entries = Object.entries(payload).filter(([k]) => k !== 'key');
  if (!entries.length) return 'Continue';
  return entries.map(([k, v]) => {
    if (v === true) return prettyWord(k);
    if (v === false) return `no ${prettyWord(k).toLowerCase()}`;
    return `${prettyWord(k)}: ${v}`;
  }).join(' · ');
}

function prettyWord(word) {
  const text = String(word).replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}
