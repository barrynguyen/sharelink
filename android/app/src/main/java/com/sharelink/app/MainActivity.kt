package com.sharelink.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import android.net.Uri
import androidx.core.content.ContextCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

private const val UPDATE_URL = "https://raw.githubusercontent.com/barrynguyen/sharelink/main/version.json"

class MainActivity : AppCompatActivity() {

    private lateinit var etIp: EditText
    private lateinit var etUrl: EditText
    private lateinit var btnConnect: Button
    private lateinit var btnScanQr: Button
    private lateinit var btnShare: Button
    private lateinit var btnFullscreen: Button
    private lateinit var btnPause: Button
    private lateinit var btnStop: Button
    private lateinit var tvStatus: TextView
    private lateinit var statusDot: View

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        val content = result.contents ?: return@registerForActivityResult
        etIp.setText(content)
        connect()
    }

    private val notifPermLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* either way we proceed; Service still works, just no visible notification */ }

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .build()

    private var isVideoPlaying = false
    private var isPaused = false

    private val stateListener = ShareLinkService.StateListener { state, ip ->
        when (state) {
            ShareLinkService.ConnState.CONNECTED -> {
                setStatus(Status.CONNECTED)
                btnConnect.text = "Kết nối lại"
            }
            ShareLinkService.ConnState.CONNECTING -> {
                setStatus(Status.CONNECTING)
            }
            ShareLinkService.ConnState.DISCONNECTED -> {
                setStatus(Status.DISCONNECTED)
                resetControls()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            window.decorView.importantForAutofill =
                View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        }
        setContentView(R.layout.activity_main)

        etIp      = findViewById(R.id.etIp)
        etUrl     = findViewById(R.id.etUrl)
        btnConnect    = findViewById(R.id.btnConnect)
        btnScanQr     = findViewById(R.id.btnScanQr)
        btnShare      = findViewById(R.id.btnShare)
        btnFullscreen = findViewById(R.id.btnFullscreen)
        btnPause      = findViewById(R.id.btnPause)
        btnStop       = findViewById(R.id.btnStop)
        tvStatus  = findViewById(R.id.tvStatus)
        statusDot = findViewById(R.id.statusDot)

        val version = packageManager.getPackageInfo(packageName, 0).versionName
        findViewById<TextView>(R.id.tvVersion).text = "v$version"

        val prefs = getSharedPreferences("sharelink", MODE_PRIVATE)
        etIp.setText(prefs.getString("pc_ip", ""))

        btnConnect.setOnClickListener { connect() }
        btnScanQr.setOnClickListener {
            scanLauncher.launch(ScanOptions().apply {
                setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                setPrompt("Scan QR từ ShareLink trên máy tính")
                setBeepEnabled(false)
                setOrientationLocked(false)
            })
        }

        btnShare.setOnClickListener {
            val url = etUrl.text.toString().trim()
            if (url.isEmpty()) { toast("Nhập hoặc dán link video"); return@setOnClickListener }
            sendUrl(url)
        }

        btnFullscreen.setOnClickListener { sendCommand("fullscreen") }

        btnPause.setOnClickListener {
            sendCommand("pause")
            isPaused = !isPaused
            btnPause.text = if (isPaused) "▶  Tiếp tục" else "⏸  Tạm dừng"
        }

        btnStop.setOnClickListener {
            sendCommand("stop")
            resetControls()
        }

        handleIntent(intent)
        checkForUpdate()
        maybeRequestNotificationPerm()
        publishShareShortcut()
    }

    private fun publishShareShortcut() {
        try {
            val shortcut = ShortcutInfoCompat.Builder(this, "sharelink_cast")
                .setShortLabel("Phát lên máy tính")
                .setLongLabel("ShareLink — phát fullscreen trên máy tính")
                .setIcon(IconCompat.createWithResource(this, R.drawable.ic_shortcut_cast))
                .setIntent(
                    Intent(Intent.ACTION_VIEW)
                        .setClassName(packageName, "com.sharelink.app.MainActivity")
                )
                .setLongLived(true)
                .setCategories(setOf("com.sharelink.app.category.CAST"))
                .build()
            ShortcutManagerCompat.pushDynamicShortcut(this, shortcut)
        } catch (_: Exception) {}
    }

    override fun onStart() {
        super.onStart()
        ShareLinkService.addListener(stateListener)
    }

    override fun onStop() {
        ShareLinkService.removeListener(stateListener)
        super.onStop()
    }

    private fun maybeRequestNotificationPerm() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun checkForUpdate() {
        if (UPDATE_URL.isEmpty()) return
        Thread {
            try {
                val req = Request.Builder().url(UPDATE_URL).build()
                val body = httpClient.newCall(req).execute().body?.string() ?: return@Thread
                val json = JSONObject(body)
                val latest = json.getString("version")
                val current = packageManager.getPackageInfo(packageName, 0).versionName
                if (latest == current) return@Thread
                val changelog = json.optString("changelog", "")
                val apkUrl = json.optString("android_url", "")
                runOnUiThread {
                    AlertDialog.Builder(this)
                        .setTitle("Có bản cập nhật v$latest")
                        .setMessage(changelog)
                        .setPositiveButton("Tải về") { _, _ ->
                            if (apkUrl.isNotEmpty()) openInBrowser(apkUrl)
                        }
                        .setNegativeButton("Để sau", null)
                        .show()
                }
            } catch (_: Exception) {}
        }.start()
    }

    private fun openInBrowser(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: Exception) {
            toast("Không mở được trình duyệt")
        }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.type != "text/plain") return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        val url = extractUrl(text).takeIf { it.isNotEmpty() } ?: return
        etUrl.setText(url)
        val ip = etIp.text.toString().trim()
        if (ip.isEmpty()) {
            toast("Nhập địa chỉ IP máy tính trước")
            return
        }
        sendUrl(url)
    }

    private fun extractUrl(text: String): String {
        val decoded = htmlDecode(text)
        val match = Regex("""https?://\S+""").find(decoded) ?: return decoded.trim()
        return match.value.trimEnd(')', ']', '.', ',')
    }

    private fun htmlDecode(s: String): String {
        var cur = s
        repeat(3) {
            val next = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                android.text.Html.fromHtml(cur, android.text.Html.FROM_HTML_MODE_LEGACY).toString()
            } else {
                @Suppress("DEPRECATION") android.text.Html.fromHtml(cur).toString()
            }
            if (next == cur) return cur
            cur = next
        }
        return cur
    }

    private fun connect() {
        val input = etIp.text.toString().trim()
        if (input.isEmpty()) { toast("Nhập địa chỉ IP máy tính"); return }
        setStatus(Status.CONNECTING)
        val svc = Intent(this, ShareLinkService::class.java).apply {
            action = ShareLinkService.ACTION_CONNECT
            putExtra(ShareLinkService.EXTRA_IP, input)
        }
        ContextCompat.startForegroundService(this, svc)
    }

    private fun sendUrl(url: String) {
        val ip = etIp.text.toString().trim()
        if (ip.isEmpty()) { toast("Nhập địa chỉ IP máy tính trước"); return }
        val svc = Intent(this, ShareLinkService::class.java).apply {
            action = ShareLinkService.ACTION_SEND_URL
            putExtra(ShareLinkService.EXTRA_URL, url)
            putExtra(ShareLinkService.EXTRA_IP, ip)
        }
        ContextCompat.startForegroundService(this, svc)

        if (ShareLinkService.currentState == ShareLinkService.ConnState.CONNECTED) {
            toast("Đang phát video trên màn hình lớn!")
            etUrl.setText("")
            isVideoPlaying = true
            isPaused = false
            setControlsEnabled(true)
            btnPause.text = "⏸  Tạm dừng"
        } else {
            toast("Đã xếp hàng — đang kết nối lại...")
        }
    }

    private fun sendCommand(command: String) {
        if (ShareLinkService.currentState != ShareLinkService.ConnState.CONNECTED) {
            toast("Chưa kết nối với máy tính")
            return
        }
        val svc = Intent(this, ShareLinkService::class.java).apply {
            action = ShareLinkService.ACTION_SEND_COMMAND
            putExtra(ShareLinkService.EXTRA_COMMAND, command)
        }
        ContextCompat.startForegroundService(this, svc)
    }

    private fun resetControls() {
        isVideoPlaying = false
        isPaused = false
        setControlsEnabled(false)
        btnPause.text = "⏸  Tạm dừng"
    }

    private fun setControlsEnabled(enabled: Boolean) {
        val alpha = if (enabled) 1.0f else 0.45f
        btnFullscreen.isEnabled = enabled
        btnFullscreen.alpha = alpha
        btnPause.isEnabled = enabled
        btnPause.alpha = alpha
        btnStop.isEnabled = enabled
        btnStop.alpha = alpha
    }

    private enum class Status { CONNECTING, CONNECTED, DISCONNECTED, ERROR }

    private fun setStatus(s: Status, msg: String? = null) {
        val (text, color) = when (s) {
            Status.CONNECTING   -> "Đang kết nối..."  to "#FFC107"
            Status.CONNECTED    -> "Đã kết nối"       to "#4CAF50"
            Status.DISCONNECTED -> "Chưa kết nối"     to "#F44336"
            Status.ERROR        -> (msg ?: "Lỗi kết nối") to "#F44336"
        }
        tvStatus.text = text
        statusDot.backgroundTintList =
            android.content.res.ColorStateList.valueOf(android.graphics.Color.parseColor(color))
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
