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

## 设计与实现要点

- **零第三方依赖**，连 AndroidX 都不用：只用框架 API + `HttpURLConnection` + `org.json`。CI 不用解析依赖，APK 只有几十 KB
- **不用 `Content-Length` 靠猜**：能拿到文件大小（`OpenableColumns.SIZE`）时用 `setFixedLengthStreamingMode` 精确声明长度，
  拿不到才退回 chunked，避免经 Cloudflare 时出幺蛾子
- **文件名清洗**：multipart header 里不能有引号/换行，截断到 180 字符；UTF-8 原名（含中文）能正常传到服务端
- **端点是绝对 URL**，并且带 `?response=json`，所以拿到的是 JSON 而不是浏览器用的 303 跳转
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

## 已验证 / 未验证

- 已验证：Java 编译通过、APK 由 CI 构建并发布；App 会发出的 multipart 字节（含中文文件名、title/text 字段、
  固定 Content-Length）被逐字节复刻后打到线上接口，返回 200 与正确的分享码
- **未验证**：真机行为（本机没有安卓设备也没有模拟器）。分享面板能否出现、文件读取权限、大文件上传表现都要装到手机上实测
