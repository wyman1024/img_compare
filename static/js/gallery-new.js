(() => {
  'use strict';

  const formatBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes < 0) return '—';
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    const megabytes = bytes / (1024 * 1024);
    return `${megabytes >= 100 ? Math.round(megabytes) : megabytes.toFixed(1)} MB`;
  };

  const moveItem = (items, fromIndex, toIndex) => {
    if (fromIndex < 0 || fromIndex >= items.length) return items;
    const boundedTarget = Math.max(0, Math.min(items.length - 1, toIndex));
    if (fromIndex === boundedTarget) return items;
    const next = [...items];
    const [item] = next.splice(fromIndex, 1);
    next.splice(boundedTarget, 0, item);
    return next;
  };

  const runConcurrent = async (items, limit, worker) => {
    let cursor = 0;
    const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (cursor < items.length) {
        const index = cursor;
        cursor += 1;
        await worker(items[index], index);
      }
    });
    await Promise.all(runners);
  };

  const testHooks = { formatBytes, moveItem, runConcurrent, uploadConcurrency: 3 };
  globalThis.GalleryCreateUtils = testHooks;
  globalThis.__galleryTestHooks = Object.assign(globalThis.__galleryTestHooks || {}, testHooks);

  if (typeof document === 'undefined') return;
  const pairEditor = document.querySelector('#pair-editor');
  if (!pairEditor) return;

  const titleInput = document.querySelector('#gallery-title');
  const pendingTitleInput = document.querySelector('#pending-group-title');
  const pendingHint = document.querySelector('#pending-hint');
  const addGroupButton = document.querySelector('#add-group');
  const swapPendingButton = document.querySelector('#swap-pending');
  const zones = [...document.querySelectorAll('.gallery-drop-zone')];
  const pendingImageTitleInputs = [...document.querySelectorAll('.gallery-pending-image-title')];
  const reviewList = document.querySelector('#review-list');
  const reviewTemplate = document.querySelector('#review-item-template');
  const emptyList = document.querySelector('#empty-list');
  const reviewCount = document.querySelector('#review-count');
  const publishButton = document.querySelector('#publish-button');
  const publishSummary = document.querySelector('#publish-summary');
  const publishDetail = document.querySelector('#publish-detail');
  const blockingError = document.querySelector('#blocking-error');
  const uploadDialog = document.querySelector('#upload-dialog');
  const uploadTitle = document.querySelector('#upload-title');
  const uploadPercent = document.querySelector('#upload-percent');
  const uploadProgress = document.querySelector('#upload-progress');
  const uploadSummary = document.querySelector('#upload-summary');
  const uploadList = document.querySelector('#upload-list');
  const uploadError = document.querySelector('#upload-error');
  const cancelUploadButton = document.querySelector('#cancel-upload');
  const retryFailedButton = document.querySelector('#retry-failed');

  const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp']);
  const configuredMaxImageBytes = Number(document.body.dataset.maxImageBytes);
  const configuredMaxProjectBytes = Number(document.body.dataset.maxProjectBytes);
  const configuredMaxGroups = Number(document.body.dataset.maxGroups);
  const limits = {
    image: Number.isFinite(configuredMaxImageBytes) && configuredMaxImageBytes > 0
      ? configuredMaxImageBytes : 20 * 1024 * 1024,
    project: Number.isFinite(configuredMaxProjectBytes) && configuredMaxProjectBytes > 0
      ? configuredMaxProjectBytes : 512 * 1024 * 1024,
    groups: Number.isInteger(configuredMaxGroups) && configuredMaxGroups > 0
      ? configuredMaxGroups : 30,
  };

  const state = {
    pending: { A: null, B: null },
    pendingVersion: { A: 0, B: 0 },
    validationCount: 0,
    groups: [],
    locked: false,
    draggedGroupId: null,
    draft: null,
    uploadRecords: new Map(),
    activeRequests: new Set(),
    createController: null,
    uploadRunning: false,
    finalizing: false,
    cancelling: false,
  };

  const makeId = (prefix) => {
    const randomPart = globalThis.crypto?.randomUUID
      ? globalThis.crypto.randomUUID().replaceAll('-', '')
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    return `${prefix}_${randomPart}`;
  };

  const totalBytes = () => state.groups.reduce(
    (sum, group) => sum + group.images.A.file.size + group.images.B.file.size,
    0,
  );

  const validateFile = (file) => {
    if (!file || !allowedTypes.has(file.type)) return '请选择 PNG、JPG、WebP、GIF 或 BMP 图片。';
    if (!file.size) return `“${file.name}”是空文件，请重新选择。`;
    if (file.size > limits.image) {
      return `“${file.name}”超过单图 ${formatBytes(limits.image)} 的限制。`;
    }
    return '';
  };

  const releaseImage = (image) => {
    if (image?.objectUrl) URL.revokeObjectURL(image.objectUrl);
  };

  const makeImageEntry = (file) => new Promise((resolve, reject) => {
    const validationError = validateFile(file);
    if (validationError) {
      reject(new Error(validationError));
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    const probe = new Image();
    probe.onload = () => resolve({
      id: makeId('img'),
      file,
      objectUrl,
      width: probe.naturalWidth,
      height: probe.naturalHeight,
      title: '',
    });
    probe.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error(`“${file.name}”已损坏或无法读取，请换一张图片。`));
    };
    probe.src = objectUrl;
  });

  const setPendingMessage = (message, isError = false) => {
    pendingHint.textContent = message;
    pendingHint.classList.toggle('is-error', isError);
  };

  const renderPendingSlot = (slot) => {
    const zone = document.querySelector(`.gallery-drop-zone[data-slot="${slot}"]`);
    const empty = zone.querySelector('.gallery-drop-empty');
    const preview = zone.querySelector('.gallery-drop-preview');
    const imageTitleInput = document.querySelector(`.gallery-pending-image-title[data-slot="${slot}"]`);
    const entry = state.pending[slot];

    if (!entry) {
      empty.hidden = false;
      preview.hidden = true;
      zone.classList.remove('has-file');
      preview.querySelector('img').removeAttribute('src');
      imageTitleInput.value = '';
      imageTitleInput.disabled = true;
      return;
    }

    preview.querySelector('img').src = entry.objectUrl;
    preview.querySelector('.gallery-file-name').textContent = entry.file.name;
    preview.querySelector('.gallery-file-meta').textContent = `${entry.width} × ${entry.height} · ${formatBytes(entry.file.size)}`;
    empty.hidden = true;
    preview.hidden = false;
    zone.classList.add('has-file');
    imageTitleInput.value = entry.title;
    imageTitleInput.disabled = state.locked;
  };

  const getBlockingError = () => {
    if (!state.groups.length) return '请至少添加一组 A / B 图片。';
    if (state.validationCount) return '正在检查所选图片，请稍候。';
    if (state.groups.length > limits.groups) return `每个图集最多包含 ${limits.groups} 组。`;
    if (totalBytes() > limits.project) return `图片总大小超过 ${formatBytes(limits.project)}，请删除或压缩部分图片。`;
    if (state.pending.A || state.pending.B) return '“逐组添加”区仍有未加入清单的图片，请先添加或移除。';
    return '';
  };

  const updatePageState = () => {
    const pendingReady = Boolean(state.pending.A && state.pending.B);
    const atLimit = state.groups.length >= limits.groups;
    addGroupButton.disabled = state.locked || Boolean(state.validationCount) || !pendingReady || atLimit;
    swapPendingButton.disabled = state.locked || Boolean(state.validationCount) || !pendingReady;

    if (state.validationCount && !state.locked) {
      setPendingMessage('正在检查所选图片…');
    } else if (atLimit && !state.locked) {
      setPendingMessage(`已达到 ${limits.groups} 组上限，请先删除一组再添加。`, true);
    } else if (pendingReady && !state.locked) {
      setPendingMessage('两张图片已就绪，可以加入核对清单。');
    } else if (!state.pending.A && !state.pending.B && !state.locked) {
      setPendingMessage('需要 A、B 两张有效图片才能加入清单。');
    } else if (!state.locked) {
      setPendingMessage(`还需要 ${state.pending.A ? 'B' : 'A'} 图片。`);
    }

    reviewCount.textContent = String(state.groups.length);
    emptyList.hidden = state.groups.length > 0;
    reviewList.hidden = state.groups.length === 0;

    const bytes = totalBytes();
    publishSummary.textContent = state.groups.length
      ? `${state.groups.length} 组 · ${state.groups.length * 2} 张图片`
      : '尚未添加对比组';
    publishDetail.textContent = state.groups.length
      ? `总大小 ${formatBytes(bytes)} / ${formatBytes(limits.project)}`
      : `最多 ${limits.groups} 组 · 单图不超过 ${formatBytes(limits.image)}`;

    const error = getBlockingError();
    blockingError.textContent = error;
    publishButton.disabled = state.locked || Boolean(error);
  };

  const setLocked = (locked) => {
    state.locked = locked;
    document.body.classList.toggle('gallery-is-uploading', locked);
    document.querySelector('.gallery-create-main').setAttribute('aria-busy', String(locked));
    document.querySelectorAll('.gallery-create-main input, .gallery-create-main button').forEach((control) => {
      control.disabled = locked;
    });
    renderPendingSlot('A');
    renderPendingSlot('B');
    renderReview();
    updatePageState();
  };

  const assignPending = async (slot, file) => {
    if (state.locked) return;
    const version = state.pendingVersion[slot] + 1;
    state.pendingVersion[slot] = version;
    state.validationCount += 1;
    const previous = state.pending[slot];
    setPendingMessage(`正在检查“${file.name}”…`);
    updatePageState();
    let validationError = '';
    try {
      const entry = await makeImageEntry(file);
      if (state.locked || state.pendingVersion[slot] !== version) {
        releaseImage(entry);
        return;
      }
      releaseImage(previous);
      state.pending[slot] = entry;
      renderPendingSlot(slot);
    } catch (error) {
      if (state.pendingVersion[slot] === version) {
        validationError = error.message || '图片无法读取，请重新选择。';
      }
    } finally {
      state.validationCount = Math.max(0, state.validationCount - 1);
      updatePageState();
      if (validationError) setPendingMessage(validationError, true);
    }
  };

  const assignDropped = async (files, preferredSlot = null) => {
    if (state.locked) return;
    const incoming = [...files];
    if (!incoming.length) return;
    if (incoming.length > 2) {
      setPendingMessage('每次最多拖入两张图片。', true);
      return;
    }
    if (incoming.length === 2) {
      await Promise.all([assignPending('A', incoming[0]), assignPending('B', incoming[1])]);
      return;
    }
    const target = preferredSlot || (!state.pending.A ? 'A' : !state.pending.B ? 'B' : 'A');
    await assignPending(target, incoming[0]);
  };

  zones.forEach((zone) => {
    const slot = zone.dataset.slot;
    const input = zone.querySelector('input[type="file"]');

    zone.addEventListener('click', (event) => {
      if (state.locked || event.target.closest('button')) return;
      input.click();
    });
    zone.addEventListener('keydown', (event) => {
      if (state.locked || event.target !== zone || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      input.click();
    });
    input.addEventListener('change', () => {
      const file = input.files?.[0];
      input.value = '';
      if (file) assignPending(slot, file);
    });
    zone.querySelector('.gallery-clear-pending').addEventListener('click', (event) => {
      event.stopPropagation();
      if (state.locked) return;
      state.pendingVersion[slot] += 1;
      releaseImage(state.pending[slot]);
      state.pending[slot] = null;
      renderPendingSlot(slot);
      updatePageState();
    });
    zone.addEventListener('dragenter', (event) => {
      event.preventDefault();
      zone.classList.add('is-dragging');
    });
    zone.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });
    zone.addEventListener('dragleave', (event) => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-dragging');
    });
    zone.addEventListener('drop', (event) => {
      event.preventDefault();
      event.stopPropagation();
      zone.classList.remove('is-dragging');
      assignDropped(event.dataTransfer.files, slot);
    });
  });

  pendingImageTitleInputs.forEach((input) => {
    input.addEventListener('input', () => {
      if (state.locked) return;
      const entry = state.pending[input.dataset.slot];
      if (entry) entry.title = input.value;
    });
  });

  pairEditor.addEventListener('dragover', (event) => {
    if (![...(event.dataTransfer?.types || [])].includes('Files')) return;
    event.preventDefault();
  });
  pairEditor.addEventListener('drop', (event) => {
    if (event.target.closest('.gallery-drop-zone')) return;
    event.preventDefault();
    assignDropped(event.dataTransfer.files);
  });

  swapPendingButton.addEventListener('click', () => {
    if (state.validationCount) return;
    [state.pending.A, state.pending.B] = [state.pending.B, state.pending.A];
    renderPendingSlot('A');
    renderPendingSlot('B');
    updatePageState();
  });

  addGroupButton.addEventListener('click', () => {
    if (!state.pending.A || !state.pending.B || state.locked || state.groups.length >= limits.groups) return;
    state.groups.push({
      id: makeId('group'),
      title: pendingTitleInput.value.trim(),
      images: { A: state.pending.A, B: state.pending.B },
      replaceVersion: { A: 0, B: 0 },
    });
    state.pending = { A: null, B: null };
    pendingTitleInput.value = '';
    renderPendingSlot('A');
    renderPendingSlot('B');
    renderReview();
    updatePageState();
    pendingTitleInput.focus();
  });

  const reviewFileSummary = (group) => {
    const a = group.images.A;
    const b = group.images.B;
    return `A · ${a.file.name} (${a.width}×${a.height})  ·  B · ${b.file.name} (${b.width}×${b.height})`;
  };

  function renderReview() {
    reviewList.replaceChildren();
    state.groups.forEach((group, index) => {
      const item = reviewTemplate.content.firstElementChild.cloneNode(true);
      item.dataset.groupId = group.id;
      item.draggable = !state.locked;
      item.querySelector('.gallery-order').textContent = String(index + 1).padStart(2, '0');
      item.querySelector('.gallery-review-image-a img').src = group.images.A.objectUrl;
      item.querySelector('.gallery-review-image-b img').src = group.images.B.objectUrl;
      item.querySelector('.gallery-review-image-a img').alt = `A：${group.images.A.file.name}`;
      item.querySelector('.gallery-review-image-b img').alt = `B：${group.images.B.file.name}`;
      const groupTitle = item.querySelector('.gallery-review-title');
      groupTitle.value = group.title;
      groupTitle.placeholder = `第 ${index + 1} 组`;
      groupTitle.disabled = state.locked;
      item.querySelectorAll('.gallery-review-image-title').forEach((input) => {
        const image = group.images[input.dataset.slot];
        input.value = image.title;
        input.disabled = state.locked;
      });
      item.querySelector('.gallery-review-files').textContent = reviewFileSummary(group);
      item.querySelector('[data-action="up"]').disabled = state.locked || index === 0;
      item.querySelector('[data-action="down"]').disabled = state.locked || index === state.groups.length - 1;
      item.querySelectorAll('button').forEach((button) => {
        if (!['up', 'down'].includes(button.dataset.action)) button.disabled = state.locked;
      });
      reviewList.append(item);
    });
  }

  const replaceGroupImage = async (group, slot, file) => {
    const version = group.replaceVersion[slot] + 1;
    group.replaceVersion[slot] = version;
    state.validationCount += 1;
    setPendingMessage(`正在检查“${file.name}”…`);
    updatePageState();
    let validationError = '';
    let applied = false;
    try {
      const entry = await makeImageEntry(file);
      if (state.locked || !state.groups.includes(group) || group.replaceVersion[slot] !== version) {
        releaseImage(entry);
        return;
      }
      releaseImage(group.images[slot]);
      group.images[slot] = entry;
      applied = true;
      renderReview();
    } catch (error) {
      if (group.replaceVersion[slot] === version) {
        validationError = error.message || '图片无法读取，请重新选择。';
      }
    } finally {
      state.validationCount = Math.max(0, state.validationCount - 1);
      updatePageState();
      if (state.groups.includes(group) && group.replaceVersion[slot] === version) {
        if (validationError) setPendingMessage(validationError, true);
        else if (applied) setPendingMessage('图片已替换。');
      }
    }
  };

  reviewList.addEventListener('input', (event) => {
    const input = event.target.closest('.gallery-review-title, .gallery-review-image-title');
    if (!input || state.locked) return;
    const group = state.groups.find((candidate) => candidate.id === input.closest('li').dataset.groupId);
    if (!group) return;
    if (input.classList.contains('gallery-review-title')) group.title = input.value;
    else group.images[input.dataset.slot].title = input.value;
  });

  reviewList.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button || state.locked) return;
    const item = button.closest('li');
    const index = state.groups.findIndex((group) => group.id === item.dataset.groupId);
    if (index < 0) return;
    const group = state.groups[index];
    const action = button.dataset.action;

    if (action === 'replace-a' || action === 'replace-b') {
      item.querySelector(`.gallery-replace-input[data-slot="${action.endsWith('a') ? 'A' : 'B'}"]`).click();
      return;
    }
    if (action === 'swap') {
      [group.images.A, group.images.B] = [group.images.B, group.images.A];
    } else if (action === 'delete') {
      releaseImage(group.images.A);
      releaseImage(group.images.B);
      state.groups.splice(index, 1);
    } else if (action === 'up' && index > 0) {
      state.groups = moveItem(state.groups, index, index - 1);
    } else if (action === 'down' && index < state.groups.length - 1) {
      state.groups = moveItem(state.groups, index, index + 1);
    }
    renderReview();
    updatePageState();
  });

  reviewList.addEventListener('change', (event) => {
    const input = event.target.closest('.gallery-replace-input');
    if (!input || state.locked) return;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const group = state.groups.find((candidate) => candidate.id === input.closest('li').dataset.groupId);
    if (group) replaceGroupImage(group, input.dataset.slot, file);
  });

  reviewList.addEventListener('dragstart', (event) => {
    const item = event.target.closest('.gallery-review-item');
    if (!item || state.locked) return;
    state.draggedGroupId = item.dataset.groupId;
    item.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', state.draggedGroupId);
  });
  reviewList.addEventListener('dragover', (event) => {
    const item = event.target.closest('.gallery-review-item');
    if (!item || !state.draggedGroupId || item.dataset.groupId === state.draggedGroupId) return;
    event.preventDefault();
    reviewList.querySelectorAll('.is-drop-before, .is-drop-after').forEach((node) => {
      node.classList.remove('is-drop-before', 'is-drop-after');
    });
    const after = event.clientY > item.getBoundingClientRect().top + item.offsetHeight / 2;
    item.classList.add(after ? 'is-drop-after' : 'is-drop-before');
  });
  reviewList.addEventListener('drop', (event) => {
    const target = event.target.closest('.gallery-review-item');
    if (!target || !state.draggedGroupId) return;
    event.preventDefault();
    const from = state.groups.findIndex((group) => group.id === state.draggedGroupId);
    let to = state.groups.findIndex((group) => group.id === target.dataset.groupId);
    const after = event.clientY > target.getBoundingClientRect().top + target.offsetHeight / 2;
    if (after) to += 1;
    if (from < to) to -= 1;
    if (from >= 0) {
      const next = [...state.groups];
      const [moved] = next.splice(from, 1);
      next.splice(Math.max(0, Math.min(next.length, to)), 0, moved);
      state.groups = next;
    }
    state.draggedGroupId = null;
    renderReview();
    updatePageState();
  });
  reviewList.addEventListener('dragend', () => {
    state.draggedGroupId = null;
    reviewList.querySelectorAll('.is-dragging, .is-drop-before, .is-drop-after').forEach((node) => {
      node.classList.remove('is-dragging', 'is-drop-before', 'is-drop-after');
    });
  });

  const buildManifest = () => ({
    kind: 'gallery',
    title: titleInput.value.trim(),
    variants: [
      { id: 'A', label: 'A' },
      { id: 'B', label: 'B' },
    ],
    groups: state.groups.map((group) => ({
      id: group.id,
      title: group.title.trim(),
      images: { A: group.images.A.id, B: group.images.B.id },
    })),
    images: state.groups.flatMap((group) => ['A', 'B'].map((slot) => ({
      id: group.images[slot].id,
      original_name: group.images[slot].file.name,
      title: group.images[slot].title.trim(),
    }))),
  });

  const errorMessage = (result, fallback) => (
    result?.error?.message || (typeof result?.error === 'string' ? result.error : '') || result?.message || fallback
  );

  const normalizeUploads = (uploads) => {
    if (uploads && !Array.isArray(uploads)) return uploads;
    return Object.fromEntries((uploads || []).map((item) => [item.image_id || item.client_id, item.url]));
  };

  const makeUploadRecords = (uploadUrls) => {
    const records = new Map();
    state.groups.forEach((group, groupIndex) => {
      ['A', 'B'].forEach((slot) => {
        const image = group.images[slot];
        records.set(image.id, {
          id: image.id,
          groupId: group.id,
          groupIndex,
          slot,
          image,
          url: uploadUrls[image.id],
          status: 'waiting',
          loaded: 0,
          error: '',
        });
      });
    });
    return records;
  };

  const statusCopy = {
    waiting: '等待上传',
    uploading: '正在上传',
    success: '上传完成',
    failed: '上传失败',
    cancelled: '已取消',
  };

  const renderUploadList = () => {
    uploadList.replaceChildren();
    [...state.uploadRecords.values()].forEach((record) => {
      const item = document.createElement('li');
      item.dataset.imageId = record.id;
      item.className = `is-${record.status}`;
      const identity = document.createElement('span');
      identity.className = `gallery-upload-identity is-${record.slot.toLowerCase()}`;
      identity.textContent = `${record.groupIndex + 1}${record.slot}`;
      const copy = document.createElement('span');
      copy.className = 'gallery-upload-item-copy';
      const name = document.createElement('strong');
      name.textContent = record.image.file.name;
      const detail = document.createElement('small');
      detail.textContent = record.error || `${formatBytes(record.image.file.size)} · ${statusCopy[record.status]}`;
      copy.append(name, detail);
      const stateLabel = document.createElement('span');
      stateLabel.className = 'gallery-upload-state';
      stateLabel.textContent = record.status === 'uploading'
        ? `${Math.round((record.loaded / record.image.file.size) * 100)}%`
        : statusCopy[record.status];
      item.append(identity, copy, stateLabel);
      if (record.status === 'failed') {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.dataset.retryId = record.id;
        retry.textContent = '重试';
        retry.setAttribute('aria-label', `重试上传 ${record.image.file.name}`);
        item.append(retry);
      }
      uploadList.append(item);
    });
  };

  const updateUploadProgress = () => {
    const records = [...state.uploadRecords.values()];
    const bytes = records.reduce((sum, record) => {
      if (record.status === 'success') return sum + record.image.file.size;
      return sum + Math.min(record.loaded, record.image.file.size);
    }, 0);
    const total = records.reduce((sum, record) => sum + record.image.file.size, 0);
    const percent = total ? Math.min(100, Math.round((bytes / total) * 100)) : 0;
    uploadProgress.style.width = `${percent}%`;
    uploadPercent.textContent = `${percent}%`;

    const successful = records.filter((record) => record.status === 'success').length;
    const failed = records.filter((record) => record.status === 'failed').length;
    const uploading = records.filter((record) => record.status === 'uploading').length;
    uploadSummary.textContent = failed
      ? `${successful} / ${records.length} 张完成，${failed} 张需要重试。`
      : `${successful} / ${records.length} 张完成${uploading ? `，${uploading} 张正在上传` : ''}。`;
    retryFailedButton.hidden = failed === 0 || state.uploadRunning || state.finalizing;
  };

  const updateUploadRecord = (record, status, error = '') => {
    record.status = status;
    record.error = error;
    if (status === 'success') record.loaded = record.image.file.size;
    renderUploadList();
    updateUploadProgress();
  };

  const uploadOne = (record) => new Promise((resolve) => {
    if (state.cancelling || !state.draft) {
      updateUploadRecord(record, 'cancelled');
      resolve();
      return;
    }
    if (!record.url) {
      updateUploadRecord(record, 'failed', '服务器没有提供这个文件的上传地址。');
      resolve();
      return;
    }

    record.loaded = 0;
    updateUploadRecord(record, 'uploading');
    const request = new XMLHttpRequest();
    state.activeRequests.add(request);
    request.open('PUT', record.url, true);
    request.setRequestHeader('X-Edit-Token', state.draft.token);
    request.upload.addEventListener('progress', (event) => {
      if (!event.lengthComputable) return;
      record.loaded = Math.min(event.loaded, record.image.file.size);
      const item = uploadList.querySelector(`[data-image-id="${CSS.escape(record.id)}"]`);
      if (item) item.querySelector('.gallery-upload-state').textContent = `${Math.round((record.loaded / record.image.file.size) * 100)}%`;
      updateUploadProgress();
    });
    request.addEventListener('load', () => {
      state.activeRequests.delete(request);
      const result = (() => {
        try { return JSON.parse(request.responseText); } catch { return {}; }
      })();
      if (request.status >= 200 && request.status < 300) {
        updateUploadRecord(record, 'success');
      } else {
        record.loaded = 0;
        updateUploadRecord(record, 'failed', errorMessage(result, `上传失败（HTTP ${request.status}）`));
      }
      resolve();
    });
    request.addEventListener('error', () => {
      state.activeRequests.delete(request);
      record.loaded = 0;
      updateUploadRecord(record, 'failed', '网络连接中断，请重试这一张。');
      resolve();
    });
    request.addEventListener('abort', () => {
      state.activeRequests.delete(request);
      record.loaded = 0;
      updateUploadRecord(record, state.cancelling ? 'cancelled' : 'failed', state.cancelling ? '' : '上传已中断。');
      resolve();
    });
    const body = new FormData();
    body.append('file', record.image.file, record.image.file.name);
    request.send(body);
  });

  const finalizeDraft = async () => {
    if (!state.draft || state.finalizing || state.cancelling) return;
    state.finalizing = true;
    retryFailedButton.hidden = true;
    cancelUploadButton.disabled = true;
    uploadTitle.textContent = '正在发布图集';
    uploadSummary.textContent = '所有图片已上传，正在生成缩略图和专属页面…';
    uploadError.textContent = '';
    try {
      const response = await fetch(`/api/drafts/${encodeURIComponent(state.draft.id)}/finalize`, {
        method: 'POST',
        headers: { 'X-Edit-Token': state.draft.token },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(result, '发布失败，请稍后重试。'));
      const destination = result.url || (result.id ? `/gallery/${encodeURIComponent(result.id)}` : '');
      if (!destination) throw new Error('服务器没有返回图集地址。');
      window.location.assign(destination);
    } catch (error) {
      state.finalizing = false;
      cancelUploadButton.disabled = false;
      uploadTitle.textContent = '发布未完成';
      uploadError.textContent = error.message || '发布失败，请检查服务后重试。';
      uploadSummary.textContent = '图片已上传，无需重新上传。请点击下方按钮再次发布。';
      retryFailedButton.hidden = false;
      retryFailedButton.textContent = '重新发布';
      retryFailedButton.dataset.action = 'finalize';
    }
  };

  const runUploads = async (records) => {
    if (state.uploadRunning || state.cancelling) return;
    state.uploadRunning = true;
    uploadError.textContent = '';
    retryFailedButton.hidden = true;
    retryFailedButton.dataset.action = 'retry';
    retryFailedButton.textContent = '重试全部失败项';
    await runConcurrent(records, 3, uploadOne);
    state.uploadRunning = false;
    if (state.cancelling) return;
    const failed = [...state.uploadRecords.values()].filter((record) => record.status === 'failed');
    renderUploadList();
    updateUploadProgress();
    if (failed.length) {
      uploadTitle.textContent = '有图片上传失败';
      uploadError.textContent = '已完成的图片不会重传。可单独重试失败项，或一次重试全部。';
    } else {
      await finalizeDraft();
    }
  };

  const createAndUpload = async () => {
    const blocking = getBlockingError();
    if (blocking || state.locked) {
      blockingError.textContent = blocking;
      return;
    }
    setLocked(true);
    state.cancelling = false;
    state.finalizing = false;
    state.draft = null;
    state.uploadRecords.clear();
    uploadList.replaceChildren();
    uploadProgress.style.width = '0%';
    uploadPercent.textContent = '0%';
    uploadTitle.textContent = '正在上传图集';
    uploadSummary.textContent = '正在创建安全上传清单…';
    uploadError.textContent = '';
    cancelUploadButton.disabled = false;
    retryFailedButton.hidden = true;
    publishButton.classList.add('is-loading');
    uploadDialog.showModal();

    state.createController = new AbortController();
    try {
      const response = await fetch('/api/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildManifest()),
        signal: state.createController.signal,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(errorMessage(result, '无法创建上传清单。'));
      if (!result.draft_id || !result.edit_token) throw new Error('服务器返回的草稿信息不完整。');
      state.draft = { id: result.draft_id, token: result.edit_token };
      const uploadUrls = normalizeUploads(result.uploads);
      state.uploadRecords = makeUploadRecords(uploadUrls);
      renderUploadList();
      updateUploadProgress();
      await runUploads([...state.uploadRecords.values()]);
    } catch (error) {
      if (state.cancelling || error.name === 'AbortError') return;
      uploadTitle.textContent = '无法开始上传';
      uploadSummary.textContent = '图片仍保留在本页，可以取消后再次尝试。';
      uploadError.textContent = error.message || '网络连接失败，请确认服务仍在运行。';
    } finally {
      state.createController = null;
    }
  };

  const cancelUpload = async () => {
    if (state.cancelling || state.finalizing) return;
    state.cancelling = true;
    cancelUploadButton.disabled = true;
    uploadTitle.textContent = '正在取消上传';
    uploadSummary.textContent = '正在停止请求并清理服务器草稿…';
    uploadError.textContent = '';
    state.createController?.abort();
    state.activeRequests.forEach((request) => request.abort());

    try {
      if (state.draft) {
        const response = await fetch(`/api/drafts/${encodeURIComponent(state.draft.id)}`, {
          method: 'DELETE',
          headers: { 'X-Edit-Token': state.draft.token },
        });
        if (!response.ok && response.status !== 404) {
          const result = await response.json().catch(() => ({}));
          throw new Error(errorMessage(result, '服务器未能清理草稿，请重试取消。'));
        }
      }
    } catch (error) {
      state.cancelling = false;
      cancelUploadButton.disabled = false;
      uploadTitle.textContent = '取消未完成';
      uploadSummary.textContent = '本地清单仍然保留，请在连接恢复后再次取消。';
      uploadError.textContent = error.message || '无法确认服务器草稿是否已清理，请重试。';
      return;
    }

    state.draft = null;
    state.uploadRecords.clear();
    state.activeRequests.clear();
    state.uploadRunning = false;
    state.cancelling = false;
    publishButton.classList.remove('is-loading');
    if (uploadDialog.open) uploadDialog.close();
    setLocked(false);
  };

  uploadList.addEventListener('click', (event) => {
    const retry = event.target.closest('button[data-retry-id]');
    if (!retry || state.uploadRunning || state.finalizing) return;
    const record = state.uploadRecords.get(retry.dataset.retryId);
    if (!record || record.status !== 'failed') return;
    record.loaded = 0;
    record.error = '';
    record.status = 'waiting';
    runUploads([record]);
  });

  retryFailedButton.addEventListener('click', () => {
    if (retryFailedButton.dataset.action === 'finalize') {
      finalizeDraft();
      return;
    }
    const failed = [...state.uploadRecords.values()].filter((record) => record.status === 'failed');
    failed.forEach((record) => {
      record.loaded = 0;
      record.error = '';
      record.status = 'waiting';
    });
    if (failed.length) runUploads(failed);
  });

  publishButton.addEventListener('click', createAndUpload);
  cancelUploadButton.addEventListener('click', cancelUpload);
  uploadDialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    cancelUpload();
  });

  window.addEventListener('beforeunload', (event) => {
    if (state.locked && !state.finalizing) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('pagehide', () => {
    releaseImage(state.pending.A);
    releaseImage(state.pending.B);
    state.groups.forEach((group) => {
      releaseImage(group.images.A);
      releaseImage(group.images.B);
    });
  });

  renderPendingSlot('A');
  renderPendingSlot('B');
  renderReview();
  updatePageState();
})();
