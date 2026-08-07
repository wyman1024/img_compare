from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
GIF_1X1 = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)


def _pair_manifest(*, title_a: str = "", title_b: str = "") -> dict:
    return {
        "kind": "pair",
        "title": "",
        "variants": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
        "groups": [
            {
                "id": "group_1",
                "title": "图片对比",
                "images": {"A": "image_a", "B": "image_b"},
            }
        ],
        "images": [
            {"id": "image_a", "original_name": "left.png", "title": title_a},
            {"id": "image_b", "original_name": "right.gif", "title": title_b},
        ],
    }


def _publish_manifest(client, manifest: dict, payloads: dict[str, tuple[bytes, str]]) -> dict:
    created = client.post("/api/drafts", json=manifest)
    assert created.status_code == 201
    draft = created.get_json()
    for image_id, (raw, filename) in payloads.items():
        uploaded = client.put(
            draft["uploads"][image_id],
            data={"file": (io.BytesIO(raw), filename)},
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


def _publish_pair(client, *, title_a: str = "", title_b: str = "") -> dict:
    return _publish_manifest(
        client,
        _pair_manifest(title_a=title_a, title_b=title_b),
        {
            "image_a": (PNG_1X1, "left.png"),
            "image_b": (GIF_1X1, "right.gif"),
        },
    )


def test_home_exposes_unified_two_to_eight_image_creator(client):
    home = client.get("/")

    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert 'href="/gallery/new"' in html
    assert 'data-max-images="8"' in html
    assert 'id="add-slot"' in html
    assert 'id="remove-slot"' in html
    assert "0 张已选 · 2 个槽位" in html
    assert "图片对比" in html
    assert "2–8" in html
    assert "批量图集" in html
    assert client.get("/gallery/new").status_code == 200
    legacy_creator = client.get("/analyze/new")
    assert legacy_creator.status_code == 302
    assert legacy_creator.headers["Location"] == "/"
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert client.application.config["MAX_CONTENT_LENGTH"] == (
        client.application.config["MAX_IMAGE_BYTES"] + 2 * 1024 * 1024
    )


def test_creation_pages_offer_optional_per_image_title_fields(client):
    unified = client.get("/").get_data(as_text=True)
    gallery = client.get("/gallery/new").get_data(as_text=True)

    assert 'class="analyze-image-title-input"' in unified
    assert 'placeholder="图片标题（可选）"' in unified
    assert "analyze-label-input" not in unified
    assert "自定义版本标签" not in unified
    assert unified.count('maxlength="80"') >= 2
    assert gallery.count("gallery-pending-image-title") >= 2
    assert "gallery-review-image-title" in gallery
    assert "variant-a-label" not in gallery
    assert "variant-b-label" not in gallery
    assert "A 标签" not in gallery
    assert "B 标签" not in gallery
    assert unified.count('class="create-mode-tabs"') == 1
    assert gallery.count('class="create-mode-tabs"') == 1
    assert '<a href="/" aria-current="page">图片对比</a>' in unified
    assert '<a href="/gallery/new">批量图集</a>' in unified
    assert '<a href="/">图片对比</a>' in gallery
    assert '<a href="/gallery/new" aria-current="page">批量图集</a>' in gallery
    assert "gallery-back-link" not in gallery


def test_create_button_spinner_is_hidden_until_generation_starts(client):
    home = client.get("/").get_data(as_text=True)
    shared_css = client.get("/static/css/styles.css").get_data(as_text=True).replace("\r\n", "\n")
    analyze_css = client.get("/static/css/analyze.css").get_data(as_text=True).replace("\r\n", "\n")

    assert 'class="analyze-submit-copy"' in home
    assert ".button-spinner {\n  display: none;" in shared_css
    assert ".primary-button.is-loading .button-spinner" in shared_css
    assert ".analyze-submit-copy > strong,\n.analyze-submit-copy > span {" in analyze_css
    assert ".analyze-submit-bar span {" not in analyze_css


def test_pair_image_title_is_inside_fullscreen_comparison_and_blank_is_omitted(client):
    published = _publish_pair(client, title_a="基准渲染")

    page = client.get(published["url"])
    html = page.get_data(as_text=True)

    assert page.status_code == 200
    shell_start = html.index('class="comparison-shell"')
    shell_end = html.index('</section>', shell_start)
    fullscreen_shell = html[shell_start:shell_end]
    assert 'class="image-title-overlay title-a"' in fullscreen_shell
    assert "基准渲染" in fullscreen_shell
    assert 'class="image-title-overlay title-b"' not in fullscreen_shell


def test_gallery_and_multi_pages_receive_persisted_image_titles(client):
    gallery_manifest = {
        "kind": "gallery",
        "title": "标题测试图集",
        "variants": [
            {"id": "A", "label": "不应显示的 A 标签"},
            {"id": "B", "label": "不应显示的 B 标签"},
        ],
        "groups": [{
            "id": "group_1",
            "title": "第一组",
            "images": {"A": "gallery_a", "B": "gallery_b"},
        }],
        "images": [
            {"id": "gallery_a", "original_name": "gallery-a.png", "title": "图集 A 标题"},
            {"id": "gallery_b", "original_name": "gallery-b.gif"},
        ],
    }
    gallery = _publish_manifest(
        client,
        gallery_manifest,
        {
            "gallery_a": (PNG_1X1, "gallery-a.png"),
            "gallery_b": (GIF_1X1, "gallery-b.gif"),
        },
    )
    gallery_html = client.get(gallery["url"]).get_data(as_text=True)
    assert 'data-a-title="图集 A 标题"' in gallery_html
    assert 'data-b-title=""' in gallery_html
    assert "gallery-card-image-title-a" in gallery_html
    assert "gallery-card-image-title-b" not in gallery_html
    assert 'id="dialog-image-title-a"' in gallery_html
    assert "不应显示的 A 标签" not in gallery_html
    assert "不应显示的 B 标签" not in gallery_html
    dialog_viewer_start = gallery_html.index('id="dialog-viewer"')
    dialog_stage_start = gallery_html.index('id="dialog-stage"', dialog_viewer_start)
    dialog_toolbar = gallery_html[dialog_viewer_start:dialog_stage_start]
    assert 'data-gallery-action="fit"' in dialog_toolbar
    assert 'data-gallery-action="actual"' in dialog_toolbar
    assert 'id="dialog-zoom"' in dialog_toolbar
    assert 'id="dialog-title-size"' in dialog_toolbar
    assert 'id="dialog-title-size-output"' in dialog_toolbar
    assert 'id="dialog-fullscreen"' in dialog_toolbar
    assert 'aria-keyshortcuts="Shift+F"' in dialog_toolbar
    assert "Shift+F" in dialog_toolbar
    assert 'id="dialog-divider"' in gallery_html
    assert 'id="dialog-range"' not in gallery_html

    multi_manifest = {
        "kind": "multi",
        "title": "标题测试分析",
        "variants": [{"id": "A", "label": "版本 A"}, {"id": "B", "label": "版本 B"}],
        "groups": [{
            "id": "main",
            "title": "",
            "images": {"A": "multi_a", "B": "multi_b"},
        }],
        "images": [
            {"id": "multi_a", "original_name": "multi-a.png", "title": "多图 A 标题"},
            {"id": "multi_b", "original_name": "multi-b.gif"},
        ],
    }
    multi = _publish_manifest(
        client,
        multi_manifest,
        {
            "multi_a": (PNG_1X1, "multi-a.png"),
            "multi_b": (GIF_1X1, "multi-b.gif"),
        },
    )
    multi_html = client.get(multi["url"]).get_data(as_text=True)
    assert 'id="analysis-project-data"' in multi_html
    assert "多图 A 标题" in multi_html
    workspace_start = multi_html.index('id="analysis-workspace"')
    viewer_start = multi_html.index('id="analysis-viewer"', workspace_start)
    fullscreen_toolbar = multi_html[workspace_start:viewer_start]
    assert 'id="analysis-title-size"' in fullscreen_toolbar
    assert 'id="analysis-title-size-output"' in fullscreen_toolbar
    assert 'id="analysis-fullscreen"' in fullscreen_toolbar
    assert 'aria-keyshortcuts="Shift+F"' in fullscreen_toolbar
    assert "Shift+F" in fullscreen_toolbar


def test_pair_draft_publishes_fullscreen_page_and_survives_restart(
    app, app_module
):
    published = _publish_pair(app.test_client())
    project_id = published["id"]

    assert published["url"] == f"/compare/{project_id}"
    project_dir = Path(app.config["PROJECTS_DIR"]) / project_id
    assert project_dir.is_dir()
    assert not (Path(app.config["DRAFTS_DIR"]) / project_id).exists()

    restarted = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": app.config["DATA_DIR"],
            "MAX_IMAGE_BYTES": app.config["MAX_IMAGE_BYTES"],
            "MIN_FREE_DISK_BYTES": 1,
        }
    ).test_client()
    page = restarted.get(published["url"])
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'id="fullscreen-button"' in html
    assert 'aria-keyshortcuts="Shift+F"' in html
    assert "Shift + F" in html
    assert 'data-dimensions="1 × 1"' in html
    assert restarted.get(f"/media/{project_id}/image_a").data == PNG_1X1
    assert restarted.get(f"/media/{project_id}/image_b").data == GIF_1X1


def test_loopback_share_address_uses_configured_lan_url(app):
    app.config["LAN_BASE_URL"] = "http://192.168.50.14:8000"
    published = _publish_pair(app.test_client())

    loopback = app.test_client().get(published["url"]).get_data(as_text=True)
    assert f'value="http://192.168.50.14:8000{published["url"]}"' in loopback

    lan = app.test_client().get(
        published["url"], base_url="http://192.168.50.99:8000"
    ).get_data(as_text=True)
    assert f'value="http://192.168.50.99:8000{published["url"]}"' in lan


def test_v1_data_is_left_in_place_but_cannot_be_read_or_uploaded(app):
    old_id = "A" * 24
    old_dir = Path(app.config["DATA_DIR"]) / "comparisons" / old_id
    old_dir.mkdir(parents=True)
    (old_dir / "metadata.json").write_text(
        json.dumps({"id": old_id, "images": {}}), encoding="utf-8"
    )

    assert app.test_client().get(f"/compare/{old_id}").status_code == 404
    assert app.test_client().get(f"/media/{old_id}/image1").status_code == 404
    removed_api = app.test_client().post("/api/comparisons")
    assert removed_api.status_code == 404
    assert removed_api.get_json()["error"]["code"] == "not_found"
    assert old_dir.is_dir()


@pytest.mark.parametrize(
    "path",
    [
        "/compare/not-a-valid-id",
        "/gallery/AAAAAAAAAAAAAAAAAAAAAAAA",
        "/analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
        "/media/%2e%2e%2fmetadata.json/image_a",
        "/preview/%252e%252e/image_a",
    ],
)
def test_unknown_and_traversal_paths_return_404(client, path):
    assert client.get(path).status_code == 404


def test_api_request_size_limit_returns_json(app_module, tmp_path):
    limited = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "limited"),
            "MAX_IMAGE_BYTES": 1024,
            "MAX_CONTENT_LENGTH": 128,
            "MIN_FREE_DISK_BYTES": 1,
        }
    )
    response = limited.test_client().put(
        "/api/drafts/AAAAAAAAAAAAAAAAAAAAAAAA/images/image_a",
        data={"file": (io.BytesIO(PNG_1X1), "a.png")},
        content_type="multipart/form-data",
        headers={"X-Edit-Token": "not-important"},
    )

    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "request_too_large"


@pytest.mark.parametrize("quality", [0, 101, "invalid"])
def test_invalid_preview_quality_fails_at_startup(app_module, tmp_path, quality):
    with pytest.raises(RuntimeError, match="PREVIEW_QUALITY"):
        app_module.create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(tmp_path / f"quality-{quality}"),
                "PREVIEW_QUALITY": quality,
                "MIN_FREE_DISK_BYTES": 1,
            }
        )
