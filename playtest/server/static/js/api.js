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
};

// Card art is named "{Group}_{Name}.png" -- the same string as the engine's
// card key. Those filenames contain spaces, so the key must be encoded or the
// request 404s. `width` asks for a downscaled copy: never ship the 378x537
// print-density original to a phone.
export function cardImageUrl(key, width = 240) {
  return `/api/card-image/${encodeURIComponent(key)}?w=${width}`;
}
