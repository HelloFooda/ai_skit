"""真实调用跑一遍 /generate 主路径，落盘完整 JSON + 打印机检摘要。

用法（在 backend/ 下执行）：.venv/bin/python tests/manual/run_real.py <max_groups> [target_duration]
"""

import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from app.llm import extract_assets

ROOT = BACKEND_ROOT.parent
CHAPTER = (ROOT / "剧集" / "EP001" / "输入" / "第1章.txt").read_text(encoding="utf-8")

max_groups = int(sys.argv[1]) if len(sys.argv) > 1 else 6
target = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

print(f"章节字数 {len(CHAPTER)} | max_groups={max_groups} | target={target}s", flush=True)
t0 = time.time()
res = extract_assets(CHAPTER, mode="auto", max_groups=max_groups, target_duration=target)
elapsed = time.time() - t0

p = res.package
print(f"\n耗时 {elapsed:.1f}s | 真实调用={not res.mock} | llm_calls={res.llm_calls}")
print(f"技能装载 {res.skills_loaded} | refs 展开 {res.skills_refs_expanded}")
print("分段:", res.stages)
print(f"\n五类计数: 角色 {len(p.characters)} / 道具 {len(p.props)} / 场景 {len(p.scenes)}"
      f" / 站位 {len(p.blockings)} / 分镜组 {len(p.storyboards)}")
print(f"total_groups_estimated={res.total_groups_estimated} truncated={res.truncated}")

issues = res.gate_issues
print(f"\n机检告警 {len(issues)} 条:")
for i in issues:
    print(f"  [{i.code}] {i.group_id} {i.detail}")

names = {s.name for s in p.scenes}
print("\nlocation 对账:")
for g in p.storyboards:
    ok = "✓" if g.location in names else "✗ 不在场景表"
    print(f"  {g.group_id} {g.duration}s / 实测 {g.shots_total}s / {len(g.shots)}镜 / {g.location} {ok}")

out = ROOT / "剧集" / "EP001" / "产出"
out.mkdir(parents=True, exist_ok=True)
stamp = time.strftime("%H%M%S")
path = out / f"run_{stamp}_g{max_groups}.json"
payload = {
    "run_id": path.stem,
    "latency_ms": res.latency_ms,
    "model_calls": res.llm_calls,
    "prompt_version": res.prompt_version,
    "skills_loaded": res.skills_loaded,
    "stages": res.stages,
    "total_groups_estimated": res.total_groups_estimated,
    "truncated": res.truncated,
    "gate_issues": [i.model_dump() for i in issues],
    "package": p.model_dump(),
}
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已落盘: {path}")
