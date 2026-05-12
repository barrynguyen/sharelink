package com.sharelink.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import java.io.File
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import okhttp3.*
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

    private var webSocket: WebSocket? = null
    private var isConnected = false
    private var isVideoPlaying = false
    private var isPaused = false
    private var autoReconnect = false
    private var reconnectThread: Thread? = null

    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private val wsListener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            isConnected = true
            runOnUiThread {
                setStatus(Status.CONNECTED)
                btnConnect.text = "Kết nối lại"
                val url = etUrl.text.toString().trim()
                if (url.isNotEmpty()) sendUrl(url)
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            isConnected = false
            runOnUiThread {
                setStatus(Status.DISCONNECTED)
                resetControls()
            }
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            isConnected = false
            runOnUiThread {
                setStatus(Status.DISCONNECTED)
                resetControls()
            }
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        if (!autoReconnect) return
        reconnectThread = Thread {
            Thread.sleep(3000)
            if (autoReconnect && !isConnected) {
                runOnUiThread { connect(silent = true) }
            }
        }.also { it.isDaemon = true; it.start() }
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
            if (!isConnected) { toast("Chưa kết nối với máy tính"); return@setOnClickListener }
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
    }

    private fun checkForUpdate() {
        if (UPDATE_URL.isEmpty()) return
        Thread {
            try {
                val req = Request.Builder().url(UPDATE_URL).build()
                val body = client.newCall(req).execute().body?.string() ?: return@Thread
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
                        .setPositiveButton("Cập nhật") { _, _ ->
                            if (apkUrl.isNotEmpty()) downloadAndInstall(apkUrl)
                        }
                        .setNegativeButton("Để sau", null)
                        .show()
                }
            } catch (_: Exception) {}
        }.start()
    }

    private fun downloadAndInstall(apkUrl: String) {
        toast("Đang tải bản cập nhật...")
        Thread {
            try {
                val bytes = client.newCall(
                    Request.Builder().url(apkUrl).build()
                ).execute().body?.bytes() ?: run {
                    runOnUiThread { toast("Tải thất bại") }
                    return@Thread
                }
                val file = File(getExternalFilesDir(null), "ShareLink-update.apk")
                file.writeBytes(bytes)
                val uri = FileProvider.getUriForFile(this, "$packageName.provider", file)
                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                runOnUiThread { startActivity(intent) }
            } catch (e: Exception) {
                runOnUiThread { toast("Lỗi: ${e.message}") }
            }
        }.start()
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
        when {
            isConnected -> sendUrl(url)
            etIp.text.isNotEmpty() -> connect()  // onOpen sẽ tự sendUrl
            else -> toast("Nhập địa chỉ IP máy tính trước")
        }
    }

    private fun extractUrl(text: String): String {
        val match = Regex("""https?://\S+""").find(text) ?: return text.trim()
        // Bỏ dấu ngoặc hoặc dấu câu cuối URL do app thêm vào
        return match.value.trimEnd(')', ']', '.', ',')
    }

    private fun connect(silent: Boolean = false) {
        val input = etIp.text.toString().trim()
        if (input.isEmpty()) {
            if (!silent) toast("Nhập địa chỉ IP máy tính")
            return
        }

        getSharedPreferences("sharelink", MODE_PRIVATE).edit()
            .putString("pc_ip", input).apply()

        val wsUrl = when {
            input.startsWith("ws://") -> input
            input.contains(":")       -> "ws://$input"
            else                      -> "ws://$input:8765"
        }

        autoReconnect = true
        webSocket?.close(1000, null)
        if (!silent) setStatus(Status.CONNECTING)

        val request = Request.Builder().url(wsUrl).build()
        webSocket = client.newWebSocket(request, wsListener)
    }

    override fun onResume() {
        super.onResume()
        if (autoReconnect && !isConnected && etIp.text.isNotEmpty()) {
            connect(silent = true)
        }
    }

    private fun sendUrl(url: String) {
        val payload = JSONObject().put("url", url).toString()
        val ok = webSocket?.send(payload) ?: false
        if (ok) {
            toast("Đang phát video trên màn hình lớn!")
            etUrl.setText("")
            isVideoPlaying = true
            isPaused = false
            setControlsEnabled(true)
            btnPause.text = "⏸  Tạm dừng"
        } else {
            toast("Gửi thất bại — thử kết nối lại")
            isConnected = false
            setStatus(Status.DISCONNECTED)
        }
    }

    private fun sendCommand(command: String) {
        val payload = JSONObject().put("command", command).toString()
        val ok = webSocket?.send(payload) ?: false
        if (!ok) toast("Lỗi gửi lệnh — thử kết nối lại")
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

    override fun onDestroy() {
        super.onDestroy()
        autoReconnect = false
        reconnectThread?.interrupt()
        webSocket?.close(1000, null)
        client.dispatcher.executorService.shutdown()
    }
}
