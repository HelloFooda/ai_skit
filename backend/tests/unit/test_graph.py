"""LangGraph 编排回归：按章回环顺序、聚合口径、失败即停。"""

import unittest
from unittest.mock import patch

from app.core.schemas import Character, ScriptPackage
from app.generation import graph as graph_module

SOURCE = "第一章：雨夜\n甲在雨里。\n第二章：铜钥匙\n乙掏出钥匙。\n第三章：收尾\n丙收好表。\n"


def _result(name: str) -> dict:
    """章内 agent 的返回契约：dict（与 agent.run_chapter_agent 对齐）。"""
    return {
        "package": ScriptPackage(
            characters=[Character(name=name, appearance="衣着", stable_features="特征", prompt="描述")]
        ),
        "mock": True,
        "latency_ms": 10,
        "prompt_version": "v7",
        "mode": "auto",
        "llm_calls": 2,
        "hints_used": [],
        "injection_warning": False,
        "skills_loaded": [],
        "skills_skipped": [],
        "skills_refs_expanded": 0,
        "skills_refs_skipped": 0,
        "stages": [{"stage": "assets", "ms": 5}],
        "gate_issues": [],
        "total_groups_estimated": 0,
        "truncated": False,
        "tool_trace": ["plan_assets", "write_storyboard"],
    }


class NovelGraphTests(unittest.TestCase):
    def test_loops_once_per_chapter_in_order(self):
        seen: list[str] = []

        def fake(chapter: str, params: dict, run_id: str = "-"):
            seen.append(chapter)
            return _result(f"角色{len(seen)}")

        with patch.object(graph_module, "generate_chapter", side_effect=fake):
            final = graph_module.run_novel_pipeline("测试小说", SOURCE, {"mode": "auto"})

        self.assertEqual(len(seen), 3)
        self.assertIn("甲在雨里", seen[0])
        self.assertIn("乙掏出钥匙", seen[1])
        self.assertIn("丙收好表", seen[2])
        self.assertEqual(final["novel_title"], "测试小说")
        self.assertEqual(final["chapter_count"], 3)
        self.assertEqual([c["index"] for c in final["chapters"]], [1, 2, 3])
        self.assertEqual([c["title"] for c in final["chapters"]], ["第一章：雨夜", "第二章：铜钥匙", "第三章：收尾"])
        self.assertEqual(final["total_llm_calls"], 6)
        self.assertEqual(final["total_latency_ms"], 30)
        self.assertEqual(final["total_characters"], 3)

    def test_params_passed_to_each_chapter(self):
        seen: list[dict] = []

        def fake(chapter: str, params: dict, run_id: str = "-"):
            seen.append(params)
            return _result("甲")

        params = {"mode": "assets_only", "character_hint": "只看外貌"}
        with patch.object(graph_module, "generate_chapter", side_effect=fake):
            graph_module.run_novel_pipeline("书名", "第一章：一\n正文。\n", params)

        self.assertEqual(seen, [params])

    def test_failure_in_middle_chapter_stops_graph(self):
        calls = []

        def fake(chapter: str, params: dict, run_id: str = "-"):
            calls.append(chapter)
            if len(calls) == 2:
                raise RuntimeError("模型调用失败")
            return _result("甲")

        with (
            patch.object(graph_module, "generate_chapter", side_effect=fake),
            self.assertRaises(RuntimeError),
        ):
            graph_module.run_novel_pipeline("书名", SOURCE)

        self.assertEqual(len(calls), 2)

    def test_route_condition(self):
        state = {"chapters": [{}, {}], "chapter_index": 1}
        self.assertEqual(graph_module.route_after_generate(state), "generate")
        state["chapter_index"] = 2
        self.assertEqual(graph_module.route_after_generate(state), "finalize")

    def test_split_error_propagates_before_any_model_call(self):
        with (
            patch.object(graph_module, "generate_chapter") as mock_generate,
            self.assertRaises(ValueError),
        ):
            graph_module.run_novel_pipeline("书名", "没有章节标题的正文。")
        mock_generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
