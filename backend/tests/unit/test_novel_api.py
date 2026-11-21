"""多章编排接口回归：两字段请求、按章响应、非法输入 400。"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

SOURCE = "第一章：雨夜\n林桂芳推开木门。\n第二章：铜钥匙\n沈砚舟掏出怀表。\n"


class NovelApiTests(unittest.TestCase):
    def setUp(self):
        # 本机 .env 里有真实密钥，接口测试必须强制 mock，避免跑单测打真实模型
        self._mock = patch.object(settings, "mock", True)
        self._key = patch.object(settings, "api_key", "")
        self._mock.start()
        self._key.start()
        self.addCleanup(self._mock.stop)
        self.addCleanup(self._key.stop)
        self.client = TestClient(app)

    def test_generate_novel_returns_per_chapter_packages(self):
        res = self.client.post(
            "/api/pipeline/generate-novel",
            json={"novel_title": "测试小说", "content": SOURCE},
        )
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["novel_title"], "测试小说")
        self.assertEqual(body["chapter_count"], 2)
        self.assertEqual([c["index"] for c in body["chapters"]], [1, 2])
        self.assertEqual(
            [c["title"] for c in body["chapters"]], ["第一章：雨夜", "第二章：铜钥匙"]
        )
        self.assertTrue(body["run_id"])
        self.assertEqual(body["total_llm_calls"], sum(c["llm_calls"] for c in body["chapters"]))
        self.assertEqual(
            body["total_characters"], sum(len(c["characters"]) for c in body["chapters"])
        )

    def test_old_single_chapter_endpoint_still_works(self):
        res = self.client.post(
            "/api/pipeline/generate", json={"chapter": "林桂芳推开老饭馆的木门，铜钥匙在掌心。"}
        )
        self.assertEqual(res.status_code, 200, res.text)

    def test_missing_heading_is_400(self):
        res = self.client.post(
            "/api/pipeline/generate-novel",
            json={"novel_title": "测试小说", "content": "这是一段没有章节标题的正文内容。"},
        )
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIn("章节标题", res.json()["detail"])

    def test_body_before_first_heading_is_400(self):
        res = self.client.post(
            "/api/pipeline/generate-novel",
            json={
                "novel_title": "测试小说",
                "content": "序幕：有事发生。\n第一章：开头\n正文开始了。",
            },
        )
        self.assertEqual(res.status_code, 400, res.text)

    def test_visual_only_not_allowed_for_multi_chapter(self):
        res = self.client.post(
            "/api/pipeline/generate-novel",
            json={"novel_title": "测试", "content": SOURCE, "mode": "visual_only"},
        )
        self.assertEqual(res.status_code, 422, res.text)


if __name__ == "__main__":
    unittest.main()
