from __future__ import annotations

import io
import json
import os
import queue
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from project_store import PROJECT_ID_RE, ProjectStore


CACHE_SCHEMA_VERSION = 1
COVER_VERSION = 1
CACHE_NAME = "feishu-preview.json"
COVER_SIZE = (1200, 630)
MAX_COVER_IMAGES = 4


class FeishuImageUploadError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_image_ids(project: dict[str, Any]) -> list[str]:
    variants = project.get("variants")
    groups = project.get("groups")
    images = project.get("images")
    if (
        not isinstance(variants, list)
        or not isinstance(groups, list)
        or not isinstance(images, dict)
    ):
        return []

    variant_ids = [
        variant.get("id")
        for variant in variants
        if isinstance(variant, dict) and isinstance(variant.get("id"), str)
    ]
    selected: list[str] = []
    group_limit = 2 if project.get("kind") == "gallery" else 1
    variant_limit = 2 if project.get("kind") == "gallery" else MAX_COVER_IMAGES
    for group in groups[:group_limit]:
        mapping = group.get("images") if isinstance(group, dict) else None
        if not isinstance(mapping, dict):
            continue
        for variant_id in variant_ids[:variant_limit]:
            image_id = mapping.get(variant_id)
            if (
                isinstance(image_id, str)
                and image_id in images
                and image_id not in selected
            ):
                selected.append(image_id)
                if len(selected) == MAX_COVER_IMAGES:
                    return selected
    return selected


def build_project_cover(store: ProjectStore, project: dict[str, Any]) -> bytes:
    project_id = project.get("id")
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("项目 ID 无效")

    source_paths: list[Path] = []
    for image_id in _project_image_ids(project):
        media = store.media_file(project_id, image_id, preview=True)
        if media is not None:
            source_paths.append(media[0])
    if not source_paths:
        raise ValueError("项目没有可用的预览图片")

    count = len(source_paths)
    columns = 1 if count == 1 else 2
    rows = 1 if count <= 2 else 2
    canvas_width, canvas_height = COVER_SIZE
    outer_margin = 12
    gap = 8
    pane_width = (
        canvas_width - outer_margin * 2 - gap * (columns - 1)
    ) // columns
    pane_height = (canvas_height - outer_margin * 2 - gap * (rows - 1)) // rows
    canvas = Image.new("RGB", COVER_SIZE, (239, 241, 245))

    for index, source_path in enumerate(source_paths):
        row, column = divmod(index, columns)
        left = outer_margin + column * (pane_width + gap)
        top = outer_margin + row * (pane_height + gap)
        pane = Image.new("RGB", (pane_width, pane_height), (255, 255, 255))
        with Image.open(source_path) as source:
            source.seek(0)
            oriented = ImageOps.exif_transpose(source)
            rgba = oriented.convert("RGBA")
            flattened = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            flattened.alpha_composite(rgba)
            contained = ImageOps.contain(
                flattened.convert("RGB"),
                (max(1, pane_width - 16), max(1, pane_height - 16)),
                method=Image.Resampling.LANCZOS,
            )
        paste_left = (pane_width - contained.width) // 2
        paste_top = (pane_height - contained.height) // 2
        pane.paste(contained, (paste_left, paste_top))
        canvas.paste(pane, (left, top))

    output = io.BytesIO()
    canvas.save(output, format="WEBP", quality=86, method=4)
    return output.getvalue()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class FeishuPreviewPublisher:
    def __init__(
        self,
        store: ProjectStore,
        *,
        app_id: str,
        app_secret: str,
        logger: Any,
        upload_image: Callable[[bytes], str] | None = None,
        upload_timeout_seconds: float = 8.0,
        publish_wait_seconds: float = 2.5,
        retry_cooldown_seconds: float = 60.0,
    ) -> None:
        self.store = store
        self.app_id = app_id
        self.app_secret = app_secret
        self.logger = logger
        self._upload_image = upload_image
        self.upload_timeout_seconds = upload_timeout_seconds
        self.publish_wait_seconds = publish_wait_seconds
        self.retry_cooldown_seconds = retry_cooldown_seconds

        self._guard = threading.Lock()
        self._client_guard = threading.Lock()
        self._client: Any = None
        self._queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self._jobs: dict[str, threading.Event] = {}
        self._cooldown_until: dict[str, float] = {}
        self._sequence = 0
        self._worker: threading.Thread | None = None
        self._last_upload_at: str | None = None
        self._last_error_type: str | None = None
        self._last_error_code: int | None = None
        self._last_error_message: str | None = None

    def _cache_path(self, project: dict[str, Any]) -> Path | None:
        project_id = project.get("id")
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            return None
        project_dir = self.store.projects_dir / project_id
        return project_dir / CACHE_NAME if project_dir.is_dir() else None

    def cached_image_key(self, project: dict[str, Any]) -> str | None:
        cache_path = self._cache_path(project)
        cache = _read_json(cache_path) if cache_path is not None else None
        if (
            cache is None
            or cache.get("schema_version") != CACHE_SCHEMA_VERSION
            or cache.get("cover_version") != COVER_VERSION
            or cache.get("app_id") != self.app_id
        ):
            return None
        image_key = cache.get("image_key")
        if (
            not isinstance(image_key, str)
            or not image_key.startswith("img_")
            or len(image_key) > 512
        ):
            return None
        return image_key

    @staticmethod
    def _completed_event() -> threading.Event:
        event = threading.Event()
        event.set()
        return event

    def _start_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run_worker,
            name="feishu-preview-images",
            daemon=True,
        )
        self._worker.start()

    def ensure_async(
        self,
        project: dict[str, Any],
        *,
        priority: int = 10,
    ) -> threading.Event:
        if self.cached_image_key(project) is not None:
            return self._completed_event()
        project_id = project.get("id")
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            return self._completed_event()

        now = time.monotonic()
        with self._guard:
            existing = self._jobs.get(project_id)
            if existing is not None:
                return existing
            if self._cooldown_until.get(project_id, 0) > now:
                return self._completed_event()
            event = threading.Event()
            self._jobs[project_id] = event
            self._sequence += 1
            self._queue.put((priority, self._sequence, project_id))
            self._start_worker_locked()
            return event

    def prepare_after_publish(self, project: dict[str, Any]) -> None:
        event = self.ensure_async(project, priority=0)
        event.wait(self.publish_wait_seconds)

    def _run_worker(self) -> None:
        while True:
            _priority, _sequence, project_id = self._queue.get()
            error: Exception | None = None
            uploaded = False
            try:
                project = self.store.load_project(project_id)
                if project is not None:
                    uploaded = self._ensure_image_key(project) is not None
            except Exception as exc:  # The published project must remain usable.
                error = exc
                self.logger.warning(
                    "飞书图片卡片预热失败（项目 %s）：%s",
                    project_id,
                    exc,
                )
            finally:
                with self._guard:
                    event = self._jobs.pop(project_id, None)
                    if uploaded:
                        self._cooldown_until.pop(project_id, None)
                        self._last_upload_at = _utc_iso()
                        self._last_error_type = None
                        self._last_error_code = None
                        self._last_error_message = None
                    elif error is not None:
                        self._cooldown_until[project_id] = (
                            time.monotonic() + self.retry_cooldown_seconds
                        )
                        self._last_error_type = type(error).__name__
                        self._last_error_code = getattr(error, "code", None)
                        self._last_error_message = str(error)[:300]
                    if event is not None:
                        event.set()
                self._queue.task_done()

    def _ensure_image_key(self, project: dict[str, Any]) -> str | None:
        cached = self.cached_image_key(project)
        if cached is not None:
            return cached
        cache_path = self._cache_path(project)
        if cache_path is None:
            return None
        cover = build_project_cover(self.store, project)
        image_key = self._upload(cover)
        if not image_key.startswith("img_"):
            raise FeishuImageUploadError("飞书返回了无效的 image_key")
        _atomic_json(
            cache_path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "cover_version": COVER_VERSION,
                "app_id": self.app_id,
                "image_key": image_key,
                "created_at": _utc_iso(),
            },
        )
        return image_key

    def _upload(self, cover: bytes) -> str:
        if self._upload_image is not None:
            return self._upload_image(cover)

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        with self._client_guard:
            if self._client is None:
                self._client = (
                    lark.Client.builder()
                    .app_id(self.app_id)
                    .app_secret(self.app_secret)
                    .timeout(self.upload_timeout_seconds)
                    .build()
                )
            client = self._client

        stream = io.BytesIO(cover)
        request = (
            CreateImageRequest.builder()
            .request_body(
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(("comparison-preview.webp", stream, "image/webp"))
                .build()
            )
            .build()
        )
        response = client.im.v1.image.create(request)
        if not response.success():
            log_id = response.get_log_id()
            detail = f"code={response.code}, msg={response.msg}"
            if log_id:
                detail += f", log_id={log_id}"
            raise FeishuImageUploadError(
                f"上传预览图到飞书失败：{detail}",
                code=response.code,
            )
        image_key = getattr(response.data, "image_key", None)
        if not isinstance(image_key, str) or not image_key:
            raise FeishuImageUploadError("飞书图片上传成功，但响应中没有 image_key")
        return image_key

    def status_snapshot(self) -> dict[str, Any]:
        with self._guard:
            return {
                "configured": True,
                "worker_running": (
                    self._worker is not None and self._worker.is_alive()
                ),
                "queued": len(self._jobs),
                "last_uploaded_at": self._last_upload_at,
                "last_error_type": self._last_error_type,
                "last_error_code": self._last_error_code,
                "last_error_message": self._last_error_message,
            }
