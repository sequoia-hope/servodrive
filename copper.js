/* copper.js — the copper viewer on index.html and single.html (the PCB tab).
 *
 * Everything it draws comes from img/layers/<board>/layers.json, written by
 * tools/plot_layers.py: one SVG per layer, all cropped to the same rectangle
 * so they stack in register, plus the geometry needed to put the board body
 * under them and to turn a cursor position into a radius and an angle about
 * the shaft axis. Re-run the tool after routing and this follows.
 */
(() => {
  const root = document.getElementById('copper');
  if (!root) return;
  const DIR = root.dataset.dir || 'img/layers/a/';
  const $ = (sel, el = root) => el.querySelector(sel);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const MAXZOOM = 16;

  fetch(DIR + 'layers.json')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(build)
    .catch(e => {
      $('#cu-fallback').innerHTML = 'The layer plots are not built yet — run ' +
        '<code>python3 tools/plot_layers.py</code>. (' + e + ')';
    });

  function build(meta) {
    const [, , vw, vh] = meta.view_mm;          // the crop, in mm
    const [bx, by] = meta.centre_mm;            // board centre inside the crop
    const copper = meta.layers.filter(l => l.kind === 'copper');
    const over = meta.layers.filter(l => l.kind !== 'copper');
    const rows = copper.concat(over);           // panel order, and key order
    const state = {
      sel: copper[0].slug, vis: {}, dim: 0.16, mirror: false,
      s: 1, tx: 0, ty: 0, grid: false,
    };
    rows.forEach(l => state.vis[l.slug] = l.kind === 'copper' || l.kind === 'outline');

    // ---- the plate, and one img per layer ------------------------------
    const plate = $('#cu-plate'), pan = $('#cu-pan'), flip = $('#cu-flip');
    plate.style.aspectRatio = vw + ' / ' + vh;
    plate.style.setProperty('--ar', vw / vh);                // style.css caps the height by it
    if (meta.plate) $('.cu').style.setProperty('--plate', meta.plate);   // the holes' colour
    const body = el('div', 'cu-body');
    Object.assign(body.style, {
      left: pct(bx - meta.dia_mm / 2, vw), top: pct(by - meta.dia_mm / 2, vh),
      width: pct(meta.dia_mm, vw), height: pct(meta.dia_mm, vh),
    });
    flip.appendChild(body);
    const imgs = {};
    for (const l of rows) {
      const img = el('img');
      img.alt = l.name + ' of ' + meta.board;
      img.dataset.src = DIR + l.file;           // loaded when first shown
      flip.appendChild(img);
      imgs[l.slug] = img;
    }

    // ---- the panel -----------------------------------------------------
    const box = $('#cu-layers');
    rows.forEach((l, i) => {
      if (l === over[0]) box.appendChild(el('div', 'cu-head', 'Overlay'));
      else if (i === 0) box.appendChild(el('div', 'cu-head', 'Copper, front to back'));
      const row = el('div', 'cu-lay');
      row.dataset.slug = l.slug;
      row.innerHTML =
        `<span class="k">${i + 1}</span>` +
        `<input type="radio" name="cu-sel" id="cu-r-${l.slug}" title="draw this layer on top, opaque">` +
        `<input type="checkbox" id="cu-v-${l.slug}" title="show this layer at all">` +
        `<span class="sw" style="background:${l.color}"></span>` +
        `<label for="cu-r-${l.slug}"><span class="nm">${l.name}</span>` +
        `<span class="fx">${facts(l)}</span></label>`;
      box.appendChild(row);
      row.querySelector('input[type=radio]').onchange = () => { state.sel = l.slug; draw(); };
      row.querySelector('input[type=checkbox]').onchange = e => {
        state.vis[l.slug] = e.target.checked; draw();
      };
    });

    $('#cu-stats').textContent =
      `${sum(copper, 'tracks')} track segments · ` +
      `${Math.round(sum(copper, 'length_mm'))} mm of routed copper · ` +
      `${meta.vias} vias · ${copper.length} layers · Ø${meta.dia_mm} mm · ` +
      `plotted ${meta.generated}`;
    $('#cu-files').innerHTML = 'Layer files: ' + meta.layers.map(l =>
      `<a href="${DIR + l.file}">${l.name}</a>`).join(' · ');

    // ---- controls ------------------------------------------------------
    const dim = $('#cu-dim'), mir = $('#cu-mirror');
    dim.value = state.dim * 100;
    dim.oninput = () => { state.dim = dim.value / 100; draw(); };
    mir.onchange = () => { state.mirror = mir.checked; draw(); };
    root.addEventListener('click', e => {
      const act = e.target.closest('button')?.dataset.act;
      if (!act) return;
      e.preventDefault();
      if (act === 'all') rows.forEach(l => state.vis[l.slug] = true);
      else if (act === 'none') rows.forEach(l => state.vis[l.slug] = l.slug === state.sel);
      else if (act === 'solo') rows.forEach(l => state.vis[l.slug] =
        l.slug === state.sel || l.kind === 'outline');
      else if (act === 'in') zoom(state.s * 1.5, plate.clientWidth / 2, plate.clientHeight / 2);
      else if (act === 'out') zoom(state.s / 1.5, plate.clientWidth / 2, plate.clientHeight / 2);
      else if (act === 'fit') { state.s = 1; state.tx = state.ty = 0; }
      else if (act === 'grid') { state.grid = !state.grid; grid(); }
      draw();
    });

    // ---- pan, zoom, and the cursor readout ------------------------------
    let drag = null;
    plate.addEventListener('pointerdown', e => {
      drag = { x: e.clientX, y: e.clientY, tx: state.tx, ty: state.ty };
      plate.setPointerCapture(e.pointerId);
      plate.classList.add('drag');
    });
    plate.addEventListener('pointerup', e => {
      drag = null; plate.classList.remove('drag');
      plate.releasePointerCapture(e.pointerId);
    });
    plate.addEventListener('pointermove', e => {
      const r = plate.getBoundingClientRect();
      if (drag) {
        state.tx = drag.tx + (e.clientX - drag.x);
        state.ty = drag.ty + (e.clientY - drag.y);
        place();
      }
      readout(e.clientX - r.left, e.clientY - r.top, r);
    });
    plate.addEventListener('pointerleave', () => { $('#cu-read').textContent = ''; });
    plate.addEventListener('wheel', e => {
      e.preventDefault();
      const r = plate.getBoundingClientRect();
      zoom(state.s * Math.exp(-e.deltaY * 0.0018), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    plate.addEventListener('dblclick', () => {
      state.s = 1; state.tx = state.ty = 0; place();
    });

    document.addEventListener('keydown', e => {
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.metaKey || e.ctrlKey) return;
      const r = root.getBoundingClientRect();                 // only when on screen
      if (!r.height || r.bottom < 0 || r.top > innerHeight) return;   // (or its tab is shut)
      const n = parseInt(e.key, 10);
      if (n >= 1 && n <= rows.length) {
        state.sel = rows[n - 1].slug; state.vis[state.sel] = true;
      } else if (e.key === 'm') { state.mirror = mir.checked = !state.mirror; }
      else if (e.key === 's') {
        rows.forEach(l => state.vis[l.slug] = l.slug === state.sel || l.kind === 'outline');
      } else if (e.key === 'a') { rows.forEach(l => state.vis[l.slug] = true); }
      else if (e.key === '0' || e.key === 'Escape') { state.s = 1; state.tx = state.ty = 0; }
      else return;
      e.preventDefault();
      draw();
    });

    function zoom(next, ax, ay) {
      const s2 = clamp(next, 1, MAXZOOM);
      state.tx = ax - (ax - state.tx) * s2 / state.s;
      state.ty = ay - (ay - state.ty) * s2 / state.s;
      state.s = s2;
      place();
    }

    // Keep the board inside the plate: at fit it is pinned, zoomed in it may
    // move until an edge would leave the frame.
    function place() {
      const w = plate.clientWidth, h = plate.clientHeight;
      state.tx = clamp(state.tx, w * (1 - state.s), 0);
      state.ty = clamp(state.ty, h * (1 - state.s), 0);
      pan.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.s})`;
      flip.style.transform = state.mirror ? 'scaleX(-1)' : '';
      $('#cu-zoom').textContent = state.s.toFixed(1) + '×';
      const mmpx = vw / (plate.clientWidth * state.s);        // scale bar
      const nice = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50].reduce((a, b) =>
        Math.abs(b - 90 * mmpx) < Math.abs(a - 90 * mmpx) ? b : a);
      $('#cu-bar').style.width = (nice / mmpx) + 'px';
      $('#cu-barlab').textContent = (nice < 1 ? nice.toFixed(1) : nice) + ' mm';
      $('#cu-back').textContent = state.mirror ? 'from the back' : '';
    }

    function readout(px, py, r) {
      let x = (px - state.tx) / state.s / r.width * vw;        // crop mm
      const y = (py - state.ty) / state.s / r.height * vh;
      if (state.mirror) x = vw - x;
      const dx = x - bx, dy = by - y;                          // board mm, y up
      const ang = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
      $('#cu-read').textContent =
        `x ${dx.toFixed(2)}  y ${dy.toFixed(2)}  ·  r ${Math.hypot(dx, dy).toFixed(2)} mm  ` +
        `θ ${ang.toFixed(1)}°`;
    }

    function draw() {
      const back = state.mirror;
      rows.forEach((l, i) => {
        const img = imgs[l.slug], on = state.vis[l.slug], sel = l.slug === state.sel;
        if (on && !img.src) img.src = img.dataset.src;         // load on demand
        img.style.display = on ? '' : 'none';
        img.style.opacity = sel || l.kind !== 'copper' ? 1 : state.dim;
        // physical order, front on top — reversed when looking from the back;
        // the outline sits above everything so a hole reads as a hole, and
        // the selected layer above the rest of the copper.
        img.style.zIndex = l.kind === 'outline' ? 300 : sel ? 200
          : back ? rows.length - i : i + 1;
        const row = box.querySelector(`.cu-lay[data-slug="${l.slug}"]`);
        row.classList.toggle('on', sel);
        row.querySelector('input[type=radio]').checked = sel;
        row.querySelector('input[type=checkbox]').checked = on;
      });
      const l = rows.find(r => r.slug === state.sel);
      $('#cu-note').innerHTML = `<b>${l.name}</b> — ${l.note}` +
        (l.zones && l.zones.length ? ` Pours: <b>${l.zones.join(', ')}</b>.` : '');
      place();
      if (state.grid) grid(true);
    }

    // ---- grid mode: the same files again, as six tiles -------------------
    function grid(refresh) {
      const g = $('#cu-gridbox');
      g.hidden = !state.grid;
      $('#cu-stack').hidden = state.grid;
      $('[data-act=grid]').classList.toggle('on', state.grid);
      if (!state.grid || (g.childElementCount && !refresh)) return;
      g.innerHTML = '';
      for (const l of copper) {
        const cell = el('div', 'cu-cell'), t = el('div', 'cu-tile');
        const b = el('div', 'cu-body');
        Object.assign(b.style, {
          left: pct(bx - meta.dia_mm / 2, vw), top: pct(by - meta.dia_mm / 2, vh),
          width: pct(meta.dia_mm, vw), height: pct(meta.dia_mm, vh),
        });
        t.style.aspectRatio = vw + ' / ' + vh;
        t.appendChild(b);
        for (const src of [l.file, 'edge.svg']) {
          const i = el('img'); i.src = DIR + src; i.alt = l.name; t.appendChild(i);
        }
        t.appendChild(el('b', null, l.name));
        cell.appendChild(t);
        cell.appendChild(el('div', 'cu-cap',
          `${facts(l)}<span>pour ${l.zones.join(', ') || 'none'}</span>`));
        g.appendChild(cell);
      }
    }

    draw();
    addEventListener('resize', place);
  }

  const pct = (v, of) => (100 * v / of).toFixed(3) + '%';
  const sum = (ls, k) => ls.reduce((a, l) => a + (l[k] || 0), 0);
  const facts = l => l.kind !== 'copper' ? '' :
    (l.tracks ? `${l.tracks} tracks · ${Math.round(l.length_mm)} mm · ` : 'plane · ') +
    `${l.pads} pads`;
})();
