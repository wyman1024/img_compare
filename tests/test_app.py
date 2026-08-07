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


def _pair_manifest() -> dict:
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
            {"id": "image_a", "original_name": "left.png"},
            {"id": "image_b", "original_name": "right.gif"},
        ],
    }


def _publish_pair(client) -> dict:
    created = client.post("/api/drafts", json=_pair_manifest())
    assert created.status_code == 201
    draft = created.get_json()
    for image_id, raw, filename in (
        ("image_a", PNG_1X1, "left.png"),
        ("image_b", GIF_1X1, "right.gif"),
    ):
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


def test_home_and_creation_pages_expose_three_independent_modes(client):
    home = client.get("/")

    assert home.status_code == 200
    html = home.get_data(as_text=True)
    assert 'href="/gallery/new"' in html
    assert 'href="/analyze/new"' in html
    assert "双图对比" in html
    assert "批量图集" in html
    assert "多图分析" in html
    assert client.get("/gallery/new").status_code == 200
    assert client.get("/analyze/new").status_code == 200
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    assert client.application.config["MAX_CONTENT_LENGTH"] == (
        client.application.config["MAX_IMAGE_BYTES"] + 2 * 1024 * 1024
    )


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
