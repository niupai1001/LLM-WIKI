/* Local PDF.js reader. PDF page pixels preserve the publisher's original layout. */
const root = document.getElementById('pdf-reader');

if (root) initializeReader(root);

async function initializeReader(root) {
  const MAX_PIXELS = 6_000_000;
  const MAX_DIMENSION = 4096;
  const MIN_SCALE = 0.2;
  const MAX_SCALE = 5;
  const vendor = new URL('./vendor/pdfjs/', import.meta.url);
  let source;
  try {
    source = new URL(root.dataset.pdfUrl, window.location.href);
    if (source.origin !== window.location.origin || !['http:', 'https:'].includes(source.protocol)) {
      throw new Error('PDF must come from this workspace');
    }
  } catch {
    root.textContent = 'PDF 地址无效，请从原始资料页面重新打开。';
    return;
  }

  root.classList.add('pdf-reader');
  const toolbar = document.createElement('div');
  toolbar.className = 'pdf-reader-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', 'PDF 阅读工具');
  const stage = document.createElement('div');
  stage.className = 'pdf-reader-stage';
  stage.tabIndex = 0;
  stage.setAttribute('role', 'region');
  stage.setAttribute('aria-label', 'PDF 原件页面');
  const status = document.createElement('p');
  status.className = 'pdf-reader-status';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.textContent = '正在加载 PDF 原件…';

  const button = (label, action, title = label) => {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = 'button';
    element.textContent = label;
    element.title = title;
    element.addEventListener('click', action);
    toolbar.append(element);
    return element;
  };
  const previous = button('上一页', () => changePage(state.page - 1));
  const pageLabel = document.createElement('label');
  pageLabel.className = 'pdf-reader-page-label';
  pageLabel.textContent = '页码 ';
  const pageInput = document.createElement('input');
  pageInput.className = 'pdf-reader-page-input';
  pageInput.type = 'number';
  pageInput.min = '1';
  pageInput.value = '1';
  pageInput.inputMode = 'numeric';
  pageInput.setAttribute('aria-label', '跳转到 PDF 页码');
  const pageTotal = document.createElement('span');
  pageTotal.className = 'pdf-reader-page-total';
  pageTotal.textContent = ' / —';
  pageLabel.append(pageInput, pageTotal);
  toolbar.append(pageLabel);
  const next = button('下一页', () => changePage(state.page + 1));
  const fit = button('适合宽度', () => {
    state.fitWidth = true;
    requestRender();
  });
  const zoomOut = button('−', () => changeZoom(1 / 1.2), '缩小');
  zoomOut.setAttribute('aria-label', '缩小 PDF');
  const zoomLabel = document.createElement('span');
  zoomLabel.className = 'pdf-reader-zoom';
  zoomLabel.textContent = '—';
  toolbar.append(zoomLabel);
  const zoomIn = button('＋', () => changeZoom(1.2), '放大');
  zoomIn.setAttribute('aria-label', '放大 PDF');
  const original = document.createElement('a');
  original.className = 'button pdf-reader-original';
  original.href = source.href;
  original.target = '_blank';
  original.rel = 'noopener noreferrer';
  original.textContent = '打开原 PDF ↗';
  toolbar.append(original);
  root.replaceChildren(toolbar, stage, status);

  const state = {
    pdf: null, page: 1, scale: 1, fitWidth: true, revision: 0,
    pumping: false, renderTask: null, disposed: false, canvas: null,
  };
  let loadingTask = null;
  let resizeTimer = null;
  let observedWidth = 0;
  let observer = null;
  const controls = [previous, next, fit, zoomOut, zoomIn, pageInput];
  controls.forEach(control => { control.disabled = true; });

  function updateControls() {
    const ready = Boolean(state.pdf) && !state.disposed;
    previous.disabled = !ready || state.page <= 1;
    next.disabled = !ready || state.page >= state.pdf.numPages;
    fit.disabled = !ready;
    zoomOut.disabled = !ready || (!state.fitWidth && state.scale <= MIN_SCALE);
    zoomIn.disabled = !ready || (!state.fitWidth && state.scale >= MAX_SCALE);
    pageInput.disabled = !ready;
    pageInput.value = String(state.page);
    if (ready) {
      pageInput.max = String(state.pdf.numPages);
      pageTotal.textContent = ` / ${state.pdf.numPages}`;
    }
    fit.setAttribute('aria-pressed', String(state.fitWidth));
  }

  function changePage(value) {
    if (!state.pdf || !Number.isFinite(value)) return;
    const requested = Math.max(1, Math.min(state.pdf.numPages, Math.trunc(value)));
    if (requested === state.page) {
      updateControls();
      return;
    }
    state.page = requested;
    stage.scrollTop = 0;
    requestRender();
  }

  function changeZoom(factor) {
    if (!state.pdf) return;
    state.fitWidth = false;
    state.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, state.scale * factor));
    requestRender();
  }

  pageInput.addEventListener('change', () => {
    if (pageInput.value.trim()) changePage(Number(pageInput.value));
    updateControls();
  });
  pageInput.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (pageInput.value.trim()) changePage(Number(pageInput.value));
      updateControls();
      pageInput.blur();
    }
  });
  stage.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key === 'ArrowLeft' || event.key === 'PageUp') {
      event.preventDefault();
      changePage(state.page - 1);
    } else if (event.key === 'ArrowRight' || event.key === 'PageDown') {
      event.preventDefault();
      changePage(state.page + 1);
    }
  });

  function discardCanvas(canvas) {
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
  }

  function showFailure(message) {
    status.textContent = `${message} 可以使用“打开原 PDF”查看或下载原件。`;
    status.classList.add('error');
    root.dataset.pdfState = 'error';
    stage.setAttribute('aria-busy', 'false');
  }

  function requestRender() {
    if (!state.pdf || state.disposed) return;
    state.revision += 1;
    state.renderTask?.cancel();
    updateControls();
    if (!state.pumping) pumpRenders();
  }

  async function pumpRenders() {
    state.pumping = true;
    try {
      while (!state.disposed) {
        const revision = state.revision;
        const pageNumber = state.page;
        stage.setAttribute('aria-busy', 'true');
        root.dataset.pdfState = 'rendering';
        status.classList.remove('error');
        status.textContent = `正在呈现第 ${pageNumber} 页…`;
        try {
          await renderPage(pageNumber, revision);
        } catch (error) {
          if (revision === state.revision && !state.disposed && error?.name !== 'RenderingCancelledException') {
            discardCanvas(state.canvas);
            state.canvas = null;
            stage.replaceChildren();
            showFailure(`第 ${pageNumber} 页暂时无法呈现。`);
          }
        }
        if (revision === state.revision) break;
      }
    } finally {
      state.pumping = false;
      state.renderTask = null;
      if (!state.disposed) stage.setAttribute('aria-busy', 'false');
    }
  }

  async function renderPage(pageNumber, revision) {
    let page = null;
    let candidate = null;
    try {
      page = await state.pdf.getPage(pageNumber);
      if (state.disposed || revision !== state.revision) return;
      const natural = page.getViewport({ scale: 1 });
      const padding = getComputedStyle(stage);
      const usableWidth = Math.max(180, stage.clientWidth - parseFloat(padding.paddingLeft || 0) - parseFloat(padding.paddingRight || 0));
      const scale = state.fitWidth ? Math.min(MAX_SCALE, usableWidth / natural.width) : state.scale;
      state.scale = scale;
      const viewport = page.getViewport({ scale });
      // A single visible page plus one render candidate. Limit physical pixels,
      // including on Retina screens, so large pages cannot exhaust canvas memory.
      const outputScale = Math.min(
        window.devicePixelRatio || 1, 2,
        Math.sqrt(MAX_PIXELS / (viewport.width * viewport.height)),
        MAX_DIMENSION / viewport.width, MAX_DIMENSION / viewport.height,
      );
      candidate = document.createElement('canvas');
      candidate.className = 'pdf-reader-canvas';
      candidate.setAttribute('role', 'img');
      candidate.setAttribute('aria-label', `PDF 原件，第 ${pageNumber} 页，共 ${state.pdf.numPages} 页`);
      candidate.width = Math.max(1, Math.floor(viewport.width * outputScale));
      candidate.height = Math.max(1, Math.floor(viewport.height * outputScale));
      candidate.style.width = `${viewport.width}px`;
      candidate.style.height = `${viewport.height}px`;
      candidate.style.maxWidth = 'none';
      const context = candidate.getContext('2d', { alpha: false });
      if (!context) throw new Error('Canvas unavailable');
      const task = page.render({
        canvasContext: context,
        viewport,
        transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
        background: '#ffffff',
        intent: 'display',
      });
      state.renderTask = task;
      await task.promise;
      if (state.disposed || revision !== state.revision) return;
      const previousCanvas = state.canvas;
      stage.replaceChildren(candidate);
      state.canvas = candidate;
      discardCanvas(previousCanvas);
      zoomLabel.textContent = `${Math.round(scale * 100)}%`;
      status.textContent = `第 ${pageNumber} / ${state.pdf.numPages} 页 · PDF 原件`;
      root.dataset.pdfState = 'ready';
      stage.setAttribute('aria-busy', 'false');
      updateControls();
    } finally {
      if (candidate !== state.canvas) discardCanvas(candidate);
      page?.cleanup();
    }
  }

  const resized = () => {
    if (!state.fitWidth || !state.pdf || state.disposed) return;
    const width = stage.clientWidth;
    if (Math.abs(width - observedWidth) < 2) return;
    observedWidth = width;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(requestRender, 120);
  };
  if ('ResizeObserver' in window) {
    observer = new ResizeObserver(resized);
    observer.observe(stage);
  } else {
    window.addEventListener('resize', resized);
  }

  const dispose = () => {
    if (state.disposed) return;
    state.disposed = true;
    state.revision += 1;
    clearTimeout(resizeTimer);
    observer?.disconnect();
    window.removeEventListener('resize', resized);
    state.renderTask?.cancel();
    discardCanvas(state.canvas);
    Promise.resolve(loadingTask?.destroy()).catch(() => {});
  };
  // Preserve the reader when the browser puts the page into its back/forward cache.
  window.addEventListener('pagehide', event => { if (!event.persisted) dispose(); });

  try {
    const pdfjs = await import(new URL('pdf.mjs', vendor).href);
    if (state.disposed) return;
    pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdf.worker.mjs', vendor).href;
    loadingTask = pdfjs.getDocument({
      url: source.href,
      cMapUrl: new URL('cmaps/', vendor).href,
      cMapPacked: true,
      standardFontDataUrl: new URL('standard_fonts/', vendor).href,
      wasmUrl: new URL('wasm/', vendor).href,
      useWorkerFetch: true,
      isEvalSupported: false,
      enableXfa: false,
    });
    loadingTask.onProgress = ({ loaded, total }) => {
      if (!state.pdf && !state.disposed && total > 0) {
        status.textContent = `正在加载 PDF 原件… ${Math.min(100, Math.round(loaded / total * 100))}%`;
      }
    };
    loadingTask.onPassword = () => {
      showFailure('这份 PDF 需要密码。');
      Promise.resolve(loadingTask.destroy()).catch(() => {});
    };
    state.pdf = await loadingTask.promise;
    if (state.disposed) return;
    const initialPage = Number(new URL(location.href).searchParams.get('page'));
    if (Number.isInteger(initialPage) && initialPage >= 1 && initialPage <= state.pdf.numPages) state.page = initialPage;
    observedWidth = stage.clientWidth;
    updateControls();
    requestRender();
  } catch {
    if (!state.disposed) showFailure('PDF 暂时无法加载。');
  }
}
