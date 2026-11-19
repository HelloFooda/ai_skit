"""把真实调用的三段产物（资产 / 前半章分镜 / 后半章分镜）合成一份人工可读的五类报告。

用法（在 backend/ 下执行）：.venv/bin/python tests/manual/build_report.py <assets.json> <分镜1.json> [<分镜2.json> ...]
"""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas import (
    Assets,
    ScriptPackage,
    StoryboardGroup,
    build_blockings,
    check_storyboards,
)

ROOT = BACKEND_ROOT.parent
OUT = ROOT / "剧集" / "EP001" / "真实跑产出.md"

statics = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assets = Assets.model_validate(statics.get("package", statics))

groups: list[StoryboardGroup] = []
for part in sys.argv[2:]:
    d = json.loads(Path(part).read_text(encoding="utf-8"))
    groups.extend(StoryboardGroup.model_validate(g) for g in d["package"]["storyboards"])

pkg = ScriptPackage(
    characters=assets.characters,
    props=assets.props,
    scenes=assets.scenes,
    blockings=build_blockings(groups),
    storyboards=groups,
)
issues = check_storyboards(groups)
est = [json.loads(Path(p).read_text(encoding="utf-8")).get("total_groups_estimated") for p in sys.argv[2:]]

lines: list[str] = []
add = lines.append

add("# 真实调用产出 · 第 1 章（deepseek-flash / PROMPT_VERSION v7）")
add("")
add("- 章节：`剧集/EP001/输入/第1章.txt`（4542 字）")
add(f"- 产出：角色 {len(pkg.characters)} / 角色道具 {len(pkg.props)} / 场景 {len(pkg.scenes)}"
    f" / 站位 {len(pkg.blockings)} 组 / 分镜 {len(pkg.storyboards)} 组"
    f"（合计 {sum(g.shots_total for g in pkg.storyboards):.0f}s）")
add(f"- 机检告警：{len(issues)} 条" + ("——" + "；".join(f"{i.code}@{i.group_id}" for i in issues) if issues else "（全过）"))
add("- 生成方式：段1 `short-drama-assets` + 段2 `short-drama-storyboard`，站位由 `build_blockings` 从分镜聚合")
add("")

add("## 一、角色")
for c in pkg.characters:
    add(f"### {c.name}")
    add(f"- 外貌服饰：{c.appearance}")
    add(f"- 稳定特征：{c.stable_features}")
    add(f"- 本集状态：{c.state or '（无变化）'}")
    add(f"- 声线：{c.voice or '（无台词）'}")
    add(f"- 生图提示词：{c.prompt}")
    add("")

add("## 二、角色道具")
for x in pkg.props:
    add(f"### {x.name}　（归属：{x.owner or '环境陈设'}）")
    add(f"- 外观：{x.appearance}")
    add(f"- 状态变化：{x.state_changes or '（无变化）'}")
    add(f"- 生图提示词：{x.prompt}")
    add("")

add("## 三、场景")
for s in pkg.scenes:
    add(f"### {s.name}")
    add(f"- 时间：{s.time}")
    add(f"- 光线：{s.light}")
    add(f"- 氛围：{s.atmosphere}")
    add(f"- 生图提示词：{s.prompt}")
    add("")

add("## 四、角色站位（由分镜每镜 blocking 聚合，不单独生成）")
for b in pkg.blockings:
    add(f"### {b.group_id} · {b.location}")
    for line in b.text.splitlines():
        add(f"- {line}")
    add("")

add("## 五、15s 分镜组表")
for g in pkg.storyboards:
    add(f"### {g.group_id} · {g.duration:g}s · {g.location}")
    add(f"{g.summary}")
    add("")
    add("| 镜 | 时长 | 景别/角度/运镜 | 目的 | 起点→动作→终点 | 台词 | 站位 |")
    add("|---|---|---|---|---|---|---|")
    for s in g.shots:
        cells = [
            str(s.shot_no),
            f"{s.duration:g}s",
            f"{s.shot_size}/{s.angle}/{s.camera}",
            s.function,
            s.content.replace("|", "｜"),
            (s.dialogue or "—").replace("|", "｜"),
            s.blocking.replace("|", "｜"),
        ]
        add("| " + " | ".join(cells) + " |")
    add("")

add("## 六、机检明细")
if issues:
    add("| 规则 | 组 | 说明 |")
    add("|---|---|---|")
    for i in issues:
        add(f"| {i.code} | {i.group_id} | {i.detail} |")
else:
    add("8 项算术与可拍性规则全部通过：各镜之和＝组时长、组落在 4–15s、单镜 ≥1.2s、location 命中场景表。")
add("")
add(f"分组预估：{'/'.join(str(e) for e in est)}（各分片自报的全章组数估计）")

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"写入 {OUT}：{len(lines)} 行")
print(f"角色 {len(pkg.characters)} 道具 {len(pkg.props)} 场景 {len(pkg.scenes)} "
      f"站位 {len(pkg.blockings)} 分镜 {len(pkg.storyboards)} 机检 {len(issues)}")
