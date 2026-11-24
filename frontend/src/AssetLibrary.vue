<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  approveAssetPrompt,
  getAssetDocument,
  getAssetPrompt,
  listAssetChapters,
  listAssetNovels,
  listAssetPrompts,
  mediaFileUrl,
  regenerateAssetPrompt,
  saveAssetPrompt,
  type ChapterEntry,
  type MediaImageRecord,
  type NovelAssetSummary,
  type PromptDetail,
  type PromptEntry,
  type PromptStatus,
} from "./api";
import { useAssetMedia } from "./useAssetMedia";

const props = withDefaults(defineProps<{ preferNovel?: string }>(), { preferNovel: "" });

const novels = ref<NovelAssetSummary[]>([]);
const selectedNovel = ref("");
const entries = ref<PromptEntry[]>([]);
const chapters = ref<ChapterEntry[]>([]);
const onlyUnapproved = ref(false);
const keyword = ref("");
const loading = ref(false);
const error = ref("");
const notice = ref("");

// 编辑抽屉：id → 详情（含正文与模板对照）
const openId = ref("");
const detail = ref<PromptDetail | null>(null);
const draft = ref("");
const saving = ref(false);
const showCompare = ref(false);

// 章节文档只读抽屉
const openDoc = ref<{ path: string; name: string; body: string } | null>(null);
const docLoading = ref(false);

// ---------------------------------------------------------------- 出图（S7 设定图）
// 出图状态与请求统一走共享层，和对话页资产抽屉共用同一套确认码 / 闸门逻辑。
const {
  imagesByPrompt,
  mediaPreview,
  mediaLoading,
  mediaBusy,
  mediaError,
  mediaStep,
  mediaSidecar,
  lightbox,
  imagesOf,
  currentImage,
  loadImages,
  openMedia,
  confirmMedia,
  pickImage,
  showSidecar,
  closeMedia,
} = useAssetMedia((message) => {
  notice.value = message;
});

/** 折叠状态：默认全部展开，只记被手动折叠的分类 */
const collapsed = ref<Record<string, boolean>>({});

const CATEGORY_LABELS: Record<string, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  chapter: "章节",
};
const STATUS_LABELS: Record<PromptStatus, string> = {
  draft: "模板生成",
  edited: "已编辑",
  approved: "已定稿",
};

/** 分类目录的固定顺序，与后端 asset_store.CATEGORY_DIRS + CHAPTER_DIR 一致 */
const CATEGORY_ORDER = ["character", "scene", "prop", "chapter"];

/** 树上的一行：目录名 + 该行文档 + 可选清单条目（章节行没有条目） */
interface TreeRow {
  /** 条目名目录，如「唐欣妍」「第1章」 */
  dir: string;
  /** 目录里的文件名，如「唐欣妍-图片提示词.md」「分镜.md」 */
  file: string;
  entry?: PromptEntry;
  docPath?: string;
}

/** 树上一个分类：固定中文目录名 + 其下条目 */
interface CategoryNode {
  key: string;
  /** 分类目录名，如「角色」——直接取 path 第一段，与磁盘一致 */
  folder: string;
  rows: TreeRow[];
}

/**
 * 按落盘层级拆条目：`角色/唐欣妍/唐欣妍-图片提示词.md` → 角色 › 唐欣妍 › 该文件。
 * 层级信息本来就写在清单的 `path` 里，前端只做拆解展示，不额外请求接口。
 * 章节不进清单，单独从 /chapters 接口读，拼成同样形状的第四组。
 */
const tree = computed<CategoryNode[]>(() => {
  const buckets = new Map<string, CategoryNode>();
  const kw = keyword.value.trim().toLowerCase();
  const hit = (...parts: string[]) =>
    !kw || parts.some((p) => p.toLowerCase().includes(kw));

  const list = onlyUnapproved.value
    ? entries.value.filter((e) => e.status !== "approved")
    : entries.value;
  for (const entry of list) {
    const segs = entry.path.split("/").filter(Boolean);
    const layered = segs.length >= 3;
    const folder = layered
      ? segs[0]
      : (CATEGORY_LABELS[entry.category] ?? entry.category);
    const dir = layered ? segs[1] : entry.asset_name;
    const file = layered ? segs.slice(2).join("/") : `${entry.asset_name}-图片提示词.md`;
    if (!hit(dir, file, entry.asset_name, entry.img_id)) continue;
    const node = buckets.get(entry.category) ?? {
      key: entry.category,
      folder,
      rows: [],
    };
    node.rows.push({ dir, file, entry });
    buckets.set(entry.category, node);
  }

  // 章节：一个目录一行（列出目录内所有 md），按标题名搜索
  if (!onlyUnapproved.value) {
    for (const chapter of chapters.value) {
      const names = chapter.docs.map((d) => d.name);
      if (!hit(chapter.title, ...names)) continue;
      const node = buckets.get("chapter") ?? { key: "chapter", folder: "章节", rows: [] };
      for (const doc of chapter.docs) {
        node.rows.push({ dir: chapter.title, file: doc.name, docPath: doc.path });
      }
      buckets.set("chapter", node);
    }
  }

  const rank = (key: string) => {
    const i = CATEGORY_ORDER.indexOf(key);
    return i < 0 ? CATEGORY_ORDER.length : i;
  };
  return [...buckets.values()]
    .sort((a, b) => rank(a.key) - rank(b.key))
    .map((node) => ({
      ...node,
      rows: node.rows.sort(
        (a, b) => a.dir.localeCompare(b.dir, "zh") || a.file.localeCompare(b.file, "zh"),
      ),
    }));
});

const totalShown = computed(() =>
  tree.value.reduce((sum, node) => sum + node.rows.length, 0),
);

function toggle(key: string) {
  collapsed.value = { ...collapsed.value, [key]: !collapsed.value[key] };
}

function expandAll() {
  collapsed.value = {};
}

function collapseAll() {
  const next: Record<string, boolean> = {};
  for (const node of tree.value) next[node.key] = true;
  collapsed.value = next;
}

/** 抽屉里的面包屑：分类 › 条目 › 文件 */
const detailSegments = computed(() =>
  (detail.value?.path ?? "").split("/").filter(Boolean),
);

const docSegments = computed(() => (openDoc.value?.path ?? "").split("/").filter(Boolean));

const selectedSummary = computed(() =>
  novels.value.find((n) => n.name === selectedNovel.value) ?? null,
);

async function loadNovels(prefer?: string) {
  loading.value = true;
  error.value = "";
  try {
    novels.value = await listAssetNovels();
    const target = prefer && novels.value.some((n) => n.name === prefer) ? prefer : novels.value[0]?.name;
    selectedNovel.value = target ?? "";
    if (!target) {
      entries.value = [];
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function loadPrompts() {
  if (!selectedNovel.value) return;
  loading.value = true;
  error.value = "";
  detail.value = null;
  openId.value = "";
  openDoc.value = null;
  try {
    const [promptList, chapterList] = await Promise.all([
      listAssetPrompts(selectedNovel.value),
      listAssetChapters(selectedNovel.value).catch(() => [] as ChapterEntry[]),
    ]);
    entries.value = promptList;
    chapters.value = chapterList;
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
  // 出图索引单独拉：失败也只降级为「没有图」，不影响提示词列表。
  await loadImages(selectedNovel.value);
}

watch(selectedNovel, loadPrompts);

// 组件是 v-else 懒挂载：点 tab 那一次父组件的 ref 调用发生在挂载前会落空，
// 所以组件自己挂载时必须拉一次列表，否则首次进入资产库永远是空的。
onMounted(() => loadNovels(props.preferNovel));

async function open(id: string) {
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

async function openDocFile(path: string, name: string) {
  if (!selectedNovel.value) return;
  openId.value = "";
  detail.value = null;
  openDoc.value = { path, name, body: "" };
  docLoading.value = true;
  error.value = "";
  try {
    const doc = await getAssetDocument(selectedNovel.value, path);
    openDoc.value = { path: doc.path, name: doc.name, body: doc.body };
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    docLoading.value = false;
  }
}

function close() {
  openId.value = "";
  detail.value = null;
  openDoc.value = null;
}

async function refreshList() {
  await loadNovels(selectedNovel.value);
  if (openId.value) await open(openId.value);
}

async function save() {
  if (!detail.value || !openId.value) return;
  saving.value = true;
  notice.value = "";
  try {
    detail.value = await saveAssetPrompt(openId.value, draft.value);
    await refreshList();
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
    await refreshList();
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
    await refreshList();
    notice.value = "已采用模板版本，状态回到「模板生成」";
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

async function copy(text: string, key: string) {
  try {
    await navigator.clipboard.writeText(text);
    notice.value = `已复制 ${key}`;
    setTimeout(() => {
      if (notice.value.startsWith("已复制")) notice.value = "";
    }, 1200);
  } catch {
    notice.value = "复制失败，请手动选中文本";
  }
}

/** 资产库打开出图抽屉时，先关掉章节文档抽屉，避免两个抽屉叠着显示 */
function openMediaFromLibrary(promptId: string) {
  openDoc.value = null;
  void openMedia(promptId);
}

defineExpose({ loadNovels });
</script>

<template>
  <section class="library">
    <div class="bar">
      <label for="novel-select">小说</label>
      <select id="novel-select" v-model="selectedNovel" :disabled="loading">
        <option v-for="n in novels" :key="n.name" :value="n.name">{{ n.title }}</option>
      </select>
      <input
        v-model="keyword"
        class="search"
        type="search"
        placeholder="搜索角色 / 场景 / 道具 / 章节…"
        aria-label="搜索资产"
      />
      <button class="ghost" :disabled="loading || !novels.length" @click="loadNovels(selectedNovel)">
        刷新
      </button>
      <button class="ghost" :disabled="!tree.length" @click="expandAll">全部展开</button>
      <button class="ghost" :disabled="!tree.length" @click="collapseAll">全部折叠</button>
      <label class="check">
        <input type="checkbox" v-model="onlyUnapproved" />
        仅看未定稿
      </label>
      <span v-if="selectedSummary" class="muted small">
        {{ selectedSummary.draft }} 模板 / {{ selectedSummary.edited }} 已编辑 /
        {{ selectedSummary.approved }} 已定稿<template v-if="selectedSummary.chapters">
          / {{ selectedSummary.chapters }} 章</template>
      </span>
    </div>

    <p v-if="!novels.length && !loading" class="empty">
      还没有归档资产。切到「生成」页，贴小说内容点一次生成，提示词会自动落到这里。
    </p>
    <p v-if="error" class="err">{{ error }}</p>

    <p v-if="novels.length && !tree.length && !loading" class="empty">
      <template v-if="keyword.trim()">没有匹配「{{ keyword.trim() }}」的条目。</template>
      <template v-else>当前筛选下没有条目。</template>
    </p>

    <!-- 层级与磁盘一致：分类目录 › 条目名目录 › 文档 -->
    <section v-for="node in tree" :key="node.key" class="category">
      <h2 class="block-title">
        <button
          class="fold"
          :aria-expanded="!collapsed[node.key]"
          :title="collapsed[node.key] ? '展开' : '折叠'"
          @click="toggle(node.key)"
        >
          <span class="caret" :class="{ closed: collapsed[node.key] }" aria-hidden="true">▾</span>
          <span class="folder-icon" aria-hidden="true">📁</span>
          {{ node.folder }}
          <span class="count">{{ node.rows.length }}</span>
        </button>
      </h2>
      <ul v-show="!collapsed[node.key]" class="entries">
        <li v-for="(item, i) in node.rows" :key="item.entry?.id ?? item.docPath ?? i" class="entry">
          <div v-if="item.entry" class="entry-row">
            <button
              class="entry-btn"
              :class="item.entry.status"
              :title="`打开 ${item.entry.path}`"
              @click="open(item.entry.id)"
            >
              <span class="entry-main">
                <span class="entry-dir">{{ item.dir }}/</span>
                <span class="entry-file">{{ item.file }}</span>
              </span>
              <span class="entry-meta">
                <span class="mono small">{{ item.entry.img_id }}</span>
                <span class="badge" :class="item.entry.status">
                  {{ STATUS_LABELS[item.entry.status] }}
                </span>
              </span>
            </button>
            <!-- 缩略图：已有图就显示当前选用版，点开大图；没有就显示「出图」入口 -->
            <button
              v-if="currentImage(item.entry.id)"
              class="thumb"
              :title="`查看 ${item.dir} 第 ${currentImage(item.entry.id)!.version} 版`"
              @click="lightbox = currentImage(item.entry.id)"
            >
              <img
                :src="mediaFileUrl(selectedNovel, currentImage(item.entry.id)!.path)"
                :alt="`${item.dir} 设定图`"
                loading="lazy"
              />
              <span class="thumb-badge">v{{ currentImage(item.entry.id)!.version }}</span>
            </button>
            <button
              class="ghost gen-btn"
              :disabled="mediaBusy"
              :title="imagesOf(item.entry.id).length ? '再出一版（旧版保留）' : '生成设定图'"
              @click="openMediaFromLibrary(item.entry.id)"
            >
              {{ imagesOf(item.entry.id).length ? "再出图" : "出图" }}
            </button>
          </div>
          <button
            v-else
            class="entry-btn doc"
            :title="`查看 ${item.docPath}`"
            @click="openDocFile(item.docPath ?? '', item.file)"
          >
            <span class="entry-main">
              <span class="entry-dir">{{ item.dir }}/</span>
              <span class="entry-file">{{ item.file }}</span>
            </span>
            <span class="entry-meta">
              <span class="badge doc-badge">只读</span>
            </span>
          </button>
        </li>
      </ul>
    </section>

    <p v-if="tree.length" class="muted small tree-total">
      共 {{ totalShown }} 份文档 · 路径：<code>assets/{{ selectedNovel }}/&lt;分类&gt;/&lt;条目名&gt;/</code>
    </p>

    <div v-if="openId && detail" class="drawer-mask" @click.self="close">
      <aside class="drawer">
        <div class="drawer-head">
          <div>
            <b>{{ detail.asset_name }}</b>
            <span class="muted small"> · {{ detail.img_id }}</span>
          </div>
          <button class="ghost" @click="close">关闭</button>
        </div>
        <p class="muted small path">
          <template v-for="(seg, i) in detailSegments" :key="i">
            <span :class="i < detailSegments.length - 1 ? 'seg-dir' : 'seg-file'">{{ seg }}</span
            ><span v-if="i < detailSegments.length - 1" class="sep">/</span>
          </template>
        </p>
        <p v-if="notice" class="notice">{{ notice }}</p>

        <div class="editor-head">
          <span>可复制提示词（Markdown）</span>
          <span class="actions-mini">
            <button class="ghost" :disabled="saving" @click="copy(draft, '提示词')">复制</button>
            <button
              v-if="detail.generated_body"
              class="ghost"
              @click="showCompare = !showCompare"
            >
              {{ showCompare ? "收起对照" : "看模板对照" }}
            </button>
          </span>
        </div>
        <textarea v-model="draft" class="editor" rows="18" spellcheck="false"></textarea>

        <div v-if="showCompare && detail.generated_body" class="compare">
          <p class="muted small">模板最新版本（重跑生成时因你已编辑而未覆盖主文件）：</p>
          <pre>{{ detail.generated_body }}</pre>
          <button class="ghost" :disabled="saving" @click="regenerate">采用模板版本</button>
        </div>

        <div class="drawer-actions">
          <button :disabled="saving || draft === detail.body" @click="save">保存编辑</button>
          <button
            :disabled="saving || detail.status !== 'edited'"
            @click="approve"
          >
            定稿
          </button>
        </div>
      </aside>
    </div>

    <!-- 出图确认抽屉：先看账（指纹/额度/阻塞项），点确认才提交 -->
    <div v-if="mediaPreview || mediaLoading" class="drawer-mask" @click.self="closeMedia">
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
          <!-- 阻塞项优先：现在提交必然失败，先说清楚为什么 -->
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
              <template v-if="mediaPreview.cost_estimate !== null">
                {{ mediaPreview.cost_estimate }}
              </template>
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
            <p class="muted small">
              确认码绑定当前指纹；改了提示词或任何参数，这张码立刻失效，需要重新预览。
            </p>
            <code class="confirm-code">{{ mediaPreview.confirmation }}</code>
          </div>

          <!-- 已出的版本：点一下切换当前选用版，旧图不删 -->
          <div v-if="mediaPreview.images.length" class="versions">
            <p class="editor-head">已出 {{ mediaPreview.images.length }} 版（点选切换当前版）</p>
            <div class="version-strip">
              <button
                v-for="image in mediaPreview.images"
                :key="image.id"
                class="version-item"
                :class="{ active: image.selected }"
                :title="`v${image.version} · ${image.provider} · ${image.created_at}`"
                @click="pickImage(image, selectedNovel)"
              >
                <img :src="mediaFileUrl(selectedNovel, image.path)" :alt="`v${image.version}`" />
                <span class="version-tag">v{{ image.version }}</span>
              </button>
            </div>
          </div>

          <div class="drawer-actions">
            <button
              :disabled="mediaBusy || !!mediaPreview.blocking.length"
              @click="confirmMedia(selectedNovel)"
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

    <!-- 大图：看细节与 sidecar -->
    <div v-if="lightbox" class="drawer-mask" @click.self="lightbox = null">
      <aside class="drawer">
        <div class="drawer-head">
          <div>
            <b>{{ lightbox.asset_name }}</b>
            <span class="muted small"> · v{{ lightbox.version }}</span>
          </div>
          <button class="ghost" @click="lightbox = null">关闭</button>
        </div>
        <p class="muted small path">{{ lightbox.path }}</p>
        <img class="full" :src="mediaFileUrl(selectedNovel, lightbox.path)" :alt="lightbox.asset_name" />
        <dl class="facts">
          <dt>尺寸</dt>
          <dd>
            {{ lightbox.width ?? "?" }} × {{ lightbox.height ?? "?" }} · {{ lightbox.mime }} ·
            {{ Math.round(lightbox.bytes / 1024) }} KB
          </dd>
          <dt>厂商 / 模型</dt>
          <dd>{{ lightbox.provider }} · {{ lightbox.model }}</dd>
          <dt>画幅</dt>
          <dd>{{ lightbox.size }} · {{ lightbox.ratio }}</dd>
          <dt>seed</dt>
          <dd>{{ lightbox.seed ?? "未固定" }}</dd>
          <dt>sha256</dt>
          <dd class="mono small break">{{ lightbox.sha256 }}</dd>
        </dl>
        <div class="drawer-actions">
          <button class="ghost" @click="showSidecar(lightbox)">看技术档案</button>
          <button :disabled="lightbox.selected" @click="pickImage(lightbox, selectedNovel)">
            {{ lightbox.selected ? "当前版本" : "设为当前版本" }}
          </button>
        </div>
        <pre v-if="mediaSidecar" class="readonly">{{ JSON.stringify(mediaSidecar, null, 2) }}</pre>
      </aside>
    </div>

    <!-- 章节文档：只读查看，不提供编辑（章节由生成阶段产出，不在三态管理内） -->
    <div v-if="openDoc" class="drawer-mask" @click.self="close">
      <aside class="drawer">
        <div class="drawer-head">
          <div>
            <b>{{ openDoc.name }}</b>
            <span class="muted small"> · 只读</span>
          </div>
          <button class="ghost" @click="close">关闭</button>
        </div>
        <p class="muted small path">
          <template v-for="(seg, i) in docSegments" :key="i">
            <span :class="i < docSegments.length - 1 ? 'seg-dir' : 'seg-file'">{{ seg }}</span
            ><span v-if="i < docSegments.length - 1" class="sep">/</span>
          </template>
        </p>
        <div class="editor-head">
          <span>正文（Markdown）</span>
          <span class="actions-mini">
            <button class="ghost" @click="copy(openDoc.body, '正文')">复制</button>
          </span>
        </div>
        <pre v-if="!docLoading" class="readonly">{{ openDoc.body }}</pre>
        <p v-else class="muted small">读取中…</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.library { margin-top: 18px; }
.bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.bar label { font-size: 13px; font-weight: 600; }
select {
  padding: 7px 10px; font-size: 13px; border: 1px solid var(--border);
  border-radius: 8px; font-family: inherit; background: var(--bg);
}
.search {
  padding: 7px 11px; font-size: 13px; border: 1px solid var(--border); border-radius: 8px;
  font-family: inherit; background: var(--bg); min-width: 220px; flex: 1 1 220px; max-width: 340px;
}
.search:focus { outline: none; border-color: var(--accent); }
.check { display: inline-flex; align-items: center; gap: 5px; font-weight: 400 !important; cursor: pointer; }
.small { font-size: 12px; }
.muted { color: var(--fg-muted); }
.empty { color: var(--fg-muted); font-size: 13px; line-height: 1.7; padding: 18px; background: var(--bg-subtle); border-radius: 10px; }
.err { color: var(--danger); font-size: 13px; }
.notice { color: var(--accent); background: var(--accent-soft); border-radius: 8px; padding: 8px 11px; font-size: 12px; }
.category { margin-top: 16px; }
.block-title { font-size: 15px; margin: 20px 0 8px; display: flex; align-items: baseline; gap: 8px; }
.fold {
  display: inline-flex; align-items: baseline; gap: 7px; background: none; color: inherit;
  border: none; padding: 0; font: inherit; font-size: 15px; font-weight: 700; cursor: pointer;
}
.fold:hover { color: var(--accent); }
.caret { display: inline-block; font-size: 11px; color: var(--fg-muted); transition: transform 0.12s ease; }
.caret.closed { transform: rotate(-90deg); }
.folder-icon { font-size: 14px; }
.count { font-size: 12px; color: var(--fg-muted); font-weight: 400; }
.entries { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.entry { margin: 0; }
.entry-btn {
  width: 100%; box-sizing: border-box; display: flex; align-items: center;
  justify-content: space-between; gap: 12px; padding: 9px 12px;
  background: var(--bg-subtle); color: inherit; border: 1px solid transparent;
  border-radius: 10px; text-align: left; font: inherit; cursor: pointer;
}
.entry-btn:hover { border-color: var(--accent); background: var(--bg-hover); }
.entry-btn.approved { background: var(--success-soft); }
.entry-main { display: inline-flex; align-items: baseline; gap: 0; min-width: 0; }
.entry-dir { font-size: 13.5px; font-weight: 600; color: var(--fg); }
.entry-file {
  font-size: 12px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.entry-meta { display: inline-flex; align-items: center; gap: 8px; flex: none; }
.entry-meta .mono { margin: 0; }
.tree-total { margin: 16px 0 0; }
.tree-total code { background: var(--bg-inset); border-radius: 4px; padding: 1px 5px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--fg-muted); margin: 6px 0 0; }
.badge { font-size: 11px; border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
.badge.draft { background: var(--tag-bg); color: var(--fg-secondary); }
.badge.edited { background: var(--warning-soft); color: var(--warning); }
.badge.approved { background: var(--success-soft); color: var(--success); }
.badge.doc-badge { background: var(--bg-inset); color: var(--fg-secondary); }
.entry-btn.doc { background: var(--bg-inset); }
.readonly {
  white-space: pre-wrap; font-size: 12.5px; line-height: 1.75; color: var(--fg-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--bg-inset); border-radius: 10px; padding: 14px; margin: 0;
  max-height: 72vh; overflow: auto;
}
button {
  padding: 7px 15px; font-size: 13px; border: none; border-radius: 8px;
  background: var(--accent); color: var(--accent-contrast); cursor: pointer;
}
button:disabled { opacity: 0.45; cursor: default; }
button.ghost { background: var(--bg); color: var(--accent); border: 1px solid var(--accent-border); }
.drawer-mask {
  position: absolute; inset: 0; background: var(--mask);
  display: flex; justify-content: flex-end; z-index: 20;
}
.drawer {
  width: min(680px, 92vw); height: 100%; overflow-y: auto; background: var(--bg);
  padding: 20px 22px; box-shadow: var(--drawer-shadow);
}
.drawer-head { display: flex; align-items: center; justify-content: space-between; }
.path { font-family: ui-monospace, monospace; margin: 4px 0 12px; }
.path .sep { color: var(--border-strong); margin: 0 1px; }
.path .seg-dir { color: var(--fg-secondary); }
.path .seg-file { color: var(--fg-muted); }
.editor-head {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 600; margin: 12px 0 6px;
}
.actions-mini { display: inline-flex; gap: 6px; font-weight: 400; }
.editor {
  width: 100%; box-sizing: border-box; padding: 12px; font-size: 12.5px;
  line-height: 1.7; border: 1px solid var(--border); border-radius: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; resize: vertical;
}
.compare { margin-top: 14px; background: var(--bg-inset); border-radius: 10px; padding: 12px; }
.compare pre {
  white-space: pre-wrap; font-size: 12px; line-height: 1.7; color: var(--fg-secondary);
  font-family: ui-monospace, monospace; margin: 6px 0 10px;
}
.drawer-actions { display: flex; gap: 10px; margin-top: 16px; }

/* ---- 出图：缩略图 / 版本条 / 确认抽屉 ---- */
.entry-row { display: flex; align-items: center; gap: 8px; }
.entry-row .entry-btn { flex: 1 1 auto; min-width: 0; }
.thumb {
  flex: none; width: 46px; height: 46px; padding: 0; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); overflow: hidden; position: relative;
  cursor: pointer;
}
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb:hover { border-color: var(--accent); }
.thumb-badge {
  position: absolute; right: 1px; bottom: 1px; font-size: 9px; line-height: 1;
  padding: 2px 3px; border-radius: 4px; background: var(--accent); color: var(--accent-contrast);
}
.gen-btn { flex: none; padding: 6px 11px; font-size: 12px; }
.block-box {
  background: var(--danger-soft); border-radius: 10px; padding: 11px 13px; margin: 10px 0;
  font-size: 12.5px; color: var(--danger-soft-fg);
}
.block-box ul { margin: 6px 0 0; padding-left: 18px; }
.warn-line { color: var(--warning); background: var(--warning-soft); border-radius: 8px; padding: 8px 11px; font-size: 12px; margin: 8px 0; }
.cache-box { background: var(--accent-soft); border-radius: 8px; padding: 9px 12px; font-size: 12.5px; margin: 8px 0; }
.facts { display: grid; grid-template-columns: 92px 1fr; gap: 6px 10px; font-size: 12.5px; margin: 14px 0; }
.facts dt { color: var(--fg-muted); }
.facts dd { margin: 0; word-break: break-word; }
.facts .on { color: var(--danger-soft-fg); font-weight: 600; }
.facts .off { color: var(--success); }
.confirm-box { background: var(--bg-subtle); border-radius: 10px; padding: 11px 13px; }
.confirm-box p { margin: 0 0 7px; }
.confirm-code {
  display: block; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px; background: var(--bg); border: 1px solid var(--border); border-radius: 7px;
  padding: 7px 9px; word-break: break-all; user-select: all;
}
.versions { margin-top: 16px; }
.version-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.version-item {
  flex: none; width: 82px; height: 82px; padding: 0; border-radius: 9px; overflow: hidden;
  border: 2px solid transparent; background: var(--bg-inset); position: relative; cursor: pointer;
}
.version-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.version-item.active { border-color: var(--accent); }
.version-tag {
  position: absolute; left: 3px; bottom: 3px; font-size: 10px; padding: 1px 4px;
  border-radius: 4px; background: rgba(0, 0, 0, 0.55); color: #fff;
}
img.full {
  width: 100%; border-radius: 10px; background: var(--bg-inset); display: block; margin: 8px 0 4px;
}
.mono.small.break { word-break: break-all; font-size: 11px; }
</style>
