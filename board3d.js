/* board3d.js — the 3D viewer on index.html and single.html (the 3D tab).
 *
 * Draws img/3d/<board>.glb, written by tools/export_3d.py: the board as
 * kicad-cli exports it, merged and quantized, each part's node carrying its
 * reference, value, footprint and side. img/3d/<board>.json says what the
 * caption says, and img/3d/<board>.parts.json what the part pane on the
 * right says about a clicked part: its role, footprint, position, pins and
 * nets, read out of the board file by the same tool. The panel names them:
 * <div id="board3d" data-model="img/3d/a">. Nothing loads until the panel is
 * near the screen, and nothing draws while it is off it.
 *
 * glTF is y-up: the board lies in x-z, the outward face (F.Cu) towards +y,
 * and +z is KiCad's +y, so looking down -y with -z up is pcbnew's own view.
 */
import * as THREE from 'three';
import { TrackballControls } from './vendor/TrackballControls.js';
import { GLTFLoader } from './vendor/GLTFLoader.js';
import { RoomEnvironment } from './vendor/RoomEnvironment.js';

const root = document.getElementById('board3d');
if (root) init(root);

// camera direction (from the board towards the eye) and up, per view
const VIEWS = {
  front: [[0, 1, 0], [0, 0, -1]],
  back: [[0, -1, 0], [0, 0, -1]],       // turned over left to right, like pcbnew's flip
  tilt: [[0, 0.62, 0.78], [0, 1, 0]],
  edge: [[0, 0.06, 1], [0, 1, 0]],
};
// the board's own meshes, by the layer export_3d.py names them, into toggles
const GROUPS = [
  ['parts', 'Parts', null],
  ['copper', 'Copper', ['copper', 'pad', 'via']],
  ['soldermask', 'Solder mask', ['soldermask']],
  ['silkscreen', 'Silkscreen', ['silkscreen']],
  ['PCB', 'Laminate', ['PCB']],
];
const HILITE = new THREE.Color(0x2f6feb);

function init(root) {
  const BASE = root.dataset.model || 'img/3d/a';
  const $ = s => root.querySelector(s);
  const stage = $('#b3-stage'), msg = $('#b3-msg'), tag = $('#b3-tag');
  const info = $('#b3-info'), ib = $('#b3-ib');
  let details = null;                          // <board>.parts.json, when it has come
  const say = html => { msg.innerHTML = html; msg.hidden = !html; };

  let started = false, visible = false, running = false;
  new IntersectionObserver(es => {
    visible = es.some(e => e.isIntersecting);
    if (visible && !started) { started = true; start(); }
    if (visible) kick();
  }, { rootMargin: '300px' }).observe(stage);

  let renderer, scene, camera, controls, board, tween = null, dirty = true;
  const parts = new Map(), layers = {}, occluders = [];
  let hover = null, chosen = null;

  function start() {
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch (e) {
      say('This browser would not start WebGL, so there is no 3D view here.');
      return;
    }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;    // bare copper glares without
    stage.prepend(renderer.domElement);
    scene = new THREE.Scene();
    scene.background = new THREE.Color(getComputedStyle(stage).backgroundColor);   // style.css's canvas
    const pmrem = new THREE.PMREMGenerator(renderer);
    scene.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;
    camera = new THREE.PerspectiveCamera(30, 1, 1e-4, 10);
    const head = new THREE.DirectionalLight(0xffffff, 0.7);    // rides with the eye
    head.position.set(0.3, 0.6, 1);
    camera.add(head);
    scene.add(camera);
    controls = new TrackballControls(camera, renderer.domElement);
    Object.assign(controls, { rotateSpeed: 3.5, zoomSpeed: 1.3, panSpeed: 0.8,
                              dynamicDampingFactor: 0.14 });
    controls.addEventListener('start', () => { tween = null; });
    controls.addEventListener('change', () => { dirty = true; });
    new ResizeObserver(resize).observe(stage);
    resize();
    wire();
    say('Loading the model&hellip;');

    fetch(BASE + '.json').then(r => r.ok ? r.json() : null).then(facts).catch(() => {});
    fetch(BASE + '.parts.json').then(r => r.ok ? r.json() : null)
      .then(d => { details = d; if (chosen) pane(chosen); }).catch(() => {});
    new GLTFLoader().load(BASE + '.glb', loaded,
      e => { if (e.total) say('Loading the model&hellip; ' + Math.round(100 * e.loaded / e.total) + '%'); },
      e => say('The model is not built yet &mdash; run <code>python3 tools/export_3d.py' +
               (BASE.endsWith('/s') ? ' --board s' : '') + '</code>. (' + (e.message || e) + ')'));
  }

  function loaded(gltf) {
    board = gltf.scene;
    board.traverse(o => {
      const x = o.userData;
      if (x.ref) parts.set(x.ref, o);
      if (x.layer) {
        (layers[x.layer] = layers[x.layer] || []).push(o);
        if (x.layer === 'PCB' || x.layer === 'soldermask') occluders.push(o);
      }
    });
    // centre on the laminate, not on everything: the USB-C hangs over the rim
    const pcb = layers.PCB ? new THREE.Box3().setFromObject(layers.PCB[0])
                           : new THREE.Box3().setFromObject(board);
    board.position.sub(pcb.getCenter(new THREE.Vector3()));
    scene.add(board);
    const size = pcb.getSize(new THREE.Vector3());
    board.userData.radius = Math.max(size.x, size.z) / 2;
    view('tilt', true);
    toggles();
    finder();
    say('');
    redraw();
  }

  // ---- views ---------------------------------------------------------------
  function fitDistance(r) {
    const fov = THREE.MathUtils.degToRad(camera.fov) / 2;
    return 1.08 * r / Math.sin(Math.min(fov, Math.atan(Math.tan(fov) * camera.aspect)));
  }

  function view(name, now) {
    const [d, u] = VIEWS[name];
    const r = board.userData.radius;
    // tilted, the disc foreshortens; edge on, it is a strip: fit what shows
    const dist = fitDistance(r * ({ tilt: 0.85, edge: 0.75 }[name] || 1));
    fly(new THREE.Vector3(...d).normalize(), new THREE.Vector3(...u), new THREE.Vector3(), dist, now);
    root.querySelectorAll('[data-view]').forEach(b => b.classList.toggle('on', b.dataset.view === name));
    return name;
  }

  function fly(dir, up, target, dist, now) {
    const eye = new THREE.PerspectiveCamera();   // a camera: lookAt aims its -z, not +z
    eye.position.copy(target).addScaledVector(dir, dist);
    eye.up.copy(up);
    eye.lookAt(target);
    const to = { q: eye.quaternion.clone(), t: target.clone(), d: dist };
    still();
    if (now) { place(to.q, to.t, to.d); redraw(); return; }
    tween = { from: { q: camera.quaternion.clone(), t: controls.target.clone(),
                      d: camera.position.distanceTo(controls.target) },
              to, t0: performance.now() };
    kick();
  }

  function place(q, t, d) {
    camera.quaternion.copy(q);
    camera.up.set(0, 1, 0).applyQuaternion(q);
    camera.position.set(0, 0, d).applyQuaternion(q).add(t);
    controls.target.copy(t);
    camera.lookAt(t);
  }

  // TrackballControls keeps turning after a flick, and has no way to stop it
  // but a damping of 1 for one update
  function still() {
    const d = controls.dynamicDampingFactor;
    controls.dynamicDampingFactor = 1;
    controls.update();
    controls.dynamicDampingFactor = d;
  }

  function step(now) {
    if (!tween) return false;
    const k = Math.min(1, Math.max(0, (now - tween.t0) / 450)), e = k * k * (3 - 2 * k);
    const { from, to } = tween;
    place(from.q.clone().slerp(to.q, e), from.t.clone().lerp(to.t, e),
          from.d + (to.d - from.d) * e);
    if (k === 1) tween = null;
    return true;
  }

  // ---- drawing -------------------------------------------------------------
  function kick() {
    if (running || !renderer) return;
    running = true;
    requestAnimationFrame(loop);
  }

  function redraw() { dirty = true; kick(); }

  // runs while the section is on screen; draws only when something moved
  function loop(now) {
    if (!visible) { running = false; return; }
    if (step(now)) dirty = true;
    else controls.update();
    if (dirty) {
      dirty = false;
      clip();
      renderer.render(scene, camera);
      placeTag();
    }
    requestAnimationFrame(loop);
  }

  // copper sits 35 um over the laminate and the mask over that: the depth
  // range follows the eye, or they fight at any distance worth looking from
  function clip() {
    const d = camera.position.distanceTo(controls.target);
    const r = board ? board.userData.radius : 0.05;
    camera.near = Math.max(d / 100, 1e-5);
    camera.far = d + 4 * r;
    camera.updateProjectionMatrix();
  }

  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    controls.handleResize();
    redraw();
  }

  // ---- parts: hover, click, find ------------------------------------------
  const ray = new THREE.Raycaster();
  const shown = o => { for (; o; o = o.parent) if (!o.visible) return false; return true; };

  function partAt(ev) {
    if (!board) return null;
    const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1,
                                        -((ev.clientY - r.top) / r.height) * 2 + 1), camera);
    const targets = [...parts.values(), ...occluders].filter(shown);
    for (const h of ray.intersectObjects(targets, true)) {
      if (!shown(h.object)) continue;
      for (let o = h.object; o; o = o.parent) if (o.userData.ref) return o;
      return null;                           // the board is in the way
    }
    return null;
  }

  function tint(part, on) {
    part.traverse(o => {
      if (!o.isMesh) return;
      if (on && !o.userData.own) {
        o.userData.own = o.material;
        o.material = o.material.clone();
        o.material.emissive = HILITE;
        o.material.emissiveIntensity = 0.7;
      } else if (!on && o.userData.own) {
        o.material.dispose();
        o.material = o.userData.own;
        delete o.userData.own;
      }
    });
    redraw();
  }

  function setHover(p) {
    if (p === hover) return;
    if (hover && hover !== chosen) tint(hover, false);
    hover = p;
    if (hover) tint(hover, true);
    renderer.domElement.style.cursor = hover ? 'pointer' : '';
    label();
  }

  function choose(p, go) {
    if (chosen && chosen !== p && chosen !== hover) tint(chosen, false);
    chosen = p;
    if (chosen) tint(chosen, true);
    $('#b3-find').value = chosen ? chosen.userData.ref : '';
    pane(chosen);
    if (chosen) info.hidden = false;           // the stage narrows; its observer re-fits
    label();
    if (chosen && go) focus(chosen);
  }

  function focus(p) {
    const box = new THREE.Box3().setFromObject(p);
    if (box.isEmpty()) return;                   // a node with no geometry: nowhere to fly
    const c = box.getCenter(new THREE.Vector3());
    let dir = camera.position.clone().sub(controls.target).normalize();
    let up = camera.up.clone();
    const back = p.userData.side === 'back';
    if (back ? dir.y > 0 : dir.y < 0) {          // it is on the face turned away
      [dir, up] = VIEWS[back ? 'back' : 'front'].map(v => new THREE.Vector3(...v));
      dir.lerp(new THREE.Vector3(0, 0, 1), 0.35).normalize();
      up.set(0, 1, 0);
    }
    const size = box.getSize(new THREE.Vector3()).length();
    fly(dir, up, c, Math.max(fitDistance(size * 1.6), 0.012));
    root.querySelectorAll('[data-view]').forEach(b => b.classList.remove('on'));
  }

  // the tag names what is under the cursor, and the chosen part only while
  // the pane is shut: with it open, the pane says it
  const tagged = () => hover || (info.hidden ? chosen : null);

  function label() {
    const p = tagged();
    if (!p) { tag.hidden = true; return; }
    const x = p.userData;
    tag.innerHTML = '<b>' + x.ref + '</b> ' + esc(x.value) +
      '<span>' + esc(x.footprint) + ' &middot; ' + x.side + '</span>';
    tag.hidden = false;
    placeTag();
  }

  function placeTag() {
    const p = tagged();
    if (!p || tag.hidden) return;
    const box = new THREE.Box3().setFromObject(p);
    const at = box.getCenter(new THREE.Vector3()).project(camera);
    const w = stage.clientWidth, h = stage.clientHeight;
    tag.style.left = Math.round((at.x + 1) / 2 * w) + 'px';
    tag.style.top = Math.round((1 - at.y) / 2 * h) + 'px';
  }

  function finder() {
    const refs = [...parts.keys()].sort(natural);
    $('#b3-refs').innerHTML = refs.map(r => {
      const x = parts.get(r).userData;
      return '<option value="' + r + '">' + esc(x.value) + ' &middot; ' + x.side + '</option>';
    }).join('');
    const box = $('#b3-find');
    box.disabled = false;
    const go = () => {
      const v = box.value.trim().toUpperCase();
      const p = parts.get(v);
      box.classList.toggle('bad', !!v && !p);
      if (p) {
        if (!shown(p)) { layerOn('parts'); }
        choose(p, true);
      }
    };
    box.addEventListener('change', go);
    box.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  }

  // ---- toggles ---------------------------------------------------------------
  function toggles() {
    const box = $('#b3-layers');
    for (const [key, name, list] of GROUPS) {
      const objs = list ? list.flatMap(l => layers[l] || []) : [...parts.values()];
      if (!objs.length) continue;
      const lab = document.createElement('label');
      lab.innerHTML = '<input type="checkbox" checked data-layer="' + key + '"> ' + name;
      lab.firstChild.addEventListener('change', e => {
        objs.forEach(o => { o.visible = e.target.checked; });
        if (!e.target.checked && key === 'parts') { setHover(null); choose(null); }
        redraw();
      });
      box.appendChild(lab);
    }
  }

  function layerOn(key) {
    const c = root.querySelector('[data-layer="' + key + '"]');
    if (c && !c.checked) { c.checked = true; c.dispatchEvent(new Event('change')); }
  }

  function wire() {
    root.querySelectorAll('[data-view]').forEach(b =>
      b.addEventListener('click', () => { if (board) view(b.dataset.view); }));
    const cv = renderer.domElement;
    let down = null, pending = null;
    cv.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    cv.addEventListener('pointermove', e => {
      if (e.buttons) return;
      if (!pending) pending = requestAnimationFrame(() => { pending = null; setHover(partAt(e)); });
    });
    cv.addEventListener('pointerleave', () => setHover(null));
    cv.addEventListener('pointerup', e => {
      if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
      const p = partAt(e);
      choose(p === chosen ? null : p, false);
    });
    cv.addEventListener('dblclick', e => { const p = partAt(e); if (p) choose(p, true); });
    root.addEventListener('keydown', e => {
      if (e.key === 'Escape') { choose(null); $('#b3-find').blur(); }
    });
    $('#b3-x').addEventListener('click', () => { info.hidden = true; choose(null); });
  }

  // ---- the part pane ---------------------------------------------------------
  // What the board file says about the chosen part, laid out the way A365's
  // properties panel is. Without parts.json it still has the node's own four.
  const ATTRS = { smd: 'SMD', through_hole: 'through-hole', dnp: 'not fitted (DNP)',
                  exclude_from_bom: 'not in the BOM', exclude_from_pos_files: 'not in the placement file',
                  board_only: 'board only', allow_missing_courtyard: 'no courtyard' };
  const mm = v => (Math.round(v * 100) / 100).toFixed(2).replace('-', '\u2212');
  const dl = (rows, cls) => '<dl' + (cls ? ' class="' + cls + '"' : '') + '>' +
    rows.filter(Boolean).map(([k, v]) => '<dt>' + k + '</dt><dd>' + v + '</dd>').join('') + '</dl>';
  const small = t => t ? '<small>' + esc(t) + '</small>' : '';

  function pane(p) {
    if (!p) {
      $('#b3-iref').textContent = '';
      ib.innerHTML = '<p class="b3-empty">Click a part to see it here.</p>';
      return;
    }
    const x = p.userData, d = details && details.parts && details.parts[x.ref];
    $('#b3-iref').textContent = x.ref;
    const back = x.side === 'back';
    let h = dl([
      ['Designator', esc(x.ref) + (d && d.attrs.includes('dnp') ? '<span class="b3-dnp">DNP</span>' : '')],
      ['Component', esc(x.value) + small(d && d.role)],
      d && !d.attrs.includes('exclude_from_bom') && ['LCSC', lcsc(d, x)],
      ['Footprint', '<span class="mono">' + esc(x.footprint) + '</span>' +
        small(d && [d.lib, d.descr].filter(Boolean).join(' \u00b7 '))],
    ]);
    const tall = height(p, back);
    h += dl([
      d && ['Location', 'x ' + mm(d.x) + ' &middot; y ' + mm(d.y) + ' mm' +
        small('r ' + mm(Math.hypot(d.x, d.y)) + ' mm \u00b7 \u03b8 ' +
              ((Math.atan2(d.y, d.x) * 180 / Math.PI + 360) % 360).toFixed(1) + '\u00b0 from the axis')],
      d && ['Rotation', String(d.rot).replace('-', '\u2212') + '&deg;'],
      ['Side', back ? 'Back' + small('the motor-facing face') : 'Front' + small('the outward face')],
      tall != null && ['Height', mm(tall) + ' mm' + small('its model, above the laminate')],
    ]);
    if (d) {
      const par = [];
      if (d.attrs.length) par.push(['Mounting', esc(d.attrs.map(a => ATTRS[a] || a).join(', '))]);
      if (/^https?:\/\//.test(d.datasheet))
        par.push(['Datasheet', '<a href="' + esc(d.datasheet) + '" target="_blank" rel="noopener">open</a>']);
      if (d.description) par.push(['Description', esc(d.description)]);
      for (const [k, v] of Object.entries(d.fields)) par.push([esc(k), esc(v)]);
      if (par.length) h += '<h4>Parameters</h4>' + dl(par, 'par');
      if (d.pins.length) {
        h += '<h4>Pins <span>' + d.pins.length + '</span></h4><table class="b3-pins"><tbody>' +
          d.pins.map(([n, net]) => '<tr><td>' + esc(n) + '</td><td>' +
            (!net || net.startsWith('unconnected-') ? '<span class="b3-nc">not connected</span>' : esc(net)) +
            '</td></tr>').join('') + '</tbody></table>';
      }
    }
    ib.innerHTML = h;
    ib.scrollTop = 0;
  }

  // The number hardware/parts/lcsc.csv (or the footprint) gives it, to its LCSC
  // page -- and to JLCPCB's, the same C-number, which lists a few parts LCSC's
  // own shop does not. With none chosen yet, a search for its value and package.
  function lcsc(d, x) {
    const ext = (href, text, cls) => '<a' + (cls ? ' class="' + cls + '"' : '') + ' href="' + esc(href) +
      '" target="_blank" rel="noopener">' + esc(text) + '</a>';
    if (/^C\d+$/.test(d.lcsc)) {
      return ext('https://www.lcsc.com/product-detail/' + d.lcsc + '.html', d.lcsc) + ' ' +
        ext('https://jlcpcb.com/partdetail/' + d.lcsc, 'JLCPCB', 'b3-alt') + small(d.mpn);
    }
    const pkg = (x.footprint.match(/_(\d{4})_\d{4}Metric/) || [])[1];
    const q = [x.value.replace(/\//g, ' '), pkg].filter(Boolean).join(' ');
    return '<span class="b3-nc">none chosen</span> ' +
      ext('https://www.lcsc.com/search?q=' + encodeURIComponent(q), 'search LCSC', 'b3-alt');
  }

  // how far the part's model stands off its own face of the laminate, in mm
  function height(p, back) {
    if (!layers.PCB) return null;
    const pcb = new THREE.Box3().setFromObject(layers.PCB[0]);
    const box = new THREE.Box3().setFromObject(p);
    if (box.isEmpty()) return null;
    return 1000 * (back ? pcb.min.y - box.min.y : box.max.y - pcb.max.y);
  }

  function facts(f) {
    if (!f) return;
    const mb = (f.bytes / 1e6).toFixed(1);
    let html = f.modelled + ' of ' + f.parts + ' footprints drawn from their models; the other ' +
      f.no_model.length + ' (' + span(f.no_model) + ') have no body to draw. ';
    if (f.substituted.length)
      html += span(f.substituted) + (f.substituted.length > 1 ? ' are' : ' is') +
        ' drawn with a stand-in model (see <code>tools/export_3d.py</code>). ';
    if (f.missing.length)
      html += '<b class="warn">No model found for ' + span(f.missing) + '.</b> ';
    html += mb + '&nbsp;MB, ' + f.triangles.toLocaleString('en') + ' triangles, exported ' +
      f.generated + ' from <code>' + f.board + '</code>.';
    $('#b3-facts').innerHTML = html;
  }
}

// "H1 H2 H3 H4 H10" -> "H1–H4, H10"
function span(refs) {
  const out = [];
  for (let i = 0; i < refs.length;) {
    const m = refs[i].match(/^([A-Z_]*)(\d+)$/i);
    let j = i;
    if (m) {
      while (j + 1 < refs.length) {
        const n = refs[j + 1].match(/^([A-Z_]*)(\d+)$/i);
        if (!n || n[1] !== m[1] || +n[2] !== +refs[j].match(/\d+$/)[0] + 1) break;
        j++;
      }
    }
    out.push(j > i + 1 ? refs[i] + '&ndash;' + refs[j] : refs.slice(i, j + 1).join(', '));
    i = j + 1;
  }
  return out.join(', ');
}

function natural(a, b) {
  const [, pa, na] = a.match(/^(\D*)(\d*)/), [, pb, nb] = b.match(/^(\D*)(\d*)/);
  return pa < pb ? -1 : pa > pb ? 1 : (+na - +nb);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}
