(() => {
  'use strict';

  const MIN_IMAGES = 2;
  const HARD_MAX_IMAGES = 8;

  const clampImageLimit = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return HARD_MAX_IMAGES;
    return Math.max(MIN_IMAGES, Math.min(HARD_MAX_IMAGES, Math.trunc(numeric)));
  };

  const normalizeSlotCount = (slotCount, selectedCount, maxImages = HARD_MAX_IMAGES) => {
    const limit = clampImageLimit(maxImages);
    const selected = Math.max(0, Math.min(limit, Math.trunc(Number(selectedCount)) || 0));
    const requested = Math.trunc(Number(slotCount)) || MIN_IMAGES;
    return Math.max(MIN_IMAGES, selected, Math.min(limit, requested));
  };

  const planSlotAssignments = (
    occupiedSlots,
    incomingCount,
    targetIndex = null,
    maxImages = HARD_MAX_IMAGES,
  ) => {
    const limit = clampImageLimit(maxImages);
    const occupied = Array.from(
      { length: Math.min(limit, Math.max(MIN_IMAGES, occupiedSlots.length)) },
      (_, index) => Boolean(occupiedSlots[index]),
    );
    let remaining = Math.max(0, Math.trunc(Number(incomingCount)) || 0);
    const targets = [];
    const preferred = Number.isInteger(targetIndex)
      && targetIndex >= 0
      && targetIndex < occupied.length
      ? targetIndex
      : null;

    if (preferred !== null && remaining) {
      targets.push(preferred);
      occupied[preferred] = true;
      remaining -= 1;
    }
    const candidates = preferred === null
      ? Array.from({ length: limit }, (_, index) => index)
      : [
          ...Array.from({ length: limit - preferred - 1 }, (_, index) => preferred + index + 1),
          ...Array.from({ length: preferred }, (_, index) => index),
        ];
    for (const next of candidates) {
      if (!remaining) break;
      if (occupied[next]) continue;
      while (occupied.length <= next) occupied.push(false);
      targets.push(next);
      occupied[next] = true;
      remaining -= 1;
    }
    return { targets, slotCount: occupied.length, remaining };
  };

  const testHooks = {
    minImages: MIN_IMAGES,
    hardMaxImages: HARD_MAX_IMAGES,
    clampImageLimit,
    normalizeSlotCount,
    planSlotAssignments,
  };
  globalThis.AnalyzeCreateUtils = testHooks;
  globalThis.__analyzeCreateTestHooks = Object.assign(
    globalThis.__analyzeCreateTestHooks || {},
    testHooks,
  );

  if (typeof document === 'undefined') return;

  const form = document.querySelector('#analyze-form');
  const grid = document.querySelector('#analyze-slot-grid');
  const template = document.querySelector('#analyze-slot-template');
  const titleInput = document.querySelector('#project-title');
  const countNode = document.querySelector('#slot-count');
  const errorNode = document.querySelector('#analyze-error');
  const summaryNode = document.querySelector('#selection-summary');
  const sizeNode = document.querySelector('#selection-size');
  const createButton = document.querySelector('#create-analysis');
  const uploadPanel = document.querySelector('#analyze-upload-panel');
  const uploadList = document.querySelector('#analyze-upload-list');
  const uploadPhase = document.querySelector('#upload-phase');
  const uploadProgressCopy = document.querySelector('#upload-progress-copy');
  const uploadProgressBar = document.querySelector('#upload-progress-bar');
  const retryButton = document.querySelector('#retry-upload');
  const cancelButton = document.querySelector('#cancel-upload');
  const addSlotButton = document.querySelector('#add-slot');
  const removeSlotButton = document.querySelector('#remove-slot');

  if (!form || !grid || !template) return;

  const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp']);
  const letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
  const configuredImageLimit = Number(document.body.dataset.maxImageBytes);
  const configuredProjectLimit = Number(document.body.dataset.maxProjectBytes);
  const maxImages = clampImageLimit(document.body.dataset.maxImages);
  const maxImageBytes = configuredImageLimit > 0 ? configuredImageLimit : 20 * 1024 * 1024;
  const maxProjectBytes = configuredProjectLimit > 0 ? configuredProjectLimit : 512 * 1024 * 1024;
  const records = Array(MIN_IMAGES).fill(null);
  let slotCount = MIN_IMAGES;
  let busy = false;
  let draft = null;
  let jobs = [];
  let activeRequests = new Set();
  let createController = null;
  let cancelling = false;
  let finalizing = false;
  let allowNavigation = false;
  let dragDepth = 0;

  const formatBytes = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    const value = bytes / (1024 * 1024);
    return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} MB`;
  };

  const showError = (message) => {
    errorNode.textContent = message || '';
    form.classList.toggle('has-error', Boolean(message));
  };

  const validateFile = (file) => {
    if (!allowedTypes.has(file.type)) return `“${file.name}”不是支持的图片格式。`;
    if (!file.size) return `“${file.name}”是空文件，请重新选择。`;
    if (file.size > maxImageBytes) return `“${file.name}”超过单图 ${formatBytes(maxImageBytes)} 的限制。`;
    return '';
  };

  const makeRecord = (file) => ({
    file,
    title: '',
    previewUrl: URL.createObjectURL(file),
  });

  const revokeRecord = (record) => {
    if (record?.previewUrl) URL.revokeObjectURL(record.previewUrl);
  };

  const selectedRecords = () => records.filter(Boolean);

  const updateSummary = () => {
    const selected = selectedRecords();
    const total = selected.reduce((sum, record) => sum + record.file.size, 0);
    countNode.textContent = `${selected.length} 张已选 · ${slotCount} 个槽位`;
    sizeNode.textContent = selected.length
      ? `合计 ${formatBytes(total)} · 单图上限 ${formatBytes(maxImageBytes)}`
      : '支持 PNG、JPG、WebP、GIF、BMP';
    const missing = slotCount - selected.length;
    if (selected.length < MIN_IMAGES) summaryNode.textContent = `还需要 ${MIN_IMAGES - selected.length} 张图片`;
    else if (missing) summaryNode.textContent = `还需要填入 ${missing} 个槽位`;
    else summaryNode.textContent = `已选择 ${selected.length} 张图片，可以生成`;
    const valid = selected.length === slotCount
      && selected.length >= MIN_IMAGES
      && selected.length <= maxImages
      && total <= maxProjectBytes;
    createButton.disabled = busy || !valid;
    addSlotButton.disabled = busy || slotCount >= maxImages;
    removeSlotButton.disabled = busy || slotCount <= MIN_IMAGES || Boolean(records[slotCount - 1]);
    if (total > maxProjectBytes) showError(`全部图片合计不能超过 ${formatBytes(maxProjectBytes)}。`);
  };

  const moveRecord = (from, to) => {
    if (busy || !records[from] || to < 0 || to >= records.length || from === to) return;
    [records[from], records[to]] = [records[to], records[from]];
    renderSlots();
  };

  const removeRecord = (index) => {
    if (busy || !records[index]) return;
    revokeRecord(records[index]);
    records[index] = null;
    showError('');
    renderSlots();
  };

  const addFiles = (fileList, targetIndex = null) => {
    if (busy) return;
    const incoming = [...fileList];
    if (!incoming.length) return;
    const preferred = Number.isInteger(targetIndex)
      && targetIndex >= 0
      && targetIndex < records.length
      ? targetIndex
      : null;
    const replacing = preferred !== null && Boolean(records[preferred]);
    const plan = planSlotAssignments(
      records.map(Boolean),
      incoming.length,
      preferred,
      maxImages,
    );
    if (plan.remaining) {
      showError(`图片对比最多使用 ${maxImages} 张图片，本次最多还能添加 ${plan.targets.length} 张。`);
      return;
    }
    const error = incoming.map(validateFile).find(Boolean);
    if (error) {
      showError(error);
      return;
    }
    const nextTotal = records.reduce((sum, record, index) => (
      sum + (!record || (replacing && index === preferred) ? 0 : record.file.size)
    ), 0) + incoming.reduce((sum, file) => sum + file.size, 0);
    if (nextTotal > maxProjectBytes) {
      showError(`全部图片合计不能超过 ${formatBytes(maxProjectBytes)}。`);
      return;
    }

    showError('');
    const additions = incoming.map(makeRecord);
    while (records.length < plan.slotCount) records.push(null);
    plan.targets.forEach((slotIndex, additionIndex) => {
      const previous = records[slotIndex];
      revokeRecord(previous);
      records[slotIndex] = additions[additionIndex];
    });
    if (additions.length > plan.targets.length) {
      additions.slice(plan.targets.length).forEach(revokeRecord);
    }
    slotCount = records.length;
    renderSlots();
  };

  const wireSlot = (slot, index) => {
    const drop = slot.querySelector('.analyze-slot-drop');
    const input = slot.querySelector('.analyze-file-input');
    const record = records[index];

    drop.addEventListener('click', (event) => {
      if (busy || event.target.closest('button, input, label')) return;
      input.click();
    });
    drop.addEventListener('keydown', (event) => {
      if (busy || event.target !== drop || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      input.click();
    });
    input.addEventListener('change', () => {
      if (input.files.length) addFiles([input.files[0]], index);
      input.value = '';
    });
    drop.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (!busy) drop.classList.add('is-dragging');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('is-dragging'));
    drop.addEventListener('drop', (event) => {
      event.preventDefault();
      event.stopPropagation();
      dragDepth = 0;
      document.body.classList.remove('is-file-dragging');
      drop.classList.remove('is-dragging');
      addFiles(event.dataTransfer.files, index);
    });

    if (!record) return;
    slot.querySelector('.analyze-image-title-input').addEventListener('input', (event) => {
      if (busy) {
        event.target.value = record.title;
        return;
      }
      record.title = event.target.value;
    });
    slot.querySelector('.move-left').addEventListener('click', () => moveRecord(index, index - 1));
    slot.querySelector('.move-right').addEventListener('click', () => moveRecord(index, index + 1));
    slot.querySelector('.replace-image').addEventListener('click', () => input.click());
    slot.querySelector('.remove-image').addEventListener('click', () => removeRecord(index));
  };

  function renderSlots() {
    grid.replaceChildren();
    slotCount = normalizeSlotCount(slotCount, records.length, maxImages);
    grid.style.setProperty('--slot-columns', String(Math.min(slotCount, 4)));
    for (let index = 0; index < slotCount; index += 1) {
      const slot = template.content.firstElementChild.cloneNode(true);
      const letter = letters[index];
      const record = records[index];
      slot.dataset.index = String(index);
      slot.querySelectorAll('.analyze-variant-badge').forEach((badge) => { badge.textContent = letter; });
      const drop = slot.querySelector('.analyze-slot-drop');
      const fileInput = slot.querySelector('.analyze-file-input');
      const empty = slot.querySelector('.analyze-slot-empty');
      const preview = slot.querySelector('.analyze-slot-preview');
      const edit = slot.querySelector('.analyze-slot-edit');
      fileInput.disabled = busy;
      drop.tabIndex = busy ? -1 : 0;
      drop.setAttribute('aria-disabled', String(busy));

      if (record) {
        slot.classList.add('has-file');
        drop.setAttribute('aria-label', `替换图片 ${letter}：${record.file.name}`);
        empty.hidden = true;
        preview.hidden = false;
        edit.hidden = false;
        const image = preview.querySelector('img');
        image.src = record.previewUrl;
        image.alt = `图片 ${letter} 预览`;
        preview.querySelector('.analyze-file-name').textContent = record.file.name;
        preview.querySelector('.analyze-file-meta').textContent = formatBytes(record.file.size);
        const imageTitleInput = edit.querySelector('.analyze-image-title-input');
        imageTitleInput.value = record.title;
        imageTitleInput.setAttribute('aria-label', `图片 ${letter} 的标题（可选）`);
        imageTitleInput.disabled = busy;
        edit.querySelector('.move-left').disabled = busy || index === 0;
        edit.querySelector('.move-right').disabled = busy || index === records.length - 1;
        edit.querySelector('.replace-image').disabled = busy;
        edit.querySelector('.remove-image').disabled = busy;
      } else {
        drop.setAttribute('aria-label', `添加图片 ${letter}，按回车选择文件`);
        empty.querySelector('strong').textContent = `添加图片 ${letter}`;
      }
      wireSlot(slot, index);
      grid.append(slot);
    }
    updateSummary();
  }

  const buildManifest = () => {
    const variants = records.map((_record, index) => {
      const id = letters[index];
      return { id, label: id };
    });
    const imageMap = {};
    const images = records.map((record, index) => {
      const variantId = letters[index];
      const imageId = `image_${variantId.toLowerCase()}`;
      imageMap[variantId] = imageId;
      return {
        id: imageId,
        original_name: record.file.name,
        title: record.title.trim(),
      };
    });
    return {
      kind: 'multi',
      title: titleInput.value.trim(),
      variants,
      groups: [{ id: 'main', title: '', images: imageMap }],
      images,
    };
  };

  const setBusy = (value) => {
    busy = value;
    form.classList.toggle('is-uploading', value);
    form.setAttribute('aria-busy', String(value));
    titleInput.disabled = value;
    createButton.classList.toggle('is-loading', value);
    uploadPanel.hidden = !value;
    updateSummary();
    renderSlots();
  };

  const jobStatusCopy = (job) => {
    if (job.status === 'uploading') return `${Math.round((job.loaded / job.total) * 100)}%`;
    if (job.status === 'success') return '已完成';
    if (job.status === 'failed') return job.error || '上传失败';
    if (job.status === 'cancelled') return '已取消';
    return '等待上传';
  };

  const renderUploadProgress = () => {
    const total = jobs.reduce((sum, job) => sum + job.total, 0);
    const loaded = jobs.reduce((sum, job) => sum + Math.min(job.loaded, job.total), 0);
    const percent = total ? Math.round((loaded / total) * 100) : 0;
    uploadProgressCopy.textContent = `${percent}%`;
    uploadProgressBar.style.width = `${percent}%`;
    uploadList.replaceChildren(...jobs.map((job) => {
      const row = document.createElement('li');
      row.className = `is-${job.status}`;
      const identity = document.createElement('div');
      const badge = document.createElement('span');
      badge.className = 'analyze-variant-badge';
      badge.textContent = job.variantId;
      const name = document.createElement('strong');
      name.textContent = job.file.name;
      identity.append(badge, name);
      const status = document.createElement('span');
      status.className = 'analyze-job-status';
      status.textContent = jobStatusCopy(job);
      row.append(identity, status);
      return row;
    }));
  };

  const parseJson = (text) => {
    try { return JSON.parse(text); } catch { return {}; }
  };

  const uploadOne = (job) => new Promise((resolve) => {
    if (cancelling) {
      job.status = 'cancelled';
      resolve();
      return;
    }
    job.status = 'uploading';
    job.error = '';
    job.loaded = 0;
    renderUploadProgress();

    const request = new XMLHttpRequest();
    activeRequests.add(request);
    request.open('PUT', job.url);
    request.setRequestHeader('X-Edit-Token', draft.editToken);
    request.upload.addEventListener('progress', (event) => {
      if (!event.lengthComputable) return;
      job.loaded = Math.min(job.total, (event.loaded / event.total) * job.total);
      renderUploadProgress();
    });
    request.addEventListener('load', () => {
      activeRequests.delete(request);
      const result = parseJson(request.responseText);
      if (request.status >= 200 && request.status < 300) {
        job.status = 'success';
        job.loaded = job.total;
      } else {
        job.status = 'failed';
        job.loaded = 0;
        job.error = result.error?.message || '上传失败，请重试。';
      }
      renderUploadProgress();
      resolve();
    });
    request.addEventListener('error', () => {
      activeRequests.delete(request);
      job.status = 'failed';
      job.loaded = 0;
      job.error = '网络连接中断，请重试。';
      renderUploadProgress();
      resolve();
    });
    request.addEventListener('abort', () => {
      activeRequests.delete(request);
      job.status = 'cancelled';
      job.loaded = 0;
      renderUploadProgress();
      resolve();
    });
    const body = new FormData();
    body.append('file', job.file, job.file.name);
    request.send(body);
  });

  const runQueue = async (selectedJobs) => {
    let cursor = 0;
    const worker = async () => {
      while (!cancelling && cursor < selectedJobs.length) {
        const job = selectedJobs[cursor];
        cursor += 1;
        await uploadOne(job);
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, selectedJobs.length) }, worker));
  };

  const finalizeDraft = async () => {
    if (!draft || finalizing || cancelling) return;
    finalizing = true;
    let published = false;
    cancelButton.disabled = true;
    retryButton.hidden = true;
    uploadPhase.textContent = '正在发布只读图片对比页面…';
    try {
      const response = await fetch(`/api/drafts/${encodeURIComponent(draft.id)}/finalize`, {
        method: 'POST',
        headers: { 'X-Edit-Token': draft.editToken },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error?.message || '发布失败，请重试。');
      uploadPhase.textContent = '发布完成，正在打开…';
      published = true;
      allowNavigation = true;
      window.location.assign(result.url || `/analyze/${result.id}`);
    } catch (error) {
      uploadPhase.textContent = '图片已上传，但发布失败';
      showError(error.message || '发布失败，请稍后重试。');
      retryButton.textContent = '重新发布';
      retryButton.hidden = false;
    } finally {
      finalizing = false;
      if (!published) cancelButton.disabled = false;
    }
  };

  const uploadPending = async (selectedJobs) => {
    retryButton.hidden = true;
    uploadPhase.textContent = '正在上传图片…';
    await runQueue(selectedJobs);
    if (cancelling) return;
    const failed = jobs.filter((job) => job.status === 'failed');
    if (failed.length) {
      uploadPhase.textContent = `${failed.length} 张图片上传失败`;
      retryButton.textContent = '重试失败项';
      retryButton.hidden = false;
      showError('成功图片已保留；重试只会重新上传失败项。');
      return;
    }
    await finalizeDraft();
  };

  const resetUploadState = () => {
    draft = null;
    jobs = [];
    createController = null;
    cancelling = false;
    finalizing = false;
    allowNavigation = false;
    uploadList.replaceChildren();
    uploadProgressBar.style.width = '0%';
    uploadProgressCopy.textContent = '0%';
    retryButton.hidden = true;
    setBusy(false);
  };

  const cancelUpload = async () => {
    if (!busy || cancelling || finalizing) return;
    cancelling = true;
    cancelButton.disabled = true;
    uploadPhase.textContent = '正在取消并清理草稿…';
    createController?.abort();
    activeRequests.forEach((request) => request.abort());
    activeRequests.clear();
    try {
      if (draft) {
        const response = await fetch(`/api/drafts/${encodeURIComponent(draft.id)}`, {
          method: 'DELETE',
          headers: { 'X-Edit-Token': draft.editToken },
        });
        if (!response.ok && response.status !== 404) {
          const result = await response.json().catch(() => ({}));
          throw new Error(result.error?.message || '草稿清理失败，请重试取消。');
        }
      }
      showError('上传已取消，所选图片仍保留在本页。');
      resetUploadState();
    } catch (error) {
      cancelling = false;
      uploadPhase.textContent = '取消失败';
      showError(error.message || '无法清理服务器草稿，请重试。');
    } finally {
      cancelButton.disabled = false;
    }
  };

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const selected = selectedRecords();
    if (
      busy
      || selected.length < MIN_IMAGES
      || selected.length !== records.length
      || selected.length > maxImages
    ) return;
    showError('');
    const manifest = buildManifest();
    const fileSnapshot = records.map((record) => record.file);
    setBusy(true);
    uploadPhase.textContent = '正在创建安全上传草稿…';
    renderUploadProgress();
    createController = new AbortController();

    try {
      const response = await fetch('/api/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manifest),
        signal: createController.signal,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error?.message || '无法创建上传草稿。');
      const draftId = result.draft_id || result.id;
      const editToken = result.edit_token;
      const uploadMap = result.uploads || {};
      if (!draftId || !editToken) throw new Error('服务器返回的草稿信息不完整。');
      draft = { id: draftId, editToken };
      jobs = manifest.images.map((image, index) => {
        const slot = uploadMap[image.id];
        const url = typeof slot === 'string' ? slot : slot?.url;
        if (!url) throw new Error(`服务器未分配图片 ${image.id} 的上传地址。`);
        return {
          imageId: image.id,
          variantId: manifest.variants[index].id,
          file: fileSnapshot[index],
          total: fileSnapshot[index].size,
          loaded: 0,
          status: 'pending',
          error: '',
          url,
        };
      });
      renderUploadProgress();
      await uploadPending(jobs);
    } catch (error) {
      if (error.name === 'AbortError') return;
      showError(error.message || '网络连接失败，请确认服务仍在运行。');
      uploadPhase.textContent = '无法开始上传';
      if (draft) {
        retryButton.textContent = '重试失败项';
        retryButton.hidden = jobs.length === 0;
      } else {
        resetUploadState();
      }
    }
  });

  retryButton.addEventListener('click', () => {
    if (!draft || cancelling) return;
    showError('');
    const failed = jobs.filter((job) => ['failed', 'cancelled'].includes(job.status));
    if (failed.length) uploadPending(failed);
    else finalizeDraft();
  });
  cancelButton.addEventListener('click', cancelUpload);
  addSlotButton.addEventListener('click', () => {
    if (busy || slotCount >= maxImages) return;
    records.push(null);
    slotCount = records.length;
    showError('');
    renderSlots();
  });
  removeSlotButton.addEventListener('click', () => {
    if (busy || slotCount <= MIN_IMAGES || records[slotCount - 1]) return;
    records.pop();
    slotCount = records.length;
    showError('');
    renderSlots();
  });

  document.addEventListener('dragenter', (event) => {
    if (busy || ![...event.dataTransfer.types].includes('Files')) return;
    dragDepth += 1;
    document.body.classList.add('is-file-dragging');
  });
  document.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) document.body.classList.remove('is-file-dragging');
  });
  document.addEventListener('dragover', (event) => {
    if ([...event.dataTransfer.types].includes('Files')) event.preventDefault();
  });
  document.addEventListener('drop', (event) => {
    event.preventDefault();
    dragDepth = 0;
    document.body.classList.remove('is-file-dragging');
    if (!event.target.closest('.analyze-slot-drop')) addFiles(event.dataTransfer.files);
  });

  window.addEventListener('beforeunload', (event) => {
    if (!busy || allowNavigation) return;
    event.preventDefault();
    event.returnValue = '';
  });
  window.addEventListener('pagehide', (event) => {
    if (!event.persisted) records.forEach(revokeRecord);
  });
  renderSlots();
})();
