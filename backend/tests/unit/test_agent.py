"""章内 create_agent 回归：工具形状、顺序闸门、原文隔离、模式分支。"""

import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.core.config import settings
from app.core.schemas import Character, ScriptPackage
from app.generation import agent as agent_module
from app.generation.graph import run_novel_pipeline

CHAPTER = "第一章：雨夜\n林桂芳推开木门，铜钥匙在掌心。\n"


def _package() -> ScriptPackage:
    return ScriptPackage(
        characters=[
            Character(
                name="林桂芳",
                appearance="衣着",
                stable_features="特征",
                prompt="描述",
            )
        ]
    )


def _fake_result(mode: str = "auto"):
    from app.generation.llm import ExtractionResult

    return ExtractionResult(
        package=_package(),
        mock=True,
        latency_ms=10,
        llm_calls=2 if mode == "auto" else 1,
        stages=[{"stage": "assets", "ms": 5}],
    )


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self._mock = patch.object(settings, "mock", True)
        self._key = patch.object(settings, "api_key", "")
        self._mock.start()
        self._key.start()
        self.addCleanup(self._mock.stop)
        self.addCleanup(self._key.stop)

    def test_tool_schemas_have_no_chapter_argument(self):
        for tool in agent_module.CHAPTER_TOOLS:
            self.assertNotIn("chapter", tool.args)
            self.assertEqual(tool.args, {})

    def test_scripted_model_follows_sop_and_returns_package(self):
        result = agent_module.run_chapter_agent(CHAPTER, {"mode": "auto"})
        self.assertEqual(result["tool_trace"], ["plan_assets", "write_storyboard"])
        self.assertGreaterEqual(len(result["package"].characters), 1)
        # mock 分支不计真实生成调用，但脚本模型的编排决策仍被计数
        self.assertEqual(result["llm_calls"], 0)
        self.assertEqual(result["agent_calls"], 3)
        self.assertTrue(result["mock"])

    def test_assets_only_stops_after_first_tool(self):
        result = agent_module.run_chapter_agent(CHAPTER, {"mode": "assets_only"})
        self.assertEqual(result["tool_trace"], ["plan_assets"])
        self.assertEqual(result["package"].storyboards, [])

    def test_gate_rejects_storyboard_without_assets(self):
        # 只发一次分镜调用；闸门拒绝后模型以纯文本收尾，验证工具没有执行
        model = agent_module.scripted_model(
            [
                agent_module._tool_call("write_storyboard", "bad-1"),
                AIMessage(content="我改为先盘点资产。"),
            ]
        )
        agent = agent_module.create_agent(
            model=model,
            tools=agent_module.CHAPTER_TOOLS,
            state_schema=agent_module.ChapterAgentState,
            middleware=[agent_module.asset_first_gate],
        )
        outcome = agent.invoke(agent_module._empty_state(CHAPTER, {"mode": "auto"}))

        self.assertIsNone(outcome["result"])
        self.assertFalse(outcome["tool_trace"])
        rejections = [
            m.content
            for m in outcome["messages"]
            if isinstance(m, ToolMessage) and "拒绝" in m.content
        ]
        self.assertTrue(rejections)

    def test_chapter_text_reaches_state_verbatim(self):
        captured: dict = {}

        def fake_extract(chapter: str, **kwargs):
            captured["chapter"] = chapter
            return _fake_result(kwargs.get("mode"))

        with patch.object(agent_module.llm, "extract_assets", side_effect=fake_extract):
            agent_module.run_chapter_agent(CHAPTER, {"mode": "auto"})

        self.assertEqual(captured["chapter"], CHAPTER)

    def test_per_type_mode_is_preserved_in_asset_tool(self):
        seen: list[str] = []

        def fake_extract(chapter: str, **kwargs):
            seen.append(kwargs["mode"])
            return _fake_result(kwargs["mode"])

        with patch.object(agent_module.llm, "extract_assets", side_effect=fake_extract):
            agent_module.run_chapter_agent(CHAPTER, {"mode": "per_type"})

        self.assertEqual(seen, ["per_type"])

    def test_no_tool_call_raises_instead_of_empty_package(self):
        model = agent_module.scripted_model([AIMessage(content="我不想调用工具。")])
        with (
            patch.object(agent_module, "_build_agent_model", return_value=model),
            self.assertRaises(ValueError),
        ):
            agent_module.run_chapter_agent(CHAPTER, {"mode": "auto"})


class GraphWithAgentTests(unittest.TestCase):
    """图节点确实经过 agent 入口，且按章循环与聚合口径不变。"""

    def test_graph_delegates_each_chapter_to_agent(self):
        seen: list[str] = []

        def fake(chapter: str, params: dict, run_id: str = "-"):
            seen.append(chapter)
            return {
                "package": _package(),
                "mock": True,
                "latency_ms": 10,
                "prompt_version": "v7",
                "mode": params.get("mode", "auto"),
                "llm_calls": 2,
                "hints_used": [],
                "injection_warning": False,
                "skills_loaded": [],
                "skills_skipped": [],
                "skills_refs_expanded": 0,
                "skills_refs_skipped": 0,
                "stages": [],
                "gate_issues": [],
                "total_groups_estimated": 0,
                "truncated": False,
                "tool_trace": ["plan_assets", "write_storyboard"],
            }

        source = "第一章：一\n甲在雨里。\n第二章：二\n乙掏出钥匙。\n"
        with patch("app.generation.graph.generate_chapter", side_effect=fake):
            final = run_novel_pipeline("书名", source, {"mode": "auto"})

        self.assertEqual(len(seen), 2)
        self.assertEqual(final["chapter_count"], 2)
        self.assertEqual(final["total_llm_calls"], 4)


if __name__ == "__main__":
    unittest.main()
