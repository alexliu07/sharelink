"""HTTP 接口端到端测试（走 FastAPI TestClient，真实读写磁盘与 SQLite）。"""

from __future__ import annotations

import hashlib
import os
import re

from app import codes, config, db


class TestUpload:
    def test_returns_share_code(self, client, upload):
        payload = upload(b"hello sharelink", "你好.txt", "30m")

        assert re.fullmatch(rf"[{codes.ALPHABET}]{{{config.CODE_LENGTH}}}", payload["code"])
        assert payload["filename"] == "你好.txt"
        assert payload["size"] == 15
        assert payload["sha256"] == hashlib.sha256(b"hello sharelink").hexdigest()
        assert payload["ttl_seconds"] == 1800
        assert payload["ttl_human"] == "30 分钟"
        assert payload["code"] in payload["share_url"]
        assert payload["code"] in payload["download_url"]
        assert payload["expired"] is False
        assert 1795 <= payload["seconds_left"] <= 1800

    def test_file_lands_on_disk_with_opaque_name(self, upload):
        payload = upload(b"content", "报告 final.pdf", "1h")
        record = db.get_file(payload["code"])

        assert record is not None
        assert record.path.exists()
        assert record.path.stat().st_size == payload["size"]
        # 磁盘名不含原始文件名，避免路径穿越/重名
        assert record.stored_name.startswith(payload["code"])
        assert "报告" not in record.stored_name

    def test_ttl_seconds_takes_priority(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("a.txt", b"x", "text/plain")},
            data={"ttl": "30m", "ttl_seconds": "7200"},
        )
        assert resp.status_code == 201
        assert resp.json()["ttl_seconds"] == 7200

    def test_default_ttl_when_not_specified(self, client):
        resp = client.post("/api/upload", files={"file": ("a.txt", b"x", "text/plain")})
        assert resp.status_code == 201
        assert resp.json()["ttl_seconds"] == config.DEFAULT_TTL_SECONDS

    def test_invalid_ttl_rejected(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("a.txt", b"x", "text/plain")},
            data={"ttl": "5 分钟"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_ttl"

    def test_ttl_out_of_range_rejected(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("a.txt", b"x", "text/plain")},
            data={"ttl": str(config.MAX_TTL_SECONDS + 1)},
        )
        assert resp.status_code == 400

    def test_oversize_rejected_and_cleaned_up(self, client):
        limit = config.MAX_UPLOAD_BYTES
        resp = client.post(
            "/api/upload",
            files={"file": ("big.bin", b"0" * (limit + 1024), "application/octet-stream")},
            data={"ttl": "1h"},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "too_large"
        # 不留半个文件，也不留元数据
        assert list(config.DATA_DIR.glob("*")) == []
        assert db.stats()["files"] == 0

    def test_path_traversal_attempt_is_neutralised(self, client):
        resp = client.post(
            "/api/upload",
            files={"file": ("../../etc/passwd", b"root:x:0:0", "text/plain")},
            data={"ttl": "1h"},
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["filename"] == "passwd"          # 目录部分被剥掉
        record = db.get_file(payload["code"])
        assert record.path.parent == config.DATA_DIR    # 仍写在数据目录内


class TestDownload:
    def test_roundtrip_keeps_bytes(self, client, upload):
        blob = os.urandom(300_000)  # 跨多个 1 MiB 分块边界之上仍验证哈希
        payload = upload(blob, "blob.bin", "1h")

        resp = client.get(f"/api/download/{payload['code']}")
        assert resp.status_code == 200
        assert resp.content == blob
        assert hashlib.sha256(resp.content).hexdigest() == payload["sha256"]

    def test_headers_force_attachment(self, client, upload):
        payload = upload(b"<script>alert(1)</script>", "evil.html", "1h")
        resp = client.get(f"/api/download/{payload['code']}")

        disposition = resp.headers["content-disposition"]
        assert "attachment" in disposition
        assert "evil.html" in disposition
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_lowercase_and_dashed_code_works(self, client, upload):
        payload = upload(b"data", "a.txt", "1h")
        code = payload["code"]
        dashed = f"{code[:4].lower()}-{code[4:].lower()}"

        assert client.get(f"/api/files/{dashed}").status_code == 200
        assert client.get(f"/api/download/{dashed}").status_code == 200

    def test_download_count_increments(self, client, upload):
        payload = upload(b"data", "a.txt", "1h")
        for _ in range(3):
            client.get(f"/api/download/{payload['code']}")
        info = client.get(f"/api/files/{payload['code']}").json()
        assert info["download_count"] == 3

    def test_unknown_code_returns_404(self, client):
        resp = client.get("/api/download/ZZZZZZZZ")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "not_found"

    def test_malformed_code_returns_400(self, client):
        resp = client.get("/api/download/ABC")   # 太短
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_code"

    def test_missing_file_on_disk_cleans_record(self, client, upload):
        payload = upload(b"data", "a.txt", "1h")
        db.get_file(payload["code"]).path.unlink()

        resp = client.get(f"/api/download/{payload['code']}")
        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "gone"
        assert db.get_file(payload["code"]) is None


class TestExpiry:
    def test_expired_download_returns_410_and_deletes(self, client, upload, expire_now):
        payload = upload(b"secret", "a.txt", "1h")
        record = expire_now(payload["code"])
        path = record.path
        assert path.exists()

        resp = client.get(f"/api/download/{payload['code']}")
        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "expired"
        assert not path.exists()                     # 文件已删
        assert db.get_file(payload["code"]) is None  # 记录已删

    def test_expired_info_also_deletes(self, client, upload, expire_now):
        payload = upload(b"secret", "a.txt", "1h")
        record = expire_now(payload["code"])

        assert client.get(f"/api/files/{payload['code']}").status_code == 410
        assert not record.path.exists()
        assert client.get(f"/api/download/{payload['code']}").status_code == 404  # 二次访问变 404

    def test_not_expired_still_downloadable(self, client, upload):
        payload = upload(b"alive", "a.txt", "1h")
        assert client.get(f"/api/download/{payload['code']}").status_code == 200


class TestDelete:
    def test_revoke_removes_file(self, client, upload):
        payload = upload(b"data", "a.txt", "7d")
        record = db.get_file(payload["code"])

        resp = client.delete(f"/api/files/{payload['code']}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not record.path.exists()
        assert db.get_file(payload["code"]) is None
        assert client.delete(f"/api/files/{payload['code']}").status_code == 404

    def test_revoke_bad_code(self, client):
        assert client.delete("/api/files/nope").status_code == 400


class TestOps:
    def test_healthz(self, client):
        assert client.get("/api/healthz").json()["status"] == "ok"

    def test_stats_reflects_state(self, client, upload):
        upload(b"12345", "a.txt", "1h")
        upload(b"1234567", "b.txt", "1h")
        stats = client.get("/api/stats").json()

        assert stats["shares"] == 2
        assert stats["shares_bytes"] == 12
        assert stats["disk_files"] == 2
        assert stats["max_upload_mb"] == config.MAX_UPLOAD_MB
        assert stats["cleanup_interval_seconds"] == config.CLEANUP_INTERVAL_SECONDS

    def test_index_page_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "ShareLink" in resp.text
        assert client.get("/app.js").status_code == 200
        assert client.get("/style.css").status_code == 200

    def test_codes_are_unique_across_uploads(self, client):
        seen = {client.post("/api/upload", files={"file": ("a.txt", b"x")}, data={"ttl": "1h"}).json()["code"]
                for _ in range(20)}
        assert len(seen) == 20


class TestPublicBasePath:
    """对外挂在子路径（如 https://host/share/）时，链接要带上前缀。"""

    def test_default_is_root(self, upload):
        payload = upload(b"x", "a.txt", "1h")

        assert payload["share_url"].endswith(f"/?code={payload['code']}")
        assert "/share/" not in payload["share_url"]
        assert payload["download_url"].endswith(f"/api/download/{payload['code']}")

    def test_prefix_applied_to_share_and_download_url(self, upload, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_BASE_PATH", "/share")
        payload = upload(b"x", "a.txt", "1h")

        assert payload["share_url"].endswith(f"/share/?code={payload['code']}")
        assert payload["download_url"].endswith(f"/share/api/download/{payload['code']}")


class TestBasePathNormalization:
    def test_normalizes(self, monkeypatch):
        for raw, expected in [
            (None, ""),
            ("", ""),
            ("/", ""),
            ("  ", ""),
            ("share", "/share"),
            ("/share", "/share"),
            ("/share/", "/share"),
            ("/a/b/", "/a/b"),
        ]:
            monkeypatch.setenv("SHARELINK_PUBLIC_BASE_PATH", raw if raw is not None else "")
            assert config._env_base_path("SHARELINK_PUBLIC_BASE_PATH") == expected, raw

    def test_rejects_query_string(self, monkeypatch):
        import pytest

        monkeypatch.setenv("SHARELINK_PUBLIC_BASE_PATH", "/share?x=1")
        with pytest.raises(RuntimeError):
            config._env_base_path("SHARELINK_PUBLIC_BASE_PATH")


class TestManifest:
    """manifest 的 share_target.action 必须是绝对 URL —— 相对路径会让安卓 Chrome 不把应用放进分享面板。"""

    def test_action_is_absolute_and_placeholder_replaced(self, client):
        resp = client.get("/manifest.webmanifest")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/manifest+json")
        assert resp.headers["cache-control"] == "no-cache"
        assert "__SHARE_TARGET_ACTION__" not in resp.text  # 占位符必须被换成真实地址

    def test_share_target_shape(self, client):
        target = client.get("/manifest.webmanifest").json()["share_target"]

        assert target["method"] == "POST"
        assert target["enctype"] == "multipart/form-data"
        assert target["action"].startswith(("http://", "https://"))
        assert target["action"].endswith("/api/share-target")
        assert target["params"]["files"][0]["name"] == "file"
        assert "*/*" in target["params"]["files"][0]["accept"]


class TestShareTarget:
    """安卓「分享 → ShareLink」的落地点：收文件或纯文本 → 生成分享码 → 303 回首页。"""

    class _Redirected:
        def __init__(self, response):
            location = response.headers["location"]
            assert "code=" in location, location
            self.code = location.split("code=")[1].split("&")[0]

    def _post(self, client, **kwargs):
        resp = client.post("/api/share-target", follow_redirects=False, **kwargs)
        assert resp.status_code == 303, resp.text
        return self._Redirected(resp)

    def test_shared_file_becomes_share_code(self, client):
        result = self._post(client, files={"file": ("报告.txt", b"hello", "text/plain")},
                            data={"title": "测试", "text": "来自分享面板"})
        info = client.get(f"/api/files/{result.code}").json()

        assert info["filename"] == "报告.txt"
        assert info["size"] == 5

    def test_text_only_share_saved_as_txt(self, client):
        """安卓分享链接时 URL 落在 text 里，title 拿来当文件名。"""
        result = self._post(client, data={"title": "一个链接", "text": "https://example.com"})
        info = client.get(f"/api/files/{result.code}").json()

        assert info["filename"] == "一个链接.txt"
        assert info["size"] == len("https://example.com".encode())

    def test_text_share_without_title_uses_fallback_name(self, client):
        result = self._post(client, data={"text": "只有正文"})
        info = client.get(f"/api/files/{result.code}").json()

        assert info["filename"] == "分享文本.txt"

    def test_empty_share_redirects_with_flag(self, client):
        resp = client.post("/api/share-target", data={"text": "   "}, follow_redirects=False)

        assert resp.status_code == 303
        assert "share=empty" in resp.headers["location"]
