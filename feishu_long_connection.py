from __future__ import annotations

import ipaddress
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from flask import Flask

from feishu_preview import build_preview_response, resolve_preview_target
from feishu_preview_media import FeishuPreviewPublisher
from project_store import ProjectStore


def validate_feishu_config(app: Flask) -> None:
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_PREVIEW_IMAGE_KEY"):
        value = app.config.get(key, "")
        if not isinstance(value, str):
            raise RuntimeError(f"配置项 {key} 必须是文本")
        app.config[key] = value.strip()

    app_id = app.config["FEISHU_APP_ID"]
    app_secret = app.config["FEISHU_APP_SECRET"]
    if bool(app_id) != bool(app_secret):
        raise RuntimeError("FEISHU_APP_ID 和 FEISHU_APP_SECRET 必须同时配置")
    if not app_id:
        return

    share_base_url = app.config.get("LAN_BASE_URL")
    if not isinstance(share_base_url, str) or not share_base_url:
        raise RuntimeError("启用飞书预览时必须设置 SHARE_BASE_URL 为公司内网完整域名")
    try:
        parsed = urlsplit(share_base_url)
    except ValueError as exc:
        raise RuntimeError("SHARE_BASE_URL 不是有效地址") from exc
    hostname = parsed.hostname or ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        hostname_is_ip = False
    else:
        hostname_is_ip = True
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or hostname_is_ip
        or "." not in hostname
    ):
        raise RuntimeError(
            "启用飞书预览时，SHARE_BASE_URL 必须使用包含顶级域名和二级域名的地址"
        )


def build_url_preview_handler(
    store: ProjectStore,
    *,
    image_key: str,
    response_class: Callable[[dict[str, Any]], Any],
    publisher: FeishuPreviewPublisher | None = None,
    on_result: Callable[[bool, bool], None] | None = None,
):
    def handle_url_preview(data):
        event = getattr(data, "event", None)
        context = getattr(event, "context", None)
        raw_url = getattr(context, "url", None)
        project = resolve_preview_target(store, raw_url)
        card_image_key: str | None = None
        if project is not None and publisher is not None:
            card_image_key = publisher.cached_image_key(project)
            if card_image_key is None:
                publisher.ensure_async(project)
        payload = (
            build_preview_response(
                project,
                raw_url,
                inline_image_key=image_key,
                card_image_key=card_image_key or "",
            )
            if project is not None
            else {}
        )
        if on_result is not None:
            on_result(project is not None, card_image_key is not None)
        return response_class(payload)

    return handle_url_preview


def _create_feishu_client(app: Flask, store: ProjectStore):
    import lark_oapi as lark
    from lark_oapi.event.callback.model.p2_url_preview_get import (
        P2URLPreviewGetResponse,
    )

    def record_result(matched: bool, card_ready: bool) -> None:
        state = app.extensions.setdefault("feishu_preview_state", {})
        state["worker_state"] = "active"
        state["last_callback_at"] = datetime.now(timezone.utc).isoformat()
        state["last_callback_result"] = "matched" if matched else "ignored"
        state["last_callback_card_ready"] = card_ready if matched else None

    publisher = app.extensions.get("feishu_preview_publisher")
    handler = build_url_preview_handler(
        store,
        image_key=app.config["FEISHU_PREVIEW_IMAGE_KEY"],
        response_class=P2URLPreviewGetResponse,
        publisher=(
            publisher if isinstance(publisher, FeishuPreviewPublisher) else None
        ),
        on_result=record_result,
    )
    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_url_preview_get(handler)
        .build()
    )
    return lark.ws.Client(
        app.config["FEISHU_APP_ID"],
        app.config["FEISHU_APP_SECRET"],
        event_handler=event_handler,
        log_level=lark.LogLevel.WARNING,
    )


def _run_feishu_client(app: Flask, store: ProjectStore) -> None:
    retry_delay = 5
    while True:
        try:
            state = app.extensions.setdefault("feishu_preview_state", {})
            state["worker_state"] = "connecting"
            state["last_error_type"] = None
            client = _create_feishu_client(app, store)
            app.extensions["feishu_ws_client"] = client
            client.start()
            state["worker_state"] = "retrying"
            app.logger.warning("飞书长连接已结束，%s 秒后重新连接。", retry_delay)
        except Exception as error:
            state = app.extensions.setdefault("feishu_preview_state", {})
            state["worker_state"] = "retrying"
            state["last_error_type"] = type(error).__name__
            app.logger.exception("飞书长连接异常，%s 秒后重新连接。", retry_delay)
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)


def start_feishu_long_connection(app: Flask) -> threading.Thread | None:
    validate_feishu_config(app)
    if not app.config["FEISHU_APP_ID"]:
        return None
    existing = app.extensions.get("feishu_preview_thread")
    if isinstance(existing, threading.Thread) and existing.is_alive():
        return existing

    store = app.extensions["project_store"]
    app.extensions["feishu_preview_state"] = {
        "worker_state": "starting",
        "last_error_type": None,
        "last_callback_at": None,
        "last_callback_result": None,
        "last_callback_card_ready": None,
    }
    thread = threading.Thread(
        target=_run_feishu_client,
        args=(app, store),
        name="feishu-link-preview",
        daemon=True,
    )
    thread.start()
    app.extensions["feishu_preview_thread"] = thread
    return thread


def feishu_status_snapshot(app: Flask) -> dict[str, Any]:
    thread = app.extensions.get("feishu_preview_thread")
    state = app.extensions.get("feishu_preview_state")
    state = state if isinstance(state, dict) else {}
    publisher = app.extensions.get("feishu_preview_publisher")
    card_preview = (
        publisher.status_snapshot()
        if isinstance(publisher, FeishuPreviewPublisher)
        else {
            "configured": False,
            "worker_running": False,
            "queued": 0,
            "last_uploaded_at": None,
            "last_error_type": None,
            "last_error_code": None,
            "last_error_message": None,
        }
    )
    return {
        "mode": "long_connection",
        "configured": bool(app.config.get("FEISHU_APP_ID")),
        "worker_running": isinstance(thread, threading.Thread) and thread.is_alive(),
        "client_initialized": "feishu_ws_client" in app.extensions,
        "worker_state": state.get("worker_state", "disabled"),
        "last_error_type": state.get("last_error_type"),
        "last_callback_at": state.get("last_callback_at"),
        "last_callback_result": state.get("last_callback_result"),
        "last_callback_card_ready": state.get("last_callback_card_ready"),
        "share_base_url": app.config.get("LAN_BASE_URL"),
        "card_preview": card_preview,
    }
