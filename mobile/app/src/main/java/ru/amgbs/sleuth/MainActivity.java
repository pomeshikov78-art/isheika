package ru.amgbs.sleuth;

import android.annotation.SuppressLint;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

/**
 * Мобильный клиент «Ищейки».
 *
 * Система серверная: анализ ведут тринадцать агентов на сервере, телефон
 * лишь показывает интерфейс. Поэтому приложение - это WebView поверх того же
 * веб-интерфейса, что открывается в браузере на компьютере. Смысл отдельного
 * приложения в том, что адрес сервера запоминается, и аналитик открывает
 * систему одним касанием, без набора адреса.
 */
public class MainActivity extends AppCompatActivity {

    private static final String PREFS = "sleuth";
    private static final String KEY_SERVER = "server_url";

    private WebView webView;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);

        String server = prefs.getString(KEY_SERVER, null);
        if (server == null || server.isEmpty()) {
            showSetup(null);
        } else {
            showWeb(server);
        }
    }

    /** Экран ввода адреса сервера. */
    private void showSetup(String error) {
        setContentView(R.layout.activity_setup);

        EditText input = findViewById(R.id.serverUrl);
        Button connect = findViewById(R.id.connectButton);
        TextView errorText = findViewById(R.id.errorText);

        String saved = prefs.getString(KEY_SERVER, "");
        if (!saved.isEmpty()) {
            input.setText(saved);
        }
        if (error != null) {
            errorText.setText(error);
            errorText.setVisibility(TextView.VISIBLE);
        }

        connect.setOnClickListener(view -> {
            String url = normalize(input.getText().toString());
            if (url == null) {
                Toast.makeText(this, R.string.no_server, Toast.LENGTH_SHORT).show();
                return;
            }
            prefs.edit().putString(KEY_SERVER, url).apply();
            showWeb(url);
        });
    }

    /**
     * Приводит введённое к рабочему адресу: пользователь чаще всего пишет
     * «192.168.1.10:8123» без схемы, и без этой обработки WebView такой
     * адрес не откроет.
     */
    private String normalize(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) {
            return null;
        }
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        return url;
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void showWeb(String server) {
        setContentView(R.layout.activity_main);
        webView = findViewById(R.id.webView);

        WebSettings settings = webView.getSettings();
        // Интерфейс держится на JavaScript и WebSocket, без них смысла нет
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;  // навигация остаётся внутри приложения
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // Ошибка подпапки не должна выкидывать пользователя на настройку
                if (request.isForMainFrame()) {
                    showSetup(getString(R.string.error_connect));
                }
            }
        });

        webView.loadUrl(server);
    }

    /** Кнопка «назад» листает историю интерфейса, а не закрывает приложение. */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView != null && webView.canGoBack()) {
            webView.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }
}
