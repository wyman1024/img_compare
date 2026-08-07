from __future__ import annotations

import base64
import io

import pytest

from feishu_preview import FEISHU_PREVIEW_CALLBACK_PATH


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


def _event(url: str, *, token: str | None = None, event_type: str = "url.preview.get"):
    header = {"event_type": event_type}
    if token is not None:
        header["token"] = token
    return {
        "schema": "2.0",
        "header": header,
        "event": {"host": "im_message", "context": {"url": url}},
    }


def test_feishu_callback_verifies_challenge_and_token(app):
    app.config["FEISHU_VERIFICATION_TOKEN"] = "verification-secret"
    client = app.test_client()

    verified = client.post(
        FEISHU_PREVIEW_CALLBACK_PATH,
        json={
            "type": "url_verification",
            "token": "verification-secret",
            "challenge": "challenge-value",
        },
    )
    assert verified.status_code == 200
    assert verified.get_json() == {"challenge": "challenge-value"}

    rejected = client.post(
        FEISHU_PREVIEW_CALLBACK_PATH,
        json={
            "type": "url_verification",
            "token": "wrong-secret",
            "challenge": "challenge-value",
        },
    )
    assert rejected.status_code == 403
    assert rejected.get_json()["error"]["code"] == "invalid_verification_token"


@pytest.mark.parametrize(
    ("kind", "title", "expected_title"),
    [
        ("multi", "首页视觉走查", "首页视觉走查 · 图片对比"),
        ("gallery", "角色动作批次", "角色动作批次 · 批量图集"),
    ],
)
def test_feishu_preview_supports_both_current_share_modes(
    app, kind, title, expected_title
):
    app.config["FEISHU_VERIFICATION_TOKEN"] = "verification-secret"
    app.config["FEISHU_PREVIEW_IMAGE_KEY"] = "  img_test_icon  "
    client = app.test_client()
    published = _publish(client, kind, title=title)

    response = client.post(
        FEISHU_PREVIEW_CALLBACK_PATH,
        json=_event(
            f"https://compare.example.com{published['url']}?from=feishu#detail",
            token="verification-secret",
        ),
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "inline": {
            "title": expected_title,
            "image_key": "img_test_icon",
        }
    }


def test_feishu_preview_keeps_pair_share_urls_and_title_fallback(client):
    published = _publish(client, "pair")

    response = client.post(
        FEISHU_PREVIEW_CALLBACK_PATH,
        json=_event(f"http://192.168.1.20:8000{published['url']}"),
    )

    assert response.status_code == 200
    assert response.get_json() == {"inline": {"title": "图片对比"}}


@pytest.mark.parametrize(
    "url",
    [
        "https://compare.example.com/",
        "https://compare.example.com/analyze/not-a-project",
        "file:///analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
        "/analyze/AAAAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_feishu_preview_ignores_urls_that_are_not_published_projects(client, url):
    response = client.post(FEISHU_PREVIEW_CALLBACK_PATH, json=_event(url))

    assert response.status_code == 200
    assert response.get_json() == {}


def test_feishu_preview_rejects_encrypted_callbacks(client):
    response = client.post(
        FEISHU_PREVIEW_CALLBACK_PATH,
        json={"encrypt": "encrypted-body"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "encrypted_callback_not_supported"
