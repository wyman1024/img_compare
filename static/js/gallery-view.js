(() => {
  'use strict';

  const MIN_TITLE_SIZE = 10;
  const MAX_TITLE_SIZE = 32;
  const DEFAULT_TITLE_SIZE = 16;

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

  const isFullscreenShortcut = (event) => (
    String(event?.key || '').toLowerCase() === 'f'
    && Boolean(event?.shiftKey)
    && !event?.ctrlKey
    && !event?.altKey
    && !event?.metaKey
    && !event?.repeat
  );

  const isFitShortcut = (event) => (
    String(event?.key || '').toLowerCase() === 'f'
    && !event?.shiftKey
    && !event?.ctrlKey
    && !event?.altKey
    && !event?.metaKey
  );

  const calculateGridSpan = (height, rowHeight, gap) => {
    const safeRowHeight = Math.max(1, Number(rowHeight) || 1);
    const safeGap = Math.max(0, Number(gap) || 0);
    return Math.max(1, Math.ceil((Math.max(0, height) + safeGap) / (safeRowHeight + safeGap)));
  };

  const calculateFitScale = (viewportWidth, viewportHeight, imageWidth, imageHeight) => {
    if (![viewportWidth, viewportHeight, imageWidth, imageHeight].every((value) => value > 0)) return 1;
    return Math.min(viewportWidth / imageWidth, viewportHeight / imageHeight);
  };

  const calculateBaseScale = (scaleMode, viewportWidth, viewportHeight, imageWidth, imageHeight) => (
    scaleMode === 'actual'
      ? 1
      : calculateFitScale(viewportWidth, viewportHeight, imageWidth, imageHeight)
  );

  const normalizedToTransform = ({
    centerX,
    centerY,
    zoom,
    scaleMode = 'fit',
    viewportWidth,
    viewportHeight,
    imageWidth,
    imageHeight,
  }) => {
    const scale = calculateBaseScale(
      scaleMode,
      viewportWidth,
      viewportHeight,
      imageWidth,
      imageHeight,
    ) * zoom;
    return {
      scale,
      left: viewportWidth / 2 - centerX * imageWidth * scale,
      top: viewportHeight / 2 - centerY * imageHeight * scale,
    };
  };

  const screenToNormalized = ({
    screenX,
    screenY,
    centerX,
    centerY,
    zoom,
    scaleMode = 'fit',
    viewportWidth,
    viewportHeight,
    imageWidth,
    imageHeight,
  }) => {
    const scale = calculateBaseScale(
      scaleMode,
      viewportWidth,
      viewportHeight,
      imageWidth,
      imageHeight,
    ) * zoom;
    return {
      x: centerX + (screenX - viewportWidth / 2) / (imageWidth * scale),
      y: centerY + (screenY - viewportHeight / 2) / (imageHeight * scale),
    };
  };

  const centerForZoomAtPoint = ({
    screenX,
    screenY,
    centerX,
    centerY,
    oldZoom,
    newZoom,
    scaleMode = 'fit',
    viewportWidth,
    viewportHeight,
    imageWidth,
    imageHeight,
  }) => {
    const sourcePoint = screenToNormalized({
      screenX,
      screenY,
      centerX,
      centerY,
      zoom: oldZoom,
      scaleMode,
      viewportWidth,
      viewportHeight,
      imageWidth,
      imageHeight,
    });
    const nextScale = calculateBaseScale(
      scaleMode,
      viewportWidth,
      viewportHeight,
      imageWidth,
      imageHeight,
    ) * newZoom;
    return {
      x: sourcePoint.x - (screenX - viewportWidth / 2) / (imageWidth * nextScale),
      y: sourcePoint.y - (screenY - viewportHeight / 2) / (imageHeight * nextScale),
    };
  };

  const panCenter = ({
    centerX,
    centerY,
    deltaX,
    deltaY,
    zoom,
    scaleMode = 'fit',
    viewportWidth,
    viewportHeight,
    imageWidth,
    imageHeight,
  }) => {
    const scale = calculateBaseScale(
      scaleMode,
      viewportWidth,
      viewportHeight,
      imageWidth,
      imageHeight,
    ) * zoom;
    return {
      x: centerX - deltaX / (imageWidth * scale),
      y: centerY - deltaY / (imageHeight * scale),
    };
  };

  const normalizeImageTitle = (value) => typeof value === 'string' ? value.trim() : '';

  const hasImageTitles = (...values) => values.some((value) => Boolean(normalizeImageTitle(value)));

  const clampTitleSize = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return DEFAULT_TITLE_SIZE;
    return clamp(Math.round(numeric), MIN_TITLE_SIZE, MAX_TITLE_SIZE);
  };

  const calculateSplitTitleGeometry = (split) => {
    const numeric = Number(split);
    const normalized = clamp(Number.isFinite(numeric) ? numeric : 50, 0, 100);
    return {
      split: normalized,
      leftCenter: normalized / 2,
      rightCenter: normalized + (100 - normalized) / 2,
      leftWidth: normalized,
      rightWidth: 100 - normalized,
      showLeft: normalized > 0,
      showRight: normalized < 100,
    };
  };

  const testHooks = {
    calculateGridSpan,
    calculateFitScale,
    calculateBaseScale,
    normalizedToTransform,
    screenToNormalized,
    centerForZoomAtPoint,
    panCenter,
    normalizeImageTitle,
    hasImageTitles,
    clampTitleSize,
    calculateSplitTitleGeometry,
    isFullscreenShortcut,
    isFitShortcut,
  };
  globalThis.GalleryViewUtils = testHooks;
  globalThis.__galleryTestHooks = Object.assign(globalThis.__galleryTestHooks || {}, testHooks);

  if (typeof document === 'undefined') return;
  const grid = document.querySelector('#gallery-grid');
  if (!grid) return;

  const cards = [...grid.querySelectorAll('.gallery-card')];
  const dialog = document.querySelector('#gallery-dialog');
  const dialogTitle = document.querySelector('#dialog-title');
  const dialogCounter = document.querySelector('#dialog-counter');
  const previousButton = document.querySelector('#dialog-previous');
  const nextButton = document.querySelector('#dialog-next');
  const closeButton = document.querySelector('#dialog-close');
  const viewer = document.querySelector('#dialog-viewer');
  const stage = document.querySelector('#dialog-stage');
  const divider = document.querySelector('#dialog-divider');
  const actionButtons = [...document.querySelectorAll('[data-gallery-action]')];
  const zoomOutput = document.querySelector('#dialog-zoom');
  const titleSizeInput = document.querySelector('#dialog-title-size');
  const titleSizeOutput = document.querySelector('#dialog-title-size-output');
  const imageA = document.querySelector('#dialog-image-a');
  const imageB = document.querySelector('#dialog-image-b');
  const labelA = document.querySelector('#dialog-label-a');
  const labelB = document.querySelector('#dialog-label-b');
  const imageTitleA = document.querySelector('#dialog-image-title-a');
  const imageTitleB = document.querySelector('#dialog-image-title-b');
  const loading = document.querySelector('#dialog-loading');
  const nameA = document.querySelector('#dialog-a-name');
  const nameB = document.querySelector('#dialog-b-name');
  const metaA = document.querySelector('#dialog-a-meta');
  const metaB = document.querySelector('#dialog-b-meta');
  const ratioNote = document.querySelector('#dialog-ratio-note');
  const fullscreenButton = document.querySelector('#dialog-fullscreen');
  const fullscreenLabel = fullscreenButton.querySelector('.fullscreen-label');
  const copyButton = document.querySelector('#copy-button');
  const copyLabel = copyButton.querySelector('.copy-label');
  const shareInput = document.querySelector('#share-url');
  let currentIndex = 0;
  let loadGeneration = 0;
  const state = {
    centerX: 0.5,
    centerY: 0.5,
    zoom: 1,
    scaleMode: 'fit',
    split: 50,
    titleSize: DEFAULT_TITLE_SIZE,
    lastSide: 'A',
  };

  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return '—';
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    const megabytes = bytes / (1024 * 1024);
    return `${megabytes >= 100 ? Math.round(megabytes) : megabytes.toFixed(1)} MB`;
  };

  const previewImages = [...grid.querySelectorAll('.gallery-card-image[data-src]')];
  const loadPreview = (image) => {
    if (!image.dataset.src || image.hasAttribute('src')) return;
    image.src = image.dataset.src;
    image.removeAttribute('data-src');
  };
  if ('IntersectionObserver' in window) {
    const lazyObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        loadPreview(entry.target);
        lazyObserver.unobserve(entry.target);
      });
    }, { rootMargin: '0px', threshold: 0.01 });
    previewImages.forEach((image) => lazyObserver.observe(image));
  } else {
    previewImages.forEach(loadPreview);
  }

  const resizeGridItem = (card) => {
    const styles = getComputedStyle(grid);
    const rowHeight = parseFloat(styles.gridAutoRows);
    const gap = parseFloat(styles.rowGap);
    card.closest('.gallery-grid-item').style.gridRowEnd = `span ${calculateGridSpan(card.getBoundingClientRect().height, rowHeight, gap)}`;
  };

  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver((entries) => {
      entries.forEach((entry) => resizeGridItem(entry.target));
    });
    cards.forEach((card) => observer.observe(card));
  } else {
    const resizeAll = () => cards.forEach(resizeGridItem);
    window.addEventListener('resize', resizeAll);
    window.requestAnimationFrame(resizeAll);
  }

  const activeFullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;

  const updateFullscreenButton = () => {
    const active = activeFullscreenElement() === viewer || viewer.classList.contains('is-faux-fullscreen');
    fullscreenButton.classList.toggle('is-active', active);
    fullscreenButton.setAttribute('aria-pressed', String(active));
    fullscreenLabel.textContent = active ? '退出全屏' : '全屏';
    fullscreenButton.title = active
      ? '退出全屏图片对比（Shift + F）'
      : '全屏查看图片对比（Shift + F）';
    window.requestAnimationFrame(renderTransforms);
  };

  const leaveFauxFullscreen = () => {
    viewer.classList.remove('is-faux-fullscreen');
    dialog.classList.remove('has-faux-fullscreen');
    document.body.classList.remove('gallery-viewer-fullscreen');
    updateFullscreenButton();
  };

  const enterFauxFullscreen = () => {
    viewer.classList.add('is-faux-fullscreen');
    dialog.classList.add('has-faux-fullscreen');
    document.body.classList.add('gallery-viewer-fullscreen');
    updateFullscreenButton();
  };

  async function toggleFullscreen() {
    if (viewer.classList.contains('is-faux-fullscreen')) {
      leaveFauxFullscreen();
      return;
    }
    if (activeFullscreenElement()) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) await exit.call(document);
      return;
    }
    const request = viewer.requestFullscreen || viewer.webkitRequestFullscreen;
    if (!request) {
      enterFauxFullscreen();
      return;
    }
    try {
      await request.call(viewer);
    } catch {
      enterFauxFullscreen();
    }
  }
  fullscreenButton.addEventListener('click', toggleFullscreen);

  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);

  const imageForSide = (side) => side === 'B' ? imageB : imageA;

  const imageDimensions = (image) => ({
    width: image.naturalWidth || Number(image.dataset.width) || 1,
    height: image.naturalHeight || Number(image.dataset.height) || 1,
  });

  const updateZoomCopy = () => {
    if (state.scaleMode === 'fit' && Math.abs(state.zoom - 1) < 0.005) {
      zoomOutput.textContent = '适应';
      return;
    }
    zoomOutput.textContent = `${Math.round(state.zoom * 100)}%`;
  };

  const applyTitleSize = (value) => {
    state.titleSize = clampTitleSize(value);
    viewer.style.setProperty('--gallery-dialog-title-size', `${state.titleSize}px`);
    if (titleSizeInput) titleSizeInput.value = String(state.titleSize);
    if (titleSizeOutput) titleSizeOutput.textContent = `${state.titleSize}px`;
  };

  const updateTitleSizeAvailability = () => {
    if (!titleSizeInput) return;
    titleSizeInput.disabled = !hasImageTitles(imageTitleA.textContent, imageTitleB.textContent);
  };

  if (titleSizeInput) {
    titleSizeInput.min = String(MIN_TITLE_SIZE);
    titleSizeInput.max = String(MAX_TITLE_SIZE);
    titleSizeInput.step = '1';
    titleSizeInput.disabled = true;
    titleSizeInput.addEventListener('input', () => applyTitleSize(titleSizeInput.value));
  }
  applyTitleSize(DEFAULT_TITLE_SIZE);

  const applySplit = () => {
    const geometry = calculateSplitTitleGeometry(state.split);
    state.split = geometry.split;
    stage.style.setProperty('--position', `${geometry.split}%`);
    stage.style.setProperty('--dialog-left-title-center', `${geometry.leftCenter}%`);
    stage.style.setProperty('--dialog-right-title-center', `${geometry.rightCenter}%`);
    stage.style.setProperty('--dialog-left-title-width', `${geometry.leftWidth}%`);
    stage.style.setProperty('--dialog-right-title-width', `${geometry.rightWidth}%`);
    divider.setAttribute('aria-valuenow', geometry.split.toFixed(1));
    imageTitleA.hidden = !normalizeImageTitle(imageTitleA.textContent) || !geometry.showLeft;
    imageTitleB.hidden = !normalizeImageTitle(imageTitleB.textContent) || !geometry.showRight;
  };

  const renderTransforms = () => {
    if (!stage.clientWidth || !stage.clientHeight) {
      updateZoomCopy();
      applySplit();
      return;
    }
    [imageA, imageB].forEach((image) => {
      const dimensions = imageDimensions(image);
      const transform = normalizedToTransform({
        centerX: state.centerX,
        centerY: state.centerY,
        zoom: state.zoom,
        scaleMode: state.scaleMode,
        viewportWidth: stage.clientWidth,
        viewportHeight: stage.clientHeight,
        imageWidth: dimensions.width,
        imageHeight: dimensions.height,
      });
      image.style.width = `${dimensions.width}px`;
      image.style.height = `${dimensions.height}px`;
      image.style.transform = `translate3d(${transform.left}px, ${transform.top}px, 0) scale(${transform.scale})`;
    });
    updateZoomCopy();
    applySplit();
  };

  const clampCenter = (point) => {
    state.centerX = clamp(point.x, 0, 1);
    state.centerY = clamp(point.y, 0, 1);
  };

  const zoomAt = (factor, screenX, screenY, side = state.lastSide) => {
    if (!stage.clientWidth || !stage.clientHeight) return;
    const oldZoom = state.zoom;
    const newZoom = clamp(oldZoom * factor, 0.1, 64);
    if (Math.abs(oldZoom - newZoom) < 0.00001) return;
    const dimensions = imageDimensions(imageForSide(side));
    const center = centerForZoomAtPoint({
      screenX,
      screenY,
      centerX: state.centerX,
      centerY: state.centerY,
      oldZoom,
      newZoom,
      scaleMode: state.scaleMode,
      viewportWidth: stage.clientWidth,
      viewportHeight: stage.clientHeight,
      imageWidth: dimensions.width,
      imageHeight: dimensions.height,
    });
    state.zoom = newZoom;
    state.lastSide = side;
    clampCenter(center);
    renderTransforms();
  };

  const panBy = (deltaX, deltaY, side = state.lastSide) => {
    if (!stage.clientWidth || !stage.clientHeight) return;
    const dimensions = imageDimensions(imageForSide(side));
    clampCenter(panCenter({
      centerX: state.centerX,
      centerY: state.centerY,
      deltaX,
      deltaY,
      zoom: state.zoom,
      scaleMode: state.scaleMode,
      viewportWidth: stage.clientWidth,
      viewportHeight: stage.clientHeight,
      imageWidth: dimensions.width,
      imageHeight: dimensions.height,
    }));
    state.lastSide = side;
    renderTransforms();
  };

  const resetView = (includeSplit = false) => {
    state.centerX = 0.5;
    state.centerY = 0.5;
    state.zoom = 1;
    state.scaleMode = 'fit';
    state.lastSide = 'A';
    if (includeSplit) state.split = 50;
    renderTransforms();
  };

  const showActualPixels = () => {
    state.scaleMode = 'actual';
    state.zoom = 1;
    renderTransforms();
  };

  const sideAtClientX = (clientX) => {
    const rect = stage.getBoundingClientRect();
    if (!rect.width) return state.lastSide;
    return ((clientX - rect.left) / rect.width) * 100 <= state.split ? 'A' : 'B';
  };

  const setSplitFromClientX = (clientX) => {
    const rect = stage.getBoundingClientRect();
    if (!rect.width) return;
    state.split = clamp(((clientX - rect.left) / rect.width) * 100, 0, 100);
    applySplit();
  };

  const wireDivider = () => {
    divider.setAttribute('aria-valuemin', '0');
    divider.setAttribute('aria-valuemax', '100');
    divider.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      divider.setPointerCapture(event.pointerId);
      divider.classList.add('is-dragging');
      setSplitFromClientX(event.clientX);
    });
    divider.addEventListener('pointermove', (event) => {
      if (!divider.hasPointerCapture(event.pointerId)) return;
      event.preventDefault();
      setSplitFromClientX(event.clientX);
    });
    const stop = (event) => {
      if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
      divider.classList.remove('is-dragging');
    };
    divider.addEventListener('pointerup', stop);
    divider.addEventListener('pointercancel', stop);
    divider.addEventListener('lostpointercapture', stop);
    divider.addEventListener('keydown', (event) => {
      const step = event.shiftKey ? 5 : 1;
      if (event.key === 'ArrowLeft') state.split = clamp(state.split - step, 0, 100);
      else if (event.key === 'ArrowRight') state.split = clamp(state.split + step, 0, 100);
      else if (event.key === 'Home') state.split = 0;
      else if (event.key === 'End') state.split = 100;
      else return;
      event.preventDefault();
      applySplit();
    });
  };

  const wireStageGestures = () => {
    const pointers = new Map();
    let lastSingle = null;
    let pinch = null;
    let interactionSide = 'A';
    const point = (event) => ({ id: event.pointerId, x: event.clientX, y: event.clientY });
    const midpoint = (points) => ({
      x: (points[0].x + points[1].x) / 2,
      y: (points[0].y + points[1].y) / 2,
    });
    const distance = (points) => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);

    stage.tabIndex = 0;
    stage.addEventListener('pointerdown', (event) => {
      if (event.target.closest('.gallery-dialog-divider')) return;
      event.preventDefault();
      stage.focus({ preventScroll: true });
      stage.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, point(event));
      stage.classList.add('is-panning');
      if (pointers.size === 1) {
        interactionSide = sideAtClientX(event.clientX);
        state.lastSide = interactionSide;
        lastSingle = point(event);
      }
      if (pointers.size === 2) {
        const points = [...pointers.values()];
        const middle = midpoint(points);
        interactionSide = sideAtClientX(middle.x);
        state.lastSide = interactionSide;
        pinch = { midpoint: middle, distance: distance(points) };
        lastSingle = null;
      }
    });

    stage.addEventListener('pointermove', (event) => {
      if (!pointers.has(event.pointerId)) return;
      event.preventDefault();
      pointers.set(event.pointerId, point(event));
      if (pointers.size === 1) {
        const current = point(event);
        if (lastSingle) panBy(current.x - lastSingle.x, current.y - lastSingle.y, interactionSide);
        lastSingle = current;
        return;
      }
      const points = [...pointers.values()].slice(0, 2);
      const nextMidpoint = midpoint(points);
      const nextDistance = Math.max(1, distance(points));
      if (pinch) {
        const rect = stage.getBoundingClientRect();
        zoomAt(
          nextDistance / Math.max(1, pinch.distance),
          nextMidpoint.x - rect.left,
          nextMidpoint.y - rect.top,
          interactionSide,
        );
        panBy(nextMidpoint.x - pinch.midpoint.x, nextMidpoint.y - pinch.midpoint.y, interactionSide);
      }
      pinch = { midpoint: nextMidpoint, distance: nextDistance };
    });

    const release = (event) => {
      pointers.delete(event.pointerId);
      if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
      if (!pointers.size) {
        stage.classList.remove('is-panning');
        lastSingle = null;
        pinch = null;
      } else if (pointers.size === 1) {
        const remaining = [...pointers.values()][0];
        interactionSide = sideAtClientX(remaining.x);
        state.lastSide = interactionSide;
        lastSingle = remaining;
        pinch = null;
      }
    };
    stage.addEventListener('pointerup', release);
    stage.addEventListener('pointercancel', release);
    stage.addEventListener('lostpointercapture', (event) => {
      if (pointers.has(event.pointerId)) release(event);
    });
    stage.addEventListener('wheel', (event) => {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      const side = sideAtClientX(event.clientX);
      zoomAt(
        Math.exp(-event.deltaY * 0.0015),
        event.clientX - rect.left,
        event.clientY - rect.top,
        side,
      );
    }, { passive: false });
  };

  actionButtons.forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.galleryAction;
    if (action === 'fit') resetView(false);
    if (action === 'actual') showActualPixels();
    if (action === 'reset') resetView(true);
    if (action === 'zoom-in' || action === 'zoom-out') {
      zoomAt(
        action === 'zoom-in' ? 1.25 : 0.8,
        stage.clientWidth / 2,
        stage.clientHeight / 2,
        state.lastSide,
      );
    }
  }));

  wireDivider();
  wireStageGestures();
  if ('ResizeObserver' in window) {
    const viewerResizeObserver = new ResizeObserver(renderTransforms);
    viewerResizeObserver.observe(viewer);
    viewerResizeObserver.observe(stage);
  } else {
    window.addEventListener('resize', renderTransforms);
  }

  const updateNavigation = () => {
    previousButton.disabled = currentIndex <= 0;
    nextButton.disabled = currentIndex >= cards.length - 1;
  };

  const renderImageTitle = (element, value) => {
    const title = normalizeImageTitle(value);
    element.textContent = title;
    if (title) element.title = title;
    else element.removeAttribute('title');
  };

  const renderDialogCard = (index) => {
    const card = cards[index];
    if (!card) return;
    currentIndex = index;
    const data = card.dataset;
    loadGeneration += 1;
    const generation = loadGeneration;
    const settled = new Set();
    dialogTitle.textContent = data.title;
    dialogCounter.textContent = `${index + 1} / ${cards.length}`;
    labelA.textContent = 'A';
    labelB.textContent = 'B';
    nameA.textContent = data.aName;
    nameB.textContent = data.bName;
    nameA.title = data.aName;
    nameB.title = data.bName;
    renderImageTitle(imageTitleA, data.aTitle);
    renderImageTitle(imageTitleB, data.bTitle);
    updateTitleSizeAvailability();
    metaA.textContent = `${data.aWidth} × ${data.aHeight} · ${formatBytes(Number(data.aSize))}`;
    metaB.textContent = `${data.bWidth} × ${data.bHeight} · ${formatBytes(Number(data.bSize))}`;
    ratioNote.hidden = data.aWidth === data.bWidth && data.aHeight === data.bHeight;
    imageA.onload = null;
    imageA.onerror = null;
    imageB.onload = null;
    imageB.onerror = null;
    imageA.removeAttribute('src');
    imageB.removeAttribute('src');
    imageA.dataset.width = data.aWidth;
    imageA.dataset.height = data.aHeight;
    imageB.dataset.width = data.bWidth;
    imageB.dataset.height = data.bHeight;
    resetView(true);
    updateNavigation();

    loading.classList.remove('is-hidden', 'has-error');
    loading.innerHTML = '<span></span> 正在载入原图…';

    const settle = (image, failed = false) => {
      if (generation !== loadGeneration || settled.has(image)) return;
      settled.add(image);
      if (failed) {
        loading.textContent = '原图载入失败，请关闭后重试。';
        loading.classList.add('has-error');
        renderTransforms();
        return;
      }
      if (settled.size === 2 && !loading.classList.contains('has-error')) loading.classList.add('is-hidden');
      renderTransforms();
    };

    const accessibleNameA = normalizeImageTitle(data.aTitle) || data.aName;
    const accessibleNameB = normalizeImageTitle(data.bTitle) || data.bName;
    [
      [imageA, data.aUrl, `${data.title} · A：${accessibleNameA}`],
      [imageB, data.bUrl, `${data.title} · B：${accessibleNameB}`],
    ].forEach(([image, url, alt]) => {
      image.onload = () => settle(image);
      image.onerror = () => settle(image, true);
      image.alt = alt;
      image.src = url;
      if (image.complete) window.queueMicrotask(() => settle(image, !image.naturalWidth));
    });
  };

  const openCard = (index) => {
    renderDialogCard(index);
    if (!dialog.open) dialog.showModal();
    document.body.classList.add('gallery-dialog-open');
    window.requestAnimationFrame(() => {
      renderTransforms();
      divider.focus({ preventScroll: true });
    });
  };

  const closeDialog = async () => {
    if (activeFullscreenElement() === viewer) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) await exit.call(document);
    }
    leaveFauxFullscreen();
    if (dialog.open) dialog.close();
  };

  cards.forEach((card, index) => card.addEventListener('click', () => openCard(index)));
  previousButton.addEventListener('click', () => {
    if (currentIndex > 0) renderDialogCard(currentIndex - 1);
  });
  nextButton.addEventListener('click', () => {
    if (currentIndex < cards.length - 1) renderDialogCard(currentIndex + 1);
  });
  closeButton.addEventListener('click', closeDialog);

  dialog.addEventListener('cancel', (event) => {
    if (viewer.classList.contains('is-faux-fullscreen')) {
      event.preventDefault();
      leaveFauxFullscreen();
    }
  });
  dialog.addEventListener('close', () => {
    loadGeneration += 1;
    imageA.onload = null;
    imageA.onerror = null;
    imageB.onload = null;
    imageB.onerror = null;
    imageA.removeAttribute('src');
    imageB.removeAttribute('src');
    document.body.classList.remove('gallery-dialog-open');
    leaveFauxFullscreen();
    cards[currentIndex]?.focus({ preventScroll: true });
  });

  document.addEventListener('keydown', (event) => {
    if (!dialog.open) return;
    if (event.key === 'Escape' && viewer.classList.contains('is-faux-fullscreen')) {
      event.preventDefault();
      leaveFauxFullscreen();
    } else if (
      isFullscreenShortcut(event)
      && !event.target.closest?.('textarea, select, input:not([type]), input[type="text"], input[type="search"], input[type="url"], input[type="email"], input[type="password"], [contenteditable="true"]')
    ) {
      event.preventDefault();
      void toggleFullscreen();
    } else if (event.target.closest?.('input, select, button, .gallery-dialog-divider')) {
      return;
    } else if (event.key === 'PageUp' && currentIndex > 0) {
      event.preventDefault();
      renderDialogCard(currentIndex - 1);
    } else if (event.key === 'PageDown' && currentIndex < cards.length - 1) {
      event.preventDefault();
      renderDialogCard(currentIndex + 1);
    } else {
      const key = event.key.toLowerCase();
      if (isFitShortcut(event)) resetView(false);
      else if (key === 'r') resetView(true);
      else if (key === '1') showActualPixels();
      else if (['+', '='].includes(event.key)) {
        zoomAt(1.25, stage.clientWidth / 2, stage.clientHeight / 2, state.lastSide);
      } else if (event.key === '-') {
        zoomAt(0.8, stage.clientWidth / 2, stage.clientHeight / 2, state.lastSide);
      } else if (event.key === 'ArrowLeft') panBy(-30, 0, state.lastSide);
      else if (event.key === 'ArrowRight') panBy(30, 0, state.lastSide);
      else if (event.key === 'ArrowUp') panBy(0, -30, state.lastSide);
      else if (event.key === 'ArrowDown') panBy(0, 30, state.lastSide);
      else return;
      event.preventDefault();
    }
  });

  const fallbackCopy = () => {
    shareInput.focus();
    shareInput.select();
    shareInput.setSelectionRange(0, shareInput.value.length);
    return document.execCommand('copy');
  };

  copyButton.addEventListener('click', async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(shareInput.value);
      else if (!fallbackCopy()) throw new Error('copy failed');
      copyButton.classList.add('is-copied');
      copyLabel.textContent = '链接已复制';
      window.setTimeout(() => {
        copyButton.classList.remove('is-copied');
        copyLabel.textContent = '复制图集链接';
      }, 1800);
    } catch {
      shareInput.focus();
      shareInput.select();
      copyLabel.textContent = '地址已选中，请复制';
    }
  });
})();
