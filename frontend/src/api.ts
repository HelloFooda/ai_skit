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
