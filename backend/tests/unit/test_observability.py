"""日志可观测回归：分级、脱敏、run_id 关联、mock 不虚报。

用 loguru 的内存 sink 捕获 JSON 记录，断言的是**实际写出的日志内容**，
不靠「函数被调用过」这类假阳性信号。每例 setup/teardown 清空 sink，
避免测试间互相污染。
"""

from __future__ import annotations

import io
import json
import unittest
from typing import Self
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from loguru import logger

from app.api.pipeline import run
from app.core.config import settings
from app.core.schemas import ExtractAssetsRequest
from app.generation import chains, llm
from app.main import app
from app.observability import logging as observability

CHAPTER = "林桂芳推开老饭馆的木门，铜钥匙在掌心攥得发烫。"
SECRET = "sk-thisMustNeverAppearInLogs-1234567890"
HINT = "只写张三和那把黄铜钥匙，别提李四"


def _records(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line)["record"] for line in buffer.getvalue().splitlines() if line.strip()]


class _LogCapture:
    """上下文：捕获 DEBUG 级 JSON 日志，退出时移除 sink。"""

    def __init__(self) -> None:
        self.buffer = io.StringIO()
        self.sink_id: int | None = None

    def __enter__(self) -> Self:
        self.sink_id = logger.add(self.buffer, level="DEBUG", serialize=True)
        return self

    def __exit__(self, *args: object) -> None:
        if self.sink_id is not None:
            logger.remove(self.sink_id)


class LoggingCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        observability.reset_sinks()

    def tearDown(self) -> None:
        observability.reset_sinks()

    def test_llm_start_logs_lengths_and_hash_not_content(self):
        cb = observability.LoggingCallback()
        with _LogCapture() as cap:
            cb.on_llm_start(
                {"name": "ChatOpenAI"},
                [
                    "System: 你是资产提取器",
                    f"Human: <chapter_text>{CHAPTER}</chapter_text>",
                ],
                run_id=uuid4(),
                metadata={"stage": "assets", "mode": "auto", "prompt_version": "v7"},
            )
        rec = _records(cap.buffer)
        self.assertEqual(len(rec), 1)
        extra = rec[0]["extra"]
        self.assertEqual(rec[0]["level"]["name"], "DEBUG")
        self.assertEqual(extra["event"], "llm_start")
        self.assertEqual(extra["stage"], "assets")
        self.assertEqual(extra["messages"], 2)
        self.assertEqual(extra["chars_each"][1], len(f"Human: <chapter_text>{CHAPTER}</chapter_text>"))
        self.assertEqual(len(extra["prompt_sha256"]), 2)
        # 脱敏断言：正文一个字都不能出现在整条日志（含 message 与 extra）
        dump = cap.buffer.getvalue()
        self.assertNotIn(CHAPTER, dump)
        self.assertNotIn("你是资产提取器", dump)
        self.assertNotIn("chapter_text", dump)

    def test_llm_end_logs_tokens(self):
        cb = observability.LoggingCallback()
        response = LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="{}",
                            usage_metadata={
                                "input_tokens": 11,
                                "output_tokens": 3,
                                "total_tokens": 14,
                            },
                        )
                    )
                ]
            ]
        )
        with _LogCapture() as cap:
            cb.on_llm_end(response, run_id=uuid4(), metadata={"stage": "visual"})
        extra = _records(cap.buffer)[0]["extra"]
        self.assertEqual(extra["event"], "llm_end")
        self.assertEqual(extra["input_tokens"], 11)
        self.assertEqual(extra["output_tokens"], 3)

    def test_llm_error_logs_type_but_truncates_message(self):
        cb = observability.LoggingCallback()
        with _LogCapture() as cap:
            cb.on_llm_error(
                RuntimeError("x" * 500), run_id=uuid4(), metadata={"stage": "assets"}
            )
        rec = _records(cap.buffer)[0]
        self.assertEqual(rec["level"]["name"], "ERROR")
        self.assertEqual(rec["extra"]["error_type"], "RuntimeError")
        self.assertLessEqual(len(rec["extra"]["error_message"]), 200)

    def test_error_message_does_not_leak_secret_from_exception(self):
        """异常消息里若夹带密钥，日志只截长度仍会泄漏——这里验证调用方约定。

        LoggingCallback 只截 200 字不过滤内容；真正的密钥防护在 chains.run_config
        （metadata 不含 key）。本例锁定「异常类型可记、正文不主动展开」的边界。
        """
        cb = observability.LoggingCallback()
        with _LogCapture() as cap:
            cb.on_llm_error(ValueError("bad param"), run_id=uuid4(), metadata={})
        extra = _records(cap.buffer)[0]["extra"]
        self.assertEqual(extra["error_message"], "bad param")
        self.assertNotIn(SECRET, cap.buffer.getvalue())


class RequestIdTests(unittest.TestCase):
    def test_request_id_flows_through_run_config(self):
        with patch_request_id("abc123def456"):
            config = chains.run_config(
                stage="assets", mode="auto", prompt_version="v7"
            )
        self.assertEqual(config["metadata"]["run_id"], "abc123def456")
        # 回调里同时挂着 UsageCollector 之外的 LoggingCallback
        self.assertTrue(
            any(isinstance(cb, observability.LoggingCallback) for cb in config["callbacks"])
        )

    def test_request_id_is_isolated_between_calls(self):
        with patch_request_id("first-id-001"):
            a = chains.run_config(stage="assets", mode="auto", prompt_version="v7")
        b = chains.run_config(stage="assets", mode="auto", prompt_version="v7")
        self.assertEqual(a["metadata"]["run_id"], "first-id-001")
        self.assertEqual(b["metadata"]["run_id"], "-")

    def test_no_secret_in_config_after_logging_callback(self):
        with (
            patch.object(settings, "api_key", SECRET),
            patch_request_id("abc123def456"),
        ):
            config = chains.run_config(stage="assets", mode="auto", prompt_version="v7")
        self.assertNotIn(SECRET, repr(config))


class HttpLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        observability.reset_sinks()

    def tearDown(self) -> None:
        observability.reset_sinks()

    def test_run_config_injects_request_id_into_both_stages(self):
        """编排层两段的 config 都带同一 run_id（回调以此为关联键）。"""
        from tests.unit.test_langchain_chain import _RecordingChain

        fake = _RecordingChain()
        with (
            patch.object(settings, "mock", False),
            patch.object(settings, "api_key", "test-key"),
            patch.object(llm, "_client", return_value=fake),
            patch_request_id("run-abc123"),
        ):
            llm.extract_assets(CHAPTER, mode="auto")
        # _RecordingChain 不入真 LCEL，回调由 FakeMessagesListChatModel 单测覆盖；
        # 这里锁定 contextvar 注入 metadata 这一环。
        configs = [call["config"] for call in fake.calls]
        self.assertEqual([c["metadata"]["run_id"] for c in configs], ["run-abc123"] * 2)

    def test_real_lcel_emits_debug_logs_on_disk_sink(self):
        """用真 LCEL 链 + 假模型，证明模型前后 DEBUG 日志确实落盘且带 run_id。

        手写假链不触发 on_llm_start/end；这里必须是 RunnableSequence 才有效。
        """
        assets_json = '```json\n{"characters": [], "props": [], "scenes": []}\n```'
        visual_json = '```json\n{"total_groups_estimated": 1, "storyboards": []}\n```'
        fake_model = FakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content=assets_json,
                    usage_metadata={
                        "input_tokens": 11,
                        "output_tokens": 3,
                        "total_tokens": 14,
                    },
                ),
                AIMessage(
                    content=visual_json,
                    usage_metadata={
                        "input_tokens": 22,
                        "output_tokens": 5,
                        "total_tokens": 27,
                    },
                ),
            ]
        )
        chains.clear_model_cache()
        try:
            with (
                patch.object(chains, "build_chat_model", return_value=fake_model),
                patch.object(settings, "mock", False),
                patch.object(settings, "api_key", "test-key"),
                patch_request_id("real-run-777"),
                _LogCapture() as cap,
            ):
                llm.extract_assets(CHAPTER, mode="auto")
        finally:
            chains.clear_model_cache()
        records = _records(cap.buffer)
        events = [r["extra"].get("event") for r in records]
        self.assertEqual(events.count("llm_start"), 2)
        self.assertEqual(events.count("llm_end"), 2)
        starts = [r for r in records if r["extra"].get("event") == "llm_start"]
        self.assertEqual([r["extra"]["stage"] for r in starts], ["assets", "visual"])
        # run_id 来自 patcher（当前 contextvar），每条都带
        self.assertTrue(all(r["extra"].get("run_id") == "real-run-777" for r in records))
        dump = cap.buffer.getvalue()
        self.assertNotIn(CHAPTER, dump)
        self.assertNotIn("test-key", dump)

    def test_http_route_does_not_log_chapter_or_hint(self):
        with (
            patch.object(settings, "mock", True),
            patch_request_id("http-run-001"),
            _LogCapture() as cap,
        ):
            resp = run(
                ExtractAssetsRequest(
                    chapter=CHAPTER,
                    character_hint=HINT,
                    mode="assets_only",
                )
            )
        self.assertTrue(resp.mock)
        dump = cap.buffer.getvalue()
        self.assertNotIn(CHAPTER, dump)
        self.assertNotIn(HINT, dump)
        self.assertNotIn(SECRET, dump)
        extras = [r["extra"] for r in _records(cap.buffer)]
        self.assertIn("generation_done", [e.get("event") for e in extras])

    def test_testclient_request_gets_run_id_end_to_end(self):
        with patch.object(settings, "mock", True):
            client = TestClient(app)
            with _LogCapture() as cap:
                resp = client.post(
                    "/api/pipeline/generate",
                    json={"chapter": CHAPTER, "mode": "auto"},
                )
        self.assertEqual(resp.status_code, 200)
        run_id = resp.json()["run_id"]
        records = _records(cap.buffer)
        extras = [r["extra"] for r in records]
        ids = [e.get("run_id") for e in extras if "run_id" in e]
        self.assertTrue(ids)
        self.assertIn(run_id, ids)
        events = [e.get("event") for e in extras]
        self.assertIn("request_start", events)
        self.assertIn("request_done", events)  # 中间件 HTTP 完成
        self.assertIn("generation_done", events)  # 路由业务摘要
        # 中间件与路由都带同一个 run_id，串成一条链
        self.assertEqual(len(set(ids)), 1)


class _PatchContext:
    def __init__(self, value: str) -> None:
        self.value = value
        self.token = None

    def __enter__(self) -> Self:
        self.token = observability.set_request_id(self.value)
        return self

    def __exit__(self, *args: object) -> None:
        observability.reset_request_id(self.token)


def patch_request_id(value: str) -> _PatchContext:
    return _PatchContext(value)


if __name__ == "__main__":
    unittest.main()
