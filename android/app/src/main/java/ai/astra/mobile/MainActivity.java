package ai.astra.mobile;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * A thin, honest wrapper around the ASTRA web app.
 *
 * The app is a PWA; this shell gives it a real launcher icon and a full-screen
 * window on Android. The server URL is configurable at build time (see
 * ASTRA_URL in build.sh) and can also be changed at runtime by tapping the
 * error screen, so a rebuilt APK is not needed when the server moves.
 */
public class MainActivity extends Activity {

    // Default target; overridden by build.sh --url.
    private static final String DEFAULT_URL = "http://127.0.0.1:12001/";

    private static final String PREFS = "astra";
    private static final String KEY_URL = "server_url";

    private WebView web;
    private FrameLayout root;
    private TextView error;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0B0E13"));

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);           // the app uses localStorage
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                error.setVisibility(View.GONE);
                web.setVisibility(View.VISIBLE);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError err) {
                if (request != null && request.isForMainFrame()) {
                    showError();
                }
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(WebView view, int code, String desc, String url) {
                showError();
            }
        });

        error = buildErrorView();
        error.setVisibility(View.GONE);

        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(error, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        web.loadUrl(serverUrl());
    }

    private String serverUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_URL, DEFAULT_URL);
    }

    private TextView buildErrorView() {
        TextView t = new TextView(this);
        t.setText("Can't reach the ASTRA server.\n\nTap to change the address.");
        t.setTextColor(Color.parseColor("#ECE6D8"));
        t.setBackgroundColor(Color.parseColor("#0B0E13"));
        t.setGravity(android.view.Gravity.CENTER);
        t.setPadding(48, 48, 48, 48);
        t.setTextSize(16);
        t.setOnClickListener(v -> promptForUrl());
        return t;
    }

    private void showError() {
        web.setVisibility(View.GONE);
        error.setVisibility(View.VISIBLE);
    }

    private void promptForUrl() {
        final android.widget.EditText input = new android.widget.EditText(this);
        input.setText(serverUrl());
        input.setHint("http://host:12001/");
        new android.app.AlertDialog.Builder(this)
                .setTitle("ASTRA server address")
                .setView(input)
                .setPositiveButton("Save", (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty()) {
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit().putString(KEY_URL, url).apply();
                        Toast.makeText(this, "Loading " + url, Toast.LENGTH_SHORT).show();
                        web.loadUrl(url);
                    }
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
