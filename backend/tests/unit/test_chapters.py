"""章节切分回归：边界识别确定性 + 逐字回拼零容错。"""

import unittest

from app.generation.chapters import (
    MAX_CHAPTERS,
    Chapter,
    split_chapters_text,
    split_chapters_tool,
)


class ChapterSplitTests(unittest.TestCase):
    def test_splits_two_chapters_verbatim(self):
        source = (
            "第一章：雨夜来客\n"
            "林桂芳推开木门，铜钥匙在掌心攥得发烫。\n\n"
            "第二章 铜钥匙\n"
            "沈砚舟低头擦着怀表：「你怎么来了。」\n"
        )
        chapters = split_chapters_text(source)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "第一章：雨夜来客")
        self.assertEqual(chapters[1].title, "第二章 铜钥匙")
        self.assertEqual("".join(c.raw for c in chapters), source)
        self.assertIn("你怎么来了。」", chapters[1].text)

    def test_body_mention_of_chapter_word_is_not_a_boundary(self):
        source = (
            "第一章：闲谈\n"
            "他随口说，第三章才是重头戏。\n"
            "她没接话，第三章的时候天已经黑了。\n"
        )
        chapters = split_chapters_text(source)
        self.assertEqual(len(chapters), 1)
        self.assertEqual("".join(c.raw for c in chapters), source)

    def test_chinese_and_arabic_numerals(self):
        source = "第11章：十一\n正文十一。\n第十一章：中文\n正文中文。\n"
        chapters = split_chapters_text(source)
        self.assertEqual([c.title for c in chapters], ["第11章：十一", "第十一章：中文"])

    def test_leading_whitespace_is_preserved(self):
        source = "\n  第一章：开头\n正文。\n"
        chapters = split_chapters_text(source)
        self.assertEqual("".join(c.raw for c in chapters), source)
        self.assertTrue(chapters[0].raw.startswith("\n  "))

    def test_decorated_heading_from_paste_is_recognized(self):
        """从阅读器 / 笔记软件粘贴的「📖 第一章」带符号前缀，仍要认作标题行。"""
        source = (
            "📖 序幕：天外飞仙\n"
            "掌门在山巅占卜。\n"
            "📖 第一章：客栈柴房温暖如春\n"
            "王陆在客栈里宅了六天。\n"
        )
        chapters = split_chapters_text(source)
        self.assertEqual([c.title for c in chapters], ["序幕：天外飞仙", "第一章：客栈柴房温暖如春"])
        # 装饰符号属于切片的一部分，逐字回拼仍严格相等
        self.assertTrue(chapters[0].raw.startswith("📖 序幕"))
        self.assertEqual("".join(c.raw for c in chapters), source)

    def test_markdown_hash_heading_is_recognized(self):
        source = "## 第一章：开头\n正文。\n"
        chapters = split_chapters_text(source)
        self.assertEqual(chapters[0].title, "第一章：开头")
        self.assertEqual("".join(c.raw for c in chapters), source)

    def test_conventional_section_names(self):
        source = "楔子\n引子正文。\n第一章：正文\n小说开头。\n番外：后来\n多年以后。\n"
        chapters = split_chapters_text(source)
        self.assertEqual([c.title for c in chapters], ["楔子", "第一章：正文", "番外：后来"])
        self.assertEqual("".join(c.raw for c in chapters), source)

    def test_body_sentence_starting_with_section_name_is_not_a_boundary(self):
        source = "第一章：正文\n序幕里发生的事，他记了三十年。\n"
        chapters = split_chapters_text(source)
        self.assertEqual(len(chapters), 1)
        self.assertEqual("".join(c.raw for c in chapters), source)

    def test_no_heading_rejected(self):
        with self.assertRaises(ValueError):
            split_chapters_text("这是一段没有章节标题的小说正文。")

    def test_body_before_first_heading_rejected(self):
        with self.assertRaises(ValueError):
            split_chapters_text("序幕里发生的事。\n第一章：开头\n正文。\n")

    def test_empty_chapter_rejected(self):
        with self.assertRaises(ValueError):
            split_chapters_text("第一章：空\n第二章：实\n有正文。\n")

    def test_too_many_chapters_rejected(self):
        source = "".join(f"第{i}章\n正文{i}。\n" for i in range(1, MAX_CHAPTERS + 2))
        with self.assertRaises(ValueError):
            split_chapters_text(source)

    def test_tool_returns_exact_text_and_counts(self):
        source = "第一章：一\n正文一。\n第二章：二\n正文二。\n"
        payload = split_chapters_tool.invoke({"text": source})
        self.assertEqual(payload["count"], 2)
        self.assertTrue(payload["verbatim_ok"])
        self.assertEqual(payload["chapters"][0]["title"], "第一章：一")
        self.assertEqual([x["text"] for x in payload["chapters"]], ["\n正文一。\n", "\n正文二。\n"])

    def test_chapter_model_roundtrip(self):
        c = Chapter(index=1, title="第一章", text="正文", raw="第一章\n正文")
        self.assertEqual(Chapter.model_validate(c.model_dump()), c)


if __name__ == "__main__":
    unittest.main()
