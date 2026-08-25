// The 15x16 battlefield on a phone.
//
// The problem: 240 tiles on a ~390px-wide screen. A DOM table of 240 nodes
// that the browser scrolls is unreadable and untappable, so the board is a
// single <canvas> with its own camera:
//
//   * FIT shows the whole board at once (~24 px/tile) -- enough to read the
//     shape of the fight, not enough to tap accurately;
//   * one finger pans, two fingers pinch, double-tap toggles between FIT and
//     a tactical zoom (~52 px/tile) centred on what you tapped, so a tile is
//     always at least a fingertip wide when you actually have to tap one;
//   * a minimap appears whenever you are zoomed in past FIT, showing the
//     viewport rectangle and every frame, so you never lose the board;
//   * the legal destinations for a `move` decision come from the engine's own
//     `pending.options`, so the green tiles are exactly the legal ones -- the
//     client never guesses movement.
//
// Two ways to look at it, both kept (the `art` option):
//
//   * ABSTRACT -- the terrain markings on a flat dark ground, the fastest read
//     of the shape of the fight, which is what you want at FIT;
//   * ART -- the dealt terrain cards drawn behind the grid, clipped to their
//     own 3x4 tile block and turned 180 degrees for the half of the board its
//     owner laid out facing themselves, exactly as the cards sit on a table.
//
// The markings themselves are the same in both, and they are the *printed*
// ones: raised ground is drawn with the terrain card's own colour ramp, walls
// and glyphs (see below), so a rooftop looks the same on the phone as it does
// on the table.

import {
  frameImageUrl, terrainImageUrl, tokenImageUrl, tileIconUrl,
} from './api.js';
import { frameMark } from './cards.js';

// ------------------------------------------------------- the tile, as printed
//
// A marked tile is drawn the way the printed terrain card draws it, so a
// rooftop or a blocked tile looks the same on the table and on the phone.
// Everything here is lifted from `terrain_cards.py` (TERRAIN_STYLE = "full"):
//
//   * the colour ramp is `cityblue!N!citysteel` mixed in `generateCards.py`
//     (cityblue 105,156,255 -- citysteel 78,76,118), so e1 is a desaturated
//     steel blue and e3 the vivid glass-tower blue;
//   * it is laid over the photograph at `fill opacity=0.5`, not as a neutral
//     wash, so height reads as *blue* and the ground still shows through;
//   * and the height itself comes from the **walls**: each edge is drawn at
//     `ELEVATION_WALL_PER_LEVEL_PT` (3.5 pt) extra width per level this tile
//     stands above the neighbour across it. A tile flush with its neighbours
//     gets a hairline; one standing three levels proud of open ground gets a
//     band an eighth of the tile wide down that side. That is what fakes the
//     perspective onto a building's flank.
//
// Impassable and obstacle tiles come from the same place, and they are the
// reason the border is a routine rather than a `strokeRect`: the card gives a
// tile one colour and one base width from whichever code it carries, then adds
// the elevation walls on top of that.
//
//   * IMPASSIBLE_STYLE -- black at half opacity, and a 5 pt *red* border,
//     which is the whole of it: a tile you cannot enter is not shaded like one
//     you can.
//   * OBSTACLE_STYLE -- a yellow crosshatch at half opacity plus a dashed
//     yellow outline, and deliberately *no* colour or width of its own, so an
//     obstacle sitting on a rooftop keeps the rooftop's blue border and reads
//     as both. That is what the "these should not set the line style cause
//     they can appear at any elevation" comment in `terrain_cards.py` is for.
//
// Widths are in tile units: a printed tile is 2.06 cm, so 1 pt is 1/58.4 of a
// tile. The board's own tiles are square too, so the conversion is exact.
const ELEV_RGB = ['', '86,100,159', '94,124,200', '105,156,255'];
const ELEV_CSS = ['', '#56649f', '#5e7cc8', '#699cff'];

//: The card lays every fill and hatch on at half opacity, over its photograph.
const ELEV_ALPHA = 0.5;

//: `semithick` (0.6 pt) and ELEVATION_WALL_PER_LEVEL_PT (3.5 pt), in tiles.
const WALL_BASE = 0.0103;
const WALL_PER_DROP = 0.0599;

//: IMPASSIBLE_STYLE: `fill=black` at half opacity, `draw=red` at 5 pt.
const IMPASSABLE_FILL = 'rgba(0,0,0,0.5)';
const IMPASSABLE_CSS = '#ff0000';
const IMPASSABLE_WALL = 0.0856;

//: OBSTACLE_STYLE: a yellow crosshatch, and a 3 pt dashed yellow outline on
//: 2 mm / off 2 mm. The hatch is pgf's `crosshatch` -- hairlines at +/-45.
const OBSTACLE_CSS = 'rgba(255,255,0,0.5)';
const OBSTACLE_HATCH_STEP = 0.0856;
const OBSTACLE_HATCH_W = 0.0069;
const OBSTACLE_DASH_W = 0.0514;
const OBSTACLE_DASH = 0.0971;

//: The glyph is 0.6 cm in a 2.06 cm tile, inset by half its width plus the
//: base border -- `terrain_iconwidth_value` and the offsets beside it. A tile
//: carrying two of them stacks the second one width to the left, as the card's
//: `hoffset` loop does.
const TILE_ICON_W = 0.291;
const TILE_ICON_INSET = 0.156;

// Ground has no printed styling, so the abstract view keeps its own dark
// ground for it and lets the ramp above sit on that instead of a photograph.
const GROUND_FILL = '#19212c';
const GROUND_EDGE = '#101720';

//: Flat versions of the ramp over `GROUND_FILL`, for the minimap -- too small
//: for a wall or a glyph, so the colour has to carry the height on its own.
const ELEV_FLAT = ['#19212c', '#384266', '#3c4e7a', '#415e96'];

//: A terrain card is 3 tiles across and 4 down (rules; engine/terrain.py).
const CARD_COLS = 3;
const CARD_ROWS = 4;

export class BoardView {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.view = null;
    this.seat = 0;
    this.tiles = new Map();          // "x,y" -> tile
    this.w = 0; this.h = 0;
    this.cam = { x: 0, y: 0, scale: 24 };
    this.fitScale = 24;
    this.overlays = {
      reach: new Map(), los: new Set(), targets: new Set(), blocked: new Set(),
      deploy: new Set(), confirm: null,
    };
    this.selected = null;            // frame id
    this.acting = null;              // frame id currently resolving
    this.options = { los: false, threat: true, cards: false, coords: false, art: true };
    this.cards = [];                 // dealt terrain cards, with their art
    this._art = new Map();           // url -> HTMLImageElement (or null)
    this.onTapTile = () => {};
    this.onTapFrame = () => {};
    this._dirty = true;
    this._pointers = new Map();
    this._pinch = null;
    this._lastTap = 0;
    this._t0 = performance.now();
    this._bind();
    this._loop();
  }

  // ------------------------------------------------------------- data

  setView(view, seat) {
    const first = !this.view;
    this.view = view;
    this.seat = seat;
    const board = view.board || { width: 0, height: 0, tiles: [] };
    if (board.width !== this.w || board.height !== this.h) {
      this.w = board.width; this.h = board.height;
      this.fit();
    }
    this.tiles = new Map();
    for (const t of board.tiles || []) this.tiles.set(`${t.x},${t.y}`, t);
    this.objectiveTiles = new Map();
    for (const obj of board.objectives || []) {
      for (const [x, y] of obj.tiles || []) this.objectiveTiles.set(`${x},${y}`, obj);
    }
    this._layoutCards();
    if (first) this.fit();
    this._dirty = true;
  }

  /** Recover the dealt terrain cards from the tiles, so the art can be blitted.
   *
   * Every tile carries the name of the card it came from, and the deal is a
   * fixed grid of 3x4-tile cards (`engine/setup.py`), so a card is the block of
   * tiles at `(col*3, row*4)`. A block whose tiles disagree about their card is
   * not a card this client understands, and is left to the abstract renderer.
   *
   * The top half of the board is the seat that laid its cards out facing
   * itself, so its art is rotated 180 degrees -- the same thing you see looking
   * across the table at an opponent's terrain.
   */
  _layoutCards() {
    this.cards = [];
    if (!this.w || !this.h) return;
    if (this.w % CARD_COLS || this.h % CARD_ROWS) return;
    const cardRows = this.h / CARD_ROWS;
    for (let row = 0; row < cardRows; row++) {
      for (let col = 0; col < this.w / CARD_COLS; col++) {
        const x0 = col * CARD_COLS;
        const y0 = row * CARD_ROWS;
        const name = (this.tiles.get(`${x0},${y0}`) || {}).card;
        if (!name) continue;
        let uniform = true;
        for (let dy = 0; dy < CARD_ROWS && uniform; dy++) {
          for (let dx = 0; dx < CARD_COLS; dx++) {
            const t = this.tiles.get(`${x0 + dx},${y0 + dy}`);
            if (!t || t.card !== name) { uniform = false; break; }
          }
        }
        if (!uniform) continue;
        this.cards.push({
          name, x: x0, y: y0,
          rotated: row < Math.floor(cardRows / 2),
          url: terrainImageUrl(name),
        });
      }
    }
  }

  /** An <img> for a bundled asset, loaded once. `null` until it is ready. */
  _image(url) {
    if (!url) return null;
    if (this._art.has(url)) return this._art.get(url);
    const img = new Image();
    img.decoding = 'async';
    img.addEventListener('load', () => { this._dirty = true; });
    img.addEventListener('error', () => { this._art.set(url, null); }, { once: true });
    img.src = url;
    this._art.set(url, img);
    return img;
  }

  setOverlays(overlays) {
    this.overlays = {
      reach: new Map(), los: new Set(), targets: new Set(), blocked: new Set(),
      deploy: new Set(), confirm: null, ...overlays,
    };
    this._dirty = true;
  }

  setSelected(frameId) { this.selected = frameId; this._dirty = true; }
  setActing(frameId) { this.acting = frameId; this._dirty = true; }
  setOptions(options) { Object.assign(this.options, options); this._dirty = true; }
  invalidate() { this._dirty = true; }

  // ------------------------------------------------------------- camera

  get cssSize() {
    const rect = this.canvas.getBoundingClientRect();
    return { w: rect.width || 1, h: rect.height || 1 };
  }

  fit() {
    const { w, h } = this.cssSize;
    if (!this.w || !this.h) return;
    this.fitScale = Math.min(w / this.w, h / this.h) * 0.97;
    this.cam = { x: this.w / 2, y: this.h / 2, scale: this.fitScale };
    this._dirty = true;
  }

  get zoomedIn() { return this.cam.scale > this.fitScale * 1.12; }

  zoomBy(factor, anchor) {
    const before = anchor ? this.toBoard(anchor.x, anchor.y) : null;
    const min = this.fitScale * 0.9;
    this.cam.scale = Math.max(min, Math.min(110, this.cam.scale * factor));
    if (before) {
      const after = this.toBoard(anchor.x, anchor.y);
      this.cam.x += before.x - after.x;
      this.cam.y += before.y - after.y;
    }
    this._clamp();
    this._dirty = true;
  }

  centreOn(x, y, scale) {
    this.cam.x = x + 0.5;
    this.cam.y = y + 0.5;
    if (scale) this.cam.scale = scale;
    this._clamp();
    this._dirty = true;
  }

  tacticalZoom() { return Math.max(this.fitScale * 1.6, 52); }

  _clamp() {
    const { w, h } = this.cssSize;
    const halfW = w / (2 * this.cam.scale);
    const halfH = h / (2 * this.cam.scale);
    const padX = Math.max(0, halfW - this.w / 2);
    const padY = Math.max(0, halfH - this.h / 2);
    this.cam.x = Math.min(this.w - halfW + padX, Math.max(halfW - padX, this.cam.x));
    this.cam.y = Math.min(this.h - halfH + padY, Math.max(halfH - padY, this.cam.y));
  }

  toBoard(sx, sy) {
    const { w, h } = this.cssSize;
    return {
      x: (sx - w / 2) / this.cam.scale + this.cam.x,
      y: (sy - h / 2) / this.cam.scale + this.cam.y,
    };
  }

  toScreen(bx, by) {
    const { w, h } = this.cssSize;
    return {
      x: (bx - this.cam.x) * this.cam.scale + w / 2,
      y: (by - this.cam.y) * this.cam.scale + h / 2,
    };
  }

  // ------------------------------------------------------------- input

  _bind() {
    const c = this.canvas;
    c.addEventListener('pointerdown', (e) => {
      c.setPointerCapture(e.pointerId);
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY, x0: e.clientX, y0: e.clientY, t: performance.now(), moved: 0 });
      if (this._pointers.size === 2) this._startPinch();
    });
    c.addEventListener('pointermove', (e) => {
      const p = this._pointers.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x;
      const dy = e.clientY - p.y;
      p.moved += Math.abs(dx) + Math.abs(dy);
      p.x = e.clientX; p.y = e.clientY;
      if (this._pointers.size === 2) { this._updatePinch(); return; }
      if (this._pointers.size === 1) {
        this.cam.x -= dx / this.cam.scale;
        this.cam.y -= dy / this.cam.scale;
        this._clamp();
        this._dirty = true;
      }
    });
    const end = (e) => {
      const p = this._pointers.get(e.pointerId);
      this._pointers.delete(e.pointerId);
      if (this._pointers.size < 2) this._pinch = null;
      if (!p) return;
      const quick = performance.now() - p.t < 500;
      if (quick && p.moved < 12 && this._pointers.size === 0) this._tap(e);
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', (e) => { this._pointers.delete(e.pointerId); this._pinch = null; });
    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      this.zoomBy(e.deltaY < 0 ? 1.12 : 0.89,
        { x: e.clientX - rect.left, y: e.clientY - rect.top });
    }, { passive: false });
    window.addEventListener('resize', () => { this._resize(); this._dirty = true; });
  }

  _startPinch() {
    const [a, b] = [...this._pointers.values()];
    this._pinch = {
      dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
      scale: this.cam.scale,
    };
  }

  _updatePinch() {
    if (!this._pinch) { this._startPinch(); return; }
    const [a, b] = [...this._pointers.values()];
    const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
    const rect = this.canvas.getBoundingClientRect();
    const mid = { x: (a.x + b.x) / 2 - rect.left, y: (a.y + b.y) / 2 - rect.top };
    const target = this._pinch.scale * (dist / this._pinch.dist);
    this.zoomBy(target / this.cam.scale, mid);
  }

  _tap(e) {
    const rect = this.canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const now = performance.now();
    if (now - this._lastTap < 320) {          // double tap: fit <-> tactical
      this._lastTap = 0;
      const at = this.toBoard(sx, sy);
      if (this.zoomedIn) this.fit();
      else this.centreOn(Math.floor(at.x), Math.floor(at.y), this.tacticalZoom());
      return;
    }
    this._lastTap = now;
    const at = this.toBoard(sx, sy);
    const x = Math.floor(at.x);
    const y = Math.floor(at.y);
    if (x < 0 || y < 0 || x >= this.w || y >= this.h) return;
    const frame = this._frameAt(x, y);
    if (frame) this.onTapFrame(frame, x, y);
    else this.onTapTile(x, y);
  }

  _frameAt(x, y) {
    if (!this.view) return null;
    return (this.view.frames || []).find(
      (f) => f.alive && f.pos && f.pos.x === x && f.pos.y === y) || null;
  }

  // ------------------------------------------------------------- render

  _resize() {
    const { w, h } = this.cssSize;
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const pw = Math.round(w * dpr);
    const ph = Math.round(h * dpr);
    if (this.canvas.width !== pw || this.canvas.height !== ph) {
      // The decision sheet grows and shrinks under the board, so the canvas
      // changes size constantly. If we were showing the whole board, keep
      // showing the whole board rather than leaving it letterboxed.
      const wasFit = this.fitScale > 0
        && Math.abs(this.cam.scale - this.fitScale) < this.fitScale * 0.03;
      this.canvas.width = pw;
      this.canvas.height = ph;
      if (wasFit || this.fitScale <= 0 || !isFinite(this.fitScale)) this.fit();
      else this._clamp();
      this._dirty = true;
    }
    this._dpr = dpr;
  }

  _loop() {
    const step = () => {
      this._resize();
      if (this._dirty || this.acting) this._draw();
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  _draw() {
    this._dirty = false;
    const ctx = this.ctx;
    const { w, h } = this.cssSize;
    const dpr = this._dpr || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#080b0f';
    ctx.fillRect(0, 0, w, h);
    if (!this.view || !this.w) return;

    const s = this.cam.scale;
    ctx.save();
    ctx.translate(w / 2 - this.cam.x * s, h / 2 - this.cam.y * s);
    ctx.scale(s, s);

    const tl = this.toBoard(0, 0);
    const br = this.toBoard(w, h);
    const x0 = Math.max(0, Math.floor(tl.x) - 1);
    const y0 = Math.max(0, Math.floor(tl.y) - 1);
    const x1 = Math.min(this.w - 1, Math.ceil(br.x) + 1);
    const y1 = Math.min(this.h - 1, Math.ceil(br.y) + 1);

    if (this.options.art) this._drawTerrainArt(ctx, x0, y0, x1, y1);
    this._drawTiles(ctx, x0, y0, x1, y1, s);
    this._drawOverlays(ctx, x0, y0, x1, y1, s);
    if (this.options.cards) this._drawCardSeams(ctx, x0, y0, x1, y1, s);
    this._drawTokens(ctx, s);
    this._drawFrames(ctx, s);
    ctx.restore();

    if (this.zoomedIn) this._drawMinimap(ctx, w, h);
  }

  /** The dealt terrain cards, each clipped to its own 3x4 block of tiles. */
  _drawTerrainArt(ctx, x0, y0, x1, y1) {
    for (const card of this.cards) {
      if (card.x > x1 || card.x + CARD_COLS <= x0) continue;
      if (card.y > y1 || card.y + CARD_ROWS <= y0) continue;
      const img = this._image(card.url);
      if (!img || !img.complete || !img.naturalWidth) continue;
      ctx.save();
      // A half-pixel bleed on each side: neighbouring cards must not show a
      // seam of background between them when the camera lands off-pixel.
      const bleed = 0.5 / this.cam.scale;
      if (card.rotated) {
        ctx.translate(card.x + CARD_COLS / 2, card.y + CARD_ROWS / 2);
        ctx.rotate(Math.PI);
        ctx.translate(-CARD_COLS / 2, -CARD_ROWS / 2);
        ctx.drawImage(img, -bleed, -bleed,
          CARD_COLS + 2 * bleed, CARD_ROWS + 2 * bleed);
      } else {
        ctx.drawImage(img, card.x - bleed, card.y - bleed,
          CARD_COLS + 2 * bleed, CARD_ROWS + 2 * bleed);
      }
      ctx.restore();
    }
  }

  _drawTiles(ctx, x0, y0, x1, y1, s) {
    const art = this.options.art;
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const t = this.tiles.get(`${x},${y}`);
        if (!t) continue;
        const elev = Math.max(0, Math.min(3, t.elev || 0));
        // Ground first -- the photograph in ART, a flat dark tile otherwise --
        // and then the card's own fill over it at half opacity, which is
        // exactly what the printed card does over its own photo. Elevation
        // wins over impassable where a tile is both, because `_merge_style`
        // keeps the first non-default value and the elevation code comes first.
        if (!art) {
          ctx.fillStyle = GROUND_FILL;
          ctx.fillRect(x, y, 1, 1);
        }
        if (elev > 0) {
          ctx.fillStyle = `rgba(${ELEV_RGB[elev]},${ELEV_ALPHA})`;
          ctx.fillRect(x, y, 1, 1);
        } else if (t.impassable) {
          ctx.fillStyle = IMPASSABLE_FILL;
          ctx.fillRect(x, y, 1, 1);
        }
        if (t.obstacle) this._crosshatch(ctx, x, y);

        const obj = this.objectiveTiles.get(`${x},${y}`);
        if (obj) {
          ctx.strokeStyle = 'rgba(242,193,78,0.85)';
          ctx.lineWidth = 2 / s;
          ctx.strokeRect(x + 1.5 / s, y + 1.5 / s, 1 - 3 / s, 1 - 3 / s);
        } else if (t.objective) {
          ctx.fillStyle = 'rgba(242,193,78,0.5)';
          ctx.fillRect(x + 0.38, y + 0.38, 0.24, 0.24);
        }

        // The border last, so nothing is drawn over it: on a raised tile it
        // *is* the height, and on an impassable one it is the whole marking.
        this._drawTileBorder(ctx, x, y, t, elev, s);
        if (t.obstacle) this._dashedOutline(ctx, x, y);
        this._drawTileGlyphs(ctx, x, y, t, elev, s);

        if (this.options.coords && s > 34) {
          ctx.fillStyle = 'rgba(200,215,235,0.35)';
          ctx.font = `${0.22}px system-ui, sans-serif`;
          ctx.textAlign = 'left'; ctx.textBaseline = 'top';
          ctx.fillText(`${x},${y}`, x + 0.06, y + 0.05);
        }
      }
    }
  }

  /** How many levels this tile stands above the neighbour across one edge.
   *
   *  Off the board counts as ground, which is what the printed card assumes
   *  of its own edge -- but on the assembled board the neighbour is usually a
   *  real tile from the card next door, so a wall only appears where there is
   *  actually a drop.
   */
  _drop(x, y, elev) {
    const at = (px, py) => {
      const t = this.tiles.get(`${px},${py}`);
      return t ? Math.max(0, Math.min(3, t.elev || 0)) : 0;
    };
    return {
      top: Math.max(0, elev - at(x, y - 1)),
      bottom: Math.max(0, elev - at(x, y + 1)),
      left: Math.max(0, elev - at(x - 1, y)),
      right: Math.max(0, elev - at(x + 1, y)),
    };
  }

  /** One border, one edge at a time, the way `_tikz_square_lines` draws it.
   *
   *  The tile's code decides the colour and the base width -- a raised tile is
   *  its own blue at a hairline, an impassable one is red at 5 pt, plain ground
   *  is the grid line. On top of that each edge is widened by however far this
   *  tile stands above the neighbour across it, which is the wall.
   */
  _drawTileBorder(ctx, x, y, t, elev, s) {
    let css;
    let base;
    if (elev > 0) { css = ELEV_CSS[elev]; base = WALL_BASE; }
    else if (t.impassable) { css = IMPASSABLE_CSS; base = IMPASSABLE_WALL; }
    else {
      // Nothing printed here: the plain grid line, at a fixed pixel width so
      // it stays a hairline at any zoom rather than growing into a wall.
      ctx.strokeStyle = this.options.art ? 'rgba(8,12,18,0.45)' : GROUND_EDGE;
      ctx.lineWidth = 1 / s;
      ctx.strokeRect(x + 0.5 / s, y + 0.5 / s, 1 - 1 / s, 1 - 1 / s);
      return;
    }
    const drops = this._drop(x, y, elev);
    ctx.save();
    ctx.strokeStyle = css;
    ctx.lineCap = 'butt';
    for (const [edge, drop] of Object.entries(drops)) {
      const w = base + WALL_PER_DROP * drop;
      // Inset by half the width so the stroke stays inside the cell, exactly
      // as `_tikz_square_lines` does -- two neighbouring walls must not
      // overlap into a double-thick seam.
      const i = w / 2;
      ctx.lineWidth = w;
      ctx.beginPath();
      if (edge === 'top') { ctx.moveTo(x, y + i); ctx.lineTo(x + 1, y + i); }
      else if (edge === 'bottom') { ctx.moveTo(x, y + 1 - i); ctx.lineTo(x + 1, y + 1 - i); }
      else if (edge === 'left') { ctx.moveTo(x + i, y); ctx.lineTo(x + i, y + 1); }
      else { ctx.moveTo(x + 1 - i, y); ctx.lineTo(x + 1 - i, y + 1); }
      ctx.stroke();
    }
    ctx.restore();
  }

  /** An obstacle's yellow crosshatch: pgf's pattern, hairlines at +/-45. */
  _crosshatch(ctx, x, y) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, 1, 1);
    ctx.clip();
    ctx.translate(x + 0.5, y + 0.5);
    ctx.strokeStyle = OBSTACLE_CSS;
    ctx.lineWidth = OBSTACLE_HATCH_W;
    for (const angle of [Math.PI / 4, -Math.PI / 4]) {
      ctx.save();
      ctx.rotate(angle);
      for (let o = -0.75; o <= 0.75; o += OBSTACLE_HATCH_STEP) {
        ctx.beginPath();
        ctx.moveTo(o, -0.75);
        ctx.lineTo(o, 0.75);
        ctx.stroke();
      }
      ctx.restore();
    }
    ctx.restore();
  }

  /** An obstacle's dashed yellow outline -- the card's `postaction`. */
  _dashedOutline(ctx, x, y) {
    ctx.save();
    ctx.strokeStyle = OBSTACLE_CSS;
    ctx.lineWidth = OBSTACLE_DASH_W;
    ctx.setLineDash([OBSTACLE_DASH, OBSTACLE_DASH]);
    // Inset by half the stroke. The card lets it straddle the tile edge, but
    // the board fills tiles one at a time and a spilled outline would be
    // painted over by the neighbour drawn after it.
    const i = OBSTACLE_DASH_W / 2;
    ctx.strokeRect(x + i, y + i, 1 - 2 * i, 1 - 2 * i);
    ctx.restore();
  }

  /** The glyphs the card stamps in the tile's bottom-right corner.
   *
   *  More than one code on a tile means more than one glyph, and the card lays
   *  them out right to left from that corner, so this does too.
   */
  _drawTileGlyphs(ctx, x, y, t, elev, s) {
    if (s < 20) return;                 // smaller than a few pixels: pointless
    const stems = [];
    if (elev > 0) stems.push(`e${elev}`);
    if (t.impassable) stems.push('imp');
    if (t.obstacle) stems.push('obs');
    let cx = x + 1 - TILE_ICON_INSET;
    const cy = y + 1 - TILE_ICON_INSET;
    for (const stem of stems) {
      const art = this._image(tileIconUrl(stem));
      if (art && art.complete && art.naturalWidth) {
        ctx.save();
        // The glyph is black line art, drawn on a bright aerial photograph on
        // the card. Here it can land on a dark tile, so it gets a pale halo
        // rather than a repaint -- the shape stays the printed one.
        ctx.shadowColor = 'rgba(232,242,255,0.85)';
        ctx.shadowBlur = 3;
        const w = TILE_ICON_W;
        const h = w * (art.naturalHeight / art.naturalWidth);
        ctx.drawImage(art, cx - w / 2, cy - h / 2, w, h);
        ctx.restore();
      }
      cx -= TILE_ICON_W;
      if (cx < TILE_ICON_INSET) break;  // no room left in the row
    }
  }

  _drawCardSeams(ctx, x0, y0, x1, y1, s) {
    ctx.strokeStyle = 'rgba(150,180,220,0.30)';
    ctx.lineWidth = 1.6 / s;
    ctx.beginPath();
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const t = this.tiles.get(`${x},${y}`);
        if (!t) continue;
        const right = this.tiles.get(`${x + 1},${y}`);
        const below = this.tiles.get(`${x},${y + 1}`);
        if (right && right.card !== t.card) { ctx.moveTo(x + 1, y); ctx.lineTo(x + 1, y + 1); }
        if (below && below.card !== t.card) { ctx.moveTo(x, y + 1); ctx.lineTo(x + 1, y + 1); }
      }
    }
    ctx.stroke();
  }

  _drawOverlays(ctx, x0, y0, x1, y1, s) {
    const { reach, los, targets, deploy, confirm } = this.overlays;
    if (deploy && deploy.size) {
      const pulse = 0.5 + 0.5 * Math.sin((performance.now() - this._t0) / 420);
      for (const key of deploy) {
        const [x, y] = key.split(',').map(Number);
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        ctx.fillStyle = `rgba(120,200,255,${0.16 + 0.10 * pulse})`;
        ctx.fillRect(x, y, 1, 1);
        ctx.strokeStyle = 'rgba(140,215,255,0.8)';
        ctx.lineWidth = 1.4 / s;
        ctx.strokeRect(x + 1 / s, y + 1 / s, 1 - 2 / s, 1 - 2 / s);
      }
    }
    if (this.options.los && los && los.size) {
      ctx.fillStyle = 'rgba(255,110,90,0.13)';
      for (const key of los) {
        const [x, y] = key.split(',').map(Number);
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        ctx.fillRect(x, y, 1, 1);
      }
    }
    if (reach && reach.size) {
      for (const [key, cost] of reach) {
        const [x, y] = key.split(',').map(Number);
        if (x < x0 || x > x1 || y < y0 || y > y1) continue;
        ctx.fillStyle = cost === 0 ? 'rgba(78,201,138,0.16)' : 'rgba(78,201,138,0.30)';
        ctx.fillRect(x, y, 1, 1);
        ctx.strokeStyle = 'rgba(78,201,138,0.75)';
        ctx.lineWidth = 1.4 / s;
        ctx.strokeRect(x + 1 / s, y + 1 / s, 1 - 2 / s, 1 - 2 / s);
        if (s > 30) {
          ctx.fillStyle = 'rgba(220,255,235,0.85)';
          ctx.font = `700 ${0.3}px system-ui, sans-serif`;
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          ctx.fillText(cost === 0 ? 'stay' : String(cost), x + 0.5, y + 0.72);
        }
      }
    }
    if (targets && targets.size) {
      const pulse = 0.5 + 0.5 * Math.sin((performance.now() - this._t0) / 260);
      for (const key of targets) {
        const [x, y] = key.split(',').map(Number);
        ctx.strokeStyle = `rgba(255,93,93,${0.55 + 0.45 * pulse})`;
        ctx.lineWidth = 3 / s;
        ctx.strokeRect(x + 2 / s, y + 2 / s, 1 - 4 / s, 1 - 4 / s);
      }
    }
    if (confirm) this._drawConfirm(ctx, confirm, s);
  }

  /** The tile a tap has *proposed*, waiting for the second tap that commits it.
   *
   *  A phone makes a misfire far too cheap, and a move cannot be taken back,
   *  so a destination is picked twice: once to say where, once to mean it. The
   *  marker has to be unmistakable at FIT as well as zoomed in, hence the
   *  ring, the corner ticks and the pulse rather than a subtle tint.
   */
  _drawConfirm(ctx, confirm, s) {
    const { x, y } = confirm;
    const pulse = 0.5 + 0.5 * Math.sin((performance.now() - this._t0) / 190);
    ctx.save();
    ctx.fillStyle = `rgba(242,193,78,${0.18 + 0.14 * pulse})`;
    ctx.fillRect(x, y, 1, 1);
    ctx.strokeStyle = `rgba(255,214,110,${0.75 + 0.25 * pulse})`;
    ctx.lineWidth = 3.5 / s;
    this._roundRect(ctx, x + 0.06, y + 0.06, 0.88, 0.88, 0.16);
    ctx.stroke();
    // Corner ticks: at FIT the ring alone is only a few pixels across.
    ctx.lineWidth = 2.5 / s;
    ctx.beginPath();
    for (const [cx, cy, dx, dy] of [
      [0.02, 0.02, 1, 1], [0.98, 0.02, -1, 1],
      [0.02, 0.98, 1, -1], [0.98, 0.98, -1, -1]]) {
      ctx.moveTo(x + cx + 0.26 * dx, y + cy);
      ctx.lineTo(x + cx, y + cy);
      ctx.lineTo(x + cx, y + cy + 0.26 * dy);
    }
    ctx.stroke();
    if (s > 26) {
      ctx.fillStyle = 'rgba(255,236,180,0.95)';
      ctx.font = `800 ${0.24}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('TAP AGAIN', x + 0.5, y + 0.5);
    }
    ctx.restore();
  }

  _drawTokens(ctx, s) {
    for (const token of this.view.tokens || []) {
      if (!token.pos || token.alive === false) continue;
      const { x, y } = token.pos;
      // The numbered piece art encodes remaining hit points, so an undamaged
      // Tower and a Tower one hit from gone are different pictures -- which is
      // a damage read-out you can take in without reading a number.
      // Ephemeral Images ship as three different pictures on purpose: the
      // side projecting them sees which one it is standing on and which are
      // decoys, and everyone else sees three identical images.
      let kind = token.kind;
      if (kind === 'image' && token.owner === this.seat) {
        kind = token.real ? 'real' : 'illusion';
      }
      const art = this.options.art
        ? this._image(tokenImageUrl(kind, token.hp)) : null;
      if (art && art.complete && art.naturalWidth) {
        ctx.drawImage(art, x + 0.04, y + 0.04, 0.92, 0.92);
        if (token.carrier) {
          ctx.strokeStyle = 'rgba(242,193,78,0.9)';
          ctx.lineWidth = 2 / s;
          this._roundRect(ctx, x + 0.06, y + 0.06, 0.88, 0.88, 0.16);
          ctx.stroke();
        }
        continue;
      }
      const colour = {
        reactor: '#f2c14e', tower: '#b9c6d6', shiny: '#ffe28a',
        fugitive: '#6fe3d0', egg: '#e9d7a0',
        image: '#b9a6ff', drone: '#8fe0a0',
      }[token.kind] || '#c8b6ff';
      ctx.beginPath();
      ctx.arc(x + 0.5, y + 0.5, 0.3, 0, Math.PI * 2);
      ctx.fillStyle = colour;
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.5 / s;
      ctx.strokeStyle = 'rgba(10,13,18,0.9)';
      ctx.stroke();
      if (token.maxHp > 1 && s > 18) {
        ctx.fillStyle = '#0a0d12';
        ctx.font = `700 ${0.34}px system-ui, sans-serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(String(token.hp), x + 0.5, y + 0.54);
      }
    }
  }

  /** Every frame on the board, back to front.
   *
   *  With art on, a frame is a **standee**: the mech itself, cut out of its
   *  card artwork, standing on the bottom edge of its tile. It is drawn taller
   *  than one tile because a mech that fits inside a 24-pixel square is a
   *  smudge -- so the drawing order matters, and frames are painted from the
   *  top of the board down, letting a nearer standee overlap the one behind
   *  it exactly as a model would on a table.
   *
   *  With art off you get the abstract counter, which is still the faster read
   *  at FIT: a coloured box with three letters and a damage strip.
   */
  _drawFrames(ctx, s) {
    const now = performance.now();
    const order = (this.view.frames || [])
      .filter((f) => f.pos)
      .slice()
      .sort((a, b) => (a.pos.y - b.pos.y) || (a.pos.x - b.pos.x));
    for (const f of order) {
      const mine = f.seat === this.seat;
      const { x, y } = f.pos;
      const alive = f.alive;
      const art = this.options.art ? this._image(frameImageUrl(f.name)) : null;
      const standee = !!(art && art.complete && art.naturalWidth);
      ctx.globalAlpha = alive ? 1 : 0.35;

      if (f.id === this.acting) {
        const pulse = 0.5 + 0.5 * Math.sin((now - this._t0) / 200);
        ctx.strokeStyle = `rgba(242,193,78,${0.4 + 0.6 * pulse})`;
        ctx.lineWidth = 3.5 / s;
        this._roundRect(ctx, x + 0.02, y + 0.02, 0.96, 0.96, 0.2);
        ctx.stroke();
      }
      if (f.id === this.selected) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2.5 / s;
        this._roundRect(ctx, x + 0.04, y + 0.04, 0.92, 0.92, 0.18);
        ctx.stroke();
      }

      if (standee) {
        this._drawStandee(ctx, f, art, s, mine);
      } else {
        ctx.fillStyle = mine ? '#1d5a8f' : '#8f2626';
        this._roundRect(ctx, x + 0.11, y + 0.11, 0.78, 0.78, 0.16);
        ctx.fill();
        ctx.strokeStyle = mine ? '#3fa7ff' : '#ff5d5d';
        ctx.lineWidth = 2 / s;
        this._roundRect(ctx, x + 0.11, y + 0.11, 0.78, 0.78, 0.16);
        ctx.stroke();

        if (s > 14) {
          ctx.fillStyle = '#f2f7ff';
          ctx.font = `800 ${0.3}px system-ui, sans-serif`;
          ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
          // Left of centre: the right-hand strip is the damage read-out.
          ctx.fillText(abbrev(f.name), x + 0.45, y + 0.5);
        }
      }

      // Damage: three ticks down the right edge, High at the top.
      const zones = ['High', 'Mid', 'Low'];
      for (let i = 0; i < 3; i++) {
        const z = zones[i];
        const armour = (f.armour && f.armour[z]) || 0;
        const dmg = (f.damage && f.damage[z]) || 0;
        if (!armour) continue;
        const top = y + 0.14 + i * 0.25;
        ctx.fillStyle = 'rgba(6,9,13,0.75)';
        ctx.fillRect(x + 0.80, top, 0.09, 0.21);
        const share = Math.min(1, dmg / armour);
        if (share > 0) {
          ctx.fillStyle = (f.lastHit && f.lastHit[z]) ? '#ffb04d' : '#ff5d5d';
          ctx.fillRect(x + 0.80, top + 0.21 * (1 - share), 0.09, 0.21 * share);
        }
      }
      // Two frames of the same model look *identical* as standees, so the
      // ordinal off the frame's id is not decoration -- it is the only thing
      // telling them apart.
      this._drawFrameMark(ctx, f, s);
      if (!alive) {
        ctx.strokeStyle = '#ff8f8f';
        ctx.lineWidth = 2.5 / s;
        ctx.beginPath();
        ctx.moveTo(x + 0.2, y + 0.2); ctx.lineTo(x + 0.8, y + 0.8);
        ctx.moveTo(x + 0.8, y + 0.2); ctx.lineTo(x + 0.2, y + 0.8);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
  }

  /** The mech itself, standing on its tile, on a seat-coloured base. */
  _drawStandee(ctx, f, art, s, mine) {
    const { x, y } = f.pos;
    // The base does the work the counter used to: it says whose it is, and it
    // keeps the frame findable when the standee is a dark silhouette against
    // dark terrain.
    ctx.save();
    ctx.beginPath();
    ctx.ellipse(x + 0.5, y + 0.80, 0.38, 0.15, 0, 0, Math.PI * 2);
    ctx.fillStyle = mine ? 'rgba(29,90,143,0.85)' : 'rgba(143,38,38,0.85)';
    ctx.fill();
    ctx.lineWidth = 2 / s;
    ctx.strokeStyle = mine ? '#3fa7ff' : '#ff5d5d';
    ctx.stroke();

    // Fit the art into 1.15 tiles tall, standing on the base, never wider than
    // the tile -- a wide mech must not cover its neighbours' tiles. The height
    // cap is what keeps the *tall, thin* designs in proportion: they are the
    // ones the width cap never bites on, so a generous cap let them tower over
    // the squat mechs beside them for no reason but their silhouette.
    const maxH = 1.15;
    const maxW = 1.0;
    const ratio = art.naturalWidth / art.naturalHeight;
    let h = maxH;
    let w = h * ratio;
    if (w > maxW) { w = maxW; h = w / ratio; }
    ctx.shadowColor = 'rgba(0,0,0,0.55)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 1;
    ctx.drawImage(art, x + 0.5 - w / 2, y + 0.86 - h, w, h);
    ctx.restore();
  }

  /** The ordinal that tells one Kuwagata from the other, top-left of the tile. */
  _drawFrameMark(ctx, f, s) {
    const mark = frameMark(f.id);
    if (!mark || s < 16) return;
    const { x, y } = f.pos;
    ctx.save();
    ctx.fillStyle = 'rgba(9,13,19,0.88)';
    this._roundRect(ctx, x + 0.04, y + 0.04, 0.30, 0.24, 0.06);
    ctx.fill();
    ctx.strokeStyle = 'rgba(242,193,78,0.9)';
    ctx.lineWidth = 1.4 / s;
    this._roundRect(ctx, x + 0.04, y + 0.04, 0.30, 0.24, 0.06);
    ctx.stroke();
    ctx.fillStyle = '#ffe6a8';
    ctx.font = `800 ${0.19}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(mark, x + 0.19, y + 0.17);
    ctx.restore();
  }

  _drawMinimap(ctx, w, h) {
    const pad = 8;
    const maxW = 78;
    const cell = Math.min(maxW / this.w, 120 / this.h);
    const mw = this.w * cell;
    const mh = this.h * cell;
    const ox = w - mw - pad;
    const oy = h - mh - pad;
    ctx.save();
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = 'rgba(10,13,18,0.9)';
    this._roundRect(ctx, ox - 4, oy - 4, mw + 8, mh + 8, 6);
    ctx.fill();
    ctx.strokeStyle = '#263041';
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.fillStyle = '#1b2531';
    ctx.fillRect(ox, oy, mw, mh);
    for (const [key, t] of this.tiles) {
      const [x, y] = key.split(',').map(Number);
      if (t.impassable) ctx.fillStyle = '#0a0e13';
      else if (t.elev > 0) ctx.fillStyle = ELEV_FLAT[Math.min(3, t.elev)];
      else continue;
      ctx.fillRect(ox + x * cell, oy + y * cell, cell, cell);
    }
    for (const [key] of this.objectiveTiles) {
      const [x, y] = key.split(',').map(Number);
      ctx.fillStyle = 'rgba(242,193,78,0.6)';
      ctx.fillRect(ox + x * cell, oy + y * cell, cell, cell);
    }
    for (const f of this.view.frames || []) {
      if (!f.pos || !f.alive) continue;
      ctx.fillStyle = f.seat === this.seat ? '#3fa7ff' : '#ff5d5d';
      ctx.fillRect(ox + f.pos.x * cell - 0.5, oy + f.pos.y * cell - 0.5, cell + 1, cell + 1);
    }
    const tl = this.toBoard(0, 0);
    const br = this.toBoard(w, h);
    ctx.strokeStyle = '#e6edf3';
    ctx.lineWidth = 1.2;
    ctx.strokeRect(
      ox + Math.max(0, tl.x) * cell,
      oy + Math.max(0, tl.y) * cell,
      Math.min(this.w, br.x - Math.max(0, tl.x)) * cell,
      Math.min(this.h, br.y - Math.max(0, tl.y)) * cell,
    );
    ctx.restore();
  }

  _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
}

export function abbrev(name) {
  const parts = String(name || '').split(/[\s-]+/).filter(Boolean);
  if (!parts.length) return '?';
  if (parts.length === 1) return parts[0].slice(0, 3).toUpperCase();
  return (parts[0][0] + parts[1][0] + (parts[1][1] || '')).toUpperCase();
}
