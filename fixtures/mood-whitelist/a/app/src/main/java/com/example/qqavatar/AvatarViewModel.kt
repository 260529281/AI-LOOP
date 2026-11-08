package com.example.qqavatar

import android.app.Application
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

enum class Emotion(val title: String, val file: String) {
    NEUTRAL("平常", "neutral.png"),
    HAPPY("开心", "happy.png"),
    THINKING("思考", "thinking.png"),
    SAD("难过", "sad.png"),
}

enum class Role { AI, USER, SYSTEM }

data class ChatLine(val id: Long, val role: Role, val text: String, val at: Long = System.currentTimeMillis())

data class Settings(val url: String, val token: String)

data class AvatarUiState(
    val settings: Settings,
    val connected: Boolean = false,
    val busy: Boolean = false,
    val emotion: Emotion = Emotion.NEUTRAL,
    val lines: List<ChatLine> = emptyList(),
    val images: Map<Emotion, Bitmap> = emptyMap(),
    val blinkImages: Map<Emotion, Bitmap> = emptyMap(),
)

class AvatarViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = app.getSharedPreferences("avatar", 0)
    private val imageDir = File(app.filesDir, "avatar").apply { mkdirs() }
    private val historyFile = File(app.filesDir, "history.json")
    private val nextId = AtomicLong(1)

    private val _state = MutableStateFlow(AvatarUiState(settings = readSettings()))
    val state: StateFlow<AvatarUiState> = _state.asStateFlow()

    // SSE 连接不能有读超时。
    private val sseClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private var connectJob: Job? = null
    private var activeCall: Call? = null
    private var emotionResetJob: Job? = null
    private var replyTimeoutJob: Job? = null
    private var lastEventId = 0L
    private var lastError: String? = null
    private var everConnected = false

    init {
        lastEventId = prefs.getLong("lastEventId", 0L)
        loadHistory()
        loadImages()
        restart()
    }

    private fun readSettings() = Settings(
        url = prefs.getString("url", DEFAULT_URL).orEmpty(),
        token = prefs.getString("token", "").orEmpty(),
    )

    fun saveSettings(url: String, token: String) {
        val cleanUrl = url.trim().let { if (it.startsWith("http")) it else "http://$it" }.trimEnd('/')
        val cleanToken = token.trim()
        if (cleanUrl == _state.value.settings.url && cleanToken == _state.value.settings.token) {
            // 深链会在 Activity 重建时重放，配置没变就别清空会话记录。
            restart()
            return
        }
        prefs.edit().putString("url", cleanUrl).putString("token", cleanToken).putLong("lastEventId", 0L).apply()
        lastEventId = 0
        historyFile.delete()
        _state.update { it.copy(settings = Settings(cleanUrl, cleanToken), lines = emptyList()) }
        restart()
    }

    fun restart() {
        connectJob?.cancel()
        activeCall?.cancel()
        activeCall = null
        _state.update { it.copy(connected = false) }
        connectJob = viewModelScope.launch(Dispatchers.IO) { connectLoop() }
    }

    private suspend fun connectLoop() {
        var backoff = 0L
        while (currentCoroutineContext().isActive) {
            val settings = _state.value.settings
            if (settings.url.isBlank() || settings.token.isBlank()) {
                delay(1000)
                continue
            }
            val builder = Request.Builder()
                .url(settings.url + "/api/avatar/stream")
                .header("x-avatar-token", settings.token)
                .header("Accept", "text/event-stream")
            if (lastEventId > 0) builder.header("Last-Event-ID", lastEventId.toString())
            val call = sseClient.newCall(builder.build())
            activeCall = call
            try {
                call.execute().use { resp ->
                    when {
                        resp.code == 401 -> {
                            fail("访问令牌不正确（看 qq-bridge/state/avatar-token）", noisy = true)
                            delay(8000)
                            return@use
                        }
                        !resp.isSuccessful -> {
                            fail("服务器返回 HTTP ${resp.code}", noisy = true)
                            delay(5000)
                            return@use
                        }
                    }
                    succeed()
                    backoff = 0
                    val source = resp.body?.source() ?: return@use
                    val data = StringBuilder()
                    var eventId = lastEventId
                    while (currentCoroutineContext().isActive) {
                        val line = source.readUtf8Line() ?: break
                        when {
                            line.startsWith("id:") -> line.substring(3).trim().toLongOrNull()?.let { eventId = it }
                            line.startsWith("data:") -> {
                                if (data.isNotEmpty()) data.append('\n')
                                data.append(line.substring(5).trim())
                            }
                            line.isBlank() && data.isNotEmpty() -> {
                                handleEvent(data.toString())
                                data.setLength(0)
                                if (eventId != lastEventId) {
                                    lastEventId = eventId
                                    prefs.edit().putLong("lastEventId", lastEventId).apply()
                                }
                            }
                        }
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // restart() 主动取消旧连接时不算故障。
                if (call.isCanceled()) throw CancellationException("reconnect")
                val detail = if (e is IOException) e.message ?: "网络错误" else (e.message ?: "连接失败")
                fail("连不上 ${settings.url}（$detail）", noisy = !everConnected)
            } finally {
                if (activeCall === call) activeCall = null
            }
            backoff = if (backoff == 0L) 2000 else minOf(backoff * 2, 15000)
            delay(backoff)
        }
    }

    private fun succeed() {
        everConnected = true
        lastError = null
        _state.update { it.copy(connected = true) }
    }

    private fun fail(message: String, noisy: Boolean = false) {
        if (noisy && message != lastError) appendSystem(message)
        lastError = message
        _state.update { it.copy(connected = false) }
    }

    private fun handleEvent(raw: String) {
        val json = runCatching { JSONObject(raw) }.getOrNull() ?: return
        when (json.optString("type")) {
            "reply" -> onReply(json.optString("text"))
            "incoming" -> onIncoming(json)
            "say-failed" -> {
                replyTimeoutJob?.cancel()
                _state.update { it.copy(busy = false) }
                appendSystem("这条消息没送出去：${json.optString("error")}")
                setEmotion(Emotion.SAD, 6000)
            }
        }
    }

    private fun onReply(textRaw: String) {
        val text = textRaw.trim()
        if (text.isEmpty()) return
        replyTimeoutJob?.cancel()
        append(ChatLine(nextId.getAndIncrement(), Role.AI, text))
        _state.update { it.copy(busy = false) }
        setEmotion(Emotion.HAPPY, 7000)
    }

    private fun onIncoming(json: JSONObject) {
        // 自己从 App 发出的消息已经本地显示过，服务端会原样回显，跳过避免重复。
        if (json.optString("source") == "avatar") return
        val text = json.optString("text").trim()
        if (text.isEmpty()) return
        val name = json.optString("senderName").trim()
        val display = if (json.optBoolean("isOwner") || name.isEmpty()) text else "$name：$text"
        append(ChatLine(nextId.getAndIncrement(), Role.USER, display))
    }

    fun send(textRaw: String) {
        val text = textRaw.trim()
        if (text.isEmpty()) return
        val settings = _state.value.settings
        if (settings.url.isBlank() || settings.token.isBlank()) {
            appendSystem("先在设置里填写服务器地址和令牌")
            return
        }
        append(ChatLine(nextId.getAndIncrement(), Role.USER, text))
        _state.update { it.copy(busy = true) }
        setEmotion(Emotion.THINKING, 0)
        replyTimeoutJob?.cancel()
        replyTimeoutJob = viewModelScope.launch {
            delay(120_000)
            if (_state.value.busy) {
                _state.update { it.copy(busy = false) }
                appendSystem("等了 2 分钟没有回复，可能还在忙")
                setEmotion(Emotion.NEUTRAL, 0)
            }
        }
        viewModelScope.launch(Dispatchers.IO) {
            val body = JSONObject().put("text", text).toString().toRequestBody("application/json".toMediaType())
            val request = Request.Builder()
                .url(settings.url + "/api/avatar/say")
                .header("x-avatar-token", settings.token)
                .post(body)
                .build()
            try {
                httpClient.newCall(request).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        val raw = resp.body?.string().orEmpty()
                        val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                        throw IOException(msg.ifBlank { "HTTP ${resp.code}" })
                    }
                }
            } catch (e: Exception) {
                _state.update { it.copy(busy = false) }
                appendSystem("发送失败：${e.message ?: "网络错误"}")
                setEmotion(Emotion.SAD, 6000)
            }
        }
    }

    fun importImage(emotion: Emotion, uri: Uri) {
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val bitmap = getApplication<Application>().contentResolver.openInputStream(uri)?.use { decodeScaled(it) }
                if (bitmap == null) {
                    appendSystem("读不出这张图片")
                    return@launch
                }
                File(imageDir, emotion.file).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                reloadImages()
                _state.update { it.copy(emotion = emotion) }
            } catch (e: Exception) {
                appendSystem("导入失败：${e.message ?: "未知错误"}")
            }
        }
    }

    fun clearImage(emotion: Emotion) {
        File(imageDir, emotion.file).delete()
        reloadImages()
    }

    private fun decodeScaled(input: InputStream): Bitmap? {
        val bytes = input.readBytes()
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (bounds.outWidth / sample > 1600 || bounds.outHeight / sample > 1600) sample *= 2
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }

    private fun loadImages() {
        viewModelScope.launch(Dispatchers.IO) { reloadImages() }
    }

    private fun reloadImages() {
        val map = mutableMapOf<Emotion, Bitmap>()
        val blinks = mutableMapOf<Emotion, Bitmap>()
        val assets = getApplication<Application>().assets
        for (emotion in Emotion.entries) {
            val file = File(imageDir, emotion.file)
            val loaded = if (file.length() > 0) BitmapFactory.decodeFile(file.absolutePath) else null
            val bitmap = loaded ?: runCatching {
                assets.open("avatar/${emotion.file}").use { BitmapFactory.decodeStream(it) }
            }.getOrNull()
            if (bitmap != null) map[emotion] = bitmap

            val blinkName = emotion.file.replace(".png", "_blink.png")
            val blinkFile = File(imageDir, blinkName)
            val blinkLoaded = if (blinkFile.length() > 0) BitmapFactory.decodeFile(blinkFile.absolutePath) else null
            // 用户自己导入的立绘没有配套眨眼帧，就别拿内置角色的眨眼帧去叠，否则会错位。
            val blink = blinkLoaded ?: if (loaded == null) {
                runCatching {
                    assets.open("avatar/$blinkName").use { BitmapFactory.decodeStream(it) }
                }.getOrNull()
            } else null
            if (blink != null) blinks[emotion] = blink
        }
        _state.update { it.copy(images = map, blinkImages = blinks) }
    }

    private fun append(line: ChatLine) {
        var snapshot = emptyList<ChatLine>()
        _state.update { state ->
            snapshot = (state.lines + line).takeLast(300)
            state.copy(lines = snapshot)
        }
        if (line.role != Role.SYSTEM) persistHistory(snapshot)
    }

    private fun loadHistory() {
        val raw = runCatching { historyFile.readText() }.getOrNull() ?: return
        val array = runCatching { JSONArray(raw) }.getOrNull() ?: return
        val loaded = buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val role = runCatching { Role.valueOf(item.optString("role")) }.getOrNull() ?: continue
                if (role == Role.SYSTEM) continue
                add(ChatLine(item.optLong("id"), role, item.optString("text"), item.optLong("at")))
            }
        }
        if (loaded.isEmpty()) return
        nextId.set(loaded.maxOf { it.id } + 1)
        _state.update { it.copy(lines = loaded.takeLast(200)) }
    }

    private fun persistHistory(lines: List<ChatLine>) {
        viewModelScope.launch(Dispatchers.IO) {
            val array = JSONArray()
            for (line in lines.takeLast(200)) {
                array.put(
                    JSONObject()
                        .put("id", line.id)
                        .put("role", line.role.name)
                        .put("text", line.text)
                        .put("at", line.at)
                )
            }
            runCatching { historyFile.writeText(array.toString()) }
        }
    }

    private fun appendSystem(text: String) {
        append(ChatLine(nextId.getAndIncrement(), Role.SYSTEM, text))
    }

    private fun setEmotion(emotion: Emotion, revertAfterMs: Long) {
        emotionResetJob?.cancel()
        _state.update { it.copy(emotion = emotion) }
        if (revertAfterMs > 0) {
            emotionResetJob = viewModelScope.launch {
                delay(revertAfterMs)
                _state.update { it.copy(emotion = Emotion.NEUTRAL) }
            }
        }
    }

    companion object {
        const val DEFAULT_URL = "http://172.20.10.8:3101"
    }
}
