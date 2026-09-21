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
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
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
import java.util.function.BiConsumer;
import java.util.function.Consumer;

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
    private Button inboxRefresh;
    private LinearLayout groupBox;            // 设备组列表（每次刷新重建里面的行）
    private TextView groupStatus;             // 设备组状态行
    private AlertDialog activeDialog;         // 当前打开的组详情框（组变更后自动关掉）
    private TextView ttlStatus;       // 「上传有效期」卡上的当前选择说明
    private TextView codeStatus;      // 「凭分享码下载」卡上的进度 / 报错说明

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

    /**
     * 服务端已经不认这台设备（管理员清理过 / 换了浏览器 / 重装）→ 清掉本机缓存的那台：
     * 否则设备页一直显示一台服务端没有的设备，注销也注销不掉、设备组也建不了。
     * 返回 true 表示这种情况已处理，调用方不要再弹原来的报错。
     */
    private boolean handleDeviceGone(Exception e) {
        if (!(e instanceof Api.HttpException) || !((Api.HttpException) e).isDeviceGone()) {
            return false;
        }
        clearDevice();
        ui.post(() -> {
            toast("服务端已经没有这台设备了，已清除本机登记：重新「创建设备组」或「加入设备组」即可");
            showHome();
        });
        return true;
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
            final List<String[]> others = fetchOtherDevices();
            ui.post(() -> {
                if (others.isEmpty()) {
                    toast("没有别的设备，直接生成分享码");   // 免得以为"怎么没弹选择框"
                    doUpload(payload, null, null);
                } else {
                    askDestination(payload, others);
                }
            });
        }).start();
    }

    private void askDestination(final Payload payload, final List<String[]> others) {
        chooseDevices(others,
                (ids, names) -> doUpload(payload, ids, names),
                () -> doUpload(payload, null, null),
                () -> showHome());
    }

    /**
     * 后台拉「自己 + 同组设备」（排除自己）；出错就当没有别的设备，不让发送流程崩掉。
     *
     * 服务端 v1.13 起只按令牌返回同组设备，所以这里必须带上 device_id 与令牌；
     * 每台设备还会附上"我们共同的设备组"，用来在选择框里标出它属于哪个组。
     */
    private List<String[]> fetchOtherDevices() {
        final List<String[]> others = new ArrayList<>();
        try {
            String url = Api.URL_DEVICES + "?device_id=" + deviceId();
            JSONObject res = new JSONObject(Api.get(url, deviceToken()));
            JSONArray arr = res.optJSONArray("devices");
            if (arr != null) {
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject item = arr.getJSONObject(i);
                    if (deviceId().equals(item.optString("id"))) {
                        continue;
                    }
                    StringBuilder labels = new StringBuilder();
                    JSONArray groupsJson = item.optJSONArray("shared_groups");
                    if (groupsJson != null) {
                        for (int g = 0; g < groupsJson.length(); g++) {
                            if (labels.length() > 0) {
                                labels.append("、");
                            }
                            labels.append(groupsJson.getJSONObject(g).optString("name"));
                        }
                    }
                    others.add(new String[]{item.optString("id"), item.optString("name"), labels.toString()});
                }
            }
        } catch (Exception ignored) {
            // 拿不到设备列表 → 退回"只拿分享码"
        }
        return others;
    }

    /** 文件/文本共用的目标选择框（自建勾选列表 + 不做静默降级，见 v1.8 的说明）。 */
    private void chooseDevices(final List<String[]> others,
                               final BiConsumer<List<String>, List<String>> onSend,
                               final Runnable onCodeOnly,
                               final Runnable onCancel) {
        // 自己搭勾选列表：不同 ROM 上 setMultiChoiceItems 的列表可能出现整片不显示（高度塌成 0），
        // 那样用户只看到标题和按钮，点「确定」就静默变成"只拿分享码"（== 什么都没发出去）。
        // 另外这里刻意不设文字颜色：对话框主题可能是深色也可能是浅色，跟着主题走才不会看不见。
        final boolean[] checked = new boolean[others.size()];
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        list.setPadding(dp(18), dp(2), dp(18), dp(2));
        TextView hint = new TextView(this);
        hint.setText("勾选要接收的设备，然后点「发送给选中的设备」。");
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        hint.setPadding(dp(6), dp(6), dp(6), dp(10));
        list.addView(hint);
        for (int i = 0; i < others.size(); i++) {
            final int index = i;
            CheckBox box = new CheckBox(this);
            String groupLabel = others.get(i).length > 2 ? others.get(i)[2] : "";
            box.setText(groupLabel.isEmpty() ? others.get(i)[1] : others.get(i)[1] + "（" + groupLabel + "）");
            box.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
            box.setPadding(dp(6), dp(12), dp(6), dp(12));
            box.setMinHeight(dp(48));
            box.setOnCheckedChangeListener((v, isChecked) -> checked[index] = isChecked);
            list.addView(box);
        }
        ScrollView scroller = new ScrollView(this);
        scroller.addView(list);
        final AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("发给哪些设备？（共 " + others.size() + " 台）")
                .setView(scroller)
                .setPositiveButton("发送给选中的设备", null)
                .setNeutralButton("只拿分享码", (d, w) -> onCodeOnly.run())
                .setNegativeButton("取消", (d, w) -> onCancel.run())
                .create();
        dialog.show();
        // 「发送」按钮自己接管：一台都没勾时不静默上传、也不关对话框
        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            List<String> ids = new ArrayList<>();
            List<String> names = new ArrayList<>();
            for (int i = 0; i < checked.length; i++) {
                if (checked[i]) {
                    ids.add(others.get(i)[0]);
                    names.add(others.get(i)[1]);
                }
            }
            if (ids.isEmpty()) {
                toast("先勾一台设备；只要分享码就点「只拿分享码」");
                return;
            }
            dialog.dismiss();
            onSend.accept(ids, names);
        });
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
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
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
        card.addView(line((json.optBoolean("is_text") ? "文本 · " + json.optInt("chars") + " 字符 · " : "")
                + Api.humanSize(json.optLong("size")) + " · " + json.optString("filename")
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
            content.addView(groupCard());                   // 建组/加入会自动登记本机，不再有单独的登记步骤
            content.addView(withTop(ttlCard(), 14));
            content.addView(withTop(textCard(), 14));      // 没登记也能发文本（只拿分享码）
            content.addView(withTop(codeCard(), 14));      // 「凭分享码下载」统一排在最后
        } else {
            content.addView(deviceCard());
            content.addView(withTop(inboxCard(), 14));
            content.addView(withTop(groupCard(), 14));      // 设备组：只有同组设备之间才能互传
            content.addView(withTop(ttlCard(), 14));
            content.addView(withTop(actionsCard(), 14));
            content.addView(withTop(textCard(), 14));      // 发文本：跟发文件一个流程
            content.addView(withTop(codeCard(), 14));      // 接收（凭分享码下载）排到最后
        }
        show(content);
        if (device != null) {
            loadInbox();
        }
    }

    /** 已有令牌的老设备（换手机 / 重装后恢复）：从剪贴板认领原来的设备与收件箱。 */
    private void addTokenImportRows(LinearLayout card) {
        Button importBtn = button("已有令牌？从剪贴板导入", false);
        importBtn.setOnClickListener(v -> importTokenFromClipboard());
        card.addView(withTop(importBtn, 10));
        card.addView(withTop(line("例如先在电脑网页版把令牌复制到剪贴板，再点这里，"
                + "这台手机就会认领同一台设备（连设备组和收件箱一起）。", MUTED, 12), 6));
    }

    /** 没登记就顺手登记一台（名字默认用手机型号），返回 true 表示本次是新建的。 */
    private boolean ensureRegistered() throws Exception {
        if (device != null) {
            return false;
        }
        String model = Build.MODEL == null ? "" : Build.MODEL.trim();
        JSONObject payload = new JSONObject();
        payload.put("name", model.isEmpty() ? "安卓手机" : model);
        JSONObject res = new JSONObject(Api.postJson(Api.URL_DEVICES, null, payload.toString()));
        saveDevice(res.optString("id"), res.optString("token"), res.optString("name"));
        return true;
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
        Button refresh = button("刷新", false);
        refresh.setSingleLine(true);
        refresh.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        LinearLayout.LayoutParams refreshParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        refreshParams.leftMargin = dp(12);
        refresh.setLayoutParams(refreshParams);
        refresh.setOnClickListener(v -> manualRefreshInbox());
        header.addView(refresh);
        inboxRefresh = refresh;                         // loadInbox()/renderInbox() 用它收尾
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
        inboxList.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        card.addView(withTop(inboxList, 10));
        return card;
    }

    /** 分享码字符集与服务端一致（刻意去掉易混的 I O 0 1，见 app/codes.py）。 */
    private static final String CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private static final int CODE_LENGTH = 8;

    /** 文本上限，与服务端 SHARELINK_MAX_TEXT_CHARS 一致（超了服务端也会拦，这里先挡一道）。 */
    private static final int MAX_TEXT_CHARS = 10000;

    /** 凭分享码下载：查信息 → 下载 → 交给系统打开。这个流程不需要设备令牌（未登记也能用）。 */
    private View codeCard() {
        LinearLayout card = card();
        card.addView(line("凭分享码下载", FG, 16));
        card.addView(withTop(line("输入别人给你的 " + CODE_LENGTH
                + " 位分享码，直接下载并用系统应用打开。不用登记设备。", MUTED, 13), 8));

        final EditText input = new EditText(this);
        input.setHint("例如 AB3D7K9M");
        input.setTextColor(FG);
        input.setHintTextColor(MUTED);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        input.setSingleLine(true);
        // 输入框吃掉剩余宽度、按钮固定宽度：一排两件，窄屏也不会被挤出可视区
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        inputParams.rightMargin = dp(10);
        input.setLayoutParams(inputParams);

        final Button fetch = button("下载", true);
        fetch.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout inputRow = row();
        inputRow.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        inputRow.addView(input);
        inputRow.addView(fetch);
        card.addView(withTop(inputRow, 14));

        codeStatus = line("", MUTED, 12);
        card.addView(withTop(codeStatus, 10));

        fetch.setOnClickListener(v -> downloadByCode(input.getText().toString(), fetch));
        return card;
    }

    /** 清洗分享码输入：去掉空格/连字符/点/下划线并转大写（与服务端 normalize_code 一致）。 */
    static String normalizeCode(String raw) {
        return raw == null ? ""
                : raw.replaceAll("[\\s\\-_.]", "").toUpperCase(java.util.Locale.US);
    }

    /** 形如合法分享码（只做本地格式校验，存在性靠服务端）。 */
    private static boolean isCodeShaped(String code) {
        if (code.length() != CODE_LENGTH) {
            return false;
        }
        for (int i = 0; i < code.length(); i++) {
            if (CODE_ALPHABET.indexOf(code.charAt(i)) < 0) {
                return false;
            }
        }
        return true;
    }

    /** 「凭分享码下载」的完整流程（网络都在工作线程，UI 用 ui.post 回主线程）。 */
    private void downloadByCode(String raw, final Button fetch) {
        final String code = normalizeCode(raw);
        if (!isCodeShaped(code)) {
            codeStatus.setTextColor(DANGER);
            codeStatus.setText("分享码应该是 " + CODE_LENGTH + " 位字母数字（没有 I O 0 1 这些易混字符），"
                    + "现在识别到 " + code.length() + " 位");
            return;
        }
        codeStatus.setTextColor(MUTED);
        codeStatus.setText("正在查询 " + code + "…");
        fetch.setEnabled(false);
        new Thread(() -> {
            try {
                JSONObject info = new JSONObject(Api.get(Api.BASE + "/api/files/" + code, null));
                if (info.optBoolean("expired")) {
                    codeFail(fetch, "这个分享已经过期了，文件已被删除");
                    return;
                }
                if (info.optBoolean("is_text")) {
                    // 文本分享：直接取回内容看/复制，不必先下载到「下载」目录再打开
                    final String text = Api.get(Api.BASE + "/api/download/" + code, null);
                    final String textName = info.optString("filename", "文本.txt");
                    ui.post(() -> {
                        fetch.setEnabled(true);
                        codeStatus.setTextColor(MUTED);
                        codeStatus.setText("已读取文本（" + text.length() + " 字符）");
                        showTextDialog(text, textName);
                    });
                    return;
                }
                final String infoName = info.optString("filename", "");
                final long size = info.optLong("size");
                ui.post(() -> codeStatus.setText("找到 " + infoName + "（" + Api.humanSize(size) + " · "
                        + Api.humanLeft(info.optLong("seconds_left")) + "），开始下载…"));
                // 文件名与类型以下载响应为准（中文名在 Content-Disposition 的 filename*= 里）
                Api.Download download = Api.openDownload(
                        Api.BASE + "/api/download/" + code, infoName, info.optString("content_type"));
                final String name = download.filename;
                final Saved saved = saveDownloaded(download.stream, name, download.mime);
                ui.post(() -> {
                    fetch.setEnabled(true);
                    codeStatus.setTextColor(MUTED);
                    codeStatus.setText("已存到「下载」目录：" + name);
                    openSaved(saved, name, download.mime);
                });
            } catch (Api.HttpException e) {
                codeFail(fetch, e.status == 404 ? "没有这个分享码，检查一下有没有输错"
                        : e.status == 410 ? "这个分享已经过期了，文件已被删除"
                        : e.getMessage());
            } catch (IOException e) {
                codeFail(fetch, "网络失败，没能连上服务器（" + e.getMessage() + "）");
            } catch (Exception e) {
                codeFail(fetch, e.getMessage() == null ? e.toString() : e.getMessage());
            }
        }).start();
    }

    /** 出错时恢复按钮，并把原因留在卡片上（toast 一闪而过，看不到）。 */
    private void codeFail(final Button fetch, final String message) {
        ui.post(() -> {
            fetch.setEnabled(true);
            if (codeStatus != null) {
                codeStatus.setTextColor(DANGER);
                codeStatus.setText("下载失败：" + message);
            }
        });
    }

    /** 上传有效期：与服务端 / 网页版一致的五档预设（分享面板上传的文件也用这里选的值）。 */
    private View ttlCard() {
        LinearLayout card = card();
        card.addView(line("上传有效期", FG, 16));
        card.addView(withTop(line("到期后服务器自动删除，不可恢复。分享面板里上传的文件也用这里选的值。",
                MUTED, 13), 8));
        card.addView(withTop(ttlChips(), 12));
        ttlStatus = line("", ACCENT, 13);
        card.addView(withTop(ttlStatus, 10));
        refreshTtlSummary();
        return card;
    }

    /** 当前有效期文案：写出来一眼就能确认设置生效了（也方便排错）。 */
    private void refreshTtlSummary() {
        if (ttlStatus != null) {
            ttlStatus.setText("当前选择：" + Api.humanLeft(ttlSeconds).replace("剩 ", "")
                    + "（" + ttlSeconds + " 秒）");
        }
    }

    private View ttlChips() {
        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        // 宽度按权重等分：按钮宽度由布局决定（不再由文字内容决定），
        // 这样无论字体大小/字体缩放都不会有某个档位被挤出可视区
        wrap.addView(chipRow(wrap, new String[]{"10 分钟", "1 小时", "1 天"},
                new long[]{600L, 3600L, 86400L}, 3));
        wrap.addView(withTop(chipRow(wrap, new String[]{"7 天", "30 天"},
                new long[]{604800L, 2592000L}, 3), 8));
        refreshTtlChips(wrap);
        return wrap;
    }

    /** 一行有效期按钮；cells = 占几格（不满的用不可见占位补上，保证每行等宽对齐）。 */
    private LinearLayout chipRow(final View group, String[] labels, long[] presets, int cells) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setBaselineAligned(false);
        row.setGravity(android.view.Gravity.CENTER_VERTICAL);
        row.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        for (int i = 0; i < cells; i++) {
            View cell;
            if (i < labels.length) {
                final long seconds = presets[i];
                Button chip = chip(labels[i], seconds);
                chip.setOnClickListener(v -> {
                    ttlSeconds = seconds;
                    prefs.edit().putLong(KEY_TTL, seconds).apply();
                    refreshTtlChips(group);
                    refreshTtlSummary();
                });
                cell = chip;
            } else {
                cell = new View(this);          // 占位，只为让上一行的按钮宽度加起来对齐
            }
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            params.rightMargin = i == cells - 1 ? 0 : dp(8);
            cell.setLayoutParams(params);
            row.addView(cell);
        }
        return row;
    }

    private Button chip(String text, long seconds) {
        Button view = button(text, false);
        view.setTag(seconds);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        view.setPadding(dp(6), dp(7), dp(6), dp(7));   // 宽度交给权重，左右少留一点免得文字被挤
        view.setMinHeight(dp(36));
        view.setSingleLine(true);
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

    /** 「发文本」：写一段字，既能只生成分享码，也能挑设备直接投递（跟发文件同一套流程与有效期）。 */
    private View textCard() {
        LinearLayout card = card();
        card.addView(line("发文本", FG, 16));
        card.addView(withTop(line("最多 " + MAX_TEXT_CHARS + " 字符：贴链接、验证码、代码片段都行。"
                + "首行会当文件名，到期同样自动删除。", MUTED, 13), 8));

        final EditText input = new EditText(this);
        input.setHint("粘贴或输入要发送的文字…");
        input.setTextColor(FG);
        input.setHintTextColor(MUTED);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        input.setMinLines(3);
        input.setGravity(Gravity.TOP | Gravity.START);
        input.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        card.addView(withTop(input, 12));

        final TextView counter = line("0 / " + MAX_TEXT_CHARS + " 字符", MUTED, 12);
        card.addView(withTop(counter, 6));

        final Button send = button("发送这段文本", true);
        send.setEnabled(false);
        input.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence text, int start, int count, int after) {
            }

            @Override public void onTextChanged(CharSequence text, int start, int before, int count) {
            }

            @Override public void afterTextChanged(Editable text) {
                int length = text.length();
                boolean tooLong = length > MAX_TEXT_CHARS;
                counter.setText(length + " / " + MAX_TEXT_CHARS + " 字符" + (tooLong ? "（太长了）" : ""));
                counter.setTextColor(tooLong ? DANGER : MUTED);
                send.setEnabled(length > 0 && !tooLong);
            }
        });
        send.setOnClickListener(v -> startTextShare(input.getText().toString()));
        card.addView(withTop(send, 12));
        return card;
    }

    /** 点「发送这段文本」：登记过的设备先问发给谁（含「只拿分享码」），没登记就直接拿码。 */
    private void startTextShare(final String text) {
        if (text.trim().isEmpty()) {
            toast("先写点内容");
            return;
        }
        if (text.length() > MAX_TEXT_CHARS) {
            toast("太长啦：最多 " + MAX_TEXT_CHARS + " 字符");
            return;
        }
        if (device == null) {
            doTextSend(text, null, null);
            return;
        }
        showFlowBusy("正在读取设备列表…");
        new Thread(() -> {
            final List<String[]> others = fetchOtherDevices();
            ui.post(() -> {
                if (others.isEmpty()) {
                    toast("没有别的设备，直接生成分享码");
                    doTextSend(text, null, null);
                } else {
                    chooseDevices(others,
                            (ids, names) -> doTextSend(text, ids, names),
                            () -> doTextSend(text, null, null),
                            () -> showHome());
                }
            });
        }).start();
    }

    /** 发文本：走 JSON 接口，服务端把它存成小 text/plain —— 分享码、有效期、投递、清理全复用。 */
    private void doTextSend(final String text, final List<String> targets, final List<String> targetNames) {
        showFlowBusy(targets == null || targets.isEmpty() ? "正在生成分享码…" : "正在发送文本…");
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("text", text);
                body.put("ttl_seconds", ttlSeconds);
                if (targets != null && !targets.isEmpty()) {
                    JSONArray arr = new JSONArray();
                    for (String id : targets) {
                        arr.put(id);
                    }
                    body.put("targets", arr);
                    if (device != null) {
                        body.put("from_device_id", deviceId());
                    } else {
                        body.put("from_name", "安卓手机");
                    }
                }
                final JSONObject json = new JSONObject(Api.postJson(Api.URL_TEXTS, deviceToken(), body.toString()));
                ui.post(() -> showResult(json, targetNames));
            } catch (Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> showFlowMessage("发送失败：" + message));
            }
        }).start();
    }

    /** 文本内容对话框：能选中复制，也能存成 .txt（文本没必要先落盘再让用户去文件管理器找）。 */
    private void showTextDialog(final String text, final String filename) {
        final TextView body = new TextView(this);
        body.setText(text);
        body.setTextColor(FG);
        body.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        body.setTypeface(Typeface.MONOSPACE);
        body.setTextIsSelectable(true);
        body.setPadding(dp(16), dp(12), dp(16), dp(12));
        ScrollView scroller = new ScrollView(this);
        scroller.addView(body);
        new AlertDialog.Builder(this)
                .setTitle(filename + "（" + text.length() + " 字符）")
                .setView(scroller)
                .setPositiveButton("复制", (d, w) -> {
                    copyToClipboard("ShareLink 文本", text);
                    toast("已复制全文");
                })
                .setNeutralButton("存成文件", (d, w) -> saveTextAsFile(text, filename))
                .setNegativeButton("关闭", null)
                .show();
    }

    /** 把文本写进系统「下载」目录（复用 MediaStore，不需要存储权限）。 */
    private void saveTextAsFile(final String text, final String filename) {
        new Thread(() -> {
            try {
                saveDownloaded(new java.io.ByteArrayInputStream(text.getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                        filename, "text/plain");
                ui.post(() -> toast("已存到「下载」目录：" + filename));
            } catch (Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> toast("保存失败：" + message));
            }
        }).start();
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
                ui.post(() -> {
                    inboxRefreshDone();
                    renderInbox(json);
                });
            } catch (final Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> {
                    inboxRefreshDone();
                    if (inboxStatus != null) {
                        inboxStatus.setText("收件箱读取失败：" + message);
                    }
                });
            }
        }).start();
    }

    /** 收件箱里的文本条目：点一下把内容拉下来直接看。 */
    private void openInboxText(final String downloadUrl, final String filename) {
        toast("正在读取文本…");
        new Thread(() -> {
            try {
                final String text = Api.get(downloadUrl, null);
                ui.post(() -> showTextDialog(text, filename));
            } catch (Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> showFlowMessage("文本读取失败：" + message));
            }
        }).start();
    }

    /** 收件箱「刷新」：手动重拉一次，过程中按钮禁用并给出提示（成功失败都会恢复） */
    private void manualRefreshInbox() {
        if (device == null) {
            return;
        }
        if (inboxRefresh != null) {
            inboxRefresh.setEnabled(false);
            inboxRefresh.setText("刷新中");
        }
        if (inboxStatus != null) {
            inboxStatus.setText("正在刷新收件箱…");
        }
        loadInbox();
    }

    /** 一次收件箱读取结束（成功或失败）后把刷新按钮恢复可用 */
    private void inboxRefreshDone() {
        if (inboxRefresh != null) {
            inboxRefresh.setEnabled(true);
            inboxRefresh.setText("刷新");
        }
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
            final boolean isText = item.optBoolean("is_text");

            LinearLayout rowView = new LinearLayout(this);
            rowView.setOrientation(LinearLayout.VERTICAL);
            rowView.setBackground(rounded(CARD_SOFT, 12));
            int pad = dp(12);
            rowView.setPadding(pad, pad, pad, pad);
            rowView.setLayoutParams(new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            rowView.addView(line((item.optBoolean("seen") ? "" : "● ") + filename, FG, 15));
            String from = item.optString("from_name", "匿名设备");
            rowView.addView(line(Api.humanSize(item.optLong("size")) + " · 来自 " + from + " · "
                    + Api.humanLeft(item.optLong("seconds_left")), MUTED, 12));
            rowView.addView(line(isText ? "点一下看文本内容 · 长按可移出收件箱"
                    : "点一下下载并打开 · 长按可移出收件箱", MUTED, 11));

            rowView.setOnClickListener(v -> {
                if (isText) {
                    openInboxText(downloadUrl, filename);
                } else {
                    downloadItem(downloadUrl, filename, mime);
                }
            });
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
                Api.Download download = Api.openDownload(url, filename, mime);
                final String name = download.filename;
                final Saved saved = saveDownloaded(download.stream, name, download.mime);
                ui.post(() -> openSaved(saved, name, download.mime));
            } catch (final Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
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
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
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
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
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
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                        ui.post(() -> new AlertDialog.Builder(MainActivity.this)
                                .setTitle("注销失败")
                                .setMessage(message + "\n\n要只清除本机登记吗？清除后可以重新「创建设备组」或「加入设备组」。")
                                .setPositiveButton("清除本机登记", (d, w) -> {      // 不能用 dialog/which：外层 lambda 已占用同名参数
                                    clearDevice();
                                    toast("已清除本机登记");
                                    showHome();
                                })
                                .setNegativeButton("取消", null)
                                .show());
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
        // 整屏内容必须撑满宽度：默认 wrap_content 会让"内容窄"的卡片跟着缩，
        // 里面的标题/选项条就被挤成两行或被裁掉
        box.addView(content, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
    }

    // ============================================================ 设备组
    /** 设备组卡片：建组 / 凭组 id 加入 / 我的组（点「管理」看成员与操作）。 */
    private View groupCard() {
        final boolean registered = device != null;
        LinearLayout card = card();
        card.addView(line("设备组", FG, 16));
        card.addView(withTop(line(registered
                ? "只有同一个组里的设备之间才能互传。组 id 就是邀请凭证：复制下来发给别的设备，对方粘贴加入就和你同组了。"
                : "点下面的按钮就会自动把这台手机加进设备列表：创建时你直接进组并成为管理员，"
                  + "加入时用对方给的组 id。名字默认用手机型号，之后在设备页可改。", MUTED, 13), 8));

        LinearLayout buttons = row();
        Button create = button("创建设备组", true);
        create.setOnClickListener(v -> promptDialog("创建设备组", "组名，例如：家里的设备", "", "创建", name -> {
            if (name.isEmpty()) {
                toast("先给组起个名字");
                return;
            }
            groupAction("正在创建设备组…", () -> {
                ensureRegistered();                             // 没登记就顺手登记：创建设备组的设备自动进组
                JSONObject res = groupCall("POST", "", new JSONObject().put("name", name));
                JSONObject group = res.optJSONObject("group");
                return "已建「" + group.optString("name") + "」，本机已在组里（你是管理员）：组 id "
                        + group.optString("id") + "，复制发给别的设备即可加入";
            });
        }));
        LinearLayout.LayoutParams createParams =
                new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        createParams.rightMargin = dp(10);
        buttons.addView(create, createParams);

        Button join = button("加入设备组", false);
        join.setOnClickListener(v -> promptDialog("加入设备组", "粘贴对方给的组 id，例如 grp_7KQ2M4XZ9B3D", "", "加入", raw -> {
            if (raw.isEmpty()) {
                toast("先粘贴组 id");
                return;
            }
            groupAction("正在加入设备组…", () -> {
                ensureRegistered();                             // 没登记就顺手登记，加入后本机就是成员
                JSONObject res = groupCall("POST", "/" + raw + "/join", null);
                JSONObject group = res.optJSONObject("group");
                return res.optBoolean("already_member")
                        ? "你已经在「" + group.optString("name") + "」里了"
                        : "已加入「" + group.optString("name") + "」，现在可以和组里的设备互传了";
            });
        }));
        buttons.addView(join, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        card.addView(withTop(buttons, 10));

        groupStatus = line(registered ? "正在读取设备组…" : "还没有加入任何设备组", MUTED, 12);
        card.addView(withTop(groupStatus, 10));
        groupBox = column();
        groupBox.setPadding(0, 0, 0, 0);
        card.addView(groupBox);
        if (registered) {
            loadGroups();
        } else {
            addTokenImportRows(card);                       // 未登记时给老设备留一条「认领原设备」的路
            card.addView(withTop(line("在相册 / 文件管理里点「分享」→ 选 ShareLink，文件会先上传并给出分享码，"
                    + "也可以顺手发给同组设备。", MUTED, 12), 12));
        }
        return card;
    }

    /** 拉我加入的设备组（每组再拉一次成员名单），完成后在主线程渲染。 */
    private void loadGroups() {
        final LinearLayout box = groupBox;
        final TextView status = groupStatus;
        if (device == null || box == null) {
            return;
        }
        status.setText("正在读取设备组…");
        new Thread(() -> {
            try {
                JSONObject res = new JSONObject(Api.get(Api.URL_GROUPS + "?device_id=" + deviceId(), deviceToken()));
                JSONArray arr = res.optJSONArray("groups");
                final List<JSONObject> groups = new ArrayList<>();
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject group = arr.getJSONObject(i);
                        try {
                            JSONObject detail = new JSONObject(Api.get(Api.URL_GROUPS + "/" + group.optString("id")
                                    + "?device_id=" + deviceId(), deviceToken()));
                            group.put("members", detail.optJSONArray("members"));
                        } catch (Exception ignored) {
                            // 名单只有组内可见；拉不到就只显示成员数
                        }
                        groups.add(group);
                    }
                }
                ui.post(() -> {
                    if (groupBox != box) {              // 界面已经重建过，这次结果丢掉
                        return;
                    }
                    renderGroups(box, status, groups);
                });
            } catch (Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                ui.post(() -> {
                    if (groupStatus == status) {
                        status.setText("设备组读取失败：" + message);
                    }
                });
            }
        }).start();
    }

    private void renderGroups(LinearLayout box, TextView status, List<JSONObject> groups) {
        box.removeAllViews();
        status.setText(groups.isEmpty()
                ? "还没有加入任何设备组"
                : "共 " + groups.size() + " 个设备组（点「管理」看成员与操作）");
        for (JSONObject group : groups) {
            final JSONObject target = group;
            LinearLayout item = column();
            item.setPadding(dp(12), dp(12), dp(12), dp(12));
            item.setBackground(rounded(CARD_SOFT, 12));
            item.addView(line(group.optString("name") + (group.optBoolean("is_owner") ? "（我是管理员）" : ""), FG, 14));
            item.addView(withTop(line(group.optString("id"), MUTED, 12), 4));
            item.addView(withTop(line(group.optInt("member_count") + " 台设备 · 管理里可复制组 id"
                    + (group.optBoolean("is_owner") ? "、移除成员、解散" : "、退出"), MUTED, 12), 4));
            Button manage = button("管理", false);
            manage.setOnClickListener(v -> showGroupDialog(target));
            item.addView(withTop(manage, 8));
            box.addView(withTop(item, 10));
        }
    }

    /** 组详情对话框：成员名单 + 复制组 id /（管理员）改名、解散 /（成员）退出。 */
    private void showGroupDialog(final JSONObject group) {
        final String groupId = group.optString("id");
        final String groupName = group.optString("name");
        final boolean owner = group.optBoolean("is_owner");

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(18), dp(6), dp(18), dp(2));
        body.addView(line("组 id：" + groupId, MUTED, 12));

        JSONArray members = group.optJSONArray("members");
        if (members == null) {
            body.addView(withTop(line("成员 " + group.optInt("member_count") + " 台（名单读取失败，稍后可重试）",
                    MUTED, 12), 8));
        } else {
            for (int i = 0; i < members.length(); i++) {
                JSONObject member = members.optJSONObject(i);
                if (member == null) {
                    continue;
                }
                LinearLayout rowBox = row();
                rowBox.setPadding(0, dp(6), 0, 0);
                String label = member.optString("name") + (member.optBoolean("is_self") ? "（本设备）" : "")
                        + ("owner".equals(member.optString("role")) ? " · 管理员" : " · 成员");
                rowBox.addView(line(label, FG, 13),
                        new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                if (owner && !member.optBoolean("is_self")) {
                    final String memberId = member.optString("id");
                    final String memberName = member.optString("name");
                    Button remove = button("移出", false);
                    remove.setOnClickListener(v -> groupAction("正在移除 " + memberName + "…", () -> {
                        groupCall("DELETE", "/" + groupId + "/members/" + memberId, null);
                        return "已把 " + memberName + " 移出设备组";
                    }));
                    rowBox.addView(remove);
                }
                body.addView(rowBox);
            }
        }

        LinearLayout actions = row();
        Button copy = button("复制组 id", true);
        copy.setOnClickListener(v -> {
            copyToClipboard("ShareLink 设备组 id", groupId);
            toast("组 id 已复制：" + groupId);
        });
        actions.addView(copy);
        if (owner) {
            Button rename = button("改组名", false);
            rename.setOnClickListener(v -> promptDialog("改组名", "新的组名", groupName, "保存",
                    newName -> groupAction("正在改组名…", () -> {
                        groupCall("PATCH", "/" + groupId, new JSONObject().put("name", newName));
                        return "组名已改成「" + newName + "」";
                    })));
            actions.addView(rename);
            Button dissolve = button("解散", false);
            dissolve.setOnClickListener(v -> confirmDialog("解散设备组",
                    "解散「" + groupName + "」？组内成员关系会清空（已经收到的文件不受影响）。", "解散",
                    () -> groupAction("正在解散设备组…", () -> {
                        groupCall("DELETE", "/" + groupId, null);
                        return "已解散「" + groupName + "」";
                    })));
            actions.addView(dissolve);
        } else {
            Button leave = button("退出", false);
            leave.setOnClickListener(v -> confirmDialog("退出设备组",
                    "退出「" + groupName + "」？退出后就不能和组里其他设备互传了。", "退出",
                    () -> groupAction("正在退出设备组…", () -> {
                        groupCall("POST", "/" + groupId + "/leave", null);
                        return "已退出「" + groupName + "」";
                    })));
            actions.addView(leave);
        }
        body.addView(withTop(actions, 12));

        ScrollView scroller = new ScrollView(this);
        scroller.addView(body);
        activeDialog = new AlertDialog.Builder(this)
                .setTitle(groupName)
                .setView(scroller)
                .setNegativeButton("关闭", null)
                .show();
    }

    /** 带输入框的对话框（建组 / 加入 / 改名共用）。 */
    private void promptDialog(String title, String hint, String initial, String confirmLabel,
                              Consumer<String> onConfirm) {
        final EditText input = new EditText(this);
        input.setHint(hint);
        input.setText(initial == null ? "" : initial);
        input.setInputType(InputType.TYPE_CLASS_TEXT);
        input.setSingleLine(true);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(18), dp(4), dp(18), dp(2));
        box.addView(input, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setView(box)
                .setPositiveButton(confirmLabel, (dialog, which) -> onConfirm.accept(input.getText().toString().trim()))
                .setNegativeButton("取消", null)
                .show();
    }

    /** 危险操作确认框（解散 / 退出 / 移出）。 */
    private void confirmDialog(String title, String message, String confirmLabel, Runnable onConfirm) {
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setPositiveButton(confirmLabel, (dialog, which) -> onConfirm.run())
                .setNegativeButton("取消", null)
                .show();
    }

    private interface GroupTask {
        String run() throws Exception;
    }

    /** 后台跑一个设备组操作；成功刷新列表并提示，失败弹出服务端给的中文原因。 */
    private void groupAction(final String doing, final GroupTask task) {
        toast(doing);
        new Thread(() -> {
            try {
                final String message = task.run();
                ui.post(() -> {
                    toast(message);
                    if (activeDialog != null && activeDialog.isShowing()) {
                        activeDialog.dismiss();                 // 组详情已过期，关掉让用户看新的
                    }
                    showHome();                                 // 整页重画：登记后设备卡/收件箱立刻出现
                });
            } catch (Exception e) {
                if (handleDeviceGone(e)) {          // 服务端已经不认这台设备：清本机登记，回到建组/加入
                    return;
                }
                final String message = e.getMessage() == null ? e.toString() : e.getMessage();
                final String label = doing.replace("正在", "").replace("…", "");
                ui.post(() -> showFlowMessage(label + "失败：" + message));
            }
        }).start();
    }

    /** 设备组接口调用：自动带上 device_id 与令牌（DELETE 的 device_id 走查询串，跟网页端一致）。 */
    private JSONObject groupCall(String method, String path, JSONObject body) throws Exception {
        String url = Api.URL_GROUPS + path;
        String query = url.contains("?") ? "&" : "?";
        if ("GET".equals(method)) {
            return new JSONObject(Api.get(url + query + "device_id=" + deviceId(), deviceToken()));
        }
        if ("DELETE".equals(method)) {
            return new JSONObject(Api.delete(url + query + "device_id=" + deviceId(), deviceToken()));
        }
        JSONObject payload = body == null ? new JSONObject() : body;
        if (!payload.has("device_id")) {
            payload.put("device_id", deviceId());
        }
        if ("PATCH".equals(method)) {
            return new JSONObject(Api.patchJson(url, deviceToken(), payload.toString()));
        }
        return new JSONObject(Api.postJson(url, deviceToken(), payload.toString()));
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(18);
        layout.setPadding(pad, dp(26), pad, dp(32));
        layout.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return layout;
    }

    private LinearLayout card() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(14);
        layout.setPadding(pad, pad, pad, pad);
        layout.setBackground(rounded(CARD, 14));
        // 每张卡都撑满整行，宽度不随内容多少变（否则窄内容的卡片会缩成一小块）
        layout.setLayoutParams(new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return layout;
    }

    private LinearLayout row() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        // 不要按文字基线对齐（标题旁放高大按钮时会错位），改成行内元素垂直居中
        layout.setBaselineAligned(false);
        layout.setGravity(android.view.Gravity.CENTER_VERTICAL);
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
