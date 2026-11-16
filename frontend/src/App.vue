<script setup lang="ts">
import { ref } from "vue";
import { extractAssets, type ExtractAssetsResponse } from "./api";

const chapter = ref(
  "林桂芳推开老饭馆的木门，铜钥匙在掌心攥得发烫。傍晚的暖黄顶灯下，沈砚舟正低头擦着一只旧怀表。"
);
const loading = ref(false);
const error = ref("");
const result = ref<ExtractAssetsResponse | null>(null);

async function run() {
  loading.value = true;
  error.value = "";
  result.value = null;
  try {
    result.value = await extractAssets(chapter.value);
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="wrap">
    <h1>ai_skit · 资产盘点（最小闭环）</h1>
    <p class="hint">
      输入小说章节，后端调 LLM 抽取角色 / 道具 / 场景。本阶段是 agent 最底下那一层：一次 LLM 调用 + 结构化输出。
    </p>

    <textarea v-model="chapter" rows="6"></textarea>
    <button :disabled="loading" @click="run">{{ loading ? "抽取中…" : "抽取资产" }}</button>

    <p v-if="error" class="err">{{ error }}</p>

    <section v-if="result">
      <p class="meta">
        run_id: {{ result.run_id }} · model: {{ result.model }} ·
        {{ result.mock ? "mock" : "真实调用" }} · {{ result.latency_ms }}ms
      </p>

      <div class="grid">
        <div class="card">
          <h2>角色（{{ result.assets.characters.length }}）</h2>
          <div v-for="c in result.assets.characters" :key="c.name" class="item">
            <b>{{ c.name }}</b>
            <p>{{ c.appearance }}</p>
            <p class="muted">稳定特征：{{ c.stable_features }}</p>
          </div>
        </div>
        <div class="card">
          <h2>道具（{{ result.assets.props.length }}）</h2>
          <div v-for="p in result.assets.props" :key="p.name" class="item">
            <b>{{ p.name }}</b>
            <p>{{ p.appearance }}</p>
            <p v-if="p.state_changes" class="muted">状态变化：{{ p.state_changes }}</p>
          </div>
        </div>
        <div class="card">
          <h2>场景（{{ result.assets.scenes.length }}）</h2>
          <div v-for="s in result.assets.scenes" :key="s.name" class="item">
            <b>{{ s.name }}</b>
            <p>{{ s.time }} · {{ s.light }}</p>
            <p class="muted">{{ s.atmosphere }}</p>
          </div>
        </div>
      </div>
    </section>
  </main>
</template>

<style scoped>
.wrap {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
  font-family: system-ui, sans-serif;
}
h1 {
  font-size: 20px;
  margin: 0 0 4px;
}
.hint {
  color: #888;
  font-size: 13px;
  margin: 0 0 16px;
}
textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 12px;
  font-size: 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
}
button {
  margin: 12px 0;
  padding: 8px 20px;
  font-size: 14px;
  border: none;
  border-radius: 8px;
  background: #0c447c;
  color: #fff;
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
.err {
  color: #c33;
}
.meta {
  font-size: 12px;
  color: #888;
}
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 12px;
}
.card {
  background: #f7f7f5;
  border-radius: 10px;
  padding: 14px;
}
.card h2 {
  font-size: 14px;
  margin: 0 0 8px;
}
.item {
  border-top: 1px solid #eee;
  padding: 8px 0;
}
.item b {
  font-size: 14px;
}
.item p {
  margin: 2px 0;
  font-size: 13px;
}
.muted {
  color: #888;
}
</style>
