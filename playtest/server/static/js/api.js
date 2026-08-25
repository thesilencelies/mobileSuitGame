// THE TRANSPORT SEAM.
//
// This is the only module in the client that talks to the outside world. No
// view calls `fetch` (or anything else) directly -- they all go through the
// `api` object below. That is deliberate: the engine currently sits behind
// HTTP on localhost, but if it ever has to run *inside* the browser instead
// (Pyodide, a JS port, whatever), replacing this one file is the entire job.
// Everything else in the client only knows these method names and the shape of
// the JSON that comes back.
//
// The contract, in full:
//
//   getHealth()                     -> {ok, cards, frames, decks, images, ai}
//   getCatalogue()                  -> {"{Group}_{Name}": cardJson, ...}
//   getFrames()                     -> {frameName: frameJson, ...}
//   getDecks()                      -> {decks: [...]}
//   getAiParams()                   -> {source, params: [...], presets: {...}}
//   newGame(config)                 -> {gameId, view}
//   getState(gameId)                -> view
//   sendCommand(gameId, kind, payload) -> view
//   undo(gameId)                    -> view
//   getLog(gameId)                  -> {gameId, log}
//   getThreat(gameId, frameId)      -> {reach, los, movement, ...}
//   setAiParams(gameId, params)     -> view
//   cardImageUrl(key, width)        -> a URL the client can put in an <img>
//   terrainImageUrl(cardName)       -> the terrain card's playable grid
//   tokenImageUrl(kind, hp)         -> the piece art for a token at that hp
//   frameImageUrl(frameName)        -> the frame's standee, cut out of its art
//
// Nothing here may reference an external host: the app has to work with no
// network at all.

const BASE = '';                 // same origin, relative -- never an absolute host

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  let body = null;
  const text = await res.text();
  if (text) {
    try { body = JSON.parse(text); } catch { body = { detail: text }; }
  }
  if (!res.ok) {
    const detail = (body && (body.detail || body.error)) || res.statusText;
    const err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

const post = (path, payload) =>
  request(path, { method: 'POST', body: JSON.stringify(payload || {}) });

export const api = {
  // -- static data ---------------------------------------------------
  getHealth: () => request('/api/health'),
  getCatalogue: () => request('/api/cards'),
  getFrames: () => request('/api/frames'),
  getDecks: () => request('/api/decks'),
  getAiParams: () => request('/api/ai/params'),

  // -- a game --------------------------------------------------------
  newGame: (config) => post('/api/game', config),
  getState: (gameId) => request(`/api/game/${gameId}`),

  // `attack_target`'s own payload has a `kind` key, which would collide with
  // the command kind, so every command goes in the nested form.
  sendCommand: (gameId, kind, payload) =>
    post(`/api/game/${gameId}/command`, { kind, payload }),

  undo: (gameId) => post(`/api/game/${gameId}/undo`),
  getLog: (gameId) => request(`/api/game/${gameId}/log`),
  getThreat: (gameId, frameId) =>
    request(`/api/game/${gameId}/threat?frame=${encodeURIComponent(frameId)}`),
  setAiParams: (gameId, params) =>
    post(`/api/game/${gameId}/ai-params`, { aiParams: params }),

  cardImageUrl,
  terrainImageUrl,
  tokenImageUrl,
  frameImageUrl,
  elevationIconUrl,
  tileIconUrl,
};

// Card art is named "{Group}_{Name}.png" -- the same string as the engine's
// card key. Those filenames contain spaces, so the key must be encoded or the
// request 404s. `width` asks for a downscaled copy: never ship the 378x537
// print-density original to a phone.
export function cardImageUrl(key, width = 240) {
  return `/api/card-image/${encodeURIComponent(key)}?w=${width}`;
}

// Terrain and token art are plain static files rather than an API route: the
// board asks for up to twenty terrain cards at once and they never change, so
// they want to sit in the browser's cache like any other image. `assets.py`
// writes them under these names -- keep `slug` in step with `assets.slug`.
export function terrainImageUrl(cardName) {
  return `/static/terrain/${slug(cardName)}.jpg`;
}

// The numbered token art *is* the damage state: `Tower4` is untouched and
// `Tower1` is one hit from destroyed, and each Power Reactor has two states.
// `hp` picks the file; anything without states ignores it.
export function tokenImageUrl(kind, hp) {
  const stem = TOKEN_ART[String(kind || '').toLowerCase()];
  if (!stem) return null;
  if (typeof stem === 'string') return `/static/tokens/${stem}.png`;
  const step = Math.max(1, Math.min(stem.states, Number(hp) || 1));
  return `/static/tokens/${stem.stem}${step}.png`;
}

// The glyphs the printed terrain card stamps in a marked tile's corner: one
// stacked cube per elevation level ("e1".."e3"), a boxed X for impassable
// ("imp") and a boxed triangle for an obstacle ("obs").
const TILE_ICONS = new Set(['e1', 'e2', 'e3', 'imp', 'obs']);

export function tileIconUrl(stem) {
  return TILE_ICONS.has(stem) ? `/static/tiles/${stem}.png` : null;
}

export function elevationIconUrl(level) {
  return tileIconUrl(`e${level}`);
}

// The standees: the mech itself, cut out of its artwork by `assets.py` and
// standing on its tile. Same `slug` rule as the terrain, keyed by the frame's
// name -- which is what a frame in the view carries.
export function frameImageUrl(frameName) {
  return `/static/frames/${slug(frameName)}.png`;
}

const TOKEN_ART = {
  tower: { stem: 'Tower', states: 4 },
  reactor: { stem: 'PowerPlant', states: 2 },
  shiny: 'Shiny',
  fugitive: 'Fugitive',
  barricade: 'Barricade',
  gravitywell: 'GravityWell',
  portal: 'Portal',
  illusion: 'Illusion',
  real: 'Real',
  image: 'Image',
  drone: 'Swarm',
};

function slug(name) {
  return String(name || '').trim().replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}
