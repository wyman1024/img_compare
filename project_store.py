from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import threading
import time
import warnings
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file
from PIL import Image, UnidentifiedImageError


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
READ_CHUNK_BYTES = 1024 * 1024
DEFAULT_MAX_PROJECT_BYTES = 512 * 1024 * 1024
DEFAULT_DRAFT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_PREVIEW_MAX_EDGE = 960
DEFAULT_PREVIEW_QUALITY = 82
MAX_MULTI_IMAGES = 8
PUBLISHED_METADATA_NAME = "published-metadata.json"


class ProjectStoreError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int = 400,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.field = field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _detect_image_type(header: bytes) -> tuple[str, str, str] | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png", "PNG"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg", "JPEG"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif", "GIF"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp", "image/webp", "WEBP"
    if header.startswith(b"BM"):
        return "bmp", "image/bmp", "BMP"
    return None


def _safe_display_name(filename: Any, fallback: str) -> str:
    if not isinstance(filename, str):
        return fallback
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = leaf.replace("\x00", "").strip()
    return leaf[:255] or fallback


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as source:
            value = json.load(source)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _safe_child(root: Path, relative_name: Any) -> Path | None:
    if not isinstance(relative_name, str) or not relative_name:
        return None
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


class ProjectStore:
    def __init__(
        self,
        data_dir: Path,
        *,
        max_image_bytes: int,
        min_free_disk_bytes: int,
        max_project_bytes: int = DEFAULT_MAX_PROJECT_BYTES,
        draft_ttl_seconds: int = DEFAULT_DRAFT_TTL_SECONDS,
        preview_max_edge: int = DEFAULT_PREVIEW_MAX_EDGE,
        preview_quality: int = DEFAULT_PREVIEW_QUALITY,
    ) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.projects_dir = self.data_dir / "projects"
        self.drafts_dir = self.data_dir / "drafts"
        self.max_image_bytes = int(max_image_bytes)
        self.min_free_disk_bytes = int(min_free_disk_bytes)
        self.max_project_bytes = int(max_project_bytes)
        self.draft_ttl_seconds = int(draft_ttl_seconds)
        self.preview_max_edge = int(preview_max_edge)
        self.preview_quality = int(preview_quality)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._locks: weakref.WeakValueDictionary[str, threading.RLock] = (
            weakref.WeakValueDictionary()
        )
        self.cleanup_expired_drafts()

    def _lock_for(self, draft_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(draft_id, threading.RLock())

    def _check_storage(self, required_bytes: int = 0) -> None:
        required = self.min_free_disk_bytes + max(0, int(required_bytes))
        try:
            available = shutil.disk_usage(self.data_dir).free
        except OSError as exc:
            raise ProjectStoreError(
                "无法检查服务器存储空间，请稍后重试。",
                code="storage_error",
                status=500,
            ) from exc
        if available < required:
            raise ProjectStoreError(
                "服务器可用空间不足，请清理旧项目后重试。",
                code="insufficient_storage",
                status=507,
            )

    def cleanup_expired_drafts(self) -> None:
        now = _utc_now()
        try:
            candidates = list(self.drafts_dir.iterdir())
        except OSError:
            return
        for candidate in candidates:
            if not candidate.is_dir() or not PROJECT_ID_RE.fullmatch(candidate.name):
                continue
            metadata = _read_json(candidate / "metadata.json")
            expires_at = _parse_datetime(metadata.get("expires_at")) if metadata else None
            expired = expires_at is not None and expires_at <= now
            if expires_at is None:
                try:
                    expired = candidate.stat().st_mtime < time.time() - self.draft_ttl_seconds
                except OSError:
                    continue
            if expired:
                with self._lock_for(candidate.name):
                    shutil.rmtree(candidate, ignore_errors=True)

    def _normalize_manifest(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProjectStoreError(
                "请求正文必须是 JSON 对象。", code="invalid_manifest"
            )

        kind = payload.get("kind")
        if kind not in {"pair", "gallery", "multi"}:
            raise ProjectStoreError(
                "kind 必须是 pair、gallery 或 multi。",
                code="invalid_manifest",
                field="kind",
            )

        title = payload.get("title", "")
        if not isinstance(title, str):
            raise ProjectStoreError(
                "标题必须是文本。", code="invalid_manifest", field="title"
            )
        title = title.strip()
        if len(title) > 80:
            raise ProjectStoreError(
                "标题不能超过 80 个字符。",
                code="invalid_manifest",
                field="title",
            )

        raw_variants = payload.get("variants")
        if not isinstance(raw_variants, list):
            raise ProjectStoreError(
                "variants 必须是数组。",
                code="invalid_manifest",
                field="variants",
            )
        variants: list[dict[str, str]] = []
        for index, raw in enumerate(raw_variants):
            field = f"variants.{index}"
            if not isinstance(raw, dict):
                raise ProjectStoreError(
                    "每个版本必须是对象。", code="invalid_manifest", field=field
                )
            variant_id = raw.get("id")
            label = raw.get("label", variant_id)
            if not isinstance(variant_id, str) or not MANIFEST_ID_RE.fullmatch(variant_id):
                raise ProjectStoreError(
                    "版本 ID 格式无效。", code="invalid_manifest", field=f"{field}.id"
                )
            if not isinstance(label, str) or not label.strip() or len(label.strip()) > 24:
                raise ProjectStoreError(
                    "版本标签必须为 1–24 个字符。",
                    code="invalid_manifest",
                    field=f"{field}.label",
                )
            variants.append({"id": variant_id, "label": label.strip()})

        variant_ids = [item["id"] for item in variants]
        expected_ids = (
            ["A", "B"]
            if kind in {"pair", "gallery"}
            else [
                chr(ord("A") + index)
                for index in range(min(len(variants), MAX_MULTI_IMAGES))
            ]
        )
        valid_count = (
            len(variants) == 2
            if kind in {"pair", "gallery"}
            else 2 <= len(variants) <= MAX_MULTI_IMAGES
        )
        if not valid_count or variant_ids != expected_ids:
            message = (
                "pair 和 gallery 必须按顺序提供 A、B 两个版本。"
                if kind in {"pair", "gallery"}
                else (
                    "multi 必须按顺序提供 "
                    f"A–H 中的 2–{MAX_MULTI_IMAGES} 个版本。"
                )
            )
            raise ProjectStoreError(
                message, code="invalid_manifest", field="variants"
            )

        raw_groups = payload.get("groups")
        if not isinstance(raw_groups, list):
            raise ProjectStoreError(
                "groups 必须是数组。", code="invalid_manifest", field="groups"
            )
        if kind in {"pair", "multi"} and len(raw_groups) != 1:
            raise ProjectStoreError(
                "pair 和 multi 必须且只能包含一组图片。",
                code="invalid_manifest",
                field="groups",
            )
        if kind == "gallery" and not 1 <= len(raw_groups) <= 30:
            raise ProjectStoreError(
                "图集必须包含 1–30 组图片。",
                code="invalid_manifest",
                field="groups",
            )

        groups: list[dict[str, Any]] = []
        group_ids: set[str] = set()
        referenced_image_ids: list[str] = []
        for index, raw in enumerate(raw_groups):
            field = f"groups.{index}"
            if not isinstance(raw, dict):
                raise ProjectStoreError(
                    "每个图片组必须是对象。", code="invalid_manifest", field=field
                )
            group_id = raw.get("id")
            group_title = raw.get("title", "")
            image_map = raw.get("images")
            if not isinstance(group_id, str) or not MANIFEST_ID_RE.fullmatch(group_id):
                raise ProjectStoreError(
                    "图片组 ID 格式无效。",
                    code="invalid_manifest",
                    field=f"{field}.id",
                )
            if group_id in group_ids:
                raise ProjectStoreError(
                    "图片组 ID 不能重复。",
                    code="invalid_manifest",
                    field=f"{field}.id",
                )
            group_ids.add(group_id)
            if not isinstance(group_title, str) or len(group_title.strip()) > 80:
                raise ProjectStoreError(
                    "图片组标题不能超过 80 个字符。",
                    code="invalid_manifest",
                    field=f"{field}.title",
                )
            if not isinstance(image_map, dict) or set(image_map) != set(variant_ids):
                raise ProjectStoreError(
                    "每组图片必须为每个版本提供一张图片。",
                    code="invalid_manifest",
                    field=f"{field}.images",
                )
            normalized_map: dict[str, str] = {}
            for variant_id in variant_ids:
                image_id = image_map.get(variant_id)
                if not isinstance(image_id, str) or not MANIFEST_ID_RE.fullmatch(image_id):
                    raise ProjectStoreError(
                        "图片 ID 格式无效。",
                        code="invalid_manifest",
                        field=f"{field}.images.{variant_id}",
                    )
                normalized_map[variant_id] = image_id
                referenced_image_ids.append(image_id)
            groups.append(
                {"id": group_id, "title": group_title.strip(), "images": normalized_map}
            )

        raw_images = payload.get("images")
        if not isinstance(raw_images, list):
            raise ProjectStoreError(
                "images 必须是数组。", code="invalid_manifest", field="images"
            )
        if len(raw_images) > 60:
            raise ProjectStoreError(
                "单个项目最多包含 60 张图片。",
                code="invalid_manifest",
                field="images",
            )
        images: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_images):
            field = f"images.{index}"
            if not isinstance(raw, dict):
                raise ProjectStoreError(
                    "每张图片必须是对象。", code="invalid_manifest", field=field
                )
            image_id = raw.get("id")
            if not isinstance(image_id, str) or not MANIFEST_ID_RE.fullmatch(image_id):
                raise ProjectStoreError(
                    "图片 ID 格式无效。", code="invalid_manifest", field=f"{field}.id"
                )
            if image_id in images:
                raise ProjectStoreError(
                    "图片 ID 不能重复。", code="invalid_manifest", field=f"{field}.id"
                )
            original_name = raw.get("original_name", raw.get("filename"))
            if not isinstance(original_name, str) or not original_name.strip():
                raise ProjectStoreError(
                    "图片原始文件名不能为空。",
                    code="invalid_manifest",
                    field=f"{field}.original_name",
                )
            image_title = raw.get("title", "")
            if not isinstance(image_title, str):
                raise ProjectStoreError(
                    "图片标题必须是文本。",
                    code="invalid_manifest",
                    field=f"{field}.title",
                )
            image_title = image_title.strip()
            if len(image_title) > 80:
                raise ProjectStoreError(
                    "图片标题不能超过 80 个字符。",
                    code="invalid_manifest",
                    field=f"{field}.title",
                )
            images[image_id] = {
                "id": image_id,
                "original_name": _safe_display_name(original_name, image_id),
                "title": image_title,
                "status": "pending",
            }

        if len(referenced_image_ids) != len(set(referenced_image_ids)):
            raise ProjectStoreError(
                "每个图片槽位必须使用不同的图片 ID。",
                code="invalid_manifest",
                field="groups",
            )
        if set(referenced_image_ids) != set(images):
            raise ProjectStoreError(
                "images 必须与所有图片组引用的 ID 完全一致。",
                code="invalid_manifest",
                field="images",
            )

        return {
            "schema_version": 2,
            "kind": kind,
            "title": title,
            "variants": variants,
            "groups": groups,
            "images": images,
        }

    def create_draft(self, payload: Any) -> tuple[dict[str, Any], str]:
        # Long-running LAN servers may not restart for weeks. Sweep abandoned
        # drafts whenever new work arrives so the 24-hour TTL is enforced even
        # without a restart or a direct request for the expired draft.
        self.cleanup_expired_drafts()
        manifest = self._normalize_manifest(payload)
        self._check_storage()
        now = _utc_now()
        token = secrets.token_urlsafe(32)
        for _ in range(10):
            draft_id = secrets.token_urlsafe(18)
            draft_dir = self.drafts_dir / draft_id
            try:
                draft_dir.mkdir()
            except FileExistsError:
                continue
            break
        else:
            raise ProjectStoreError(
                "无法分配唯一的草稿 ID。", code="storage_error", status=500
            )

        metadata = {
            **manifest,
            "id": draft_id,
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=self.draft_ttl_seconds)),
            "edit_token_hash": _token_hash(token),
        }
        try:
            (draft_dir / "images").mkdir()
            (draft_dir / "previews").mkdir()
            _atomic_json(draft_dir / "metadata.json", metadata)
        except OSError as exc:
            shutil.rmtree(draft_dir, ignore_errors=True)
            raise ProjectStoreError(
                "创建草稿失败，请稍后重试。", code="storage_error", status=500
            ) from exc
        return metadata, token

    def _load_draft(self, draft_id: str, *, remove_expired: bool = True) -> dict[str, Any] | None:
        if not PROJECT_ID_RE.fullmatch(draft_id):
            return None
        metadata = _read_json(self.drafts_dir / draft_id / "metadata.json")
        if metadata is None or metadata.get("id") != draft_id:
            return None
        expires_at = _parse_datetime(metadata.get("expires_at"))
        if expires_at is None or expires_at <= _utc_now():
            if remove_expired:
                shutil.rmtree(self.drafts_dir / draft_id, ignore_errors=True)
            raise ProjectStoreError(
                "草稿已过期，请重新创建。", code="draft_expired", status=410
            )
        return metadata

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        if not PROJECT_ID_RE.fullmatch(draft_id):
            return None
        with self._lock_for(draft_id):
            return self._load_draft(draft_id)

    @staticmethod
    def _require_token(metadata: dict[str, Any], token: str | None) -> None:
        if not token:
            raise ProjectStoreError(
                "缺少草稿编辑令牌。", code="missing_edit_token", status=401
            )
        expected = metadata.get("edit_token_hash")
        if not isinstance(expected, str) or not hmac.compare_digest(expected, _token_hash(token)):
            raise ProjectStoreError(
                "草稿编辑令牌无效。", code="invalid_edit_token", status=403
            )

    def _receive_original(self, upload: Any, temporary_path: Path, image_id: str) -> tuple[int, str, str, str, int, int]:
        if upload is None or not getattr(upload, "filename", None):
            raise ProjectStoreError(
                "请求中必须包含 file 图片。",
                code="missing_file",
                field="file",
            )
        if upload.content_length is not None and upload.content_length > self.max_image_bytes:
            raise ProjectStoreError(
                f"图片不能超过 {self.max_image_bytes // (1024 * 1024)} MB。",
                code="file_too_large",
                field="file",
            )
        total_bytes = 0
        header = bytearray()
        try:
            with temporary_path.open("xb") as output:
                source: BinaryIO = upload.stream
                while chunk := source.read(READ_CHUNK_BYTES):
                    total_bytes += len(chunk)
                    if total_bytes > self.max_image_bytes:
                        raise ProjectStoreError(
                            f"图片不能超过 {self.max_image_bytes // (1024 * 1024)} MB。",
                            code="file_too_large",
                            field="file",
                        )
                    if len(header) < 16:
                        header.extend(chunk[: 16 - len(header)])
                    output.write(chunk)
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise
        if total_bytes == 0:
            raise ProjectStoreError(
                "图片文件不能为空。", code="empty_file", field="file"
            )
        detected = _detect_image_type(bytes(header))
        if detected is None:
            raise ProjectStoreError(
                "仅支持 PNG、JPEG、WebP、GIF 或 BMP 图片。",
                code="unsupported_image_type",
                field="file",
            )
        extension, mime_type, expected_format = detected
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(temporary_path) as image:
                    if image.format != expected_format or min(image.size) <= 0:
                        raise ProjectStoreError(
                            "图片内容与文件类型不匹配。",
                            code="invalid_image",
                            field="file",
                        )
                    width, height = image.size
                    image.verify()
        except ProjectStoreError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ProjectStoreError(
                "图片文件已损坏或无法读取。", code="invalid_image", field="file"
            ) from exc
        return total_bytes, extension, mime_type, expected_format, width, height

    def _create_preview(self, original: Path, preview: Path) -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(original) as image:
                    image.seek(0)
                    image.thumbnail(
                        (self.preview_max_edge, self.preview_max_edge),
                        Image.Resampling.LANCZOS,
                    )
                    has_alpha = image.mode in {"RGBA", "LA"} or (
                        image.mode == "P" and "transparency" in image.info
                    )
                    converted = image.convert("RGBA" if has_alpha else "RGB")
                    converted.save(
                        preview,
                        format="WEBP",
                        quality=self.preview_quality,
                        method=4,
                    )
        except (
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise ProjectStoreError(
                "生成图片预览失败，请更换图片后重试。",
                code="preview_generation_failed",
                status=500,
                field="file",
            ) from exc

    def upload_image(self, draft_id: str, image_id: str, token: str | None, upload: Any) -> dict[str, Any]:
        if not PROJECT_ID_RE.fullmatch(draft_id) or not MANIFEST_ID_RE.fullmatch(image_id):
            raise ProjectStoreError("图片槽位不存在。", code="not_found", status=404)
        with self._lock_for(draft_id):
            metadata = self._load_draft(draft_id)
            if metadata is None:
                raise ProjectStoreError("草稿不存在。", code="not_found", status=404)
            self._require_token(metadata, token)
            images = metadata.get("images")
            if not isinstance(images, dict) or image_id not in images:
                raise ProjectStoreError("图片槽位不存在。", code="not_found", status=404)
            self._check_storage(self.max_image_bytes * 2)

            draft_dir = self.drafts_dir / draft_id
            nonce = secrets.token_hex(8)
            raw_temporary = draft_dir / f".{image_id}.{nonce}.upload"
            preview_temporary = draft_dir / f".{image_id}.{nonce}.preview"
            new_original: Path | None = None
            new_preview: Path | None = None
            committed = False
            try:
                size, extension, mime_type, image_format, width, height = self._receive_original(
                    upload, raw_temporary, image_id
                )
                current_total = sum(
                    int(item.get("size", 0))
                    for key, item in images.items()
                    if key != image_id and isinstance(item, dict)
                )
                if current_total + size > self.max_project_bytes:
                    raise ProjectStoreError(
                        f"单个项目图片总量不能超过 {self.max_project_bytes // (1024 * 1024)} MB。",
                        code="project_too_large",
                        field="file",
                    )
                self._create_preview(raw_temporary, preview_temporary)
                new_original = draft_dir / "images" / f"{image_id}-{nonce}.{extension}"
                new_preview = draft_dir / "previews" / f"{image_id}-{nonce}.webp"
                os.replace(raw_temporary, new_original)
                os.replace(preview_temporary, new_preview)

                previous_image = images[image_id]
                old_original = _safe_child(draft_dir, previous_image.get("stored_name"))
                old_preview = _safe_child(draft_dir, previous_image.get("preview_name"))
                uploaded_name = _safe_display_name(upload.filename, images[image_id]["original_name"])
                updated = {
                    "id": image_id,
                    "original_name": uploaded_name,
                    "title": previous_image["title"],
                    "status": "uploaded",
                    "stored_name": new_original.relative_to(draft_dir).as_posix(),
                    "preview_name": new_preview.relative_to(draft_dir).as_posix(),
                    "format": image_format,
                    "mime_type": mime_type,
                    "size": size,
                    "width": width,
                    "height": height,
                }
                images[image_id] = updated
                try:
                    _atomic_json(draft_dir / "metadata.json", metadata)
                except OSError:
                    images[image_id] = previous_image
                    raise
                committed = True
                for obsolete in (old_original, old_preview):
                    if obsolete and obsolete not in {new_original, new_preview}:
                        try:
                            obsolete.unlink()
                        except OSError:
                            pass
                return updated
            except ProjectStoreError:
                raise
            except OSError as exc:
                raise ProjectStoreError(
                    "保存图片失败，请稍后重试。", code="storage_error", status=500
                ) from exc
            finally:
                for temporary in (raw_temporary, preview_temporary):
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
                # If metadata was not committed, versioned files are unreferenced and
                # can be safely removed. A committed entry points at both of them.
                for candidate in (new_original, new_preview):
                    if candidate is not None and not committed:
                        try:
                            candidate.unlink()
                        except OSError:
                            pass

    def cancel_draft(self, draft_id: str, token: str | None) -> None:
        if not PROJECT_ID_RE.fullmatch(draft_id):
            raise ProjectStoreError("草稿不存在。", code="not_found", status=404)
        with self._lock_for(draft_id):
            metadata = self._load_draft(draft_id)
            if metadata is None:
                raise ProjectStoreError("草稿不存在。", code="not_found", status=404)
            self._require_token(metadata, token)
            try:
                shutil.rmtree(self.drafts_dir / draft_id)
            except OSError as exc:
                raise ProjectStoreError(
                    "取消草稿失败，请稍后重试。", code="storage_error", status=500
                ) from exc

    def finalize(self, draft_id: str, token: str | None) -> tuple[dict[str, Any], bool]:
        if not PROJECT_ID_RE.fullmatch(draft_id):
            raise ProjectStoreError("草稿不存在。", code="not_found", status=404)
        with self._lock_for(draft_id):
            existing = self.load_project(draft_id)
            if existing is not None:
                return existing, False
            metadata = self._load_draft(draft_id)
            if metadata is None:
                raise ProjectStoreError("草稿不存在。", code="not_found", status=404)
            self._require_token(metadata, token)
            draft_dir = self.drafts_dir / draft_id
            images = metadata.get("images")
            if not isinstance(images, dict):
                raise ProjectStoreError("草稿数据无效。", code="invalid_draft", status=500)
            missing = [image_id for image_id, image in images.items() if image.get("status") != "uploaded"]
            if missing:
                raise ProjectStoreError(
                    "仍有图片尚未上传。",
                    code="incomplete_draft",
                    status=409,
                    field=missing[0],
                )
            for image_id, image in images.items():
                original = _safe_child(draft_dir, image.get("stored_name"))
                preview = _safe_child(draft_dir, image.get("preview_name"))
                if original is None or preview is None or not original.is_file() or not preview.is_file():
                    raise ProjectStoreError(
                        "草稿中的图片文件不完整。",
                        code="invalid_draft",
                        status=500,
                        field=image_id,
                    )

            self._check_storage()
            published_metadata = dict(metadata)
            published_metadata["published_at"] = _iso(_utc_now())
            published_metadata.pop("edit_token_hash", None)
            published_metadata.pop("expires_at", None)
            # Keep metadata.json as a fully recoverable draft until the directory
            # move becomes the publication boundary.  A crash before that move
            # leaves the token and expiry intact; a crash after it leaves this
            # tokenless sidecar in projects/, where load_project() completes the
            # metadata swap before returning the public project.
            published_metadata_path = draft_dir / PUBLISHED_METADATA_NAME
            try:
                _atomic_json(published_metadata_path, published_metadata)
            except OSError as exc:
                raise ProjectStoreError(
                    "准备发布项目失败，请稍后重试。",
                    code="storage_error",
                    status=500,
                ) from exc
            project_dir = self.projects_dir / draft_id
            try:
                os.replace(draft_dir, project_dir)
            except OSError as exc:
                raise ProjectStoreError(
                    "发布项目失败，请稍后重试。", code="storage_error", status=500
                ) from exc
            try:
                os.replace(
                    project_dir / PUBLISHED_METADATA_NAME,
                    project_dir / "metadata.json",
                )
            except OSError:
                # The directory move is already the durable publication boundary.
                # load_project() uses the tokenless sidecar and retries this swap.
                pass
            return published_metadata, True

    def load_project(self, project_id: str) -> dict[str, Any] | None:
        if not PROJECT_ID_RE.fullmatch(project_id):
            return None
        project_dir = self.projects_dir / project_id
        published_metadata_path = project_dir / PUBLISHED_METADATA_NAME
        recovered = _read_json(published_metadata_path)
        if (
            recovered is not None
            and recovered.get("id") == project_id
            and recovered.get("schema_version") == 2
            and recovered.get("kind") in {"pair", "gallery", "multi"}
            and "edit_token_hash" not in recovered
        ):
            metadata = recovered
            try:
                os.replace(published_metadata_path, project_dir / "metadata.json")
            except OSError:
                pass
        else:
            metadata = _read_json(project_dir / "metadata.json")
        if metadata is None or metadata.get("id") != project_id:
            return None
        if metadata.get("schema_version") != 2 or metadata.get("kind") not in {"pair", "gallery", "multi"}:
            return None
        return metadata

    @staticmethod
    def project_url(project: dict[str, Any]) -> str:
        kind = project["kind"]
        prefix = {"pair": "compare", "gallery": "gallery", "multi": "analyze"}[kind]
        return f"/{prefix}/{project['id']}"

    def public_project(self, project: dict[str, Any]) -> dict[str, Any]:
        project_id = project["id"]
        public_images: dict[str, dict[str, Any]] = {}
        for image_id, image in project["images"].items():
            public_images[image_id] = {
                key: image[key]
                for key in (
                    "id",
                    "original_name",
                    "title",
                    "format",
                    "mime_type",
                    "size",
                    "width",
                    "height",
                )
                if key in image
            }
            public_images[image_id]["url"] = f"/media/{project_id}/{image_id}"
            public_images[image_id]["preview_url"] = f"/preview/{project_id}/{image_id}"
        return {
            "schema_version": 2,
            "id": project_id,
            "kind": project["kind"],
            "title": project.get("title", ""),
            "created_at": project.get("created_at"),
            "published_at": project.get("published_at"),
            "url": self.project_url(project),
            "variants": project["variants"],
            "groups": project["groups"],
            "images": public_images,
        }

    def public_draft(self, metadata: dict[str, Any]) -> dict[str, Any]:
        images: dict[str, dict[str, Any]] = {}
        for image_id, image in metadata["images"].items():
            images[image_id] = {
                key: image[key]
                for key in (
                    "id",
                    "original_name",
                    "title",
                    "status",
                    "format",
                    "mime_type",
                    "size",
                    "width",
                    "height",
                )
                if key in image
            }
        return {
            "schema_version": 2,
            "id": metadata["id"],
            "kind": metadata["kind"],
            "title": metadata.get("title", ""),
            "created_at": metadata["created_at"],
            "expires_at": metadata["expires_at"],
            "variants": metadata["variants"],
            "groups": metadata["groups"],
            "images": images,
        }

    def media_file(self, project_id: str, image_id: str, *, preview: bool = False) -> tuple[Path, str, str] | None:
        if not MANIFEST_ID_RE.fullmatch(image_id):
            return None
        project = self.load_project(project_id)
        if project is None:
            return None
        image = project.get("images", {}).get(image_id)
        if not isinstance(image, dict):
            return None
        key = "preview_name" if preview else "stored_name"
        path = _safe_child(self.projects_dir / project_id, image.get(key))
        if path is None or not path.is_file():
            return None
        mime = "image/webp" if preview else image.get("mime_type")
        if not isinstance(mime, str):
            return None
        download_name = f"preview-{image_id}.webp" if preview else image.get("original_name", image_id)
        return path, mime, download_name


def _api_error(error: ProjectStoreError):
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.field is not None:
        body["field"] = error.field
    return jsonify({"error": body}), error.status


def _share_url(app: Flask, relative_url: str) -> str:
    share_url = request.url
    hostname = urlsplit(request.host_url).hostname or ""
    is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            pass
    lan_base_url = app.config.get("LAN_BASE_URL")
    if is_loopback and lan_base_url:
        share_url = f"{str(lan_base_url).rstrip('/')}{relative_url}"
    return share_url


def register_project_routes(app: Flask, store: ProjectStore) -> None:
    @app.get("/gallery/new")
    def gallery_new():
        return render_template(
            "gallery_new.html",
            max_image_bytes=store.max_image_bytes,
            max_project_bytes=store.max_project_bytes,
            max_groups=30,
        )

    @app.get("/analyze/new")
    def analyze_new():
        return redirect("/")

    @app.get("/gallery/<project_id>")
    def gallery_view(project_id: str):
        project = store.load_project(project_id)
        if project is None or project.get("kind") != "gallery":
            abort(404)
        public = store.public_project(project)
        return render_template(
            "gallery.html",
            project=public,
            share_url=_share_url(app, public["url"]),
        )

    @app.get("/analyze/<project_id>")
    def analyze_view(project_id: str):
        project = store.load_project(project_id)
        if project is None or project.get("kind") != "multi":
            abort(404)
        public = store.public_project(project)
        return render_template(
            "analyze.html",
            project=public,
            share_url=_share_url(app, public["url"]),
        )

    @app.post("/api/drafts")
    def create_project_draft():
        try:
            metadata, token = store.create_draft(request.get_json(silent=True))
        except ProjectStoreError as error:
            return _api_error(error)
        draft_id = metadata["id"]
        uploads = {
            image_id: f"/api/drafts/{draft_id}/images/{image_id}"
            for image_id in metadata["images"]
        }
        return jsonify(
            {
                "draft_id": draft_id,
                "edit_token": token,
                "expires_at": metadata["expires_at"],
                "uploads": uploads,
            }
        ), 201

    @app.get("/api/drafts/<draft_id>")
    def get_project_draft(draft_id: str):
        try:
            metadata = store.get_draft(draft_id)
        except ProjectStoreError as error:
            return _api_error(error)
        if metadata is None:
            return _api_error(
                ProjectStoreError("草稿不存在。", code="not_found", status=404)
            )
        uploads = {
            image_id: f"/api/drafts/{draft_id}/images/{image_id}"
            for image_id in metadata["images"]
        }
        return jsonify({"draft": store.public_draft(metadata), "uploads": uploads})

    @app.put("/api/drafts/<draft_id>/images/<image_id>")
    def upload_project_image(draft_id: str, image_id: str):
        try:
            image = store.upload_image(
                draft_id,
                image_id,
                request.headers.get("X-Edit-Token"),
                request.files.get("file"),
            )
        except ProjectStoreError as error:
            return _api_error(error)
        public_image = {
            key: image[key]
            for key in (
                "id",
                "status",
                "original_name",
                "title",
                "format",
                "mime_type",
                "size",
                "width",
                "height",
            )
        }
        return jsonify({"image": public_image})

    @app.post("/api/drafts/<draft_id>/finalize")
    def finalize_project_draft(draft_id: str):
        try:
            project, created = store.finalize(
                draft_id, request.headers.get("X-Edit-Token")
            )
        except ProjectStoreError as error:
            return _api_error(error)
        public = store.public_project(project)
        return jsonify(
            {
                "id": public["id"],
                "kind": public["kind"],
                "url": public["url"],
                "project": public,
            }
        ), 201 if created else 200

    @app.delete("/api/drafts/<draft_id>")
    def delete_project_draft(draft_id: str):
        try:
            store.cancel_draft(draft_id, request.headers.get("X-Edit-Token"))
        except ProjectStoreError as error:
            return _api_error(error)
        return "", 204

    @app.get("/preview/<project_id>/<image_id>")
    def project_preview(project_id: str, image_id: str):
        media = store.media_file(project_id, image_id, preview=True)
        if media is None:
            abort(404)
        path, mime, name = media
        return send_file(
            path,
            mimetype=mime,
            download_name=name,
            conditional=True,
            max_age=31536000,
        )
