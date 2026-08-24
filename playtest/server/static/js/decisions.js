// The decision sheet -- one renderer per `PendingDecision.kind`.
//
// Two things this has to get right, because they are the game:
//   * blocking is compulsory, and whether the blocking card survives depends on
//     it being a super block. Both are stated on the option, not implied.
//   * initiative order decides everything, so every card option carries its
//     printed initiative.

import * as C from './cards.js';

const STEP_HELP = {
  movement: 'Move up to your movement allowance. Movement cannot be split.',
  effect: 'Resolve the card text.',
  attack: 'Declare a target and resolve the attack.',
};

export function renderDecision(host, ctx) {
  host.innerHTML = '';
  const { view } = ctx;
  const pending = view.pending;

  if (view.over) return gameOver(host, ctx);
  if (!pending) return waiting(host, 'The engine is resolving...');
  if (pending.waiting || pending.seat !== view.seat) {
    return waiting(host, `Seat ${pending.seat} is deciding (${pending.kind.replace(/_/g, ' ')})`);
  }

  const renderer = {
    commit_actions: commitActions,
    resolve_order: resolveOrder,
    move: movement,
    attack_target: attackTarget,
    choose_block: chooseBlock,
    effect_choice: effectChoice,
    echo_card: echoCard,
  }[pending.kind] || genericDecision;

  title(host, prettyKind(pending.kind), pending.prompt);
  renderer(host, ctx, pending);
}

// ---------------------------------------------------------------- helpers

function title(host, text, sub) {
  const h = document.createElement('p');
  h.className = 'sheet-title';
  h.textContent = text;
  host.appendChild(h);
  if (sub) {
    const s = document.createElement('p');
    s.className = 'sheet-sub';
    s.textContent = sub;
    host.appendChild(s);
  }
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

function resolveOrder(host, ctx, pending) {
  const steps = [...new Set(pending.options.flatMap((o) => o.order))];
  const picked = ctx.orderPick.filter((s) => steps.includes(s));
  banner(host, 'Movement cannot be split around the other steps', 'info');

  const row = document.createElement('div');
  row.className = 'step-row';
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
  host.appendChild(row);

  const help = document.createElement('p');
  help.className = 'sheet-sub';
  help.textContent = picked.length
    ? picked.map((s) => STEP_HELP[s] || s).join(' ')
    : 'Tap the steps in the order you want them to happen.';
  host.appendChild(help);

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
  go.addEventListener('click', () => ctx.send('resolve_order', { order: picked }));
  actions.append(reset, go);
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

function attackTarget(host, ctx, pending) {
  banner(host, 'Only zones actually in range can be attacked', 'info');
  const el = list(host);
  for (const option of pending.options) {
    const zones = option.zones || {};
    const total = Object.values(zones).reduce((a, b) => a + b, 0);
    const target = (ctx.view.frames || []).find((f) => f.id === option.id);
    const bits = Object.entries(zones).map(([z, n]) => `${z} ${n}`);
    const sub = [
      bits.join(' · ') || 'no marks',
      target ? `damage ${['High', 'Mid', 'Low'].map((z) => `${target.damage[z]}/${target.armour[z]}`).join(' ')}` : '',
    ].filter(Boolean).join('  ·  ');
    optionRow(el, {
      main: `${option.name || option.id}${option.kind === 'token' ? ' (token)' : ''}`,
      sub,
      go: `${total} mark${total === 1 ? '' : 's'}`,
      onTap: () => ctx.send('attack_target', { kind: option.kind, id: option.id }),
    });
  }
}

function chooseBlock(host, ctx, pending) {
  const zones = [...new Set(pending.options.flatMap((o) => o.zones || []))];
  banner(host, `Blocking is compulsory — ${zones.join(' / ') || 'attack'} must be blocked`);
  const note = document.createElement('p');
  note.className = 'sheet-sub';
  note.textContent = 'One matching zone stops the whole attack. A normal block is '
    + 'discarded and, if it had not resolved yet, its own action is forfeit. '
    + 'A super block is kept.';
  host.appendChild(note);

  const el = list(host);
  for (const option of pending.options) {
    const info = C.card(option.key);
    const superBlock = info && (option.zones || []).some((z) => (info.blocks[z] || 0) >= 2);
    const committed = (ctx.view.frames || [])
      .flatMap((f) => f.committed || [])
      .some((c) => c.uid === option.uid && !c.resolved);
    const bits = [];
    bits.push(superBlock ? 'SUPER BLOCK — card is kept' : 'normal block — card is discarded');
    if (committed && !superBlock) bits.push('its own action is forfeit');
    if (info && info.initiative && info.initiative.length) {
      bits.push(`initiative ${info.initiative.join('/')}`);
    }
    optionRow(el, {
      thumbKey: option.key,
      main: C.displayName(option.key),
      sub: bits.join(' · '),
      go: (option.zones || []).join('/'),
      onTap: () => ctx.send('choose_block', { uid: option.uid }),
    });
  }
}

function effectChoice(host, ctx, pending) {
  const el = list(host);
  for (const option of pending.options) {
    optionRow(el, {
      main: describePayload(option),
      sub: '',
      go: 'choose',
      onTap: () => ctx.send('effect_choice', option),
    });
  }
}

function echoCard(host, ctx, pending) {
  banner(host, 'A defeated frame can lend its next card to an ally as a block', 'info');
  const el = list(host);
  for (const option of pending.options) {
    if (option.decline) {
      optionRow(el, { main: 'Decline', sub: 'Keep the card in the dead frame\'s deck', go: '', onTap: () => ctx.send('echo_card', option) });
    } else {
      optionRow(el, {
        main: `Set it beside ${option.hostName || option.host}`,
        sub: 'It can block for that frame; the rest of its text is ignored',
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
