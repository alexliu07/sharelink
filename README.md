# ShareLink · 文件共享（分享码取件）

上传文件 → 设置有效期限 → 后端返回 **分享码** → 别人凭分享码下载 → **到期后文件自动删除**。

## 特性

- 上传任意文件，自选有效期（10 分钟 / 1 小时 / 1 天 / 7 天 / 30 天，或自定义）
- 后端返回 8 位分享码（字符集去掉 `I/O/0/1` 等易混字符），输入时自动容错大小写与 `-`/空格/`.`
- 凭分享码取件：文件名、大小、剩余有效时间对取件人可见，下载计数入库
- **设备互传**：把浏览器登记成"设备"→ 上传页点"发送至设备"→ 勾选一台或多台目标设备直接投递；
  目标设备打开网页即可在"设备"标签页看到文件与过期时间（标签上有未读角标）
- **可装成手机应用（PWA）**：安卓 Chrome 直接装成独立应用，还能用系统分享面板把文件"分享 → ShareLink"；
  iOS Safari 添加到主屏幕；设备令牌可导出/导入，换机或清缓存都能找回
- **到期自动删除**：后台定时任务扫描清理 + 取件时惰性校验双重保障（无人访问也会删）；
  投递记录跟着文件一起清掉，收件箱不会留下取不到的死条目
- 上传即返回 `share_url`，点开自动切到取件面板并填好分享码
- 纯 HTML/CSS/JS 前端，无框架、无外网依赖；图标全部为手绘 SVG（无 emoji / 无图标字体）
- 元数据落 SQLite，文件落磁盘，单机即可跑；文件名清洗、落盘随机名、UTF-8 附件名、`nosniff` 防误执行

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
| `GET` | `/api/stats` | 站点统计（分享数、占用空间、设备数、限额） |
| `GET` | `/api/healthz` | 存活探针 |

### 设备互传（不需要账号，令牌 = 凭证）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/devices` | 把当前浏览器登记成一台设备（JSON `{name?}`，留空按 User-Agent 猜）；返回设备 `id` 与 `token`，**token 只返回这一次** |
| `GET` | `/api/devices` | 设备列表（公开，供选择投递目标）：名称、最后活跃、收件箱条数 |
| `PATCH` | `/api/devices/{id}` | 改设备名（需 `X-Device-Token`） |
| `DELETE` | `/api/devices/{id}` | 注销设备（需令牌），其收件箱记录一并删除 |
| `GET` | `/api/devices/{id}/inbox` | **我的收件箱**（需令牌）：别人发来的文件 + 文件名的过期时间/剩余秒数/发送方/附言，同时刷新"最后活跃" |
| `POST` | `/api/devices/{id}/inbox/seen` | 收件箱标为已读（需令牌） |
| `DELETE` | `/api/devices/{id}/inbox/{code}` | 从自己的收件箱移掉一条（需令牌；文件与分享码不受影响） |
| `POST` | `/api/transfers` | 发送至设备：multipart `file` + `targets`（设备 id 逗号分隔，可多选）+ `ttl`/`ttl_seconds` + `note` + 可选 `from_device_id`/`from_name` |

命令行示例：

```bash
# 登记本设备（拿到 id 与 token，token 只出现这一次）
curl -X POST -H 'Content-Type: application/json' -d '{"name":"我的笔记本"}' http://127.0.0.1:8000/api/devices

# 看设备列表，挑目标
curl http://127.0.0.1:8000/api/devices

# 一次发给两台设备（发送方令牌可选，带上才会显示真实设备名）
curl -F "file=@report.pdf" -F "targets=dev_AB3D7K9M,dev_CD4E8L2N" -F "ttl=1h" \
     -F "note=周会材料" -F "from_device_id=dev_AB3D7K9M" \
     -H "X-Device-Token: <发送方 token>" http://127.0.0.1:8000/api/transfers

# 目标设备看自己的收件箱
curl -H "X-Device-Token: <目标设备 token>" http://127.0.0.1:8000/api/devices/dev_CD4E8L2N/inbox
```

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
| `SHARELINK_DEVICE_IDLE_DAYS` | `30` | 设备多久没活跃（且收件箱为空）就从列表回收（天） |
| `SHARELINK_MAX_TARGETS_PER_SEND` | `20` | 一次"发送至设备"最多选多少台目标 |
| `SHARELINK_PUBLIC_BASE_PATH` | 空 | 对外访问的路径前缀：反代挂在 `https://host/share/` 下就设为 `/share`，返回的 `share_url`/`download_url` 会带上它 |

## 设备互传怎么用

**发送方**

1. 打开上传页选好文件、选好有效期；
2. 点「发送至设备」→ 弹出设备列表，勾选一台或多台（可只发一台，也可群发）；
3. 可填附言；如果你自己已在"设备"标签页登记过，发送方名字自动用你的设备名，否则可手填（默认"匿名设备"）；
4. 点「发送给 N 台设备」，成功后提示发送给了哪些设备、分享码与到期时间。
   落盘只有一份文件，多台设备共用同一个分享码，不会按设备数复制。

**接收方**

1. 打开网站 → 「设备」标签页 → 首次点「添加本设备」（名字可留空，后端按浏览器/系统猜一个）；
2. 之后该标签页就列出别人发来的文件：文件名、大小、来自谁、附言、**剩余有效时间（每秒变化）**；
3. 点「下载」直接取件；「从收件箱移除」只清掉这条记录，分享码仍然可用；
4. 设备标签上有未读角标，打开标签页即自动标记已读；页面开着时每 20 秒自动刷新一次。

**凭证模型**：登记设备时后端返回 `token`，浏览器存在 `localStorage`，库里只存其 SHA256；
换个浏览器/清掉存储 = 需要重新登记（旧设备会在"设备列表"里留着，直到注销或 30 天不活跃被回收）。
文件过期时投递记录跟着一起删，收件箱不会有取不到的条目。

## 装成手机应用（PWA）

已经内置，不需要打包、不需要上架、不需要 Apple 开发者账号：

- `static/manifest.webmanifest`：名称、图标（192/512 + maskable）、`display: standalone`、`start_url`，以及安卓的 **share_target**（分享面板入口）
- `static/icons/app-icon.svg` / `app-icon-maskable.svg`：**手绘 SVG 源**；`static/icons/*.png` 是 `scripts/render-icons.py` 渲染出来的构建产物（iOS 的 `apple-touch-icon` 和安卓安装条件只认 PNG）
- `static/sw.js`：service worker，静态资源**网络优先**（部署后刷新即最新，不会卡在旧缓存），
  `/api/*` 一律直连不缓存（文件有"过期即删"语义），断网时回退到缓存的外壳
- 设备标签页里的**安装引导**：安卓/桌面 Chrome 走 `beforeinstallprompt` 按钮，iOS Safari 给「分享 → 添加到主屏幕」步骤，已装（`display-mode: standalone`）时自动隐藏
- 设备标签页里的**导出/导入令牌**：换手机或 iOS 清理站点存储后，用导出的 JSON 找回设备（导入时会先读一次收件箱校验令牌，错的令牌不会被存下来）

**装法**：安卓 Chrome 打开站点 → 菜单「安装应用」（或页面上点「装到本设备」）；iOS Safari → 分享 → 添加到主屏幕。
装完是独立图标 + 全屏窗口。安卓还会多出「分享 → ShareLink」：在文件管理器/微信里分享文件，直接上传并跳回页面显示分享码。

**Cloudflare 侧必须注意的三点**（踩过的坑）：

1. `Caching → Configuration → Browser Cache TTL` 必须是 **Respect Existing Headers**。
   默认的 4 小时会**覆盖**源站的 `Cache-Control`，连 `sw.js` 的 `no-cache` 都被改写成 `max-age=14400`，
   结果 service worker 更新要等 4 小时才生效（源站已发 `no-cache`，可直连 `curl -I http://127.0.0.1:18000/sw.js` 核对）。
2. 免费版请求体上限 **100MB**，所以服务端 `SHARELINK_MAX_UPLOAD_MB` 设成 **95**（留 multipart 余量），
   这样超大文件由我们自己返回友好提示，而不是 CF 的错误页。
3. 确认没有对 `/api/*`（含 `/share/api/*`）设 `Cache Everything` / 缓存规则。

重新生成图标（改了 SVG 之后）：

```bash
uv venv /tmp/svgvenv && uv pip install --python /tmp/svgvenv/bin/python cairosvg pillow
/tmp/svgvenv/bin/python scripts/render-icons.py --sheet /tmp/icons.png   # --sheet 出一张裁切预览图
```

## 测试

```bash
.venv/bin/python -m pytest -q                 # 112 个用例：分享码取件 + 设备互传
.venv/bin/python scripts/check_frontend.py    # 静态检查：DOM id / sprite / emoji / 标签页→面板映射

# 真 DOM 测试：用 jsdom 加载 index.html 并执行 app.js，模拟点击标签页与发送流程
cd scripts && npm install && node dom_test.js
```

> 只做静态检查会漏掉"标签页切过去但面板不显示"这类问题（`switchTab()` 是按 `els.panels`
> 里的登记切换 `.active` 的，新增标签页忘了登记就整块 `display:none`）。
> `check_frontend.py` 现在会校验每个 `data-panel` 都有登记，`dom_test.js` 则真点一遍。

覆盖点：上传/下载字节与 SHA256 一致、大小写与 `-` 容错、404/400/410/413 分支、
过期后文件与元数据都被删除、后台清理协程真跑一遍、路径穿越文件名被中和、
磁盘文件名不含原始名字、删除接口幂等；设备侧覆盖令牌鉴权（无/错/未知设备 403/404）、
一次投递多台设备、收件箱隔离与已读、过期后收件箱清空、注销设备级联删记录、
不活跃设备回收、投递目标不存在/超上限/空目标等边界。

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
- **设备令牌只在登记它的浏览器里**（localStorage）：库里只存令牌的 SHA256，泄库也拿不到可用令牌；
  令牌丢了就重新登记一台设备，旧设备可以在设备页注销。
- **设备列表是公开的**：任何能打开本站的人都能看到设备名并向任一设备投递文件（类似 AirDrop 的"对所有人"）；
  反向代理层按来源 IP 限制访问范围（例如只允许内网/校园网）比在应用里做鉴权更简单有效。
- 生产环境建议只走 HTTPS，并按需在反向代理层加限流。
- 上传的 HTML/SVG 一律以附件形式下发并带 `X-Content-Type-Options: nosniff`，避免同源 XSS；
  前端渲染设备名/文件名/附言时统一走 HTML 转义。

## 目录结构

```
app/        后端（FastAPI）
  config.py   环境变量与常量
  codes.py    分享码生成 / 规范化 / 有效期解析
  db.py       SQLite 元数据层（files / devices / transfers）
  devices.py  设备令牌、名称清洗、目标规范化
  storage.py  文件落盘 / 删除 / 校验
  cleanup.py  过期清理（惰性 + 后台定时）与设备回收
  main.py     HTTP 接口与静态页面挂载
static/     前端页面（index.html / style.css / app.js / sw.js / manifest.webmanifest / favicon.svg）
  icons/      应用图标：手绘 SVG 源 + 渲染出的 PNG（安卓/iOS 装成应用时用）
scripts/    check_frontend.py 静态接线检查 · dom_test.js jsdom 真 DOM 测试 · render-icons.py 渲染图标
tests/      pytest 用例
```
