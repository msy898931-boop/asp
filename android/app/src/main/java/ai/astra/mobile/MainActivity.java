package ai.astra.mobile;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
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

    // Default target; overridden by build.sh --url. Points at the live server:
    // loopback would be the phone itself and open an empty screen.
    private static final String DEFAULT_URL = "https://astra-xlp9.onrender.com/";

    // Optional gate key baked in by build.sh --key for a *personal* build, so
    // the app opens straight to the data with no typing. It is empty by
    // default and must stay empty in the public rolling release: the APK is a
    // public download, and a key in it would hand the gate to everyone. Build
    // with --key locally when you want the zero-tap experience.
    private static final String DEFAULT_KEY = "";

    private static final String PREFS = "astra";
    private static final String KEY_URL = "server_url";
    private static final String KEY_ACCESS = "access_key";

    private WebView web;
    private FrameLayout root;
    private TextView error;
    // True once /unlock has been followed to a clean "/" after a key entry, so
    // onPageFinished does not redirect again.
    private boolean onUnlockLoaded;
    // True once a page has ever loaded. Distinguishes a first-load 401 (auto
    // unlock with the saved key) from a later one (the key is wrong — ask).
    private boolean loadedOnce;

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

        // The server may gate every route behind a key and hand out an
        // HttpOnly astra_key cookie on a first /unlock?k= visit. Let the WebView
        // accept and keep that cookie: without this the app answers 401 and
        // looks broken whenever the gate is on. Cookies stay on the device.
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            cm.setAcceptThirdPartyCookies(web, true);
        }

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                loadedOnce = true;
                error.setVisibility(View.GONE);
                web.setVisibility(View.VISIBLE);
                // Persist any cookie the gate just set so the session survives
                // the app being closed; the WebView flush is async and easy to
                // lose on a process that is killed.
                CookieManager.getInstance().flush();
                // With a stored or baked-in key, establish the gate's cookie
                // once the host is reachable. The HttpOnly cookie persists
                // after this single visit; when the gate is off, /unlock
                // redirects straight to "/".
                if (!onUnlockLoaded && !accessKey().isEmpty()) {
                    loadUnlock();
                }
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

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            android.webkit.WebResourceResponse response) {
                // The gate answers 401 for the whole app. On the first load the
                // saved key auto-unlocks, so wait for that instead of racing it
                // with a prompt; a 401 after a page has loaded means the key is
                // wrong, so ask for a new one rather than loop.
                if (request != null && request.isForMainFrame()
                        && response != null
                        && response.getStatusCode() == 401
                        && loadedOnce) {
                    promptForKey();
                }
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

        // A saved address wins. Otherwise, if a reachable server is baked in,
        // go straight to it: the point of the app is to show the signals, not
        // to make the user type a URL first. If that server is down, the error
        // screen still lets them point somewhere else.
        if (hasSavedUrl()) {
            web.loadUrl(serverUrl());
        } else if (!DEFAULT_URL.isEmpty()) {
            web.loadUrl(DEFAULT_URL);
        } else {
            showSetup();
        }
    }

    private boolean hasSavedUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).contains(KEY_URL);
    }

    private String serverUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_URL, DEFAULT_URL);
    }

    private TextView buildErrorView() {
        TextView t = new TextView(this);
        t.setText("ASTRA\n\nأدخل عنوان الخادم للبدء.\n\nاضغط هنا.");
        t.setTextColor(Color.parseColor("#ECE6D8"));
        t.setBackgroundColor(Color.parseColor("#0B0E13"));
        t.setGravity(android.view.Gravity.CENTER);
        t.setPadding(48, 48, 48, 48);
        t.setTextSize(16);
        t.setTextDirection(View.TEXT_DIRECTION_RTL);
        t.setOnClickListener(v -> onErrorTap());
        return t;
    }

    // A free host sleeps when idle, so the first open after a while can fail
    // even though the address is right. Offer a retry before asking the user
    // to retype anything. The key is a separate action: a 401 is not a network
    // failure, and conflating the two sends the user to the wrong screen.
    private void onErrorTap() {
        new android.app.AlertDialog.Builder(this)
                .setTitle("تعذّر الوصول إلى خادم ASTRA")
                .setMessage(serverUrl())
                .setPositiveButton("إعادة المحاولة", (d, w) -> {
                    error.setVisibility(View.GONE);
                    web.setVisibility(View.VISIBLE);
                    web.loadUrl(serverUrl());
                })
                .setNeutralButton("مفتاح الوصول", (d, w) -> promptForKey())
                .setNegativeButton("تغيير العنوان", (d, w) -> promptForUrl())
                .show();
    }

    /** The access key the gate expects: the entered one, else a baked-in one. */
    private String accessKey() {
        String saved = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_ACCESS, "");
        return saved.isEmpty() ? DEFAULT_KEY : saved;
    }

    /** Follow the gate's /unlock with the current key; the cookie lands there. */
    private void loadUnlock() {
        onUnlockLoaded = true;
        error.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        Uri base = Uri.parse(serverUrl());
        String unlock = base.buildUpon()
                .encodedPath("/unlock")
                .encodedQuery("k=" + Uri.encode(accessKey())).build().toString();
        Toast.makeText(this, "جارٍ الدخول…", Toast.LENGTH_SHORT).show();
        web.loadUrl(unlock);
    }

    /**
     * Ask for the server's access key and store the cookie via /unlock?k=.
     *
     * The key is kept on the device so changing the address can re-establish
     * the cookie without retyping it. HttpOnly means the WebView, not this code,
     * holds the actual cookie; this is only the seed for the first visit.
     */
    private void promptForKey() {
        final android.widget.EditText input = new android.widget.EditText(this);
        input.setText(accessKey());
        input.setHint("مفتاح الوصول");
        input.setTextDirection(View.TEXT_DIRECTION_LTR);
        new android.app.AlertDialog.Builder(this)
                .setTitle("مفتاح الوصول إلى ASTRA")
                .setMessage("أدخله مرة واحدة؛ يُحفظ على الجهاز ويُنشئ جلسة آمنة.")
                .setView(input)
                .setPositiveButton("دخول", (d, w) -> {
                    String key = input.getText().toString().trim();
                    if (key.isEmpty()) {
                        return;
                    }
                    getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit().putString(KEY_ACCESS, key).apply();
                    loadUnlock();
                })
                // Cancelling a wrong-key prompt must not leave a blank WebView
                // with no way out: fall back to the tap-to-retry error screen.
                .setNegativeButton("إلغاء", (d, w) -> {
                    if (!loadedOnce) {
                        showError();
                    }
                })
                .show();
    }

    private void showError() {
        web.setVisibility(View.GONE);
        error.setText("تعذّر الوصول إلى خادم ASTRA.\n\nاضغط لإعادة المحاولة أو تغيير العنوان.");
        error.setVisibility(View.VISIBLE);
    }

    private void showSetup() {
        web.setVisibility(View.GONE);
        error.setText("ASTRA\n\nأدخل عنوان الخادم للبدء.\n\nاضغط هنا.");
        error.setVisibility(View.VISIBLE);
        promptForUrl();
    }

    private void promptForUrl() {
        final android.widget.EditText input = new android.widget.EditText(this);
        input.setText(serverUrl());
        input.setHint("http://host:12001/");
        // A URL is written left-to-right even in an Arabic UI.
        input.setTextDirection(View.TEXT_DIRECTION_LTR);
        android.app.AlertDialog.Builder b = new android.app.AlertDialog.Builder(this)
                .setTitle("عنوان خادم ASTRA")
                .setView(input)
                .setPositiveButton("حفظ", (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty()) {
                        getSharedPreferences(PREFS, MODE_PRIVATE)
                                .edit().putString(KEY_URL, url).apply();
                        Toast.makeText(this, "جارٍ التحميل " + url, Toast.LENGTH_SHORT).show();
                        web.loadUrl(url);
                    }
                })
                .setNegativeButton("إلغاء", null);
        // If a server was baked in at build time, let the user accept it with
        // one tap instead of retyping the address.
        if (!DEFAULT_URL.isEmpty()) {
            b.setNeutralButton("الافتراضي", (d, w) -> {
                getSharedPreferences(PREFS, MODE_PRIVATE)
                        .edit().putString(KEY_URL, DEFAULT_URL).apply();
                web.loadUrl(DEFAULT_URL);
            });
        }
        b.show();
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
