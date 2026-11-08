const $ = (sel) => document.querySelector(sel);

const state = {
  health: null,
  tasks: [],
  runs: [],
  runId: null,
  detail: null,
  events: [],
  offset: 0,
  rendered: 0,
  timer: null,
  stalls: 0,
  stages: {},
  stageOrder: [],
};

const AGENT_COLORS = ["#3f7dfb", "#7a5cff", "#2fb3a3", "#d98a2b", "#e05c7a"];

const CHECK_TEXT = {
  artifact_nonempty: "产物非空",
  artifact_contains_all: "产物含关键结论",
  patch_scope: "只改允许的目录",
  patch_applies: "补丁能应用到项目",
  build: "assembleDebug 编译通过",
};

/* ---------- 基础工具 ---------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    method: opts.method || "GET",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const isJson = (res.headers.get("Content-Type") || "").includes("application/json");
  const data = isJson ? await res.json().catch(() => ({})) : await res.text();
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function fmtBytes(n) {
  if (n === undefined || n === null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function short(text, n) {
  const s = String(text ?? "");
  return s.length > n ? s.slice(0, n) + "…" : s;
}

let toastTimer = null;
function toast(text, bad = false, html = null) {
  const box = $("#toast");
  box.textContent = "";
  box.className = "toast" + (bad ? " bad" : "");
  box.appendChild(el("div", "", text));
  if (html) {
    const pre = el("pre", "", html);
    box.appendChild(pre);
  }
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), bad ? 9000 : 4200);
}

function stageMeta(id) {
  return state.stages[id] || { name: id, title: id, index: state.stageOrder.indexOf(id) };
}

function agentName(id) {
  const m = stageMeta(id);
  return m.name || id;
}

/* ---------- 运行列表 ---------- */

async function loadRuns(reselect = false) {
  const data = await api("/api/runs");
  state.runs = data.runs;
  const list = $("#run-list");
  list.textContent = "";
  if (!state.runs.length) {
    list.appendChild(el("div", "empty", "还没有运行记录"));
  }
  state.runs.forEach((run) => {
    const item = el("div", "run-item" + (run.run_id === state.runId ? " active" : "") + (run.stale ? " stale" : ""));
    const line = el("div", "rid");
    line.appendChild(el("span", "dot " + (run.stale ? "error" : run.status)));
    line.appendChild(el("span", "", run.run_id));
    item.appendChild(line);
    const stateWord = run.stale ? "已中断" : statusText(run.status);
    const modeTag = run.mode === "verify" ? "外部补丁 · " : "";
    item.appendChild(el("div", "rtitle", modeTag + (run.title ? run.title + " · " : "") + stateWord));
    item.onclick = () => selectRun(run.run_id);
    list.appendChild(item);
  });
  if (reselect && state.runId) {
    const still = state.runs.find((r) => r.run_id === state.runId);
    if (still) renderHeader(state.detail || { run_id: still.run_id, state: still, process: still.process, task: {} });
  }
}

/* ---------- 选择 / 轮询 ---------- */

async function selectRun(runId) {
  state.runId = runId;
  state.events = [];
  state.offset = 0;
  state.rendered = 0;
  state.stalls = 0;
  clearTimeout(state.timer);
  $("#timeline").textContent = "";
  $("#timeline").appendChild(el("div", "empty", "加载中…"));
  await loadRuns();
  await poll();
}

function schedule(ms = 1300) {
  clearTimeout(state.timer);
  state.timer = setTimeout(poll, ms);
}

async function poll() {
  state.timer = null;
  if (!state.runId) return;
  const runId = state.runId;
  try {
    const ev = await api(`/api/runs/${runId}/events?from=${state.offset}`);
    if (state.runId !== runId) return;
    if (ev.events.length) {
      state.events.push(...ev.events);
      state.offset = ev.next;
    }
    appendEvents();
    const detail = await api(`/api/runs/${runId}`);
    if (state.runId !== runId) return;
    state.detail = detail;
    renderHeader(detail);
    renderArtifacts(detail);
    renderReport(detail);

    const running = detail.process.alive;
    const active = running || (detail.state.status === "running" && !detail.stale);
    if (!active) {
      state.stalls = 0;
      await loadRuns(true);
      renderLive(false);
      return;
    }
    if (!state.runs.some((r) => r.run_id === runId)) await loadRuns();
    if (!running) {
      state.stalls += 1;
      if (state.stalls > 6) {
        renderLive(false);
        toast("进程已退出，但状态仍是 running：可能是模型调用异常，看「产物」里的 ui-console.log", true);
        return;
      }
    } else {
      state.stalls = 0;
    }
    renderLive(true);
    schedule();
  } catch (err) {
    schedule(2600);
  }
}

/* ---------- 头部 ---------- */

function statusText(status) {
  return { passed: "已通过", blocked: "已阻塞", error: "运行出错", running: "运行中" }[status] || status;
}

function renderSteps(st) {
  const wrap = el("div", "steps");
  if (st.mode === "verify") {
    const passed = st.status === "passed";
    const active = st.status === "running";
    const step = el("div", "step " + (passed ? "done" : active ? "active" : "retry"));
    step.appendChild(el("span", "n", passed ? "✓" : "1"));
    step.appendChild(el("span", "nm", "外部补丁校验"));
    step.appendChild(el("span", "tries", "不调用模型"));
    wrap.appendChild(step);
    return wrap;
  }
  const attempts = st.attempts || {};
  const verdicts = st.verdicts || {};
  state.stageOrder.forEach((sid, i) => {
    const passed = verdicts[sid] === "pass";
    const active = st.status === "running" && st.stage === sid;
    const tries = attempts[sid] || 0;
    const cls = passed ? "done" : active ? "active" : tries ? "retry" : "";
    const step = el("div", "step " + cls);
    step.appendChild(el("span", "n", passed ? "✓" : String(i + 1)));
    step.appendChild(el("span", "nm", stageMeta(sid).title || sid));
    if (tries > 1) step.appendChild(el("span", "tries", `${tries} 次`));
    wrap.appendChild(step);
  });
  return wrap;
}

function renderHeader(detail) {
  const box = $("#run-header");
  box.textContent = "";
  if (!detail || !detail.run_id) return;
  const card = el("div", "card");
  const st = detail.state || {};

  const top = el("div", "run-top");
  top.appendChild(el("span", "rid", detail.run_id));
  top.appendChild(
    el("span", "pill " + (detail.stale ? "error" : st.status || ""),
      detail.stale ? "已中断" : statusText(st.status))
  );
  if (detail.task && detail.task.title) top.appendChild(el("span", "badge", detail.task.title));
  if (st.mode === "verify") top.appendChild(el("span", "badge", "外部补丁"));
  if (state.events.some((e) => e.event === "apply" && !e.check && e.ok)) {
    top.appendChild(el("span", "badge ok", "补丁已落地"));
  }
  card.appendChild(top);

  if (state.stageOrder.length) card.appendChild(renderSteps(st));

  const actions = el("div", "actions");
  if (detail.process && detail.process.alive) {
    const stop = el("button", "danger", "停止运行");
    stop.onclick = async () => {
      if (!confirm("停止这次运行？沙箱里的构建也会被结束。")) return;
      try {
        await api(`/api/runs/${detail.run_id}/stop`, { method: "POST", body: {} });
        toast("已发出停止指令");
      } catch (err) {
        toast(err.message, true);
      }
    };
    actions.appendChild(stop);
  }
  if (st.status === "passed") {
    const check = el("button", "", "预检补丁（不改项目）");
    const land = el("button", "go", "落地到源码");
    check.onclick = () => runApply(detail.run_id, true, [check, land]);
    land.onclick = () => runApply(detail.run_id, false, [check, land]);
    actions.appendChild(check);
    actions.appendChild(land);
  }
  if (detail.stale) actions.appendChild(el("span", "badge warn", "进程已不在"));
  if (actions.childElementCount) card.appendChild(actions);

  if (st.status === "passed") {
    card.appendChild(
      el("div", "apply-note",
        `落地只写源码：不构建、不安装手机。原文件先备份到 runs\\${detail.run_id}\\backup\\。`)
    );
  }
  box.appendChild(card);
}

async function runApply(runId, check, buttons = []) {
  const tip = check
    ? "只做试应用检查（git apply --check），不改动项目。继续？"
    : "会把补丁写进 qq-avatar-app 的源码（原文件先备份）。不构建、不安装手机。继续？";
  if (!confirm(tip)) return;
  buttons.forEach((b) => { b.disabled = true; });
  toast(check ? "预检中…" : "落地中…");
  try {
    const out = await api(`/api/runs/${runId}/apply`, { method: "POST", body: { check } });
    await poll();
    const ok = out.returncode === 0;
    const msg = ok
      ? (check ? "预检通过：补丁能干净应用，项目未改动" : "已落地到源码（未构建、未安装手机）")
      : (check ? "预检没通过，原因见时间线" : "落地失败，原因见时间线");
    toast(msg, !ok);
  } catch (err) {
    toast(err.message, true);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

function renderLive(running) {
  const live = $("#badge-live");
  live.textContent = running ? "运行中" : "空闲";
  live.className = "badge " + (running ? "warn" : "");
  const start = $("#btn-start");
  const busy = state.runs.some((r) => r.process && r.process.alive);
  start.disabled = busy;
  $("#compose-hint").textContent = busy
    ? "有运行正在进行，等它结束再发起（构建资源独占）。"
    : "一次只跑一条；关掉网页不会打断运行。";
}

/* ---------- 时间线 ---------- */

function nearBottom() {
  const tl = $("#timeline");
  return tl.scrollHeight - tl.scrollTop - tl.clientHeight < 80;
}

function appendEvents() {
  const tl = $("#timeline");
  if (!state.events.length) {
    tl.textContent = "";
    tl.appendChild(el("div", "empty", "这次运行还没有事件"));
    state.rendered = 0;
    return;
  }
  if (state.rendered === 0) tl.textContent = "";
  const stick = nearBottom();
  state.events.slice(state.rendered).forEach((ev) => {
    const node = renderEvent(ev);
    if (node) tl.appendChild(node);
  });
  state.rendered = state.events.length;
  if (stick) tl.scrollTop = tl.scrollHeight;
}

function renderEvent(ev) {
  const sid = ev.stage;
  switch (ev.event) {
    case "run_start":
      return ev.mode === "verify"
        ? el("div", "sys", `外部补丁校验 · 不调用模型 · 任务 ${ev.task || "-"}`)
        : el("div", "sys", `开始运行 · 任务 ${ev.task || "-"}`);
    case "stage_start": {
      const card = el("div", "agent");
      const meta = stageMeta(sid);
      const idx = state.stageOrder.indexOf(sid);
      const av = el("div", "avatar", (meta.name || sid).slice(0, 1));
      av.style.background = "#1d2634";
      av.style.color = AGENT_COLORS[idx % AGENT_COLORS.length];
      card.appendChild(av);
      const body = el("div", "agent-body");
      const head = el("div", "agent-head");
      head.appendChild(el("b", "", meta.name || sid));
      head.appendChild(el("span", "sid", sid));
      body.appendChild(head);
      body.appendChild(el("div", "agent-line", meta.readback ? "先接手复述，再动手…" : "开始工作…"));
      card.appendChild(body);
      return card;
    }
    case "agent_message": {
      const card = el("div", "msg");
      const head = el("div", "msg-head");
      head.appendChild(el("b", "", agentName(sid)));
      head.appendChild(el("span", "tag", `第 ${ev.attempt} 次 · ${ev.chars} 字符`));
      card.appendChild(head);
      card.appendChild(el("pre", "msg-body", ev.text || ""));
      return card;
    }
    case "artifact": {
      const chip = el("div", "chip");
      chip.appendChild(el("span", "", "产物 " + ev.name));
      chip.appendChild(el("span", "bytes", fmtBytes(ev.bytes)));
      chip.onclick = () => openFile(`${ev.stage}/${ev.name}`);
      return chip;
    }
    case "l1": {
      const wrap = el("div", "checks");
      const title = el("div", "row");
      title.appendChild(el("span", ev.ok ? "ok" : "no", ev.ok ? "✓" : "✗"));
      title.appendChild(el("span", "", "机械校验"));
      title.appendChild(el("span", "det", ev.ok ? "全部通过" : "有硬性检查没过"));
      wrap.appendChild(title);
      (ev.results || []).forEach((r) => {
        const row = el("div", "row");
        row.appendChild(el("span", r.ok ? "ok" : "no", r.ok ? "✓" : "✗"));
        row.appendChild(el("span", "", CHECK_TEXT[r.check] || r.check));
        row.appendChild(el("span", "det", short(r.detail, 160)));
        wrap.appendChild(row);
      });
      return wrap;
    }
    case "behavior": {
      const wrap = el("div", "checks");
      const title = el("div", "row");
      title.appendChild(el("span", ev.ok ? "ok" : "no", ev.ok ? "✓" : "✗"));
      title.appendChild(el("span", "", "行为探针"));
      title.appendChild(el("span", "det", ev.ok ? "全部找到实现" : "有行为没找到实现证据"));
      wrap.appendChild(title);
      (ev.results || []).forEach((r) => {
        const row = el("div", "row");
        row.appendChild(el("span", r.ok ? "ok" : "no", r.ok ? "✓" : "✗"));
        row.appendChild(el("span", "", r.text || r.id));
        row.appendChild(el("span", "det", short(r.detail, 160)));
        wrap.appendChild(row);
      });
      return wrap;
    }
    case "verdict": {
      const pass = ev.verdict === "pass";
      const card = el("div", "agent verdict " + (pass ? "pass" : "revise"));
      const body = el("div", "agent-body");
      const head = el("div", "vhead");
      head.appendChild(el("b", "", pass ? "审批通过" : "审批否决"));
      head.appendChild(el("span", "tag", ev.label === "readback" ? "接手复述" : "环节产物"));
      head.appendChild(el("span", "tag", `${agentName(sid)} · 第 ${ev.attempt} 次`));
      head.appendChild(el("span", "tag", ev.arbiter === "llm" ? "AI 审批官" : "自动审批"));
      body.appendChild(head);
      const blocks = (ev.blocking || []).map((b) =>
        typeof b === "string" ? { criterion: b, reason: "", evidence: "" } : b);
      if (blocks.length) {
        const bx = el("div", "block");
        blocks.forEach((b) => {
          const line = el("div");
          line.appendChild(el("b", "why",
            b.reason ? `[${b.criterion}] ${b.reason} ` : `[${b.criterion}] 未通过 `));
          if (b.evidence) line.appendChild(el("span", "ev", short(b.evidence, 400)));
          bx.appendChild(line);
        });
        body.appendChild(bx);
      }
      card.appendChild(body);
      return card;
    }
    case "revise":
      return el("div", "chip warn",
        `第 ${ev.attempt} 次未过，返工：${
          (ev.blocking || []).map((b) => (typeof b === "string" ? b : b.criterion)).join("、")
        }`);
    case "readback_revise":
      return el("div", "chip warn", `接手复述未过，重来（第 ${ev.attempt} 次）`);
    case "escalate":
      return el("div", "banner bad", `返工预算用尽，流水线停住等人：${ev.reason || ""}`);
    case "run_end": {
      const yes = ev.status === "passed";
      const skipped = ev.skipped_semantic || [];
      const skipNote = skipped.length
        ? ` 语义验收 ${skipped.join("、")} 未机检（确定性模式不判），落地前请人工确认或跑闭环由审批官判。`
        : "";
      if (ev.mode === "verify") {
        return el("div", "banner " + (yes ? "ok" : "bad"),
          yes
            ? "校验通过：补丁能应用、构建通过、机检标准全过。可以预检或落地。" + skipNote
            : "校验没通过：看上面卡在哪一项（机械校验或行为探针）。");
      }
      return el("div", "banner " + (yes ? "ok" : "bad"),
        yes ? "闭环通过：产物已齐，可以预检或落地补丁。" + skipNote : "本次运行没通过。");
    }
    case "report":
      return el("div", "sys", "运行报告已生成 · 见右侧「运行报告」");
    case "apply": {
      const checkText = ev.check ? "预检" : "落地";
      const banner = el("div", "banner " + (ev.ok ? "ok" : "bad"));
      if (ev.ok) {
        banner.appendChild(el("div", "",
          ev.check ? "预检通过：补丁能干净应用，项目没有改动" : "已落地到源码（未构建、未安装手机）"));
      } else {
        banner.appendChild(el("div", "", `${checkText}失败，命令返回非 0`));
      }
      if (ev.output) {
        const more = document.createElement("details");
        more.appendChild(el("summary", "", "命令输出"));
        more.appendChild(el("pre", "", ev.output));
        banner.appendChild(more);
      }
      return banner;
    }
    case "error":
      return el("div", "banner bad", `运行出错：${ev.reason || ev.text || ""}`);
    default:
      return el("div", "sys", `${ev.event}${ev.stage ? " · " + ev.stage : ""}`);
  }
}

/* ---------- 产物 / 报告 ---------- */

function renderArtifacts(detail) {
  const list = $("#file-list");
  const files = detail.artifacts || [];
  const sig = files.map((f) => f.rel + ":" + f.bytes).join("|");
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.textContent = "";
  const groups = new Map();
  files.forEach((f) => {
    const key = f.stage || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  });
  const order = ["", ...state.stageOrder];
  order.forEach((sid) => {
    const files = groups.get(sid);
    if (!files || !files.length) return;
    list.appendChild(el("div", "file-group", sid ? `${sid} · ${agentName(sid)}` : "运行根目录"));
    files.forEach((f) => {
      const item = el("div", "file-item");
      item.appendChild(el("span", "", f.name));
      item.appendChild(el("span", "sz", fmtBytes(f.bytes)));
      item.onclick = () => openFile(f.rel);
      list.appendChild(item);
    });
  });
  if (!(detail.artifacts || []).length) list.appendChild(el("div", "empty", "还没有产物"));
}

async function openFile(rel) {
  try {
    const text = await api(`/api/runs/${state.runId}/file?path=${encodeURIComponent(rel)}`);
    $("#file-name").textContent = rel;
    $("#file-body").textContent = text;
    $("#file-view").classList.remove("hidden");
    $("#file-list").classList.add("hidden");
    $("#btn-back").classList.remove("hidden");
    $("#inspector-title").textContent = "产物内容";
  } catch (err) {
    toast(err.message, true);
  }
}

function closeFile() {
  $("#file-view").classList.add("hidden");
  $("#file-list").classList.remove("hidden");
  $("#btn-back").classList.add("hidden");
  $("#inspector-title").textContent = "产物";
}

function renderReport(detail) {
  const box = $("#report");
  const text = (detail.report || "").trim();
  box.textContent = text || "还没有报告（运行结束后生成）";
}

/* ---------- 发起运行 ---------- */

function fillTasks(prefer) {
  const sel = $("#task-select");
  sel.textContent = "";
  state.tasks.forEach((t) => {
    const opt = el("option", "", t.title || t.task_id);
    opt.value = t.task_id;
    sel.appendChild(opt);
  });
  const chosen = state.tasks.find((t) => t.task_id === prefer) || state.tasks[0];
  if (chosen) {
    sel.value = chosen.task_id;
    $("#requirement").value = chosen.requirement || "";
  }
}

function splitList(text) {
  return String(text || "").split(/[\n,，]/).map((s) => s.trim()).filter(Boolean);
}

async function createTask() {
  const btn = $("#btn-create-card");
  btn.disabled = true;
  try {
    const payload = {
      title: $("#card-title").value.trim(),
      requirement: $("#card-requirement").value.trim(),
      acceptance: splitList($("#card-acceptance").value),
      patch_paths: splitList($("#card-paths").value),
      keywords: splitList($("#card-keywords").value),
    };
    const out = await api("/api/tasks", { method: "POST", body: payload });
    const tasks = await api("/api/tasks");
    state.tasks = tasks.tasks;
    fillTasks(out.task_id);
    ["#card-title", "#card-requirement", "#card-acceptance", "#card-paths", "#card-keywords"]
      .forEach((s) => { $(s).value = ""; });
    $("#new-card-panel").open = false;
    toast(`任务卡已保存：${out.file}，已选中，可直接开始闭环或校验外部补丁`);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function startRun() {
  const btn = $("#btn-start");
  btn.disabled = true;
  try {
    const payload = {
      task_id: $("#task-select").value,
      requirement: $("#requirement").value,
      backend: $("#backend-select").value,
      drill: $("#drill").checked,
    };
    const out = await api("/api/runs", { method: "POST", body: payload });
    toast(`已发起 ${out.run_id}（backend=${out.backend}）`);
    await loadRuns();
    await selectRun(out.run_id);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    renderLive(false);
  }
}

/* ---------- 启动 ---------- */

async function init() {
  try {
    state.health = await api("/api/health");
    state.stages = {};
    state.stageOrder = [];
    (state.health.stages || []).forEach((s) => {
      state.stages[s.id] = { ...s, index: state.stageOrder.length };
      state.stageOrder.push(s.id);
    });
    const real = state.health.backend === "cloud";
    const modelName = String(state.health.model || "").split("/").pop();
    $("#badge-model").textContent = real ? `真实模型 · ${modelName}` : "离线桩模型";
    $("#badge-model").className = "badge " + (real ? "ok" : "warn");

    const tasks = await api("/api/tasks");
    state.tasks = tasks.tasks;
    fillTasks();

    await loadRuns();
    if (state.runs.length) await selectRun(state.runs[0].run_id);
    else renderLive(false);
  } catch (err) {
    toast("初始化失败：" + err.message, true);
  }
}

async function startVerify() {
  const btn = $("#btn-verify");
  btn.disabled = true;
  try {
    const out = await api("/api/verify", {
      method: "POST",
      body: { task_id: $("#task-select").value, patch: $("#patch-input").value },
    });
    toast(`已发起校验 ${out.run_id}（不调用模型）`);
    $("#patch-input").value = "";
    await loadRuns();
    await selectRun(out.run_id);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    renderLive(false);
  }
}

$("#btn-start").onclick = startRun;
$("#btn-verify").onclick = startVerify;
$("#btn-create-card").onclick = createTask;
$("#btn-refresh").onclick = () => loadRuns(true);
$("#btn-back").onclick = closeFile;
$("#task-select").onchange = (e) => {
  const task = state.tasks.find((t) => t.task_id === e.target.value);
  if (task) $("#requirement").value = task.requirement || "";
};

init();
