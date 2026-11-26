<script setup lang="ts">
/** 图生图迭代弹窗：左看参考图、右写追加描述与 diff。
 *  状态与动作由 useAssetMedia 持有，本组件只负责展示与转发。 */
import { mediaFileUrl, type MediaImageRecord, type MediaRefineDraft } from "./api";

const props = defineProps<{
  novel: string;
  source: MediaImageRecord | null;
  addition: string;
  draft: MediaRefineDraft | null;
  loading: boolean;
  applying: boolean;
  error: string;
}>();

const emit = defineEmits<{
  close: [];
  "update:addition": [value: string];
  build: [];
  apply: [];
}>();
</script>

<template>
  <div v-if="source" class="drawer-mask refine-mask" @click.self="emit('close')">
    <section class="drawer refine-drawer">
      <div class="drawer-head">
        <div>
          <b>迭代 v{{ source.version }} · {{ source.asset_name }}</b>
          <span class="muted small"> · 以这张图为参考重出</span>
        </div>
        <button class="ghost" :disabled="applying" @click="emit('close')">关闭</button>
      </div>

      <div class="refine-grid">
        <!-- 左：参考图占满，方便对照改哪里 -->
        <div class="refine-left">
          <p class="section-title">参考图 · v{{ source.version }}</p>
          <img
            class="source-image"
            :src="mediaFileUrl(novel, source.path)"
            :alt="`参考图 v${source.version}`"
          />
          <p class="muted small tips">
            参考图保留人物长相 / 风格 / 构图；分栏版式、连戏锁、画幅词由正文守。
            追加描述只写"想改什么"，不用重复原描述。
          </p>
        </div>

        <!-- 右：追加描述 → diff → 确认 -->
        <div class="refine-right">
          <label class="field-label" for="refine-addition">追加描述</label>
          <textarea
            id="refine-addition"
            class="refine-input"
            :value="addition"
            :disabled="loading || applying"
            placeholder="例：让他手里多拿一把半合的折扇，背景加一株竹"
            rows="5"
            @input="emit('update:addition', ($event.target as HTMLTextAreaElement).value)"
          />
          <button
            class="build-btn"
            :disabled="loading || applying || !addition.trim()"
            @click="emit('build')"
          >
            {{ loading ? "融合中…" : "生成正文草案（不落盘、不扣图费）" }}
          </button>

          <p v-if="error" class="block-box">{{ error }}</p>

          <template v-if="draft">
            <div class="diff-head">
              <b>正文改动预览</b>
              <span class="muted small"> · + 新增 / - 删除；版式与连戏锁不会动</span>
            </div>
            <pre class="diff-view"><code
              v-for="(line, i) in draft.diff"
              :key="i"
              :class="{
                add: line.startsWith('+') && !line.startsWith('+++'),
                del: line.startsWith('-') && !line.startsWith('---'),
                meta: line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++'),
              }"
            >{{ line }}</code></pre>

            <div v-for="(warn, i) in draft.warnings" :key="i" class="warn-line">
              {{ warn }}
            </div>

            <div class="refine-meta muted small">
              补丁 {{ draft.patches.length }} 条 · LLM {{ draft.usage.total_tokens || 0 }} token ·
              {{ draft.prompt_version }}
            </div>

            <div class="drawer-actions">
              <button :disabled="applying" @click="emit('apply')">
                {{ applying ? "写回中…" : "确认改动并重新算账" }}
              </button>
              <button class="ghost" :disabled="applying" @click="emit('close')">放弃</button>
            </div>
            <p class="muted small hint">
              确认后正文写回，随后点"确认并生成"才真正扣费出图。
            </p>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 本组件是独立子树：父页面的 .drawer-mask / .drawer / button 都是 scoped 样式，
   跨组件边界不会命中这里，必须自带完整定义，否则遮罩与面板都会透明、文字透出。 */
.drawer-mask.refine-mask {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  justify-content: center;
  align-items: center;
  background: rgba(0, 0, 0, 0.66);
  backdrop-filter: blur(2px);
}
.drawer.refine-drawer {
  width: min(1120px, 94vw);
  height: min(88vh, 900px);
  min-height: 560px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  padding: 22px 26px 24px;
  border-radius: 16px;
  overflow: hidden;
  background: var(--bg);
  color: var(--fg);
  box-shadow: 0 16px 56px rgba(0, 0, 0, 0.5);
}
.drawer.refine-drawer button {
  padding: 7px 15px;
  font-size: 13px;
  font-family: inherit;
  border: 1px solid transparent;
  border-radius: 8px;
  background: var(--accent);
  color: var(--accent-contrast);
  cursor: pointer;
}
.drawer.refine-drawer button:disabled { opacity: 0.45; cursor: default; }
.drawer.refine-drawer button.ghost {
  background: var(--bg);
  color: var(--accent);
  border-color: var(--accent-border);
}
.drawer-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.refine-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 24px;
  margin-top: 14px;
}
.refine-left,
.refine-right { min-height: 0; overflow-y: auto; padding-right: 4px; }
.section-title { font-size: 13px; font-weight: 600; margin: 0 0 10px; }
.source-image {
  display: block;
  width: 100%;
  max-height: 58vh;
  object-fit: contain;
  background: var(--bg-inset);
  border-radius: 12px;
}
.tips { margin: 12px 0 0; line-height: 1.7; }
.field-label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.refine-input {
  width: 100%; box-sizing: border-box; resize: vertical; min-height: 110px; padding: 10px;
  border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg-inset); color: var(--fg); font-size: 13px; font-family: inherit;
  line-height: 1.6;
}
.refine-input:focus { outline: none; border-color: var(--accent); }
.build-btn { margin-top: 10px; }
.diff-head { margin-top: 18px; font-size: 13px; }
.diff-view {
  max-height: 300px; overflow: auto; padding: 10px; margin: 8px 0;
  background: var(--bg-inset); border-radius: 8px;
  font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.diff-view code.add { color: var(--success); }
.diff-view code.del { color: var(--danger); }
.diff-view code.meta { color: var(--fg-muted); }
.refine-meta { margin: 8px 0; }
.drawer-actions { display: flex; gap: 10px; margin-top: 14px; }
.hint { margin-top: 8px; }
.block-box { color: var(--danger); }
.warn-line { color: var(--warning); font-size: 12px; margin: 4px 0; }
.muted { color: var(--fg-muted); }
.small { font-size: 12px; }

@media (max-width: 860px) {
  .drawer.refine-drawer { width: 96vw; height: 94vh; min-height: 0; }
  .refine-grid { grid-template-columns: 1fr; gap: 14px; }
  .source-image { max-height: 30vh; }
}
</style>
