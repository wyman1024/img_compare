(() => {
  'use strict';

  const calculateGridSpan = (height, rowHeight, gap) => {
    const safeRowHeight = Math.max(1, Number(rowHeight) || 1);
    const safeGap = Math.max(0, Number(gap) || 0);
    return Math.max(1, Math.ceil((Math.max(0, height) + safeGap) / (safeRowHeight + safeGap)));
  };

  globalThis.GalleryViewUtils = { calculateGridSpan };
  globalThis.__galleryTestHooks = Object.assign(globalThis.__galleryTestHooks || {}, { calculateGridSpan });

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
  const range = document.querySelector('#dialog-range');
  const imageA = document.querySelector('#dialog-image-a');
  const imageB = document.querySelector('#dialog-image-b');
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
    fullscreenButton.title = active ? '退出全屏图片对比' : '全屏查看图片对比';
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

  fullscreenButton.addEventListener('click', async () => {
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
  });

  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);

  const setPosition = (value) => stage.style.setProperty('--position', `${value}%`);
  range.addEventListener('input', () => setPosition(range.value));

  const updateNavigation = () => {
    previousButton.disabled = currentIndex <= 0;
    nextButton.disabled = currentIndex >= cards.length - 1;
  };

  const renderDialogCard = (index) => {
    const card = cards[index];
    if (!card) return;
    currentIndex = index;
    const data = card.dataset;
    dialogTitle.textContent = data.title;
    dialogCounter.textContent = `${index + 1} / ${cards.length}`;
    nameA.textContent = data.aName;
    nameB.textContent = data.bName;
    nameA.title = data.aName;
    nameB.title = data.bName;
    metaA.textContent = `${data.aWidth} × ${data.aHeight} · ${formatBytes(Number(data.aSize))}`;
    metaB.textContent = `${data.bWidth} × ${data.bHeight} · ${formatBytes(Number(data.bSize))}`;
    ratioNote.hidden = data.aWidth === data.bWidth && data.aHeight === data.bHeight;
    range.value = '50';
    setPosition(50);
    updateNavigation();

    loadGeneration += 1;
    const generation = loadGeneration;
    const settled = new Set();
    loading.classList.remove('is-hidden', 'has-error');
    loading.innerHTML = '<span></span> 正在载入原图…';

    const settle = (image, failed = false) => {
      if (generation !== loadGeneration || settled.has(image)) return;
      settled.add(image);
      if (failed) {
        loading.textContent = '原图载入失败，请关闭后重试。';
        loading.classList.add('has-error');
        return;
      }
      if (settled.size === 2 && !loading.classList.contains('has-error')) loading.classList.add('is-hidden');
    };

    [[imageA, data.aUrl, `${data.title} · A：${data.aName}`], [imageB, data.bUrl, `${data.title} · B：${data.bName}`]].forEach(([image, url, alt]) => {
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
    window.requestAnimationFrame(() => range.focus({ preventScroll: true }));
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
    if (event.key === 'PageUp' && currentIndex > 0) {
      event.preventDefault();
      renderDialogCard(currentIndex - 1);
    } else if (event.key === 'PageDown' && currentIndex < cards.length - 1) {
      event.preventDefault();
      renderDialogCard(currentIndex + 1);
    } else if (event.key === 'Escape' && viewer.classList.contains('is-faux-fullscreen')) {
      event.preventDefault();
      leaveFauxFullscreen();
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
