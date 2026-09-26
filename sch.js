// The schematic viewer: the SCH tab of the page's viewer. Board S's sheets as
// KiCad plots them (tools/regen.py writes them, and sheets.json, into the
// directory data-dir names), one at a time: pick a sheet on the left, drag to
// pan, scroll to zoom.
(() => {
  function start() {
    const panel = document.getElementById('schematic');
    if (!panel) return;
    const dir = panel.dataset.dir;
    const list = panel.querySelector('#sch-sheets');
    const plate = panel.querySelector('#sch-plate');
    const pan = panel.querySelector('#sch-pan');
    const img = panel.querySelector('#sch-img');
    const zoomLab = panel.querySelector('#sch-zoom');
    const cap = panel.querySelector('#sch-cap');
    const pdf = panel.querySelector('#sch-pdf');
    let sheets = [], cur = -1, z = 1, x = 0, y = 0, fetched = false;

    const shown = () => !panel.hidden && panel.offsetParent !== null;
    const place = () => {
      pan.style.transform = `translate(${x}px, ${y}px) scale(${z})`;
      zoomLab.textContent = z.toFixed(1) + '×';
    };
    const fit = () => { z = 1; x = 0; y = 0; place(); };
    function zoomAt(f, cx, cy) {
      const nz = Math.min(12, Math.max(1, z * f));
      x = cx - (cx - x) * nz / z; y = cy - (cy - y) * nz / z; z = nz;
      if (z === 1) { x = 0; y = 0; }
      place();
    }

    function show(i) {
      if (i === cur || !sheets[i]) return;
      cur = i;
      const s = sheets[i];
      list.querySelectorAll('button').forEach((b, k) => b.classList.toggle('on', k === i));
      img.alt = `${s.title}: ${s.desc}`;
      img.src = dir + s.file;
      cap.textContent = `${s.title} (${s.sheet === 'top' ? 'top level' : s.sheet}.kicad_sch, ` +
        `${s.paper}): ${s.desc}`;
      fit();
    }

    function load() {
      if (fetched || !shown()) return;
      fetched = true;
      fetch(dir + 'sheets.json').then(r => r.json()).then(j => {
        sheets = j.sheets;
        if (j.pdf) { pdf.href = dir + j.pdf; pdf.hidden = false; }
        list.replaceChildren(...sheets.map((s, i) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.className = 'sch-sheet';
          b.innerHTML = `<span class="k">${i + 1}</span><span class="nm"></span>` +
            `<span class="fx"></span>`;
          b.querySelector('.nm').textContent = s.title;
          b.querySelector('.fx').textContent = s.desc;
          b.addEventListener('click', () => show(i));
          return b;
        }));
        const want = sheets.findIndex(s => '#sch-' + s.sheet === location.hash);
        show(want >= 0 ? want : Math.min(1, sheets.length - 1));
      }).catch(() => { cap.textContent = 'The schematic plots are missing: run ' +
        'python3 tools/regen.py --board s --sch'; });
    }

    // drag to pan, wheel to zoom about the cursor
    let drag = null;
    plate.addEventListener('pointerdown', e => {
      drag = { px: e.clientX, py: e.clientY, x, y };
      plate.setPointerCapture(e.pointerId);
      plate.classList.add('drag');
    });
    plate.addEventListener('pointermove', e => {
      if (!drag) return;
      x = drag.x + e.clientX - drag.px; y = drag.y + e.clientY - drag.py; place();
    });
    const end = () => { drag = null; plate.classList.remove('drag'); };
    plate.addEventListener('pointerup', end);
    plate.addEventListener('pointercancel', end);
    plate.addEventListener('wheel', e => {
      e.preventDefault();
      const r = plate.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    plate.addEventListener('dblclick', e => {
      const r = plate.getBoundingClientRect();
      zoomAt(2, e.clientX - r.left, e.clientY - r.top);
    });
    panel.querySelectorAll('[data-sch]').forEach(b => b.addEventListener('click', () => {
      const r = plate.getBoundingClientRect();
      if (b.dataset.sch === 'fit') fit();
      else zoomAt(b.dataset.sch === 'in' ? 1.5 : 1 / 1.5, r.width / 2, r.height / 2);
    }));
    addEventListener('keydown', e => {
      if (!shown() || e.target.closest('input, textarea')) return;
      if (e.key === '0') fit();
      else if (e.key === '+' || e.key === '=') zoomAt(1.5, plate.clientWidth / 2, plate.clientHeight / 2);
      else if (e.key === '-') zoomAt(1 / 1.5, plate.clientWidth / 2, plate.clientHeight / 2);
      else if (/^[1-9]$/.test(e.key) && sheets[+e.key - 1]) show(+e.key - 1);
    });

    // shell.js fires resize when a tab is chosen: the first time this one is,
    // fetch the list and the first sheet
    addEventListener('resize', load);
    addEventListener('hashchange', () => {
      const i = sheets.findIndex(s => '#sch-' + s.sheet === location.hash);
      if (i >= 0) show(i);
    });
    load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
