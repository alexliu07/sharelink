"""设备互传测试：登记/令牌鉴权、定向投递、收件箱、过期与回收。"""

from __future__ import annotations

import io
import sqlite3
from datetime import timedelta

import pytest

from app import cleanup, config, db, groups

UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
UA_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def hdr(token: str = None, ua: str = UA_MAC) -> dict:
    headers = {"User-Agent": ua}
    if token:
        headers["X-Device-Token"] = token
    return headers


def register(client, name=None, ua=UA_MAC) -> dict:
    resp = client.post("/api/devices", json={"name": name}, headers=hdr(ua=ua))
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_group(client, owner: dict, *members: dict, name: str = "测试组") -> dict:
    """通过接口建一个设备组，并把 members 逐个加进去（返回组的公开信息）。"""
    created = client.post("/api/groups", json={"device_id": owner["id"], "name": name}, headers=hdr(owner["token"]))
    assert created.status_code == 201, created.text
    group_id = created.json()["group"]["id"]
    for member in members:
        joined = client.post(f"/api/groups/{group_id}/join", json={"device_id": member["id"]},
                             headers=hdr(member["token"]))
        assert joined.status_code in (200, 201), joined.text
    return created.json()["group"]


def ensure_same_group(sender_id: str, target_ids) -> str:
    """测试用的铺路函数：把发送方与目标设备放进同一个组（已经同组就复用）。

    直接落库建组（目标设备在测试里只有 id、拿不到它们的令牌），等价于"对方拿组 id 加入"；
    建设备组接口本身的规则由 tests/test_groups.py 从接口层覆盖。
    """
    devices = [db.get_device(target_id) for target_id in target_ids if target_id]
    strangers = [d for d in devices if d is not None and not db.list_shared_groups(sender_id, d.id)]
    existing = db.device_group_ids(sender_id)
    if not strangers:
        return existing[0] if existing else ""
    now = db.to_iso(db.utcnow())
    group_id = groups.generate_group_id(db.group_id_exists)
    db.insert_group(db.GroupRecord(id=group_id, name="测试组", owner_device_id=sender_id, created_at=now))
    db.add_group_member(group_id, sender_id, "owner", now)
    for stranger in strangers:
        db.add_group_member(group_id, stranger.id, "member", now)
    return group_id


def send(client, targets, content=b"payload", filename="a.txt", ttl="1h", token=None, **form):
    """投递（multipart）。

    投递现在必须「实名 + 同组」，所以这里自动铺路：没给令牌就造一台发送设备，
    给了令牌就用 from_device_id 把发送方和目标设备放进同一个组。
    组与权限规则本身另有 test_groups.py 专门覆盖。
    """
    target_ids = [targets] if isinstance(targets, str) else list(targets)
    sender_id = form.get("from_device_id")
    if token is None:
        sender = register(client, "测试发送方")
        token, sender_id = sender["token"], sender["id"]
        form["from_device_id"] = sender_id
    if sender_id:
        ensure_same_group(sender_id, target_ids)
    data = {"targets": ",".join(targets) if isinstance(targets, (list, tuple)) else targets, "ttl": ttl}
    data.update({k: str(v) for k, v in form.items()})
    return client.post("/api/transfers", files={"file": (filename, io.BytesIO(content), "text/plain")},
                       data=data, headers=hdr(token))


def inbox(client, device, token=None, **params) -> dict:
    resp = client.get(f"/api/devices/{device['id']}/inbox", headers=hdr(token or device["token"]), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def age_device(device_id: str, days: int) -> None:
    """把设备的最后活跃时间改成 N 天前（模拟长期不活跃）。"""
    past = db.to_iso(db.utcnow() - timedelta(days=days))
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (past, device_id))


class TestRegistration:
    def test_register_returns_id_and_token(self, client):
        device = register(client, "我的笔记本")

        assert device["id"].startswith("dev_")
        assert len(device["id"]) == len("dev_") + config.CODE_LENGTH
        assert len(device["token"]) >= 20
        assert device["name"] == "我的笔记本"
        assert device["idle_seconds"] == 0

    def test_name_optional_guess_from_user_agent(self, client):
        assert register(client, ua=UA_WIN)["name"] == "Chrome · Windows"
        assert register(client, ua=UA_MAC)["name"] == "Safari · macOS"

    def test_name_is_cleaned(self, client):
        assert register(client, "  <b>坏</b>   名字  ")["name"] == "b 坏 /b 名字"
        assert register(client, "   ")["name"] == "未命名设备"
        assert len(register(client, "长" * 100)["name"]) == 40   # 超长名截断而不是报错

    def test_absurdly_long_name_rejected(self, client):
        assert client.post("/api/devices", json={"name": "长" * 500}, headers=hdr()).status_code == 422

    def test_duplicate_names_allowed(self, client):
        first, second = register(client, "同名"), register(client, "同名")
        assert first["id"] != second["id"]

    def test_token_never_leaks_in_listing(self, client):
        device = register(client, "我的笔记本")
        params = {"device_id": device["id"]}
        body = client.get("/api/devices", params=params, headers=hdr(device["token"])).text

        assert device["token"] not in body
        assert "token" not in body
        listed = client.get("/api/devices", params=params, headers=hdr(device["token"])).json()["devices"][0]
        assert set(listed) == {"id", "name", "created_at", "last_seen_at", "idle_seconds", "inbox_count",
                               "is_self", "shared_groups"}

    def test_token_stored_hashed(self, client):
        device = register(client)
        row = db.get_device(device["id"])
        assert row.token_hash != device["token"]
        assert len(row.token_hash) == 64  # sha256 hex


class TestDeviceList:
    """设备页只显示「自己 + 同组设备」；不带令牌不再对外列出任何设备名单。"""

    def test_anonymous_sees_no_roster(self, client):
        register(client, "别人的设备")

        listed = client.get("/api/devices").json()
        assert listed["count"] == 0
        assert listed["devices"] == []
        assert listed["scope"] == "unregistered"

    def test_shows_only_same_group_devices(self, client):
        me, mate, stranger = register(client, "我"), register(client, "同组的"), register(client, "不同组的")
        make_group(client, me, mate, name="家里的设备")

        params = {"device_id": me["id"]}
        listed = client.get("/api/devices", params=params, headers=hdr(me["token"])).json()

        assert listed["count"] == 2                      # 我 + 同组的一台，不同组的不出现
        assert [d["name"] for d in listed["devices"]] == ["我", "同组的"]
        assert listed["devices"][0]["is_self"] is True
        assert listed["devices"][0]["shared_groups"] == []            # 自己跟自己没有"共同组"
        assert [g["name"] for g in listed["devices"][1]["shared_groups"]] == ["家里的设备"]

    def test_inbox_count_included(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        make_group(client, sender, receiver)
        assert send(client, [receiver["id"]], token=sender["token"],
                    from_device_id=sender["id"]).status_code == 201

        params = {"device_id": sender["id"]}
        listed = {d["id"]: d for d in client.get("/api/devices", params=params,
                                                 headers=hdr(sender["token"])).json()["devices"]}
        assert listed[receiver["id"]]["inbox_count"] == 1
        assert listed[sender["id"]]["inbox_count"] == 0


class TestTransfers:
    def test_send_to_single_device(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        resp = send(client, [receiver["id"]], b"hello", "报告.pdf", "30m", token=sender["token"],
                    note="周会材料", from_device_id=sender["id"])

        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["filename"] == "报告.pdf"
        assert payload["size"] == 5
        assert payload["ttl_seconds"] == 1800
        assert payload["from_name"] == "发送方"
        assert payload["note"] == "周会材料"
        assert payload["transfer_count"] == 1
        assert payload["targets"] == [{"id": receiver["id"], "name": "收件方"}]

    def test_send_to_multiple_devices_stores_one_file(self, client):
        sender = register(client, "发送方")
        targets = [register(client, f"设备{i}") for i in range(3)]

        resp = send(client, [d["id"] for d in targets], b"shared", "包.zip", "1h", token=sender["token"],
                    from_device_id=sender["id"])
        assert resp.status_code == 201
        code = resp.json()["code"]
        assert resp.json()["transfer_count"] == 3

        assert len(list(config.DATA_DIR.glob("*"))) == 1  # 只落一份文件
        for target in targets:
            items = inbox(client, target)["items"]
            assert [i["code"] for i in items] == [code]

    def test_duplicate_targets_deduped(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        resp = send(client, [receiver["id"], receiver["id"], f" {receiver['id']} "], token=sender["token"],
                    from_device_id=sender["id"])
        assert resp.json()["transfer_count"] == 1

    def test_delivery_requires_registered_device(self, client):
        """投递给设备必须实名：没有设备令牌 → 403 needs_device（组才是授权单位）。"""
        receiver = register(client, "收件方")

        resp = client.post(
            "/api/transfers",
            files={"file": ("a.txt", io.BytesIO(b"payload"), "text/plain")},
            data={"targets": receiver["id"], "ttl": "1h", "from_name": "办公室的电脑"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "needs_device"
        assert db.stats()["transfers"] == 0

        # 而不登记也能用的路是"只生成分享码"
        assert client.post("/api/upload", files={"file": ("a.txt", b"payload", "text/plain")},
                           data={"ttl": "1h"}).status_code == 201

    def test_wrong_token_is_not_a_sender(self, client):
        """令牌不对 = 不是这台设备：不会顶着它的名字投递，而是直接拒绝。"""
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        resp = send(client, [receiver["id"]], token="forged-token", from_device_id=sender["id"], from_name="冒充者")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "needs_device"

    def test_download_link_works(self, client):
        receiver = register(client, "收件方")
        code = send(client, [receiver["id"]], b"file-bytes").json()["code"]

        resp = client.get(f"/api/download/{code}")
        assert resp.status_code == 200
        assert resp.content == b"file-bytes"

    @pytest.mark.parametrize(
        "targets,status,error",
        [
            ("", 400, "no_target"),
            ("dev_NOPE1234", 400, "bad_target"),
            ("not-a-device", 400, "bad_target"),
        ],
    )
    def test_bad_targets(self, client, targets, status, error):
        resp = send(client, targets)
        assert resp.status_code == status
        assert resp.json()["detail"]["error"] == error

    def test_too_many_targets(self, client, monkeypatch):
        monkeypatch.setattr(config, "MAX_TARGETS_PER_SEND", 2)
        targets = [register(client, f"设备{i}") for i in range(3)]

        resp = send(client, [d["id"] for d in targets])
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "too_many_targets"

    def test_invalid_ttl_rejected(self, client):
        receiver = register(client)
        resp = send(client, [receiver["id"]], ttl="不是时长")
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_ttl"

    def test_oversize_leaves_no_transfer(self, client):
        receiver = register(client)
        resp = send(client, [receiver["id"]], b"0" * (config.MAX_UPLOAD_BYTES + 1024))

        assert resp.status_code == 413
        assert inbox(client, receiver)["count"] == 0
        assert db.stats()["transfers"] == 0


class TestInbox:
    def test_only_own_files_visible(self, client):
        sender, alice, bob = register(client, "发送方"), register(client, "Alice"), register(client, "Bob")
        make_group(client, sender, alice, bob)
        send(client, [alice["id"]], b"for-alice", "alice.txt", token=sender["token"], from_device_id=sender["id"])
        send(client, [bob["id"]], b"for-bob", "bob.txt", token=sender["token"], from_device_id=sender["id"])

        assert [i["filename"] for i in inbox(client, alice)["items"]] == ["alice.txt"]
        assert [i["filename"] for i in inbox(client, bob)["items"]] == ["bob.txt"]

    def test_item_fields(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        send(client, [receiver["id"]], b"12345", "材料.pdf", "2h", token=sender["token"],
             from_device_id=sender["id"], note="备注")

        item = inbox(client, receiver)["items"][0]
        assert item["filename"] == "材料.pdf"
        assert item["size"] == 5
        assert item["from_name"] == "发送方"
        assert item["from_device_id"] == sender["id"]
        assert item["note"] == "备注"
        assert 7100 <= item["seconds_left"] <= 7200
        assert item["expired"] is False
        assert item["download_url"].endswith(f"/api/download/{item['code']}")
        assert item["share_url"].endswith(f"/?code={item['code']}")
        assert item["seen"] is False

    def test_ordered_newest_first(self, client):
        receiver = register(client, "收件方")
        names = []
        for i in range(3):
            name = f"f{i}.txt"
            names.append(name)
            send(client, [receiver["id"]], f"data{i}".encode(), name)
        assert [i["filename"] for i in inbox(client, receiver)["items"]] == list(reversed(names))

    def test_mark_seen(self, client):
        receiver = register(client, "收件方")
        send(client, [receiver["id"]])

        assert inbox(client, receiver)["unread"] == 1
        assert client.post(f"/api/devices/{receiver['id']}/inbox/seen", headers=hdr(receiver["token"])).json() == {"marked": 1}
        assert inbox(client, receiver)["unread"] == 0

    def test_remove_item_keeps_file(self, client):
        receiver = register(client, "收件方")
        code = send(client, [receiver["id"]], b"kept").json()["code"]

        resp = client.delete(f"/api/devices/{receiver['id']}/inbox/{code}", headers=hdr(receiver["token"]))
        assert resp.status_code == 200
        assert inbox(client, receiver)["count"] == 0
        assert client.get(f"/api/download/{code}").content == b"kept"
        # 再删一次 → 404
        assert client.delete(f"/api/devices/{receiver['id']}/inbox/{code}", headers=hdr(receiver["token"])).status_code == 404

    def test_inbox_touches_last_seen(self, client):
        device = register(client)
        age_device(device["id"], 5)
        before = db.get_device(device["id"]).idle_seconds()
        assert before > 4 * 86400

        inbox(client, device)
        assert db.get_device(device["id"]).idle_seconds() < 60


class TestAuth:
    @pytest.mark.parametrize("token,status,error", [(None, 403, "bad_device_token"), ("wrong", 403, "bad_device_token")])
    def test_inbox_requires_token(self, client, token, status, error):
        device = register(client)
        resp = client.get(f"/api/devices/{device['id']}/inbox", headers=hdr(token))
        assert resp.status_code == status
        assert resp.json()["detail"]["error"] == error

    def test_unknown_device(self, client):
        resp = client.get("/api/devices/dev_ZZZZZZZZ/inbox", headers=hdr("x"))
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "device_not_found"

    def test_cannot_read_other_inbox(self, client):
        alice, bob = register(client, "Alice"), register(client, "Bob")
        assert client.get(f"/api/devices/{alice['id']}/inbox", headers=hdr(bob["token"])).status_code == 403

    def test_rename_requires_token(self, client):
        device = register(client, "原名")
        assert client.patch(f"/api/devices/{device['id']}", json={"name": "新名"}).status_code == 403

        resp = client.patch(f"/api/devices/{device['id']}", json={"name": "  新名  "}, headers=hdr(device["token"]))
        assert resp.status_code == 200
        assert resp.json()["name"] == "新名"
        assert resp.json()["renamed"] is True

    def test_unregister_requires_token_and_cascades(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        make_group(client, sender, receiver)
        send(client, [receiver["id"]], token=sender["token"], from_device_id=sender["id"])

        assert client.delete(f"/api/devices/{sender['id']}", headers=hdr(receiver["token"])).status_code == 403

        assert client.delete(f"/api/devices/{sender['id']}", headers=hdr(sender["token"])).status_code == 200
        assert db.get_device(sender["id"]) is None
        assert db.stats()["devices"] == 1
        assert inbox(client, receiver)["count"] == 1  # 收件方不受影响
        # 管理员（=创建者）注销 → 组跟着解散，收件方在设备页只剩自己
        params = {"device_id": receiver["id"]}
        assert client.get("/api/devices", params=params, headers=hdr(receiver["token"])).json()["count"] == 1
        assert db.stats()["groups"] == 0


class TestExpiryAndPrune:
    def test_expired_file_disappears_from_inbox(self, client, expire_now):
        receiver = register(client, "收件方")
        code = send(client, [receiver["id"]], b"temporary").json()["code"]
        assert inbox(client, receiver)["count"] == 1

        expire_now(code)
        cleanup.purge_expired()

        assert inbox(client, receiver)["count"] == 0
        assert db.count_device_inbox(receiver["id"]) == 0   # 投递记录也被清掉，不留死条目
        assert client.get(f"/api/download/{code}").status_code == 404

    def test_purge_one_also_clears_transfers(self, client):
        receiver = register(client, "收件方")
        code = send(client, [receiver["id"]]).json()["code"]

        client.delete(f"/api/files/{code}")
        assert db.count_device_inbox(receiver["id"]) == 0
        assert client.get("/api/stats").json()["transfers"] == 0

    def test_prune_idle_devices(self, client):
        active = register(client, "活跃设备")
        stale = register(client, "闲置设备")
        age_device(stale["id"], 40)

        removed = cleanup.prune_idle_devices(days=config.DEVICE_IDLE_DAYS)

        assert removed == [stale["id"]]
        assert db.get_device(stale["id"]) is None
        assert db.get_device(active["id"]) is not None

    def test_prune_keeps_device_with_pending_files(self, client):
        receiver = register(client, "有待取文件的设备")
        send(client, [receiver["id"]], ttl="7d")
        age_device(receiver["id"], 40)

        assert cleanup.prune_idle_devices(days=30) == []
        assert db.get_device(receiver["id"]) is not None
        assert inbox(client, receiver)["count"] == 1


def send_text(client, text, targets=None, ttl="1h", token=None, **extra):
    """发文本（JSON 接口）。targets 省略 = 只生成分享码（不要求登记）；给了 targets 就按投递规则铺路。"""
    payload = {"text": text, "ttl": ttl}
    if targets is not None:
        payload["targets"] = list(targets)
    payload.update(extra)
    sender_id = extra.get("from_device_id")
    if targets and sender_id and token:
        ensure_same_group(sender_id, list(targets))
    return client.post("/api/texts", json=payload, headers=hdr(token))


class TestTexts:
    """发文本：既能只生成分享码，也能直接投递给设备；存成小 text/plain，客户端按 is_text 内联显示。"""

    def test_text_only_returns_share_code(self, client):
        body = "第一行当标题\n后面是正文"
        resp = send_text(client, body)

        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["filename"] == "第一行当标题.txt"          # 首行做文件名
        assert payload["size"] == len(body.encode("utf-8"))
        assert payload["chars"] == len(body)
        assert payload["is_text"] is True
        assert payload["ttl_seconds"] == 3600          # 调 send_text 时给的是 ttl="1h"
        assert payload["transfer_count"] == 0                     # 没给 targets → 只给码
        assert payload["code"] in payload["share_url"]

        # 内容原样，取回来逐字节一致
        got = client.get(f"/api/download/{payload['code']}")
        assert got.status_code == 200
        assert got.content == body.encode("utf-8")
        assert got.headers["content-type"].startswith("text/plain")

    def test_long_first_line_is_truncated_for_filename(self, client):
        payload = send_text(client, "标题" * 60 + "\n正文").json()
        assert payload["filename"].endswith(".txt")
        assert len(payload["filename"]) <= 40 + len(".txt")

    def test_multiline_text_info_marked_as_text(self, client):
        code = send_text(client, "abc").json()["code"]
        info = client.get(f"/api/files/{code}").json()
        assert info["is_text"] is True
        assert info["filename"] == "abc.txt"

    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t "])
    def test_empty_text_rejected(self, client, text):
        resp = send_text(client, text)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "empty_text"

    def test_too_long_rejected_and_nothing_stored(self, client):
        resp = send_text(client, "字" * (config.MAX_TEXT_CHARS + 1))
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "too_long"
        assert "上限" in resp.json()["detail"]["message"]
        assert len(list(config.DATA_DIR.glob("*"))) == 0          # 校验在落盘之前
        assert db.stats()["files"] == 0

    def test_exactly_at_limit_is_accepted(self, client):
        resp = send_text(client, "字" * config.MAX_TEXT_CHARS)
        assert resp.status_code == 201
        assert resp.json()["chars"] == config.MAX_TEXT_CHARS

    def test_deliver_text_to_device(self, client):
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        make_group(client, sender, receiver)
        resp = send_text(client, "周会要点：1) 改接口 2) 加测试", targets=[receiver["id"]],
                         token=sender["token"], from_device_id=sender["id"], note="看完回我")

        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["transfer_count"] == 1
        assert payload["from_name"] == "发送方"
        assert payload["targets"] == [{"id": receiver["id"], "name": "收件方"}]

        items = inbox(client, receiver)["items"]
        assert [i["code"] for i in items] == [payload["code"]]
        assert items[0]["is_text"] is True
        assert items[0]["note"] == "看完回我"
        assert items[0]["from_name"] == "发送方"
        # 收件方能原样取回文字
        assert client.get(f"/api/download/{items[0]['code']}").content == "周会要点：1) 改接口 2) 加测试".encode("utf-8")

    def test_text_without_targets_does_not_touch_inbox(self, client):
        receiver = register(client, "收件方")
        send_text(client, "只给码", targets=[])
        assert inbox(client, receiver)["count"] == 0

    def test_bad_target_rejected_and_nothing_stored(self, client):
        resp = send_text(client, "hello", targets=["dev_NOPE1234"])
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "bad_target"
        assert len(list(config.DATA_DIR.glob("*"))) == 0

    def test_wrong_token_is_not_a_sender(self, client):
        """投递文本同样要实名：令牌不对 → 403，而不是顶着别人的名字发出去。"""
        sender, receiver = register(client, "发送方"), register(client, "收件方")
        resp = send_text(client, "hi", targets=[receiver["id"]], token="forged",
                         from_device_id=sender["id"], from_name="冒充者")
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "needs_device"

    def test_text_share_code_still_needs_no_device(self, client):
        """只生成分享码这条路不要求登记（谁都能用）。"""
        resp = send_text(client, "不需要登记也能发的文本")
        assert resp.status_code == 201
        assert resp.json()["from_device_id"] is None

    def test_ttl_seconds_wins_over_ttl(self, client):
        payload = send_text(client, "hi", ttl="7d", ttl_seconds=600).json()
        assert payload["ttl_seconds"] == 600
        assert payload["ttl_human"] == "10 分钟"

    def test_small_text_upload_also_flagged_as_text(self, client):
        """用户自己上传的小文本文件同样能内联显示，不只是 /api/texts 发的。"""
        resp = client.post("/api/upload", files={"file": ("笔记.md", b"# hi", "text/markdown")},
                           data={"ttl_seconds": "600"})
        info = client.get(f"/api/files/{resp.json()['code']}").json()
        assert info["is_text"] is True

    def test_binary_upload_is_not_text(self, client):
        resp = client.post("/api/upload", files={"file": ("blob.bin", b"\x00\x01" * 40000, "application/octet-stream")},
                           data={"ttl_seconds": "600"})
        info = client.get(f"/api/files/{resp.json()['code']}").json()
        assert info["is_text"] is False
