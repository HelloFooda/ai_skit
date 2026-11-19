"""长章分片：用已有资产表只重拆指定场次的分镜（mode=visual_only + scene_filter）。

用法（在 backend/ 下执行）：.venv/bin/python tests/manual/run_scene.py <assets_run.json> <场次1,场次2> [max_groups]
"""

import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from app.llm import extract_assets
from app.schemas import Assets

ROOT = BACKEND_ROOT.parent
CHAPTER = (ROOT / "剧集" / "EP001" / "输入" / "第1章.txt").read_text(encoding="utf-8")

src = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
# 兼容两种落盘形状：{package:{...}} 与直接的资产三类
assets = Assets.model_validate(src.get("package", src))
scenes = [s.strip() for s in sys.argv[2].split(",") if s.strip()]
max_groups = int(sys.argv[3]) if len(sys.argv) > 3 else 6

print(f"复用资产：角色 {len(assets.characters)} / 道具 {len(assets.props)} / 场景 {len(assets.scenes)}")
print(f"只拆场次：{scenes} | max_groups={max_groups}", flush=True)
unknown = [s for s in scenes if s not in {x.name for x in assets.scenes}]
if unknown:
    raise SystemExit(f"场次名不在资产表里：{unknown}")

t0 = time.time()
res = extract_assets(
    CHAPTER,
    mode="visual_only",
    assets=assets,
    scene_filter=scenes,
    max_groups=max_groups,
    target_duration=15.0,
)
print(f"\n耗时 {time.time() - t0:.1f}s | llm_calls={res.llm_calls} | stages={res.stages}")
print(f"组数 {len(res.package.storyboards)} | total_groups_estimated={res.total_groups_estimated}")
print(f"机检告警 {len(res.gate_issues)} 条")
for i in res.gate_issues:
    print(f"  [{i.code}] {i.group_id} {i.detail}")
for g in res.package.storyboards:
    print(f"  {g.group_id} {g.duration}s/{g.shots_total}s {len(g.shots)}镜 {g.location} | {g.summary}")

out = ROOT / "剧集" / "EP001" / "产出"
stamp = time.strftime("%H%M%S")
path = out / f"scene_{stamp}.json"
path.write_text(
    json.dumps(
        {
            "mode": res.mode,
            "latency_ms": res.latency_ms,
            "scene_filter": scenes,
            "total_groups_estimated": res.total_groups_estimated,
            "gate_issues": [i.model_dump() for i in res.gate_issues],
            "package": res.package.model_dump(),
        },
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"\n已落盘: {path}")
