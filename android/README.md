# ShareLink 安卓小工具

把别的应用「分享」过来的文件或链接直接交给自建 ShareLink 服务，换回分享码。
存在的意义：安卓 Chrome 生成 WebAPK 需要连 Google 的服务器，国产 ROM／无 Google 服务的机器上会静默退化成
「网页快捷方式」，而快捷方式**永远不会**注册成系统分享目标——PWA 自带的 `share_target` 在那类机器上没戏，
所以用这个几十 KB 的原生小应用顶上。

## 它做什么

- 注册成系统分享目标（`ACTION_SEND` / `ACTION_SEND_MULTIPLE`，`*/*`）：在相册、文件管理、浏览器里点分享，选 ShareLink 即可
- 把第一个文件（或分享的文字/链接）以 `multipart/form-data` POST 到 `https://skylare.me/share/api/share-target?response=json`
- 拿到 JSON 后显示**分享码**（自动复制到剪贴板）＋落地页链接，可一键打开或转发给别人
- 从桌面图标点开则显示说明页；上传失败（超限、断网、服务端报错）会显示服务端返回的原因并给「重试」
- **上传有效期**可自己选（主页「上传有效期」卡片：10 分钟 / 1 小时 / 1 天 / 7 天 / 30 天，与网页版同一套档位），
  选择记在本地；分享面板里上传的文件也用这个值（上传时带 `ttl_seconds`，结果页和收件箱都显示到期时间）
- **凭分享码下载**（主页「凭分享码下载」卡片）：输入 8 位分享码点「下载」，先 `GET /api/files/{code}` 看信息，
  再 `GET /api/download/{code}` 下载，文件名/MIME 从下载响应的 `Content-Disposition` / `Content-Type` 取，
  存进系统「下载」目录并交给系统应用打开。这条链路不需要设备令牌（没登记设备也能用）；
  失败会按状态码给中文原因（404 没这个码 / 410 已过期 / 网络失败）

## 设计与实现要点

- **零第三方依赖**，连 AndroidX 都不用：只用框架 API + `HttpURLConnection` + `org.json`。CI 不用解析依赖，APK 只有几十 KB
- **不用 `Content-Length` 靠猜**：能拿到文件大小（`OpenableColumns.SIZE`）时用 `setFixedLengthStreamingMode` 精确声明长度，
  拿不到才退回 chunked，避免经 Cloudflare 时出幺蛾子
- **文件名清洗**：multipart header 里不能有引号/换行，截断到 180 字符；UTF-8 原名（含中文）能正常传到服务端
- **端点是绝对 URL**，并且带 `?response=json`，所以拿到的是 JSON 而不是浏览器用的 303 跳转
- **按钮/图标留白自己兜住**：换成自定义背景后系统按钮那层 9-patch 的内边距就没了，
  而各 ROM 默认内边距差别很大（有的让文字贴着边框）→ 统一在 `button()` 里给 18×11dp 内边距、46dp 最小高度、
  行内 10dp 右边距；标题行用 `layout_weight=1` 把按钮顶到右边（标题与按钮之间就有间距），
  长标签设 `setSingleLine(true)` 免得折成两行。自适应图标前景只占画布 47%（安全区是中间 72/108），
  太大在圆/圆角遮罩里看着像标志顶到边
- 图标与网页端同源：`mipmap-*/ic_launcher.png` 由 `static/favicon.svg` 渲染，
  自适应图标前景层来自 `icons/ic_launcher_foreground.svg`，都用 `tools/render-icons.py` 生成（安卓不认 SVG）

## 构建（在 GitHub Actions 里，本机不需要 Android SDK）

推送到 `android/**` 会自动跑 `.github/workflows/android-apk.yml`：装 JDK 17 + Android SDK，`gradle assembleRelease`，
然后把 `ShareLink.apk` 挂到 Release（tag `android-latest`）：

```
https://github.com/alexliu07/sharelink/releases/download/android-latest/ShareLink.apk
```

签名用的是仓库里 `keystore/debug.keystore`（首次构建时自动生成并提交回来，密码是调试签名的公开值 `android`）。
**这样做的目的是让签名固定**，否则 CI 每次生成新签名，装新版本就得先卸载。

当前签名证书指纹（sha256，每次发版应保持不变）：

```
D2:46:8E:45:C0:2C:75:3F:51:F1:10:FF:B0:48:CA:6C:B7:6C:46:0B:29:A8:B8:09:B2:FF:0D:B4:84:88:A2:9D
```

校验（不用安卓设备）：

```bash
python3 android/tools/verify_release.py D2:46:8E:45:C0:2C:75:3F:51:F1:10:FF:B0:48:CA:6C:B7:6C:46:0B:29:A8:B8:09:B2:FF:0D:B4:84:88:A2:9D
```

CI 自己也会自检（`apksigner verify --print-certs` 的结果必须等于 `keytool -list -v` 的指纹），签名一漂就红。

**构建脚本里两个必须写死的点**（踩过坑）：
- 不能用 `signingConfigs.getByName("debug")`：它的语义是「用 `~/.android/debug.keystore`，没有或者**别名对不上就自己造一张**」，
  而它期望的别名是大写 `AndroidDebugKey`、我们 `keytool` 生成的是小写 `androiddebugkey` —— 于是它把仓库 keystore 覆盖重造，
  每次构建换一把密钥（实测三次构建指纹各不相同）。现在改成显式 `signingConfigs.create("release-key")`，
  `storeFile = rootProject.file("keystore/debug.keystore")`、`storeType = "PKCS12"`、`keyAlias = "androiddebugkey"`
- 不能用 `android-actions/setup-android@v3`：它在新 runner 上会去装已淘汰的 `tools` 包而失败（`Failed to find package 'tools'`），
  workflow 里直接用镜像自带的 `/usr/local/lib/android/sdk`

介意的可以换成自己的 keystore：替换该文件并同步别名/口令即可，代价是已装的版本要卸载重装一次。

本地构建（有 Android SDK 的机器上）：

```bash
cd android && gradle assembleRelease     # 产物在 app/build/outputs/apk/release/
```

## 设备互传（App 就是一台设备）

装完 App 在主页可以「登记这台设备」（`POST /api/devices`，拿到 `id` + `token` 存在本机），之后：

- **收**：主页收件箱列出别的设备发来的文件（`GET /api/devices/{id}/inbox` + `X-Device-Token`），
  显示文件名 / 大小 / 谁发的 / 剩余有效期；点一下下载并交给系统打开（安卓 10+ 存进系统「下载」目录，
  不需要任何存储权限，用 MediaStore 的 `content://` URI 打开）；长按可移出收件箱（只删投递记录，文件还在）
- **发**：分享文件时先问"要发给哪台设备吗"（多选，不选＝只生成分享码），
  选中就走 `POST /api/transfers`（`file` + `targets` + `from_device_id` + `note`，带自己的令牌）——
  **一次上传同时拿到分享码和投递**，不会为了投递再传一遍大文件
- 主页「选择文件发给设备」：不用分享面板也能发（`ACTION_GET_CONTENT` 选文件 → 勾设备 → 投递）。
  投递目标用**自建勾选列表**（`ScrollView` + 一行一个 `CheckBox`）而不是系统的 `setMultiChoiceItems`：
  部分 ROM 上那种列表会整片不渲染，用户只看到标题和按钮、点「确定」就静默变成"只拿分享码"（等于没发出去）。
  现在主按钮叫「发送给选中的设备」，一台都没勾时只提示、不发请求也不关对话框；没有别的设备时也会提示一句
- **设备组（v1.13）**：首页「设备组」卡 → 创建设备组（创建者即管理员）/ 粘贴组 id 加入 / 点「管理」
  看成员名单并操作（复制组 id、管理员移除成员·改组名·解散、成员退出）。投递前服务端会检查"每台目标都与自己同组"，
  所以选择设备时每台后面会标出「共同设备组」；不同组会拿到 403 not_in_same_group 的中文提示。
  设备列表接口现在要带 `device_id` + 令牌，只返回「自己 + 同组设备」。
- **发文本**：主页「发文本」卡（输入框 + 字符计数，超上限直接禁用并标红）→ 「发送这段文本」→
  登记过设备就先弹目标选择框（含「只拿分享码」），没登记就直接拿码；走 `POST /api/texts`（JSON，不是 multipart），
  服务端存成小 `text/plain`，所以分享码、有效期、投递、到期清理全都复用文件那套。
  取件卡与收件箱里凡是 `is_text` 的条目都不再走「先下载再打开」，而是直接拉回内容弹窗显示（可选中复制，也能存成 .txt）
- **上传的 multipart 边界**：带文件时字段块写在文件字节后面，边界必须是 `CRLF--boundary`——
  少这个 CRLF 服务端会把字段块当成文件内容（`ttl_seconds` 静默丢失 → 有效期回落到默认 1 小时，
  落盘文件尾部还多几十字节垃圾）。回归脚本 `scripts/live_multipart_check.py` 对真实服务端发新旧两种布局对照
- 还有：改名字（`PATCH /api/devices/{id}`，`HttpURLConnection` 不认 PATCH，靠反射塞方法名）、
  注销设备、导出令牌到剪贴板、从剪贴板导入令牌（导入时先读一次收件箱验证令牌有效才保存，
  和网页端的做法一致；两边格式互通：`{sharelink_device:1, id, name, token}`）

新文件提醒只做「打开 App 时拉一次」，没做后台轮询/通知（省电、不用通知权限）。

### 设备互传这一段怎么验证的

`scripts/live_device_transfer_check.py` 把 App 会发的请求（含 multipart 字节布局、`X-Device-Token` 头）
逐字节复刻后打真实服务端，跑完整链路：登记两台设备 → 投递（校验实名发送者、附言、中文文件名）→
读收件箱（校验 `from_name`/`sent_at`/`seen`/`download_url`/`seconds_left`）→ 下载并比对字节 →
标记已读 → 移出 → 改名 → 注销 → 校验设备数回到跑之前。当前 **29 项全过**。

**仍未验证**：真机上的实际交互（收件箱列表渲染、下载后用系统应用打开、MediaStore 写入在国行 ROM 上的行为）。

### 「凭分享码下载」这一段怎么验证的

`scripts/live_code_download_check.py` 复刻 App 的两条请求（`GET /api/files/{code}` → `GET /api/download/{code}`）
打真实服务端，并用**与 `Api.filenameOf` 同构的解析逻辑**去解真实的 `Content-Disposition`：

- 中文名走 Starlette 的 RFC 5987 形式（`attachment; filename*=utf-8''%E5%87%AD…`），必须能解回 `凭码下载自检-中文名.txt`；
  ASCII 名走 `filename="plain-name.txt"` 分支；`Content-Type: text/plain; charset=utf-8` 去参数后作 MIME
- 输入清洗与格式校验按 `MainActivity.normalizeCode` / `isCodeShaped` 的等价实现断言（`ab3d-7k9m` → `AB3D7K9M`，
  含易混字符 `I` 的码本地就该拦）
- 失败分支按真实状态码核对：不存在的码 404（带中文原因）、格式不对 400
- 跑完删掉测试分享码并复查 404，不留垃圾。当前 **23 项全过**（打 `http://127.0.0.1/share`，即 nginx → uvicorn）

> 注意：`headers.get("Content-Disposition")` 这类**大小写敏感**的取法会取不到（Starlette 发的是小写头名），
> 复刻脚本里要按大小写不敏感取（`HttpURLConnection.getHeaderField` 本来就是不敏感的）。

**仍未验证**：真机上点这个卡片（输入、下载、`MediaStore` 写入与「用什么应用打开」的系统选择）。

## 已验证 / 未验证（构建与上传部分）

- 已验证：Java 编译通过、APK 由 CI 构建并发布；App 会发出的 multipart 字节（含中文文件名、title/text 字段、
  固定 Content-Length）被逐字节复刻后打到线上接口，返回 200 与正确的分享码
- **未验证**：真机行为（本机没有安卓设备也没有模拟器）。分享面板能否出现、文件读取权限、大文件上传表现都要装到手机上实测
