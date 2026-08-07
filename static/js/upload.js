(() => {
  const card = document.querySelector('#upload-card');
  const createButton = document.querySelector('#create-button');
  const swapButton = document.querySelector('#swap-button');
  const errorBox = document.querySelector('#upload-error');
  const zones = [...document.querySelectorAll('.drop-zone')];
  const files = { image1: null, image2: null };
  const objectUrls = { image1: null, image2: null };
  const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp']);
  const configuredMaxBytes = Number(document.body.dataset.maxImageBytes);
  const maxBytes = Number.isFinite(configuredMaxBytes) && configuredMaxBytes > 0
    ? configuredMaxBytes
    : 20 * 1024 * 1024;
  let dragDepth = 0;

  const formatBytes = (bytes) => {
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    const megabytes = bytes / (1024 * 1024);
    return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)} MB`;
  };

  const clearError = () => {
    errorBox.textContent = '';
    card.classList.remove('has-error');
  };

  const showError = (message) => {
    errorBox.textContent = message;
    card.classList.add('has-error');
  };

  const validate = (file) => {
    if (!allowedTypes.has(file.type)) return '请选择 PNG、JPG、WebP、GIF 或 BMP 图片。';
    if (file.size > maxBytes) return `“${file.name}”超过 ${formatBytes(maxBytes)}，请压缩后再试。`;
    if (!file.size) return `“${file.name}”是空文件，请重新选择。`;
    return null;
  };

  const updateActions = () => {
    const ready = Boolean(files.image1 && files.image2);
    const uploading = card.classList.contains('is-uploading');
    createButton.disabled = !ready || uploading;
    swapButton.disabled = !ready || uploading;
  };

  const renderSlot = (slot) => {
    const zone = document.querySelector(`[data-slot="${slot}"]`);
    const empty = zone.querySelector('.drop-empty');
    const preview = zone.querySelector('.drop-preview');
    const file = files[slot];

    if (objectUrls[slot]) {
      URL.revokeObjectURL(objectUrls[slot]);
      objectUrls[slot] = null;
    }

    if (!file) {
      empty.hidden = false;
      preview.hidden = true;
      zone.classList.remove('has-file');
      updateActions();
      return;
    }

    objectUrls[slot] = URL.createObjectURL(file);
    const previewImage = preview.querySelector('img');
    const meta = preview.querySelector('.file-meta');
    previewImage.onload = () => {
      if (files[slot] !== file) return;
      meta.textContent = `${previewImage.naturalWidth} × ${previewImage.naturalHeight} · ${formatBytes(file.size)}`;
    };
    previewImage.onerror = () => {
      if (files[slot] !== file) return;
      files[slot] = null;
      renderSlot(slot);
      showError(`“${file.name}”已损坏或无法读取，请换一张图片。`);
    };
    previewImage.src = objectUrls[slot];
    preview.querySelector('.file-name').textContent = file.name;
    meta.textContent = formatBytes(file.size);
    empty.hidden = true;
    preview.hidden = false;
    zone.classList.add('has-file');
    updateActions();
  };

  const assignFile = (slot, file) => {
    const error = validate(file);
    if (error) {
      showError(error);
      return false;
    }
    clearError();
    files[slot] = file;
    renderSlot(slot);
    return true;
  };

  const readJson = async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error?.message || '服务器未能处理这次上传。');
    }
    return payload;
  };

  const uploadDraftImage = async (url, token, file) => {
    const body = new FormData();
    body.append('file', file, file.name);
    const response = await fetch(url, {
      method: 'PUT',
      headers: { 'X-Edit-Token': token },
      body,
    });
    return readJson(response);
  };

  const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  const finalizeDraft = async (draft) => {
    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await fetch(`/api/drafts/${encodeURIComponent(draft.draft_id)}/finalize`, {
          method: 'POST',
          headers: { 'X-Edit-Token': draft.edit_token },
        });
        return await readJson(response);
      } catch (error) {
        lastError = error;
        if (attempt < 2) await wait(250 * (attempt + 1));
      }
    }
    throw lastError || new Error('发布结果暂时无法确认。');
  };

  const assignDroppedFiles = (fileList, preferredSlot) => {
    if (card.classList.contains('is-uploading')) return;
    if (fileList.length > 2) {
      showError('每次最多放入两张图片，请重新选择。');
      return;
    }
    const incoming = [...fileList];
    if (!incoming.length) {
      showError('这里需要图片文件，请重新拖入。');
      return;
    }

    if (incoming.length >= 2) {
      const firstError = validate(incoming[0]);
      const secondError = validate(incoming[1]);
      if (firstError || secondError) {
        showError(firstError || secondError);
        return;
      }
      clearError();
      files.image1 = incoming[0];
      files.image2 = incoming[1];
      renderSlot('image1');
      renderSlot('image2');
      return;
    }

    const target = preferredSlot || (!files.image1 ? 'image1' : !files.image2 ? 'image2' : 'image1');
    assignFile(target, incoming[0]);
  };

  zones.forEach((zone) => {
    const slot = zone.dataset.slot;
    const input = zone.querySelector('.file-input');

    zone.addEventListener('click', (event) => {
      if (card.classList.contains('is-uploading')) return;
      if (event.target.closest('.remove-file')) return;
      input.click();
    });
    zone.addEventListener('keydown', (event) => {
      if (card.classList.contains('is-uploading')) return;
      if (event.target !== zone) return;
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        input.click();
      }
    });
    zone.addEventListener('dragover', (event) => {
      event.preventDefault();
      zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragleave', () => zone.classList.remove('is-dragging'));
    zone.addEventListener('drop', (event) => {
      event.preventDefault();
      event.stopPropagation();
      dragDepth = 0;
      card.classList.remove('is-global-dragging');
      zone.classList.remove('is-dragging');
      assignDroppedFiles(event.dataTransfer.files, slot);
    });
    input.addEventListener('change', () => {
      if (card.classList.contains('is-uploading')) return;
      if (input.files[0]) assignFile(slot, input.files[0]);
      input.value = '';
    });
    zone.querySelector('.remove-file').addEventListener('click', (event) => {
      event.stopPropagation();
      if (card.classList.contains('is-uploading')) return;
      files[slot] = null;
      renderSlot(slot);
      clearError();
    });
  });

  document.addEventListener('dragenter', (event) => {
    if (![...event.dataTransfer.types].includes('Files')) return;
    dragDepth += 1;
    card.classList.add('is-global-dragging');
  });
  document.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) card.classList.remove('is-global-dragging');
  });
  document.addEventListener('dragover', (event) => event.preventDefault());
  document.addEventListener('drop', (event) => {
    event.preventDefault();
    dragDepth = 0;
    card.classList.remove('is-global-dragging');
    if (!event.target.closest('.drop-zone')) assignDroppedFiles(event.dataTransfer.files);
  });

  swapButton.addEventListener('click', () => {
    [files.image1, files.image2] = [files.image2, files.image1];
    renderSlot('image1');
    renderSlot('image2');
  });

  createButton.addEventListener('click', async () => {
    if (!files.image1 || !files.image2 || createButton.classList.contains('is-loading')) return;

    clearError();
    createButton.classList.add('is-loading');
    card.classList.add('is-uploading');
    updateActions();
    card.setAttribute('aria-busy', 'true');

    let draft = null;
    let finalizeStarted = false;
    try {
      const createResponse = await fetch('/api/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: 'pair',
          title: '',
          variants: [
            { id: 'A', label: 'A' },
            { id: 'B', label: 'B' },
          ],
          groups: [{
            id: 'group_1',
            title: '图片对比',
            images: { A: 'image_a', B: 'image_b' },
          }],
          images: [
            { id: 'image_a', original_name: files.image1.name },
            { id: 'image_b', original_name: files.image2.name },
          ],
        }),
      });
      draft = await readJson(createResponse);
      await Promise.all([
        uploadDraftImage(draft.uploads.image_a, draft.edit_token, files.image1),
        uploadDraftImage(draft.uploads.image_b, draft.edit_token, files.image2),
      ]);
      finalizeStarted = true;
      const result = await finalizeDraft(draft);
      window.location.assign(result.url);
    } catch (error) {
      if (finalizeStarted && draft?.draft_id) {
        const expectedUrl = `/compare/${encodeURIComponent(draft.draft_id)}`;
        try {
          const published = await fetch(expectedUrl, { cache: 'no-store' });
          if (published.ok) {
            window.location.assign(expectedUrl);
            return;
          }
        } catch {
          // Keep the draft/project untouched when publication may have succeeded.
        }
      } else if (draft?.draft_id && draft?.edit_token) {
        fetch(`/api/drafts/${draft.draft_id}`, {
          method: 'DELETE',
          headers: { 'X-Edit-Token': draft.edit_token },
          keepalive: true,
        }).catch(() => {});
      }
      showError(finalizeStarted && draft?.draft_id
        ? `发布结果暂时无法确认。服务恢复后可尝试打开 /compare/${draft.draft_id}`
        : (error.message || '网络连接失败，请确认服务仍在运行。'));
      createButton.classList.remove('is-loading');
      card.classList.remove('is-uploading');
      updateActions();
      card.removeAttribute('aria-busy');
    }
  });

  window.addEventListener('beforeunload', () => {
    Object.values(objectUrls).forEach((url) => url && URL.revokeObjectURL(url));
  });
})();
