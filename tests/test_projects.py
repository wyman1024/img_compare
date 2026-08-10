from __future__ import annotations

import base64
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from project_store import ProjectStoreError


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
GIF_1X1 = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
)


def manifest(kind: str = "pair", *, groups: int = 1, variants: int = 2):
    variant_items = [
        {"id": chr(ord("A") + index), "label": f"版本 {index + 1}"}
        for index in range(variants)
    ]
    image_items = []
    group_items = []
    for group_index in range(groups):
        image_map = {}
        for variant in variant_items:
            image_id = f"image_{group_index}_{variant['id'].lower()}"
            image_map[variant["id"]] = image_id
            image_items.append(
                {"id": image_id, "original_name": f"{image_id}.png"}
            )
        group_items.append(
            {
                "id": f"group_{group_index}",
                "title": f"第 {group_index + 1} 组",
                "images": image_map,
            }
        )
    return {
        "kind": kind,
        "title": "测试项目",
        "variants": variant_items,
        "groups": group_items,
        "images": image_items,
    }


def create_draft(client, body=None):
    response = client.post("/api/drafts", json=body or manifest())
    assert response.status_code == 201
    return response.get_json()


def upload(client, draft, image_id, raw=PNG_1X1, filename=None, token=None):
    return client.put(
        draft["uploads"][image_id],
        data={"file": (io.BytesIO(raw), filename or f"{image_id}.png")},
        content_type="multipart/form-data",
        headers={"X-Edit-Token": token or draft["edit_token"]},
    )


def assert_error(response, status, code, field=None):
    assert response.status_code == status
    payload = response.get_json()["error"]
    assert payload["code"] == code
    assert payload["message"]
    if field is None:
        assert "field" not in payload
    else:
        assert payload["field"] == field


def encoded_image(image_format: str, width: int = 2, height: int = 1) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 220)).save(
        output, format=image_format
    )
    return output.getvalue()


def test_create_draft_returns_upload_map_and_hashes_edit_token(app):
    client = app.test_client()
    created = create_draft(client)

    assert set(created) == {"draft_id", "edit_token", "expires_at", "uploads"}
    assert set(created["uploads"]) == {"image_0_a", "image_0_b"}
    draft_dir = Path(app.config["DRAFTS_DIR"]) / created["draft_id"]
    metadata = json.loads((draft_dir / "metadata.json").read_text("utf-8"))
    assert metadata["schema_version"] == 2
    assert metadata["kind"] == "pair"
    assert metadata["images"]["image_0_a"]["title"] == ""
    assert metadata["edit_token_hash"] != created["edit_token"]
    assert created["edit_token"] not in (draft_dir / "metadata.json").read_text("utf-8")
    assert (draft_dir / "images").is_dir()
    assert (draft_dir / "previews").is_dir()
    public_state = client.get(f"/api/drafts/{created['draft_id']}")
    assert public_state.status_code == 200
    public_text = public_state.get_data(as_text=True)
    assert created["edit_token"] not in public_text
    assert "edit_token_hash" not in public_text
    assert public_state.get_json()["draft"]["images"]["image_0_a"]["title"] == ""


@pytest.mark.parametrize("title", [123, "x" * 81])
def test_manifest_rejects_invalid_image_title(client, title):
    body = manifest()
    body["images"][0]["title"] = title

    response = client.post("/api/drafts", json=body)

    assert_error(response, 400, "invalid_manifest", "images.0.title")


def test_image_title_is_trimmed_and_survives_upload_retry_and_publication(app):
    client = app.test_client()
    body = manifest()
    body["images"][0]["title"] = "  精修结果  "
    draft = create_draft(client, body)

    draft_state = client.get(f"/api/drafts/{draft['draft_id']}").get_json()["draft"]
    assert draft_state["images"]["image_0_a"]["title"] == "精修结果"

    first = upload(client, draft, "image_0_a")
    assert first.status_code == 200
    assert first.get_json()["image"]["title"] == "精修结果"
    retried = upload(client, draft, "image_0_a", raw=GIF_1X1, filename="replacement.gif")
    assert retried.status_code == 200
    assert retried.get_json()["image"]["title"] == "精修结果"

    assert upload(client, draft, "image_0_b").status_code == 200
    finalized = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert finalized.status_code == 201
    public_image = finalized.get_json()["project"]["images"]["image_0_a"]
    assert public_image["title"] == "精修结果"

    project_dir = Path(app.config["PROJECTS_DIR"]) / draft["draft_id"]
    metadata = json.loads((project_dir / "metadata.json").read_text("utf-8"))
    assert metadata["images"]["image_0_a"]["title"] == "精修结果"


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"kind": "video"}, "kind"),
        ({"title": "x" * 81}, "title"),
        ({"variants": [{"id": "A", "label": "A"}]}, "variants"),
        ({"groups": []}, "groups"),
    ],
)
def test_manifest_validation_reports_fields(client, change, field):
    body = manifest()
    body.update(change)

    response = client.post("/api/drafts", json=body)

    assert_error(response, 400, "invalid_manifest", field)


def test_manifest_rejects_path_ids_duplicate_references_and_extra_images(client):
    body = manifest()
    body["images"][0]["id"] = "../outside"
    assert_error(
        client.post("/api/drafts", json=body),
        400,
        "invalid_manifest",
        "images.0.id",
    )

    body = manifest()
    body["groups"][0]["images"]["B"] = body["groups"][0]["images"]["A"]
    assert_error(client.post("/api/drafts", json=body), 400, "invalid_manifest", "groups")

    body = manifest()
    body["images"].append({"id": "unused", "original_name": "unused.png"})
    assert_error(client.post("/api/drafts", json=body), 400, "invalid_manifest", "images")


def test_kind_cardinality_limits(client):
    assert client.post("/api/drafts", json=manifest("gallery", groups=30)).status_code == 201
    assert_error(
        client.post("/api/drafts", json=manifest("gallery", groups=31)),
        400,
        "invalid_manifest",
        "groups",
    )
    assert client.post("/api/drafts", json=manifest("multi", variants=8)).status_code == 201
    assert_error(
        client.post("/api/drafts", json=manifest("multi", variants=9)),
        400,
        "invalid_manifest",
        "variants",
    )

    non_contiguous = manifest("multi", variants=3)
    non_contiguous["variants"][2]["id"] = "D"
    assert_error(
        client.post("/api/drafts", json=non_contiguous),
        400,
        "invalid_manifest",
        "variants",
    )


def test_legacy_analyze_new_redirects_to_unified_creator(client):
    response = client.get("/analyze/new")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_upload_requires_token_and_known_slot(client):
    draft = create_draft(client)
    url = draft["uploads"]["image_0_a"]
    data = {"file": (io.BytesIO(PNG_1X1), "a.png")}

    missing = client.put(url, data=data, content_type="multipart/form-data")
    assert_error(missing, 401, "missing_edit_token")

    wrong = upload(client, draft, "image_0_a", token="wrong-token")
    assert_error(wrong, 403, "invalid_edit_token")

    unknown = client.put(
        f"/api/drafts/{draft['draft_id']}/images/unknown",
        data={"file": (io.BytesIO(PNG_1X1), "a.png")},
        content_type="multipart/form-data",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert_error(unknown, 404, "not_found")


def test_valid_upload_generates_webp_and_retry_atomically_replaces_file(app):
    client = app.test_client()
    draft = create_draft(client)
    first = upload(client, draft, "image_0_a", filename=r"C:\incoming\first.png")
    assert first.status_code == 200
    assert first.get_json()["image"] == {
        "id": "image_0_a",
        "status": "uploaded",
        "original_name": "first.png",
        "title": "",
        "format": "PNG",
        "mime_type": "image/png",
        "size": len(PNG_1X1),
        "width": 1,
        "height": 1,
    }

    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    first_metadata = json.loads((draft_dir / "metadata.json").read_text("utf-8"))
    first_path = draft_dir / first_metadata["images"]["image_0_a"]["stored_name"]
    first_preview = draft_dir / first_metadata["images"]["image_0_a"]["preview_name"]
    assert first_path.read_bytes() == PNG_1X1
    assert first_preview.read_bytes().startswith(b"RIFF")

    retried = upload(
        client,
        draft,
        "image_0_a",
        raw=GIF_1X1,
        filename="replacement.gif",
    )
    assert retried.status_code == 200
    second_metadata = json.loads((draft_dir / "metadata.json").read_text("utf-8"))
    second_image = second_metadata["images"]["image_0_a"]
    assert second_image["format"] == "GIF"
    assert second_image["mime_type"] == "image/gif"
    assert (draft_dir / second_image["stored_name"]).read_bytes() == GIF_1X1
    assert not first_path.exists()
    assert not first_preview.exists()
    assert len(list((draft_dir / "images").iterdir())) == 1
    assert len(list((draft_dir / "previews").iterdir())) == 1


@pytest.mark.parametrize(
    ("image_format", "filename", "mime_type"),
    [
        ("JPEG", "photo.jpg", "image/jpeg"),
        ("WEBP", "render.webp", "image/webp"),
        ("BMP", "capture.bmp", "image/bmp"),
    ],
)
def test_all_additional_static_formats_upload_end_to_end(
    client, image_format, filename, mime_type
):
    draft = create_draft(client)

    response = upload(
        client,
        draft,
        "image_0_a",
        raw=encoded_image(image_format),
        filename=filename,
    )

    assert response.status_code == 200
    image = response.get_json()["image"]
    assert image["format"] == image_format
    assert image["mime_type"] == mime_type
    assert image["width"] == 2
    assert image["height"] == 1


def test_concurrent_slot_uploads_do_not_lose_metadata(app):
    draft = create_draft(app.test_client())

    def upload_one(image_id):
        with app.test_client() as thread_client:
            return upload(thread_client, draft, image_id).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(upload_one, ("image_0_a", "image_0_b")))

    assert statuses == [200, 200]
    state = app.test_client().get(f"/api/drafts/{draft['draft_id']}").get_json()["draft"]
    assert state["images"]["image_0_a"]["status"] == "uploaded"
    assert state["images"]["image_0_b"]["status"] == "uploaded"


@pytest.mark.parametrize(
    ("raw", "filename", "code"),
    [
        (b"not an image", "fake.png", "unsupported_image_type"),
        (b"\x89PNG\r\n\x1a\ncorrupt", "broken.png", "invalid_image"),
        (b"", "empty.png", "empty_file"),
    ],
)
def test_invalid_upload_does_not_change_pending_slot(app, raw, filename, code):
    client = app.test_client()
    draft = create_draft(client)

    response = upload(client, draft, "image_0_a", raw=raw, filename=filename)

    assert_error(response, 400, code, "file")
    state = client.get(f"/api/drafts/{draft['draft_id']}").get_json()["draft"]
    assert state["images"]["image_0_a"]["status"] == "pending"
    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    assert list((draft_dir / "images").iterdir()) == []
    assert list((draft_dir / "previews").iterdir()) == []


def test_preview_generation_failure_keeps_slot_retryable(app, monkeypatch):
    client = app.test_client()
    draft = create_draft(client)
    store = app.extensions["project_store"]

    def fail_preview(_original, _preview):
        raise ProjectStoreError(
            "生成图片预览失败，请更换图片后重试。",
            code="preview_generation_failed",
            status=500,
            field="file",
        )

    monkeypatch.setattr(store, "_create_preview", fail_preview)
    response = upload(client, draft, "image_0_a")

    assert_error(response, 500, "preview_generation_failed", "file")
    state = client.get(f"/api/drafts/{draft['draft_id']}").get_json()["draft"]
    assert state["images"]["image_0_a"]["status"] == "pending"
    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    assert list((draft_dir / "images").iterdir()) == []
    assert list((draft_dir / "previews").iterdir()) == []


def test_published_preview_is_webp_with_configured_maximum_edge(
    app_module, tmp_path
):
    preview_app = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "preview-size"),
            "MAX_IMAGE_BYTES": 1024 * 1024,
            "MIN_FREE_DISK_BYTES": 1,
            "PREVIEW_MAX_EDGE": 96,
        }
    )
    client = preview_app.test_client()
    draft = create_draft(client)
    large_png = encoded_image("PNG", 400, 200)
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id, raw=large_png).status_code == 200
    finalized = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    ).get_json()

    preview = client.get(finalized["project"]["images"]["image_0_a"]["preview_url"])
    assert preview.status_code == 200
    assert preview.content_type == "image/webp"
    with Image.open(io.BytesIO(preview.data)) as image:
        assert image.format == "WEBP"
        assert image.size == (96, 48)


def test_per_image_and_project_total_limits(app_module, tmp_path):
    tiny_image_app = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "tiny-image"),
            "MAX_IMAGE_BYTES": len(PNG_1X1) - 1,
            "MIN_FREE_DISK_BYTES": 1,
        }
    )
    client = tiny_image_app.test_client()
    draft = create_draft(client)
    assert_error(upload(client, draft, "image_0_a"), 400, "file_too_large", "file")

    tiny_project_app = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(tmp_path / "tiny-project"),
            "MAX_IMAGE_BYTES": 1024,
            "MAX_PROJECT_BYTES": len(PNG_1X1),
            "MIN_FREE_DISK_BYTES": 1,
        }
    )
    client = tiny_project_app.test_client()
    draft = create_draft(client)
    assert upload(client, draft, "image_0_a").status_code == 200
    assert_error(upload(client, draft, "image_0_b"), 400, "project_too_large", "file")


def test_finalize_requires_all_files_then_publishes_atomically_and_is_idempotent(app, app_module):
    client = app.test_client()
    draft = create_draft(client)
    assert upload(client, draft, "image_0_a").status_code == 200
    incomplete = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert_error(incomplete, 409, "incomplete_draft", "image_0_b")
    assert upload(client, draft, "image_0_b", raw=GIF_1X1, filename="b.gif").status_code == 200

    finalize_url = f"/api/drafts/{draft['draft_id']}/finalize"
    published = client.post(finalize_url, headers={"X-Edit-Token": draft["edit_token"]})
    assert published.status_code == 201
    payload = published.get_json()
    assert payload["id"] == draft["draft_id"]
    assert payload["url"] == f"/compare/{draft['draft_id']}"
    assert payload["project"]["images"]["image_0_a"]["format"] == "PNG"
    assert payload["project"]["images"]["image_0_b"]["format"] == "GIF"
    assert not (Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]).exists()
    project_dir = Path(app.config["PROJECTS_DIR"]) / draft["draft_id"]
    assert project_dir.is_dir()
    stored = (project_dir / "metadata.json").read_text("utf-8")
    assert "edit_token_hash" not in stored
    assert "expires_at" not in stored

    repeated = client.post(finalize_url, headers={"X-Edit-Token": "already-invalid"})
    assert repeated.status_code == 200
    assert repeated.get_json()["project"] == payload["project"]

    restarted = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": app.config["DATA_DIR"],
            "MAX_IMAGE_BYTES": app.config["MAX_IMAGE_BYTES"],
            "MIN_FREE_DISK_BYTES": 1,
        }
    ).test_client()
    project = payload["project"]
    for image in project["images"].values():
        assert restarted.get(image["url"]).status_code == 200
        preview = restarted.get(image["preview_url"])
        assert preview.status_code == 200
        assert preview.content_type == "image/webp"
    assert restarted.get(payload["url"]).status_code == 200


def test_failed_directory_move_keeps_draft_retryable(app, monkeypatch):
    client = app.test_client()
    draft = create_draft(client)
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id).status_code == 200

    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    project_dir = Path(app.config["PROJECTS_DIR"]) / draft["draft_id"]
    real_replace = __import__("project_store").os.replace

    def fail_publication(source, destination):
        if Path(source) == draft_dir and Path(destination) == project_dir:
            raise OSError("simulated directory move failure")
        return real_replace(source, destination)

    monkeypatch.setattr("project_store.os.replace", fail_publication)
    url = f"/api/drafts/{draft['draft_id']}/finalize"
    failed = client.post(url, headers={"X-Edit-Token": draft["edit_token"]})
    assert_error(failed, 500, "storage_error")
    assert draft_dir.is_dir()
    metadata = json.loads((draft_dir / "metadata.json").read_text("utf-8"))
    assert "edit_token_hash" in metadata
    assert "published_at" not in metadata

    monkeypatch.setattr("project_store.os.replace", real_replace)
    retried = client.post(url, headers={"X-Edit-Token": draft["edit_token"]})
    assert retried.status_code == 201
    assert project_dir.is_dir()


def test_failed_published_metadata_write_returns_json_and_keeps_draft(
    app, monkeypatch
):
    client = app.test_client()
    draft = create_draft(client)
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id).status_code == 200

    project_store_module = __import__("project_store")
    real_atomic_json = project_store_module._atomic_json

    def fail_sidecar(path, value):
        if Path(path).name == "published-metadata.json":
            raise OSError("simulated sidecar write failure")
        return real_atomic_json(path, value)

    monkeypatch.setattr("project_store._atomic_json", fail_sidecar)
    url = f"/api/drafts/{draft['draft_id']}/finalize"
    failed = client.post(url, headers={"X-Edit-Token": draft["edit_token"]})

    assert_error(failed, 500, "storage_error")
    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    assert draft_dir.is_dir()
    metadata = json.loads((draft_dir / "metadata.json").read_text("utf-8"))
    assert "edit_token_hash" in metadata
    assert "published_at" not in metadata

    monkeypatch.setattr("project_store._atomic_json", real_atomic_json)
    assert client.post(url, headers={"X-Edit-Token": draft["edit_token"]}).status_code == 201


def test_project_recovers_if_process_stops_after_publication_move(app, monkeypatch):
    client = app.test_client()
    draft = create_draft(client)
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id).status_code == 200

    project_dir = Path(app.config["PROJECTS_DIR"]) / draft["draft_id"]
    published_sidecar = project_dir / "published-metadata.json"
    metadata_path = project_dir / "metadata.json"
    real_replace = __import__("project_store").os.replace

    def fail_only_final_metadata_swap(source, destination):
        if Path(source) == published_sidecar and Path(destination) == metadata_path:
            raise OSError("simulated stop after directory publication")
        return real_replace(source, destination)

    monkeypatch.setattr("project_store.os.replace", fail_only_final_metadata_swap)
    response = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert response.status_code == 201
    assert published_sidecar.is_file()
    assert "edit_token_hash" in metadata_path.read_text("utf-8")

    monkeypatch.setattr("project_store.os.replace", real_replace)
    page = client.get(f"/compare/{draft['draft_id']}")
    assert page.status_code == 200
    assert not published_sidecar.exists()
    recovered = json.loads(metadata_path.read_text("utf-8"))
    assert "edit_token_hash" not in recovered
    assert "expires_at" not in recovered
    assert recovered["published_at"]


def test_cancel_requires_token_and_removes_only_draft(app):
    client = app.test_client()
    draft = create_draft(client)
    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]

    assert_error(client.delete(f"/api/drafts/{draft['draft_id']}"), 401, "missing_edit_token")
    cancelled = client.delete(
        f"/api/drafts/{draft['draft_id']}",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert cancelled.status_code == 204
    assert not draft_dir.exists()
    assert_error(client.get(f"/api/drafts/{draft['draft_id']}"), 404, "not_found")


def test_expired_draft_is_removed_and_returns_410(app):
    client = app.test_client()
    draft = create_draft(client)
    draft_dir = Path(app.config["DRAFTS_DIR"]) / draft["draft_id"]
    metadata_path = draft_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    metadata["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    response = client.get(f"/api/drafts/{draft['draft_id']}")

    assert_error(response, 410, "draft_expired")
    assert not draft_dir.exists()


def test_creating_a_draft_sweeps_other_expired_drafts(app):
    client = app.test_client()
    expired = create_draft(client)
    expired_dir = Path(app.config["DRAFTS_DIR"]) / expired["draft_id"]
    metadata_path = expired_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    metadata["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    fresh = create_draft(client)

    assert fresh["draft_id"] != expired["draft_id"]
    assert not expired_dir.exists()
    assert (Path(app.config["DRAFTS_DIR"]) / fresh["draft_id"]).is_dir()


def test_public_media_rejects_unknown_and_traversal_metadata(app):
    client = app.test_client()
    draft = create_draft(client)
    assert upload(client, draft, "image_0_a").status_code == 200
    assert upload(client, draft, "image_0_b").status_code == 200
    client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )

    assert client.get(f"/media/{draft['draft_id']}/unknown").status_code == 404
    project_dir = Path(app.config["PROJECTS_DIR"]) / draft["draft_id"]
    metadata_path = project_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text("utf-8"))
    metadata["images"]["image_0_a"]["stored_name"] = "../../outside.png"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    outside = Path(app.config["DATA_DIR"]) / "outside.png"
    outside.write_bytes(PNG_1X1)
    assert client.get(f"/media/{draft['draft_id']}/image_0_a").status_code == 404


def test_v1_comparison_data_is_left_in_place_but_never_read(app):
    legacy_id = "A" * 24
    legacy_dir = Path(app.config["DATA_DIR"]) / "comparisons" / legacy_id
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": legacy_id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "images": {},
            }
        ),
        encoding="utf-8",
    )

    client = app.test_client()
    assert client.get(f"/compare/{legacy_id}").status_code == 404
    assert client.get(f"/media/{legacy_id}/image1").status_code == 404
    assert_error(client.post("/api/comparisons"), 404, "not_found")
    assert legacy_dir.is_dir()


def test_insufficient_disk_returns_507(app, monkeypatch):
    usage = type("Usage", (), {"total": 100, "used": 99, "free": 1})()
    monkeypatch.setattr("project_store.shutil.disk_usage", lambda _path: usage)

    response = app.test_client().post("/api/drafts", json=manifest())

    assert_error(response, 507, "insufficient_storage")


def test_health_reports_storage_readiness(app, app_module, monkeypatch):
    healthy_usage = type(
        "Usage",
        (),
        {
            "total": app.config["MIN_FREE_DISK_BYTES"] + 100,
            "used": 100,
            "free": app.config["MIN_FREE_DISK_BYTES"],
        },
    )()
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda _path: healthy_usage)

    response = app.test_client().get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "storage": {
            "writable": True,
            "free_bytes": app.config["MIN_FREE_DISK_BYTES"],
            "required_free_bytes": app.config["MIN_FREE_DISK_BYTES"],
        },
    }


def test_health_returns_503_when_storage_is_not_ready(app, app_module, monkeypatch):
    low_usage = type("Usage", (), {"total": 100, "used": 99, "free": 1})()
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda _path: low_usage)

    response = app.test_client().get("/api/health")

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_upload_reserves_configured_free_space_after_worst_case_write(
    app, monkeypatch
):
    client = app.test_client()
    draft = create_draft(client)
    required = (
        app.config["MIN_FREE_DISK_BYTES"]
        + app.config["MAX_IMAGE_BYTES"] * 2
        - 1
    )
    usage = type(
        "Usage",
        (),
        {"total": required + 100, "used": 101, "free": required},
    )()
    monkeypatch.setattr("project_store.shutil.disk_usage", lambda _path: usage)

    response = upload(client, draft, "image_0_a")

    assert_error(response, 507, "insufficient_storage")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_IMAGE_BYTES", 0),
        ("MAX_PROJECT_BYTES", -1),
        ("DRAFT_TTL_SECONDS", "not-an-integer"),
        ("PREVIEW_MAX_EDGE", 0),
        ("PREVIEW_QUALITY", 101),
    ],
)
def test_invalid_storage_config_fails_at_startup(app_module, tmp_path, key, value):
    with pytest.raises(RuntimeError, match=key):
        app_module.create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(tmp_path / f"invalid-{key}"),
                key: value,
            }
        )


@pytest.mark.parametrize(
    ("kind", "groups", "variants", "route_prefix"),
    [
        ("gallery", 1, 2, "gallery"),
        ("multi", 1, 2, "analyze"),
        ("multi", 1, 3, "analyze"),
        ("multi", 1, 4, "analyze"),
        ("multi", 1, 8, "analyze"),
    ],
)
def test_gallery_and_multi_projects_have_kind_specific_urls(
    client, app_module, kind, groups, variants, route_prefix
):
    body = manifest(kind, groups=groups, variants=variants)
    draft = create_draft(client, body)
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id).status_code == 200
    response = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["url"] == f"/{route_prefix}/{draft['draft_id']}"
    assert len(payload["project"]["variants"]) == variants
    assert client.get(payload["url"]).status_code == 200

    restarted = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": client.application.config["DATA_DIR"],
            "MAX_IMAGE_BYTES": client.application.config["MAX_IMAGE_BYTES"],
            "MIN_FREE_DISK_BYTES": 1,
        }
    ).test_client()
    assert restarted.get(payload["url"]).status_code == 200
    for image in payload["project"]["images"].values():
        assert restarted.get(image["url"]).status_code == 200
        assert restarted.get(image["preview_url"]).status_code == 200


def test_full_30_group_gallery_survives_restart(app, app_module):
    client = app.test_client()
    draft = create_draft(client, manifest("gallery", groups=30))
    for image_id in draft["uploads"]:
        assert upload(client, draft, image_id).status_code == 200
    finalized = client.post(
        f"/api/drafts/{draft['draft_id']}/finalize",
        headers={"X-Edit-Token": draft["edit_token"]},
    )
    assert finalized.status_code == 201
    assert len(finalized.get_json()["project"]["groups"]) == 30
    assert len(finalized.get_json()["project"]["images"]) == 60

    restarted = app_module.create_app(
        {
            "TESTING": True,
            "DATA_DIR": app.config["DATA_DIR"],
            "MAX_IMAGE_BYTES": app.config["MAX_IMAGE_BYTES"],
            "MIN_FREE_DISK_BYTES": 1,
        }
    ).test_client()
    assert restarted.get(f"/gallery/{draft['draft_id']}").status_code == 200
