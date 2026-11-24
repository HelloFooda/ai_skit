/** 五类文生文产出，字段口径与后端 schemas.py 一一对应 */

export interface Character {
  name: string;
  appearance: string;
  /** 跨场不变的「外观特征」（疤痕/痣/配饰/身形） */
  stable_features: string;
  /** 「本集状态」：换装、伤污、淋湿、持物等阶段变化；无变化为空 */
  state?: string;
  /** 声线依据：音域质地、语速、语气；无台词为空 */
  voice: string;
  /** 资产描述：供下游写图片提示词时引用；不含版式/画幅/景别（那些属 S3 阶段） */
  prompt: string;
}

export interface Prop {
  name: string;
  /** 归属角色名；环境陈设留空 —— 这就是「角色道具」的判据 */
  owner: string;
  appearance: string;
  state_changes: string;
  prompt: string;
}

export interface Scene {
  name: string;
  time: string;
  light: string;
  atmosphere: string;
  prompt: string;
}

export interface Shot {
  shot_no: number;
  duration: number;
  shot_size: string;
  angle: string;
  camera: string;
  function: string;
  content: string;
  dialogue: string;
  /** 谁｜画面位置｜朝向｜机位在轴线哪一侧 */
  blocking: string;
}

/** 一个 15s 组；一章通常切多组 */
export interface StoryboardGroup {
  group_id: string;
  duration: number;
  location: string;
  summary: string;
  shots: Shot[];
}

/** 站位：把分镜表的站位列按组聚合，与 shots[].blocking 永远一致 */
export interface Blocking {
  group_id: string;
  location: string;
  text: string;
}

export interface GateIssue {
  code: string;
  group_id: string;
  detail: string;
}

/** auto 出全五类（2 次调用）；assets_only 只出资产（1 次）；per_type 三类并发（3 次） */
export type ExtractMode = "auto" | "assets_only" | "per_type" | "visual_only";

/** 技能菜单条目（只有元数据，不含正文） */
export interface SkillInfo {
  name: string;
  description: string;
  /** 显式声明的类别；未声明时为 ["manual"]，只能按名字手动选择 */
  categories: string[];
  version: string;
  license: string;
  display_name: string;
  body_chars: number;
  /** 正文引用的参考文件，装载时会被内联展开 */
  refs: string[];
  /** 非致命提示，如 name 与目录名不一致 */
  notes: string[];
}

export interface SkillCatalogResponse {
  skills: SkillInfo[];
  /** 格式不合法、已被后端跳过的技能文件 */
  errors: string[];
}

/** 主路径只需要 chapter，其余全是可选 */
export interface GenerateRequest {
  chapter: string;
  character_hint?: string;
  prop_hint?: string;
  scene_hint?: string;
  mode?: ExtractMode;
  /** 单组目标时长（秒），默认 15 */
  target_duration?: number;
  /** 本次最多出几组，超出的标记 truncated */
  max_groups?: number;
  /** 留空 = 按声明类别自动装载；点名 = 只装这些 */
  skills?: string[];
  /** 仅 mode="visual_only" 时需要：把上次返回的三类资产原样带回来，只重跑分镜 */
  assets?: { characters: Character[]; props: Prop[]; scenes: Scene[] };
  /** 只拆这些场次（逐字取场景名），长章按场分片用；空 = 整章 */
  scene_filter?: string[];
}

export interface GenerateResponse {
  run_id: string;
  model: string;
  mock: boolean;

  characters: Character[];
  props: Prop[];
  scenes: Scene[];
  blockings: Blocking[];
  storyboards: StoryboardGroup[];

  latency_ms: number;
  prompt_version: string;
  mode: ExtractMode;
  llm_calls: number;
  /** agent 编排自己发起的模型调用（选工具 / 收尾），与生成的 llm_calls 分开计 */
  agent_calls: number;
  hints_used: string[];
  injection_warning: boolean;
  skills_loaded: string[];
  skills_skipped: string[];
  /** 已内联展开 / 因预算未展开的参考文件数量 */
  skills_refs_expanded: number;
  skills_refs_skipped: number;

  /** 每段的耗时与产出条数 */
  stages: Record<string, unknown>[];
  /** 机检结果（S4 硬规则的代码版），首版只提示不阻断 */
  gate_issues: GateIssue[];
  total_groups_estimated: number;
  truncated: boolean;
}

/** 资产库：清单条目与三态状态 */
export type PromptStatus = "draft" | "edited" | "approved";

export interface PromptEntry {
  id: string;
  img_id: string;
  category: string;
  asset_name: string;
  path: string;
  status: PromptStatus;
  body_hash: string;
  updated_at: string;
}

export interface PromptDetail extends PromptEntry {
  body: string;
  generated_body: string;
}

export interface NovelAssetSummary {
  name: string;
  title: string;
  updated_at: string;
  entries: number;
  draft: number;
  edited: number;
  approved: number;
  /** 章节目录数（章节不进清单，按磁盘统计） */
  chapters?: number;
}

/** 章节目录下的一份文档：角色站位.md / 分镜.md */
export interface ChapterDoc {
  name: string;
  path: string;
  size: number;
  updated_at: string;
}

/** 一个章节目录：标题名 + 其下文档 */
export interface ChapterEntry {
  title: string;
  path: string;
  docs: ChapterDoc[];
}

/** 任意归档 Markdown 的正文（章节文档只读） */
export interface AssetDocument {
  path: string;
  name: string;
  body: string;
}

async function assetFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }
  return res.json();
}

export function listAssetNovels(): Promise<NovelAssetSummary[]> {
  return assetFetch("/api/assets/novels");
}

export function listAssetPrompts(novel: string): Promise<PromptEntry[]> {
  return assetFetch(`/api/assets/novels/${encodeURIComponent(novel)}/prompts`);
}

/** 章节目录与文档清单（只列文件名，正文走 getAssetDocument） */
export function listAssetChapters(novel: string): Promise<ChapterEntry[]> {
  return assetFetch(`/api/assets/novels/${encodeURIComponent(novel)}/chapters`);
}

/** 读任意归档 Markdown（章节站位/分镜只读查看） */
export function getAssetDocument(novel: string, path: string): Promise<AssetDocument> {
  return assetFetch(
    `/api/assets/novels/${encodeURIComponent(novel)}/document?path=${encodeURIComponent(path)}`,
  );
}

export function getAssetPrompt(id: string): Promise<PromptDetail> {
  return assetFetch(`/api/assets/prompts/${encodeURIComponent(id)}`);
}

export function saveAssetPrompt(id: string, body: string): Promise<PromptDetail> {
  return assetFetch(`/api/assets/prompts/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
}

export function approveAssetPrompt(id: string): Promise<PromptDetail> {
  return assetFetch(`/api/assets/prompts/${encodeURIComponent(id)}/approve`, {
    method: "POST",
  });
}

export function regenerateAssetPrompt(id: string): Promise<PromptDetail> {
  return assetFetch(`/api/assets/prompts/${encodeURIComponent(id)}/regenerate`, {
    method: "POST",
  });
}

export async function listSkills(categories?: string[]): Promise<SkillCatalogResponse> {
  const query = categories?.length
    ? `?categories=${encodeURIComponent(categories.join(","))}`
    : "";
  const res = await fetch(`/api/pipeline/skills${query}`);
  if (!res.ok) {
    throw new Error("技能列表加载失败");
  }
  return res.json();
}

export async function generatePackage(req: GenerateRequest): Promise<GenerateResponse> {
  const res = await fetch("/api/pipeline/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }
  return res.json();
}

/** 多章编排只放开三种模式；visual_only 依赖单章外部资产表，逐章生成时语义不成立 */
export type NovelExtractMode = "auto" | "assets_only" | "per_type";

export interface NovelGenerateRequest {
  novel_title: string;
  /** 若干章的「标题行 + 正文」，标题独占一行、从「第X章」开头 */
  content: string;
  character_hint?: string;
  prop_hint?: string;
  scene_hint?: string;
  mode?: NovelExtractMode;
  target_duration?: number;
  max_groups?: number;
  skills?: string[];
  scene_filter?: string[];
}

export interface ChapterOutput {
  index: number;
  title: string;
  text_chars: number;
  characters: Character[];
  props: Prop[];
  scenes: Scene[];
  blockings: Blocking[];
  storyboards: StoryboardGroup[];
  latency_ms: number;
  prompt_version: string;
  mode: string;
  llm_calls: number;
  agent_calls: number;
  hints_used: string[];
  injection_warning: boolean;
  skills_loaded: string[];
  skills_skipped: string[];
  skills_refs_expanded: number;
  skills_refs_skipped: number;
  stages: Record<string, unknown>[];
  gate_issues: GateIssue[];
  total_groups_estimated: number;
  truncated: boolean;
}

export interface NovelGenerateResponse {
  run_id: string;
  model: string;
  mock: boolean;
  novel_title: string;
  chapter_count: number;
  chapters: ChapterOutput[];
  total_llm_calls: number;
  total_agent_calls: number;
  total_latency_ms: number;
  total_characters: number;
  total_props: number;
  total_scenes: number;
  total_storyboards: number;
  injection_warning: boolean;
  gate_issue_count: number;
  archive: {
    root: string;
    novel_dir: string;
    files_written: number;
    image_prompt_entries: number;
    image_prompts_written: number;
    prompts_skipped: number;
    prompt_draft: number;
    prompt_edited: number;
    prompt_approved: number;
    errors: string[];
  } | null;
}

/** 生成进度事件：只报阶段，不含内容；total=0 表示总数未知（不确定态） */
export interface ProgressEvent {
  phase: "split" | "chapter" | "finalize" | "archive" | string;
  text: string;
  step: number;
  total: number;
}

/** 进度流回调；done 拿到完整结果，error 拿到错误文案 */
export interface StreamHandlers {
  onProgress: (event: ProgressEvent) => void;
  onDone: (result: NovelGenerateResponse) => void;
  onError: (message: string) => void;
  /** 供取消生成用（AbortController.signal） */
  signal?: AbortSignal;
}

/** POST + ReadableStream 解析 SSE（EventSource 只支持 GET，带正文的请求不能用它） */
export async function generateNovelStream(
  req: NovelGenerateRequest,
  handlers: StreamHandlers,
): Promise<void> {
  const res = await fetch("/api/pipeline/generate-novel/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal: handlers.signal,
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 帧以空行分隔；一次 read 可能含多帧或半帧，按 \n\n 切，末段留到下一轮。
    let split: number;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = "";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!event || !data) continue;
      const payload = JSON.parse(data);
      if (event === "progress") handlers.onProgress(payload as ProgressEvent);
      else if (event === "done") handlers.onDone(payload as NovelGenerateResponse);
      else if (event === "error") handlers.onError(payload.message || "生成失败");
    }
  }
}

export async function generateNovel(req: NovelGenerateRequest): Promise<NovelGenerateResponse> {
  const res = await fetch("/api/pipeline/generate-novel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }
  return res.json();
}

/** ---------------------------------------------------------------- 出图（S7 设定图投产） */

/** 一张已落盘的设定图；完整技术档案走 getMediaSidecar */
export interface MediaImageRecord {
  id: string;
  prompt_id: string;
  novel: string;
  category: string;
  asset_name: string;
  img_id: string;
  /** 该条目的第几版；只增不减，旧版留着能退 */
  version: number;
  /** 相对小说目录的路径，读图走 mediaFileUrl */
  path: string;
  sidecar: string;
  mime: string;
  bytes: number;
  sha256: string;
  width: number | null;
  height: number | null;
  provider: string;
  model: string;
  size: string;
  ratio: string;
  seed: number | null;
  fingerprint: string;
  job_id: string;
  /** 是否为该条目的当前选用版本 */
  selected: boolean;
  created_at: string;
}

/** 预览回告：算账不花钱，给确认码与阻塞项 */
export interface MediaPreview {
  job_id: string;
  novel: string;
  prompt_id: string;
  img_id: string;
  title: string;
  category: string;
  /** 确认码：CONFIRM <job_id> <fingerprint[:12]>，改任何参数都会失效 */
  confirmation: string;
  fingerprint: string;
  prompt_hash: string;
  prompt_chars: number;
  provider: string;
  model: string;
  size: string;
  ratio: string;
  seed: number | null;
  n: number;
  state: string;
  /** 非空表示现在提交必然失败 */
  blocking: string[];
  warnings: string[];
  cost_estimate: number | null;
  /** False 时只允许离线 fixture，真实厂商会被拒绝 */
  allow_paid: boolean;
  quota: { date: string; submitted: number; cost: number; quota: number; remaining: number | null };
  /** 同指纹已有产物时直接给出，无需重跑 */
  cached: MediaImageRecord | null;
  images: MediaImageRecord[];
  /** 已有图不是按当前提示词出的 */
  prompt_stale: boolean;
}

export interface MediaImagesResponse {
  novel: string;
  images: MediaImageRecord[];
  current_body_hash: Record<string, string>;
}

export interface MediaStreamHandlers {
  onProgress: (event: ProgressEvent) => void;
  /** 每落盘一张图推一条 */
  onMedia: (image: MediaImageRecord) => void;
  onDone: (payload: {
    cached: boolean;
    images?: MediaImageRecord[];
    image?: MediaImageRecord;
    latency_ms?: number;
    cost_estimate?: number | null;
    appended_suffix?: string;
  }) => void;
  onError: (message: string) => void;
  signal?: AbortSignal;
}

/** 读图片本体：路径相对小说目录，后端校验越界与后缀 */
export function mediaFileUrl(novel: string, path: string): string {
  return `/api/media/file?novel=${encodeURIComponent(novel)}&path=${encodeURIComponent(path)}`;
}

export function previewMedia(promptId: string, seed?: number | null): Promise<MediaPreview> {
  return assetFetch("/api/media/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, seed: seed ?? null }),
  });
}

export function listMediaImages(novel: string, promptId = ""): Promise<MediaImagesResponse> {
  const query = promptId ? `?novel=${encodeURIComponent(novel)}&prompt_id=${encodeURIComponent(promptId)}` : `?novel=${encodeURIComponent(novel)}`;
  return assetFetch(`/api/media/images${query}`);
}

export function selectMediaImage(imageId: string): Promise<MediaImageRecord> {
  return assetFetch(`/api/media/images/${encodeURIComponent(imageId)}/select`, { method: "POST" });
}

export function getMediaSidecar(imageId: string): Promise<Record<string, unknown>> {
  return assetFetch(`/api/media/images/${encodeURIComponent(imageId)}/sidecar`);
}

/**
 * 提交出图。事件：progress 若干 → media 每张一条 → done；失败走 error。
 * 与生成主链路共用同一套 SSE 解析手法（POST + ReadableStream）。
 */
export async function generateMediaStream(
  promptId: string,
  confirmation: string,
  handlers: MediaStreamHandlers,
): Promise<void> {
  const res = await fetch("/api/media/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_id: promptId, confirmation }),
    signal: handlers.signal,
  });
  if (!res.ok || !res.body) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "请求失败");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split: number;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = "";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!event || !data) continue;
      const payload = JSON.parse(data);
      if (event === "progress") handlers.onProgress(payload as ProgressEvent);
      else if (event === "media") handlers.onMedia(payload as MediaImageRecord);
      else if (event === "done") handlers.onDone(payload);
      else if (event === "error") handlers.onError(payload.message || "出图失败");
    }
  }
}
