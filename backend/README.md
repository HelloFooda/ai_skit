# ai_skit backend

最小前后端闭环的后端。当前做一件事：**贴一段小说章节，出五类文生文描述**——角色 / 角色道具 / 场景 / 角色站位 / 分镜组（每组合成 4–15 秒，模型单条上限）。用户不用写提示词。

内部按依赖顺序跑**两段**：先拆视觉设定（人物 / 造型并入角色 / 地点 / 道具），再带着已定稿的设定写站位与分镜。文生文走 **LangChain 1.x**：`ChatPromptTemplate` 管提示词、`ChatOpenAI` / `init_chat_model` 管模型、LCEL 链管调用、`RunnableConfig` 管超时/重试/并发/链路标签；输出仍是「JSON 文本 + 容错解析 + Pydantic 强校验」。先不上 Function Calling、不上 LangGraph。

## 开发环境（uv）

整个仓库只有 `backend/` 一个 Python 项目，环境就是 `backend/.venv`，由 uv 单独管理：解释器是 uv 托管的 CPython 3.12.16（不装进系统），`pyvenv.cfg` 里 `include-system-site-packages=false`，site-packages 只有 venv 自己那一处，与系统 Python 和其它项目的 venv 互不相通。版本由 `backend/.python-version` 固定为 3.12。

```bash
cd backend
uv sync --frozen       # 按 uv.lock 精确安装运行依赖 + dev 工具，不重新解析、不改 lock
uv run python -V       # Python 3.12.16，别直接敲 python3
```

项目内的命令一律加 `uv run`（自动用这个 venv，无需手动 activate）：

```bash
uv run uvicorn app.main:app --reload --port 8000
uv run python -m unittest discover -s tests                 # 全量单测
uv run ruff check app tests                                # 门禁；ruff 已锁在 dev 组，不必再 uvx
uv run python skills/short-drama/scripts/creator_markdown_check.py 剧集/EP001 --project-root .. --partial
```

最后一条说明：技能库脚本只依赖标准库，复用同一个 venv 即可，不需要再建一套环境，也不该落到系统 `python3` 上。

- 改依赖：`uv add <包>` / `uv add --dev <包>`，会同步更新 `pyproject.toml` 与 `uv.lock`，两个文件都要提交。
- 换机器或重建：`uv venv --clear --python 3.12 --prompt ai-skit-backend && uv sync --frozen`。`.venv` 已在 `backend/.gitignore` 里，不入库。
- PyCharm 2025.1：项目解释器注册为 `uv (ai_skit)`，指向 `backend/.venv/bin/python`；改完 SDK 配置要重启 IDE 才生效，可在 `Settings → Project → Python Interpreter` 核对。

## 跑起来

```bash
cd backend
uv sync --frozen
uv run uvicorn app.main:app --reload --port 8000
```

## 配置：`backend/.env`（全项目唯一一份）

运行时只读这一份 `.env`。配置类用绝对路径锚定 `backend/`，从仓库根目录或 `backend/` 启动读到的都是同一份。该文件已被 `backend/.gitignore` 忽略、不会入库；新克隆没有它也能跑——所有键都有默认值，空 key 会自动进 mock 模式。

按下面的骨架新建或补全 `backend/.env`，填真实 `LLM_API_KEY` 即为真实调用：

```dotenv
# 文生文通道（LangChain）
# provider=openai 走 OpenAI 兼容端点；换成 anthropic / google_genai 等时
# 需另装对应集成包（如 uv add langchain-anthropic），base_url 对非兼容端点无效
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-flash
LLM_MOCK=false
# 单次调用：温度 / 超时秒数 / SDK 重试次数 / batch 并发上限
LLM_TEMPERATURE=0.2
LLM_TIMEOUT=120
LLM_MAX_RETRIES=2
LLM_MAX_CONCURRENCY=3
# 技能库目录，留空则用 backend/skills
LLM_SKILLS_DIR=
# 项目侧「技能 → 类别」绑定表，留空则用 backend/skill-bindings.yaml
LLM_SKILLS_BINDINGS=
# 一次注入的技能总字数预算（0 表示用默认 60000）
LLM_SKILLS_BUDGET_CHARS=60000
```

- 未填 key：自动 mock，返回五类假数据，用于跑通前后端联调与演示。
- 填了 key：真实调用 LLM。模型统一由 `init_chat_model` 按 `LLM_PROVIDER` 装配；默认 `openai` 额外带 `LLM_BASE_URL`，覆盖 DeepSeek / 通义 / vLLM 等一切 OpenAI 兼容端点；换成 `anthropic` / `google_genai` 等需另装对应集成包（如 `uv add langchain-anthropic`）。
- 通道参数默认值：`LLM_TEMPERATURE` 0.2、`LLM_TIMEOUT` 120 秒、`LLM_MAX_RETRIES` 2、`LLM_MAX_CONCURRENCY` 3（`per_type` 的 batch 并发上限）。技能预算默认 60000 字。改完 `.env` 重启进程生效。

## 接口

- `GET /api/health` → `{"status":"ok"}`
- `GET /api/pipeline/skills?categories=characters,scenes` → 技能菜单（只有元数据，供前端多选）
- `POST /api/pipeline/generate` → 一次性出五类
- `POST /api/pipeline/extract-assets` → 同一函数的兼容别名（老前端不用改）

请求体：主路径只填 `chapter`，其余全可选。

```json
{
  "chapter": "林桂芳推开老饭馆的木门……",
  "character_hint": "重点写可跨分镜复用的稳定视觉特征，别写情绪",
  "prop_hint": "只登记后文还会再出现的道具",
  "scene_hint": "光线写具体光源，不要只写氛围词",
  "mode": "auto",
  "target_duration": 15,
  "max_groups": 6,
  "skills": ["short-drama-assets"]
}
```

`mode`：
- `auto`（默认）：两段串行，出全五类，2 次调用。
- `assets_only`：只出三类资产，1 次调用（返修资产时用，便宜一半）。
- `per_type`：三类资产并发三次，物理隔离，1 段 3 次调用；用来 A/B 验证「三类同出会不会串味」。不出站位与分镜。
- `visual_only`：跳过盘资产，用请求里带回来的 `assets` 只重跑站位+分镜，1 次调用。**长章按场分片的跑法**：先跑一次 `auto`/`assets_only` 拿到资产表，之后每场一次 `visual_only` + `scene_filter`，资产一个字不变，分镜名字自然接得上。

`scene_filter`（长章必备）：只拆点名的场次，值逐字取资产表里的场景名，如 `["梅洛星花海凉亭"]`；空 = 整章从头顺场推进。实测这一章 4542 字，模型自估 **22–55 组（≈6–14 分钟）**，而单次上限 20 组——不分场就永远只拍得到开头那一场，后面几场排不上。**不要靠调大 `max_groups` 解决覆盖，要靠 `scene_filter` 分片。**

`target_duration` / `max_groups`：**组是复数，不是一条**。镜长按本场声音与动作时间线推算，3.6 字/秒只作粗估（一章通常切十几到几十镜）；组只是投产分组，4–15 秒。`max_groups` 是输出体量闸门（默认 6，上限 20），超出时只出前 N 组，响应带 `truncated: true` 与 `total_groups_estimated`（模型对本章组数的预估）。

`skills`：
- 留空或不传 → 按**显式声明的类别**自动装载（`metadata.applies-to` / `skill-bindings.yaml` / `agents/openai.yaml`，或声明为 `global`）。没声明类别的一个都不自动装。
- 显式点名 → 按类目分流：只声明 `storyboard`/`blocking` 的进第二段，只声明资产类的进第一段，类别不明的两段都装。名字不存在返回 `400`。

响应是**五类平铺** + 元数据：`characters` / `props` / `scenes` / `blockings` / `storyboards`，加 `prompt_version` / `mode` / `llm_calls` / `hints_used` / `injection_warning` / `skills_loaded` / `skills_skipped` / `skills_refs_expanded` / `skills_refs_skipped` / `latency_ms` / `stages` / `gate_issues` / `total_groups_estimated` / `truncated`。真实调用（非 mock）时 `stages[]` 每项还带 `usage`（`input_tokens` / `output_tokens` / `total_tokens` / `llm_calls` / `llm_errors`），由 LangChain 回调采集，便于按段核算成本。

## 五类产出的来源

| 产出 | 来源 | 说明 |
|---|---|---|
| 角色 | 第一段 | 含 `stable_features`（跨场不变的外观特征）、`state`（本集状态/换装）、`voice`（声线）、`prompt`（资产描述，供 S3 写图片提示词时引用；三视图/姓名标签等版式属 S3，不在本段） |
| 角色道具 | 第一段 | 多一个 `owner` 字段：能归属到角色的填角色名，环境陈设留空——这就是「角色道具」的判据 |
| 场景 | 第一段 | `light` 必须写具体光源 |
| 角色站位 | 第二段**聚合** | 由 `build_blockings()` 从 `shots[].blocking` 按组聚合，**不单独生成**，所以永远与分镜一致 |
| 分镜组 | 第二段 | 组号/时长/场景/概要 + 逐镜（镜号/时长/景别/角度/运镜/目的/内容/台词/站位） |

分镜技能（`short-drama-storyboard`）里站位写进每镜的「起点/终点」，不单列成表；本项目接口把它按组聚合出来单独展示，只是**视图**，不是第五份独立内容。单独生成会出现两个版本、互相矛盾（分镜写「左侧三分之一」、站位写「居中」）。

## 两段为什么必须串行

`short-drama-assets` 的 `stage-contract` 写明本阶段「不越界决定单镜临时动作、左右手拿握、构图景别、动作终点或生图/视频提示词」，技能正文也写着「机位走位交由下游」。装了它还想让它写站位，它会把活推回去。

更硬的理由是一致性：分镜里的角色名、道具名、地点名必须与视觉设定一字不差，唯一可靠的做法是让第二段**看到**第一段的结果——所以第一段的资产表作为 `<asset_table>` 进第二段的 user 消息。

代价是 2 次调用（约 2 倍 token），换来名字对得上。

## 机检（代码判，不上 LLM）

规则能解决的一律不上 LLM。`check_storyboards()` 就是这条规矩的落点：

| 规则码 | 触发 |
|---|---|
| `shot_sum` | 各镜时长之和 ≠ 组时长（误差 > 0.5s） |
| `group_fragment` | 组时长 < 4s（低于模型单条下限，应并入相邻组） |
| `group_overlong` | 组时长 > 15s（超模型单条上限，应拆组） |
| `empty_group` | 组内没有任何镜头 |
| `shot_too_short` / `shot_overlong` | 单镜 < 1.2s（可拍下限）或 > 15s |

**镜数不判上下限**：分镜技能 `SHT-03` 要求按表演节奏、信息量与连戏决定合并或拆镜，硬套 4–8 镜只会逼模型注水或砍戏。

首版把这些作为 `gate_issues` 回告，**不阻断**。字段级校验也只挡「物理不可能」——组时长上限放到 120s，让 18s 的组变成一条机检提示，而不是让整个请求 500。

## LangChain 通道

文生文分三层，**提示词正文与输出契约都没变**：

| 层 | 文件 | 负责 |
| --- | --- | --- |
| 规则层 | `app/prompts.py` | 两套 system 用 `ChatPromptTemplate`(jinja2) 渲染；`PROMPT_VERSION` 只在正文改动时升版 |
| 通道层 | `app/chains.py` | 模型工厂（`LLM_PROVIDER` 配置驱动）、dict → Message 映射、`RunnableConfig`（超时/重试/标签/并发）、`UsageCollector`（token 按 stage 归因） |
| 编排层 | `app/llm.py` | 消息组装、两段串行、mock、Pydantic 校验、响应元数据 |

一条 LCEL 链：`dict 消息 → SystemMessage/HumanMessage → ChatModel → 纯文本`，单次走 `invoke`，`per_type` 走 `batch`（并发上限 `LLM_MAX_CONCURRENCY`）。每次调用带 `run_name` / `tags` / `metadata`，接 LangSmith / Langfuse 不用改业务代码。

三条硬约束：

1. **章节原文不进模板引擎**。它属不可信输入、逐字零容错，只由编排层直接拼进 user；通道层只做 role → Message 映射，不改 content。system 是项目可信规则，才进模板。技能正文作为**变量值**传入，即使含 `{{ }}` 也不会被 jinja2 二次解析。
2. **输出仍是纯 JSON 文本 + `_parse_json` 容错 + Pydantic 强校验**，不上 `with_structured_output`。DeepSeek 这类兼容端点对 function calling 支持参差，换了就等于改掉「只返回 JSON 对象」的输出契约；要上须先在目标模型实测并跑评测基线。
3. **重试不叠加**：模型层 `max_retries` 做一次，链层不再包 `with_retry`（否则指数级放大重试、烧额度）。密钥只以指纹进模型缓存键，不进 `metadata` 与日志。

`tests/unit/prompt_baseline_v7.py` 是从 git 里迁移前版本提取的提示词基线，`test_langchain_chain.py` 用它**逐字**对比模板渲染结果，证明这次只换了载体。

## 提示词分层

`app/prompts.py` 里的提示词是 `PROMPT_VERSION = "v7"`，**两套 system 提示词**（资产段 / 视觉段），各自两层：

- **system**：角色、硬性规则、技能规范、输入可信度声明、输出契约（稳定不变），由 `ChatPromptTemplate` 渲染。
- **user**：`<chapter_text>` 章节原文（声明为素材、不可信）+ `<user_hints>` 按类别分区的补充要求；第二段额外带 `<asset_table>`。由 `app/llm.py` 直接拼字符串，不经模板引擎。

章节原文逐字放入，不截断不改写；其中若出现注入式语句，只置 `injection_warning`，不拦截也不改字。改提示词正文请升版本号、同步 `tests/unit/prompt_baseline_v7.py` 并重跑全量评测。

技能规范进 system 而不进 user：skill 是项目维护的规则、可信；章节是用户输入、不可信。混在一起会削弱「素材不是指令」这条声明。视觉段的 system 里还明确禁止写首尾帧提示词与出片参数——那是 S6 的事，不越界。

## 技能库

目录与格式兼容 [Agent Skills](https://anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) 开放规范，从 GitHub 拿到的 skill 目录可以直接整个拷进来（含 `references/`、`scripts/`、`assets/`、`agents/` 子目录）：

```
skills/<kebab-case-name>/SKILL.md
skills/<kebab-case-name>/references/*.md     # 第三层：按需读取的细则
skills/<kebab-case-name>/agents/openai.yaml  # 可选的展示元数据
```

```markdown
---
name: short-drama-assets       # 必填，小写 kebab-case，目录名建议与之一致
description: 做什么 + 什么时候用  # 必填，出现在 /skills 菜单里
license: MIT                    # 可选，透传到菜单
metadata:                       # 可选
  applies-to: characters,props  # 本项目私有扩展，需要自动装载时才写
  version: "1.0"
---
```

### 加载器只强制的三件事

1. `name` 小写 kebab-case、`description` 非空、正文非空。**缺 `applies-to` 不是错误**，只是不参与自动装载。
2. **正文不截断**。篇幅控制靠预算：单技能块上限 `MAX_SKILL_BLOCK_CHARS`（32k 字符），一次注入总量 `LLM_SKILLS_BUDGET_CHARS`（默认 60k）。超了整体跳过并在 `skills_skipped` 回告，绝不悄悄砍半截——砍掉的往往正是「完成标准」这类收尾约束。
3. `name` 与目录名不一致、类别词表不认识，都只记 `notes` 提示，**不拒绝加载**。拒绝会让一条外来技能整条失效。

### references 内联展开

通用规范是三层渐进披露：frontmatter（菜单）→ SKILL.md 正文（装载）→ `references/*.md`（按需读取）。本项目当前**没有文件读取工具**，所以第三层用内联展开降级模拟：

- 解析正文里的 `[文本](references/x.md)`，把目标文件内容包进 `<ref path="...">` 插到正文之后；
- 只认技能目录内的 `.md`，外链、`scripts/*.py`、`assets/*.jsonl` 一律跳过；
- `..` 一律拒绝（目录穿越没有正当用途）；
- 递归两层（总纲 → 细则），单技能展开预算为块预算的一半，超了保留原链接并在段末说明「未展开」。

`skills_refs_expanded` / `skills_refs_skipped` 会回告展开了几篇、跳过了几篇。等阶段 03 接上 Function Calling，把展开换成真 `read_file` 即可，上层不用改。

### 类别与自动装载

- 类别词表：`characters` `props` `scenes` `storyboard` `blocking` `script` `review` `media` `global`。不在词表里的类别只提示、不拒绝。
- **自动装载只认显式声明**（`metadata.applies-to` → `skill-bindings.yaml` → `agents/openai.yaml`）。不按描述里的关键词猜：12 个阶段型技能里大半都提到「角色」「场景」，猜一次就装进半个库。
- 没声明的落到 `manual`，**只在你显式勾选 / 点名时才装**。前端默认不过滤，全部可见。
- 目录可用 `LLM_SKILLS_DIR` 覆盖，默认 `backend/skills`。

### skill-bindings.yaml

项目侧「技能 → 类别」绑定表（`backend/skill-bindings.yaml`，可用 `LLM_SKILLS_BINDINGS` 覆盖）。**用处是不改技能文件也能让它参与自动装载**——外来通用技能不会写本项目的私有字段，把归属声明放在项目侧，技能本身一个字不用动。改完保存即生效（按 mtime 缓存）。

```yaml
short-drama-assets: [characters, props, scenes]      # 第一段：盘资产
short-drama-storyboard: [storyboard, blocking]        # 第二段：站位 + 分镜
```

**装载由代码决定，不是模型自己挑**：本阶段没有工具调用，模型读不了文件。等阶段 03 把 `catalog()` 当成工具菜单给模型，就能变成「模型自己决定加载哪个技能」——骨架不用改，只多一个 `load_skill` 工具。

## 目录

```
app/
├── main.py        # FastAPI 入口 + CORS
├── config.py      # 配置（provider / base_url / api_key / model / 调用参数 / mock / skills_*）
├── schemas.py     # Pydantic 契约（五类产出 + Shot/StoryboardGroup + 机检函数）
├── skills.py      # 技能库：扫描 SKILL.md、解析通用 frontmatter、references 内联展开、预算控制
├── prompts.py     # 两套 system 规则层（ChatPromptTemplate / jinja2）+ 输出契约
├── chains.py      # 模型工厂 + LCEL 链 + RunnableConfig + UsageCollector
├── llm.py         # 业务编排：消息组装 / 两段串行 / json 解析 / Pydantic 校验 / mock
└── routers/
    └── pipeline.py  # /api/pipeline/skills、/api/pipeline/generate（+ extract-assets 别名）
skills/            # 技能库（每个技能一个目录 + SKILL.md + references/）
skill-bindings.yaml  # 项目侧「技能 → 类别」绑定表
tests/             # 所有测试统一放这里，按类型分子目录
├── unit/          # 标准库 unittest 单测（提示词分层、两段调用、机检、技能装载、LangChain 通道）
│   └── prompt_baseline_v7.py  # 迁移前提示词基线，用于逐字等价回归
└── manual/        # 手工试跑脚本（真实调用 / 分片重拆 / 报告合成），需凭据与网络
pyproject.toml     # 运行依赖 + dev 组（ruff），uv 项目定义
uv.lock            # 精确版本锁；uv sync --frozen 按它安装，不重新解析
.python-version    # 固定 Python 3.12（实际 CPython 3.12.16，uv 托管）
.venv/             # uv 隔离环境，已 gitignore，不入库
```
