package com.example.qqavatar

import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

private val Bg = Color(0xFF081722)
private val Panel = Color(0xFF0D2231)
private val Accent = Color(0xFF7FD1FF)
private val TextMain = Color(0xFFE6F3FF)
private val TextDim = Color(0xFF7C93A6)

class MainActivity : ComponentActivity() {
    private val setup = mutableStateOf<Pair<String?, String>?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        readSetup(intent)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Accent,
                    background = Bg,
                    surface = Panel,
                    onSurface = TextMain,
                )
            ) {
                AvatarApp(
                    initialUrl = setup.value?.first,
                    initialToken = setup.value?.second,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        readSetup(intent)
    }

    private fun readSetup(intent: Intent?) {
        val data = intent?.data?.takeIf { it.scheme == "qqavatar" && it.host == "setup" } ?: return
        val token = data.getQueryParameter("token") ?: return
        setup.value = data.getQueryParameter("url") to token
    }
}

@Composable
fun AvatarApp(
    initialUrl: String? = null,
    initialToken: String? = null,
    vm: AvatarViewModel = viewModel(),
) {
    val state by vm.state.collectAsState()
    var showSettings by remember { mutableStateOf(state.settings.token.isBlank()) }
    // 深链 qqavatar://setup?url=...&token=... 免去在手机上敲长令牌。
    LaunchedEffect(initialToken) {
        if (!initialToken.isNullOrBlank()) {
            vm.saveSettings(initialUrl ?: state.settings.url, initialToken)
            showSettings = false
        }
    }
    if (showSettings) {
        SettingsScreen(state, vm) { showSettings = false }
    } else {
        MainScreen(state, onSend = vm::send) { showSettings = true }
    }
}

@Composable
fun MainScreen(state: AvatarUiState, onSend: (String) -> Unit, onSettings: () -> Unit) {
    // 最新一句 AI 台词已经浮在立绘上方，聊天记录里就不再重复。
    val stageLine = state.lines.lastOrNull { it.role == Role.AI }
    val transcript = if (stageLine == null) state.lines else state.lines.filterNot { it.id == stageLine.id }
    Scaffold(containerColor = Bg) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).imePadding()) {
            Header(state, onSettings)
            AvatarStage(state, Modifier.weight(0.52f).fillMaxWidth())
            Transcript(transcript, Modifier.weight(0.48f).fillMaxWidth())
            Composer(onSend)
        }
    }
}

@Composable
private fun Header(state: AvatarUiState, onSettings: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text("大肥鱼", fontSize = 22.sp, fontWeight = FontWeight.Bold, color = TextMain)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(8.dp).clip(CircleShape)
                        .background(if (state.connected) Color(0xFF5BE3A5) else Color(0xFFE07070))
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    if (state.connected) "已连接" else "未连接",
                    fontSize = 12.sp,
                    color = if (state.connected) Color(0xFF5BE3A5) else TextDim,
                )
            }
        }
        TextButton(onClick = onSettings) { Text("设置", color = Accent) }
    }
}

@Composable
private fun AvatarStage(state: AvatarUiState, modifier: Modifier) {
    BoxWithConstraints(
        modifier.background(
            Brush.verticalGradient(listOf(Color(0xFF0C2A3E), Bg))
        )
    ) {
        // 台词多长气泡就多高，但最多占舞台的四成，剩下的都给立绘。
        val bubbleMax = maxHeight * 0.42f
        Column(
            Modifier.fillMaxSize().padding(horizontal = 18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val lastAi = state.lines.lastOrNull { it.role == Role.AI }
            Box(
                Modifier.fillMaxWidth().heightIn(max = bubbleMax),
                contentAlignment = Alignment.BottomCenter,
            ) {
                if (lastAi != null) {
                    Surface(
                        color = Color(0xF01A3346),
                        shape = RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 6.dp),
                    ) {
                        TypewriterText(
                            text = lastAi.text,
                            id = lastAi.id,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 11.dp),
                        )
                    }
                } else {
                    Text("和 TA 说句话吧", fontSize = 13.sp, color = TextDim, modifier = Modifier.padding(bottom = 6.dp))
                }
            }
            if (state.busy) ThinkingDots()
            Spacer(Modifier.height(6.dp))
            AnimatedAvatar(
                images = state.images,
                blinks = state.blinkImages,
                emotion = state.emotion,
                busy = state.busy,
                modifier = Modifier.weight(2.2f).fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun TypewriterText(text: String, id: Long, modifier: Modifier = Modifier) {
    var shown by remember(id) { mutableIntStateOf(0) }
    val scroll = rememberScrollState()
    LaunchedEffect(id) {
        shown = 0
        while (shown < text.length) {
            delay(16)
            shown += 1
        }
    }
    // 台词长到被高度上限截住时，让正在打出来的字一直留在视野里。
    LaunchedEffect(scroll) {
        snapshotFlow { scroll.maxValue }.collect { scroll.scrollTo(it) }
    }
    Text(
        text.take(shown),
        modifier = modifier.verticalScroll(scroll).widthIn(max = 320.dp),
        color = TextMain,
        fontSize = 15.sp,
        lineHeight = 21.sp,
    )
}

@Composable
private fun ThinkingDots() {
    val transition = rememberInfiniteTransition(label = "thinking")
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(vertical = 4.dp)) {
        repeat(3) { index ->
            val alpha by transition.animateFloat(
                initialValue = 0.2f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(650, delayMillis = index * 160, easing = FastOutSlowInEasing),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "dot$index",
            )
            Box(Modifier.size(7.dp).clip(CircleShape).background(Accent.copy(alpha = alpha)))
        }
    }
}

@Composable
private fun AnimatedAvatar(
    images: Map<Emotion, Bitmap>,
    blinks: Map<Emotion, Bitmap>,
    emotion: Emotion,
    busy: Boolean,
    modifier: Modifier,
) {
    // 眨眼是离散事件，单独用 Animatable 驱动；呼吸/摇摆/上浮气泡走无限循环。
    val blink = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(2400L + Random.nextLong(3600L))
            blink.animateTo(1f, tween(70))
            delay(80)
            blink.animateTo(0f, tween(140))
        }
    }
    // 每隔几秒来一次轻微的「换重心」，比纯正弦摆动更像真人待机。
    val nod = remember { Animatable(0f) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(7000L + Random.nextLong(5000L))
            nod.animateTo(1f, tween(260, easing = FastOutSlowInEasing))
            nod.animateTo(-0.4f, tween(220, easing = FastOutSlowInEasing))
            nod.animateTo(0f, tween(520, easing = FastOutSlowInEasing))
        }
    }

    val idle = rememberInfiniteTransition(label = "idle")
    val breath by idle.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(3400, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "breath",
    )
    val sway by idle.animateFloat(
        initialValue = -1f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(5600, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "sway",
    )
    val bob by idle.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(2800, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "bob",
    )
    val glow by idle.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(2600, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "glow",
    )
    val rise by idle.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(9600, easing = LinearEasing)),
        label = "rise",
    )

    // 心情决定动作幅度和站姿：开心蹦跶，思考偏头，难过低头塌肩。
    val amp = when (emotion) {
        Emotion.NEUTRAL -> 1f
        Emotion.HAPPY -> 1.6f
        Emotion.THINKING -> 0.55f
        Emotion.SAD -> 0.4f
    }
    val tiltBase = when (emotion) {
        Emotion.NEUTRAL -> 0f
        Emotion.HAPPY -> 1.1f
        Emotion.THINKING -> -2.4f
        Emotion.SAD -> 2f
    }
    val droopPx = with(LocalDensity.current) {
        (when (emotion) {
            Emotion.SAD -> 5f
            else -> 0f
        }).dp.toPx()
    }
    val bobPx = with(LocalDensity.current) { 3.2.dp.toPx() }
    val shiftPx = with(LocalDensity.current) { 1.6.dp.toPx() }
    val tint = when (emotion) {
        Emotion.NEUTRAL -> Accent
        Emotion.HAPPY -> Color(0xFFFFB3C7)
        Emotion.THINKING -> Color(0xFFB6C8FF)
        Emotion.SAD -> Color(0xFF9FB3C8)
    }

    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            drawRect(
                brush = Brush.radialGradient(
                    colors = listOf(tint.copy(alpha = 0.20f * glow), Color.Transparent),
                    center = Offset(size.width / 2f, size.height * 0.52f),
                    radius = size.minDimension * 0.62f,
                )
            )
            val shadowW = size.width * 0.40f * (1f + breath * 0.02f)
            val shadowH = size.height * 0.045f
            drawOval(
                color = Color.Black.copy(alpha = 0.30f),
                topLeft = Offset(size.width / 2f - shadowW / 2f, size.height * 0.96f - shadowH),
                size = Size(shadowW, shadowH),
            )
            repeat(7) { i ->
                val t = (rise + i / 7f) % 1f
                val baseX = size.width * (0.12f + 0.76f * ((i * 0.37f) % 1f))
                val x = baseX + sin(rise * 2f * PI.toFloat() + i * 1.7f) * size.width * 0.02f
                val y = size.height * (0.92f - t * 0.72f)
                val r = size.minDimension * (0.008f + 0.006f * ((i * 0.53f) % 1f))
                val a = sin(t * PI.toFloat()) * 0.32f
                drawCircle(tint.copy(alpha = a), radius = r, center = Offset(x, y))
            }
        }

        Box(
            Modifier.fillMaxSize().graphicsLayer {
                transformOrigin = TransformOrigin(0.5f, 0.94f)
                val s = 1f + breath * 0.012f * amp
                scaleX = s
                scaleY = s
                rotationZ = tiltBase + sway * amp + nod.value * 0.9f * amp + if (busy) -1.6f else 0f
                translationX = sway * shiftPx * amp
                translationY = -bob * bobPx * amp + droopPx
            },
            contentAlignment = Alignment.Center,
        ) {
            Crossfade(targetState = emotion, animationSpec = tween(400), label = "mood") { mood ->
                val open = images[mood]
                val wink = blinks[mood]
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (open == null) {
                        FishPlaceholder(mood, Modifier.fillMaxSize().padding(8.dp))
                    } else {
                        val art = Modifier.fillMaxSize().padding(horizontal = 4.dp, vertical = 2.dp)
                        Image(
                            open.asImageBitmap(),
                            contentDescription = "虚拟形象",
                            contentScale = ContentScale.Fit,
                            modifier = art,
                        )
                        if (wink != null) {
                            Image(
                                wink.asImageBitmap(),
                                contentDescription = null,
                                contentScale = ContentScale.Fit,
                                modifier = art.graphicsLayer { alpha = blink.value },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FishPlaceholder(emotion: Emotion, modifier: Modifier) {
    val body = when (emotion) {
        Emotion.NEUTRAL -> Color(0xFF4FA8E0)
        Emotion.HAPPY -> Color(0xFF5CC9A7)
        Emotion.THINKING -> Color(0xFF6E8FE0)
        Emotion.SAD -> Color(0xFF7E8BA3)
    }
    Canvas(modifier) {
        val w = size.width
        val h = size.height
        val cx = w * 0.46f
        val cy = h * 0.5f
        val bw = w * 0.52f
        val bh = h * 0.42f

        val tail = Path().apply {
            moveTo(cx + bw * 0.44f, cy)
            lineTo(cx + bw * 0.86f, cy - bh * 0.42f)
            lineTo(cx + bw * 0.86f, cy + bh * 0.42f)
            close()
        }
        drawPath(tail, body)
        drawOval(body, topLeft = Offset(cx - bw / 2, cy - bh / 2), size = Size(bw, bh))
        drawOval(
            Color.White.copy(alpha = 0.16f),
            topLeft = Offset(cx - bw * 0.30f, cy + bh * 0.02f),
            size = Size(bw * 0.60f, bh * 0.34f),
        )
        val fin = Path().apply {
            moveTo(cx - bw * 0.14f, cy - bh * 0.34f)
            quadraticTo(cx, cy - bh * 0.74f, cx + bw * 0.24f, cy - bh * 0.30f)
            close()
        }
        drawPath(fin, body.copy(alpha = 0.92f))

        val eyeX = cx - bw * 0.20f
        val eyeY = cy - bh * 0.16f
        drawCircle(Color.White, radius = bw * 0.09f, center = Offset(eyeX, eyeY))
        drawCircle(Color(0xFF0B2233), radius = bw * 0.045f, center = Offset(eyeX - bw * 0.012f, eyeY + bh * 0.01f))
        drawCircle(
            Color.White.copy(alpha = 0.9f),
            radius = bw * 0.015f,
            center = Offset(eyeX - bw * 0.03f, eyeY - bh * 0.02f),
        )

        val mouthY = cy + bh * 0.20f
        drawArc(
            color = Color(0xFF0B2233).copy(alpha = 0.85f),
            startAngle = if (emotion == Emotion.SAD) 200f else 10f,
            sweepAngle = 160f,
            useCenter = false,
            topLeft = Offset(cx - bw * 0.13f, mouthY - bh * 0.09f),
            size = Size(bw * 0.22f, bh * 0.18f),
            style = Stroke(width = bw * 0.018f),
        )
        if (emotion == Emotion.HAPPY) {
            drawCircle(Color(0x55FF7C9C), radius = bw * 0.06f, center = Offset(cx - bw * 0.31f, cy + bh * 0.04f))
        }
    }
}

@Composable
private fun Transcript(lines: List<ChatLine>, modifier: Modifier) {
    val listState = rememberLazyListState()
    LaunchedEffect(lines.size) {
        if (lines.isNotEmpty()) listState.animateScrollToItem(lines.lastIndex)
    }
    LazyColumn(
        state = listState,
        modifier = modifier,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(lines, key = { it.id }) { line ->
            when (line.role) {
                Role.SYSTEM -> Text(
                    line.text,
                    color = TextDim,
                    fontSize = 12.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp),
                )
                Role.AI -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Start) {
                    Bubble(line.text, Color(0xFF173B52), TextMain, RoundedCornerShape(4.dp, 18.dp, 18.dp, 18.dp))
                }
                Role.USER -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    Bubble(line.text, Color(0xFF2A6B8F), Color.White, RoundedCornerShape(18.dp, 4.dp, 18.dp, 18.dp))
                }
            }
        }
    }
}

@Composable
private fun Bubble(text: String, background: Color, foreground: Color, shape: RoundedCornerShape) {
    Surface(color = background, shape = shape, modifier = Modifier.widthIn(max = 300.dp)) {
        Text(
            text,
            color = foreground,
            fontSize = 15.sp,
            lineHeight = 21.sp,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 9.dp),
        )
    }
}

@Composable
private fun Composer(onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val submit = {
        if (text.isNotBlank()) {
            onSend(text)
            text = ""
        }
    }
    Surface(color = Panel) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                placeholder = { Text("和大肥鱼说点什么…", fontSize = 14.sp, color = TextDim) },
                modifier = Modifier.weight(1f),
                maxLines = 3,
                shape = RoundedCornerShape(22.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { submit() }),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent,
                    unfocusedBorderColor = Color(0xFF274252),
                    focusedTextColor = TextMain,
                    unfocusedTextColor = TextMain,
                ),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = submit,
                enabled = text.isNotBlank(),
                shape = RoundedCornerShape(22.dp),
                contentPadding = PaddingValues(horizontal = 18.dp, vertical = 12.dp),
            ) {
                Text("发送")
            }
        }
    }
}

@Composable
private fun SettingsScreen(state: AvatarUiState, vm: AvatarViewModel, onBack: () -> Unit) {
    var url by remember { mutableStateOf(state.settings.url) }
    var token by remember { mutableStateOf(state.settings.token) }
    val pendingSlot = remember { mutableStateOf<Emotion?>(null) }
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        val slot = pendingSlot.value
        if (uri != null && slot != null) vm.importImage(slot, uri)
        pendingSlot.value = null
    }

    Column(
        Modifier.fillMaxSize()
            .background(Bg)
            .verticalScroll(rememberScrollState())
            .statusBarsPadding()
            .padding(horizontal = 20.dp, vertical = 8.dp)
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("返回", color = Accent) }
            Spacer(Modifier.weight(1f))
            Text("设置", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = TextMain)
            Spacer(Modifier.weight(1f))
            Spacer(Modifier.width(56.dp))
        }

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("服务器地址") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            colors = OutlinedTextFieldDefaults.colors(focusedTextColor = TextMain, unfocusedTextColor = TextMain),
        )
        Text(
            "填电脑上 qq-bridge 启动日志里的「虚拟形象 App 地址」。同一 Wi-Fi 下用局域网地址（如 192.168.x.x:3101）；不在同一网络时填电脑的 Tailscale 地址（100.x.x.x:3101），两端登录同一个 Tailscale 账号即可。",
            fontSize = 12.sp,
            color = TextDim,
            modifier = Modifier.padding(top = 4.dp),
        )

        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text("访问令牌") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
            colors = OutlinedTextFieldDefaults.colors(focusedTextColor = TextMain, unfocusedTextColor = TextMain),
        )
        Text(
            "令牌在 qq-bridge/state/avatar-token 文件里。",
            fontSize = 12.sp,
            color = TextDim,
            modifier = Modifier.padding(top = 4.dp),
        )

        Button(
            onClick = {
                vm.saveSettings(url, token)
                onBack()
            },
            modifier = Modifier.fillMaxWidth().padding(top = 16.dp),
        ) {
            Text("保存并重连")
        }

        HorizontalDivider(Modifier.padding(vertical = 22.dp), color = Color(0xFF1B3446))

        Text("心情白名单", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = TextMain)
        Text(
            "只显示勾选的心情，勾选即时生效。收到没勾选的心情时，立绘回退到「平常」；默认四套全开。",
            fontSize = 12.sp,
            color = TextDim,
            modifier = Modifier.padding(top = 4.dp),
        )
        for (emotion in Emotion.entries) {
            Row(
                Modifier.fillMaxWidth().padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(emotion.title, color = TextMain, fontSize = 15.sp, modifier = Modifier.weight(1f))
                Checkbox(
                    checked = emotion in state.allowedEmotions,
                    onCheckedChange = { vm.toggleEmotionAllowed(emotion) },
                )
            }
        }

        HorizontalDivider(Modifier.padding(vertical = 22.dp), color = Color(0xFF1B3446))

        Text("表情立绘", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = TextMain)
        Text(
            "内置「大肥鱼」四套动态立绘（含眨眼帧），在这里导入透明背景 PNG 可逐张替换；没导入的表情会用「平常」。",
            fontSize = 12.sp,
            color = TextDim,
            modifier = Modifier.padding(top = 4.dp),
        )

        for (emotion in Emotion.entries) {
            Row(
                Modifier.fillMaxWidth().padding(top = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    Modifier.size(64.dp).clip(RoundedCornerShape(12.dp)).background(Color(0xFF10293A)),
                    contentAlignment = Alignment.Center,
                ) {
                    val bitmap = state.images[emotion]
                    if (bitmap != null) {
                        Image(bitmap.asImageBitmap(), contentDescription = emotion.title, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize())
                    } else {
                        FishPlaceholder(emotion, Modifier.fillMaxSize())
                    }
                }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f)) {
                    Text(emotion.title, color = TextMain, fontSize = 15.sp)
                    Row {
                        TextButton(onClick = {
                            pendingSlot.value = emotion
                            picker.launch("image/*")
                        }) { Text("选择图片", color = Accent) }
                        if (state.images[emotion] != null) {
                            TextButton(onClick = { vm.clearImage(emotion) }) { Text("清除", color = TextDim) }
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(36.dp))
    }
}
