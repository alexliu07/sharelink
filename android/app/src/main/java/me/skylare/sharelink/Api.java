package me.skylare.sharelink;

import android.content.ContentResolver;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * ShareLink 服务端接口的极简客户端：只用 HttpURLConnection，不引任何第三方库。
 *
 * 约定：这些方法都会阻塞网络，必须在工作线程里调用（Activity 里统一丢到 Thread 里，再 post 回主线程改 UI）。
 */
final class Api {

    /** 站点挂在 /share 子路径下（nginx 反代到本机 uvicorn）。 */
    static final String BASE = "https://skylare.me/share";
    static final String URL_SHARE_TARGET = BASE + "/api/share-target?response=json";
    static final String URL_DEVICES = BASE + "/api/devices";
    static final String URL_TRANSFERS = BASE + "/api/transfers";
    static final String UA = "ShareLink-Android/1.3";

    private Api() {
    }

    /** 服务端返回 4xx/5xx 时抛这个，message 已经是给人看的中文原因。 */
    static class HttpException extends Exception {
        final int status;

        HttpException(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    /** 上传进度回调（在工作线程里触发）。 */
    interface Progress {
        void onProgress(long sent, long total);
    }

    /** multipart 里的一个文本字段。 */
    static final class Part {
        final String name;
        final String value;

        Part(String name, String value) {
            this.name = name;
            this.value = value;
        }
    }

    /* ------------------------------------------------------------ 基础请求 */

    private static HttpURLConnection open(String url, String method, String token) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(20_000);
        conn.setReadTimeout(180_000);   // 大文件上传/下载要慢慢来
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("User-Agent", UA);
        if (token != null && !token.isEmpty()) {
            conn.setRequestProperty("X-Device-Token", token);
        }
        if ("PATCH".equals(method)) {
            // HttpURLConnection 不认 PATCH（会抛 ProtocolException），只能反射把方法名塞进去
            conn.setRequestMethod("POST");
            try {
                java.lang.reflect.Field field = HttpURLConnection.class.getDeclaredField("method");
                field.setAccessible(true);
                field.set(conn, "PATCH");
            } catch (Exception e) {
                throw new IOException("改名字失败（系统不允许 PATCH 请求）：" + e);
            }
        } else {
            conn.setRequestMethod(method);
        }
        return conn;
    }

    private static String finish(HttpURLConnection conn) throws IOException, HttpException {
        int status = conn.getResponseCode();
        InputStream in = status >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String body = in == null ? "" : readText(in);
        conn.disconnect();
        if (status >= 400) {
            throw new HttpException(status, describe(status, body));
        }
        return body;
    }

    /** 把服务端的错误 JSON 变成一句人话。 */
    static String describe(int status, String body) {
        try {
            JSONObject json = new JSONObject(body == null ? "" : body);
            JSONObject detail = json.optJSONObject("detail");
            String message = detail != null ? detail.optString("message", "") : json.optString("message", "");
            if (message.isEmpty() && detail != null) {
                message = detail.optString("error", "");
            }
            if (message.isEmpty()) {
                message = json.optString("error", "");
            }
            if (!message.isEmpty()) {
                return message;
            }
        } catch (Exception ignored) {
            // 不是 JSON（比如 Cloudflare 的错误页），退回状态码
        }
        if (status == 413) {
            return "文件太大：服务端上限 95 MB";
        }
        if (status == 401 || status == 403) {
            return "设备令牌无效，请重新登记或从剪贴板导入令牌";
        }
        return "服务端返回 HTTP " + status;
    }

    private static String request(String method, String url, String token, String jsonBody)
            throws IOException, HttpException {
        HttpURLConnection conn = open(url, method, token);
        if (jsonBody != null) {
            byte[] payload = jsonBody.getBytes(StandardCharsets.UTF_8);
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(payload.length);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            OutputStream out = conn.getOutputStream();
            out.write(payload);
            out.flush();
            out.close();
        }
        return finish(conn);
    }

    static String get(String url, String token) throws IOException, HttpException {
        return request("GET", url, token, null);
    }

    static String postJson(String url, String token, String json) throws IOException, HttpException {
        return request("POST", url, token, json);
    }

    static String patchJson(String url, String token, String json) throws IOException, HttpException {
        return request("PATCH", url, token, json);
    }

    static String delete(String url, String token) throws IOException, HttpException {
        return request("DELETE", url, token, null);
    }

    /* ------------------------------------------------------------ 上传 */

    /**
     * 传文件给服务端（上传 / 分享落地 / 定向投递共用）。
     *
     * 文件部分固定叫 file；能拿到文件大小时声明精确 Content-Length，拿不到才退回 chunked
     * （Cloudflare 前面，别赌分块传输）。
     */
    static String uploadMultipart(String url, List<Part> fields, Uri file, ContentResolver resolver,
                                 String token, Progress progress) throws IOException, HttpException {
        String boundary = "----ShareLink" + System.currentTimeMillis();
        ByteArrayOutputStream pre = new ByteArrayOutputStream();
        ByteArrayOutputStream post = new ByteArrayOutputStream();
        byte[] fileHead = null;
        long fileSize = file == null ? 0 : knownSize(resolver, file);

        if (file != null) {
            String name = sanitizeName(displayName(resolver, file));
            String type = resolver.getType(file);
            if (type == null || type.isEmpty()) {
                type = "application/octet-stream";
            }
            fileHead = bytes("--" + boundary + "\r\n"
                    + "Content-Disposition: form-data; name=\"file\"; filename=\"" + name + "\"\r\n"
                    + "Content-Type: " + type + "\r\n\r\n");
            for (Part part : fields) {
                if (part.value != null && !part.value.isEmpty()) {
                    post.write(field(boundary, part.name, part.value));
                }
            }
            post.write(bytes("\r\n--" + boundary + "--\r\n"));
        } else {
            for (Part part : fields) {
                if (part.value != null && !part.value.isEmpty()) {
                    pre.write(field(boundary, part.name, part.value));
                }
            }
            pre.write(bytes("--" + boundary + "--\r\n"));
        }

        HttpURLConnection conn = open(url, "POST", token);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        long length = pre.size() + post.size() + (file != null ? fileHead.length + fileSize : 0);
        if (file == null || fileSize >= 0) {
            conn.setFixedLengthStreamingMode(length);
        } else {
            conn.setChunkedStreamingMode(64 * 1024);
        }

        DataOutputStream out = new DataOutputStream(conn.getOutputStream());
        out.write(pre.toByteArray());
        if (file != null) {
            out.write(fileHead);
            InputStream in = resolver.openInputStream(file);
            if (in == null) {
                throw new IOException("读不到这个文件：可能是分享授权已过期，重新分享一次试试");
            }
            byte[] buffer = new byte[64 * 1024];
            long sent = 0;
            int read;
            while ((read = in.read(buffer)) > 0) {
                out.write(buffer, 0, read);
                sent += read;
                if (progress != null) {
                    progress.onProgress(sent, fileSize);
                }
            }
            in.close();
            out.write(post.toByteArray());
        }
        out.flush();
        out.close();
        return finish(conn);
    }

    /** 下载文件（投递过来的收件箱文件）。 */
    static InputStream openDownload(String url) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(20_000);
        conn.setReadTimeout(180_000);
        conn.setRequestProperty("User-Agent", UA);
        conn.setRequestProperty("Accept", "*/*");
        int status = conn.getResponseCode();
        if (status >= 400) {
            InputStream in = conn.getErrorStream();
            String body = in == null ? "" : readText(in);
            conn.disconnect();
            throw new IOException(describe(status, body));
        }
        return conn.getInputStream();
    }

    /* ------------------------------------------------------------ 小工具 */

    private static byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }

    private static byte[] field(String boundary, String name, String value) {
        return bytes("--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n"
                + value + "\r\n");
    }

    private static String readText(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[16 * 1024];
        int read;
        while ((read = in.read(chunk)) > 0) {
            buffer.write(chunk, 0, read);
        }
        in.close();
        return buffer.toString("UTF-8");
    }

    /** multipart 头里的文件名不能有引号和换行。 */
    static String sanitizeName(String raw) {
        String name = raw == null ? "" : raw.replace("\"", "_").replace("\r", " ").replace("\n", " ").trim();
        if (name.isEmpty()) {
            name = "未命名文件";
        }
        if (name.length() > 180) {
            name = name.substring(name.length() - 180);
        }
        return name;
    }

    /** 从 ContentResolver 拿原始文件名（分享过来的 URI 通常都提供）。 */
    static String displayName(ContentResolver resolver, Uri uri) {
        Cursor cursor = null;
        try {
            cursor = resolver.query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    String name = cursor.getString(index);
                    if (name != null && !name.trim().isEmpty()) {
                        return name;
                    }
                }
            }
        } catch (Exception ignored) {
            // 有些 provider 不认，退回路径末段
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        String last = uri.getLastPathSegment();
        return last == null || last.trim().isEmpty() ? "share.bin" : last;
    }

    /** 文件大小；拿不到返回 -1（调用方会退回 chunked 传输）。 */
    static long knownSize(ContentResolver resolver, Uri uri) {
        Cursor cursor = null;
        try {
            cursor = resolver.query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.SIZE);
                if (index >= 0 && !cursor.isNull(index)) {
                    return cursor.getLong(index);
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        return -1;
    }

    /** 把字节数说成人话。 */
    static String humanSize(long bytes) {
        if (bytes < 1024) {
            return bytes + " B";
        }
        if (bytes < 1024 * 1024) {
            return String.format(java.util.Locale.US, "%.1f KB", bytes / 1024.0);
        }
        if (bytes < 1024L * 1024 * 1024) {
            return String.format(java.util.Locale.US, "%.1f MB", bytes / (1024.0 * 1024));
        }
        return String.format(java.util.Locale.US, "%.2f GB", bytes / (1024.0 * 1024 * 1024));
    }

    /** 剩余有效时间说成人话。 */
    static String humanLeft(long seconds) {
        if (seconds <= 0) {
            return "已过期";
        }
        if (seconds < 3600) {
            return "剩 " + Math.max(1, seconds / 60) + " 分钟";
        }
        if (seconds < 86400) {
            return "剩 " + (seconds / 3600) + " 小时";
        }
        return "剩 " + (seconds / 86400) + " 天";
    }
}
