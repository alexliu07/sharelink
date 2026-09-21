"""设备组测试：建组/加入/退出/移除/解散、管理员权限边界，以及"投递必须实名 + 同组"。

这一组用例刻意全部走 HTTP 接口（跟浏览器、App 用同一套路径），只有"伪造历史数据"这类
准备动作才落库。核心规则：
* 任何已登记设备都能建组，创建者即管理员；
* 谁拿到组 id 谁就能加入（组 id 就是邀请凭证，"不要邀请"）；
* 管理员可移除成员/改名/解散，自己不能"退出"只能解散；
* 任何成员都能自己退出；
* 只有同组设备之间才能互传，且投递必须实名。
"""

from __future__ import annotations

import io

import pytest

from app import config, db, groups

UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"


def hdr(token: str = None) -> dict:
    headers = {"User-Agent": UA_MAC}
    if token:
        headers["X-Device-Token"] = token
    return headers


def register(client, name=None) -> dict:
    resp = client.post("/api/devices", json={"name": name}, headers=hdr())
    assert resp.status_code == 201, resp.text
    return resp.json()


def create_group(client, owner: dict, name="测试组"):
    return client.post("/api/groups", json={"device_id": owner["id"], "name": name}, headers=hdr(owner["token"]))


def join(client, device: dict, group_id: str):
    return client.post(f"/api/groups/{group_id}/join", json={"device_id": device["id"]}, headers=hdr(device["token"]))


def leave(client, device: dict, group_id: str):
    return client.post(f"/api/groups/{group_id}/leave", json={"device_id": device["id"]}, headers=hdr(device["token"]))


def my_groups(client, device: dict) -> dict:
    resp = client.get("/api/groups", params={"device_id": device["id"]}, headers=hdr(device["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


def group_detail(client, device: dict, group_id: str) -> dict:
    resp = client.get(f"/api/groups/{group_id}", params={"device_id": device["id"]}, headers=hdr(device["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


def send(client, sender, targets, content=b"payload", filename="a.txt", ttl="1h"):
    """以 sender 的身份投递（多目标）。"""
    ids = [targets] if isinstance(targets, str) else list(targets)
    return client.post(
        "/api/transfers",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        data={"targets": ",".join(ids), "ttl": ttl, "from_device_id": sender["id"]},
        headers=hdr(sender["token"]),
    )


def send_anonymous(client, targets, content=b"payload"):
    return client.post(
        "/api/transfers",
        files={"file": ("a.txt", io.BytesIO(content), "text/plain")},
        data={"targets": targets, "ttl": "1h"},
        headers=hdr(),
    )


def inbox(client, device: dict) -> dict:
    resp = client.get(f"/api/devices/{device['id']}/inbox", headers=hdr(device["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


def device_list(client, device: dict) -> dict:
    resp = client.get("/api/devices", params={"device_id": device["id"]}, headers=hdr(device["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestGroupId:
    """组 id 要跟分享码明显不同：grp_ 前缀 + 12 位。"""

    def test_new_group_id_shape(self, client):
        owner = register(client, "我")
        group = create_group(client, owner).json()["group"]

        assert group["id"].startswith("grp_")
        assert len(group["id"]) == len("grp_") + config.GROUP_ID_LENGTH == len("grp_") + 12
        body = group["id"][len("grp_") :]
        assert body.isalnum() and len(body) == 12
        assert not any(ch in body for ch in "IO01")          # 沿用分享码那套易混字符集
        assert len(group["id"]) != config.CODE_LENGTH        # 跟 8 位分享码长度不同
        assert group["is_owner"] is True
        assert group["my_role"] == "owner"
        assert group["member_count"] == 1

    def test_owner_becomes_member_automatically(self, client):
        owner = register(client, "我")
        group_id = create_group(client, owner).json()["group"]["id"]

        detail = group_detail(client, owner, group_id)
        assert [m["role"] for m in detail["members"]] == ["owner"]
        assert detail["members"][0]["is_self"] is True

    def test_name_defaults_and_cleaning(self, client):
        owner = register(client, "我")
        assert create_group(client, owner, name="   ").json()["group"]["name"] == groups.DEFAULT_GROUP_NAME

        cleaned = create_group(client, owner, name=" <b>家里的</b>  设备 ").json()["group"]["name"]
        assert cleaned == "b家里的/b 设备"          # 去掉尖括号、压掉多余空白
        assert "<" not in cleaned and ">" not in cleaned
        assert len(create_group(client, owner, name="长" * 100).json()["group"]["name"]) == config.GROUP_NAME_MAX_CHARS

    def test_creating_requires_token(self, client):
        owner = register(client, "我")
        resp = client.post("/api/groups", json={"device_id": owner["id"], "name": "x"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "bad_device_token"

    def test_group_id_generator_retries_on_collision(self):
        """生成器遇到"已被占用"的 id 必须换一个（不会覆盖别人的组）。"""
        seen = []

        def exists(candidate: str) -> bool:
            seen.append(candidate)
            return len(seen) <= 2                      # 假装前两次都撞了

        generated = groups.generate_group_id(exists)

        assert len(seen) == 3
        assert generated == seen[-1]
        assert all(len(candidate) == len("grp_") + 12 for candidate in seen)

    def test_too_many_groups_rejected(self, client, monkeypatch):
        monkeypatch.setattr(config, "MAX_GROUPS_PER_DEVICE", 2)
        owner = register(client, "我")
        assert create_group(client, owner).status_code == 201
        assert create_group(client, owner).status_code == 201

        resp = create_group(client, owner)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "too_many_groups"


class TestJoin:
    def test_join_by_group_id(self, client):
        owner, friend = register(client, "我的手机"), register(client, "朋友的手机")
        group_id = create_group(client, owner, "一起用").json()["group"]["id"]

        resp = join(client, friend, group_id)
        assert resp.status_code == 201, resp.text
        assert resp.json()["joined"] is True
        assert resp.json()["group"]["member_count"] == 2

        names = [m["name"] for m in group_detail(client, owner, group_id)["members"]]
        assert names == ["我的手机", "朋友的手机"]      # 管理员排最前
        assert [g["id"] for g in my_groups(client, friend)["groups"]] == [group_id]

    def test_join_is_idempotent(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        assert join(client, friend, group_id).status_code == 201

        again = join(client, friend, group_id)
        assert again.status_code == 200
        assert again.json()["already_member"] is True
        assert again.json()["joined"] is False
        assert again.json()["group"]["member_count"] == 2

    @pytest.mark.parametrize("pasted", ["{gid}", " {gid} ", "{lower}", "{bare}"])
    def test_pasted_format_is_normalized(self, client, pasted):
        """粘进来的组 id 允许大小写不同、带空格、省略 grp_ 前缀（但不会被"猜"成别的组）。"""
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        raw = pasted.format(
            gid=group_id, lower=group_id.lower(), bare=group_id[len("grp_") :], 
        )
        assert join(client, friend, raw).status_code == 201

    @pytest.mark.parametrize("bad", ["grp_SHORT", "ABCD1234", "hello", "grp_" + "I" * 12, "grp_" + "0" * 12])
    def test_bad_group_id_pasted(self, client, bad):
        """抄错/乱填的组 id → 404（不猜、不误入别人的组）。"""
        friend = register(client, "朋友")
        resp = join(client, friend, bad)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "group_not_found"

    def test_group_full(self, client, monkeypatch):
        monkeypatch.setattr(config, "MAX_GROUP_MEMBERS", 2)
        owner, friend, third = register(client, "我"), register(client, "朋友"), register(client, "第三台")
        group_id = create_group(client, owner).json()["group"]["id"]
        assert join(client, friend, group_id).status_code == 201

        resp = join(client, third, group_id)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "group_full"

    def test_outsider_cannot_see_members(self, client):
        owner = register(client, "我")
        outsider = register(client, "外人")
        group_id = create_group(client, owner).json()["group"]["id"]

        resp = client.get(f"/api/groups/{group_id}", params={"device_id": outsider["id"]}, headers=hdr(outsider["token"]))
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "not_a_member"

        assert join(client, outsider, group_id).status_code == 201
        assert group_detail(client, outsider, group_id)["group"]["member_count"] == 2

    def test_one_device_can_join_many_groups(self, client):
        owner_a, owner_b, me = register(client, "组长A"), register(client, "组长B"), register(client, "我")
        group_a = create_group(client, owner_a, "家里的").json()["group"]["id"]
        group_b = create_group(client, owner_b, "公司的").json()["group"]["id"]
        join(client, me, group_a)
        join(client, me, group_b)

        assert {g["name"] for g in my_groups(client, me)["groups"]} == {"家里的", "公司的"}
        # 共同组标签：两组的成员在"我"的设备页里都能看到，且各自标出共同的那个组
        listed = {d["name"]: d for d in device_list(client, me)["devices"]}
        assert set(listed) == {"我", "组长A", "组长B"}
        assert [g["name"] for g in listed["组长A"]["shared_groups"]] == ["家里的"]
        assert [g["name"] for g in listed["组长B"]["shared_groups"]] == ["公司的"]


class TestLeaveRemoveDissolve:
    def test_member_can_leave(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)

        resp = leave(client, friend, group_id)
        assert resp.status_code == 200
        assert resp.json()["left"] is True
        assert my_groups(client, friend)["count"] == 0
        assert group_detail(client, owner, group_id)["group"]["member_count"] == 1
        # 退出后再投递 → 不在同组
        assert send(client, friend, owner["id"]).status_code == 403

    def test_leave_when_not_member(self, client):
        owner, stranger = register(client, "我"), register(client, "路过的")
        group_id = create_group(client, owner).json()["group"]["id"]

        resp = leave(client, stranger, group_id)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "not_a_member"

    def test_owner_cannot_leave_only_dissolve(self, client):
        owner = register(client, "我")
        group_id = create_group(client, owner).json()["group"]["id"]

        resp = leave(client, owner, group_id)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "owner_must_dissolve"
        assert "解散" in resp.json()["detail"]["message"]
        assert db.get_group(group_id) is not None           # 组还在

    def test_owner_removes_member(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)

        resp = client.delete(
            f"/api/groups/{group_id}/members/{friend['id']}",
            params={"device_id": owner["id"]},
            headers=hdr(owner["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] is True
        assert my_groups(client, friend)["count"] == 0
        assert send(client, friend, owner["id"]).status_code == 403

    def test_non_owner_cannot_remove(self, client):
        owner, friend, other = register(client, "我"), register(client, "朋友"), register(client, "别的")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)
        join(client, other, group_id)

        resp = client.delete(
            f"/api/groups/{group_id}/members/{other['id']}",
            params={"device_id": friend["id"]},
            headers=hdr(friend["token"]),
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "not_owner"

    def test_owner_cannot_be_removed(self, client):
        owner = register(client, "我")
        group_id = create_group(client, owner).json()["group"]["id"]

        resp = client.delete(
            f"/api/groups/{group_id}/members/{owner['id']}",
            params={"device_id": owner["id"]},
            headers=hdr(owner["token"]),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "owner_cannot_be_removed"

    def test_remove_unknown_member(self, client):
        owner, stranger = register(client, "我"), register(client, "没进组的")
        group_id = create_group(client, owner).json()["group"]["id"]

        resp = client.delete(
            f"/api/groups/{group_id}/members/{stranger['id']}",
            params={"device_id": owner["id"]},
            headers=hdr(owner["token"]),
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "not_a_member"

    def test_rename_group(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner, "旧名").json()["group"]["id"]
        join(client, friend, group_id)

        assert client.patch(f"/api/groups/{group_id}", json={"device_id": friend["id"], "name": "偷偷改名"},
                            headers=hdr(friend["token"])).status_code == 403

        resp = client.patch(f"/api/groups/{group_id}", json={"device_id": owner["id"], "name": "  新名字  "},
                            headers=hdr(owner["token"]))
        assert resp.status_code == 200
        assert resp.json()["group"]["name"] == "新名字"

    def test_dissolve(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)
        assert send(client, friend, owner["id"]).status_code == 201      # 解散前能互传

        assert client.delete(f"/api/groups/{group_id}", params={"device_id": friend["id"]},
                             headers=hdr(friend["token"])).status_code == 403

        resp = client.delete(f"/api/groups/{group_id}", params={"device_id": owner["id"]},
                             headers=hdr(owner["token"]))
        assert resp.status_code == 200
        assert resp.json()["dissolved"] is True and resp.json()["member_count"] == 2

        assert db.get_group(group_id) is None
        assert my_groups(client, friend)["count"] == 0
        assert send(client, friend, owner["id"]).status_code == 403
        assert inbox(client, owner)["count"] == 1        # 已经收到的文件不受影响

    def test_deleting_owner_device_dissolves_group(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)

        assert client.delete(f"/api/devices/{owner['id']}", headers=hdr(owner["token"])).status_code == 200
        assert db.get_group(group_id) is None            # 外键级联：管理员注销 → 组解散
        assert my_groups(client, friend)["count"] == 0


class TestDeliveryPermission:
    """投递规则：必须实名，且每台目标都与自己同组。"""

    def test_anonymous_delivery_rejected(self, client):
        owner = register(client, "收件方")
        resp = send_anonymous(client, owner["id"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "needs_device"
        assert db.stats()["files"] == 0 and db.stats()["transfers"] == 0   # 校验在落盘前

    def test_forged_token_rejected(self, client):
        owner = register(client, "收件方")
        resp = client.post(
            "/api/transfers",
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
            data={"targets": owner["id"], "ttl": "1h", "from_device_id": owner["id"]},
            headers={"X-Device-Token": "forged", "User-Agent": UA_MAC},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "needs_device"

    def test_same_group_delivery_allowed(self, client):
        owner, friend = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, owner).json()["group"]["id"]
        join(client, friend, group_id)

        resp = send(client, owner, friend["id"])
        assert resp.status_code == 201, resp.text
        assert resp.json()["from_name"] == "我"
        assert [i["code"] for i in inbox(client, friend)["items"]] == [resp.json()["code"]]

    def test_different_group_rejected(self, client):
        me, stranger = register(client, "我"), register(client, "别人组里的")
        mine = create_group(client, me, "我的组").json()["group"]["id"]
        theirs = create_group(client, stranger, "别人的组").json()["group"]["id"]
        assert mine != theirs

        resp = send(client, me, stranger["id"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "not_in_same_group"
        assert "别人组里的" in resp.json()["detail"]["message"]        # 说清楚是谁不在组里
        assert db.stats()["files"] == 0 and db.stats()["transfers"] == 0

    def test_one_bad_target_rejects_everything(self, client):
        me, mate, stranger = register(client, "我"), register(client, "同组的"), register(client, "不同组的")
        group_id = create_group(client, me).json()["group"]["id"]
        join(client, mate, group_id)

        resp = send(client, me, [mate["id"], stranger["id"]])
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "not_in_same_group"
        assert db.stats()["files"] == 0                     # 一台不合格 → 整单不落盘
        assert inbox(client, mate)["count"] == 0

    def test_text_delivery_follows_same_rule(self, client):
        me, mate, stranger = register(client, "我"), register(client, "同组的"), register(client, "不同组的")
        group_id = create_group(client, me).json()["group"]["id"]
        join(client, mate, group_id)

        ok = client.post("/api/texts", json={"text": "喂", "targets": [mate["id"]], "from_device_id": me["id"]},
                         headers=hdr(me["token"]))
        assert ok.status_code == 201, ok.text
        assert inbox(client, mate)["items"][0]["is_text"] is True

        bad = client.post("/api/texts", json={"text": "喂", "targets": [stranger["id"]], "from_device_id": me["id"]},
                          headers=hdr(me["token"]))
        assert bad.status_code == 403
        assert bad.json()["detail"]["error"] == "not_in_same_group"

    def test_self_delivery_needs_no_group(self, client):
        """发给自己的收件箱不需要组（没有第三方参与），但依然要实名。"""
        me = register(client, "我")
        assert my_groups(client, me)["count"] == 0

        resp = send(client, me, me["id"], content="给自己留一份".encode("utf-8"))
        assert resp.status_code == 201, resp.text
        assert [i["code"] for i in inbox(client, me)["items"]] == [resp.json()["code"]]

    def test_share_code_path_needs_no_device(self, client):
        """不建组、不登记，照样能用分享码收文件（有意保留的逃生舱）。"""
        uploaded = client.post("/api/upload", files={"file": ("a.txt", b"hello", "text/plain")}, data={"ttl": "1h"})
        assert uploaded.status_code == 201
        code = uploaded.json()["code"]
        assert client.get(f"/api/download/{code}").content == b"hello"

    def test_device_list_scoped_to_groups(self, client):
        me, mate, stranger = register(client, "我"), register(client, "同组的"), register(client, "不同组的")
        group_id = create_group(client, me, "我的组").json()["group"]["id"]
        join(client, mate, group_id)

        listed = device_list(client, me)
        assert [d["name"] for d in listed["devices"]] == ["我", "同组的"]
        assert listed["self_id"] == me["id"]
        assert listed["devices"][0]["shared_groups"] == []          # 自己那条不标"共同组"
        assert [g["name"] for g in listed["devices"][1]["shared_groups"]] == ["我的组"]

        # 从伴侣设备看：反过来的关系也成立
        listed = device_list(client, mate)
        assert [d["name"] for d in listed["devices"]] == ["同组的", "我"]

        # 不认识的人：完全看不到
        assert device_list(client, stranger)["count"] == 1
        assert device_list(client, stranger)["devices"][0]["is_self"] is True

    def test_remove_then_delivery_rejected(self, client):
        me, mate = register(client, "我"), register(client, "朋友")
        group_id = create_group(client, me).json()["group"]["id"]
        join(client, mate, group_id)
        assert send(client, me, mate["id"]).status_code == 201

        client.delete(f"/api/groups/{group_id}/members/{mate['id']}",
                      params={"device_id": me["id"]}, headers=hdr(me["token"]))
        resp = send(client, me, mate["id"])
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "not_in_same_group"


class TestStats:
    def test_stats_expose_group_limits(self, client):
        register(client, "我")
        stats = client.get("/api/stats").json()

        assert stats["groups"] == 0
        assert stats["group_id_length"] == 12
        assert stats["max_groups_per_device"] == config.MAX_GROUPS_PER_DEVICE
        assert stats["max_group_members"] == config.MAX_GROUP_MEMBERS
