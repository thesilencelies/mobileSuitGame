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
    head.innerHTML = `<b>${C.escapeHtml(option.name || option.id)}</b>
      ${option.kind === 'token' ? '<small>token</small>' : ''}
      <span class="t-cards">${defence
        ? `${defence.remaining} card${defence.remaining === 1 ? '' : 's'} left`
          + (defence.faceDown ? ` · <em>${defence.faceDown} face down</em>` : '')
        : ''}</span>`;
    box.appendChild(head);

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
      + (openZones ? ` · ${openZones} zone${openZones === 1 ? '' : 's'} uncovered` : '');
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
function isGuardBreak(ctx) {
  const res = ctx.view.resolving;
  if (!res || !res.key) return false;
  const info = C.card(res.key);
  return !!(info && (info.keywords || []).includes('guardbreak'));
}

function chooseBlock(host, ctx, pending) {
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
