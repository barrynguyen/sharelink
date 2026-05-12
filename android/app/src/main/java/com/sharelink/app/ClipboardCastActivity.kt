package com.sharelink.app

import android.app.Activity
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.text.Html
import android.widget.Toast
import androidx.core.content.ContextCompat

class ClipboardCastActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Activity launched from notification action has focus → can read clipboard on Android 10+
        window.decorView.post {
            castFromClipboard()
            finish()
            overridePendingTransition(0, 0)
        }
    }

    private fun castFromClipboard() {
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val clip = cm?.primaryClip
        if (clip == null || clip.itemCount == 0) {
            toast("Clipboard trống")
            return
        }
        val text = clip.getItemAt(0).coerceToText(this)?.toString().orEmpty()
        val url = extractUrl(text)
        if (url.isNullOrBlank()) {
            toast("Clipboard không có link video")
            return
        }

        val ip = getSharedPreferences("sharelink", MODE_PRIVATE)
            .getString("pc_ip", null)?.trim()
        if (ip.isNullOrBlank()) {
            toast("Chưa kết nối máy tính")
            startActivity(Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }

        val svc = Intent(this, ShareLinkService::class.java).apply {
            action = ShareLinkService.ACTION_SEND_URL
            putExtra(ShareLinkService.EXTRA_URL, url)
            putExtra(ShareLinkService.EXTRA_IP, ip)
        }
        ContextCompat.startForegroundService(this, svc)

        val msg = if (ShareLinkService.currentState == ShareLinkService.ConnState.CONNECTED)
            "Đang phát: $url"
        else
            "Đã xếp hàng — đang kết nối lại..."
        toast(msg)
    }

    private fun extractUrl(text: String?): String? {
        if (text.isNullOrBlank()) return null
        val decoded = htmlDecode(text)
        val match = Regex("""https?://\S+""").find(decoded) ?: return null
        return match.value.trimEnd(')', ']', '.', ',')
    }

    private fun htmlDecode(s: String): String {
        var cur = s
        repeat(3) {
            val next = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                Html.fromHtml(cur, Html.FROM_HTML_MODE_LEGACY).toString()
            } else {
                @Suppress("DEPRECATION") Html.fromHtml(cur).toString()
            }
            if (next == cur) return cur
            cur = next
        }
        return cur
    }

    private fun toast(msg: String) =
        Toast.makeText(applicationContext, msg, Toast.LENGTH_SHORT).show()
}
