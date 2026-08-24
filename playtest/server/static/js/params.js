// AI parameter controls, built entirely from `GET /api/ai/params`.
//
// Nothing here knows the name of a single parameter: the schema decides what
// exists, what it is called, its range and its help text. Workstream D can add
// a parameter and it appears in both the new-game screen and the settings
// drawer without a line changing in this file.

import { escapeHtml } from './cards.js';

export class ParamForm {
  /**
   * @param {HTMLElement} host        where the controls go
   * @param {HTMLElement} presetHost  where preset chips go (may be null)
   */
  constructor(host, presetHost) {
    this.host = host;
    this.presetHost = presetHost;
    this.schema = { params: [], presets: {} };
    this.values = {};
    this.preset = null;
    this.onChange = () => {};
  }

  setSchema(schema) {
    this.schema = schema || { params: [], presets: {} };
    this.values = {};
    for (const p of this.schema.params || []) {
      if (p.default !== undefined && p.default !== null) this.values[p.name] = p.default;
    }
    this.preset = this.schema.defaultPreset || null;
    this.render();
  }

  /** Exactly what to POST as `aiParams`. */
  payload() {
    const out = { ...this.values };
    if (this.preset) out.preset = this.preset;
    return out;
  }

  applyPreset(name) {
    this.preset = name;
    const values = (this.schema.presets || {})[name] || {};
    for (const p of this.schema.params || []) {
      const fromPreset = values[p.name];
      this.values[p.name] = fromPreset !== undefined ? fromPreset : p.default;
    }
    this.render();
    this.onChange(this.payload());
  }

  render() {
    if (this.presetHost) {
      this.presetHost.innerHTML = '';
      const names = Object.keys(this.schema.presets || {});
      for (const name of names) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'preset';
        chip.textContent = name;
        if (name === this.preset) chip.dataset.on = '1';
        chip.addEventListener('click', () => this.applyPreset(name));
        this.presetHost.appendChild(chip);
      }
      this.presetHost.hidden = names.length === 0;
    }

    this.host.innerHTML = '';
    for (const p of this.schema.params || []) {
      this.host.appendChild(this._control(p));
    }
    if (!(this.schema.params || []).length) {
      const empty = document.createElement('p');
      empty.className = 'hint';
      empty.textContent = 'The AI exposes no tunable parameters.';
      this.host.appendChild(empty);
    }
  }

  _control(p) {
    const wrap = document.createElement('div');
    wrap.className = 'param';
    const value = this.values[p.name];

    const top = document.createElement('div');
    top.className = 'param-top';
    top.innerHTML = `<b>${escapeHtml(p.label || p.name)}</b>
                     <span class="param-val"></span>`;
    const readout = top.querySelector('.param-val');
    wrap.appendChild(top);

    const commit = (v) => {
      this.values[p.name] = v;
      this.preset = null;                    // a hand-tweaked set is not a preset
      if (this.presetHost) {
        for (const chip of this.presetHost.children) delete chip.dataset.on;
      }
      this.onChange(this.payload());
    };

    if (p.type === 'bool') {
      const label = document.createElement('label');
      label.className = 'toggle';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !!value;
      box.addEventListener('change', () => { commit(box.checked); readout.textContent = box.checked ? 'on' : 'off'; });
      label.append(box, document.createTextNode(p.help || ''));
      readout.textContent = value ? 'on' : 'off';
      wrap.appendChild(label);
      return wrap;
    }

    if (p.type === 'choice' && Array.isArray(p.options)) {
      const select = document.createElement('select');
      for (const option of p.options) {
        const el = document.createElement('option');
        el.value = String(option);
        el.textContent = String(option);
        if (String(option) === String(value)) el.selected = true;
        select.appendChild(el);
      }
      select.addEventListener('change', () => { commit(select.value); readout.textContent = select.value; });
      readout.textContent = value === undefined ? '' : String(value);
      wrap.appendChild(select);
      if (p.help) wrap.appendChild(help(p.help));
      return wrap;
    }

    if (p.min === undefined || p.max === undefined) {
      const input = document.createElement('input');
      input.type = 'text';
      input.value = value === undefined ? '' : String(value);
      input.addEventListener('change', () => {
        const num = Number(input.value);
        commit(Number.isFinite(num) && input.value.trim() !== '' ? num : input.value);
        readout.textContent = input.value;
      });
      readout.textContent = value === undefined ? '' : String(value);
      wrap.appendChild(input);
      if (p.help) wrap.appendChild(help(p.help));
      return wrap;
    }

    const isInt = p.type === 'int';
    const slider = document.createElement('input');
    slider.type = 'range';
    slider.min = String(p.min);
    slider.max = String(p.max);
    slider.step = String(p.step || (isInt ? 1 : 0.05));
    slider.value = String(value === undefined ? p.min : value);
    const show = () => {
      const num = Number(slider.value);
      readout.textContent = isInt ? String(Math.round(num)) : num.toFixed(2);
    };
    slider.addEventListener('input', show);
    slider.addEventListener('change', () => {
      const num = Number(slider.value);
      commit(isInt ? Math.round(num) : num);
    });
    show();
    wrap.appendChild(slider);
    if (p.help) wrap.appendChild(help(p.help));
    return wrap;
  }
}

function help(text) {
  const el = document.createElement('div');
  el.className = 'help';
  el.textContent = text;
  return el;
}
