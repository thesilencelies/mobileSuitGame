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

const ELEV_FILL = ['#19212c', '#26323f', '#354557', '#48607a'];
const ELEV_EDGE = ['#101720', '#1b242f', '#26333f', '#33455a'];

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
    this.overlays = { reach: new Map(), los: new Set(), targets: new Set(), blocked: new Set() };
    this.selected = null;            // frame id
    this.acting = null;              // frame id currently resolving
    this.options = { los: false, threat: true, cards: false, coords: false };
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
    if (first) this.fit();
    this._dirty = true;
  }

  setOverlays(overlays) {
    this.overlays = { reach: new Map(), los: new Set(), targets: new Set(), blocked: new Set(), ...overlays };
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

    this._drawTiles(ctx, x0, y0, x1, y1, s);
    this._drawOverlays(ctx, x0, y0, x1, y1, s);
    if (this.options.cards) this._drawCardSeams(ctx, x0, y0, x1, y1, s);
    this._drawTokens(ctx, s);
    this._drawFrames(ctx, s);
    ctx.restore();

    if (this.zoomedIn) this._drawMinimap(ctx, w, h);
  }

  _drawTiles(ctx, x0, y0, x1, y1, s) {
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const t = this.tiles.get(`${x},${y}`);
        if (!t) continue;
        const elev = Math.max(0, Math.min(3, t.elev || 0));
        ctx.fillStyle = t.impassable ? '#080c11' : ELEV_FILL[elev];
        ctx.fillRect(x, y, 1, 1);

        if (t.impassable) {
          ctx.strokeStyle = '#232c38';
          ctx.lineWidth = 1.4 / s;
          ctx.beginPath();
          ctx.moveTo(x + 0.15, y + 0.15); ctx.lineTo(x + 0.85, y + 0.85);
          ctx.moveTo(x + 0.85, y + 0.15); ctx.lineTo(x + 0.15, y + 0.85);
          ctx.stroke();
        } else if (t.obstacle) {
          ctx.fillStyle = '#0f151d';
          this._roundRect(ctx, x + 0.16, y + 0.16, 0.68, 0.68, 0.12);
          ctx.fill();
          ctx.fillStyle = '#2c3a4a';
          this._roundRect(ctx, x + 0.16, y + 0.16, 0.68, 0.5, 0.1);
          ctx.fill();
        }
        if (elev > 0 && !t.impassable) {
          // A lit top edge reads as height at any zoom.
          ctx.fillStyle = 'rgba(255,255,255,0.10)';
          ctx.fillRect(x, y, 1, 0.09 + 0.03 * elev);
          if (s > 20) {
            ctx.fillStyle = 'rgba(230,240,255,0.5)';
            ctx.font = `${0.3}px system-ui, sans-serif`;
            ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
            ctx.fillText(String(elev), x + 0.94, y + 0.96);
          }
        }
        const obj = this.objectiveTiles.get(`${x},${y}`);
        if (obj) {
          ctx.strokeStyle = 'rgba(242,193,78,0.85)';
          ctx.lineWidth = 2 / s;
          ctx.strokeRect(x + 1.5 / s, y + 1.5 / s, 1 - 3 / s, 1 - 3 / s);
        } else if (t.objective) {
          ctx.fillStyle = 'rgba(242,193,78,0.5)';
          ctx.fillRect(x + 0.38, y + 0.38, 0.24, 0.24);
        }
        ctx.strokeStyle = ELEV_EDGE[elev];
        ctx.lineWidth = 1 / s;
        ctx.strokeRect(x + 0.5 / s, y + 0.5 / s, 1 - 1 / s, 1 - 1 / s);

        if (this.options.coords && s > 34) {
          ctx.fillStyle = 'rgba(200,215,235,0.35)';
          ctx.font = `${0.22}px system-ui, sans-serif`;
          ctx.textAlign = 'left'; ctx.textBaseline = 'top';
          ctx.fillText(`${x},${y}`, x + 0.06, y + 0.05);
        }
      }
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
    const { reach, los, targets } = this.overlays;
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
  }

  _drawTokens(ctx, s) {
    for (const token of this.view.tokens || []) {
      if (!token.pos || token.alive === false) continue;
      const { x, y } = token.pos;
      const colour = {
        reactor: '#f2c14e', tower: '#b9c6d6', shiny: '#ffe28a',
        fugitive: '#6fe3d0', egg: '#e9d7a0',
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

  _drawFrames(ctx, s) {
    const now = performance.now();
    for (const f of this.view.frames || []) {
      if (!f.pos) continue;
      const mine = f.seat === this.seat;
      const { x, y } = f.pos;
      const alive = f.alive;
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
      else if (t.elev > 0) ctx.fillStyle = ELEV_FILL[Math.min(3, t.elev)];
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
