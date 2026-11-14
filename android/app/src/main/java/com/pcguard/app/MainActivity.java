package com.pcguard.app;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Thin native shell around the PC Guard web page.
 * - First launch asks for the server URL (https://...), then opens it full screen.
 * - While a remote session is active, the phone's volume buttons control the LAPTOP volume.
 * - Keeps the screen awake during a session.
 */
public class MainActivity extends Activity {
    private static final String PREFS = "pcguard";
    private static final String KEY_URL = "url";
    private static final int BG = 0xFF0B0E14;

    private WebView web;
    private volatile boolean capture = false;

    /** Exposed to the page as window.PCGuardNative */
    private class Bridge {
        @JavascriptInterface
        public void session(final boolean active) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    capture = active;
                    if (web != null) web.setKeepScreenOn(active);
                }
            });
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        String url = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, null);
        if (url == null) {
            showSetup();
        } else {
            showWeb(url);
        }
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable rounded(int color, int radiusDp, int strokeColor) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(radiusDp));
        if (strokeColor != 0) d.setStroke(dp(1), strokeColor);
        return d;
    }

    private void showSetup() {
        capture = false;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setPadding(dp(24), dp(24), dp(24), dp(24));

        TextView title = new TextView(this);
        title.setText("PC Guard");
        title.setTextColor(Color.WHITE);
        title.setTextSize(30);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("Enter your server's HTTPS link (for example https://my-laptop.tailnet.ts.net)");
        hint.setTextColor(0xFF8B97AD);
        hint.setPadding(0, dp(8), 0, dp(20));
        root.addView(hint);

        final EditText input = new EditText(this);
        input.setHint("https://your-server.com");
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(0xFF5B667A);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setPadding(dp(14), dp(14), dp(14), dp(14));
        input.setBackground(rounded(0xFF182030, 12, 0xFF232B3D));
        root.addView(input, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Button save = new Button(this);
        save.setText("Continue");
        save.setAllCaps(false);
        save.setTextColor(Color.WHITE);
        save.setTextSize(16);
        save.setBackground(rounded(0xFF3B82F6, 12, 0));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52));
        lp.topMargin = dp(16);
        save.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String u = input.getText().toString().trim();
                if (u.endsWith("/")) u = u.substring(0, u.length() - 1);
                if (!u.startsWith("https://")) {
                    Toast.makeText(MainActivity.this, "The link must start with https://", Toast.LENGTH_LONG).show();
                    return;
                }
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_URL, u).apply();
                showWeb(u);
            }
        });
        root.addView(save, lp);

        setContentView(root);
    }

    private void showWeb(String url) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.setBackgroundColor(BG);
        web.setWebViewClient(new WebViewClient());
        web.addJavascriptInterface(new Bridge(), "PCGuardNative");
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView change = new TextView(this);
        change.setText("Change server");
        change.setTextColor(0xFF5B667A);
        change.setTextSize(11);
        change.setGravity(Gravity.CENTER);
        change.setPadding(0, dp(6), 0, dp(6));
        change.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(KEY_URL).apply();
                if (web != null) {
                    web.destroy();
                    web = null;
                }
                showSetup();
            }
        });
        root.addView(change);

        setContentView(root);
        web.loadUrl(url);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (capture && web != null
                && (keyCode == KeyEvent.KEYCODE_VOLUME_UP || keyCode == KeyEvent.KEYCODE_VOLUME_DOWN)) {
            int d = keyCode == KeyEvent.KEYCODE_VOLUME_UP ? 1 : -1;
            web.evaluateJavascript("window.pcgVolKey && window.pcgVolKey(" + d + ")", null);
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (capture && (keyCode == KeyEvent.KEYCODE_VOLUME_UP || keyCode == KeyEvent.KEYCODE_VOLUME_DOWN)) {
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }

    @Override
    public void onBackPressed() {
        if (web == null) {
            super.onBackPressed();
            return;
        }
        web.evaluateJavascript("(window.pcgBack && window.pcgBack()) ? '1' : '0'", value -> {
            if (!"\"1\"".equals(value)) {
                MainActivity.super.onBackPressed();
            }
        });
    }
}
