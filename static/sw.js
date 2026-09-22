/* ShareLink service worker
 *
 * 策略（刻意保守，这是个要长期可维护的小站）：
 *   1. 外壳文件（页面/样式/脚本/图标/manifest）：**网络优先**，成功就顺手更新缓存；
 *      断网时才用缓存。这样部署后刷新一次就是新版本，不会出现"缓存的旧 app.js"。
 *   2. `/api/*`（上传、下载、查询、投递、收件箱）：**完全不插手**。文件可能几十 MB，
 *      且"过期即删"的语义不允许中间层缓存。
 *   3. 导航请求断网时回退到缓存的外壳，页面还能打开并提示离线。
 *
 * 注意：服务端对 sw.js / app.js / style.css 发 `Cache-Control: no-cache`，
 * 否则 Cloudflare 会按默认的 .js 规则缓存 4 小时，SW 更新要等 4 小时才生效。
 * 另外 index.html 里引用 app.js / style.css 都带 `?v=N`（见 app.js 的 ASSET_VERSION）：
 * 前端一改就 +1，已经进过别人浏览器缓存的旧副本会被新 URL 直接绕开。
 */
const VERSION = "sharelink-v10";

// 预缓存清单：相对 URL 以 sw.js 自身位置为基准（部署在 /share/ 下也能work）
const SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./favicon.svg",
  "./manifest.webmanifest",
  "./icons/app-icon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-512.png",
  "./icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(VERSION);
      // 逐个 add：单个文件缺失不会让整次安装失败（否则 SW 会一直不激活）
      await Promise.all(
        SHELL.map(async (url) => {
          try {
            await cache.add(new Request(url, { cache: "reload" }));
          } catch (error) {
            console.warn("[sw] 预缓存失败（跳过）", url, error);
          }
        })
      );
      await self.skipWaiting();
    })()
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

const isApi = (url) => url.pathname.includes("/api/");

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // 上传/投递是 POST，下载可能带 Range，全都直连
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;   // 跨域不插手
  if (isApi(url)) return;                            // 接口/下载绝不缓存

  event.respondWith(
    (async () => {
      try {
        const fresh = await fetch(request);
        if (fresh && fresh.ok && fresh.type === "basic") {
          const cache = await caches.open(VERSION);
          cache.put(request, fresh.clone());
        }
        return fresh;
      } catch (error) {
        const cached = await caches.match(request, { ignoreSearch: request.mode === "navigate" });
        if (cached) return cached;
        if (request.mode === "navigate") {
          const shell = (await caches.match("./index.html")) || (await caches.match("./"));
          if (shell) return shell;
        }
        return new Response("离线，且这个资源没有缓存", {
          status: 503,
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
    })()
  );
});
