<script setup lang="ts">
/** 一次生成结果的展示：章节切换 + 五类产出。作为对话里的一条助手消息内容渲染。 */
import { computed, ref, watch } from "vue";
import type { NovelGenerateResponse } from "./api";

const props = defineProps<{
  result: NovelGenerateResponse;
  targetDuration: number;
  maxGroups: number;
}>();

const emit = defineEmits<{
  (e: "open-library"): void;
  (e: "open-assets", chapterIndex: number): void;
}>();

const activeChapter = ref(0);
const current = computed(() => props.result.chapters[activeChapter.value] ?? null);

watch(
  () => props.result,
  () => {
    activeChapter.value = 0;
  },
);

/** 分镜总时长（按组时长之和，用来核对是否超出一章容量） */
const totalSeconds = computed(() =>
  (current.value?.storyboards ?? []).reduce((sum, g) => sum + g.duration, 0),
);

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
  <section v-if="current" class="result">
    <p class="meta">
      《{{ result.novel_title }}》共 {{ result.chapter_count }} 章 ·
      {{ result.total_llm_calls }} 次生成 +
      {{ result.total_agent_calls }} 次编排 · 合计 {{ result.total_latency_ms }}ms ·
      机检 {{ result.gate_issue_count }} 项
    </p>
    <div class="chapter-tabs">
      <button
        v-for="ch in result.chapters"
        :key="ch.index"
        class="tab"
        :class="{ active: ch.index === current.index }"
        @click="activeChapter = ch.index - 1"
      >
        {{ ch.index }}. {{ ch.title }}
      </button>
      <button class="assets-entry" @click="emit('open-assets', activeChapter)">
        本章资产 →
      </button>
    </div>
    <p class="meta">
      《{{ current.title }}》原文 {{ current.text_chars }} 字 · {{ current.latency_ms }}ms ·
      提示词 {{ current.prompt_version }} · 生成 {{ current.llm_calls }} 次 +
      编排 {{ current.agent_calls }} 次 ·
      技能 {{ current.skills_loaded.join("、") || "未装载" }} ·
      run_id {{ result.run_id }} · {{ result.model }} ·
      {{ result.mock ? "mock（未配密钥）" : "真实调用" }}
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

    <!-- 5 分镜 -->
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

    <p v-if="result.archive && result.archive.image_prompt_entries" class="archive-tip">
      已归档 {{ result.archive.image_prompt_entries }} 条生图提示词到
      <code>{{ result.archive.novel_dir }}</code>
      <button class="ghost-link" @click="emit('open-library')">去资产库查看 / 编辑</button>
    </p>
  </section>
</template>

<style scoped>
.result {
  margin-top: 2px;
}
.chapter-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}
.tab {
  padding: 6px 12px;
  font-size: 12px;
  background: var(--bg);
  color: var(--accent);
  border: 1px solid var(--accent-border);
  border-radius: 999px;
  cursor: pointer;
}
.tab.active {
  background: var(--accent);
  color: var(--accent-contrast);
}
.assets-entry {
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--accent-border);
  border-radius: 999px;
  cursor: pointer;
}
.assets-entry:hover {
  background: var(--accent);
  color: var(--accent-contrast);
}
.meta {
  font-size: 12px;
  color: var(--fg-muted);
  line-height: 1.7;
  margin: 4px 0;
}
.block-title {
  font-size: 15px;
  margin: 22px 0 8px;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.count {
  font-size: 12px;
  color: var(--fg-muted);
  font-weight: 400;
}
.note {
  font-size: 12px;
  color: var(--fg-muted);
  margin: 0 0 10px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.card {
  background: var(--bg-subtle);
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
  color: var(--fg-muted);
  font-size: 12px;
  margin-right: 6px;
}
.badge {
  font-size: 11px;
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 999px;
  padding: 2px 8px;
  white-space: nowrap;
}
.badge.gray {
  background: var(--tag-bg);
  color: var(--fg-secondary);
}
.prompt {
  margin-top: 9px;
  border-top: 1px dashed var(--border);
  padding-top: 8px;
}
.prompt-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--fg-muted);
  margin-bottom: 4px;
}
.prompt p {
  color: var(--fg-secondary);
  font-size: 12.5px;
  line-height: 1.7;
}
.copy {
  background: var(--bg);
  color: var(--accent);
  border: 1px solid var(--accent-border);
  border-radius: 6px;
  font-size: 11px;
  padding: 2px 8px;
  cursor: pointer;
}
.groups {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.group {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--bg);
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
  color: var(--fg-secondary);
  white-space: pre-wrap;
  background: var(--bg-inset);
  border-radius: 8px;
  padding: 9px 11px;
}
.shot {
  border-top: 1px solid var(--border);
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
  background: var(--accent);
  color: var(--accent-contrast);
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tag {
  font-size: 11px;
  color: var(--fg-secondary);
  background: var(--tag-bg);
  border-radius: 5px;
  padding: 2px 7px;
}
.tag.func {
  background: var(--warning-soft);
  color: var(--warning);
}
.shot-body {
  font-size: 13px;
  line-height: 1.65;
  margin: 5px 0 3px;
}
.dialogue {
  font-size: 13px;
  color: var(--accent);
  margin: 2px 0;
}
.blocking-line {
  font-size: 12px;
  color: var(--fg-muted);
  margin: 3px 0 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.archive-tip {
  margin-top: 18px;
  font-size: 12px;
  color: var(--fg-secondary);
  background: var(--bg-subtle);
  border-radius: 8px;
  padding: 10px 12px;
  line-height: 1.7;
}
.archive-tip code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--accent);
}
.ghost-link {
  background: transparent;
  color: var(--accent);
  border: none;
  padding: 0 4px;
  font-size: 12px;
  text-decoration: underline;
  cursor: pointer;
}
.warn {
  color: var(--warning);
  background: var(--warning-soft);
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.6;
}
.muted {
  color: var(--fg-muted);
  font-weight: 400;
}
</style>
