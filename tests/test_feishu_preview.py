from __future__ import annotations

import base64
import io
import json
import threading

import pytest
from PIL import Image

import feishu_long_connection
from feishu_long_connection import (
    _create_feishu_client,
    build_url_preview_handler,
    start_feishu_long_connection,
)
from feishu_preview import build_preview_response, resolve_preview_inline
from feishu_preview_media import FeishuPreviewPublisher


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
        ("multi", "首页视觉走查", "首页视觉走查 · 图片对比 · 像素对照台"),
        ("gallery", "角色动作批次", "角色动作批次 · 批量图集 · 像素对照台"),
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

    assert inline == {"title": "图片对比 · 像素对照台"}


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

    assert response.inline.title == "SDK 回调 · 图片对比 · 像素对照台"
    assert response.inline.image_key == "img_sdk_icon"


@pytest.mark.parametrize("kind", ["multi", "gallery"])
def test_publisher_builds_and_caches_project_cover_for_both_modes(app, kind):
    published = _publish(app.test_client(), kind, title="封面测试")
    store = app.extensions["project_store"]
    project = store.load_project(published["id"])
    uploaded_covers: list[bytes] = []

    def upload_cover(cover: bytes) -> str:
        uploaded_covers.append(cover)
        with Image.open(io.BytesIO(cover)) as image:
            assert image.format == "WEBP"
            assert image.size == (1200, 630)
        return "img_v3_test_project_cover"

    publisher = FeishuPreviewPublisher(
        store,
        app_id="cli_cover_test",
        app_secret="secret",
        logger=app.logger,
        upload_image=upload_cover,
        publish_wait_seconds=1,
    )

    publisher.prepare_after_publish(project)
    publisher.prepare_after_publish(project)

    assert publisher.cached_image_key(project) == "img_v3_test_project_cover"
    assert len(uploaded_covers) == 1
    cache_path = store.projects_dir / published["id"] / "feishu-preview.json"
    cache = json.loads(cache_path.read_text("utf-8"))
    assert cache["app_id"] == "cli_cover_test"
    assert cache["image_key"] == "img_v3_test_project_cover"
    assert "secret" not in cache_path.read_text("utf-8")


def test_sdk_handler_returns_raw_image_card_when_cover_is_cached(app):
    from lark_oapi.event.callback.model.p2_url_preview_get import (
        P2URLPreviewGet,
        P2URLPreviewGetResponse,
    )

    published = _publish(app.test_client(), "multi", title="首页走查")
    store = app.extensions["project_store"]
    project = store.load_project(published["id"])
    publisher = FeishuPreviewPublisher(
        store,
        app_id="cli_card_test",
        app_secret="secret",
        logger=app.logger,
        upload_image=lambda _cover: "img_v3_card_cover",
        publish_wait_seconds=1,
    )
    publisher.prepare_after_publish(project)
    raw_url = f"http://img-compare.sg.cn:8000{published['url']}"
    handler = build_url_preview_handler(
        store,
        image_key="img_inline_icon",
        response_class=P2URLPreviewGetResponse,
        publisher=publisher,
    )
    event = P2URLPreviewGet({"event": {"context": {"url": raw_url}}})

    response = handler(event)

    assert response.inline.title == "首页走查 · 图片对比 · 像素对照台"
    assert response.inline.image_key == "img_inline_icon"
    assert response.card.type == "raw"
    assert "header" not in response.card.data
    assert response.card.data["elements"][0]["img_key"] == "img_v3_card_cover"
    assert response.card.data["elements"][0]["mode"] == "fit_horizontal"
    assert response.card.data["elements"][1]["actions"][0]["url"] == raw_url


@pytest.mark.parametrize(
    ("kind", "title", "expected_inline"),
    [
        ("pair", "", "图片对比 · 像素对照台"),
        ("multi", "首页走查", "首页走查 · 图片对比 · 像素对照台"),
        ("gallery", "角色批次", "角色批次 · 批量图集 · 像素对照台"),
    ],
)
def test_card_brand_moves_to_inline_title_for_every_share_mode(
    kind, title, expected_inline
):
    response = build_preview_response(
        {"kind": kind, "title": title},
        "http://img-compare.sg.cn:8000/project/test",
        card_image_key="img_v3_card_cover",
    )

    assert response["inline"]["title"] == expected_inline
    assert "header" not in response["card"]["data"]


def test_sdk_handler_schedules_missing_cover_without_blocking_response(app):
    from lark_oapi.event.callback.model.p2_url_preview_get import (
        P2URLPreviewGet,
        P2URLPreviewGetResponse,
    )

    class PendingPublisher:
        def __init__(self):
            self.scheduled = 0

        def cached_image_key(self, _project):
            return None

        def ensure_async(self, _project):
            self.scheduled += 1

    published = _publish(app.test_client(), "gallery", title="批次预览")
    raw_url = f"http://img-compare.sg.cn:8000{published['url']}"
    publisher = PendingPublisher()
    handler = build_url_preview_handler(
        app.extensions["project_store"],
        image_key="",
        response_class=P2URLPreviewGetResponse,
        publisher=publisher,
    )

    response = handler(P2URLPreviewGet({"event": {"context": {"url": raw_url}}}))

    assert response.inline.title == "批次预览 · 批量图集 · 像素对照台"
    assert response.card is None
    assert publisher.scheduled == 1


def test_concurrent_cover_requests_are_deduplicated(app):
    published = _publish(app.test_client(), "multi")
    store = app.extensions["project_store"]
    project = store.load_project(published["id"])
    upload_started = threading.Event()
    release_upload = threading.Event()
    upload_count = 0

    def upload_cover(_cover: bytes) -> str:
        nonlocal upload_count
        upload_count += 1
        upload_started.set()
        assert release_upload.wait(2)
        return "img_v3_deduplicated_cover"

    publisher = FeishuPreviewPublisher(
        store,
        app_id="cli_deduplicate",
        app_secret="secret",
        logger=app.logger,
        upload_image=upload_cover,
    )

    events = [publisher.ensure_async(project) for _ in range(5)]
    assert upload_started.wait(2)
    assert all(event is events[0] for event in events)
    release_upload.set()
    assert events[0].wait(2)
    assert upload_count == 1


def test_image_key_cache_is_scoped_to_feishu_app(app):
    published = _publish(app.test_client(), "multi")
    store = app.extensions["project_store"]
    project = store.load_project(published["id"])
    first = FeishuPreviewPublisher(
        store,
        app_id="cli_first",
        app_secret="secret",
        logger=app.logger,
        upload_image=lambda _cover: "img_v3_first_app",
        publish_wait_seconds=1,
    )
    second = FeishuPreviewPublisher(
        store,
        app_id="cli_second",
        app_secret="secret",
        logger=app.logger,
        upload_image=lambda _cover: "img_v3_second_app",
    )

    first.prepare_after_publish(project)

    assert first.cached_image_key(project) == "img_v3_first_app"
    assert second.cached_image_key(project) is None


def test_card_prewarm_failure_does_not_rollback_publication(
    app_module, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    class FailingPublisher:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def prepare_after_publish(self, _project):
            type(self).calls += 1
            raise RuntimeError("simulated upload failure")

    monkeypatch.setattr(app_module, "FeishuPreviewPublisher", FailingPublisher)
    configured_app = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "prewarm-failure"),
            "MIN_FREE_DISK_BYTES": 1,
            "FEISHU_APP_ID": "cli_test",
            "FEISHU_APP_SECRET": "secret",
            "LAN_BASE_URL": "http://img-compare.sg.cn:8000",
        }
    )

    client = configured_app.test_client()
    published = _publish(client, "multi")
    repeated = client.post(f"/api/drafts/{published['id']}/finalize")

    assert repeated.status_code == 200
    assert FailingPublisher.calls == 1
    assert configured_app.extensions["project_store"].load_project(published["id"])


def test_long_connection_is_disabled_without_credentials(app):
    assert start_feishu_long_connection(app) is None
    assert "feishu_preview_thread" not in app.extensions

    status = app.test_client().get("/api/feishu/status")
    assert status.status_code == 200
    assert status.get_json() == {
        "mode": "long_connection",
        "configured": False,
        "worker_running": False,
        "client_initialized": False,
        "worker_state": "disabled",
        "last_error_type": None,
        "last_callback_at": None,
        "last_callback_result": None,
        "last_callback_card_ready": None,
        "share_base_url": None,
        "card_preview": {
            "configured": False,
            "worker_running": False,
            "queued": 0,
            "last_uploaded_at": None,
            "last_error_type": None,
            "last_error_code": None,
            "last_error_message": None,
        },
    }


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
