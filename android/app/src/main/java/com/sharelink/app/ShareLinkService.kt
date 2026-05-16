package com.sharelink.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class ShareLinkService : Service() {

    enum class ConnState { DISCONNECTED, CONNECTING, CONNECTED }

    fun interface StateListener {
        fun onState(state: ConnState, ip: String?)
    }

    companion object {
        const val ACTION_CONNECT = "com.sharelink.action.CONNECT"
        const val ACTION_SEND_URL = "com.sharelink.action.SEND_URL"
        const val ACTION_SEND_COMMAND = "com.sharelink.action.SEND_COMMAND"
        const val ACTION_SEND_BROWSER = "com.sharelink.action.SEND_BROWSER"
        const val ACTION_DISCONNECT = "com.sharelink.action.DISCONNECT"

        const val EXTRA_IP = "ip"
        const val EXTRA_URL = "url"
        const val EXTRA_COMMAND = "command"
        const val EXTRA_BROWSER = "browser"

        private const val CHANNEL_ID = "sharelink_connection"
        private const val NOTIF_ID = 42
        private const val PREFS = "sharelink"
        private const val PREF_IP = "pc_ip"
        private const val PREF_BROWSER = "browser"

        private val listeners = CopyOnWriteArrayList<StateListener>()

        @Volatile var currentState: ConnState = ConnState.DISCONNECTED
            private set
        @Volatile var currentIp: String? = null
            private set

        fun addListener(l: StateListener) {
            listeners.add(l)
            mainHandler.post { l.onState(currentState, currentIp) }
        }
        fun removeListener(l: StateListener) { listeners.remove(l) }

        private val mainHandler = Handler(Looper.getMainLooper())

        internal fun publishState(state: ConnState, ip: String?) {
            currentState = state
            currentIp = ip
            listeners.forEach { l -> mainHandler.post { l.onState(state, ip) } }
        }
    }

    private val client by lazy {
        OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }

    private var webSocket: WebSocket? = null
    private val isConnected = AtomicBoolean(false)
    @Volatile private var targetIp: String? = null
    @Volatile private var shuttingDown = false
    private val pendingUrls = ConcurrentLinkedDeque<String>()

    private val reconnectExecutor = Executors.newSingleThreadScheduledExecutor()
    private var reconnectTask: ScheduledFuture<*>? = null
    @Volatile private var backoffSec = 3L
    private val maxBackoffSec = 30L

    private var connectivityManager: ConnectivityManager? = null
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            if (targetIp != null && !isConnected.get() && !shuttingDown) {
                cancelReconnect()
                mainHandler.post { connectNow() }
            }
        }
    }

    private val wsListener = object : WebSocketListener() {
        override fun onOpen(ws: WebSocket, response: Response) {
            isConnected.set(true)
            backoffSec = 3L
            publishState(ConnState.CONNECTED, targetIp)
            updateNotification()
            // Sync browser preference tới desktop ngay khi connect
            val browser = getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(PREF_BROWSER, "edge") ?: "edge"
            ws.send(JSONObject()
                .put("command", "set_browser")
                .put("browser", browser).toString())
            // flush queued URLs
            while (true) {
                val u = pendingUrls.pollFirst() ?: break
                ws.send(JSONObject().put("url", u).toString())
            }
        }

        override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
            isConnected.set(false)
            webSocket = null
            publishState(ConnState.DISCONNECTED, targetIp)
            updateNotification()
            if (!shuttingDown) scheduleReconnect()
        }

        override fun onClosed(ws: WebSocket, code: Int, reason: String) {
            isConnected.set(false)
            webSocket = null
            publishState(ConnState.DISCONNECTED, targetIp)
            updateNotification()
            if (!shuttingDown) scheduleReconnect()
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startInForeground(buildNotification(ConnState.DISCONNECTED, null))
        registerNetworkCallback()
        val saved = getSharedPreferences(PREFS, MODE_PRIVATE).getString(PREF_IP, null)
        if (!saved.isNullOrBlank()) {
            targetIp = saved
            publishState(ConnState.CONNECTING, saved)
            connectNow()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CONNECT -> {
                val newIp = intent.getStringExtra(EXTRA_IP)?.trim()
                if (!newIp.isNullOrBlank()) {
                    if (newIp != targetIp) {
                        targetIp = newIp
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString(PREF_IP, newIp).apply()
                        forceReconnect()
                    } else if (!isConnected.get()) {
                        forceReconnect()
                    }
                }
            }
            ACTION_SEND_URL -> {
                val u = intent.getStringExtra(EXTRA_URL)?.trim().orEmpty()
                if (u.isNotEmpty()) {
                    val ipExtra = intent.getStringExtra(EXTRA_IP)?.trim()
                    if (!ipExtra.isNullOrBlank() && ipExtra != targetIp) {
                        targetIp = ipExtra
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                            .putString(PREF_IP, ipExtra).apply()
                        forceReconnect()
                    }
                    val ws = webSocket
                    if (isConnected.get() && ws != null) {
                        val browser = getSharedPreferences(PREFS, MODE_PRIVATE)
                            .getString(PREF_BROWSER, "edge") ?: "edge"
                        ws.send(JSONObject()
                            .put("command", "set_browser")
                            .put("browser", browser).toString())
                        ws.send(JSONObject().put("url", u).toString())
                    } else {
                        pendingUrls.addLast(u)
                        if (targetIp != null && webSocket == null) connectNow()
                    }
                }
            }
            ACTION_SEND_COMMAND -> {
                val cmd = intent.getStringExtra(EXTRA_COMMAND)?.trim().orEmpty()
                if (cmd.isNotEmpty()) {
                    webSocket?.send(JSONObject().put("command", cmd).toString())
                }
            }
            ACTION_SEND_BROWSER -> {
                val browser = intent.getStringExtra(EXTRA_BROWSER)?.trim().orEmpty()
                if (browser == "edge" || browser == "firefox") {
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                        .putString(PREF_BROWSER, browser).apply()
                    webSocket?.send(JSONObject()
                        .put("command", "set_browser")
                        .put("browser", browser).toString())
                }
            }
            ACTION_DISCONNECT -> {
                shutdown()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        shuttingDown = true
        cancelReconnect()
        reconnectExecutor.shutdownNow()
        unregisterNetworkCallback()
        webSocket?.close(1000, null)
        webSocket = null
        publishState(ConnState.DISCONNECTED, null)
        super.onDestroy()
    }

    // -------- Connection lifecycle --------

    private fun connectNow() {
        val ip = targetIp ?: return
        if (shuttingDown) return
        publishState(ConnState.CONNECTING, ip)
        updateNotification()
        val wsUrl = when {
            ip.startsWith("ws://") -> ip
            ip.contains(":")       -> "ws://$ip"
            else                   -> "ws://$ip:8765"
        }
        webSocket?.close(1000, null)
        webSocket = client.newWebSocket(Request.Builder().url(wsUrl).build(), wsListener)
    }

    private fun forceReconnect() {
        cancelReconnect()
        isConnected.set(false)
        connectNow()
    }

    private fun scheduleReconnect() {
        if (shuttingDown) return
        if (targetIp == null) return
        cancelReconnect()
        val delay = backoffSec
        reconnectTask = reconnectExecutor.schedule({
            if (!shuttingDown && !isConnected.get()) {
                mainHandler.post { connectNow() }
            }
        }, delay, TimeUnit.SECONDS)
        backoffSec = (backoffSec * 2).coerceAtMost(maxBackoffSec)
    }

    private fun cancelReconnect() {
        reconnectTask?.cancel(false)
        reconnectTask = null
    }

    private fun shutdown() {
        shuttingDown = true
        cancelReconnect()
        unregisterNetworkCallback()
        webSocket?.close(1000, null)
        webSocket = null
        isConnected.set(false)
        publishState(ConnState.DISCONNECTED, null)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // -------- Network monitoring --------

    private fun registerNetworkCallback() {
        connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val req = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        try {
            connectivityManager?.registerNetworkCallback(req, networkCallback)
        } catch (_: Exception) {}
    }

    private fun unregisterNetworkCallback() {
        try { connectivityManager?.unregisterNetworkCallback(networkCallback) } catch (_: Exception) {}
    }

    // -------- Notification --------

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "ShareLink kết nối",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Giữ kết nối với máy tính để cast nhanh"
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }
    }

    private fun startInForeground(notif: Notification) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }

    private fun updateNotification() {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        nm.notify(NOTIF_ID, buildNotification(currentState, currentIp))
    }

    private fun buildNotification(state: ConnState, ip: String?): Notification {
        val text = when (state) {
            ConnState.CONNECTED    -> "Đã kết nối${ip?.let { " — $it" } ?: ""}"
            ConnState.CONNECTING   -> "Đang kết nối${ip?.let { " tới $it" } ?: ""}..."
            ConnState.DISCONNECTED -> "Mất kết nối${ip?.let { " — sẽ thử lại" } ?: ""}"
        }

        val openAppPi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val clipboardPi = PendingIntent.getActivity(
            this, 1,
            Intent(this, ClipboardCastActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val disconnectPi = PendingIntent.getService(
            this, 2,
            Intent(this, ShareLinkService::class.java).setAction(ACTION_DISCONNECT),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("ShareLink")
            .setContentText(text)
            .setContentIntent(openAppPi)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .addAction(0, "Cast clipboard", clipboardPi)
            .addAction(0, "Ngắt", disconnectPi)
            .build()
    }
}
