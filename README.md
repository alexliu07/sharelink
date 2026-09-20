# ShareLink · 文件共享（分享码取件）

上传文件 → 设置有效期限 → 后端返回 **分享码** → 别人凭分享码下载 → **到期后文件自动删除**。

## 特性

- 上传任意文件，自选有效期（10 分钟 / 1 小时 / 1 天 / 7 天 / 30 天，或自定义）
- 后端返回 8 位分享码（字符集去掉 `I/O/0/1` 等易混字符），输入时自动容错大小写与 `-`/空格/`.`
- 凭分享码取件：文件名、大小、剩余有效时间对取件人可见，下载计数入库
- **到期自动删除**：后台定时任务扫描清理 + 取件时惰性校验双重保障（无人访问也会删）
- 上传即返回 `share_url`，点开自动切到取件面板并填好分享码
- 纯 HTML/CSS/JS 前端，无框架、无外网依赖；元数据落 SQLite，文件落磁盘，单机即可跑
- 文件名清洗（去掉路径部分与非法字符）、落盘随机名、UTF-8 附件名、`nosniff` 防误执行

## 快速开始

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 <http://127.0.0.1:8000/> 即可使用。

## 命令行用法

```bash
# 上传（返回分享码）→ {"code":"AB3D7K9M", ...}
curl -F "file=@report.pdf" -F "ttl=1h" http://127.0.0.1:8000/api/upload

# 查信息 / 下载 / 提前删除
curl http://127.0.0.1:8000/api/files/AB3D7K9M
curl -OJ http://127.0.0.1:8000/api/download/AB3D7K9M
curl -X DELETE http://127.0.0.1:8000/api/files/AB3D7K9M
```

## HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/upload` | multipart 表单：`file`（文件）、`ttl`（`30m`/`6h`/`7d`/秒数）或 `ttl_seconds`。成功 `201`，返回分享码与分享链接 |
| `GET` | `/api/files/{code}` | 分享信息（文件名、大小、SHA256、剩余秒数、下载次数） |
| `GET` | `/api/download/{code}` | 下载文件（`Content-Disposition: attachment`，中文名走 RFC 5987） |
| `DELETE` | `/api/files/{code}` | 提前取消分享（分享码即凭证），立即删文件与记录 |
| `GET` | `/api/stats` | 站点统计（分享数、占用空间、限额） |
| `GET` | `/api/healthz` | 存活探针 |

错误码：`400` 分享码格式/有效期非法 · `404` 分享码不存在 · `410` 已过期（响应同时删除文件） · `413` 超过单文件上限。
错误体统一为 `{"detail": {"error": "...", "message": "..."}}`。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SHARELINK_DATA_DIR` | `./storage` | 上传文件存放目录 |
| `SHARELINK_DB_PATH` | `./data/sharelink.db` | SQLite 元数据库路径 |
| `SHARELINK_MAX_UPLOAD_MB` | `200` | 单文件大小上限 |
| `SHARELINK_DEFAULT_TTL_SECONDS` | `3600` | 未指定有效期时的默认值（秒） |
| `SHARELINK_MIN_TTL_SECONDS` | `60` | 允许的最短有效期 |
| `SHARELINK_MAX_TTL_SECONDS` | `2592000` | 允许的最长有效期（30 天） |
| `SHARELINK_CLEANUP_INTERVAL_SECONDS` | `60` | 后台清理间隔（秒） |
| `SHARELINK_PUBLIC_BASE_PATH` | 空 | 对外访问的路径前缀：反代挂在 `https://host/share/` 下就设为 `/share`，返回的 `share_url`/`download_url` 会带上它 |

## 测试

```bash
.venv/bin/python -m pytest -q          # 71 个用例：接口往返、分享码、过期删除、安全边界
```

覆盖点：上传/下载字节与 SHA256 一致、大小写与 `-` 容错、404/400/410/413 分支、
过期后文件与元数据都被删除、后台清理协程真跑一遍、路径穿越文件名被中和、
磁盘文件名不含原始名字、删除接口幂等。

## 部署建议

```ini
# /etc/systemd/system/sharelink.service
[Unit]
Description=ShareLink
After=network.target

[Service]
WorkingDirectory=/opt/sharelink
Environment=SHARELINK_DATA_DIR=/var/lib/sharelink/storage
Environment=SHARELINK_DB_PATH=/var/lib/sharelink/data/sharelink.db
ExecStart=/opt/sharelink/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
User=sharelink

[Install]
WantedBy=multi-user.target
```

Nginx 反代时记得放开上传体积（要和 `SHARELINK_MAX_UPLOAD_MB` 对齐）：`client_max_body_size 200m;`

## 安全须知

- **分享码就是唯一凭证**：没有账号体系，知道分享码即可下载或删除。8 位 / 32 字符集 ≈ 1.1e12 组合，适合小范围分享；请勿用于机密数据。
- 生产环境建议只走 HTTPS，并按需在反向代理层加限流。
- 上传的 HTML/SVG 一律以附件形式下发并带 `X-Content-Type-Options: nosniff`，避免同源 XSS。

## 目录结构

```
app/        后端（FastAPI）
  config.py   环境变量与常量
  codes.py    分享码生成 / 规范化 / 有效期解析
  db.py       SQLite 元数据层
  storage.py  文件落盘 / 删除 / 校验
  cleanup.py  过期清理（惰性 + 后台定时）
  main.py     HTTP 接口与静态页面挂载
static/     前端页面（index.html / style.css / app.js）
tests/      pytest 用例
```
