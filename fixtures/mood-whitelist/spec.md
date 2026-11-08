# 心情白名单 · 拆解（planner 产物）

## 需求复述
设置页新增「心情白名单」：四种心情默认全开；用户逐项勾选允许显示的心情；hub 推来未勾选的心情时，立绘回退到「平常」；勾选状态持久化，重启后保留。只改手机端。

## 子任务 1：状态与持久化（AvatarViewModel.kt）
- 输入：`Emotion` 枚举（NEUTRAL / HAPPY / THINKING / SAD）、现有 `SharedPreferences("avatar")`。
- 改动：`AvatarUiState` 增加 `allowedEmotions: Set<Emotion>`，默认 `Emotion.entries.toSet()`（默认全开）；启动时从 prefs 的 `allowedEmotions` 读回；新增 `toggleEmotionAllowed(emotion)` 写回 prefs。
- 产物：`AvatarViewModel.kt` 的补丁片段。
- 验收：默认全开；切换后 prefs 里有值；重启后状态一致。

## 子任务 2：按白名单收敛表情（AvatarViewModel.kt）
- 输入：子任务 1 的 `allowedEmotions`。
- 改动：`setEmotion()` 里把目标心情收敛到白名单内，不在白名单时用 `Emotion.NEUTRAL` 作为回退值；切换开关时如果当前心情被移出白名单，立即回退到「平常」。
- 产物：`AvatarViewModel.kt` 的补丁片段。
- 验收：未勾选「开心」时收到 HAPPY 事件，立绘显示「平常」；勾回来以后 HAPPY 恢复。

## 子任务 3：设置页勾选 UI（MainActivity.kt）
- 输入：子任务 1 的 `toggleEmotionAllowed` 与 `allowedEmotions`。
- 改动：设置页在「保存并重连」和「表情立绘」之间新增「心情白名单」小节，四行勾选项，一行一种心情，勾选即时生效，不需要点保存。
- 产物：`MainActivity.kt` 的补丁片段。
- 验收：四项默认都勾上；点一下即时保存并影响立绘。

## 合并后的实现验收（进审查环用）
- C1 改动只落在 `app/src/main/java/` 下
- C2 补丁可干净应用
- C3 `AvatarViewModel.kt` 同时出现 `allowedEmotions` 与 `toggleEmotionAllowed`
- C4 `setEmotion` 里有 `emotion in _state.value.allowedEmotions` 的收敛判断
- C5 `MainActivity.kt` 里出现 `Checkbox` 与 `toggleEmotionAllowed`
- C6 `gradle --offline assembleDebug` 退出码 0

## 非目标
- 不改 qq-bridge 的推送逻辑
- 不做「至少要留一项」的强约束（全部取消勾选时一切心情都回退平常，是可接受的）
- 不改立绘资源与眨眼帧
