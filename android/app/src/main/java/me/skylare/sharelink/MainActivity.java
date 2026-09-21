package me.skylare.sharelink;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 把别处「分享」过来的文件或文字直接交给 ShareLink，拿回分享码。
 *
 * 一个 Activity 两种身份：
 *   - 从分享面板进来（ACTION_SEND / ACTION_SEND_MULTIPLE）→ 立刻上传并显示分享码；
 *   - 从桌面图标进来（MAIN/LAUNCHER）→ 说明 + 「打开网页版」。
 *
 * 刻意只用 Android 框架 API、零第三方依赖：CI 构建不用解析依赖，APK 也小。
 */
public class MainActivity extends Activity {

    /** ?response=json 让它回 JSON（分享码 + 链接），而不是给浏览器用的 303 跳转。 */
    private static final String ENDPOINT = "https://skylare.me/share/api/share-target?response=json";
    private static final String SITE = "https://skylare.me/share/";

    private static final int BG = 0xFF0B1020;
    private static final int CARD = 0xFF17203A;
    private static final int FG = 0xFFE8ECF8;
    private static final int MUTED = 0xFF93A0C8;
    private static final int ACCENT = 0xFF6C8CFF;
    private static final int DANGER = 0xFFFF6B81;
    private static final int ON_ACCENT = 0xFF0B1020;

    private final Handler ui = new Handler(Looper.getMainLooper());

    private TextView status;
    private TextView progressLabel;
    private ProgressBar progress;
    private LinearLayout resultBox;
    private TextView codeView;
    private TextView linkView;
    private LinearLayout homeBox;
    private Button retryButton;

    private String shareUrl = "";
    private Payload pending;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        setContentView(buildUi());

        Intent intent = getIntent();
        if (isShareIntent(intent)) {
            send(Payload.from(intent));
        } else {
            showHome();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (isShareIntent(intent)) {
            send(Payload.from(intent));
        } else {
            showHome();
        }
    }

    // ------------------------------------------------------------------ 界面

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.setFillViewport(true);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(28), dp(20), dp(28));
        scroll.addView(root);

        TextView title = new TextView(this);
        title.setText("分享到 ShareLink");
        title.setTextColor(FG);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 22);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        root.addView(title);

        status = new TextView(this);
        status.setTextColor(MUTED);
        status.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        status.setLineSpacing(dp(4), 1f);
        root.addView(status, lp(true, dp(10), 0));

        progressLabel = new TextView(this);
        progressLabel.setTextColor(MUTED);
        progressLabel.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        progressLabel.setVisibility(View.GONE);
        root.addView(progressLabel, lp(true, dp(12), 0));

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        root.addView(progress, lp(true, dp(4), 0));

        retryButton = button("重试", ACCENT);
        retryButton.setVisibility(View.GONE);
        retryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (pending != null) {
                    send(pending);
                }
            }
        });
        root.addView(retryButton, lp(false, dp(14), 0));

        resultBox = new LinearLayout(this);
        resultBox.setOrientation(LinearLayout.VERTICAL);
        resultBox.setBackground(rounded(CARD, 14));
        resultBox.setPadding(dp(16), dp(16), dp(16), dp(16));
        resultBox.setVisibility(View.GONE);
        root.addView(resultBox, lp(true, dp(18), 0));

        TextView codeHint = new TextView(this);
        codeHint.setText("分享码");
        codeHint.setTextColor(MUTED);
        codeHint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        resultBox.addView(codeHint);

        codeView = new TextView(this);
        codeView.setTextColor(ACCENT);
        codeView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 30);
        codeView.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        codeView.setTextIsSelectable(true);
        codeView.setPadding(0, dp(4), 0, dp(10));
        resultBox.addView(codeView);

        linkView = new TextView(this);
        linkView.setTextColor(MUTED);
        linkView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        linkView.setTextIsSelectable(true);
        resultBox.addView(linkView);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        resultBox.addView(actions, lp(true, dp(14), 0));

        Button copy = button("复制分享码", ACCENT);
        copy.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                copyText(codeView.getText().toString(), "分享码已复制");
            }
        });
        actions.addView(copy);

        Button open = button("打开", CARD);
        open.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (!shareUrl.isEmpty()) {
                    openUrl(shareUrl);
                }
            }
        });
        actions.addView(open, lp(false, 0, dp(10)));

        Button share = button("分享链接", CARD);
        share.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (shareUrl.isEmpty()) {
                    return;
                }
                Intent send = new Intent(Intent.ACTION_SEND);
                send.setType("text/plain");
                send.putExtra(Intent.EXTRA_TEXT, shareUrl);
                startActivity(Intent.createChooser(send, "分享链接"));
            }
        });
        actions.addView(share, lp(false, 0, dp(10)));

        homeBox = new LinearLayout(this);
        homeBox.setOrientation(LinearLayout.VERTICAL);
        homeBox.setVisibility(View.GONE);
        root.addView(homeBox, lp(true, dp(18), 0));

        TextView hint = new TextView(this);
        hint.setText("在别的应用里点「分享」，选 ShareLink，文件或链接就会上传到你的服务器，"
                + "并在这里显示分享码。\n\n单文件上限 95 MB（Cloudflare 免费版 100 MB 请求体限制）。");
        hint.setTextColor(MUTED);
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        hint.setLineSpacing(dp(5), 1f);
        homeBox.addView(hint);

        Button site = button("打开 ShareLink 网页版", ACCENT);
        site.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                openUrl(SITE);
            }
        });
        homeBox.addView(site, lp(false, dp(16), 0));

        return scroll;
    }

    private void showHome() {
        homeBox.setVisibility(View.VISIBLE);
        status.setText("把这个小工具当成分享目标用");
    }

    private void send(final Payload payload) {
        pending = payload;
        status.setTextColor(MUTED);
        resultBox.setVisibility(View.GONE);
        homeBox.setVisibility(View.GONE);
        retryButton.setVisibility(View.GONE);

        if (payload.isEmpty()) {
            fail("这次分享里既没有文件也没有文字");
            return;
        }

        progress.setVisibility(View.VISIBLE);
        progress.setProgress(0);
        progressLabel.setVisibility(View.VISIBLE);
        progressLabel.setText("准备中…");
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        if (payload.uris.isEmpty()) {
            status.setText("正在上传分享的文字");
        } else {
            String name = displayName(payload.uris.get(0));
            status.setText(payload.uris.size() > 1
                    ? "正在上传 " + name + "（共 " + payload.uris.size() + " 个，只发第一个）"
                    : "正在上传 " + name);
        }

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    final JSONObject json = new JSONObject(upload(payload));
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            succeed(json);
                        }
                    });
                } catch (final Exception e) {
                    ui.post(new Runnable() {
                        @Override
                        public void run() {
                            fail(describe(e));
                        }
                    });
                }
            }
        }).start();
    }

    private void succeed(JSONObject json) {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        progress.setVisibility(View.GONE);
        progressLabel.setVisibility(View.GONE);

        String code = json.optString("code", "");
        shareUrl = json.optString("share_url", "");
        String filename = json.optString("filename", "");
        long size = json.optLong("size", 0);

        status.setTextColor(MUTED);
        status.setText("已上传" + (filename.isEmpty() ? "" : "：" + filename + "（" + human(size) + "）"));
        codeView.setText(code);
        linkView.setText(shareUrl);
        resultBox.setVisibility(View.VISIBLE);
        if (!code.isEmpty()) {
            copyText(code, "分享码 " + code + " 已复制");
        }
    }

    private void fail(String message) {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        progress.setVisibility(View.GONE);
        progressLabel.setVisibility(View.GONE);
        status.setTextColor(DANGER);
        status.setText("上传失败：" + message);
        retryButton.setVisibility(View.VISIBLE);
    }

    // ------------------------------------------------------------------ 上传

    private String upload(Payload payload) throws Exception {
        String boundary = "----ShareLink" + System.currentTimeMillis();
        Uri uri = payload.uris.isEmpty() ? null : payload.uris.get(0);
        long fileSize = uri == null ? 0 : knownSize(uri);

        ByteArrayOutputStream pre = new ByteArrayOutputStream();
        ByteArrayOutputStream post = new ByteArrayOutputStream();
        byte[] fileHead = null;

        if (uri == null) {
            if (!payload.title.trim().isEmpty()) {
                pre.write(field(boundary, "title", payload.title));
            }
            if (!payload.text.trim().isEmpty()) {
                pre.write(field(boundary, "text", payload.text));
            }
            pre.write(bytes("--" + boundary + "--\r\n"));
        } else {
            String type = getContentResolver().getType(uri);
            fileHead = bytes("--" + boundary + "\r\n"
                    + "Content-Disposition: form-data; name=\"file\"; filename=\""
                    + safeFileName(displayName(uri)) + "\"\r\n"
                    + "Content-Type: " + (type == null || type.isEmpty() ? "application/octet-stream" : type)
                    + "\r\n\r\n");
            if (!payload.title.trim().isEmpty()) {
                post.write(field(boundary, "title", payload.title));
            }
            if (!payload.text.trim().isEmpty()) {
                post.write(field(boundary, "text", payload.text));
            }
            post.write(bytes("\r\n--" + boundary + "--\r\n"));
        }

        long length = pre.size() + post.size() + (fileHead == null ? 0 : fileHead.length + fileSize);
        HttpURLConnection conn = (HttpURLConnection) new URL(ENDPOINT).openConnection();
        try {
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(20000);
            conn.setReadTimeout(300000);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("User-Agent", "ShareLink-Android/1.1");
            if (fileHead == null || fileSize >= 0) {
                conn.setFixedLengthStreamingMode(length);
            } else {
                conn.setChunkedStreamingMode(64 * 1024);
            }

            DataOutputStream out = new DataOutputStream(conn.getOutputStream());
            try {
                out.write(pre.toByteArray());
                if (fileHead != null) {
                    out.write(fileHead);
                    InputStream in = getContentResolver().openInputStream(uri);
                    if (in == null) {
                        throw new IOException("读不到这个文件（分享方没给读取权限）");
                    }
                    try {
                        byte[] buffer = new byte[64 * 1024];
                        long sent = 0;
                        int read;
                        while ((read = in.read(buffer)) > 0) {
                            out.write(buffer, 0, read);
                            sent += read;
                            reportProgress(sent, fileSize);
                        }
                    } finally {
                        in.close();
                    }
                    out.write(post.toByteArray());
                }
                out.flush();
            } finally {
                out.close();
            }

            int code = conn.getResponseCode();
            InputStream response = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            String body = response == null ? "" : readText(response);
            if (code != 200) {
                throw new IOException(messageFor(code, body));
            }
            return body;
        } finally {
            conn.disconnect();
        }
    }

    /** 把服务端的错误变成人话（我们的接口回 JSON，Cloudflare 那层可能回 HTML）。 */
    private String messageFor(int code, String body) {
        try {
            JSONObject json = new JSONObject(body);
            JSONObject detail = json.optJSONObject("detail");
            String message = detail != null ? detail.optString("message") : json.optString("message");
            if (message != null && !message.isEmpty()) {
                return message;
            }
        } catch (Exception ignored) {
            // 落回下面的通用文案
        }
        if (code == 413) {
            return "文件太大（HTTP 413）";
        }
        return "服务端返回 HTTP " + code;
    }

    private void reportProgress(final long sent, final long total) {
        ui.post(new Runnable() {
            @Override
            public void run() {
                if (total > 0) {
                    progress.setProgress((int) Math.min(100, sent * 100 / total));
                    progressLabel.setText("已上传 " + human(sent) + " / " + human(total));
                } else {
                    progressLabel.setText("已上传 " + human(sent));
                }
            }
        });
    }

    private String describe(Exception e) {
        String message = e.getMessage();
        if (message == null || message.isEmpty()) {
            return e.getClass().getSimpleName();
        }
        return message;
    }

    // ------------------------------------------------------------------ 小工具

    private boolean isShareIntent(Intent intent) {
        if (intent == null || intent.getAction() == null) {
            return false;
        }
        String action = intent.getAction();
        return Intent.ACTION_SEND.equals(action) || Intent.ACTION_SEND_MULTIPLE.equals(action);
    }

    private String displayName(Uri uri) {
        String name = null;
        Cursor cursor = null;
        try {
            cursor = getContentResolver().query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (index >= 0) {
                    name = cursor.getString(index);
                }
            }
        } catch (Exception ignored) {
            // 拿不到就用 URI 末段兜底
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        if (name == null || name.trim().isEmpty()) {
            name = uri.getLastPathSegment();
        }
        if (name == null || name.trim().isEmpty()) {
            name = "share.bin";
        }
        return name;
    }

    private long knownSize(Uri uri) {
        Cursor cursor = null;
        try {
            cursor = getContentResolver().query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int index = cursor.getColumnIndex(OpenableColumns.SIZE);
                if (index >= 0 && !cursor.isNull(index)) {
                    return cursor.getLong(index);
                }
            }
        } catch (Exception ignored) {
            // 未知大小 → 退回 chunked 传输
        } finally {
            if (cursor != null) {
                cursor.close();
            }
        }
        return -1;
    }

    /** multipart 的文件名里不能有引号/换行，否则 header 会被拆坏。 */
    private String safeFileName(String name) {
        String cleaned = name.replace("\"", "_").replace("\r", "").replace("\n", "").trim();
        if (cleaned.isEmpty()) {
            cleaned = "share.bin";
        }
        return cleaned.length() > 180 ? cleaned.substring(0, 180) : cleaned;
    }

    private byte[] field(String boundary, String name, String value) {
        return bytes("--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n"
                + value + "\r\n");
    }

    private byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }

    private String readText(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try {
            byte[] chunk = new byte[8192];
            int read;
            while ((read = in.read(chunk)) > 0) {
                buffer.write(chunk, 0, read);
            }
        } finally {
            in.close();
        }
        return buffer.toString("UTF-8");
    }

    private String human(long bytes) {
        if (bytes < 1024) {
            return bytes + " B";
        }
        if (bytes < 1024 * 1024) {
            return String.format(Locale.US, "%.1f KB", bytes / 1024.0);
        }
        return String.format(Locale.US, "%.1f MB", bytes / 1024.0 / 1024.0);
    }

    private void copyText(String text, String toast) {
        if (text == null || text.isEmpty()) {
            return;
        }
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (clipboard != null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("ShareLink", text));
            toast(toast);
        }
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

    private void openUrl(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (ActivityNotFoundException e) {
            toast("没有能打开链接的应用");
        }
    }

    private Button button(String text, int background) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextColor(background == ACCENT ? ON_ACCENT : FG);
        button.setBackground(rounded(background, 10));
        return button;
    }

    private GradientDrawable rounded(int color, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }

    private LinearLayout.LayoutParams lp(boolean matchWidth, int top, int left) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                matchWidth ? ViewGroup.LayoutParams.MATCH_PARENT : ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = top;
        params.leftMargin = left;
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    // ------------------------------------------------------------------ 分享过来的内容

    private static final class Payload {
        final List<Uri> uris = new ArrayList<Uri>();
        String title = "";
        String text = "";

        static Payload from(Intent intent) {
            Payload payload = new Payload();
            payload.title = nullToEmpty(intent.getStringExtra(Intent.EXTRA_SUBJECT));
            payload.text = nullToEmpty(intent.getStringExtra(Intent.EXTRA_TEXT));
            String action = intent.getAction();
            if (Intent.ACTION_SEND.equals(action)) {
                Uri uri = uriFrom(intent);
                if (uri != null) {
                    payload.uris.add(uri);
                }
            } else if (Intent.ACTION_SEND_MULTIPLE.equals(action)) {
                payload.uris.addAll(listFrom(intent));
            }
            return payload;
        }

        boolean isEmpty() {
            return uris.isEmpty() && title.trim().isEmpty() && text.trim().isEmpty();
        }

        private static String nullToEmpty(String value) {
            return value == null ? "" : value;
        }

        @SuppressWarnings("deprecation")
        private static Uri uriFrom(Intent intent) {
            Uri uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (uri != null) {
                return uri;
            }
            ClipData clip = intent.getClipData();
            if (clip != null && clip.getItemCount() > 0) {
                return clip.getItemAt(0).getUri();
            }
            return null;
        }

        @SuppressWarnings("deprecation")
        private static ArrayList<Uri> listFrom(Intent intent) {
            ArrayList<Uri> out = new ArrayList<Uri>();
            ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                for (Uri uri : list) {
                    if (uri != null) {
                        out.add(uri);
                    }
                }
            }
            if (out.isEmpty()) {
                ClipData clip = intent.getClipData();
                if (clip != null) {
                    for (int i = 0; i < clip.getItemCount(); i++) {
                        Uri uri = clip.getItemAt(i).getUri();
                        if (uri != null) {
                            out.add(uri);
                        }
                    }
                }
            }
            return out;
        }
    }
}
