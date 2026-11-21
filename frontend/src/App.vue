<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  generateNovel,
  listSkills,
  type NovelExtractMode,
  type NovelGenerateResponse,
  type SkillInfo,
} from "./api";

const novelTitle = ref("测试小说");
const content = ref(
  "第一章：雨夜来客\n林桂芳推开老饭馆的木门，铜钥匙在掌心攥得发烫。傍晚的暖黄顶灯下，沈砚舟正低头擦着一只旧怀表。\n\n" +
    "第二章：铜钥匙\n沈砚舟抬起头：「你怎么来了。」林桂芳摊开手掌，铜钥匙在灯下发亮。",
);

// 高级设置：默认全部走默认口径，用户不用管
const showAdvanced = ref(false);
const characterHint = ref("");
const propHint = ref("");
const sceneHint = ref("");
const mode = ref<NovelExtractMode>("auto");
const targetDuration = ref(15);
const maxGroups = ref(6);

// 技能：全量展示，不按阶段过滤。只有「显式声明了类别」的技能会被自动装载
// （本项目用 backend/skill-bindings.yaml 声明，不改技能文件本身）。
const skillOptions = ref<SkillInfo[]>([]);
const selectedSkills = ref<string[]>([]);
const skillNotice = ref("");

const loading = ref(false);
const error = ref("");
const novelResult = ref<NovelGenerateResponse | null>(null);
const activeChapter = ref(0);
const current = computed(() => novelResult.value?.chapters[activeChapter.value] ?? null);

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

const autoLoadable = computed(() =>
  skillOptions.value.filter((s) => s.categories.some((c) => c !== "manual")),
);

/** 分镜总时长（按组时长之和，用来核对是否超出一章容量） */
const totalSeconds = computed(() =>
  (current.value?.storyboards ?? []).reduce((sum, g) => sum + g.duration, 0),
);

onMounted(async () => {
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
});

async function run() {
  loading.value = true;
  error.value = "";
  novelResult.value = null;
  activeChapter.value = 0;
  try {
    novelResult.value = await generateNovel({
      novel_title: novelTitle.value,
      content: content.value,
      character_hint: characterHint.value,
      prop_hint: propHint.value,
      scene_hint: sceneHint.value,
      mode: mode.value,
      target_duration: targetDuration.value,
      max_groups: maxGroups.value,
      skills: selectedSkills.value,
    });
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

const copied = ref("");
async function copy(text: string, key: string) {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = key;
    setTimeout(() => {
      if (copied.value === key) copied.value = "";
    }, 1200);
  } catch {
    copied.value = "";
  }
}
</script>

<template>
  <main class="wrap">
    <h1>贴一部小说的若干章，按章出五类描述</h1>
    <p class="lead">
      小说名独立填写，标题与正文贴同一个框。按「第X章」标题行确定性切分并逐字回拼，
      通过后才交给 LangGraph 逐章生成：每章先盘资产，再带资产表写站位与
      {{ targetDuration }}s 分镜组表。
    </p>

    <label class="field-label" for="novel-title">小说名</label>
    <input
      id="novel-title"
      class="title-input"
      v-model="novelTitle"
      placeholder="逐字保留，供后续资产归档使用"
    />

    <label class="field-label" for="content">章节原文（标题 + 正文，可多章）</label>
    <textarea
      id="content"
      v-model="content"
      rows="10"
      placeholder="第一章：标题&#10;正文……&#10;&#10;第二章：标题&#10;正文……"
    ></textarea>
    <p class="muted small hint-line">
      标题必须独占一行且以「第X章」开头（章/回/节/部/篇/集均可）；正文里随口提到的章号不会被误切。
    </p>

    <div class="actions">
      <button :disabled="loading" @click="run">
        {{ loading ? "生成中…" : "按章生成五类描述" }}
      </button>
      <span class="muted small">
        每章 2 次模型调用：盘资产 → 写站位与分镜。只想要资产可切「仅资产」，省一半。
      </span>
    </div>

    <details class="advanced" :open="showAdvanced" @toggle="showAdvanced = !showAdvanced">
      <summary>高级设置（选填，不填就走默认口径）</summary>

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
          出全五类 <span class="muted">（两段生成 + agent 编排，默认）</span>
        </label>
        <label>
          <input type="radio" value="assets_only" v-model="mode" />
          仅资产 <span class="muted">（一段生成，返修资产时用）</span>
        </label>
        <label>
          <input type="radio" value="per_type" v-model="mode" />
          三类分开 <span class="muted">（3 次并发，对照用）</span>
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
          （不勾选 = 只自动装「声明过类别」的 {{ autoLoadable.length }} 个）
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
    </details>

    <p v-if="error" class="err">{{ error }}</p>

    <section v-if="novelResult && current" class="result">
      <p class="meta">
        《{{ novelResult.novel_title }}》共 {{ novelResult.chapter_count }} 章 ·
        {{ novelResult.total_llm_calls }} 次生成 +
        {{ novelResult.total_agent_calls }} 次编排 · 合计 {{ novelResult.total_latency_ms }}ms ·
        机检 {{ novelResult.gate_issue_count }} 项
      </p>
      <div class="chapter-tabs">
        <button
          v-for="ch in novelResult.chapters"
          :key="ch.index"
          class="tab"
          :class="{ active: ch.index === current.index }"
          @click="activeChapter = ch.index - 1"
        >
          {{ ch.index }}. {{ ch.title }}
        </button>
      </div>
      <p class="meta">
        《{{ current.title }}》原文 {{ current.text_chars }} 字 · {{ current.latency_ms }}ms ·
        提示词 {{ current.prompt_version }} · 生成 {{ current.llm_calls }} 次 +
        编排 {{ current.agent_calls }} 次 ·
        技能 {{ current.skills_loaded.join("、") || "未装载" }} ·
        run_id {{ novelResult.run_id }} · {{ novelResult.model }} ·
        {{ novelResult.mock ? "mock（未配密钥）" : "真实调用" }}
        <template v-if="current.skills_refs_expanded">
          （展开 {{ current.skills_refs_expanded }} 篇细则<template v-if="current.skills_refs_skipped"
            >，{{ current.skills_refs_skipped }} 篇超预算</template
          >）
        </template>
      </p>

      <p v-if="current.injection_warning" class="warn">
        章节里检测到疑似指令式语句。已按「素材而非指令」处理，原文一字未改。
      </p>
      <p v-if="current.skills_skipped.length" class="warn">
        因篇幅预算未装载：{{ current.skills_skipped.join("、") }}
      </p>
      <p v-if="current.truncated" class="warn">
        本章预估需 {{ current.total_groups_estimated }} 组，本次只出前
        {{ current.storyboards.length }} 组（上限 {{ maxGroups }}）。调高「本次最多组数」或分段贴原文。
      </p>
      <p v-if="current.gate_issues.length" class="warn">
        机检提示 {{ current.gate_issues.length }} 项（不阻断，S5 正式门禁会拦）：
        {{ current.gate_issues.map((i) => `${i.group_id} ${i.detail}`).join("；") }}
      </p>

      <!-- 1 角色 -->
      <h2 class="block-title">① 角色 <span class="count">{{ current.characters.length }}</span></h2>
      <div class="cards">
        <article v-for="c in current.characters" :key="c.name" class="card">
          <header>
            <b>{{ c.name }}</b>
          </header>
          <p>{{ c.appearance }}</p>
          <p class="kv"><span>稳定特征</span>{{ c.stable_features }}</p>
          <p v-if="c.state" class="kv"><span>本集状态</span>{{ c.state }}</p>
          <p v-if="c.voice" class="kv"><span>声线</span>{{ c.voice }}</p>
          <div v-if="c.prompt" class="prompt">
            <div class="prompt-head">
              <span>资产描述</span>
              <button class="copy" @click="copy(c.prompt, 'c-' + c.name)">
                {{ copied === "c-" + c.name ? "已复制" : "复制" }}
              </button>
            </div>
            <p>{{ c.prompt }}</p>
          </div>
        </article>
      </div>

      <!-- 2 角色道具 -->
      <h2 class="block-title">② 角色道具 <span class="count">{{ current.props.length }}</span></h2>
      <div class="cards">
        <article v-for="p in current.props" :key="p.name" class="card">
          <header>
            <b>{{ p.name }}</b>
            <span v-if="p.owner" class="badge">归属 {{ p.owner }}</span>
            <span v-else class="badge gray">环境陈设</span>
          </header>
          <p>{{ p.appearance }}</p>
          <p v-if="p.state_changes" class="kv"><span>状态变化</span>{{ p.state_changes }}</p>
          <div v-if="p.prompt" class="prompt">
            <div class="prompt-head">
              <span>资产描述</span>
              <button class="copy" @click="copy(p.prompt, 'p-' + p.name)">
                {{ copied === "p-" + p.name ? "已复制" : "复制" }}
              </button>
            </div>
            <p>{{ p.prompt }}</p>
          </div>
        </article>
      </div>

      <!-- 3 场景 -->
      <h2 class="block-title">③ 场景 <span class="count">{{ current.scenes.length }}</span></h2>
      <div class="cards">
        <article v-for="s in current.scenes" :key="s.name" class="card">
          <header>
            <b>{{ s.name }}</b>
            <span class="badge gray">{{ s.time }}</span>
          </header>
          <p class="kv"><span>光线</span>{{ s.light }}</p>
          <p class="kv"><span>氛围</span>{{ s.atmosphere }}</p>
          <div v-if="s.prompt" class="prompt">
            <div class="prompt-head">
              <span>资产描述</span>
              <button class="copy" @click="copy(s.prompt, 's-' + s.name)">
                {{ copied === "s-" + s.name ? "已复制" : "复制" }}
              </button>
            </div>
            <p>{{ s.prompt }}</p>
          </div>
        </article>
      </div>

      <!-- 4 角色站位 -->
      <template v-if="current.blockings.length">
        <h2 class="block-title">
          ④ 角色站位 <span class="count">{{ current.blockings.length }} 组</span>
        </h2>
        <p class="note">从分镜表第十列聚合，不单独生成，所以永远和分镜一致。</p>
        <div class="groups">
          <article v-for="b in current.blockings" :key="b.group_id" class="group">
            <header class="group-head">
              <b>{{ b.group_id }}</b>
              <span class="muted">{{ b.location }}</span>
            </header>
            <pre class="blocking">{{ b.text }}</pre>
          </article>
        </div>
      </template>

      <!-- 5 15s 分镜 -->
      <template v-if="current.storyboards.length">
        <h2 class="block-title">
          ⑤ {{ targetDuration }}s 分镜
          <span class="count">
            {{ current.storyboards.length }} 组 / 共 {{ Math.round(totalSeconds) }}s
          </span>
        </h2>
        <p class="note">组时长 = 各镜之和；单镜 ≥1.2s；固定机位写「机位固定」；台词逐字取自原文。</p>
        <div class="groups">
          <article v-for="g in current.storyboards" :key="g.group_id" class="group">
            <header class="group-head">
              <b>{{ g.group_id }}</b>
              <span class="badge">{{ g.duration }}s</span>
              <span class="muted">{{ g.location }}</span>
              <span v-if="g.summary" class="muted">· {{ g.summary }}</span>
            </header>
            <div v-for="sh in g.shots" :key="sh.shot_no" class="shot">
              <div class="shot-head">
                <span class="shot-no">{{ sh.shot_no }}</span>
                <span class="badge gray">{{ sh.duration }}s</span>
                <span class="tag">{{ sh.shot_size }}</span>
                <span class="tag">{{ sh.angle }}</span>
                <span class="tag">{{ sh.camera }}</span>
                <span class="tag func">{{ sh.function }}</span>
              </div>
              <p class="shot-body">{{ sh.content }}</p>
              <p v-if="sh.dialogue" class="dialogue">「{{ sh.dialogue }}」</p>
              <p class="blocking-line">{{ sh.blocking }}</p>
            </div>
          </article>
        </div>
      </template>
    </section>
  </main>
</template>

<style scoped>
.wrap {
  max-width: 1040px;
  margin: 0 auto;
  padding: 28px 24px 60px;
  font-family: system-ui, -apple-system, sans-serif;
  color: #1d1d1f;
}
h1 {
  font-size: 21px;
  margin: 0 0 6px;
}
.lead {
  color: #777;
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 18px;
}
.field-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 6px;
}
.sub-title {
  font-size: 13px;
  font-weight: 600;
  margin: 18px 0 8px;
}
textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 12px;
  font-size: 14px;
  line-height: 1.7;
  border: 1px solid #ddd;
  border-radius: 10px;
  font-family: inherit;
}
textarea::placeholder {
  color: #b0b0b0;
}
.title-input {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 14px;
  padding: 10px 12px;
  font-size: 14px;
  border: 1px solid #ddd;
  border-radius: 10px;
  font-family: inherit;
}
.hint-line {
  margin: 6px 0 0;
}
.chapter-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0;
}
.tab {
  padding: 6px 12px;
  font-size: 12px;
  background: #fff;
  color: #0c447c;
  border: 1px solid #d6e2ef;
  border-radius: 999px;
}
.tab.active {
  background: #0c447c;
  color: #fff;
}
.hint-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.hint-grid textarea {
  font-size: 13px;
  padding: 9px;
  resize: vertical;
}
.actions {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin: 12px 0 4px;
}
button {
  padding: 9px 22px;
  font-size: 14px;
  border: none;
  border-radius: 9px;
  background: #0c447c;
  color: #fff;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.advanced {
  margin: 20px 0 8px;
  border: 1px dashed #ddd;
  border-radius: 10px;
  padding: 0 14px 12px;
}
.advanced summary {
  cursor: pointer;
  font-size: 13px;
  color: #666;
  padding: 10px 0;
}
.mode-row {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  margin-top: 12px;
  font-size: 13px;
}
.mode-row .field-label {
  display: inline;
  margin: 0;
}
.mode-row label {
  cursor: pointer;
}
.num {
  width: 56px;
  padding: 3px 6px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 13px;
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
  padding: 6px 10px;
  border: 1px solid #ddd;
  border-radius: 999px;
  font-size: 13px;
  cursor: pointer;
  background: #fff;
}
.skill-item:hover {
  border-color: #bbb;
}
.skill-manual {
  background: #fafafa;
  border-style: dashed;
}
.skill-refs {
  color: #aaa;
  font-size: 11px;
}
.skill-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.skill-tag {
  color: #888;
  font-size: 12px;
}
.small {
  font-size: 12px;
}
.muted {
  color: #888;
  font-weight: 400;
}
.err {
  color: #c33;
  font-size: 13px;
}
.warn {
  color: #8a5a00;
  background: #fff7e6;
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.6;
}
.result {
  margin-top: 26px;
  border-top: 1px solid #eee;
  padding-top: 8px;
}
.meta {
  font-size: 12px;
  color: #999;
  line-height: 1.7;
}
.block-title {
  font-size: 15px;
  margin: 26px 0 8px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.count {
  font-size: 12px;
  color: #999;
  font-weight: 400;
}
.note {
  font-size: 12px;
  color: #999;
  margin: 0 0 10px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}
.card {
  background: #f8f8f6;
  border-radius: 10px;
  padding: 13px 15px;
}
.card header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.card b {
  font-size: 14px;
}
.card p {
  margin: 3px 0;
  font-size: 13px;
  line-height: 1.65;
}
.kv span {
  display: inline-block;
  min-width: 62px;
  color: #999;
  font-size: 12px;
  margin-right: 6px;
}
.badge {
  font-size: 11px;
  background: #e8f0f9;
  color: #0c447c;
  border-radius: 999px;
  padding: 2px 8px;
  white-space: nowrap;
}
.badge.gray {
  background: #eee;
  color: #777;
}
.prompt {
  margin-top: 9px;
  border-top: 1px dashed #e2e2e2;
  padding-top: 8px;
}
.prompt-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #999;
  margin-bottom: 4px;
}
.prompt p {
  color: #333;
  font-size: 12.5px;
  line-height: 1.7;
}
.copy {
  background: #fff;
  color: #0c447c;
  border: 1px solid #d6e2ef;
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 8px;
}
.groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.group {
  border: 1px solid #e8e8e8;
  border-radius: 10px;
  padding: 12px 14px;
  background: #fff;
}
.group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  font-size: 13px;
}
.blocking {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.9;
  color: #444;
  white-space: pre-wrap;
  background: #fafafa;
  border-radius: 8px;
  padding: 9px 11px;
}
.shot {
  border-top: 1px solid #f0f0f0;
  padding: 9px 0;
}
.shot:first-of-type {
  border-top: none;
}
.shot-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.shot-no {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0c447c;
  color: #fff;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tag {
  font-size: 11px;
  color: #555;
  background: #f2f2f2;
  border-radius: 5px;
  padding: 2px 7px;
}
.tag.func {
  background: #f6f0e4;
  color: #8a5a00;
}
.shot-body {
  font-size: 13px;
  line-height: 1.65;
  margin: 5px 0 3px;
}
.dialogue {
  font-size: 13px;
  color: #0c447c;
  margin: 2px 0;
}
.blocking-line {
  font-size: 12px;
  color: #999;
  margin: 3px 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (max-width: 720px) {
  .hint-grid {
    grid-template-columns: 1fr;
  }
}
</style>
