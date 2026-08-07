from __future__ import annotations

import hmac
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

from project_store import ProjectStore


FEISHU_PREVIEW_CALLBACK_PATH = "/api/feishu/link-preview"
_ROUTE_KINDS = {
    "compare": "pair",
    "analyze": "multi",
    "gallery": "gallery",
}
_KIND_LABELS = {
    "pair": "图片对比",
    "multi": "图片对比",
    "gallery": "批量图集",
}


def _error(message: str, status: int, *, code: str):
    return jsonify({"error": {"code": code, "message": message}}), status


def _callback_token(payload: dict[str, Any]) -> str | None:
    token = payload.get("token")
    if isinstance(token, str):
        return token
    header = payload.get("header")
    if not isinstance(header, dict):
        return None
    token = header.get("token")
    return token if isinstance(token, str) else None


def _has_valid_token(app: Flask, payload: dict[str, Any]) -> bool:
    expected = app.config.get("FEISHU_VERIFICATION_TOKEN")
    if not isinstance(expected, str) or not expected:
        return True
    received = _callback_token(payload)
    return received is not None and hmac.compare_digest(expected, received)


def _project_from_url(store: ProjectStore, raw_url: Any) -> dict[str, Any] | None:
    if not isinstance(raw_url, str):
        return None
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or not parts[1] or not parts[2]:
        return None
    expected_kind = _ROUTE_KINDS.get(parts[1])
    if expected_kind is None:
        return None

    project = store.load_project(parts[2])
    if project is None or project.get("kind") != expected_kind:
        return None
    if store.project_url(project) != parsed.path:
        return None
    return project


def _preview_title(project: dict[str, Any]) -> str:
    kind = project["kind"]
    mode_label = _KIND_LABELS[kind]
    project_title = project.get("title")
    if not isinstance(project_title, str) or not project_title.strip():
        return mode_label
    project_title = project_title.strip()
    if project_title == mode_label:
        return project_title
    return f"{project_title} · {mode_label}"


def register_feishu_preview_routes(app: Flask, store: ProjectStore) -> None:
    @app.post(FEISHU_PREVIEW_CALLBACK_PATH)
    def feishu_link_preview():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("请求正文必须是 JSON 对象。", 400, code="invalid_json")
        if "encrypt" in payload:
            return _error(
                "暂不支持加密回调，请在飞书后台将 Encrypt Key 留空。",
                400,
                code="encrypted_callback_not_supported",
            )
        if not _has_valid_token(app, payload):
            return _error("回调校验失败。", 403, code="invalid_verification_token")

        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                return _error("缺少 challenge。", 400, code="invalid_challenge")
            return jsonify({"challenge": challenge})

        header = payload.get("header")
        if not isinstance(header, dict) or header.get("event_type") != "url.preview.get":
            return jsonify({})
        event = payload.get("event")
        context = event.get("context") if isinstance(event, dict) else None
        project = _project_from_url(
            store, context.get("url") if isinstance(context, dict) else None
        )
        if project is None:
            return jsonify({})

        inline = {"title": _preview_title(project)}
        image_key = app.config.get("FEISHU_PREVIEW_IMAGE_KEY")
        if isinstance(image_key, str) and image_key.strip():
            inline["image_key"] = image_key.strip()
        return jsonify({"inline": inline})
