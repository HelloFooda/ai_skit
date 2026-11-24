"""数据契约：五类文生文产出。

对应 AGENTS.md 与技能包的口径：
- 角色 / 角色道具 / 场景  → S2 视觉设定（`剧集/<EP>/视觉设定.md` 的人物 / 地点 / 道具条目；
  技能里还有第四类「造型」，本项目把它挂在角色条目下输出，不另立一块）
- 站位 / 分镜组表          → S4 分镜（`分镜.md` 的 `SHOT-...` 逐镜条目）；站位写进每镜
  起点/终点，这里按组聚合出来单看，不重新生成

五类在**展示**上是五块，在**生成**上是两段：站位由分镜的 `blocking` 聚合（`build_blockings`），
跟分镜同一次调用产出；单独生成会写两遍且互相矛盾（见 llm.py 的分段说明）。
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------- 资产三类（S2）

class Character(BaseModel):
    name: str = Field(description="角色名，与原文一致")
    appearance: str = Field(description="外貌与服饰")
    stable_features: str = Field(
        description="跨场不变的「外观特征」（疤痕/痣/配饰/身形），跨分镜保持一致用"
    )
    state: str = Field(
        default="",
        description="「本集状态」：只写外观/造型的阶段性变化（换装、伤污、淋湿、持物、年龄跨度）"
        "+ 原因 + 起止场次；不是剧情摘要（「随后走到窗边」这类行程属动作，不写在这里）。"
        "与 stable_features 严格分开，无变化留空。技能里单立的「造型」条目也并入这里",
    )
    voice: str = Field(
        default="", description="声线依据：音域质地、语速、语气；无台词角色留空"
    )
    prompt: str = Field(
        default="",
        description="资产描述：把 appearance/stable_features/state 整合成一段可读的外观陈述，"
        "供下游写图片提示词时引用。不含版式（三视图/并排/姓名标签）、画幅、景别、机位"
        "——那些属图片提示词阶段（S3）",
    )


class Prop(BaseModel):
    name: str = Field(description="道具名")
    owner: str = Field(
        default="", description="归属角色名；环境陈设类道具留空（这是「角色道具」的判据）"
    )
    appearance: str = Field(description="外观")
    state_changes: str = Field(default="", description="状态变化，无则空")
    prompt: str = Field(
        default="",
        description="资产描述：轮廓尺寸、材质肌理、关键结构与字迹处理；不含版式、画幅、参考图说明",
    )


class Scene(BaseModel):
    name: str = Field(description="场景名")
    time: str = Field(description="时间")
    light: str = Field(description="光线（写具体光源，不写「光线昏暗」这类空话）")
    atmosphere: str = Field(description="氛围")
    prompt: str = Field(
        default="",
        description="资产描述：空间布局、固定陈设相对位置、材质与主光方位；不含版式、画幅、机位、参考图说明",
    )


class Assets(BaseModel):
    """第一段的中间结构：三类资产。第二段拿它的序列化结果当输入，
    保证分镜里出现的角色名、道具名、场景名与资产表一字不差。"""

    characters: list[Character] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


# ------------------------------------------------- 站位 + 分镜组表（S4/S5）
class Shot(BaseModel):
    """一个镜头。字段对应 `分镜.md` 里 `SHOT-...` 条目的「时长 / 景别·机位 / 运镜 /
    目的 / 起点·动作·终点 / 声音」，站位不是独立字段而是写进起点与终点。"""

    shot_no: int = Field(ge=1, description="组内镜号，从 1 起连续")
    # 字段级只挡「物理不可能」；1.2s 可拍下限这类规则交给机检报，不要让它变 500
    duration: float = Field(ge=0.1, le=120, description="本镜时长（秒），由声音与动作时间线推算")
    shot_size: str = Field(description="景别：远景/全景/中景/近景/特写")
    angle: str = Field(description="角度：平视/俯拍/仰拍/过肩/主观视角")
    camera: str = Field(description="运镜；固定机位也必须写「机位固定」")
    function: str = Field(description="目的：交代环境/推进/反应/转折/收尾")
    content: str = Field(description="起点→动作→终点，写能拍出来的动作与微表情")
    dialogue: str = Field(default="", description="台词，逐字取自原文；无则空")
    blocking: str = Field(
        description="站位：谁｜画面位置｜朝向｜机位在轴线哪一侧"
    )


class StoryboardGroup(BaseModel):
    """一个投产分组（合生分组 `GROUP-...`）：相邻镜打包成一个任务，4–15 秒。

    组只是投产视图，不改变每镜独立可核查；一章通常切多个组，不是一组。
    """

    group_id: str = Field(description="组号，G01 起")
    # 同上：15s 上限由机检判，这里只挡明显不可能的值，避免模型跑偏就整单 500
    duration: float = Field(ge=0.5, le=120, description="组时长（秒），应等于各镜时长之和")
    location: str = Field(description="所在场景名，必须是已登记的场景之一")
    summary: str = Field(default="", description="本组剧情节点一句话")
    shots: list[Shot] = Field(description="组成员镜，按分镜顺序紧邻")

    @property
    def shots_total(self) -> float:
        """各镜时长之和。组时长该不该等于它，由 check_storyboards 判，不在模型里抛错——
        抛错会让整次请求 500，而首版的门禁口径是「提示不阻断」。"""
        return round(sum(s.duration for s in self.shots), 2)


class Blocking(BaseModel):
    """站位预演（S5）：从分镜表的站位列抽出来单独看，方便查走位与轴线。

    不重新生成，只是把 shots[].blocking 按组聚合，保证两者永远一致。
    """

    group_id: str
    location: str = ""
    text: str = Field(description="该组站位：第一镜写全，后续只写变化；跨组须接得上")


# ---------------------------------------------------------------- 五类聚合

class ScriptPackage(BaseModel):
    characters: list[Character] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    blockings: list[Blocking] = Field(default_factory=list)
    storyboards: list[StoryboardGroup] = Field(default_factory=list)


# ---------------------------------------------------------------- 机检（代码判，不上 LLM）

class GateIssue(BaseModel):
    """一条机检告警。首版只提示不阻断，S5 正式门禁再改为阻断。"""

    code: str = Field(description="规则码，如 shot_sum / shot_too_short")
    group_id: str = ""
    detail: str


def check_storyboards(groups: list[StoryboardGroup]) -> list[GateIssue]:
    """分镜的算术与可拍性门禁（代码判，不上 LLM）。

    规则只留「能算准的」：各镜之和 = 组时长、组落在 4–15 秒（模型单条上限）、
    单镜 1.2 秒可拍下限。镜数不设上下限——`SHT-03` 要求按表演节奏与信息量决定
    合并或拆镜，硬套 4–8 镜只会逼模型注水或砍戏。
    """
    issues: list[GateIssue] = []
    for g in groups:
        if not g.shots:
            issues.append(
                GateIssue(code="empty_group", group_id=g.group_id, detail="组内没有任何镜头")
            )
            continue
        total = g.shots_total
        if abs(total - g.duration) > 0.5:
            issues.append(
                GateIssue(
                    code="shot_sum",
                    group_id=g.group_id,
                    detail=f"各镜之和 {total}s ≠ 组时长 {g.duration}s",
                )
            )
        if total < 4:
            issues.append(
                GateIssue(
                    code="group_fragment",
                    group_id=g.group_id,
                    detail=f"组时长 {total}s < 4s，低于模型单条下限，应并入相邻组",
                )
            )
        if total > 15:
            issues.append(
                GateIssue(
                    code="group_overlong",
                    group_id=g.group_id,
                    detail=f"组时长 {total}s > 15s，超模型单条上限，应拆组",
                )
            )
        for s in g.shots:
            if s.duration < 1.2:
                issues.append(
                    GateIssue(
                        code="shot_too_short",
                        group_id=g.group_id,
                        detail=f"第 {s.shot_no} 镜 {s.duration}s < 1.2s，低于可拍下限",
                    )
                )
            elif s.duration > 15:
                issues.append(
                    GateIssue(
                        code="shot_overlong",
                        group_id=g.group_id,
                        detail=f"第 {s.shot_no} 镜 {s.duration}s > 15s",
                    )
                )
    return issues


def build_blockings(groups: list[StoryboardGroup]) -> list[Blocking]:
    """把分镜表的站位列抽成独立的「角色站位」一栏，不重新生成。"""
    out: list[Blocking] = []
    for g in groups:
        if not g.shots:
            continue
        lines = [f"{s.shot_no}. {s.blocking}" for s in g.shots if s.blocking.strip()]
        out.append(
            Blocking(group_id=g.group_id, location=g.location, text="\n".join(lines))
        )
    return out


# ---------------------------------------------------------------- 请求 / 响应

class ExtractAssetsRequest(BaseModel):
    """主接口的请求体。用户只需要填 chapter，其余全是可选。"""

    # 章节原文只做长度校验，绝不截断/改写（全程对账基准）
    chapter: str = Field(min_length=10, max_length=20_000, description="章节原文，逐字不改")

    # 「额外用户提示词」：默认全空 = 按 skill 与内置口径走，用户不用管
    character_hint: str = Field(default="", max_length=2_000, description="角色类补充要求")
    prop_hint: str = Field(default="", max_length=2_000, description="道具类补充要求")
    scene_hint: str = Field(default="", max_length=2_000, description="场景类补充要求")

    # auto=两段（资产→站位+分镜）；assets_only=只盘资产；per_type=三类资产并发（对照实验）；
    # visual_only=跳过盘资产，直接用请求里带回来的 assets 只重跑分镜（长章按场分片用）
    mode: Literal["auto", "assets_only", "per_type", "visual_only"] = Field(
        default="auto",
        description="auto 出全五类（2 次调用）；assets_only 只出三类资产（1 次）；"
        "per_type 三类并发（3 次）；visual_only 只重跑分镜（1 次，需带 assets）",
    )

    # visual_only 专用：把上一次 /generate 返回的三类资产原样带回来，保证分镜引用的名字不变
    assets: Assets | None = Field(
        default=None, description="已定稿的三类资产；仅 mode=visual_only 时需要、也只用它"
    )

    # 长章按场分片：只拆点名场次，组号仍从 G01 起。空=整章
    scene_filter: list[str] = Field(
        default_factory=list, max_length=20, description="只拆这些场景（逐字取资产表里的场景名）；空=整章"
    )

    # 合生分组：一章常常不止一组，用上限控制输出体量，超出只出前 N 组并标记 truncated
    target_duration: float = Field(
        default=15.0, ge=4, le=15, description="单组目标时长（秒），模型单条上限 4–15"
    )
    max_groups: int = Field(
        default=6, ge=1, le=20, description="本次最多产出几组；超出部分标记 truncated"
    )

    # 技能：留空=按「显式声明的类别」自动装载；显式点名则只装这些（名字不存在返回 400）
    skills: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="要装载的技能名，留空=按声明类别自动匹配（未声明类别的不自动装）",
    )

    @model_validator(mode="after")
    def _check_visual_only(self):
        """visual_only 没有资产表就无从保证名字一致，属请求错误，在边界上就挡掉。"""
        if self.mode == "visual_only" and self.assets is None:
            raise ValueError("mode=visual_only 必须同时提供 assets（上一次 /generate 返回的三类资产）")
        return self


class SkillInfo(BaseModel):
    """技能菜单条目：只有元数据，不含正文。"""

    name: str
    description: str
    # 显式声明的类别；空/未声明时为 ["manual"]，只按名字手动选择
    categories: list[str]
    version: str = ""
    license: str = ""
    display_name: str = ""
    body_chars: int = 0
    # 正文引用到的本地参考文件（装载时会内联展开）
    refs: list[str] = Field(default_factory=list)
    # 非致命提示，如 name 与目录名不一致
    notes: list[str] = Field(default_factory=list)


class ExtractAssetsResponse(BaseModel):
    run_id: str
    model: str
    mock: bool

    # 五类产出：资产三类 + 站位 + 分镜组表
    characters: list[Character] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    blockings: list[Blocking] = Field(default_factory=list)
    storyboards: list[StoryboardGroup] = Field(default_factory=list)

    latency_ms: int
    # 可观测元数据：提示词版本 / 模式 / 实际调用次数 / 用到的补充要求 / 注入告警
    prompt_version: str
    mode: str
    llm_calls: int
    hints_used: list[str]
    injection_warning: bool
    # 本次真正装进系统提示词的技能，以及因篇幅预算被跳过的技能
    skills_loaded: list[str]
    skills_skipped: list[str]
    # references 内联展开情况（第三层渐进披露的降级实现）
    skills_refs_expanded: int = 0
    skills_refs_skipped: int = 0

    # 分段执行情况：每段耗时与产出条数，出问题时能定位是哪一段
    stages: list[dict] = Field(default_factory=list)
    # 机检结果（S4 硬规则的代码版），首版只提示不阻断
    gate_issues: list[GateIssue] = Field(default_factory=list)
    # 组数控制：模型预估本章需要多少组，本次实际出了多少，是否被上限截断
    total_groups_estimated: int = 0
    truncated: bool = False


class SkillCatalogResponse(BaseModel):
    skills: list[SkillInfo]
    errors: list[str] = Field(
        default_factory=list, description="格式不合法、已跳过的技能文件（不影响服务）"
    )


# ---------------------------------------------------------------- 小说名 + 多章输入（LangGraph 编排）

class NovelPipelineRequest(BaseModel):
    """两字段输入：小说名独立；章节标题与正文同框，由行的「第X章」边界确定性切分。"""

    novel_title: str = Field(min_length=1, max_length=200, description="小说名，逐字保留供资产归档")
    content: str = Field(
        min_length=10,
        max_length=200_000,
        description="若干章的「标题行 + 正文」，标题独占一行、从「第X章」开头",
    )

    character_hint: str = Field(default="", max_length=2_000, description="角色类补充要求")
    prop_hint: str = Field(default="", max_length=2_000, description="道具类补充要求")
    scene_hint: str = Field(default="", max_length=2_000, description="场景类补充要求")

    # visual_only 依赖外部已定稿资产表，多章逐章生成时语义不成立，这里不放开
    mode: Literal["auto", "assets_only", "per_type"] = Field(
        default="auto", description="auto 出全五类；assets_only 只出资产；per_type 三类并发"
    )

    target_duration: float = Field(default=15.0, ge=4, le=15, description="单组目标时长（秒）")
    max_groups: int = Field(default=6, ge=1, le=20, description="每章最多产出几组")
    skills: list[str] = Field(default_factory=list, max_length=20, description="点名装载的技能")
    scene_filter: list[str] = Field(default_factory=list, max_length=20, description="只拆这些场景")


class ChapterOutput(BaseModel):
    """一章的产出：五类内容 + 该章独立的成本/机检元数据。"""

    index: int
    title: str
    text_chars: int
    characters: list[Character] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    blockings: list[Blocking] = Field(default_factory=list)
    storyboards: list[StoryboardGroup] = Field(default_factory=list)
    latency_ms: int
    prompt_version: str
    mode: str
    llm_calls: int
    # agent 编排自己发起的模型调用次数（选工具 / 收尾），与 llm_calls（生成）分开计，
    # 两者相加才是这一章的真实模型调用成本
    agent_calls: int = 0
    hints_used: list[str] = Field(default_factory=list)
    injection_warning: bool = False
    skills_loaded: list[str] = Field(default_factory=list)
    skills_skipped: list[str] = Field(default_factory=list)
    skills_refs_expanded: int = 0
    skills_refs_skipped: int = 0
    stages: list[dict] = Field(default_factory=list)
    gate_issues: list[GateIssue] = Field(default_factory=list)
    total_groups_estimated: int = 0
    truncated: bool = False


# ---------------------------------------------------------------- 资产库清单与三态

PromptStatus = Literal["draft", "edited", "approved"]


class PromptEntry(BaseModel):
    """清单里的一条提示词索引：定位信息与状态，正文只在 md 文件里。"""

    id: str = Field(description="稳定 id：img-<category>-<hash8>，用于 API 寻址")
    img_id: str = Field(description="展示标签：IMG-CHAR-001 等，按渲染顺序编号")
    category: str = Field(description="character / scene / prop")
    asset_name: str = Field(description="资产名，跨章合并后的唯一名")
    path: str = Field(description="相对小说目录的提示词文件路径")
    status: PromptStatus = Field(default="draft", description="draft / edited / approved")
    body_hash: str = Field(default="", description="正文 sha256，判断模板版本是否变化")
    updated_at: str = Field(default="", description="最近更新时间（UTC ISO）")


class PromptDetail(PromptEntry):
    """单条提示词全文：正文 + 可选的模板对照副本。"""

    body: str = Field(default="", description="当前正文，从 md 文件读出")
    generated_body: str = Field(
        default="", description="`.generated.md` 模板对照版本；空串表示没有对照"
    )


class MediaImageRecord(BaseModel):
    """一张已落盘的设定图。清单里只存索引，完整技术档案在同目录 `-vN.json`。"""

    id: str = Field(description="稳定 id：mi-<hash10>，由 prompt_id + 版本派生，API 按它寻址")
    prompt_id: str = Field(description="所属提示词条目 id")
    novel: str = Field(description="小说目录名")
    category: str = Field(default="", description="character / scene / prop")
    asset_name: str = Field(default="", description="条目名，如「唐欣妍」")
    img_id: str = Field(default="", description="展示标签，如 IMG-CHAR-001")
    version: int = Field(default=1, ge=1, description="该条目的第几版；只增不减、不复用")
    path: str = Field(description="相对小说目录的图片路径，如 角色/唐欣妍/图片/唐欣妍-v1.png")
    sidecar: str = Field(default="", description="相对小说目录的技术档案路径")
    mime: str = Field(default="image/png")
    bytes: int = Field(default=0)
    sha256: str = Field(default="")
    width: int | None = None
    height: int | None = None
    provider: str = Field(default="", description="产出厂商名，如 fixture")
    model: str = Field(default="")
    size: str = Field(default="")
    ratio: str = Field(default="")
    seed: int | None = None
    fingerprint: str = Field(default="", description="生成时指纹；提示词一改就不再匹配")
    job_id: str = Field(default="")
    selected: bool = Field(default=False, description="是否为该条目的当前选用版本")
    created_at: str = Field(default="")


class AssetManifest(BaseModel):
    """一本小说的 `_manifest.json`：提示词条目清单 + 已出图索引。"""

    novel: str = Field(description="小说名（净化后的目录名）")
    updated_at: str = Field(default="", description="清单最近更新时间（UTC ISO）")
    entries: list[PromptEntry] = Field(default_factory=list)
    images: list[MediaImageRecord] = Field(
        default_factory=list, description="已落盘设定图索引（正文与文件在 图片/ 下）"
    )


class NovelAssetSummary(BaseModel):
    """小说列表的一项：目录名 + 状态计数。"""

    name: str = Field(description="归档目录名，API 用它定位")
    title: str = Field(description="清单记录的小说名")
    updated_at: str = Field(default="")
    entries: int = Field(default=0)
    draft: int = Field(default=0)
    edited: int = Field(default=0)
    approved: int = Field(default=0)
    chapters: int = Field(default=0, description="章节目录数（不在清单里，按磁盘统计）")


class ChapterDoc(BaseModel):
    """章节目录下的一份文档：`角色站位.md` / `分镜.md`。"""

    name: str = Field(description="文件名，含 .md 后缀")
    path: str = Field(description="相对小说目录的路径，如 章节/第1章/分镜.md")
    size: int = Field(default=0, description="字节数")
    updated_at: str = Field(default="", description="文件 mtime（UTC ISO）")


class ChapterEntry(BaseModel):
    """一个章节目录：标题目录名 + 其下文档。

    章节不是提示词条目，没有 `IMG-...` 编号与三态状态，所以不进 `_manifest.json`，
    由 `asset_store.list_chapters` 直接读磁盘目录得到。
    """

    title: str = Field(description="章节目录名，如 第1章")
    path: str = Field(description="相对小说目录的路径，如 章节/第1章")
    docs: list[ChapterDoc] = Field(default_factory=list)


class AssetDocument(BaseModel):
    """任意一份归档 Markdown 的正文（章节文档只读用）。"""

    path: str = Field(description="相对小说目录的路径")
    name: str = Field(description="文件名")
    body: str = Field(default="", description="文件正文")


class PromptSaveRequest(BaseModel):
    """PUT /api/assets/prompts/{id} 的请求体。"""

    body: str = Field(max_length=20_000, description="编辑后的完整 Markdown 正文")


class ProgressEvent(BaseModel):
    """生成进度流的一条阶段事件：只报"做到哪一步"，不含任何生成内容。"""

    phase: str = Field(description="阶段标识：split / chapter / finalize / archive")
    text: str = Field(description="给人看的阶段文案，前端直接渲染")
    step: int = Field(default=0, ge=0, description="当前步序号；0 表示总数未知（如切分前）")
    total: int = Field(default=0, ge=0, description="总步数；0 表示不确定态")


# ---------------------------------------------------------------- 出图（S7 设定图投产）


class MediaPreviewRequest(BaseModel):
    """POST /api/media/preview：算账不花钱。"""

    prompt_id: str = Field(description="提示词条目 id（清单里的 img-<category>-<hash8>）")
    seed: int | None = Field(default=None, description="固定种子；留空则每次不同")


class MediaPreviewResponse(BaseModel):
    """预览回告：指纹 + 确认码 + 阻塞项 + 额度。确认码绑定当前指纹，改任何参数都会失效。"""

    job_id: str
    novel: str
    prompt_id: str
    img_id: str = ""
    title: str = ""
    category: str = ""
    confirmation: str = Field(description="确认码：CONFIRM <job_id> <fingerprint[:12]>")
    fingerprint: str = ""
    prompt_hash: str = ""
    prompt_chars: int = Field(default=0, description="可复制提示词正文字符数，供成本与长度判断")
    provider: str = ""
    model: str = ""
    size: str = ""
    ratio: str = ""
    seed: int | None = None
    n: int = 1
    state: str = Field(default="planned", description="planned / confirmed / submitted / done / failed")
    blocking: list[str] = Field(default_factory=list, description="非空表示现在提交必然失败")
    warnings: list[str] = Field(default_factory=list)
    cost_estimate: float | None = None
    allow_paid: bool = Field(default=False, description="False 时只允许离线 fixture")
    quota: dict = Field(default_factory=dict)
    cached: MediaImageRecord | None = Field(
        default=None, description="同指纹已有产物时直接给出，无需重跑"
    )
    images: list[MediaImageRecord] = Field(default_factory=list, description="该条目已有全部版本")
    prompt_stale: bool = Field(
        default=False, description="已有图不是按当前提示词出的（提示词改过）"
    )


class MediaGenerateRequest(BaseModel):
    """POST /api/media/generate：必须带上与当前指纹匹配的确认码。"""

    prompt_id: str
    confirmation: str = Field(description="预览返回的确认码，逐字回传")


class MediaImagesResponse(BaseModel):
    """GET /api/media/images：某本小说（可限定条目）已出的图。"""

    novel: str
    images: list[MediaImageRecord] = Field(default_factory=list)
    current_body_hash: dict[str, str] = Field(
        default_factory=dict, description="prompt_id → 当前正文 hash，用于判断旧图是否过期"
    )


class MediaSelectRequest(BaseModel):
    """POST /api/media/images/{id}/select。"""



class MediaJobStatusResponse(BaseModel):
    """GET /api/media/jobs：任务状态与额度。"""

    novel: str
    jobs: list[dict] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    quota: dict = Field(default_factory=dict)


class ArchiveReport(BaseModel):
    """资产归档回告：落了多少文件、聚合/实际写了多少条目、有没有局部失败。

    归档是生成成功后的副作用，失败只记 error，不改生成结果、不抛 500。
    """

    root: str = Field(description="归档根目录（已解析为绝对路径字符串）")
    novel_dir: str = Field(description="本次小说落盘的目录")
    files_written: int = 0
    characters: int = Field(default=0, description="跨章去重后的角色数")
    props: int = Field(default=0, description="跨章去重后的道具数")
    scenes: int = Field(default=0, description="跨章去重后的场景数")
    characters_written: int = 0
    props_written: int = 0
    scenes_written: int = 0
    image_prompt_entries: int = Field(
        default=0, description="由资产事实模板拼出的 IMG-... 条目数（零 LLM 调用）"
    )
    image_prompts_written: int = Field(default=0, description="本次实际写入主文件的提示词文档数")
    prompts_skipped: int = Field(
        default=0, description="因 edited/approved 被跳过覆盖、改写对照副本的提示词数"
    )
    prompt_draft: int = Field(default=0, description="归档后 draft 状态的提示词总数")
    prompt_edited: int = Field(default=0, description="归档后 edited 状态的提示词总数")
    prompt_approved: int = Field(default=0, description="归档后 approved 状态的提示词总数")
    chapters: int = Field(default=0, description="落了站位或分镜的章数")
    errors: list[str] = Field(default_factory=list, description="局部失败说明；非空表示部分落盘")


class NovelPipelineResponse(BaseModel):
    run_id: str
    model: str
    mock: bool
    novel_title: str
    chapter_count: int
    chapters: list[ChapterOutput] = Field(default_factory=list)
    total_llm_calls: int = 0
    total_agent_calls: int = 0
    total_latency_ms: int = 0
    total_characters: int = 0
    total_props: int = 0
    total_scenes: int = 0
    total_storyboards: int = 0
    injection_warning: bool = False
    gate_issue_count: int = 0
    # 资产归档回告；未触发（如无章）时为 None，局部失败见 archive.errors
    archive: ArchiveReport | None = None


def chapter_output(item: dict) -> ChapterOutput:
    """把图中的章结果（package 是对象）压成响应契约（五类摊平）。

    `model_validate` 会忽略多余键（package），所以直接合并、交给 Pydantic 校验一次，
    不必逐字段手搬——少一处改字段忘记同步的地方。
    """
    return ChapterOutput.model_validate({**item, **item["package"].model_dump()})
