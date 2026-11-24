# 前端技术文档

面向开发者：运行方式、组件结构、API 封装、响应字段。产品效果见[根 README](../README.md)，后端契约见[后端技术文档](backend.md)。

前端是一个 Vue 3 + Vite + TypeScript 的最小闭环：输入小说名与若干章原文 → 调后端 LangGraph 编排 → 按章切换并分栏展示五类结果。核心只有三个源码文件，没有路由、状态库或 UI 框架。

## 运行方式

```bash
cd frontend
npm install
npm run dev        # http://localhost:5174，/api 已代理到后端 8001
```

其余脚本：`npm run build`（先 `vue-tsc --noEmit` 再 `vite build`）、`npm run preview`（预览构建产物）。后端需先启动，配置见[后端技术文档](backend.md#开发环境uv)。

技术栈版本见 `package.json`：Vue 3.5、Vite 5.4、TypeScript 5.6、`@vitejs/plugin-vue` 5.1。

## 源码结构

```
frontend/
├── src/
│   ├── App.vue          # 生成页：输入、高级设置、技能勾选、结果展示（对话形态）
│   ├── AssetLibrary.vue # 资产库页：条目树、提示词编辑、出图（缩略图/版本条/确认抽屉）
│   ├── ResultView.vue   # 五类产出卡片
│   ├── api.ts           # 类型定义 + fetch / SSE 封装
│   ├── main.ts          # 挂载 Vue 应用
│   └── vite-env.d.ts    # Vite 环境类型
├── index.html
├── vite.config.ts       # /api 代理到 localhost:8001
└── tsconfig*.json
```

## 组件逻辑（App.vue）

`<script setup lang="ts">` 里的响应式状态：

| 状态 | 作用 |
| --- | --- |
| `novelTitle` / `content` | 小说名独立输入；章节标题与正文同框，可贴多章 |
| `showAdvanced` | 高级设置开关，默认关闭，全部走后端默认口径 |
| `characterHint` / `propHint` / `sceneHint` | 三类资产的补充要求，分别送后端对应字段 |
| `mode` | 多章模式：`auto` / `assets_only` / `per_type`；`visual_only` 不适用 |
| `targetDuration` / `maxGroups` | 单组目标时长（默认 15 秒）、本次最多出几组（默认 6） |
| `skillOptions` / `selectedSkills` | 技能菜单与勾选结果 |
| `skillNotice` | 菜单加载失败或技能文件格式不合法时的提示 |
| `loading` / `error` / `novelResult` | 请求状态与按章结果 |
| `activeChapter` / `current` | 当前选中的章号与对应产出，结果区所有五类卡片都读 `current` |

 mounting 时调 `listSkills()` 拉菜单，按「是否自动装载」排序：显式声明类别的排前面，`manual` 的排后面。前端默认不过滤，全部可见，方便手动点名装载。

`run()` 调 `generateNovel()`，请求体带上当前所有设置。结果顶部是章节页签（后端按「第X章」切分并逐字回拼后的顺序），点选后结果区五类卡片都读 `current`；`totalSeconds` 按当前章的分镜组时长求和，用于核对是否超出一章容量。

## API 封装（api.ts）

三个函数：

```ts
listSkills(categories?: string[]): Promise<SkillCatalogResponse>
generateNovel(req: NovelGenerateRequest): Promise<NovelGenerateResponse>   // 多章主路径
generatePackage(req: GenerateRequest): Promise<GenerateResponse>          // 单章兼容路径
```

都走相对路径 `/api/...`，由 Vite 代理到 8001。非 2xx 时解析 `detail` 字段作为错误信息抛出。

**多章请求字段**：`novel_title`、`content` 必填，其余可选——`character_hint` / `prop_hint` / `scene_hint`、`mode`、`target_duration`、`max_groups`、`skills`、`scene_filter`。`content` 里章节标题必须独占一行且以「第X章」开头，否则后端返回 400；正文里随口提到的章号不会误切。

**响应字段**：`run_id` / `model` / `mock`；五类平铺 `characters` / `props` / `scenes` / `blockings` / `storyboards`；元数据 `latency_ms` / `prompt_version` / `mode` / `llm_calls` / `hints_used` / `injection_warning` / `skills_loaded` / `skills_skipped` / `skills_refs_expanded` / `skills_refs_skipped` / `stages` / `gate_issues` / `total_groups_estimated` / `truncated`。

类型定义与后端 `schemas.py` 的 Pydantic 契约一一对应，关键字段注释：

- `Character.stable_features`：跨场不变的外观特征（疤痕 / 痣 / 配饰 / 身形）。
- `Character.state`：本集状态，换装、伤污、淋湿、持物等阶段变化；无变化为空。
- `Prop.owner`：归属角色名；环境陈设留空——这是「角色道具」的判据。
- `Shot.blocking`：格式为 `谁｜画面位置｜朝向｜机位在轴线哪一侧`。
- `Blocking`：由 `shots[].blocking` 按组聚合而来，不是独立生成，因此与分镜永远一致。
- `stages[]`：每段耗时与产出条数；真实调用时还带 `usage` token 归因。
- `gate_issues[]`：代码机检结果，首版只提示不阻断。

接口语义与模式说明见[后端技术文档](backend.md#接口)。

## 出图交互（AssetLibrary.vue）

资产库里每个条目行右侧是「缩略图 + 出图按钮」。已出过图的条目显示当前选用版的缩略图（带 `vN` 角标），点开看大图与 sidecar；没出过图的只显示「出图」入口。

出图走**预览 → 确认 → 提交**三步，对应后端的确认码协议：

| 步 | 前端动作 | 用户看到 |
| --- | --- | --- |
| ① 预览 | `previewMedia(promptId)` | 确认抽屉：厂商/模型、画幅、提示词字数、预估成本、今日额度、付费开关状态、阻塞项、确认码 |
| ② 确认 | 用户点「确认并生成」 | 确认码随请求原样回传；改过提示词就会失效，抽屉里会提示重新预览 |
| ③ 提交 | `generateMediaStream(...)` | 逐张回推，`media` 事件一到就更新缩略图；`done` 后刷新索引 |

**确认抽屉里必须显示的**（缺一项都算设计不完整）：

- **阻塞项**：`blocking` 非空时按钮直接禁用，并把原因列出来（如提示词缺 `### 可复制提示词`、额度已满）。
- **付费开关**：`allow_paid=false` 时明确写「已关闭（只允许离线 fixture）」，避免用户以为点了就会出真图。
- **预估成本与额度**：`cost_estimate` 为 `null` 时写「未配置（MEDIA_COST_PER_IMAGE）」，不要显示成 0。
- **水印警告**：`warnings` 里有水印提示时高亮，交付前必须关掉重出。
- **确认码**：可选中复制，并注明「改任何参数都会失效」。

**缓存提示**：`cached` 非空时抽屉显示「同参数已有成品（vN），确认后不会重复扣费」，按钮文案变成「确认（复用已有）」。

**版本条**：抽屉底部横向列出该条目所有版本，点一下切换当前选用版（旧图不删，随时可退）。`prompt_stale` 为真时提示「提示词在上次出图后改过，当前图不代表最新提示词」。

**读图**：一律走 `mediaFileUrl(novel, path)`（即 `/api/media/file?novel=&path=`），不直接拼磁盘路径；后端会校验越界与非图片后缀。
