(() => {
  const isFullscreenShortcut = (event) => (
    String(event?.key || '').toLowerCase() === 'f'
    && Boolean(event?.shiftKey)
    && !event?.ctrlKey
    && !event?.altKey
    && !event?.metaKey
    && !event?.repeat
  );

  const stage = document.querySelector('#comparison-stage');
  const fullscreenTarget = document.querySelector('.comparison-shell');
  const fullscreenButton = document.querySelector('#fullscreen-button');
  const fullscreenLabel = fullscreenButton.querySelector('.fullscreen-label');
  const range = document.querySelector('#comparison-range');
  const loading = document.querySelector('#image-loading');
  const images = [...stage.querySelectorAll('.compare-image')];
  const copyButton = document.querySelector('#copy-button');
  const copyLabel = copyButton.querySelector('.copy-label');
  const shareUrl = copyButton.dataset.shareUrl;
  const ratioNote = document.querySelector('#ratio-note');

  const setPosition = (value) => stage.style.setProperty('--position', `${value}%`);
  range.addEventListener('input', () => setPosition(range.value));

  const activeFullscreenElement = () => (
    document.fullscreenElement || document.webkitFullscreenElement || null
  );

  const updateFullscreenButton = () => {
    const active = activeFullscreenElement() === fullscreenTarget
      || fullscreenTarget.classList.contains('is-faux-fullscreen');
    fullscreenButton.classList.toggle('is-active', active);
    fullscreenButton.setAttribute('aria-pressed', String(active));
    fullscreenLabel.textContent = active ? '退出全屏' : '全屏';
    fullscreenButton.title = active
      ? '退出全屏图片对比（Shift + F）'
      : '全屏查看图片对比（Shift + F）';
  };

  const leaveFauxFullscreen = () => {
    fullscreenTarget.classList.remove('is-faux-fullscreen');
    document.body.classList.remove('viewer-fullscreen');
    updateFullscreenButton();
  };

  const enterFauxFullscreen = () => {
    fullscreenTarget.classList.add('is-faux-fullscreen');
    document.body.classList.add('viewer-fullscreen');
    updateFullscreenButton();
  };

  async function toggleFullscreen() {
    if (fullscreenTarget.classList.contains('is-faux-fullscreen')) {
      leaveFauxFullscreen();
      return;
    }

    const activeElement = activeFullscreenElement();
    if (activeElement) {
      const exit = document.exitFullscreen || document.webkitExitFullscreen;
      if (exit) await exit.call(document);
      return;
    }

    const request = fullscreenTarget.requestFullscreen
      || fullscreenTarget.webkitRequestFullscreen;
    if (!request) {
      enterFauxFullscreen();
      return;
    }

    try {
      await request.call(fullscreenTarget);
    } catch {
      enterFauxFullscreen();
    }
  }
  fullscreenButton.addEventListener('click', toggleFullscreen);

  document.addEventListener('fullscreenchange', updateFullscreenButton);
  document.addEventListener('webkitfullscreenchange', updateFullscreenButton);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && fullscreenTarget.classList.contains('is-faux-fullscreen')) {
      leaveFauxFullscreen();
    } else if (
      isFullscreenShortcut(event)
      && !event.target.closest?.('textarea, select, input:not([type]), input[type="text"], input[type="search"], input[type="url"], input[type="email"], input[type="password"], [contenteditable="true"]')
    ) {
      event.preventDefault();
      void toggleFullscreen();
    }
  });

  let loaded = 0;
  const imageReady = () => {
    loaded += 1;
    if (loaded === images.length) {
      loading.classList.add('is-hidden');
      stage.classList.add('is-ready');
      const [imageA, imageB] = images;
      const ratioA = imageA.naturalWidth / imageA.naturalHeight;
      const ratioB = imageB.naturalWidth / imageB.naturalHeight;
      if (imageA.naturalWidth !== imageB.naturalWidth
        || imageA.naturalHeight !== imageB.naturalHeight
        || Math.abs(ratioA - ratioB) > 0.001) {
        ratioNote.hidden = false;
      }
    }
  };
  const imageFailed = () => {
    loading.textContent = '图片载入失败，请刷新页面重试。';
    loading.classList.add('has-error');
  };
  images.forEach((img) => {
    if (img.complete) {
      if (img.naturalWidth) imageReady();
      else imageFailed();
    } else {
      img.addEventListener('load', imageReady, { once: true });
      img.addEventListener('error', imageFailed, { once: true });
    }
  });

  const fallbackCopy = () => {
    const textarea = document.createElement('textarea');
    textarea.value = shareUrl;
    textarea.readOnly = true;
    textarea.style.position = 'fixed';
    textarea.style.top = '0';
    textarea.style.left = '0';
    textarea.style.width = '1px';
    textarea.style.height = '1px';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.append(textarea);
    try {
      textarea.select();
      textarea.setSelectionRange(0, textarea.value.length);
      return document.execCommand('copy');
    } finally {
      textarea.remove();
      copyButton.focus({ preventScroll: true });
    }
  };

  const copyShareUrl = async () => {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(shareUrl);
        return true;
      } catch {
        // Continue with the synchronous fallback when clipboard permission is unavailable.
      }
    }
    return fallbackCopy();
  };

  copyButton.addEventListener('click', async () => {
    try {
      if (!await copyShareUrl()) throw new Error('copy failed');
      copyButton.classList.add('is-copied');
      copyLabel.textContent = '链接已复制';
      window.setTimeout(() => {
        copyButton.classList.remove('is-copied');
        copyLabel.textContent = '复制此页链接';
      }, 1800);
    } catch {
      copyLabel.textContent = '复制失败，请重试';
    }
  });

  document.querySelectorAll('.meta-size').forEach((node) => {
    const bytes = Number(node.dataset.bytes);
    const size = bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    node.textContent = `${node.dataset.dimensions} · ${size}`;
  });
})();
