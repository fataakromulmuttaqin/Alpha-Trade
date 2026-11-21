package com.fata.alphatrade;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

public class MainActivity extends Activity {
    private WebView wv;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        wv = new WebView(this);
        WebSettings s = wv.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        setContentView(wv);
        wv.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, String url) {
                return false; // semua navigasi di dalam WebView
            }
            @Override public void onPageFinished(WebView v, String url) {
                // halaman error file:// -> kembali ke launcher (URL mati)
                if (url.startsWith("file://") && !url.contains("launcher.html")) {
                    wv.loadUrl("file:///android_asset/launcher.html");
                }
            }
        });
        wv.loadUrl("file:///android_asset/launcher.html");
    }

    @Override public void onBackPressed() {
        if (wv != null && wv.canGoBack()) wv.goBack();
        else super.onBackPressed();
    }
}
