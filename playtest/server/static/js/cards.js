// Card catalogue lookups, thumbnails and the card-detail modal.
//
// 26 cards (all 24 pilot cards and both drone cards) load, block and deal
// damage but their *text* does nothing in v1. `catalogue_json` marks those
// `notImplemented`; every place a card is drawn says so, because a playtester
// mistaking a no-op for a real effect is a wasted playtest.

import { cardImageUrl } from './api.js';

export const ZONES = ['High', 'Mid', 'Low'];

let CATALOGUE = {};
let FRAMES = {};

export function setCatalogue(catalogue) { CATALOGUE = catalogue || {}; }
export function card(key) { return CATALOGUE[key] || null; }
export function catalogue() { return CATALOGUE; }

// Frame abilities are live rules -- Hector's free block, Adam's pierce
// initiative, Kamikiri's bonus cut all change what a turn is worth -- so the
// frame card has to be readable, not just its name.
export function setFrames(frames) { FRAMES = frames || {}; }
export function frame(name) { return FRAMES[name] || null; }

export function displayName(key) {
  const info = card(key);
  if (info) return info.name;
  return (key || '').replace(/_/g, ' ');
}

export function initiativeOf(key) {
  const info = card(key);
  if (!info || !info.initiative || !info.initiative.length) return null;
  return info.initiative;
}

export function isNotImplemented(key) {
  const info = card(key);
  return !!(info && info.notImplemented);
}

/** A small clickable card thumbnail. */
export function thumb(key, opts = {}) {
  const {
    width = 240, selected = false, dim = false, onTap = null,
    showName = true, initiative = false, tag = '',
  } = opts;
  // `initiative` is off by default: the art already prints the number, and a
  // badge on top of it just hides the real card.
  const info = card(key);
  const el = document.createElement('div');
  el.className = 'cardt';
  if (selected) el.dataset.on = '1';
  if (dim) el.dataset.dim = '1';
  if (info && info.notImplemented) el.dataset.ni = '1';

  const img = document.createElement('img');
  img.className = 'cardart';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.alt = displayName(key);
  img.src = cardImageUrl(key, width);
  img.addEventListener('error', () => {
    img.remove();
    const fb = document.createElement('div');
    fb.className = 'fallback';
    fb.innerHTML = `<b>${escapeHtml(displayName(key))}</b>
      <span>${escapeHtml(info ? info.group : '')}</span>
      <span>no art</span>`;
    el.prepend(fb);
  }, { once: true });
  el.appendChild(img);

  if (initiative && info && info.initiative && info.initiative.length) {
    const badge = document.createElement('div');
    badge.className = 'badge-init';
    badge.textContent = info.initiative.join('/');
    badge.title = 'Printed initiative';
    el.appendChild(badge);
  }
  if (tag) {
    const t = document.createElement('div');
    t.className = 'badge-tag';
    t.textContent = tag;
    el.appendChild(t);
  }
  if (showName) {
    const name = document.createElement('div');
    name.className = 'card-name';
    name.textContent = displayName(key);
    el.appendChild(name);
  }
  if (info && info.notImplemented) {
    const badge = document.createElement('div');
    badge.className = 'badge-ni';
    badge.textContent = 'text not implemented';
    badge.title = info.notImplemented;
    el.appendChild(badge);
  }
  if (onTap) el.addEventListener('click', onTap);
  return el;
}

/** A face-down card back, for the opponent's committed actions. */
export function faceDown(label = 'face down') {
  const el = document.createElement('div');
  el.className = 'cardt';
  const fb = document.createElement('div');
  fb.className = 'fallback';
  fb.innerHTML = `<b>?</b><span>${escapeHtml(label)}</span>`;
  fb.style.background =
    'repeating-linear-gradient(45deg, #1b222c, #1b222c 6px, #222b37 6px, #222b37 12px)';
  el.appendChild(fb);
  return el;
}

/** Three zone pips summarising a card's attack/block profile. */
export function zonePips(key) {
  const info = card(key);
  const wrap = document.createElement('div');
  wrap.className = 'lzones';
  for (const z of ZONES) {
    const pip = document.createElement('span');
    pip.className = 'zpip';
    pip.textContent = z[0];
    if (info) {
      const atk = info.attacks[z] || 0;
      const blk = info.blocks[z] || 0;
      if (atk > 0) { pip.dataset.atk = '1'; pip.textContent = `${z[0]}${atk}`; }
      if (blk >= 2) pip.dataset.sblk = '1';
      else if (blk > 0) pip.dataset.blk = '1';
      const bits = [];
      if (atk) bits.push(`attacks ${z} for ${atk}` +
        (info.ranges[z] ? ` at range ${info.ranges[z]}` : ' (melee)'));
      if (blk) bits.push(blk >= 2 ? `super blocks ${z}` : `blocks ${z}`);
      pip.title = bits.join('; ') || `${z}: nothing`;
    }
    wrap.appendChild(pip);
  }
  return wrap;
}

export function summaryLine(key) {
  const info = card(key);
  if (!info) return key;
  const bits = [];
  const atk = ZONES.filter((z) => info.attacks[z] > 0);
  if (atk.length) {
    bits.push('atk ' + atk.map((z) => {
      const r = info.ranges[z];
      return `${z[0]}${info.attacks[z]}${r ? `@${r}` : ''}`;
    }).join(' '));
  }
  const blk = ZONES.filter((z) => info.blocks[z] > 0);
  if (blk.length) {
    bits.push('blk ' + blk.map((z) => (info.blocks[z] >= 2 ? `${z[0]}*` : z[0])).join(''));
  }
  if (info.movement) bits.push(`mv ${info.movement > 0 ? '+' : ''}${info.movement}`);
  if (info.keywords && info.keywords.length) bits.push(info.keywords.join(', '));
  return bits.join(' · ') || info.type;
}

// ---------------------------------------------------------------- modal

let modalEl = null;

export function initModal() {
  modalEl = document.getElementById('modal');
  document.getElementById('modal-scrim').addEventListener('click', closeModal);
}

export function closeModal() { if (modalEl) modalEl.hidden = true; }

export function showCard(key) {
  const info = card(key);
  const body = document.getElementById('modal-body');
  body.innerHTML = '';

  const art = document.createElement('img');
  art.className = 'big-art';
  art.loading = 'lazy';
  art.src = cardImageUrl(key, 360);
  art.alt = displayName(key);
  art.addEventListener('error', () => art.remove(), { once: true });
  body.appendChild(art);

  const h = document.createElement('h2');
  h.textContent = displayName(key);
  body.appendChild(h);

  const sub = document.createElement('div');
  sub.className = 'sub';
  sub.textContent = info
    ? `${info.group} · ${info.type}${info.faction ? ' · ' + info.faction : ''}`
    : key;
  body.appendChild(sub);

  if (info && info.notImplemented) {
    const warn = document.createElement('div');
    warn.className = 'warnbox';
    warn.innerHTML = `<b>Card text not implemented</b>
      This card loads, blocks and deals damage correctly, but its text does
      nothing in this build. Treat its printed effect as absent when you judge
      the balance.`;
    body.appendChild(warn);
  }

  if (info) {
    const table = document.createElement('table');
    table.className = 'zonetable';
    table.innerHTML = `<thead><tr><th>Zone</th><th>Attack</th><th>Range</th>
        <th>Type</th><th>Block</th></tr></thead><tbody>${
      ZONES.map((z) => `<tr>
        <td>${z}</td>
        <td>${info.attacks[z] || '–'}</td>
        <td>${info.attacks[z] ? (info.ranges[z] || 'melee') : '–'}</td>
        <td>${info.dtypes[z] || '–'}</td>
        <td>${info.blocks[z] >= 2 ? 'super' : (info.blocks[z] ? 'yes' : '–')}</td>
      </tr>`).join('')}</tbody>`;
    body.appendChild(table);

    const dl = document.createElement('dl');
    dl.className = 'statgrid';
    const rows = [
      ['Initiative', (info.initiative || []).join(' then ') || '–'],
      ['Movement', info.movement ? `${info.movement > 0 ? '+' : ''}${info.movement}` : '0'],
      ['Persistence', info.persistence === null ? 'permanent'
        : (info.persistence ? `${info.persistence} turn(s)` : 'none')],
      ['Keywords', (info.keywords || []).join(', ') || '–'],
    ];
    if (info.knockback) rows.push(['Knockback', String(info.knockback)]);
    for (const [k, v] of rows) {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.append(dt, dd);
    }
    body.appendChild(dl);

    if (info.text) {
      const text = document.createElement('div');
      text.className = 'cardtext';
      text.textContent = cleanText(info.text);
      body.appendChild(text);
    }
  }

  const close = document.createElement('button');
  close.className = 'btn big';
  close.textContent = 'Close';
  close.addEventListener('click', closeModal);
  body.appendChild(close);

  modalEl.hidden = false;
}

/** The frame's own card: art, armour, and the ability text that is live. */
export function showFrame(name, live = null) {
  const spec = frame(name);
  const body = document.getElementById('modal-body');
  body.innerHTML = '';

  const art = document.createElement('img');
  art.className = 'big-art';
  art.loading = 'lazy';
  art.src = cardImageUrl(name, 360);
  art.alt = name;
  art.addEventListener('error', () => art.remove(), { once: true });
  body.appendChild(art);

  const h = document.createElement('h2');
  h.textContent = name;
  body.appendChild(h);

  const sub = document.createElement('div');
  sub.className = 'sub';
  sub.textContent = spec
    ? `${spec.faction} · move ${spec.movement} · ${spec.weaponSlots} weapons, `
      + `${spec.boosterSlots} boosters · deck ${spec.deckSize}`
    : 'frame';
  body.appendChild(sub);

  if (spec && spec.ability) {
    const text = document.createElement('div');
    text.className = 'cardtext';
    text.textContent = cleanText(spec.ability);
    body.appendChild(text);
  }

  if (spec) {
    const table = document.createElement('table');
    table.className = 'zonetable';
    table.innerHTML = `<thead><tr><th>Zone</th><th>Armour</th>${
      live ? '<th>Damage</th><th>Left</th>' : ''}</tr></thead><tbody>${
      ZONES.map((z) => {
        const armour = (spec.armour || {})[z] || 0;
        const dmg = live ? (live.damage[z] || 0) : 0;
        return `<tr><td>${z}</td><td>${armour}</td>${
          live ? `<td>${dmg}</td><td>${Math.max(0, armour - dmg)}</td>` : ''}</tr>`;
      }).join('')}</tbody>`;
    body.appendChild(table);

    const dl = document.createElement('dl');
    dl.className = 'statgrid';
    const rows = [
      ['Shield', spec.shield ? String(spec.shield) : '–'],
      ['Keywords', (spec.keywords || []).join(', ') || '–'],
    ];
    if (live) {
      const statuses = Object.entries(live.statuses || {}).filter(([, n]) => n > 0)
        .map(([k, n]) => `${k} ${n}`).join(', ');
      rows.push(['Statuses', statuses || 'none']);
      rows.push(['Cards in play',
        `${(live.committed || []).length} face down · ${(live.onField || []).length} on field`]);
      rows.push(['Deck / discard', `${live.deckCount} / ${live.discardCount}`]);
    }
    for (const [k, v] of rows) {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.append(dt, dd);
    }
    body.appendChild(dl);
  }

  const close = document.createElement('button');
  close.className = 'btn big';
  close.textContent = 'Close';
  close.addEventListener('click', closeModal);
  body.appendChild(close);

  modalEl.hidden = false;
}

const KEYWORD_WORDS = {
  onhit: 'On Hit', closequarters: 'Close Quarters', guardbreak: 'Guardbreak',
  deathstrike: 'Deathstrike', knockback: 'Knockback', reload: 'Reload',
  feint: 'Feint', committed: 'Committed', flying: 'Flying', shield: 'Shield',
  slowed: 'Slowed', stunned: 'Stunned', dazed: 'Dazed', lucid: 'Lucid',
  stimmed: 'Stimmed', boosted: 'Boosted', revealed: 'Revealed',
};

function keywordWord(name) {
  const bare = name.replace(/^full/, '').toLowerCase();
  return KEYWORD_WORDS[bare] || (bare.charAt(0).toUpperCase() + bare.slice(1));
}

/** The Text column is LaTeX. Most macros take no argument (`\fullslowed`),
 *  so stripping them outright would delete the keyword the card is about. */
export function cleanText(text) {
  return (text || '')
    .replace(/\\\\/g, '\n')
    .replace(/\\(?:full)?kw\s*\{([^}]*)\}/g, '$1')
    .replace(/\\([a-zA-Z]+)\s*\{([^}]*)\}/g,
      (m, name, arg) => `${keywordWord(name)} (${arg})`)
    .replace(/\\([a-zA-Z]+)/g, (m, name) => keywordWord(name))
    .replace(/[{}]/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*/g, '\n')
    .trim();
}

export function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
