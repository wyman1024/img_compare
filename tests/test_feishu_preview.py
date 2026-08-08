from __future__ import annotations

import base64
import io
import threading

import pytest

import feishu_long_connection
from feishu_long_connection import (
    _create_feishu_client,
    build_url_preview_handler,
    start_feishu_long_connection,
)
from feishu_preview import resolve_preview_inline


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _manifest(kind: str, *, title: str = "") -> dict:
    variant_count = 3 if kind == "multi" else 2
    group_count = 2 if kind == "gallery" else 1
    variants = [
        {"id": chr(ord("A") + index), "label": chr(ord("A") + index)}
        for index in range(variant_count)
    ]
    images = []
    groups = []
    for group_index in range(group_count):
        image_map = {}
        for variant in variants:
            image_id = f"image_{group_index}_{variant['id'].lower()}"
            image_map[variant["id"]] = image_id
            images.append(
                {"id": image_id, "original_name": f"{image_id}.png"}
            )
        groups.append(
            {
                "id": f"group_{group_index}",
                "title": f"第 {group_index + 1} 组",
                "images": image_map,
            }
        )
    return {
        "kind": kind,
        "title": title,
        "variants": variants,
        "groups": groups,
        "images": images,
    }


def _publish(client, kind: str, *, title: str = "") -> dict:
    created = client.post("/api/drafts", json=_manifest(kind, title=title))
    assert created.status_code == 201
    draft = created.get_json()
    for image_id, upload_url in draft["uploads"].items():
        uploaded = client.put(
            upload_url,
            data={"file": (io.BytesIO(PNG_1X1), f"{image_id}.png")},
            content_type="multipart/form-data",
            headers={"X-Edit-Token": draft["edit_token"]},
        )
        assert uploaded.status_code == 200
    finalized = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert finalized.status_code == 201
    return finalized.get_json()


@pytest.mark.parametrize(
    ("kind", "title", "expected_title"),
    [
        ("multi", "首页视觉走查", "首页视觉走查 · 图片对比"),
        ("gallery", "角色动作批次", "角色动作批次 · 批量图集"),
    ],
)
def test_resolver_supports_both_current_share_modes(
    app, kind, title, expected_title
):
    published = _publish(app.test_client(), kind, title=title)
    store = app.extensions["project_store"]

    inline = resolve_preview_inline(
        store,
        f"http://jz-tools-server.corp.example.com:8000{published['url']}?x=1#view",
        image_key="  img_test_icon  ",
    )

    assert inline == {"title": expected_title, "image_key": "img_test_icon"}


def test_resolver_keeps_pair_urls_and_falls_back_to_mode_title(app):
    published = _publish(app.test_client(), "pair")

    inline = resolve_preview_inline(
        app.extensions["project_store"],
        f"http://jz-tools-server.corp.example.com:8000{published['url']}",
    )

    assert inline == {"title": "图片对比"}


def test_explicit_share_base_url_replaces_legacy_request_hostname(app):
    app.config.update(
        LAN_BASE_URL="http://jz-tools-server.corp.example.com:8000",
        SHARE_BASE_URL_CONFIGURED=True,
    )
    published = _publish(app.test_client(), "pair")

    page = app.test_client().get(
        published["url"],
        base_url="http://jz-tools-server:8000",
    )

    expected = f'http://jz-tools-server.corp.example.com:8000{published["url"]}'
    assert f'data-share-url="{expected}"' in page.get_data(as_text=True)


@pytest.mark.parametrize(
    "url",
    [
        "https://compare.example.com/",
        "https://compare.example.com/analyze/not-a-project",
        "file:///analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
        "/analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
        "https://user@example.com/analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_resolver_ignores_urls_that_are_not_published_projects(app, url):
    assert resolve_preview_inline(app.extensions["project_store"], url) is None


def test_resolver_rejects_route_kind_mismatch(app):
    published = _publish(app.test_client(), "multi", title="路由校验")
    project_id = published["id"]

    inline = resolve_preview_inline(
        app.extensions["project_store"],
        f"https://compare.example.com/gallery/{project_id}",
    )

    assert inline is None


def test_sdk_handler_returns_official_response_model(app):
    from lark_oapi.event.callback.model.p2_url_preview_get import (
        P2URLPreviewGet,
        P2URLPreviewGetResponse,
    )

    published = _publish(app.test_client(), "multi", title="SDK 回调")
    handler = build_url_preview_handler(
        app.extensions["project_store"],
        image_key="img_sdk_icon",
        response_class=P2URLPreviewGetResponse,
    )
    event = P2URLPreviewGet(
        {
            "event": {
                "context": {
                    "url": f"http://jz-tools-server.corp.example.com:8000{published['url']}"
                }
            }
        }
    )

    response = handler(event)

    assert response.inline.title == "SDK 回调 · 图片对比"
    assert response.inline.image_key == "img_sdk_icon"


def test_long_connection_is_disabled_without_credentials(app):
    assert start_feishu_long_connection(app) is None
    assert "feishu_preview_thread" not in app.extensions


@pytest.mark.parametrize(
    ("app_id", "app_secret"),
    [("cli_test", ""), ("", "secret")],
)
def test_partial_long_connection_credentials_fail_at_startup(
    app_module, tmp_path, app_id, app_secret
):
    with pytest.raises(RuntimeError, match="必须同时配置"):
        app_module.create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(tmp_path / "partial-feishu-config"),
                "MIN_FREE_DISK_BYTES": 1,
                "FEISHU_APP_ID": app_id,
                "FEISHU_APP_SECRET": app_secret,
            }
        )


@pytest.mark.parametrize(
    "share_base_url",
    [
        None,
        "http://jz-tools-server:8000",
        "http://192.168.50.96:8000",
        "jz-tools-server.corp.example.com:8000",
    ],
)
def test_feishu_requires_a_full_share_domain(
    app_module, tmp_path, share_base_url
):
    with pytest.raises(RuntimeError, match="SHARE_BASE_URL"):
        app_module.create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(tmp_path / "invalid-feishu-share-host"),
                "MIN_FREE_DISK_BYTES": 1,
                "FEISHU_APP_ID": "cli_test",
                "FEISHU_APP_SECRET": "secret",
                "LAN_BASE_URL": share_base_url,
            }
        )


def test_long_connection_starts_background_worker_without_network(
    app, monkeypatch: pytest.MonkeyPatch
):
    worker_called = threading.Event()
    app.config.update(
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="secret",
        LAN_BASE_URL="http://jz-tools-server.corp.example.com:8000",
    )
    monkeypatch.setattr(
        feishu_long_connection,
        "_run_feishu_client",
        lambda _app, _store: worker_called.set(),
    )

    thread = start_feishu_long_connection(app)
    thread.join(timeout=2)

    assert worker_called.is_set()
    assert app.extensions["feishu_preview_thread"] is thread


def test_official_sdk_client_can_be_constructed_without_connecting(app):
    app.config.update(
        FEISHU_APP_ID="cli_test",
        FEISHU_APP_SECRET="secret",
        LAN_BASE_URL="http://jz-tools-server.corp.example.com:8000",
    )

    client = _create_feishu_client(app, app.extensions["project_store"])

    assert client is not None
