package com.sharelink.app

import android.app.Activity
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.text.Html
import android.widget.Toast
import androidx.core.content.ContextCompat

class ShareReceiverActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handle(intent)
        finish()
        overridePendingTransition(0, 0)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handle(intent)
        finish()
    }

    private fun handle(intent: Intent?) {
        if (intent == null) return
        if (intent.action != Intent.ACTION_SEND || intent.type != "text/plain") return

        val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()
        val url = extractUrl(text)
        if (url.isNullOrBlank()) {
            toast("Không tìm thấy link video")
            return
        }

        val prefs = getSharedPreferences("sharelink", MODE_PRIVATE)
        val ip = prefs.getString("pc_ip", null)?.trim()
        if (ip.isNullOrBlank()) {
            // Chưa pair — mở MainActivity với URL đã có
            val open = Intent(this, MainActivity::class.java).apply {
                action = Intent.ACTION_SEND
                type = "text/plain"
                putExtra(Intent.EXTRA_TEXT, url)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(open)
            toast("Nhập địa chỉ máy tính trước")
            return
        }

        val svc = Intent(this, ShareLinkService::class.java).apply {
            action = ShareLinkService.ACTION_SEND_URL
            putExtra(ShareLinkService.EXTRA_URL, url)
            putExtra(ShareLinkService.EXTRA_IP, ip)
        }
        ContextCompat.startForegroundService(this, svc)

        val msg = if (ShareLinkService.currentState == ShareLinkService.ConnState.CONNECTED)
            "Đang phát trên máy tính..."
        else
            "Đã xếp hàng — đang kết nối lại..."
        toast(msg)
    }

    private fun extractUrl(text: String?): String? {
        if (text.isNullOrBlank()) return null
        val decoded = htmlDecode(text)
        val match = Regex("""https?://\S+""").find(decoded) ?: return decoded.trim()
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
