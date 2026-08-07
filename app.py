from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from project_store import (
    DEFAULT_DRAFT_TTL_SECONDS,
    DEFAULT_MAX_PROJECT_BYTES,
    DEFAULT_PREVIEW_MAX_EDGE,
    DEFAULT_PREVIEW_QUALITY,
    MAX_MULTI_IMAGES,
    ProjectStore,
    register_project_routes,
)


DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
UPLOAD_OVERHEAD_BYTES = 2 * 1024 * 1024
DEFAULT_MIN_FREE_DISK_BYTES = 512 * 1024 * 1024


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 必须是整数") from exc

    if value <= 0:
        raise RuntimeError(f"环境变量 {name} 必须大于 0")
    return value


def _validate_storage_config(app: Flask) -> None:
    positive_keys = (
        "MAX_IMAGE_BYTES",
        "MIN_FREE_DISK_BYTES",
        "MAX_PROJECT_BYTES",
        "DRAFT_TTL_SECONDS",
        "PREVIEW_MAX_EDGE",
        "PREVIEW_QUALITY",
    )
    for key in positive_keys:
        try:
            value = int(app.config[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"配置项 {key} 必须是整数") from exc
        if value <= 0:
            raise RuntimeError(f"配置项 {key} 必须大于 0")
        app.config[key] = value
    if app.config["PREVIEW_QUALITY"] > 100:
        raise RuntimeError("配置项 PREVIEW_QUALITY 必须在 1–100 之间")


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    project_dir = Path(__file__).resolve().parent
    default_max_image_bytes = _positive_env_int(
        "MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES
    )

    app = Flask(__name__)
    app.config.from_mapping(
        DATA_DIR=os.environ.get("DATA_DIR", str(project_dir / "data")),
        MAX_IMAGE_BYTES=default_max_image_bytes,
        MIN_FREE_DISK_BYTES=_positive_env_int(
            "MIN_FREE_DISK_BYTES", DEFAULT_MIN_FREE_DISK_BYTES
        ),
        LAN_BASE_URL=os.environ.get("SHARE_BASE_URL", "").strip() or None,
        MAX_PROJECT_BYTES=_positive_env_int(
            "MAX_PROJECT_BYTES", DEFAULT_MAX_PROJECT_BYTES
        ),
        DRAFT_TTL_SECONDS=_positive_env_int(
            "DRAFT_TTL_SECONDS", DEFAULT_DRAFT_TTL_SECONDS
        ),
        PREVIEW_MAX_EDGE=_positive_env_int(
            "PREVIEW_MAX_EDGE", DEFAULT_PREVIEW_MAX_EDGE
        ),
        PREVIEW_QUALITY=_positive_env_int(
            "PREVIEW_QUALITY", DEFAULT_PREVIEW_QUALITY
        ),
    )
    if test_config:
        app.config.update(test_config)
    _validate_storage_config(app)

    if not test_config or "MAX_CONTENT_LENGTH" not in test_config:
        app.config["MAX_CONTENT_LENGTH"] = (
            int(app.config["MAX_IMAGE_BYTES"]) + UPLOAD_OVERHEAD_BYTES
        )

    project_store = ProjectStore(
        Path(app.config["DATA_DIR"]),
        max_image_bytes=int(app.config["MAX_IMAGE_BYTES"]),
        min_free_disk_bytes=int(app.config["MIN_FREE_DISK_BYTES"]),
        max_project_bytes=int(app.config["MAX_PROJECT_BYTES"]),
        draft_ttl_seconds=int(app.config["DRAFT_TTL_SECONDS"]),
        preview_max_edge=int(app.config["PREVIEW_MAX_EDGE"]),
        preview_quality=int(app.config["PREVIEW_QUALITY"]),
    )
    app.config["PROJECTS_DIR"] = str(project_store.projects_dir)
    app.config["DRAFTS_DIR"] = str(project_store.drafts_dir)
    app.extensions["project_store"] = project_store
    register_project_routes(app, project_store)
    app.json.ensure_ascii = False

    def api_error(
        message: str,
        status: int,
        *,
        code: str,
        field: str | None = None,
    ):
        error: dict[str, Any] = {"code": code, "message": message}
        if field is not None:
            error["field"] = field
        return jsonify({"error": error}), status

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        if request.path.startswith("/api/"):
            return api_error(
                "上传内容过大，请缩小图片后重试。",
                413,
                code="request_too_large",
            )
        return "上传内容过大。", 413

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return api_error("请求的内容不存在。", 404, code="not_found")
        return render_template("404.html"), 404

    @app.get("/")
    def index():
        return render_template(
            "analyze_new.html",
            max_image_bytes=int(app.config["MAX_IMAGE_BYTES"]),
            max_project_bytes=int(app.config["MAX_PROJECT_BYTES"]),
            max_images=MAX_MULTI_IMAGES,
        )

    @app.get("/compare/<comparison_id>")
    def compare(comparison_id: str):
        project = project_store.load_project(comparison_id)
        if project is None or project.get("kind") != "pair":
            abort(404)
        try:
            public_project = project_store.public_project(project)
            group = public_project["groups"][0]
            image1 = public_project["images"][group["images"]["A"]]
            image2 = public_project["images"][group["images"]["B"]]
            comparison = {
                "id": comparison_id,
                "created_at": public_project["created_at"],
                "url": public_project["url"],
                "image1": image1,
                "image2": image2,
                "labels": {
                    variant["id"]: variant["label"]
                    for variant in public_project["variants"]
                },
            }
        except (KeyError, IndexError, TypeError):
            app.logger.warning("Invalid v2 pair metadata for %s", comparison_id)
            abort(404)
        share_url = request.url
        request_hostname = urlsplit(request.host_url).hostname or ""
        is_loopback = request_hostname.lower() == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(request_hostname).is_loopback
            except ValueError:
                pass
        lan_base_url = app.config.get("LAN_BASE_URL")
        if is_loopback and lan_base_url:
            share_url = f"{str(lan_base_url).rstrip('/')}{comparison['url']}"
        return render_template(
            "compare.html", comparison=comparison, share_url=share_url
        )

    @app.get("/media/<project_id>/<image_id>")
    def project_media(project_id: str, image_id: str):
        media = project_store.media_file(project_id, image_id)
        if media is None:
            abort(404)
        image_path, mime_type, original_name = media
        return send_file(
            image_path,
            mimetype=mime_type,
            download_name=original_name,
            conditional=True,
            max_age=31536000,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=_positive_env_int("PORT", 8000),
        debug=False,
        threaded=True,
    )
