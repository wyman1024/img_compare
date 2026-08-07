(() => {
  'use strict';

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

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

  window.__analyzeTestHooks = Object.freeze({
    calculateFitScale,
    calculateBaseScale,
    normalizedToTransform,
    screenToNormalized,
    centerForZoomAtPoint,
    panCenter,
  });

  const projectNode = document.querySelector('#analysis-project-data');
  const viewer = document.querySelector('#analysis-viewer');
  const workspace = document.querySelector('#analysis-workspace');
  if (!projectNode || !viewer || !workspace) return;

  let project;
  try {
    project = JSON.parse(projectNode.textContent);
  } catch {
    viewer.textContent = '项目数据无法读取，请刷新页面重试。';
    return;
  }

  const imagesById = project.images || {};
  const group = Array.isArray(project.groups) ? project.groups[0] : null;
  const groupImages = group?.images || {};
  const records = (project.variants || []).map((variant) => {
    const imageId = groupImages[variant.id];
    const image = imagesById[imageId];
    if (!image) return null;
    return {
      variant,
      imageId,
      image,
      url: image.url || `/media/${encodeURIComponent(project.id)}/${encodeURIComponent(imageId)}`,
    };
  }).filter(Boolean);

  if (records.length < 2) {
    viewer.textContent = '这个多图项目缺少可显示的图片。';
    return;
  }

  const state = {
    mode: 'parallel',
    centerX: 0.5,
    centerY: 0.5,
    zoom: 1,
    scaleMode: 'fit',
    split: 50,
    leftId: records[0].variant.id,
    rightId: records[1].variant.id,
    lastVariantId: records[0].variant.id,
  };
  const zoomOutput = document.querySelector('#analysis-zoom');
  const pairSelectors = document.querySelector('#analysis-pair-selectors');
  const leftSelect = document.querySelector('#slider-left');
  const rightSelect = document.querySelector('#slider-right');
  const modeButtons = [...document.querySelectorAll('[data-mode]')];
  const alignmentNote = document.querySelector('#analysis-alignment-note');
  const fullscreenButton = document.querySelector('#analysis-fullscreen');
  const fullscreenLabel = fullscreenButton.querySelector('.analysis-fullscreen-label');
  const copyButton = document.querySelector('#copy-analysis-link');
  const copyLabel = copyButton.querySelector('.copy-label');
  const shareInput = document.querySelector('#analysis-share-url');
  const metaContainer = document.querySelector('#analysis-variant-meta');
  let resizeObserver = null;

  const recordForVariant = (variantId) => records.find((record) => record.variant.id === variantId) || records[0];
  const imageDimensions = (record, imageElement = null) => ({
    width: imageElement?.naturalWidth || Number(record.image.width) || 1,
    height: imageElement?.naturalHeight || Number(record.image.height) || 1,
  });

  const updateZoomCopy = () => {
    if (state.scaleMode === 'fit' && Math.abs(state.zoom - 1) < 0.005) {
      zoomOutput.textContent = '适应';
      return;
    }
    zoomOutput.textContent = `${Math.round(state.zoom * 100)}%`;
  };

  const applySplit = () => {
    viewer.style.setProperty('--analysis-split', `${state.split}%`);
    const divider = viewer.querySelector('.analysis-divider');
    if (divider) divider.setAttribute('aria-valuenow', state.split.toFixed(1));
  };

  const renderTransforms = () => {
    viewer.querySelectorAll('.analysis-image').forEach((imageElement) => {
      const record = recordForVariant(imageElement.dataset.variantId);
      const viewport = imageElement.closest('.analysis-viewport');
      if (!viewport || !viewport.clientWidth || !viewport.clientHeight) return;
      const dimensions = imageDimensions(record, imageElement);
      const transform = normalizedToTransform({
        centerX: state.centerX,
        centerY: state.centerY,
        zoom: state.zoom,
        scaleMode: state.scaleMode,
        viewportWidth: viewport.clientWidth,
        viewportHeight: viewport.clientHeight,
        imageWidth: dimensions.width,
        imageHeight: dimensions.height,
      });
      imageElement.style.width = `${dimensions.width}px`;
      imageElement.style.height = `${dimensions.height}px`;
      imageElement.style.transform = `translate3d(${transform.left}px, ${transform.top}px, 0) scale(${transform.scale})`;
    });
    updateZoomCopy();
    applySplit();
  };

  const clampCenter = (point) => {
    state.centerX = clamp(point.x, 0, 1);
    state.centerY = clamp(point.y, 0, 1);
  };

  const zoomAt = (factor, viewport, screenX, screenY, record) => {
    if (!viewport || !record) return;
    const oldZoom = state.zoom;
    const newZoom = clamp(oldZoom * factor, 0.1, 64);
    if (Math.abs(oldZoom - newZoom) < 0.00001) return;
    const imageElement = viewport.querySelector(`.analysis-image[data-variant-id="${CSS.escape(record.variant.id)}"]`)
      || viewport.querySelector('.analysis-image');
    const dimensions = imageDimensions(record, imageElement);
    const center = centerForZoomAtPoint({
      screenX,
      screenY,
      centerX: state.centerX,
      centerY: state.centerY,
      oldZoom,
      newZoom,
      scaleMode: state.scaleMode,
      viewportWidth: viewport.clientWidth,
      viewportHeight: viewport.clientHeight,
      imageWidth: dimensions.width,
      imageHeight: dimensions.height,
    });
    state.zoom = newZoom;
    clampCenter(center);
    renderTransforms();
  };

  const panBy = (deltaX, deltaY, viewport, record) => {
    if (!viewport || !record) return;
    const imageElement = viewport.querySelector(`.analysis-image[data-variant-id="${CSS.escape(record.variant.id)}"]`)
      || viewport.querySelector('.analysis-image');
    const dimensions = imageDimensions(record, imageElement);
    clampCenter(panCenter({
      centerX: state.centerX,
      centerY: state.centerY,
      deltaX,
      deltaY,
      zoom: state.zoom,
      scaleMode: state.scaleMode,
      viewportWidth: viewport.clientWidth,
      viewportHeight: viewport.clientHeight,
      imageWidth: dimensions.width,
      imageHeight: dimensions.height,
    }));
    renderTransforms();
  };

  const primaryContext = () => {
    const focused = document.activeElement?.closest?.('.analysis-viewport');
    const viewport = focused || viewer.querySelector('.analysis-viewport');
    const variantId = focused?.dataset.primaryVariant || state.lastVariantId || viewport?.dataset.primaryVariant;
    return { viewport, record: recordForVariant(variantId) };
  };

  const resetView = (includeSplit = false) => {
    state.centerX = 0.5;
    state.centerY = 0.5;
    state.zoom = 1;
    state.scaleMode = 'fit';
    if (includeSplit) state.split = 50;
    renderTransforms();
  };

  const showActualPixels = () => {
    state.scaleMode = 'actual';
    state.zoom = 1;
    renderTransforms();
  };

  const makeImage = (record, alt) => {
    const image = document.createElement('img');
    image.className = 'analysis-image';
    image.dataset.variantId = record.variant.id;
    image.src = record.url;
    image.alt = alt;
    image.draggable = false;
    return image;
  };

  const makeViewportLabel = (record, corner = '') => {
    const label = document.createElement('div');
    label.className = `analysis-viewport-label ${corner}`.trim();
    const badge = document.createElement('span');
    badge.className = 'analyze-variant-badge';
    badge.textContent = record.variant.id;
    const copy = document.createElement('strong');
    copy.textContent = record.variant.label || record.variant.id;
    label.append(badge, copy);
    return label;
  };

  const makeCheckerboard = () => {
    const checkerboard = document.createElement('div');
    checkerboard.className = 'analysis-checkerboard';
    checkerboard.setAttribute('aria-hidden', 'true');
    return checkerboard;
  };

  const wireImageLoading = (images) => {
    const loading = document.createElement('div');
    loading.className = 'analysis-loading';
    loading.innerHTML = '<span></span> 正在载入图片…';
    viewer.append(loading);
    let pending = images.length;
    let failed = false;
    const settled = (didFail = false) => {
      pending -= 1;
      failed ||= didFail;
      if (pending > 0) return;
      if (failed) {
        loading.classList.add('has-error');
        loading.textContent = '部分图片载入失败，请刷新页面重试。';
      } else {
        loading.remove();
      }
      renderTransforms();
      updateAlignmentNote();
    };
    images.forEach((image) => {
      if (image.complete) settled(!image.naturalWidth);
      else {
        image.addEventListener('load', () => settled(false), { once: true });
        image.addEventListener('error', () => settled(true), { once: true });
      }
    });
  };

  const setSplitFromClientX = (viewport, clientX) => {
    const rect = viewport.getBoundingClientRect();
    state.split = clamp(((clientX - rect.left) / rect.width) * 100, 0, 100);
    applySplit();
  };

  const wireDivider = (divider, viewport) => {
    divider.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      divider.setPointerCapture(event.pointerId);
      divider.classList.add('is-dragging');
      setSplitFromClientX(viewport, event.clientX);
    });
    divider.addEventListener('pointermove', (event) => {
      if (!divider.hasPointerCapture(event.pointerId)) return;
      event.preventDefault();
      setSplitFromClientX(viewport, event.clientX);
    });
    const stop = (event) => {
      if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
      divider.classList.remove('is-dragging');
    };
    divider.addEventListener('pointerup', stop);
    divider.addEventListener('pointercancel', stop);
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

  const wireViewportGestures = (viewport, record) => {
    const pointers = new Map();
    let lastSingle = null;
    let pinch = null;
    const point = (event) => ({ id: event.pointerId, x: event.clientX, y: event.clientY });
    const midpoint = (points) => ({
      x: (points[0].x + points[1].x) / 2,
      y: (points[0].y + points[1].y) / 2,
    });
    const distance = (points) => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);

    viewport.addEventListener('pointerdown', (event) => {
      if (event.target.closest('.analysis-divider')) return;
      event.preventDefault();
      viewport.focus({ preventScroll: true });
      state.lastVariantId = record.variant.id;
      viewport.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, point(event));
      viewport.classList.add('is-panning');
      if (pointers.size === 1) lastSingle = point(event);
      if (pointers.size === 2) {
        const points = [...pointers.values()];
        pinch = { midpoint: midpoint(points), distance: distance(points) };
        lastSingle = null;
      }
    });

    viewport.addEventListener('pointermove', (event) => {
      if (!pointers.has(event.pointerId)) return;
      event.preventDefault();
      pointers.set(event.pointerId, point(event));
      if (pointers.size === 1) {
        const current = point(event);
        if (lastSingle) panBy(current.x - lastSingle.x, current.y - lastSingle.y, viewport, record);
        lastSingle = current;
        return;
      }
      const points = [...pointers.values()].slice(0, 2);
      const nextMidpoint = midpoint(points);
      const nextDistance = Math.max(1, distance(points));
      if (pinch) {
        const rect = viewport.getBoundingClientRect();
        zoomAt(nextDistance / Math.max(1, pinch.distance), viewport, nextMidpoint.x - rect.left, nextMidpoint.y - rect.top, record);
        panBy(nextMidpoint.x - pinch.midpoint.x, nextMidpoint.y - pinch.midpoint.y, viewport, record);
      }
      pinch = { midpoint: nextMidpoint, distance: nextDistance };
    });

    const release = (event) => {
      pointers.delete(event.pointerId);
      if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
      if (!pointers.size) {
        viewport.classList.remove('is-panning');
        lastSingle = null;
        pinch = null;
      } else if (pointers.size === 1) {
        lastSingle = [...pointers.values()][0];
        pinch = null;
      }
    };
    viewport.addEventListener('pointerup', release);
    viewport.addEventListener('pointercancel', release);
    viewport.addEventListener('lostpointercapture', (event) => {
      if (pointers.has(event.pointerId)) release(event);
    });
    viewport.addEventListener('wheel', (event) => {
      event.preventDefault();
      state.lastVariantId = record.variant.id;
      const rect = viewport.getBoundingClientRect();
      zoomAt(Math.exp(-event.deltaY * 0.0015), viewport, event.clientX - rect.left, event.clientY - rect.top, record);
    }, { passive: false });
  };

  const createParallelView = () => {
    const grid = document.createElement('div');
    grid.className = 'analysis-parallel-grid';
    grid.classList.add(`count-${records.length}`);
    grid.style.setProperty('--variant-count', records.length);
    const images = [];
    records.forEach((record) => {
      const viewport = document.createElement('div');
      viewport.className = 'analysis-viewport';
      viewport.tabIndex = 0;
      viewport.dataset.primaryVariant = record.variant.id;
      viewport.setAttribute('aria-label', `${record.variant.label || record.variant.id} 视窗；拖动平移，滚轮缩放`);
      const image = makeImage(record, `${record.variant.label || record.variant.id}：${record.image.original_name}`);
      images.push(image);
      viewport.append(makeCheckerboard(), image, makeViewportLabel(record));
      wireViewportGestures(viewport, record);
      grid.append(viewport);
    });
    viewer.append(grid);
    wireImageLoading(images);
  };

  const createSliderView = () => {
    const leftRecord = recordForVariant(state.leftId);
    const rightRecord = recordForVariant(state.rightId);
    const viewport = document.createElement('div');
    viewport.className = 'analysis-viewport analysis-slider-viewport';
    viewport.tabIndex = 0;
    viewport.dataset.primaryVariant = leftRecord.variant.id;
    viewport.setAttribute('aria-label', '滑杆对比视窗；拖动画布平移，滚轮缩放');
    const rightImage = makeImage(rightRecord, `${rightRecord.variant.label || rightRecord.variant.id}：${rightRecord.image.original_name}`);
    const layer = document.createElement('div');
    layer.className = 'analysis-slider-layer';
    const leftImage = makeImage(leftRecord, `${leftRecord.variant.label || leftRecord.variant.id}：${leftRecord.image.original_name}`);
    layer.append(leftImage);
    const divider = document.createElement('div');
    divider.className = 'analysis-divider';
    divider.tabIndex = 0;
    divider.setAttribute('role', 'slider');
    divider.setAttribute('aria-label', `调整 ${leftRecord.variant.label || leftRecord.variant.id} 与 ${rightRecord.variant.label || rightRecord.variant.id} 的分界位置`);
    divider.setAttribute('aria-valuemin', '0');
    divider.setAttribute('aria-valuemax', '100');
    divider.innerHTML = '<i aria-hidden="true"></i><span aria-hidden="true">↔</span>';
    viewport.append(
      makeCheckerboard(),
      rightImage,
      layer,
      makeViewportLabel(leftRecord, 'is-left'),
      makeViewportLabel(rightRecord, 'is-right'),
      divider,
    );
    viewer.append(viewport);
    wireViewportGestures(viewport, leftRecord);
    wireDivider(divider, viewport);
    wireImageLoading([rightImage, leftImage]);
  };

  function rebuildViewer() {
    resizeObserver?.disconnect();
    viewer.replaceChildren();
    viewer.classList.toggle('is-slider-mode', state.mode === 'slider');
    if (state.mode === 'parallel') createParallelView();
    else createSliderView();
    pairSelectors.hidden = state.mode !== 'slider';
    modeButtons.forEach((button) => {
      const active = button.dataset.mode === state.mode;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    resizeObserver = new ResizeObserver(renderTransforms);
    resizeObserver.observe(viewer);
    viewer.querySelectorAll('.analysis-viewport').forEach((viewport) => resizeObserver.observe(viewport));
    requestAnimationFrame(renderTransforms);
  }

  const updateAlignmentNote = () => {
    const dimensions = records.map((record) => `${record.image.width || 0}x${record.image.height || 0}`);
    const different = new Set(dimensions).size > 1;
    alignmentNote.classList.toggle('has-different-sizes', different);
    alignmentNote.textContent = different
      ? '图片尺寸不同。同步的是归一化视图中心与缩放倍率；素材未做空间对齐，并始终保持原始比例。'
      : '同步的是归一化视图中心与缩放倍率；素材未做空间对齐，并始终保持原始比例。';
  };

  const formatBytes = (bytes) => {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const renderMetadata = () => {
    metaContainer.style.setProperty('--variant-count', records.length);
    records.forEach((record) => {
      const card = document.createElement('article');
      const badge = document.createElement('span');
      badge.className = 'analyze-variant-badge';
      badge.textContent = record.variant.id;
      const copy = document.createElement('div');
      const label = document.createElement('p');
      label.textContent = record.variant.label || record.variant.id;
      const filename = document.createElement('strong');
      filename.textContent = record.image.original_name;
      filename.title = record.image.original_name;
      copy.append(label, filename);
      const detail = document.createElement('span');
      detail.className = 'analysis-meta-detail';
      detail.textContent = `${record.image.width} × ${record.image.height} · ${formatBytes(Number(record.image.size) || 0)}`;
      card.append(badge, copy, detail);
      metaContainer.append(card);
    });
  };

  const fillSelectors = () => {
    records.forEach((record) => {
      [leftSelect, rightSelect].forEach((select) => {
        const option = document.createElement('option');
        option.value = record.variant.id;
        option.textContent = `${record.variant.id} · ${record.variant.label || record.variant.id}`;
        select.append(option);
      });
    });
    leftSelect.value = state.leftId;
    rightSelect.value = state.rightId;
  };

  const ensureDistinctPair = (changedSelect) => {
    if (leftSelect.value !== rightSelect.value) return;
    const fallback = records.find((record) => record.variant.id !== changedSelect.value)?.variant.id;
    if (changedSelect === leftSelect) rightSelect.value = fallback;
    else leftSelect.value = fallback;
  };

  modeButtons.forEach((button) => button.addEventListener('click', () => {
    if (state.mode === button.dataset.mode) return;
    state.mode = button.dataset.mode;
    rebuildViewer();
  }));

  [leftSelect, rightSelect].forEach((select) => select.addEventListener('change', () => {
    ensureDistinctPair(select);
    state.leftId = leftSelect.value;
    state.rightId = rightSelect.value;
    state.lastVariantId = state.leftId;
    rebuildViewer();
  }));

  document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'fit') resetView(false);
    if (action === 'actual') showActualPixels();
    if (action === 'reset') resetView(true);
    if (action === 'zoom-in' || action === 'zoom-out') {
      const { viewport, record } = primaryContext();
      if (viewport) zoomAt(action === 'zoom-in' ? 1.25 : 0.8, viewport, viewport.clientWidth / 2, viewport.clientHeight / 2, record);
    }
  }));

  document.addEventListener('keydown', (event) => {
    if (event.target.closest('input, select, button') || event.target.closest('.analysis-divider')) return;
    const { viewport, record } = primaryContext();
    const key = event.key.toLowerCase();
    if (key === 'f') resetView(false);
    else if (key === 'r') resetView(true);
    else if (key === '1') showActualPixels();
    else if (['+', '='].includes(event.key)) zoomAt(1.25, viewport, viewport.clientWidth / 2, viewport.clientHeight / 2, record);
    else if (event.key === '-') zoomAt(0.8, viewport, viewport.clientWidth / 2, viewport.clientHeight / 2, record);
    else if (event.key === 'ArrowLeft') panBy(-30, 0, viewport, record);
    else if (event.key === 'ArrowRight') panBy(30, 0, viewport, record);
    else if (event.key === 'ArrowUp') panBy(0, -30, viewport, record);
    else if (event.key === 'ArrowDown') panBy(0, 30, viewport, record);
    else return;
    event.preventDefault();
  });

  const activeFullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement || null;
  const updateFullscreenButton = () => {
    const active = activeFullscreenElement() === workspace || workspace.classList.contains('is-faux-fullscreen');
    fullscreenButton.classList.toggle('is-active', active);
    fullscreenButton.setAttribute('aria-pressed', String(active));
    fullscreenLabel.textContent = active ? '退出全屏' : '全屏';
    requestAnimationFrame(renderTransforms);
  };
  const leaveFauxFullscreen = () => {
    workspace.classList.remove('is-faux-fullscreen');
    document.body.classList.remove('viewer-fullscreen');
    updateFullscreenButton();
  };
  fullscreenButton.addEventListener('click', async () => {
    if (workspace.classList.contains('is-faux-fullscreen')) {
      leaveFauxFullscreen();
      return;
    }
    if (activeFullscreenElement()) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) await exit.call(document);
      return;
    }
    const request = workspace.requestFullscreen || workspace.webkitRequestFullscreen;
    if (!request) {
      workspace.classList.add('is-faux-fullscreen');
      document.body.classList.add('viewer-fullscreen');
      updateFullscreenButton();
      return;
    }
    try {
      await request.call(workspace);
    } catch {
      workspace.classList.add('is-faux-fullscreen');
      document.body.classList.add('viewer-fullscreen');
      updateFullscreenButton();
    }
  });
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && workspace.classList.contains('is-faux-fullscreen')) leaveFauxFullscreen();
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
        copyLabel.textContent = '复制此页链接';
      }, 1800);
    } catch {
      shareInput.focus();
      shareInput.select();
      copyLabel.textContent = '地址已选中，请复制';
    }
  });

  fillSelectors();
  renderMetadata();
  updateAlignmentNote();
  rebuildViewer();
})();
