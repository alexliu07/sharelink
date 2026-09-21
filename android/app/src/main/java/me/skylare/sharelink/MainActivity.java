package me.skylare.sharelink;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.text.InputType;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

/**
 * ShareLink 安卓端：既能把别处分享来的文件上传成分享码，也能当一台设备（收件箱 + 定向投递给别的设备）。
 *
 * 界面全部用代码搭（不引 AndroidX / Material），一个 Activity 几个"屏幕"：
 *   主页（设备卡 + 收件箱 + 动作）/ 登记设备 / 分享上传流程（进度 → 结果）。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "sharelink";
    private static final String KEY_DEVICE = "device";
    private static final String KEY_TTL = "ttl_seconds";
    private static final long DEFAULT_TTL_SECONDS = 3600L;   // 与服务端 SHARELINK_DEFAULT_TTL_SECONDS 一致
    private static final int REQ_PICK_FILE = 1001;
    private static final long MAX_UPLOAD_BYTES = 95L * 1024 * 1024;   // 与服务端 SHARELINK_MAX_UPLOAD_MB 对齐

    private static final int BG = 0xFF0B1020;
    private static final int CARD = 0xFF17203A;
    private static final int CARD_SOFT = 0xFF1F2A47;
    private static final int FG = 0xFFE8ECF8;
    private static final int MUTED = 0xFF93A0C8;
    private static final int ACCENT = 0xFF6C8CFF;
    private static final int DANGER = 0xFFFF7A8A;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private JSONObject device;        // 本机设备身份：{id, token, name}
    private long ttlSeconds = DEFAULT_TTL_SECONDS;   // 上传有效期（记在本地，跟网页版同一套档位）

    private LinearLayout box;         // 当前屏幕的容器（换屏 = 清空重填）
    private ProgressBar progress;
    private TextView progressLabel;
    private LinearLayout inboxList;
    private TextView inboxStatus;

    /* ================================================================ 生命周期 */

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        ttlSeconds = prefs.getLong(KEY_TTL, DEFAULT_TTL_SECONDS);
        loadDevice();
        setContentView(buildShell());
        Intent intent = getIntent();
        if (isShareIntent(intent)) {
            startShare(Payload.from(intent));
        } else {
            showHome();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (isShareIntent(intent)) {
            startShare(Payload.from(intent));
        } else {
            showHome();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_PICK_FILE) {
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                Payload payload = new Payload();
                payload.uris.add(data.getData());
                startShare(payload);
            } else {
                showHome();
            }
        }
    }

    /* ================================================================ 设备身份 */

    private void loadDevice() {
        String raw = prefs.getString(KEY_DEVICE, null);
        device = null;
        if (raw == null) {
            return;
        }
        try {
            JSONObject parsed = new JSONObject(raw);
            if (parsed.optString("id").isEmpty() || parsed.optString("token").isEmpty()) {
                return;
            }
            device = parsed;
        } catch (Exception ignored) {
        }
    }

    private void saveDevice(String id, String token, String name) {
        try {
            JSONObject json = new JSONObject();
            json.put("id", id);
            json.put("token", token);
            json.put("name", name);
            device = json;
            prefs.edit().putString(KEY_DEVICE, json.toString()).apply();
        } catch (Exception e) {
            toast("保存设备信息失败：" + e.getMessage());
        }
    }

    private void clearDevice() {
        device = null;
        prefs.edit().remove(KEY_DEVICE).apply();
    }

    private String deviceId() {
        return device == null ? "" : device.optString("id");
    }

    private String deviceToken() {
        return device == null ? "" : device.optString("token");
    }

    private String deviceName() {
        return device == null ? "" : device.optString("name");
    }

    /* ================================================================ 分享上传流程 */

    private void startShare(Payload payload) {
        if (payload == null || payload.isEmpty()) {
            showFlowMessage("这次分享没有文件也没有文字");
            return;
        }
        Uri first = payload.uris.isEmpty() ? null : payload.uris.get(0);
        if (first != null) {
            long size = Api.knownSize(getContentResolver(), first);
            if (size > MAX_UPLOAD_BYTES) {
                showFlowMessage("这个文件 " + Api.humanSize(size) + "，超过服务端上限 95 MB");
                return;
            }
        }
        showFlowBusy(first == null ? "正在上传分享的文字…"
                : "正在上传 " + Api.displayName(getContentResolver(), first) + "…");
        if (device == null) {
            doUpload(payload, null, null);      // 没登记设备 → 只拿分享码，不弹选择框
            return;
        }
        // 登记过设备：先问一句要发给谁（不选＝只拿分享码），省得大文件传两遍
        new Thread(() -> {
            final List<String[]> others = new ArrayList<>();
            try {
                JSONObject res = new JSONObject(Api.get(Api.URL_DEVICES, null));
                JSONArray arr = res.optJSONArray("devices");
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject item = arr.getJSONObject(i);
                        if (!deviceId().equals(item.optString("id"))) {
                            others.add(new String[]{item.optString("id"), item.optString("name")});
                        }
                    }
                }
            } catch (Exception ignored) {
                // 拿不到设备列表就退回"只拿分享码"，不让分享流程失败
            }
            ui.post(() -> {
                if (others.isEmpty()) {
                    doUpload(payload, null, null);
                } else {
                    askDestination(payload, others);
                }
            });
        }).start();
    }

    private void askDestination(final Payload payload, final List<String[]> others) {
        final String[] names = new String[others.size()];
        final boolean[] checked = new boolean[others.size()];
        for (int i = 0; i < others.size(); i++) {
            names[i] = others.get(i)[1];
        }
        new AlertDialog.Builder(this)
                .setTitle("要把文件发给设备吗？")
                .setMultiChoiceItems(names, checked, (dialog, which, isChecked) -> checked[which] = isChecked)
                .setMessage("选中的设备会在自己的收件箱里看到它；不选则只生成分享码。")
                .setPositiveButton("确定", (dialog, which) -> {
                    List<String> ids = new ArrayList<>();
                    List<String> picked = new ArrayList<>();
                    for (int i = 0; i < checked.length; i++) {
                        if (checked[i]) {
                            ids.add(others.get(i)[0]);
                            picked.add(others.get(i)[1]);
                        }
                    }
                    doUpload(payload, ids.isEmpty() ? null : ids, picked.isEmpty() ? null : picked);
                })
                .setNeutralButton("只拿分享码", (dialog, which) -> doUpload(payload, null, null))
                .setNegativeButton("取消", (dialog, which) -> showHome())
                .show();
    }

    /** targets 为 null = 只上传拿分享码；否则投递给这些设备。 */
    private void doUpload(final Payload payload, final List<String> targets, final List<String> targetNames) {
        final Uri file = payload.uris.isEmpty() ? null : payload.uris.get(0);
        showFlowBusy(file == null ? "正在上传分享的文字…"
                : "正在上传 " + Api.displayName(getContentResolver(), file)
                + (payload.uris.size() > 1 ? "（共 " + payload.uris.size() + " 个，只发第一个）" : ""));
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        new Thread(() -> {
            try {
                List<Api.Part> fields = new ArrayList<>();
                // 有效期：服务端优先看 ttl_seconds，两条路（分享面板 / 投递给设备）都带上
                fields.add(new Api.Part("ttl_seconds", String.valueOf(ttlSeconds)));
                String url;
                if (targets == null) {
                    url = Api.URL_SHARE_TARGET;
                    fields.add(new Api.Part("title", payload.title));
                    fields.add(new Api.Part("text", payload.text));
                } else {
                    url = Api.URL_TRANSFERS;
                    fields.add(new Api.Part("targets", String.join(",", targets)));
                    fields.add(new Api.Part("from_device_id", deviceId()));
                    String note = payload.title.trim().isEmpty() ? payload.text : payload.title;
                    fields.add(new Api.Part("note", note));
                }
                final String body = Api.uploadMultipart(url, fields, file, getContentResolver(), deviceToken(),
                        (sent, total) -> ui.post(() -> showProgress(sent, total)));
                final JSONObject json = new JSONObject(body);
                ui.post(() -> showResult(json, targetNames));
            } catch (Exception e) {
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> showFlowMessage("上传失败：" + message));
            }
        }).start();
    }

    private void showProgress(long sent, long total) {
        if (progress == null) {
            return;
        }
        progress.setVisibility(View.VISIBLE);
        progressLabel.setVisibility(View.VISIBLE);
        progressLabel.setText(total > 0
                ? "已上传 " + Api.humanSize(sent) + " / " + Api.humanSize(total)
                : "已上传 " + Api.humanSize(sent));
        if (total > 0) {
            progress.setProgress((int) Math.min(100, sent * 100 / total));
        } else {
            progress.setIndeterminate(true);
        }
    }

    private void showResult(JSONObject json, List<String> targetNames) {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        final String code = json.optString("code");
        final String shareUrl = json.optString("share_url");

        LinearLayout content = column();
        content.addView(title("已完成"));
        content.addView(line(targetNames == null || targetNames.isEmpty()
                ? "上传成功，把分享码给对方就能下载。"
                : "已投递给 " + String.join("、", targetNames) + "，对方打开 App 就能看到。", MUTED, 14));

        LinearLayout card = card();
        TextView big = new TextView(this);
        big.setText(code);
        big.setTextColor(ACCENT);
        big.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        big.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34);
        big.setLetterSpacing(0.12f);
        big.setTextIsSelectable(true);
        card.addView(big);
        card.addView(line(Api.humanSize(json.optLong("size")) + " · " + json.optString("filename")
                + (json.optString("ttl_human").isEmpty() ? "" : " · 有效期 " + json.optString("ttl_human")),
                MUTED, 13));
        card.addView(line(shortUrl(shareUrl), MUTED, 12));
        content.addView(withTop(card, 10));

        copyToClipboard("ShareLink 分享码", code);   // 自动复制，省得手抄
        content.addView(withTop(line("已自动复制分享码", MUTED, 12), 10));

        LinearLayout actions = row();
        Button copy = button("复制分享码", false);
        copy.setOnClickListener(v -> {
            copyToClipboard("ShareLink 分享码", code);
            toast("已复制分享码 " + code);
        });
        Button open = button("打开落地页", false);
        open.setOnClickListener(v -> openUrl(shareUrl));
        Button share = button("转发链接", true);
        share.setOnClickListener(v -> {
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType("text/plain");
            send.putExtra(Intent.EXTRA_TEXT, shareUrl);
            startActivity(Intent.createChooser(send, "把分享链接发给别人"));
        });
        actions.addView(copy);
        actions.addView(open);
        actions.addView(share);
        content.addView(withTop(actions, 12));

        Button close = button("回到主页", false);
        close.setOnClickListener(v -> showHome());
        content.addView(withTop(close, 18));
        show(content);
    }

    private void showFlowBusy(String text) {
        LinearLayout content = column();
        content.addView(title("ShareLink"));
        content.addView(line(text, FG, 15));
        progressLabel = line("", MUTED, 12);
        content.addView(withTop(progressLabel, 10));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        content.addView(withTop(progress, 6));
        show(content);
    }

    private void showFlowMessage(String message) {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout content = column();
        content.addView(title("ShareLink"));
        content.addView(line(message, FG, 15));
        Button home = button("回到主页", true);
        home.setOnClickListener(v -> showHome());
        content.addView(withTop(home, 20));
        show(content);
    }

    /* ================================================================ 主页 */

    private void showHome() {
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout content = column();
        content.addView(title("ShareLink"));
        if (device == null) {
            content.addView(registerSection());
            content.addView(withTop(ttlCard(), 14));
        } else {
            content.addView(deviceCard());
            content.addView(withTop(inboxCard(), 14));
            content.addView(withTop(ttlCard(), 14));
            content.addView(withTop(actionsCard(), 14));
        }
        show(content);
        if (device != null) {
            loadInbox();
        }
    }

    private View registerSection() {
        LinearLayout wrapper = column();
        wrapper.setPadding(0, 0, 0, 0);
        LinearLayout card = card();
        card.addView(line("把这台手机登记成一台设备", FG, 16));
        card.addView(withTop(line("登记后：别的设备（iPad、电脑）可以直接把文件发到这台手机的收件箱；"
                + "你分享文件时也能直接投递给它们，而不只是生成分享码。", MUTED, 13), 8));

        final EditText name = new EditText(this);
        name.setHint("设备名，例如：我的手机");
        name.setInputType(InputType.TYPE_CLASS_TEXT);
        name.setTextColor(FG);
        name.setHintTextColor(MUTED);
        card.addView(withTop(name, 14));

        Button register = button("登记这台设备", true);
        register.setOnClickListener(v -> {
            String wanted = name.getText().toString().trim();
            if (wanted.isEmpty()) {
                wanted = "安卓手机";
            }
            final String finalName = wanted;
            register.setEnabled(false);
            showFlowBusy("正在登记 " + finalName + "…");
            new Thread(() -> {
                try {
                    JSONObject payload = new JSONObject();
                    payload.put("name", finalName);
                    JSONObject res = new JSONObject(Api.postJson(Api.URL_DEVICES, null, payload.toString()));
                    saveDevice(res.optString("id"), res.optString("token"), res.optString("name"));
                    ui.post(() -> {
                        toast("已登记为「" + deviceName() + "」");
                        showHome();
                    });
                } catch (Exception e) {
                    final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                    ui.post(() -> {
                        register.setEnabled(true);
                        showFlowMessage("登记失败：" + message);
                    });
                }
            }).start();
        });
        card.addView(withTop(register, 12));

        Button importBtn = button("从剪贴板导入令牌", false);
        importBtn.setOnClickListener(v -> importTokenFromClipboard());
        card.addView(withTop(importBtn, 8));
        card.addView(withTop(line("例如先在电脑网页版导出令牌，把那段 JSON 复制到剪贴板再点这里，"
                + "这台手机就会认领同一台设备（连收件箱一起）。", MUTED, 12), 6));
        wrapper.addView(card);

        LinearLayout tip = card();
        tip.addView(line("在相册 / 文件管理里点「分享」→ 选 ShareLink，文件就会上传并给出分享码，"
                + "也可以顺手发给别的设备。", MUTED, 12));
        wrapper.addView(withTop(tip, 14));
        return wrapper;
    }

    private View deviceCard() {
        LinearLayout card = card();
        card.addView(line(deviceName(), FG, 20));
        card.addView(withTop(line("设备 id " + deviceId(), MUTED, 12), 4));
        inboxStatus = line("收件箱读取中…", MUTED, 13);
        card.addView(withTop(inboxStatus, 10));

        LinearLayout row = row();
        Button refresh = button("刷新", false);
        refresh.setOnClickListener(v -> loadInbox());
        Button rename = button("改名字", false);
        rename.setOnClickListener(v -> renameDevice());
        Button export = button("导出令牌", false);
        export.setOnClickListener(v -> exportToken());
        row.addView(refresh);
        row.addView(rename);
        row.addView(export);
        card.addView(withTop(row, 10));

        Button leave = button("注销这台设备", false);
        leave.setTextColor(DANGER);
        leave.setOnClickListener(v -> confirmLeave());
        card.addView(withTop(leave, 8));
        return card;
    }

    private View inboxCard() {
        LinearLayout card = card();
        LinearLayout header = row();
        TextView head = line("收件箱", FG, 16);
        // 标题占满左侧、按钮贴右边：两者之间自然留出间距，也不会被挤到一起
        head.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        header.addView(head);
        Button seen = button("全部标记已读", false);
        seen.setSingleLine(true);                       // 再窄也不折成两行（宁可省略号）
        seen.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        LinearLayout.LayoutParams seenParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        seenParams.leftMargin = dp(12);                 // 标题与按钮之间留缝
        seen.setLayoutParams(seenParams);
        seen.setOnClickListener(v -> markSeen());
        header.addView(seen);
        card.addView(header);
        inboxList = new LinearLayout(this);
        inboxList.setOrientation(LinearLayout.VERTICAL);
        card.addView(withTop(inboxList, 10));
        return card;
    }

    /** 上传有效期：与服务端 / 网页版一致的五档预设（分享面板上传的文件也用这里选的值）。 */
    private View ttlCard() {
        LinearLayout card = card();
        card.addView(line("上传有效期", FG, 16));
        card.addView(withTop(line("到期后服务器自动删除，不可恢复。分享面板里上传的文件也用这里选的值。",
                MUTED, 13), 8));
        card.addView(withTop(ttlChips(), 12));
        return card;
    }

    private View ttlChips() {
        final long[] presets = {600L, 3600L, 86400L, 604800L, 2592000L};
        final String[] labels = {"10 分钟", "1 小时", "1 天", "7 天", "30 天"};
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        LinearLayout first = row();
        LinearLayout second = row();
        for (int i = 0; i < presets.length; i++) {
            final long seconds = presets[i];
            Button chip = chip(labels[i], seconds);
            chip.setOnClickListener(v -> {
                ttlSeconds = seconds;
                prefs.edit().putLong(KEY_TTL, seconds).apply();
                refreshTtlChips(wrap);
            });
            (i < 3 ? first : second).addView(chip);
        }
        wrap.addView(first);
        wrap.addView(withTop(second, 8));
        refreshTtlChips(wrap);
        return wrap;
    }

    private Button chip(String text, long seconds) {
        Button view = button(text, false);
        view.setTag(seconds);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        view.setPadding(dp(12), dp(7), dp(12), dp(7));
        view.setMinHeight(dp(36));
        view.setSingleLine(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.rightMargin = dp(8);
        view.setLayoutParams(params);
        return view;
    }

    /** 把这一组有效期小按钮里被选中的那个点亮。 */
    private void refreshTtlChips(View group) {
        if (!(group instanceof ViewGroup)) {
            return;
        }
        ViewGroup parent = (ViewGroup) group;
        for (int i = 0; i < parent.getChildCount(); i++) {
            View child = parent.getChildAt(i);
            if (child instanceof Button && child.getTag() instanceof Long) {
                boolean active = ((Long) child.getTag()) == ttlSeconds;
                child.setBackground(rounded(active ? ACCENT : CARD_SOFT, 10));
                ((Button) child).setTextColor(active ? BG : FG);
            }
            refreshTtlChips(child);
        }
    }

    private View actionsCard() {
        LinearLayout card = card();
        card.addView(line("发文件给别的设备", FG, 16));
        card.addView(withTop(line("选一个文件，再勾选目标设备，一次上传直接投递（不勾就是普通分享码）。", MUTED, 13), 8));
        Button pick = button("选择文件发给设备", true);
        pick.setOnClickListener(v -> {
            Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
            intent.setType("*/*");
            intent.addCategory(Intent.CATEGORY_OPENABLE);
            try {
                startActivityForResult(Intent.createChooser(intent, "选择要发送的文件"), REQ_PICK_FILE);
            } catch (ActivityNotFoundException e) {
                toast("这台设备没有文件选择器");
            }
        });
        card.addView(withTop(pick, 12));
        Button site = button("打开网页版", false);
        site.setOnClickListener(v -> openUrl(Api.BASE + "/"));
        card.addView(withTop(site, 8));
        return card;
    }

    private void loadInbox() {
        if (device == null) {
            return;
        }
        final String url = Api.BASE + "/api/devices/" + deviceId() + "/inbox";
        new Thread(() -> {
            try {
                final JSONObject json = new JSONObject(Api.get(url, deviceToken()));
                ui.post(() -> renderInbox(json));
            } catch (final Exception e) {
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> {
                    if (inboxStatus != null) {
                        inboxStatus.setText("收件箱读取失败：" + message);
                    }
                });
            }
        }).start();
    }

    private void renderInbox(JSONObject json) {
        if (inboxList == null || inboxStatus == null) {
            return;
        }
        int count = json.optInt("count");
        int unread = json.optInt("unread");
        inboxStatus.setText(count == 0 ? "收件箱是空的"
                : "共 " + count + " 个文件" + (unread > 0 ? "（" + unread + " 个未读）" : ""));
        inboxList.removeAllViews();

        JSONArray items = json.optJSONArray("items");
        if (items == null || items.length() == 0) {
            inboxList.addView(line("还没有别的设备发东西过来。", MUTED, 13));
            return;
        }
        for (int i = 0; i < items.length(); i++) {
            final JSONObject item = items.optJSONObject(i);
            if (item == null) {
                continue;
            }
            final String code = item.optString("code");
            final String filename = item.optString("filename");
            final String mime = item.optString("content_type", "*/*");
            final String downloadUrl = item.optString("download_url", Api.BASE + "/api/download/" + code);

            LinearLayout rowView = new LinearLayout(this);
            rowView.setOrientation(LinearLayout.VERTICAL);
            rowView.setBackground(rounded(CARD_SOFT, 12));
            int pad = dp(12);
            rowView.setPadding(pad, pad, pad, pad);
            rowView.addView(line((item.optBoolean("seen") ? "" : "● ") + filename, FG, 15));
            String from = item.optString("from_name", "匿名设备");
            rowView.addView(line(Api.humanSize(item.optLong("size")) + " · 来自 " + from + " · "
                    + Api.humanLeft(item.optLong("seconds_left")), MUTED, 12));
            rowView.addView(line("点一下下载并打开 · 长按可移出收件箱", MUTED, 11));

            rowView.setOnClickListener(v -> downloadItem(downloadUrl, filename, mime));
            rowView.setOnLongClickListener(v -> {
                new AlertDialog.Builder(this)
                        .setTitle("移出收件箱？")
                        .setMessage("只是从你的收件箱里删掉这条记录，文件本身还在（分享码 " + code + " 仍可下载）。")
                        .setPositiveButton("移出", (dialog, which) -> removeInboxItem(code))
                        .setNegativeButton("取消", null)
                        .show();
                return true;
            });
            inboxList.addView(withTop(rowView, 8));
        }
    }

    private void markSeen() {
        if (device == null) {
            return;
        }
        new Thread(() -> {
            try {
                Api.postJson(Api.BASE + "/api/devices/" + deviceId() + "/inbox/seen", deviceToken(), "{}");
                ui.post(() -> {
                    toast("已全部标记为已读");
                    loadInbox();
                });
            } catch (final Exception e) {
                ui.post(() -> toast("标记失败：" + e.getMessage()));
            }
        }).start();
    }

    private void removeInboxItem(final String code) {
        new Thread(() -> {
            try {
                Api.delete(Api.BASE + "/api/devices/" + deviceId() + "/inbox/" + code, deviceToken());
                ui.post(() -> {
                    toast("已移出收件箱");
                    loadInbox();
                });
            } catch (final Exception e) {
                ui.post(() -> toast("移出失败：" + e.getMessage()));
            }
        }).start();
    }

    /** 下载一条收件箱文件并交给系统打开。 */
    private void downloadItem(final String url, final String filename, final String mime) {
        toast("开始下载 " + filename);
        new Thread(() -> {
            try {
                InputStream in = Api.openDownload(url);
                final Saved saved = saveDownloaded(in, filename, mime);
                ui.post(() -> openSaved(saved, filename, mime));
            } catch (final Exception e) {
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> toast("下载失败：" + message));
            }
        }).start();
    }

    /** 存一份下载结果：新系统给 content:// URI（能交给别的应用打开），老系统只给路径。 */
    private static final class Saved {
        final Uri uri;
        final String path;

        Saved(Uri uri, String path) {
            this.uri = uri;
            this.path = path;
        }
    }

    private Saved saveDownloaded(InputStream in, String filename, String mime) throws IOException {
        String safe = Api.sanitizeName(filename);
        String type = (mime == null || mime.isEmpty()) ? "*/*" : mime;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // 安卓 10+：写进系统「下载」目录，不需要任何存储权限，别的应用也能打开
            ContentValues values = new ContentValues();
            values.put(MediaStore.Downloads.DISPLAY_NAME, safe);
            values.put(MediaStore.Downloads.MIME_TYPE, type);
            values.put(MediaStore.Downloads.IS_PENDING, 1);
            Uri target = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (target == null) {
                throw new IOException("系统不允许写入「下载」目录");
            }
            OutputStream out = getContentResolver().openOutputStream(target);
            if (out == null) {
                throw new IOException("打不开「下载」目录里的目标文件");
            }
            copy(in, out);
            values.clear();
            values.put(MediaStore.Downloads.IS_PENDING, 0);
            getContentResolver().update(target, values, null, null);
            return new Saved(target, null);
        }
        // 安卓 9 及更早：没有 FileProvider 就没法把文件交给别的应用打开，只存在应用自己的目录里
        File dir = new File(getExternalFilesDir(null), "inbox");
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IOException("建不了保存目录");
        }
        File target = new File(dir, safe);
        OutputStream out = new FileOutputStream(target);
        copy(in, out);
        return new Saved(null, target.getAbsolutePath());
    }

    private void openSaved(Saved saved, String filename, String mime) {
        if (saved.uri == null) {
            toast("已保存到 " + saved.path + "（这台安卓版本没法直接用别的应用打开）");
            return;
        }
        String type = (mime == null || mime.isEmpty()) ? "*/*" : mime;
        Intent view = new Intent(Intent.ACTION_VIEW);
        view.setDataAndType(saved.uri, type);
        view.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(view);
            toast("已保存到「下载」：" + filename);
        } catch (ActivityNotFoundException e) {
            toast("已保存到「下载」：" + filename + "（没有能打开它的应用）");
        }
    }

    private void copy(InputStream in, OutputStream out) throws IOException {
        try {
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) {
                out.write(buffer, 0, read);
            }
            out.flush();
        } finally {
            try {
                in.close();
            } catch (IOException ignored) {
            }
            try {
                out.close();
            } catch (IOException ignored) {
            }
        }
    }

    /* ================================================================ 令牌 / 设备维护 */

    private void exportToken() {
        if (device == null) {
            return;
        }
        final String json = "{\n  \"sharelink_device\": 1,\n  \"id\": \"" + deviceId() + "\",\n"
                + "  \"name\": \"" + deviceName() + "\",\n  \"token\": \"" + deviceToken() + "\"\n}";
        copyToClipboard("ShareLink 设备令牌", json);
        new AlertDialog.Builder(this)
                .setTitle("令牌已复制到剪贴板")
                .setMessage("它就是这台设备的凭证（别发给别人）。可以在别的浏览器 / 手机上导入，认领同一台设备。")
                .setPositiveButton("分享出去", (dialog, which) -> {
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType("text/plain");
                    send.putExtra(Intent.EXTRA_TEXT, json);
                    startActivity(Intent.createChooser(send, "把设备令牌发给自己"));
                })
                .setNegativeButton("好", null)
                .show();
    }

    private void importTokenFromClipboard() {
        String text = readClipboard();
        if (text == null || text.trim().isEmpty()) {
            toast("剪贴板是空的：先在别处复制令牌 JSON");
            return;
        }
        String id;
        String token;
        String name = "已恢复设备";
        try {
            JSONObject json = new JSONObject(text.trim());
            id = json.optString("id", json.optString("device_id", "")).trim();
            token = json.optString("token", json.optString("device_token", "")).trim();
            if (!json.optString("name").trim().isEmpty()) {
                name = json.optString("name").trim();
            }
        } catch (Exception e) {
            toast("剪贴板里不是合法的令牌 JSON");
            return;
        }
        if (id.isEmpty() || token.isEmpty()) {
            toast("JSON 里缺 id 或 token");
            return;
        }
        final String finalId = id;
        final String finalToken = token;
        final String finalName = name;
        showFlowBusy("正在用这个令牌读收件箱…");
        new Thread(() -> {
            try {
                JSONObject inbox = new JSONObject(Api.get(Api.BASE + "/api/devices/" + finalId + "/inbox", finalToken));
                JSONObject own = inbox.optJSONObject("device");
                final String realName = own != null && !own.optString("name").isEmpty()
                        ? own.optString("name") : finalName;
                saveDevice(finalId, finalToken, realName);
                ui.post(() -> {
                    toast("已认领设备「" + deviceName() + "」");
                    showHome();
                });
            } catch (final Exception e) {
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> showFlowMessage("导入失败：" + message));
            }
        }).start();
    }

    private void renameDevice() {
        if (device == null) {
            return;
        }
        final EditText input = new EditText(this);
        input.setText(deviceName());
        input.setInputType(InputType.TYPE_CLASS_TEXT);
        input.setTextColor(FG);
        LinearLayout wrapper = new LinearLayout(this);
        int pad = dp(16);
        wrapper.setPadding(pad, pad, pad, 0);
        wrapper.addView(input);
        new AlertDialog.Builder(this)
                .setTitle("设备名")
                .setView(wrapper)
                .setPositiveButton("保存", (dialog, which) -> {
                    String wanted = input.getText().toString().trim();
                    if (wanted.isEmpty()) {
                        toast("名字不能为空");
                        return;
                    }
                    changeName(wanted);
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void changeName(final String name) {
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("name", name);
                Api.patchJson(Api.BASE + "/api/devices/" + deviceId(), deviceToken(), body.toString());
                saveDevice(deviceId(), deviceToken(), name);
                ui.post(() -> {
                    toast("已改名为「" + name + "」");
                    showHome();
                });
            } catch (final Exception e) {
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> toast("改名失败：" + message));
            }
        }).start();
    }

    private void confirmLeave() {
        new AlertDialog.Builder(this)
                .setTitle("注销这台设备？")
                .setMessage("注销后别的设备发不过来了，本机令牌也会被清掉。已上传的文件不受影响。")
                .setPositiveButton("注销", (dialog, which) -> new Thread(() -> {
                    try {
                        Api.delete(Api.BASE + "/api/devices/" + deviceId(), deviceToken());
                        clearDevice();
                        ui.post(() -> {
                            toast("已注销这台设备");
                            showHome();
                        });
                    } catch (final Exception e) {
                        final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                        ui.post(() -> toast("注销失败：" + message));
                    }
                }).start())
                .setNegativeButton("取消", null)
                .show();
    }

    /* ================================================================ UI 构件 */

    private View buildShell() {
        ScrollView scroller = new ScrollView(this);
        scroller.setBackgroundColor(BG);
        box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        scroller.addView(box);
        return scroller;
    }

    private void show(View content) {
        box.removeAllViews();
        box.addView(content);
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(18);
        layout.setPadding(pad, dp(26), pad, dp(32));
        return layout;
    }

    private LinearLayout card() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(14);
        layout.setPadding(pad, pad, pad, pad);
        layout.setBackground(rounded(CARD, 14));
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        return layout;
    }

    private TextView title(String text) {
        TextView view = line(text, FG, 22);
        view.setTypeface(Typeface.DEFAULT_BOLD);
        view.setPadding(0, 0, 0, dp(12));
        return view;
    }

    private TextView line(String text, int color, int sizeSp) {
        TextView view = new TextView(this);
        view.setText(text == null ? "" : text);
        view.setTextColor(color);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        return view;
    }

    private Button button(String text, boolean primary) {
        Button view = new Button(this);
        view.setText(text);
        view.setAllCaps(false);
        view.setTextColor(primary ? BG : FG);
        view.setBackground(rounded(primary ? ACCENT : CARD_SOFT, 12));
        // 内边距自己给：换成自定义背景后系统 9-patch 自带的那点留白就没了，
        // 而各 ROM 默认的按钮内边距差别很大（有的几乎让文字贴着边框）。
        view.setPadding(dp(18), dp(11), dp(18), dp(11));
        view.setMinHeight(dp(46));
        view.setMinimumWidth(0);           // 短标签也别被系统最小宽度撑得怪
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.rightMargin = dp(10);       // 同一行里按钮之间留缝
        params.bottomMargin = dp(6);
        view.setLayoutParams(params);
        return view;
    }

    /** 给子视图加上边距（顺带把输入框、进度条拉满宽度）。 */
    private <T extends View> T withTop(T view, int dpTop) {
        ViewGroup.LayoutParams raw = view.getLayoutParams();
        LinearLayout.LayoutParams params = raw instanceof LinearLayout.LayoutParams
                ? (LinearLayout.LayoutParams) raw
                : new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(dpTop);
        if (view instanceof EditText || view instanceof ProgressBar) {
            params.width = ViewGroup.LayoutParams.MATCH_PARENT;
        }
        view.setLayoutParams(params);
        return view;
    }

    private GradientDrawable rounded(int color, int radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }

    private void copyToClipboard(String label, String text) {
        ClipboardManager manager = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager != null) {
            manager.setPrimaryClip(ClipData.newPlainText(label, text));
        }
    }

    private String readClipboard() {
        ClipboardManager manager = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (manager == null || manager.getPrimaryClip() == null || manager.getPrimaryClip().getItemCount() == 0) {
            return null;
        }
        CharSequence text = manager.getPrimaryClip().getItemAt(0).coerceToText(this);
        return text == null ? null : text.toString();
    }

    private void openUrl(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (ActivityNotFoundException e) {
            toast("没有可用的浏览器");
        }
    }

    private String shortUrl(String url) {
        return url == null ? "" : url.replace("https://", "");
    }

    /* ================================================================ 分享载荷 */

    private static boolean isShareIntent(Intent intent) {
        if (intent == null || intent.getAction() == null) {
            return false;
        }
        return Intent.ACTION_SEND.equals(intent.getAction())
                || Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction());
    }

    /** 从系统分享里取出要传的东西。 */
    static final class Payload {
        final ArrayList<Uri> uris = new ArrayList<>();
        String title = "";
        String text = "";

        static Payload from(Intent intent) {
            Payload payload = new Payload();
            payload.title = safe(intent.getStringExtra(Intent.EXTRA_SUBJECT));
            payload.text = safe(intent.getStringExtra(Intent.EXTRA_TEXT));
            if (Intent.ACTION_SEND.equals(intent.getAction())) {
                Uri uri = uriExtra(intent);
                if (uri != null) {
                    payload.uris.add(uri);
                }
            } else if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
                ArrayList<Uri> list = listExtra(intent);
                if (list != null) {
                    for (Uri uri : list) {
                        if (uri != null) {
                            payload.uris.add(uri);
                        }
                    }
                }
            }
            return payload;
        }

        boolean isEmpty() {
            return uris.isEmpty() && title.trim().isEmpty() && text.trim().isEmpty();
        }

        @SuppressWarnings("deprecation")
        private static Uri uriExtra(Intent intent) {
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
        private static ArrayList<Uri> listExtra(Intent intent) {
            ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) {
                return list;
            }
            ArrayList<Uri> fromClip = new ArrayList<>();
            ClipData clip = intent.getClipData();
            if (clip != null) {
                for (int i = 0; i < clip.getItemCount(); i++) {
                    if (clip.getItemAt(i).getUri() != null) {
                        fromClip.add(clip.getItemAt(i).getUri());
                    }
                }
            }
            return fromClip;
        }

        private static String safe(String value) {
            return value == null ? "" : value;
        }
    }
}
