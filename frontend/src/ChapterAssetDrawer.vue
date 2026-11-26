<script setup lang="ts">
/**
 * 对话页右侧「本章资产」抽屉：展示本次生成结果里当前章节涉及的归档资产，
 * 出图 / 选版 / 编辑 / 定稿都在对话页闭环；资产库仍负责小说级整体把控。
 * 资产是小说级合并的（角色/道具/场景无章节维度），所以按本次结果的资产名匹配清单。
 */
import { computed, ref, watch } from "vue";
import {
  approveAssetPrompt,
  getAssetDocument,
  getAssetPrompt,
  listAssetChapters,
  listAssetPrompts,
  mediaFileUrl,
  regenerateAssetPrompt,
  saveAssetPrompt,
  type ChapterEntry,
  type MediaImageRecord,
  type NovelGenerateResponse,
  type PromptDetail,
  type PromptEntry,
  type PromptStatus,
} from "./api";
import { useAssetMedia } from "./useAssetMedia";
import RefineDrawer from "./RefineDrawer.vue";

const props = defineProps<{
  open: boolean;
  novel: string;
  result: NovelGenerateResponse;
  chapterIndex: number;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "update:chapterIndex", value: number): void;
  (e: "open-library"): void;
}>();

const entries = ref<PromptEntry[]>([]);
const chapters = ref<ChapterEntry[]>([]);
const loading = ref(false);
const error = ref("");
const notice = ref("");

const current = computed(() => props.result.chapters[props.chapterIndex] ?? null);

const {
  imagesByPrompt,
  mediaPreview,
  mediaLoading,
  mediaBusy,
  mediaError,
  mediaStep,
  mediaSidecar,
  lightbox,
  refineSource,
  refineAddition,
  refineDraft,
  refineLoading,
  refineApplying,
  imagesOf,
  currentImage,
  loadImages,
  openMedia,
  confirmMedia,
  startRefine,
  buildRefineDraft,
  applyRefine,
  closeRefine,
  pickImage,
  showSidecar,
  closeMedia,
} = useAssetMedia((message) => {
  notice.value = message;
});

// 编辑 / 只读抽屉
const openId = ref("");
const detail = ref<PromptDetail | null>(null);
const draft = ref("");
const saving = ref(false);
const showCompare = ref(false);
const openDoc = ref<{ path: string; name: string; body: string } | null>(null);
const docLoading = ref(false);

const STATUS_LABELS: Record<PromptStatus, string> = {
  draft: "模板生成",
  edited: "已编辑",
  approved: "已定稿",
};
const CATEGORY_LABELS: Record<string, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
};
const CATEGORY_ORDER = ["character", "scene", "prop"];

/** 与后端 archive._clean / _safe_name 同口径：去尾部标点与非法字符，便于按名字对齐 */
function normalizeName(value: string): string {
  return value
    .replace(/[<>:"\/\\|?*\x00-\x1f]/g, "_")
    .replace(/[.,;:!?，。；：！？、]+$/g, "")
    .trim();
}

/** 与后端 archive._chapter_dir_name 同口径：章节标题 → 目录主名 */
const HEADING_MAIN =
  /第[零〇一二三四五六七八九十百千万两0-9]+[章节回部篇集]|序章|序幕|楔子|引子|终章|尾声|后记|番外/;
function chapterDirName(title: string): string {
  const match = HEADING_MAIN.exec(title || "");
  const raw = match ? match[0] : (title || "").trim();
  return normalizeName(raw).slice(0, 80) || "未命名章节";
}

/** 本章结果里的资产名（按类别分组），同名跨章合并后清单里只有一条 */
const namesByCategory = computed<Record<string, Set<string>>>(() => {
  const ch = current.value;
  const groups: Record<string, Set<string>> = {
    character: new Set(),
    scene: new Set(),
    prop: new Set(),
  };
  if (!ch) return groups;
  for (const c of ch.characters) groups.character.add(normalizeName(c.name));
  for (const s of ch.scenes) groups.scene.add(normalizeName(s.name));
  for (const p of ch.props) groups.prop.add(normalizeName(p.name));
  return groups;
});

/** 清单里属于本章的资产条目，按类别分组（保持固定顺序） */
const assetGroups = computed(() => {
  const buckets = new Map<string, PromptEntry[]>();
  for (const entry of entries.value) {
    const names = namesByCategory.value[entry.category];
    if (!names?.has(normalizeName(entry.asset_name))) continue;
    const list = buckets.get(entry.category) ?? [];
    list.push(entry);
    buckets.set(entry.category, list);
  }
  return CATEGORY_ORDER.map((key) => ({
    key,
    label: CATEGORY_LABELS[key],
    rows: (buckets.get(key) ?? []).sort((a, b) =>
      a.asset_name.localeCompare(b.asset_name, "zh"),
    ),
  })).filter((group) => group.rows.length);
});

const assetCount = computed(() =>
  assetGroups.value.reduce((sum, group) => sum + group.rows.length, 0),
);

/** 本章章节文档（只读）：目录名与结果标题的主名对齐，兼容撞名加序号 */
const docRows = computed(() => {
  const ch = current.value;
  if (!ch) return [];
  const dir = chapterDirName(ch.title);
  const found = chapters.value.find(
    (entry) => entry.title === dir || entry.title.startsWith(`${dir}-`),
  );
  return found?.docs ?? [];
});

watch(
  () => [props.open, props.novel] as const,
  ([open, novel]) => {
    if (open && novel) void loadAll();
  },
  { immediate: true },
);

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [promptList, chapterList] = await Promise.all([
      listAssetPrompts(props.novel),
      listAssetChapters(props.novel).catch(() => [] as ChapterEntry[]),
    ]);
    entries.value = promptList;
    chapters.value = chapterList;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
  await loadImages(props.novel);
}

function close() {
  if (mediaBusy.value) return;
  openId.value = "";
  openDoc.value = null;
  emit("close");
}

function switchChapter(index: number) {
  emit("update:chapterIndex", index);
}

function closeEditor() {
  openId.value = "";
  detail.value = null;
  notice.value = "";
}

async function openEditor(id: string) {
  openId.value = id;
  detail.value = null;
  draft.value = "";
  showCompare.value = false;
  notice.value = "";
  loading.value = true;
  try {
    detail.value = await getAssetPrompt(id);
    draft.value = detail.value.body;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function refreshAfterEdit() {
  await loadAll();
  if (openId.value) {
    detail.value = await getAssetPrompt(openId.value);
    draft.value = detail.value.body;
  }
}

async function save() {
  if (!detail.value || !openId.value) return;
  saving.value = true;
  notice.value = "";
  try {
    detail.value = await saveAssetPrompt(openId.value, draft.value);
    await refreshAfterEdit();
    notice.value = "已保存为「已编辑」，重跑生成不会再覆盖这条";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

async function approve() {
  if (!openId.value) return;
  saving.value = true;
  try {
    await approveAssetPrompt(openId.value);
    await refreshAfterEdit();
    notice.value = "已定稿，后续生成不会改动这条";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

async function regenerate() {
  if (!openId.value) return;
  saving.value = true;
  try {
    await regenerateAssetPrompt(openId.value);
    await refreshAfterEdit();
    notice.value = "已采用模板版本，状态回到「模板生成」";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

async function openDocFile(path: string, name: string) {
  openId.value = "";
  openDoc.value = { path, name, body: "" };
  docLoading.value = true;
  try {
    const doc = await getAssetDocument(props.novel, path);
    openDoc.value = { path: doc.path, name: doc.name, body: doc.body };
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    docLoading.value = false;
  }
}
</script>

<template>
  <div v-if="open" class="drawer-mask main-mask" @click.self="close">
    <aside class="drawer main-drawer">
      <div class="drawer-head">
        <div>
          <b>本章资产</b>
          <span class="muted small"> · {{ novel }}</span>
        </div>
        <button class="ghost" @click="close">关闭</button>
      </div>

      <div v-if="result.chapters.length > 1" class="chapter-tabs">
        <button
          v-for="(ch, i) in result.chapters"
          :key="ch.index"
          class="chapter-tab"
          :class="{ active: i === chapterIndex }"
          @click="switchChapter(i)"
        >
          {{ ch.index }}. {{ ch.title }}
        </button>
      </div>
      <p v-else-if="current" class="muted small chapter-label">{{ current.title }}</p>

      <p class="tip">
        资产按小说级归档、同名跨章合并；这里只列本章涉及的条目。全局把控请到资产库。
        <button class="link" @click="emit('open-library')">去资产库</button>
      </p>
      <p v-if="loading" class="muted small">正在读取归档清单…</p>
      <p v-if="error" class="err">{{ error }}</p>
      <p v-if="notice" class="notice">{{ notice }}</p>

      <template v-if="current">
        <h3 class="section-title">可出图资产 <span class="count">{{ assetCount }}</span></h3>
        <p v-if="!loading && !assetCount" class="empty">
          本章没有匹配的归档资产。确认生成时归档阶段是否成功，或到资产库查看整体。
        </p>
        <div v-for="group in assetGroups" :key="group.key" class="group">
          <h4 class="group-title">{{ group.label }} <span class="count">{{ group.rows.length }}</span></h4>
          <div v-for="entry in group.rows" :key="entry.id" class="asset-row">
            <button
              v-if="currentImage(entry.id)"
              class="thumb"
              :title="`查看 ${entry.asset_name} 第 ${currentImage(entry.id)!.version} 版`"
              @click="lightbox = currentImage(entry.id)"
            >
              <img
                :src="mediaFileUrl(novel, currentImage(entry.id)!.path)"
                :alt="`${entry.asset_name} 设定图`"
                loading="lazy"
              />
              <span class="thumb-badge">v{{ currentImage(entry.id)!.version }}</span>
            </button>
            <div v-else class="thumb placeholder" aria-hidden="true">图</div>
            <div class="asset-main">
              <div class="asset-name">
                <b>{{ entry.asset_name }}</b>
                <span class="mono small">{{ entry.img_id }}</span>
                <span class="badge" :class="entry.status">{{ STATUS_LABELS[entry.status] }}</span>
              </div>
              <p class="muted small mono path-line">{{ entry.path }}</p>
            </div>
            <div class="asset-actions">
              <button class="ghost small-btn" @click="openEditor(entry.id)">编辑</button>
              <button
                class="ghost small-btn"
                :disabled="mediaBusy"
                :title="imagesOf(entry.id).length ? '再出一版（旧版保留）' : '生成设定图'"
                @click="openMedia(entry.id)"
              >
                {{ imagesOf(entry.id).length ? "再出图" : "出图" }}
              </button>
            </div>
          </div>
        </div>

        <h3 class="section-title">章节文档 <span class="count">{{ docRows.length }}</span></h3>
        <p v-if="!docRows.length" class="muted small">本章暂无归档文档。</p>
        <div v-for="doc in docRows" :key="doc.path" class="doc-row">
          <button class="entry-btn" :title="doc.path" @click="openDocFile(doc.path, doc.name)">
            <span>{{ doc.name }}</span>
            <span class="muted small">只读 · {{ Math.round(doc.size / 1024) }} KB</span>
          </button>
        </div>
      </template>
    </aside>
  </div>

  <!-- 出图确认抽屉：算账不花钱，确认才提交（与资产库共用 useAssetMedia） -->
  <div v-if="mediaPreview || mediaLoading" class="drawer-mask confirm-mask" @click.self="closeMedia">
    <aside class="drawer">
      <div class="drawer-head">
        <div>
          <b>{{ mediaPreview?.title || "生成设定图" }}</b>
          <span class="muted small"> · {{ mediaPreview?.img_id }}</span>
        </div>
        <button class="ghost" :disabled="mediaBusy" @click="closeMedia">关闭</button>
      </div>
      <p v-if="mediaLoading" class="muted small">正在算账…</p>
      <p v-if="mediaError" class="err">{{ mediaError }}</p>
      <p v-if="mediaStep" class="notice">{{ mediaStep }}</p>
      <template v-if="mediaPreview">
        <div v-if="mediaPreview.blocking.length" class="block-box">
          <b>现在不能提交：</b>
          <ul>
            <li v-for="(reason, i) in mediaPreview.blocking" :key="i">{{ reason }}</li>
          </ul>
        </div>
        <p v-for="(warn, i) in mediaPreview.warnings" :key="`w${i}`" class="warn-line">
          ⚠ {{ warn }}
        </p>
        <div v-if="mediaPreview.cached" class="cache-box">
          同参数已有成品（v{{ mediaPreview.cached.version }}），确认后不会重复扣费。
        </div>
        <p v-if="mediaPreview.prompt_stale" class="warn-line">
          ⚠ 提示词在上次出图后改过，当前图不代表最新提示词。
        </p>
        <dl class="facts">
          <dt>厂商 / 模型</dt>
          <dd>{{ mediaPreview.provider }} · {{ mediaPreview.model || "（默认）" }}</dd>
          <dt>画幅</dt>
          <dd>{{ mediaPreview.size }} · {{ mediaPreview.ratio }} · 1 张</dd>
          <dt>提示词</dt>
          <dd>{{ mediaPreview.prompt_chars }} 字</dd>
          <dt>预估成本</dt>
          <dd>
            <template v-if="mediaPreview.cost_estimate !== null">{{ mediaPreview.cost_estimate }}</template>
            <template v-else>未配置（MEDIA_COST_PER_IMAGE）</template>
          </dd>
          <dt>今日额度</dt>
          <dd>
            <template v-if="mediaPreview.quota.quota">
              已提交 {{ mediaPreview.quota.submitted }} / {{ mediaPreview.quota.quota }}
            </template>
            <template v-else>不限（已提交 {{ mediaPreview.quota.submitted }} 次）</template>
          </dd>
          <dt>付费开关</dt>
          <dd>
            <span :class="mediaPreview.allow_paid ? 'on' : 'off'">
              {{ mediaPreview.allow_paid ? "已开启（会真实计费）" : "已关闭（只允许离线 fixture）" }}
            </span>
          </dd>
        </dl>
        <div class="confirm-box">
          <p class="muted small">确认码绑定当前指纹；改了提示词或任何参数就会失效。</p>
          <code class="confirm-code">{{ mediaPreview.confirmation }}</code>
        </div>
        <div v-if="mediaPreview.images.length" class="versions">
          <p class="group-title">已出 {{ mediaPreview.images.length }} 版（点选切换当前版）</p>
          <div class="version-strip">
            <div v-for="image in mediaPreview.images" :key="image.id" class="version-cell">
              <button
                class="version-item"
                :class="{ active: image.selected }"
                :title="`v${image.version} · ${image.provider} · ${image.created_at}`"
                @click="pickImage(image, novel)"
              >
                <img :src="mediaFileUrl(novel, image.path)" :alt="`v${image.version}`" />
                <span class="version-tag">v{{ image.version }}</span>
                <span v-if="image.selected" class="picked">✓</span>
              </button>
              <button class="refine-btn" @click="startRefine(image)">迭代</button>
            </div>
          </div>
        </div>
        <div class="drawer-actions">
          <button
            :disabled="mediaBusy || !!mediaPreview.blocking.length"
            @click="confirmMedia(novel)"
          >
            {{ mediaBusy ? "生成中…" : mediaPreview.cached ? "确认（复用已有）" : "确认并生成" }}
          </button>
          <button class="ghost" :disabled="mediaBusy" @click="openMedia(mediaPreview.prompt_id)">
            重新预览
          </button>
        </div>
      </template>
    </aside>
  </div>

  <!-- 大图与 sidecar -->
  <div v-if="lightbox" class="drawer-mask lightbox-mask" @click.self="lightbox = null">
    <aside class="drawer">
      <div class="drawer-head">
        <div><b>{{ lightbox.asset_name }}</b><span class="muted small"> · v{{ lightbox.version }}</span></div>
        <button class="ghost" @click="lightbox = null">关闭</button>
      </div>
      <p class="muted small path">{{ lightbox.path }}</p>
      <img class="full" :src="mediaFileUrl(novel, lightbox.path)" :alt="lightbox.asset_name" />
      <dl class="facts">
        <dt>尺寸</dt>
        <dd>{{ lightbox.width ?? "?" }} × {{ lightbox.height ?? "?" }} · {{ lightbox.mime }}</dd>
        <dt>厂商 / 模型</dt>
        <dd>{{ lightbox.provider }} · {{ lightbox.model }}</dd>
        <dt>seed</dt>
        <dd>{{ lightbox.seed ?? "未固定" }}</dd>
        <dt>sha256</dt>
        <dd class="mono small break">{{ lightbox.sha256 }}</dd>
      </dl>
      <div class="drawer-actions">
        <button class="ghost" @click="showSidecar(lightbox)">看技术档案</button>
        <button :disabled="lightbox.selected" @click="pickImage(lightbox, novel)">
          {{ lightbox.selected ? "当前版本" : "设为当前版本" }}
        </button>
      </div>
      <pre v-if="mediaSidecar" class="readonly">{{ JSON.stringify(mediaSidecar, null, 2) }}</pre>
    </aside>
  </div>

  <!-- 提示词编辑 / 定稿 -->
  <div v-if="openId && detail" class="drawer-mask editor-mask" @click.self="closeEditor">
    <aside class="drawer">
      <div class="drawer-head">
        <div><b>{{ detail.asset_name }}</b><span class="muted small"> · {{ detail.img_id }}</span></div>
        <button class="ghost" @click="closeEditor">关闭</button>
      </div>
      <p v-if="notice" class="notice">{{ notice }}</p>
      <div class="editor-head">
        <span>可复制提示词（Markdown）</span>
        <button
          v-if="detail.generated_body"
          class="link"
          @click="showCompare = !showCompare"
        >
          {{ showCompare ? "收起对照" : "看模板对照" }}
        </button>
      </div>
      <textarea v-model="draft" class="editor" rows="16" spellcheck="false"></textarea>
      <div v-if="showCompare && detail.generated_body" class="compare">
        <p class="muted small">模板最新版本（因你已编辑而未覆盖主文件）：</p>
        <pre>{{ detail.generated_body }}</pre>
        <button class="ghost" :disabled="saving" @click="regenerate">采用模板版本</button>
      </div>
      <div class="drawer-actions">
        <button :disabled="saving || draft === detail.body" @click="save">保存编辑</button>
        <button :disabled="saving || detail.status !== 'edited'" @click="approve">定稿</button>
      </div>
    </aside>
  </div>

  <!-- 章节文档只读 -->
  <div v-if="openDoc" class="drawer-mask doc-mask" @click.self="openDoc = null">
    <aside class="drawer">
      <div class="drawer-head">
        <div><b>{{ openDoc.name }}</b><span class="muted small"> · 只读</span></div>
        <button class="ghost" @click="openDoc = null">关闭</button>
      </div>
      <p class="muted small path">{{ openDoc.path }}</p>
      <pre v-if="!docLoading" class="readonly">{{ openDoc.body }}</pre>
      <p v-else class="muted small">读取中…</p>
    </aside>
  </div>

  <!-- 图生图迭代：以已出图为参考，追加描述融合进正文后重出 -->
  <RefineDrawer
    :novel="novel"
    :source="refineSource"
    :addition="refineAddition"
    :draft="refineDraft"
    :loading="refineLoading"
    :applying="refineApplying"
    :error="mediaError"
    @close="closeRefine"
    @update:addition="refineAddition = $event"
    @build="buildRefineDraft"
    @apply="applyRefine"
  />
</template>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: var(--mask);
  display: flex;
  justify-content: flex-end;
}
.main-mask { z-index: 40; }
.confirm-mask { z-index: 50; }
.lightbox-mask { z-index: 60; }
.editor-mask { z-index: 50; }
.doc-mask { z-index: 50; }
.drawer {
  width: min(620px, 94vw);
  height: 100%;
  overflow-y: auto;
  background: var(--bg);
  color: var(--fg);
  padding: 20px 22px 32px;
  box-shadow: var(--drawer-shadow);
  box-sizing: border-box;
}
.main-drawer { width: min(680px, 94vw); }
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
button {
  padding: 7px 15px;
  font-size: 13px;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: var(--accent-contrast);
  cursor: pointer;
}
button:disabled { opacity: 0.45; cursor: default; }
button.ghost {
  background: var(--bg);
  color: var(--accent);
  border: 1px solid var(--accent-border);
}
button.link {
  background: transparent;
  color: var(--accent);
  padding: 0 2px;
  text-decoration: underline;
  font-size: 12px;
}
.chapter-tabs {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 14px 0 2px;
}
.chapter-tab {
  padding: 5px 11px;
  font-size: 12px;
  background: var(--bg);
  color: var(--accent);
  border: 1px solid var(--accent-border);
  border-radius: 999px;
}
.chapter-tab.active {
  background: var(--accent);
  color: var(--accent-contrast);
}
.chapter-label { margin: 14px 0 0; }
.tip {
  font-size: 12px;
  color: var(--fg-muted);
  background: var(--bg-subtle);
  border-radius: 8px;
  padding: 8px 11px;
  line-height: 1.6;
  margin: 12px 0;
}
.err { color: var(--danger); font-size: 13px; }
.notice {
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 8px;
  padding: 8px 11px;
  font-size: 12px;
}
.section-title { font-size: 14px; margin: 20px 0 8px; }
.group-title { font-size: 13px; margin: 14px 0 6px; font-weight: 600; }
.count { font-size: 11px; color: var(--fg-muted); font-weight: 400; }
.group { margin-bottom: 6px; }
.asset-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  background: var(--bg-subtle);
  border: 1px solid transparent;
  border-radius: 10px;
  margin-bottom: 6px;
}
.asset-row:hover { border-color: var(--accent-border); }
.thumb {
  flex: none;
  width: 46px;
  height: 46px;
  padding: 0;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg);
  overflow: hidden;
  position: relative;
}
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--fg-muted);
  font-size: 15px;
  background: var(--bg-inset);
  cursor: default;
}
.thumb-badge {
  position: absolute;
  right: 1px;
  bottom: 1px;
  font-size: 9px;
  line-height: 1;
  padding: 2px 3px;
  border-radius: 4px;
  background: var(--accent);
  color: var(--accent-contrast);
}
.asset-main { flex: 1; min-width: 0; }
.asset-name { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.asset-name b { font-size: 13px; }
.badge { font-size: 11px; border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
.badge.draft { background: var(--tag-bg); color: var(--fg-secondary); }
.badge.edited { background: var(--warning-soft); color: var(--warning); }
.badge.approved { background: var(--success-soft); color: var(--success); }
.path-line { margin: 3px 0 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset-actions { display: flex; gap: 6px; flex: none; }
.small-btn { padding: 5px 9px; font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.small { font-size: 12px; }
.muted { color: var(--fg-muted); }
.empty, .block-box, .cache-box, .warn-line, .confirm-box, .compare {
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 12.5px;
  line-height: 1.6;
}
.empty { color: var(--fg-muted); background: var(--bg-subtle); }
.block-box { background: var(--danger-soft); color: var(--danger-soft-fg); margin: 10px 0; }
.block-box ul { margin: 6px 0 0; padding-left: 18px; }
.warn-line { background: var(--warning-soft); color: var(--warning); margin: 8px 0; }
.cache-box { background: var(--accent-soft); margin: 8px 0; }
.confirm-box { background: var(--bg-subtle); }
.confirm-box p { margin: 0 0 7px; }
.confirm-code {
  display: block;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 7px 9px;
  word-break: break-all;
  user-select: all;
}
.facts { display: grid; grid-template-columns: 92px 1fr; gap: 6px 10px; font-size: 12.5px; margin: 14px 0; }
.facts dt { color: var(--fg-muted); }
.facts dd { margin: 0; word-break: break-word; }
.facts .on { color: var(--danger-soft-fg); font-weight: 600; }
.facts .off { color: var(--success); }
.version-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.version-cell { flex: none; display: flex; flex-direction: column; gap: 4px; align-items: center; }
.refine-btn { padding: 2px 8px; font-size: 11px; line-height: 1.4; }
.version-item {
  flex: none;
  width: 82px;
  height: 82px;
  padding: 0;
  border-radius: 9px;
  overflow: hidden;
  border: 2px solid transparent;
  background: var(--bg-inset);
  position: relative;
}
.version-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.version-item.active { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.version-item:hover { border-color: var(--accent); }
.picked {
  position: absolute; right: 3px; top: 3px; width: 17px; height: 17px; line-height: 17px;
  text-align: center; border-radius: 50%; font-size: 11px; font-weight: 700;
  background: var(--accent); color: var(--accent-contrast);
}
.version-tag {
  position: absolute;
  left: 3px;
  bottom: 3px;
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
}
.drawer-actions { display: flex; gap: 10px; margin-top: 16px; }
.path { font-family: ui-monospace, monospace; margin: 4px 0 12px; word-break: break-all; }
img.full { width: 100%; border-radius: 10px; background: var(--bg-inset); display: block; margin: 8px 0 4px; }
.readonly {
  white-space: pre-wrap;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--fg-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--bg-inset);
  border-radius: 10px;
  padding: 14px;
  margin: 0;
  max-height: 72vh;
  overflow: auto;
}
.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  margin: 12px 0 6px;
}
.editor {
  width: 100%;
  box-sizing: border-box;
  padding: 12px;
  font-size: 12.5px;
  line-height: 1.7;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  resize: vertical;
  background: var(--bg);
  color: var(--fg);
}
.compare { margin-top: 14px; background: var(--bg-inset); }
.compare pre {
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.7;
  color: var(--fg-secondary);
  font-family: ui-monospace, monospace;
  margin: 6px 0 10px;
}
.doc-row { margin-bottom: 6px; }
.entry-btn {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  background: var(--bg-subtle);
  color: var(--fg);
  border: 1px solid transparent;
  border-radius: 10px;
  text-align: left;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.entry-btn:hover { border-color: var(--accent-border); }
.mono.small.break { word-break: break-all; font-size: 11px; }
</style>
