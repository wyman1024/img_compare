from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from project_store import ProjectStore


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
TOOL_NAME = "像素对照台"
ROUTE_KINDS = {
    "compare": "pair",
    "analyze": "multi",
    "gallery": "gallery",
}
KIND_LABELS = {
    "pair": "图片对比",
    "multi": "图片对比",
    "gallery": "批量图集",
}


def resolve_preview_target(
    store: ProjectStore, raw_url: Any
) -> dict[str, Any] | None:
    if not isinstance(raw_url, str):
        return None
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None

    parts = parsed.path.split("/")
    if len(parts) != 3 or parts[0] or not PROJECT_ID_RE.fullmatch(parts[2]):
        return None
    expected_kind = ROUTE_KINDS.get(parts[1])
    if expected_kind is None:
        return None

    project = store.load_project(parts[2])
    if project is None or project.get("kind") != expected_kind:
        return None
    if store.project_url(project) != parsed.path:
        return None
    return project


def _preview_title(project: dict[str, Any]) -> str:
    mode_label = KIND_LABELS[project["kind"]]
    project_title = project.get("title")
    if not isinstance(project_title, str) or not project_title.strip():
        return mode_label
    project_title = project_title.strip()
    if project_title == mode_label:
        return project_title
    return f"{project_title} · {mode_label}"


def resolve_preview_inline(
    store: ProjectStore,
    raw_url: Any,
    *,
    image_key: str = "",
) -> dict[str, str] | None:
    project = resolve_preview_target(store, raw_url)
    if project is None:
        return None
    inline = {"title": _preview_title(project)}
    if image_key.strip():
        inline["image_key"] = image_key.strip()
    return inline


def build_preview_response(
    project: dict[str, Any],
    raw_url: str,
    *,
    inline_image_key: str = "",
    card_image_key: str = "",
) -> dict[str, Any]:
    title = _preview_title(project)
    inline = {"title": title}
    if inline_image_key.strip():
        inline["image_key"] = inline_image_key.strip()

    response: dict[str, Any] = {"inline": inline}
    if not card_image_key.strip():
        return response

    response["card"] = {
        "type": "raw",
        "data": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": TOOL_NAME},
            },
            "elements": [
                {
                    "tag": "img",
                    "img_key": card_image_key.strip(),
                    "alt": {
                        "tag": "plain_text",
                        "content": f"{title}预览图",
                    },
                    "mode": "fit_horizontal",
                    "preview": True,
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {
                                "tag": "plain_text",
                                "content": "打开完整对比",
                            },
                            "url": raw_url,
                        }
                    ],
                },
            ],
        },
    }
    return response
