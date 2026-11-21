"""LangChain 通道回归测试：提示词逐字等价、模型工厂、消息映射、用量归因、并发上限。

跑法（backend/ 目录）：uv run python -m unittest discover -s tests -v

`prompt_baseline_v7.py` 是从 git 里迁移前版本提取的提示词基线，用它证明
「换成 ChatPromptTemplate 只换载体、没改一个字」——这是台词零容错的延伸。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.generation import chains, llm, prompts
from tests.unit import prompt_baseline_v7 as baseline

CHAPTER = "林桂芳推开老饭馆的木门，铜钥匙在掌心攥得发烫。"


class PromptEquivalenceTests(unittest.TestCase):
    """模板渲染结果必须与迁移前的字符串拼接逐字相同。"""

    def test_asset_system_is_byte_identical_to_v7_baseline(self):
        skills = ("", "  <skill name=\"x\">规则 {{ 变量 }}</skill>  ")
        for only in (None, "characters", "props", "scenes"):
            for skill in skills:
                with self.subTest(only=only, skill=bool(skill)):
                    self.assertEqual(
                        baseline.asset_system(only, skill),
                        prompts.render_asset_system(only, skill),
                    )

    def test_visual_system_is_byte_identical_to_v7_baseline(self):
        cases = [
            ("", 6, 15.0, None),
            ("<s>分镜技能</s>", 20, 12.5, None),
            ("", 6, 15.0, ["街口"]),
            ("<s>", 6, 15.0, ["街口", "饭馆"]),
        ]
        for skill, max_groups, duration, scenes in cases:
            with self.subTest(max_groups=max_groups, scenes=scenes):
                self.assertEqual(
                    baseline.visual_system(skill, max_groups, duration, scenes),
                    prompts.render_visual_system(skill, max_groups, duration, scenes),
                )

    def test_prompt_version_not_bumped_for_carrier_only_change(self):
        """正文没变就不升版：升版等于宣告要重跑全量评测基线。"""
        self.assertEqual(llm.PROMPT_VERSION, "v7")

    def test_skill_section_with_jinja_syntax_is_not_reparsed(self):
        """技能正文是变量值，jinja2 不递归渲染变量值。"""
        tricky = "<skill>示例 {{ 占位符 }} 与 {% if x %}</skill>"
        rendered = prompts.render_asset_system(None, tricky)
        self.assertIn("{{ 占位符 }}", rendered)
        self.assertIn("{% if x %}", rendered)

    def test_chapter_never_goes_through_template_engine(self):
        """不可信素材只进 HumanMessage，模板语法与引号都必须原样保留。"""
        tricky = CHAPTER + ' {{ ignore_me }} {% if x %} "引号" \\n'
        user = llm.build_messages(tricky)[1]["content"]
        self.assertIn(tricky, user)
        self.assertIn("{{ ignore_me }}", user)
        self.assertIn('"引号"', user)


class ModelFactoryTests(unittest.TestCase):
    def tearDown(self):
        chains.clear_model_cache()

    def test_openai_compatible_endpoint_is_config_driven(self):
        with (
            patch.object(settings, "provider", "openai"),
            patch.object(settings, "model", "deepseek-flash"),
            patch.object(settings, "base_url", "https://api.deepseek.com"),
            patch.object(settings, "api_key", "test-key"),
            patch.object(settings, "temperature", 0.3),
            patch.object(settings, "timeout", 45.0),
            patch.object(settings, "max_retries", 4),
        ):
            chains.clear_model_cache()
            model = chains.build_chat_model()
            self.assertIsInstance(model, ChatOpenAI)
            self.assertEqual(model.model_name, "deepseek-flash")
            self.assertEqual(str(model.openai_api_base).rstrip("/"), "https://api.deepseek.com")
            self.assertEqual(model.temperature, 0.3)
            self.assertEqual(model.request_timeout, 45.0)
            self.assertEqual(model.max_retries, 4)
            # 同一份配置复用同一对象，不反复建 HTTP client
            self.assertIs(model, chains.build_chat_model())

    def test_non_openai_provider_delegates_to_init_chat_model(self):
        sentinel = object()
        with (
            patch.object(settings, "provider", "anthropic"),
            patch.object(settings, "api_key", "test-key"),
            patch.object(chains, "init_chat_model", return_value=sentinel) as factory,
        ):
            chains.clear_model_cache()
            self.assertIs(chains.build_chat_model(), sentinel)
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["model_provider"], "anthropic")
        self.assertNotIn("base_url", kwargs)
        self.assertEqual(factory.call_args.args[0], settings.model)

    def test_api_key_never_appears_in_cache_key(self):
        with patch.object(settings, "api_key", "super-secret-xyz"):
            self.assertNotIn("super-secret-xyz", repr(chains._model_key()))


class MessageMappingTests(unittest.TestCase):
    def test_roles_map_to_distinct_message_classes(self):
        messages = chains._to_messages(
            [{"role": "system", "content": "规则"}, {"role": "user", "content": CHAPTER}]
        )
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIsInstance(messages[1], HumanMessage)
        # 章节逐字透传，不清洗、不截断
        self.assertEqual(messages[1].content, CHAPTER)

    def test_unknown_role_is_rejected_not_silently_downgraded(self):
        with self.assertRaises(ValueError):
            chains._to_messages([{"role": "tool", "content": "x"}])

    def test_ai_message_to_text(self):
        fenced = '```json\n{"a": 1}\n```'
        self.assertEqual(chains._to_text(AIMessage(content=fenced)), fenced)
        blocks = AIMessage(
            content=[
                {"type": "text", "text": "abc"},
                {"type": "image", "text": "ignored"},
            ]
        )
        self.assertEqual(chains._to_text(blocks), "abc")


class UsageCollectorTests(unittest.TestCase):
    def test_usage_metadata_wins_and_llm_output_is_not_double_counted(self):
        collector = chains.UsageCollector()
        generation = ChatGeneration(
            message=AIMessage(
                content="x",
                usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            )
        )
        collector.on_llm_end(
            LLMResult(
                generations=[[generation]],
                llm_output={"token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
            ),
            run_id=uuid4(),
        )
        self.assertEqual(
            collector.as_dict(),
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "llm_calls": 1,
                "llm_errors": 0,
            },
        )

    def test_falls_back_to_llm_output_when_no_usage_metadata(self):
        collector = chains.UsageCollector()
        collector.on_llm_end(
            LLMResult(
                generations=[[ChatGeneration(message=AIMessage(content="x"))]],
                llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}},
            ),
            run_id=uuid4(),
        )
        self.assertEqual(collector.as_dict()["input_tokens"], 7)
        self.assertEqual(collector.as_dict()["total_tokens"], 10)

    def test_errors_are_counted(self):
        collector = chains.UsageCollector()
        collector.on_llm_error(RuntimeError("boom"), run_id=uuid4())
        self.assertEqual(collector.as_dict()["llm_errors"], 1)


class RunnableConfigTests(unittest.TestCase):
    def test_config_has_tags_metadata_and_no_secret(self):
        with patch.object(settings, "api_key", "super-secret"):
            config = chains.run_config(
                stage="assets", mode="auto", prompt_version="v7", max_concurrency=2
            )
        self.assertEqual(config["run_name"], "ai_skit.assets")
        self.assertIn("stage:assets", config["tags"])
        self.assertIn("prompt:v7", config["tags"])
        self.assertEqual(config["metadata"]["stage"], "assets")
        self.assertEqual(config["max_concurrency"], 2)
        self.assertNotIn("super-secret", repr(config))


class _RecordingChain:
    """假 LCEL 链：记录 messages 与 RunnableConfig，返回最小合法 JSON。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _reply(self, messages, config):
        self.calls.append({"messages": messages, "config": config})
        system = messages[0]["content"]
        if "分镜师" in system:
            payload = {"total_groups_estimated": 1, "storyboards": []}
        elif any(f"只输出 `{k}` 这一个键" in system for k in ("characters", "props", "scenes")):
            key = next(k for k in ("characters", "props", "scenes") if f"只输出 `{k}` 这一个键" in system)
            payload = {key: []}
        else:
            payload = {"characters": [], "props": [], "scenes": []}
        return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"

    def invoke(self, messages, config=None):
        return self._reply(messages, config)

    def batch(self, batches, config=None):
        return [self._reply(m, config) for m in batches]


class LangChainCallPathTests(unittest.TestCase):
    """主路径：两种调用都带 RunnableConfig，stages 带按段 token 归因。"""

    def _extract(self, mode="auto", **kwargs):
        fake = _RecordingChain()
        with (
            patch.object(settings, "mock", False),
            patch.object(settings, "api_key", "test-key"),
            patch.object(llm, "_client", return_value=fake),
        ):
            return llm.extract_assets(CHAPTER, mode=mode, **kwargs), fake

    def test_invoke_carries_stage_tags_and_prompt_version(self):
        _, fake = self._extract("assets_only")
        config = fake.calls[0]["config"]
        self.assertEqual(config["run_name"], "ai_skit.assets")
        self.assertEqual(config["metadata"]["stage"], "assets")
        self.assertIn("prompt:v7", config["tags"])

    def test_two_stages_are_tagged_separately(self):
        _, fake = self._extract("auto")
        stages = [call["config"]["metadata"]["stage"] for call in fake.calls]
        self.assertEqual(stages, ["assets", "visual"])

    def test_per_type_uses_batch_with_concurrency_cap(self):
        result, fake = self._extract("per_type")
        self.assertEqual(result.llm_calls, 3)
        self.assertEqual(len(fake.calls), 3)
        for call in fake.calls:
            self.assertEqual(call["config"]["max_concurrency"], settings.max_concurrency)

    def test_stages_carry_usage_breakdown(self):
        result, _ = self._extract("auto")
        self.assertEqual(len(result.stages), 2)
        for stage in result.stages:
            self.assertIn("usage", stage)
            self.assertEqual(
                set(stage["usage"]),
                {"input_tokens", "output_tokens", "total_tokens", "llm_calls", "llm_errors"},
            )

    def test_mock_path_does_not_fake_usage(self):
        """没调用就不能虚报 token 用量，mock 的 stages 保持原样。"""
        with patch.object(settings, "mock", True):
            result = llm.extract_assets(CHAPTER)
        self.assertTrue(result.mock)
        self.assertEqual(result.llm_calls, 0)
        for stage in result.stages:
            self.assertNotIn("usage", stage)


class RealLCELCallbackTests(unittest.TestCase):
    """用 LangChain 内置假模型跑**真实 RunnableSequence**，验证回调不会假阴性。

    手写假链不触发 `on_llm_end`，只能证明编排顺序；这组用真的 ChatModel
    Runnable 跑完整链路，才能证明 UsageCollector 确实挂在 config 上并采到
    非零 token。零成本、不联网。
    """

    def setUp(self):
        chains.clear_model_cache()

    def tearDown(self):
        chains.clear_model_cache()

    def test_full_pipeline_fires_usage_callback_on_real_runnable(self):
        assets_json = '```json\n{"characters": [], "props": [], "scenes": []}\n```'
        visual_json = '```json\n{"total_groups_estimated": 1, "storyboards": []}\n```'
        fake_model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=assets_json,
                    usage_metadata={"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
                ),
                AIMessage(
                    content=visual_json,
                    usage_metadata={"input_tokens": 22, "output_tokens": 5, "total_tokens": 27},
                ),
            ]
        )
        with (
            patch.object(chains, "build_chat_model", return_value=fake_model),
            patch.object(settings, "mock", False),
            patch.object(settings, "api_key", "test-key"),
        ):
            chains.clear_model_cache()
            result = llm.extract_assets(CHAPTER)  # mode=auto，两段各一次

        self.assertFalse(result.mock)
        self.assertEqual(result.llm_calls, 2)
        self.assertEqual([s["stage"] for s in result.stages], ["assets", "visual"])
        assets_usage, visual_usage = (s["usage"] for s in result.stages)
        self.assertEqual(assets_usage["llm_calls"], 1)
        self.assertEqual(assets_usage["input_tokens"], 11)
        self.assertEqual(assets_usage["total_tokens"], 14)
        self.assertEqual(visual_usage["llm_calls"], 1)
        self.assertEqual(visual_usage["input_tokens"], 22)
        self.assertEqual(visual_usage["total_tokens"], 27)


if __name__ == "__main__":
    unittest.main()
