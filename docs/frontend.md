# 前端技术文档

面向开发者：运行方式、组件结构、API 封装、响应字段。产品效果见[根 README](../README.md)，后端契约见[后端技术文档](backend.md)。

前端是一个 Vue 3 + Vite + TypeScript 的最小闭环：输入小说名与若干章原文 → 调后端 LangGraph 编排 → 按章切换并分栏展示五类结果。核心只有三个源码文件，没有路由、状态库或 UI 框架。

## 运行方式

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，/api 已代理到后端 8000
```

其余脚本：`npm run build`（先 `vue-tsc --noEmit` 再 `vite build`）、`npm run preview`（预览构建产物）。后端需先启动，配置见[后端技术文档](backend.md#开发环境uv)。

技术栈版本见 `package.json`：Vue 3.5、Vite 5.4、TypeScript 5.6、`@vitejs/plugin-vue` 5.1。

## 源码结构

```
frontend/
├── src/
│   ├── App.vue          # 唯一页面：输入、高级设置、技能勾选、结果展示
│   ├── api.ts           # 类型定义 + 两个 fetch 封装
│   ├── main.ts          # 挂载 Vue 应用
│   └── vite-env.d.ts    # Vite 环境类型
├── index.html
├── vite.config.ts       # /api 代理到 localhost:8000
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

都走相对路径 `/api/...`，由 Vite 代理到 8000。非 2xx 时解析 `detail` 字段作为错误信息抛出。

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
