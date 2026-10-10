/* Local SVG knowledge graph. No external runtime, CDN, or inferred relations. */
(() => {
  'use strict';
  const svg = document.getElementById('graph-canvas');
  if (!svg) return;
  const search = document.getElementById('graph-search');
  const category = document.getElementById('graph-category');
  const showSources = document.getElementById('graph-sources');
  const empty = document.getElementById('graph-empty');
  const info = document.getElementById('graph-info');
  const NS = 'http://www.w3.org/2000/svg';
  const colors = {wiki: 'var(--graph-wiki)', source: 'var(--graph-source)', category: 'var(--graph-category)'};
  const types = {wiki: 'Wiki 知识', source: '原始资料', category: '分类'};
  const radius = {wiki: 16, source: 12, category: 20};
  const labelFont = 18;
  const labelWidth = 210;
  // Approximate CJK full-width glyphs and Latin proportional text in SVG coordinates.
  const textWidth = text => Array.from(text).reduce((total, char) => total + labelFont *
    (/\s/u.test(char) ? .3 : /[ilI.,:;'!|]/u.test(char) ? .28 : /[MW@#]/u.test(char) ? .85 :
      /[^\u0000-\u00ff]/u.test(char) ? 1 : .56), 0);
  const labelLines = text => {
    let remaining = Array.from(text.replace(/\s+/gu, ' ').trim());
    const result = [];
    for (let line = 0; line < 2 && remaining.length; line++) {
      let count = 0, width = 0;
      while (count < remaining.length && width + textWidth(remaining[count]) <= labelWidth) {
        width += textWidth(remaining[count++]);
      }
      // Prefer a word boundary when wrapping a Latin title; long words still fit.
      const space = remaining.slice(0, count).lastIndexOf(' ');
      if (count < remaining.length && space > count * .45) count = space;
      let value = remaining.slice(0, count).join('').trim();
      remaining = remaining.slice(count);
      while (remaining[0] === ' ') remaining.shift();
      if (line === 1 && remaining.length) {
        while (textWidth(value + '…') > labelWidth) value = Array.from(value).slice(0, -1).join('');
        value = value.trimEnd() + '…';
      }
      result.push(value);
    }
    return result.length ? result : [''];
  };
  const positions = new Map();
  let graph = {nodes: [], edges: []}, visible = [], links = [], selected = null;
  let animation = null, frame = 0, drag = null, view = {x: 0, y: 0, k: 1};

  const make = (name, attrs = {}) => {
    const el = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    return el;
  };
  const viewport = make('g');
  const edgeLayer = make('g');
  const nodeLayer = make('g');
  svg.setAttribute('viewBox', '0 0 1000 640');
  svg.setAttribute('aria-label', '知识图谱：Wiki 知识、原始证据与分类的关系');
  svg.style.touchAction = 'none';
  svg.style.cursor = 'grab';
  viewport.append(edgeLayer, nodeLayer);
  svg.append(viewport);
  const transform = () => viewport.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`);
  const point = (event) => {
    const rect = svg.getBoundingClientRect();
    // SVG's default preserveAspectRatio leaves centered margins when its ratio changes.
    const scale = Math.min(rect.width / 1000, rect.height / 640);
    return {x: (event.clientX - rect.left - (rect.width - 1000 * scale) / 2) / scale,
            y: (event.clientY - rect.top - (rect.height - 640 * scale) / 2) / scale};
  };
  const say = (text) => { if (info) info.textContent = text; };
  const inspect = (node) => {
    selected = node.id;
    for (const item of visible) item.circle.setAttribute('stroke-width', item.id === selected ? 4 : 2);
    if (!info) return;
    info.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = node.label;
    const meta = document.createElement('span');
    const relations = graph.edges.filter(e => e.source === node.id || e.target === node.id);
    meta.textContent = ` · ${types[node.type]} · ${relations.length} 条关系 `;
    const anchor = document.createElement('a');
    anchor.href = node.url;
    anchor.textContent = node.type === 'source' ? '查看原始资料 →' : node.type === 'wiki' ? '阅读知识页面 →' : '查看该分类 →';
    info.append(title, meta, anchor);
    if (relations.length) {
      const ul = document.createElement('ul');
      const byId = new Map(graph.nodes.map(n => [n.id, n]));
      for (const relation of relations.slice(0, 30)) {
        const other = byId.get(relation.source === node.id ? relation.target : relation.source);
        if (!other) continue;
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = other.url;
        a.textContent = other.label;
        li.append(`${relation.label} · `, a);
        ul.append(li);
      }
      info.append(ul);
    }
  };
  const draw = () => {
    for (const node of visible) node.element.setAttribute('transform', `translate(${node.x} ${node.y})`);
    for (const link of links) {
      link.element.setAttribute('x1', link.a.x);
      link.element.setAttribute('y1', link.a.y);
      link.element.setAttribute('x2', link.b.x);
      link.element.setAttribute('y2', link.b.y);
    }
  };
  const tick = () => {
    const n = visible.length;
    const cooling = Math.max(.08, 1 - frame / 220);
    for (const node of visible) {
      node.vx += (500 - node.x) * .0007;
      node.vy += (300 - node.y) * .0007;
    }
    // Sample repulsion on large archives to keep dragging responsive.
    const step = Math.max(1, Math.ceil(n / 220));
    for (let i = 0; i < n; i++) {
      const a = visible[i];
      for (let j = i + 1; j < n; j += step) {
        const b = visible[j], dx = a.x - b.x || .1, dy = a.y - b.y || .1;
        const square = dx * dx + dy * dy + 70;
        const force = Math.min(3, 1600 * step / square);
        const distance = Math.sqrt(square);
        a.vx += dx / distance * force;
        a.vy += dy / distance * force;
        b.vx -= dx / distance * force;
        b.vy -= dy / distance * force;
        // Separate the complete node + two-line label boxes, not only circles.
        const boxDx = a.x - b.x || .1;
        const boxDy = a.y + a.boxCenterY - b.y - b.boxCenterY || .1;
        const overlapX = a.boxHalfWidth + b.boxHalfWidth - Math.abs(boxDx);
        const overlapY = (a.boxHeight + b.boxHeight) / 2 - Math.abs(boxDy);
        if (overlapX > 0 && overlapY > 0) {
          if (overlapX < overlapY) {
            const push = Math.sign(boxDx) * Math.min(9, overlapX * .2);
            a.vx += push; b.vx -= push;
          } else {
            const push = Math.sign(boxDy) * Math.min(9, overlapY * .2);
            a.vy += push; b.vy -= push;
          }
        }
      }
    }
    for (const link of links) {
      const dx = link.b.x - link.a.x, dy = link.b.y - link.a.y;
      const distance = Math.hypot(dx, dy) || 1;
      const desired = link.type === 'category' ? 260 : 220;
      const force = (distance - desired) * .012;
      link.a.vx += dx / distance * force; link.a.vy += dy / distance * force;
      link.b.vx -= dx / distance * force; link.b.vy -= dy / distance * force;
    }
    for (const node of visible) {
      node.vx *= .72; node.vy *= .72;
      if (!node.fixed) {
        node.x = Math.max(node.boxHalfWidth + 15, Math.min(985 - node.boxHalfWidth, node.x + node.vx * cooling));
        node.y = Math.max(radius[node.type] + 40, Math.min(625 - node.boxBottom, node.y + node.vy * cooling));
      }
      positions.set(node.id, {x: node.x, y: node.y});
    }
    draw();
    frame++;
    animation = frame < 220 || drag ? requestAnimationFrame(tick) : null;
  };
  const restart = () => {
    frame = 0;
    if (!animation) animation = requestAnimationFrame(tick);
  };
  const render = () => {
    if (animation) cancelAnimationFrame(animation);
    animation = null;
    edgeLayer.replaceChildren(); nodeLayer.replaceChildren();
    const query = (search?.value || '').trim().toLocaleLowerCase();
    const chosen = category?.value || '';
    const candidates = graph.nodes.filter(n => (!chosen || n.category === chosen) &&
      (!showSources || showSources.checked || n.type !== 'source'));
    const eligible = new Set(candidates.map(n => n.id));
    const matches = new Set(candidates.filter(n => !query || n.label.toLocaleLowerCase().includes(query)).map(n => n.id));
    const context = new Set(matches);
    if (query) for (const edge of graph.edges) {
      if (matches.has(edge.source) && eligible.has(edge.target)) context.add(edge.target);
      if (matches.has(edge.target) && eligible.has(edge.source)) context.add(edge.source);
    }
    visible = candidates.filter(n => context.has(n.id)).map((node, i, all) => {
      const angle = i * Math.PI * 2 / Math.max(1, all.length);
      const saved = positions.get(node.id);
      return {...node, x: saved?.x ?? 500 + Math.cos(angle) * (200 + i % 5 * 20),
              y: saved?.y ?? 300 + Math.sin(angle) * (180 + i % 5 * 15), vx: 0, vy: 0};
    });
    const byId = new Map(visible.map(n => [n.id, n]));
    links = graph.edges.filter(e => byId.has(e.source) && byId.has(e.target)).map(edge => {
      const element = make('line', {stroke: edge.type === 'category' ? 'var(--line)' : edge.type === 'evidence' ? 'var(--graph-category)' : 'var(--graph-source)',
        'stroke-width': edge.type === 'related' ? 1.8 : 1.2,
        'stroke-dasharray': edge.type === 'evidence' ? '5 4' : edge.type === 'category' ? '2 4' : 'none'});
      const title = make('title'); title.textContent = edge.label; element.append(title);
      edgeLayer.append(element);
      return {...edge, a: byId.get(edge.source), b: byId.get(edge.target), element};
    });
    for (const node of visible) {
      const element = make('g', {tabindex: 0, role: 'button', 'aria-label': `${types[node.type]}：${node.label}`});
      element.style.cursor = 'pointer';
      const circle = make('circle', {r: radius[node.type], fill: colors[node.type], stroke: 'var(--paper)', 'stroke-width': 2});
      const lines = labelLines(node.label);
      const labelY = radius[node.type] + labelFont + 9;
      const label = make('text', {x: 0, y: labelY, fill: 'var(--ink)', 'font-size': labelFont,
                                'text-anchor': 'middle', 'font-weight': node.type === 'wiki' ? 600 : 500,
                                'font-family': 'system-ui,sans-serif', 'paint-order': 'stroke', stroke: 'var(--soft)', 'stroke-width': 4});
      lines.forEach((text, index) => {
        const line = make('tspan', {x: 0, dy: index ? 23 : 0});
        line.textContent = text; label.append(line);
      });
      node.boxHalfWidth = Math.max(radius[node.type] + 12, ...lines.map(text => textWidth(text) / 2 + 15));
      node.boxBottom = labelY + (lines.length - 1) * 23 + 13;
      const boxTop = -radius[node.type] - 12;
      node.boxHeight = node.boxBottom - boxTop;
      node.boxCenterY = (node.boxBottom + boxTop) / 2;
      const title = make('title'); title.textContent = `${node.label}\n${types[node.type]} · 点击查看关系，双击打开详情`;
      element.append(circle, label, title);
      element.addEventListener('pointerdown', event => {
        event.stopPropagation();
        if (event.button !== 0) return;
        const p = point(event);
        drag = {node, origin: p, last: p, moved: false, pointerId: event.pointerId};
        node.fixed = true;
        svg.setPointerCapture(event.pointerId);
        restart();
      });
      element.addEventListener('dblclick', () => { window.location.href = node.url; });
      element.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); window.location.href = node.url; }
        if (event.key === ' ') { event.preventDefault(); inspect(node); }
      });
      node.element = element; node.circle = circle;
      nodeLayer.append(element);
    }
    if (empty) {
      empty.hidden = !!visible.length;
      empty.textContent = graph.nodes.length ? '没有匹配的节点。请调整搜索、分类或来源筛选。' :
        '知识图谱尚为空。导入并分类原始资料后会出现来源节点；审核 Wiki 草稿后会建立知识与证据的连接。';
    }
    say(`${visible.length} 个可见节点 · ${links.length} 条关系${query ? ' · 包含搜索匹配节点的直接邻居' : ''}。拖动节点调整位置，拖动空白平移，滚轮缩放；点击查看关系，双击打开详情。`);
    selected = null;
    draw(); restart();
  };
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const p = point(event);
    drag = {origin: p, last: p, pointerId: event.pointerId};
    svg.setPointerCapture(event.pointerId);
    svg.style.cursor = 'grabbing';
  });
  svg.addEventListener('pointermove', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const p = point(event), dx = p.x - drag.last.x, dy = p.y - drag.last.y;
    if (Math.hypot(p.x - drag.origin.x, p.y - drag.origin.y) > 4) drag.moved = true;
    if (drag.node) {
      drag.node.x += dx / view.k; drag.node.y += dy / view.k; draw();
    } else { view.x += dx; view.y += dy; transform(); }
    drag.last = p;
  });
  const endDrag = (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.node) {
      drag.node.fixed = false;
      if (!drag.moved && event.type !== 'pointercancel') inspect(drag.node);
      positions.set(drag.node.id, {x: drag.node.x, y: drag.node.y});
    }
    drag = null; svg.style.cursor = 'grab';
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  };
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('wheel', event => {
    event.preventDefault();
    const p = point(event), next = Math.max(.25, Math.min(5, view.k * Math.exp(-event.deltaY * .001)));
    view.x = p.x - (p.x - view.x) * next / view.k;
    view.y = p.y - (p.y - view.y) * next / view.k;
    view.k = next; transform();
  }, {passive: false});
  svg.addEventListener('keydown', event => {
    if (event.key === '0' || event.key === 'Home') { view = {x: 0, y: 0, k: 1}; transform(); }
  });
  search?.addEventListener('input', render);
  category?.addEventListener('change', render);
  showSources?.addEventListener('change', render);

  say('正在读取本地知识图谱…');
  fetch('/api/graph', {headers: {Accept: 'application/json'}}).then(response => {
    if (!response.ok) throw new Error('读取失败');
    return response.json();
  }).then(data => {
    if (!Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error('图谱格式错误');
    graph = data; render();
  }).catch(() => {
    say('知识图谱加载失败，请刷新页面重试。');
    if (empty) { empty.hidden = false; empty.textContent = '暂时无法读取图谱数据。'; }
  });
})();
