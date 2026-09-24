/* shell.js — the Altium 365 frame round every project page.
 *
 * Builds the chrome and nothing else: the top bar (back, the project, GitHub),
 * the PROJECT tree on the left, and the sheet bar under the top bar. The tree
 * lists the pages; the one on screen is open to its sections, read off its own
 * headings, and follows the scroll. The others open to theirs on demand,
 * fetched and read the same way, so no list here can drift from a page.
 * Also runs the viewer's PCB / 3D tabs, and opens whatever a #hash points
 * into: a closed disclosure, a hidden tab.
 *
 * Loaded from <head>: it marks the document at once, so the layout does not
 * jump when the chrome arrives, and builds on DOMContentLoaded.
 */
(() => {
  const BASE = new URL('.', document.currentScript.src);    // the project root
  const REPO = 'https://github.com/sequoia-hope/servodrive';
  document.documentElement.classList.add('a365');

  // the tree's top level; `design` pages sit in the Design folder, as an
  // A365 project's documents do
  const PAGES = [
    { href: 'index.html', label: 'Board A', icon: 'pcb', design: true },
    { href: 'single.html', label: 'Board S', icon: 'pcb', design: true },
    { href: 'spec.html', label: 'Specification', icon: 'doc', design: true },
    { href: 'sim/report/index.html', label: 'Simulation', icon: 'sim' },
    { href: 'index.html#log', label: 'History', icon: 'clock', leaf: true },
  ];

  const S = (body, vb = 16) => `<svg viewBox="0 0 ${vb} ${vb}" aria-hidden="true">${body}</svg>`;
  const LINE = 'fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"';
  const I = {
    chev: S(`<path d="M6 3.5 10.5 8 6 12.5" ${LINE} stroke-width="1.6"/>`),
    back: S(`<path d="M10 3 5 8l5 5" ${LINE} stroke-width="1.7"/>`),
    menu: S(`<path d="M2 4h12M2 8h12M2 12h12" ${LINE} stroke-width="1.5"/>`),
    folder: S('<path d="M1 3.5h4.6l1.5 1.6H15v8.4H1z" fill="#fff"/>'),
    folder2: S('<path d="M1 3.5h4.6l1.5 1.6H15v8.4H1z" fill="#e5c987"/>'),
    sheet: S('<rect x="1" y="3" width="14" height="10.5" rx=".6" fill="#e5c987"/>' +
             '<path d="M9.5 9.8H15M9.5 9.8v3.7M12 9.8v3.7" stroke="#9a8450" stroke-width=".8"/>'),
    pcb: S('<rect x="1" y="3" width="14" height="10.5" rx=".8" fill="#1fb182"/>' +
           '<rect x="8.3" y="5.2" width="4.6" height="4.6" fill="#fff"/>' +
           '<path d="M3 5.8h3M3 8.2h3M3 10.6h3M9.2 11.8v-2M11.9 11.8v-2" stroke="#fff" stroke-width="1.1"/>'),
    doc: S('<path d="M3 1h7l3.5 3.5V15H3z" fill="#dfe9f2"/><path d="M10 1v3.5h3.5" fill="#aebfcf"/>' +
           '<path d="M5.2 7.5h5.6M5.2 9.7h5.6M5.2 11.9h3.8" stroke="#7d8f9f" stroke-width="1"/>'),
    sim: S(`<path d="M1.5 3.5v6c0 2.5 3.5 2.5 3.5 0v-3c0-2.5 3.5-2.5 3.5 0v3c0 2.5 3.5 2.5 3.5 0v-6M14.5 3.5v9" ${LINE} stroke-width="1.3"/>`),
    clock: S(`<path d="M3 5.2A5.8 5.8 0 1 1 2.3 9" ${LINE} stroke-width="1.3"/><path d="M1.8 2.8 3 5.4l2.7-.6M8.2 5v3.4l2.2 1.4" ${LINE} stroke-width="1.3"/>`),
    cube: S(`<path d="M8 1.6 14 5v6.2l-6 3.3-6-3.3V5zM2 5l6 3.3L14 5M8 8.3v6.2" ${LINE} stroke-width="1.15"/>`),
    layers: S(`<path d="M8 1.8 14.2 5 8 8.2 1.8 5z" ${LINE} stroke-width="1.2"/><path d="M1.8 8 8 11.2 14.2 8M1.8 11 8 14.2 14.2 11" ${LINE} stroke-width="1.2"/>`),
    full: S(`<path d="M9.5 2h4.5v4.5M14 2 9 7M6.5 14H2V9.5M2 14l5-5" ${LINE} stroke-width="1.4"/>`),
    down: S(`<path d="M8 1.8v8.4M4.4 6.8 8 10.4l3.6-3.6M2 12v2.2h12V12" ${LINE} stroke-width="1.5"/>`),
    gh: S('<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"/>'),
    proj: S('<path d="M12 2h11l7 7v20H12z" fill="#e3edf6"/><path d="M23 2v7h7" fill="#b7c8d6"/>' +
            '<rect x="3" y="12.5" width="18.5" height="14" rx="1.5" fill="#1fb182"/>' +
            '<rect x="11.2" y="15.3" width="7.4" height="7.4" fill="#fff"/><rect x="12.9" y="17" width="4" height="4" fill="#1fb182"/>' +
            '<path d="M5.4 16h3.4M5.4 19.2h3.4M5.4 22.4h3.4M12.6 23.6v3M15 23.6v3M17.4 23.6v3" stroke="#fff" stroke-width="1.4"/>', 34),
  };

  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const html = s => { const t = document.createElement('template'); t.innerHTML = s.trim(); return t.content.firstChild; };
  const url = href => new URL(href, BASE);
  const path = u => u.pathname.replace(/\/$/, '/index.html');
  const HERE = path(location);
  const hereIs = p => !p.leaf && path(url(p.href)) === HERE;

  // ---- a page's own outline, from its headings -------------------------------
  // Each top-level section by its h2 (and its h3, where the h2 is only a tag,
  // as the simulation's "Q1" is), each tab of a viewer, and the disclosures
  // under "Past work" in a folder of their own. Ids that are missing are made
  // from the heading, the same way on every read, so a link built from a
  // fetched copy finds the section on the page itself.
  const text = n => n.textContent.replace(/\s+/g, ' ').trim();
  function outline(doc) {
    const main = doc.querySelector('main');
    if (!main) return [];
    const taken = new Set([...doc.querySelectorAll('[id]')].map(n => n.id));
    const idFor = (n, label) => {
      if (n.id) return n.id;
      let s = label.toLowerCase().normalize('NFKD').replace(/[^\w\s-]/g, '')
        .trim().split(/\s+/).slice(0, 6).join('-') || 'section';
      for (let k = 2, b = s; taken.has(s); k++) s = b + '-' + k;
      taken.add(s);
      return n.id = s;
    };
    const items = [];
    let folder = null;
    for (const n of main.children) {
      if (n.matches('h2.archhead')) {
        folder = { label: text(n), icon: 'folder2', kids: [] };
        items.push(folder);
      } else if (n.matches('section.vw')) {
        for (const p of n.querySelectorAll('.vw-panel')) {
          const tab = n.querySelector(`[data-tab="${p.id}"]`);
          items.push({ id: p.id, label: tab ? text(tab) : p.id,
                       icon: p.id === 'board3d' ? 'cube' : 'pcb', el: p, at: n });
        }
      } else if (n.matches('section')) {
        const h = n.querySelector('h2');
        if (!h) continue;
        let label = text(h);
        const h3 = h.nextElementSibling;
        if (label.length < 12 && h3 && h3.tagName === 'H3') label += ' — ' + text(h3);
        items.push({ id: idFor(n, label), label, icon: 'sheet', el: n, at: n });
      } else if (n.matches('details')) {
        const s = n.querySelector('summary').cloneNode(true);
        s.querySelectorAll('.sub').forEach(x => x.remove());
        const label = text(s);
        const row = { id: idFor(n, label), label, icon: 'sheet', el: n, at: n };
        (folder ? folder.kids : items).push(row);
      }
    }
    return items;
  }

  // ---- the tree ---------------------------------------------------------------
  const spy = [];            // [{row, item}] on this page, in page order
  let pageRow = null;

  function row(depth, icon, label, { href, open, leaf, page } = {}) {
    const li = document.createElement('li');
    const r = html(`<div class="a3-row${page ? ' page' : ''}" style="--d:${depth}">
      <button class="a3-tw${leaf ? ' leaf' : ''}" aria-expanded="${!!open}"
        aria-label="expand or collapse" tabindex="${leaf ? -1 : 0}">${I.chev}</button>
      ${I[icon].replace('<svg', '<svg class="ic"')}
      ${href ? `<a class="t" href="${esc(href)}">${esc(label)}</a>` : `<span class="t">${esc(label)}</span>`}
    </div>`);
    r.title = label;
    li.appendChild(r);
    const ul = document.createElement('ul');
    ul.hidden = !open;
    li.appendChild(ul);
    const tw = r.querySelector('.a3-tw');
    const toggle = on => {
      on = on ?? ul.hidden;
      ul.hidden = !on;
      tw.setAttribute('aria-expanded', on);
      if (on && li.onopen) li.onopen();
    };
    tw.addEventListener('click', e => { e.stopPropagation(); toggle(); });
    if (!href) r.addEventListener('click', () => toggle());      // a folder: the row toggles
    Object.assign(li, { row: r, kids: ul, toggle });
    return li;
  }

  function fill(ul, items, depth, base) {
    for (const it of items) {
      if (it.kids) {
        const f = row(depth, it.icon, it.label, { open: true });
        fill(f.kids, it.kids, depth + 1, base);
        ul.appendChild(f);
        continue;
      }
      const li = row(depth, it.icon, it.label, { href: base + '#' + it.id, leaf: true });
      ul.appendChild(li);
      if (!base) {
        spy.push({ row: li.row, item: it });
        li.row.querySelector('a').addEventListener('click', e => {
          e.preventDefault();
          history.pushState(null, '', '#' + it.id);
          reveal(it.id);
          closeNav();
        });
      }
    }
  }

  function tree() {
    const root = document.createElement('ul');
    const design = row(0, 'folder', 'Design', { open: true });
    root.appendChild(design);
    for (const p of PAGES) {
      const here = hereIs(p);
      const li = row(p.design ? 1 : 0, p.icon, p.label,
                     { href: url(p.href).href, open: here, leaf: p.leaf, page: true });
      (p.design ? design.kids : root).appendChild(li);
      const depth = p.design ? 2 : 1;
      if (here) {
        pageRow = li.row;
        fill(li.kids, outline(document), depth, '');
        li.row.querySelector('a').addEventListener('click', e => {
          e.preventDefault();
          history.pushState(null, '', location.pathname);
          scrollTo({ top: 0 });
          closeNav();
        });
      } else if (!p.leaf) {
        // someone else's sections: read them the first time the page is opened
        li.onopen = () => {
          li.onopen = null;
          const u = url(p.href);
          fetch(u).then(r => r.ok ? r.text() : Promise.reject(r.status))
            .then(t => fill(li.kids, outline(new DOMParser().parseFromString(t, 'text/html')),
                            depth, u.href.split('#')[0]))
            .catch(() => { li.row.querySelector('.a3-tw').classList.add('leaf'); });
        };
      }
    }
    return root;
  }

  // ---- the viewer's tabs --------------------------------------------------------
  function tab(vw, id) {
    vw.querySelectorAll('.vw-panel').forEach(p => { p.hidden = p.id !== id; });
    vw.querySelectorAll('.vw-tabs [data-tab]').forEach(b =>
      b.setAttribute('aria-selected', b.dataset.tab === id));
    const lay = vw.querySelector('[data-vw=layers]');
    if (lay) lay.hidden = !vw.querySelector('#' + id + ' .cu');
    dispatchEvent(new Event('resize'));        // copper.js re-fits to the new width
    track();
  }

  function viewers() {
    for (const vw of document.querySelectorAll('section.vw')) {
      vw.querySelectorAll('.vw-tabs [data-tab]').forEach(b =>
        b.addEventListener('click', () => tab(vw, b.dataset.tab)));
      const lay = vw.querySelector('[data-vw=layers]');
      if (lay) lay.addEventListener('click', () => {
        const off = vw.classList.toggle('nolayers');
        lay.setAttribute('aria-pressed', !off);
        dispatchEvent(new Event('resize'));
      });
      const full = vw.querySelector('[data-vw=full]');
      if (full) {
        if (!vw.requestFullscreen) full.hidden = true;
        full.addEventListener('click', () => document.fullscreenElement
          ? document.exitFullscreen() : vw.requestFullscreen());
      }
    }
    document.addEventListener('fullscreenchange', () => dispatchEvent(new Event('resize')));
  }

  // open what a hash points into, then bring it on screen
  function reveal(id) {
    const el = id && document.getElementById(decodeURIComponent(id));
    if (!el) return;
    const panel = el.closest('.vw-panel');
    if (panel) tab(panel.closest('.vw'), panel.id);
    for (let d = el.closest('details'); d; d = d.parentElement.closest('details')) d.open = true;
    el.scrollIntoView();
    track();
  }

  // ---- where the reader is ---------------------------------------------------------
  let sheet = null, pending = false;
  function track() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const line = innerHeight * 0.3 + 60;
      const bottom = innerHeight + scrollY >= document.documentElement.scrollHeight - 4;
      let cur = null;
      for (const s of spy) {
        if (s.item.el.hidden && s.item.el.classList.contains('vw-panel')) continue;
        const box = s.item.at.getBoundingClientRect();
        if (!box.height) continue;
        if (box.top <= line || bottom) cur = s;
      }
      spy.forEach(s => s.row.classList.toggle('here', s === cur));
      if (pageRow) pageRow.classList.toggle('here', !cur);
      if (sheet) sheet.value = cur ? cur.item.id : '';
    });
  }

  const closeNav = () => document.documentElement.classList.remove('nav-open');

  function build() {
    const index = path(url('index.html')) === HERE;
    const me = PAGES.find(hereIs);
    const top = html(`<div class="a3-top">
      <button class="a3-menu" aria-label="project tree">${I.menu}</button>
      <a class="a3-back${index ? ' none' : ''}" href="${url('index.html').href}"
        title="back to the project" aria-label="back to the project">${I.back}</a>
      <a class="a3-proj" href="${url('index.html').href}">${I.proj}
        <span><small>sequoia-hope</small><b>servodrive</b></span></a>
      <div class="a3-sp"></div>
      <a class="a3-btn primary" href="${REPO}">${I.gh}<span>GitHub</span></a>
    </div>`);
    const side = html(`<nav class="a3-side" aria-label="project">
      <div class="a3-label">Project</div>
      <div class="a3-tree"></div>
      <div class="a3-foot"><a class="a3-btn" href="${REPO}/archive/refs/heads/main.zip">
        ${I.down}<span>Download project</span></a></div>
    </nav>`);
    side.querySelector('.a3-tree').appendChild(tree());
    const bar = html(`<div class="a3-bar">
      <label class="a3-sheet" title="go to a section">${I[me ? me.icon : 'sheet']}
        <select aria-label="go to a section"></select></label>
    </div>`);
    sheet = bar.querySelector('select');
    sheet.add(new Option(me ? me.label : document.title, ''));
    for (const s of spy) sheet.add(new Option(s.item.label, s.item.id));
    sheet.addEventListener('change', () => {
      if (!sheet.value) { history.pushState(null, '', location.pathname); scrollTo({ top: 0 }); }
      else { history.pushState(null, '', '#' + sheet.value); reveal(sheet.value); }
    });
    document.body.prepend(top, side, bar);

    top.querySelector('.a3-menu').addEventListener('click', e => {
      e.stopPropagation();
      document.documentElement.classList.toggle('nav-open');
    });
    document.addEventListener('click', e => {
      if (!side.contains(e.target)) closeNav();
    });

    document.querySelectorAll('[data-icon]').forEach(b =>
      b.insertAdjacentHTML('afterbegin', I[b.dataset.icon] || ''));
    viewers();
    addEventListener('scroll', track, { passive: true });
    addEventListener('resize', track);
    addEventListener('hashchange', () => reveal(location.hash.slice(1)));
    if (location.hash) reveal(location.hash.slice(1));
    track();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
