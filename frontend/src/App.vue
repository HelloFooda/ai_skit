<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import AssetLibrary from "./AssetLibrary.vue";
import ChapterAssetDrawer from "./ChapterAssetDrawer.vue";
import ResultView from "./ResultView.vue";
import {
  generateNovelStream,
  listAssetNovels,
  listSkills,
  type NovelAssetSummary,
  type NovelExtractMode,
  type NovelGenerateResponse,
  type SkillInfo,
} from "./api";
import { theme, toggleTheme } from "./theme";

const tab = ref<"generate" | "library">("generate");
const novelTitle = ref("测试小说");
const content = ref("");

/** 已归档小说：小说名输入框的候选项，name 是磁盘目录名（归档用的就是它） */
const novelOptions = ref<NovelAssetSummary[]>([]);
const novelListNotice = ref("");

async function loadNovelOptions(initial = false) {
  try {
    const list = await listAssetNovels();
    // 最近更新的排前面，省得每次翻找
    novelOptions.value = [...list].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    // 仅首次加载：默认名还是占位值时落到最近用过的一本；刷新候选时不覆盖用户输入
    if (initial && novelOptions.value.length && novelTitle.value === "测试小说") {
      novelTitle.value = novelOptions.value[0].name;
    }
    novelListNotice.value = "";
  } catch (e: unknown) {
    novelListNotice.value = e instanceof Error ? e.message : String(e);
  }
}

// 高级设置：默认全部走默认口径，用户不用管
const showAdvanced = ref(false);
const characterHint = ref("");
const propHint = ref("");
const sceneHint = ref("");
const mode = ref<NovelExtractMode>("auto");
const targetDuration = ref(15);
const maxGroups = ref(6);

const skillOptions = ref<SkillInfo[]>([]);
const selectedSkills = ref<string[]>([]);
const skillNotice = ref("");

type StepState = "run" | "done";
interface Step {
  text: string;
  state: StepState;
}
/** 对话消息：text 普通消息；steps 进度步骤；result 一次生成结果 */
interface Msg {
  id: number;
  role: "user" | "assistant";
  kind: "text" | "steps" | "result";
  text?: string;
  meta?: string;
  tone?: "error";
  steps?: Step[];
  result?: NovelGenerateResponse;
  /** 生成当时的参数快照，避免改设置后回看旧结果时文案漂移 */
  dur?: number;
  maxG?: number;
}

let seq = 0;
const messages = ref<Msg[]>([
  {
    id: ++seq,
    role: "assistant",
    kind: "text",
    text:
      "把小说名和章节原文发给我，我按「第X章」切分后逐章出五类描述（角色 / 道具 / 场景 / 站位 / 分镜）。\n" +
      "原文直接贴进下面的输入框即可，标题要独占一行。",
  },
]);

const loading = ref(false);
const abortController = ref<AbortController | null>(null);
const threadEl = ref<HTMLElement | null>(null);

// ---- 提问节点条 ----
/** 每次用户提问就是一个节点；序号与消息绑定，不随消息增删漂移 */
const questions = computed(() => messages.value.filter((m) => m.role === "user" && m.kind === "text"));
const activeNodeId = ref<number | null>(null);
const railCollapsed = ref(false);

const msgDomId = (id: number) => `msg-${id}`;

/** 节点标题取原文第一个非空行（通常是「第X章」标题），过长截断 */
function nodeLabel(m: Msg): string {
  const first = (m.text ?? "").split("\n").map((s) => s.trim()).find(Boolean) ?? "未命名提问";
  return first.length > 15 ? `${first.slice(0, 15)}…` : first;
}

function nodeNovel(m: Msg): string {
  return m.meta?.split(" · ")[0] ?? "";
}

/** 滚动时找出最后一个已经越过容器顶部的提问节点，作为当前所在节点 */
function onThreadScroll() {
  const el = threadEl.value;
  if (!el || !questions.value.length) return;
  const top = el.getBoundingClientRect().top;
  let current = questions.value[0].id;
  for (const q of questions.value) {
    const node = document.getElementById(msgDomId(q.id));
    if (node && node.getBoundingClientRect().top - top <= 90) current = q.id;
  }
  activeNodeId.value = current;
}

function scrollToNode(id: number) {
  activeNodeId.value = id;
  document.getElementById(msgDomId(id))?.scrollIntoView({ behavior: "smooth", block: "start" });
}

/** 对话页资产抽屉：记录是哪条生成结果打开的，以及停在哪一章 */
const assetDrawerMsgId = ref<number | null>(null);
const assetDrawerChapter = ref(0);
const assetDrawerMsg = computed(() => messages.value.find((m) => m.id === assetDrawerMsgId.value) ?? null);
const assetDrawerResult = computed(() => assetDrawerMsg.value?.result ?? null);

function openAssetDrawer(msg: Msg, chapterIndex: number) {
  assetDrawerMsgId.value = msg.id;
  assetDrawerChapter.value = chapterIndex;
}

function closeAssetDrawer() {
  assetDrawerMsgId.value = null;
}

function assetDrawerToLibrary() {
  closeAssetDrawer();
  switchToLibrary();
}

const CATEGORY_LABELS: Record<string, string> = {
  characters: "角色",
  props: "道具",
  scenes: "场景",
  storyboard: "分镜",
  blocking: "站位",
  script: "剧本",
  review: "审查",
  media: "出片",
  global: "通用",
  manual: "手动",
};

function categoryText(categories: string[]): string {
  const real = categories.filter((c) => c !== "manual");
  return real.length ? real.map((c) => CATEGORY_LABELS[c] || c).join("/") : "未声明类别";
}

const autoLoadable = computed(
  () => skillOptions.value.filter((s) => s.categories.some((c) => c !== "manual")).length,
);

onMounted(async () => {
  // 窄屏默认收起节点条，展开时作为浮层覆盖，不挤压对话正文
  railCollapsed.value = window.innerWidth < 1320;
  void loadNovelOptions(true);
  try {
    const res = await listSkills();
    skillOptions.value = [...res.skills].sort((a, b) => {
      const aAuto = a.categories.some((c) => c !== "manual") ? 0 : 1;
      const bAuto = b.categories.some((c) => c !== "manual") ? 0 : 1;
      return aAuto - bAuto || a.name.localeCompare(b.name);
    });
    if (res.errors.length) {
      skillNotice.value = `有 ${res.errors.length} 个技能文件格式有误、已跳过：${res.errors.join("；")}`;
    }
  } catch (e: unknown) {
    skillNotice.value = e instanceof Error ? e.message : String(e);
  }
  scrollToBottom();
  onThreadScroll();
});

async function scrollToBottom() {
  await nextTick();
  const el = threadEl.value;
  if (el) el.scrollTop = el.scrollHeight;
}

function push(msg: Omit<Msg, "id">): Msg {
  messages.value.push({ ...msg, id: ++seq });
  scrollToBottom();
  // 必须从 ref 里取回代理对象再改内部数组，直接改原始对象不会触发视图更新
  return messages.value[messages.value.length - 1];
}

/** 进度事件 → 步骤列表：上一步收尾打勾，新一步接着转 */
function advance(steps: Step[], text: string) {
  if (!text) return;
  const last = steps[steps.length - 1];
  if (last && last.text === text) return;
  if (last) last.state = "done";
  steps.push({ text, state: "run" });
  // 长篇小说步骤会很多，只留最近若干条，避免把结果挤出视口
  while (steps.length > 14) steps.shift();
  scrollToBottom();
}

function onKeydown(e: KeyboardEvent) {
  // 正文是多行原文，回车用于换行；发送走 Cmd/Ctrl + Enter
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    send();
  }
}

async function send() {
  if (loading.value) return;
  const raw = content.value.trim();
  const title = novelTitle.value.trim() || "未命名小说";
  if (!raw) {
    push({ role: "assistant", kind: "text", tone: "error", text: "还没收到正文，把章节原文贴进来再发。" });
    return;
  }

  push({
    role: "user",
    kind: "text",
    text: raw,
    meta: `《${title}》 · ${raw.length} 字`,
  });
  content.value = "";

  const stepMsg = push({ role: "assistant", kind: "steps", steps: [] });
  const steps = stepMsg.steps!;
  loading.value = true;
  const controller = new AbortController();
  abortController.value = controller;
  let finished = false;

  try {
    await generateNovelStream(
      {
        novel_title: title,
        content: raw,
        character_hint: characterHint.value,
        prop_hint: propHint.value,
        scene_hint: sceneHint.value,
        mode: mode.value,
        target_duration: targetDuration.value,
        max_groups: maxGroups.value,
        skills: selectedSkills.value,
      },
      {
        signal: controller.signal,
        onProgress: (event) => advance(steps, event.text),
        onDone: (result) => {
          finished = true;
          steps.forEach((s) => (s.state = "done"));
          // 新小说归档后刷新候选，下次直接从下拉里选，不用再手打
          void loadNovelOptions();
          push({
            role: "assistant",
            kind: "result",
            result,
            dur: targetDuration.value,
            maxG: maxGroups.value,
          });
        },
        onError: (message) => {
          finished = true;
          steps.forEach((s) => (s.state = "done"));
          push({ role: "assistant", kind: "text", tone: "error", text: `生成失败：${message}` });
        },
      },
    );
  } catch (e: unknown) {
    steps.forEach((s) => (s.state = "done"));
    if (e instanceof DOMException && e.name === "AbortError") {
      push({ role: "assistant", kind: "text", text: "已停止生成。" });
    } else {
      const message = e instanceof Error ? e.message : String(e);
      push({ role: "assistant", kind: "text", tone: "error", text: `请求失败：${message}` });
    }
  } finally {
    if (!finished) steps.forEach((s) => (s.state = "done"));
    loading.value = false;
    abortController.value = null;
    scrollToBottom();
  }
}

function cancelRun() {
  abortController.value?.abort();
}

function switchToLibrary() {
  tab.value = "library";
}

function goGenerate() {
  tab.value = "generate";
  scrollToBottom();
}
</script>

<template>
  <main class="wrap" :class="{ 'library-mode': tab === 'library' }">
    <header class="topbar">
      <div class="brand">
        <span class="dot"></span>
        <b>剧集生成</b>
        <span class="muted small">小说 → 五类描述 → 资产归档</span>
      </div>
      <nav class="tabs">
        <button class="top-tab" :class="{ active: tab === 'generate' }" @click="goGenerate">
          对话
        </button>
        <button class="top-tab" :class="{ active: tab === 'library' }" @click="switchToLibrary">
          资产库
        </button>
      </nav>
      <button
        class="theme-toggle"
        :title="theme === 'dark' ? '切换到浅色' : '切换到深色'"
        :aria-label="theme === 'dark' ? '切换到浅色' : '切换到深色'"
        @click="toggleTheme"
      >
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
          <path
            fill="currentColor"
            d="M12 3a9 9 0 1 0 9 9c0-.46-.04-.92-.1-1.36a5.4 5.4 0 0 1-4.4 2.26 5.4 5.4 0 0 1-5.4-5.4c0-1.9.98-3.57 2.46-4.54A9 9 0 0 0 12 3Z"
          />
        </svg>
        <svg v-else viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
          <circle cx="12" cy="12" r="4.2" fill="currentColor" />
          <path
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            d="M12 2v2.6M12 19.4V22M4.9 4.9l1.9 1.9M17.2 17.2l1.9 1.9M2 12h2.6M19.4 12H22M4.9 19.1l1.9-1.9M17.2 6.8l1.9-1.9"
          />
        </svg>
      </button>
    </header>

    <template v-if="tab === 'generate'">
      <div ref="threadEl" class="thread" @scroll.passive="onThreadScroll">
        <div v-for="m in messages" :id="msgDomId(m.id)" :key="m.id" class="msg" :class="m.role">
          <div v-if="m.role === 'assistant'" class="avatar" aria-hidden="true">AI</div>
          <div class="bubble">
            <template v-if="m.kind === 'text'">
              <p class="msg-text" :class="{ err: m.tone === 'error' }">{{ m.text }}</p>
              <p v-if="m.meta" class="msg-meta">{{ m.meta }}</p>
            </template>

            <ul v-else-if="m.kind === 'steps'" class="steps">
              <li v-for="(s, i) in m.steps" :key="i" :class="s.state">
                <span class="step-icon" aria-hidden="true"></span>
                <span>{{ s.text }}</span>
              </li>
            </ul>

            <ResultView
              v-else-if="m.kind === 'result' && m.result"
              :result="m.result"
              :target-duration="m.dur ?? targetDuration"
              :max-groups="m.maxG ?? maxGroups"
              @open-library="switchToLibrary"
              @open-assets="openAssetDrawer(m, $event)"
            />
          </div>
        </div>
      </div>

      <!-- 提问节点条：抽屉打开时让位，避免与抽屉重叠 -->
      <aside v-if="!assetDrawerResult" class="node-rail" :class="{ collapsed: railCollapsed }">
        <button
          v-if="railCollapsed"
          class="rail-peek"
          title="展开提问节点"
          @click="railCollapsed = false"
        >
          节点 <span v-if="questions.length">{{ questions.length }}</span>
        </button>
        <template v-else>
          <div class="rail-head">
            <span>提问节点 <small>{{ questions.length }}</small></span>
            <button class="rail-close" title="收起节点条" @click="railCollapsed = true">×</button>
          </div>
          <div class="rail-list">
            <p v-if="!questions.length" class="rail-empty">发送章节原文后，这里会出现提问节点</p>
            <button
              v-for="(q, i) in questions"
              :key="q.id"
              class="rail-node"
              :class="{ active: activeNodeId === q.id }"
              :title="q.text"
              @click="scrollToNode(q.id)"
            >
              <span class="rail-dot" aria-hidden="true"></span>
              <span class="rail-body">
                <b>{{ i + 1 }}. {{ nodeLabel(q) }}</b>
                <small v-if="nodeNovel(q)">{{ nodeNovel(q) }}</small>
              </span>
            </button>
          </div>
          <div class="rail-foot">
            <button @click="scrollToBottom">回到最新 ↓</button>
          </div>
        </template>
      </aside>

      <div class="composer">
        <div v-if="showAdvanced" class="composer-panel">
          <p class="sub-title">
            分类补充提示词
            <span class="muted">（各自只作用于本类别，不会串到别的类别上）</span>
          </p>
          <div class="hint-grid">
            <div>
              <label class="field-label" for="character-hint">角色</label>
              <textarea
                id="character-hint"
                v-model="characterHint"
                rows="2"
                placeholder="例：重点写可跨分镜复用的稳定视觉特征"
              ></textarea>
            </div>
            <div>
              <label class="field-label" for="prop-hint">角色道具</label>
              <textarea
                id="prop-hint"
                v-model="propHint"
                rows="2"
                placeholder="例：只登记后文还会再次出现的道具"
              ></textarea>
            </div>
            <div>
              <label class="field-label" for="scene-hint">场景</label>
              <textarea
                id="scene-hint"
                v-model="sceneHint"
                rows="2"
                placeholder="例：光线写具体光源与方向"
              ></textarea>
            </div>
          </div>

          <div class="mode-row">
            <span class="field-label">调用方式</span>
            <label>
              <input type="radio" value="auto" v-model="mode" />
              出全五类 <span class="muted">（两段生成 + 编排）</span>
            </label>
            <label>
              <input type="radio" value="assets_only" v-model="mode" />
              仅资产 <span class="muted">（返修资产时用）</span>
            </label>
            <label>
              <input type="radio" value="per_type" v-model="mode" />
              三类分开 <span class="muted">（对照用）</span>
            </label>
          </div>

          <div class="mode-row">
            <span class="field-label">分镜</span>
            <label>
              单组目标
              <input class="num" type="number" min="4" max="16" v-model.number="targetDuration" />
              秒
            </label>
            <label>
              本次最多
              <input class="num" type="number" min="1" max="20" v-model.number="maxGroups" />
              组
            </label>
            <span class="muted small">一章常常不止一组，超出上限只出前 N 组并标注</span>
          </div>

          <p class="sub-title">
            技能规范
            <span class="muted">
              （不勾选 = 只自动装「声明过类别」的 {{ autoLoadable }} 个）
            </span>
          </p>
          <div class="skill-list">
            <label
              v-for="s in skillOptions"
              :key="s.name"
              class="skill-item"
              :class="{ 'skill-manual': !s.categories.some((c) => c !== 'manual') }"
              :title="s.description"
            >
              <input type="checkbox" :value="s.name" v-model="selectedSkills" />
              <span class="skill-name">{{ s.name }}</span>
              <span class="skill-tag">{{ categoryText(s.categories) }}</span>
              <span v-if="s.refs.length" class="skill-refs">{{ s.refs.length }} 篇细则</span>
            </label>
            <span v-if="!skillOptions.length" class="muted small">技能库为空或加载中…</span>
          </div>
          <p v-if="skillNotice" class="warn">{{ skillNotice }}</p>
        </div>

        <div class="composer-box">
          <div class="title-row">
            <span class="title-label">小说名</span>
            <input
              v-model="novelTitle"
              class="title-input"
              list="novel-options"
              placeholder="输入新小说名，或从已归档小说里选"
            />
            <datalist id="novel-options">
              <option
                v-for="n in novelOptions"
                :key="n.name"
                :value="n.name"
                :label="`${n.title} · ${n.entries} 项资产`"
              ></option>
            </datalist>
            <span v-if="novelListNotice" class="muted small" :title="novelListNotice">候选加载失败</span>
            <span v-else-if="novelOptions.length" class="muted small">{{ novelOptions.length }} 本已归档</span>
          </div>
          <textarea
            v-model="content"
            class="composer-input"
            rows="3"
            placeholder="把章节原文贴在这里……&#10;第一章：雨夜来客&#10;正文……"
            @keydown="onKeydown"
          ></textarea>
          <div class="composer-actions">
            <button class="gear" @click="showAdvanced = !showAdvanced">
              {{ showAdvanced ? "收起设置" : "高级设置" }}
            </button>
            <span class="muted small">
              ⌘/Ctrl + Enter 发送 · 标题以「第X章」开头且独占一行
            </span>
            <span class="spacer"></span>
            <button v-if="loading" class="cancel" @click="cancelRun">停止</button>
            <button class="send" :disabled="loading || !content.trim()" @click="send">
              {{ loading ? "生成中…" : "发送" }}
            </button>
          </div>
        </div>
      </div>
    </template>

    <AssetLibrary v-else :prefer-novel="novelTitle" />
  </main>

  <ChapterAssetDrawer
    v-if="assetDrawerResult"
    :open="true"
    :novel="assetDrawerResult.novel_title || novelTitle"
    :result="assetDrawerResult"
    :chapter-index="assetDrawerChapter"
    @update:chapter-index="assetDrawerChapter = $event"
    @close="closeAssetDrawer"
    @open-library="assetDrawerToLibrary"
  />
</template>

<style scoped>
.wrap {
  max-width: 980px;
  height: 100vh;
  margin: 0 auto;
  padding: 0 20px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  font-family: system-ui, -apple-system, sans-serif;
  color: var(--fg);
  background: var(--bg);
}
/* 资产库页面按普通文档流滚动，不受对话页的整屏高度约束 */
.wrap.library-mode {
  height: auto;
  min-height: 100vh;
  display: block;
  padding-bottom: 48px;
}
.topbar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border);
  padding: 5px 0 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  padding-bottom: 8px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
}
.tabs {
  display: flex;
  gap: 4px;
}
.top-tab {
  padding: 8px 16px;
  font-size: 13.5px;
  background: transparent;
  color: var(--fg-secondary);
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  cursor: pointer;
}
.top-tab.active {
  color: var(--accent);
  font-weight: 600;
  border-bottom-color: var(--accent);
}
.theme-toggle {
  flex: none;
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 6px;
  background: var(--bg-subtle);
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: 9px;
  cursor: pointer;
}
.theme-toggle:hover {
  border-color: var(--accent-border);
  background: var(--bg-hover);
}

/* ---- 对话区 ---- */
.thread {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 2px 26px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  scroll-behavior: smooth;
}
.msg {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  scroll-margin-top: 10px;
}
.msg.user {
  justify-content: flex-end;
}
.avatar {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10.5px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.bubble {
  min-width: 0;
  max-width: 100%;
  flex: 1;
}
.msg.user .bubble {
  flex: none;
  max-width: 74%;
  background: var(--user-bubble);
  border-radius: 14px 14px 4px 14px;
  padding: 10px 14px;
}
.msg-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.78;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--fg);
}
.msg.user .msg-text {
  max-height: 168px;
  overflow-y: auto;
  font-size: 13.5px;
  color: var(--fg-secondary);
}
.msg-text.err {
  color: var(--danger);
}
.msg-meta {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--fg-muted);
}

/* 进度：像助手在一步步报工，不做进度条 */
.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
}
.steps li {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  font-size: 13.5px;
  line-height: 1.6;
  color: var(--fg-muted);
}
.steps li.run {
  color: var(--accent);
}
.step-icon {
  flex: none;
  width: 13px;
  height: 13px;
  margin-top: 3px;
  border-radius: 50%;
  border: 1.5px solid var(--border-strong);
  position: relative;
}
.steps li.run .step-icon {
  border-color: var(--accent-border);
  border-top-color: var(--accent);
  animation: spin 0.8s linear infinite;
}
.steps li.done .step-icon {
  background: var(--accent);
  border-color: var(--accent);
}
.steps li.done .step-icon::after {
  content: "";
  position: absolute;
  left: 3px;
  top: 0.5px;
  width: 4px;
  height: 7px;
  border: solid var(--accent-contrast);
  border-width: 0 1.6px 1.6px 0;
  transform: rotate(42deg);
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ---- 提问节点条 ---- */
.node-rail {
  position: fixed;
  right: 16px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 5;
  width: 172px;
  max-height: calc(100vh - 150px);
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-subtle);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.node-rail.collapsed {
  width: auto;
  background: transparent;
  border: none;
  box-shadow: none;
}
.rail-peek {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--bg-subtle);
  color: var(--accent);
  font-size: 12px;
  box-shadow: var(--shadow);
  cursor: pointer;
}
.rail-peek span {
  margin-left: 3px;
  color: var(--fg-muted);
}
.rail-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px 7px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
  color: var(--fg-secondary);
}
.rail-head small {
  color: var(--fg-muted);
  font-weight: 400;
}
.rail-close {
  width: 20px;
  height: 20px;
  line-height: 1;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--fg-muted);
  font-size: 16px;
  cursor: pointer;
}
.rail-close:hover {
  background: var(--bg-hover);
  color: var(--fg);
}
.rail-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 7px 6px;
}
.rail-empty {
  margin: 6px 4px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--fg-muted);
}
.rail-node {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 7px 7px 7px 9px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--fg-secondary);
  text-align: left;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
}
.rail-node:hover {
  background: var(--bg-hover);
}
.rail-node.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.rail-dot {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  border-radius: 50%;
  border: 1.5px solid var(--border-strong);
  background: var(--bg-subtle);
}
.rail-node.active .rail-dot {
  background: var(--accent);
  border-color: var(--accent);
}
.rail-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rail-body b {
  font-weight: 500;
  word-break: break-all;
}
.rail-body small {
  font-size: 10.5px;
  color: var(--fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rail-foot {
  flex: none;
  padding: 6px 8px 8px;
  border-top: 1px solid var(--border);
}
.rail-foot button {
  width: 100%;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg);
  color: var(--fg-muted);
  font-size: 11.5px;
  cursor: pointer;
}
.rail-foot button:hover {
  border-color: var(--accent-border);
  color: var(--accent);
}

/* ---- 输入区 ---- */
.composer {
  flex: none;
  padding: 0 0 18px;
  background: var(--bg);
}
.composer-box {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 10px 12px 8px;
  background: var(--bg);
  box-shadow: var(--shadow);
  transition: border-color 0.15s ease;
}
.composer-box:focus-within {
  border-color: var(--accent-border);
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--border);
}
.title-label {
  font-size: 11.5px;
  color: var(--fg-muted);
  flex: none;
}
.title-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--fg-secondary);
  padding: 2px 0;
}
.composer-input {
  width: 100%;
  box-sizing: border-box;
  border: none;
  outline: none;
  resize: none;
  max-height: 30vh;
  overflow-y: auto;
  padding: 9px 0 4px;
  font-size: 14px;
  line-height: 1.75;
  font-family: inherit;
  color: var(--fg);
}
.composer-input::placeholder {
  color: var(--placeholder);
}
.composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 2px;
}
.spacer {
  flex: 1;
}
.composer-actions .muted.small {
  font-size: 11.5px;
}
button.send {
  padding: 7px 18px;
  font-size: 13.5px;
  border: none;
  border-radius: 9px;
  background: var(--accent);
  color: var(--accent-contrast);
  cursor: pointer;
}
button.send:disabled {
  opacity: 0.45;
  cursor: default;
}
button.cancel {
  padding: 7px 14px;
  font-size: 13px;
  background: var(--bg);
  color: var(--danger);
  border: 1px solid var(--danger-border);
  border-radius: 9px;
  cursor: pointer;
}
button.gear {
  padding: 6px 10px;
  font-size: 12px;
  background: var(--bg-subtle);
  color: var(--fg-secondary);
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
button.gear:hover {
  background: var(--bg-hover);
}

/* ---- 高级设置面板 ---- */
.composer-panel {
  max-height: 44vh;
  overflow-y: auto;
  margin-bottom: 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 0 14px 14px;
  background: var(--bg-subtle);
}
.field-label {
  display: block;
  font-size: 12.5px;
  font-weight: 600;
  margin: 0 0 6px;
}
.sub-title {
  font-size: 12.5px;
  font-weight: 600;
  margin: 14px 0 8px;
}
.hint-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.hint-grid textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 9px;
  font-size: 12.5px;
  line-height: 1.6;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  resize: vertical;
  background: var(--bg);
}
.mode-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 12px;
  font-size: 12.5px;
}
.mode-row .field-label {
  display: inline;
  margin: 0;
}
.mode-row label {
  cursor: pointer;
}
.num {
  width: 54px;
  padding: 3px 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12.5px;
  margin: 0 4px;
}
.skill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.skill-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12.5px;
  cursor: pointer;
  background: var(--bg);
}
.skill-item:hover {
  border-color: var(--border-strong);
}
.skill-manual {
  background: var(--bg-inset);
  border-style: dashed;
}
.skill-refs {
  color: var(--fg-muted);
  font-size: 11px;
}
.skill-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
}
.skill-tag {
  color: var(--fg-muted);
  font-size: 11.5px;
}
.small {
  font-size: 12px;
}
.muted {
  color: var(--fg-muted);
  font-weight: 400;
}
.warn {
  color: var(--warning);
  background: var(--warning-soft);
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.6;
}
@media (max-width: 720px) {
  .hint-grid {
    grid-template-columns: 1fr;
  }
  .msg.user .bubble {
    max-width: 86%;
  }
}
</style>
