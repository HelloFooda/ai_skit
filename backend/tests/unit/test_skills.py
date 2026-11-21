"""技能库测试：通用规范兼容性、类别装载、预算控制、references 内联展开。

这里刻意有一组针对**真实 skills 目录**的回归测试：技能库是外来资产，
加载器的约束一旦比通用规范更严，就会整批失效而单测全绿。

跑法（backend/ 目录）：
    uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import settings
from app.generation import llm
from app.knowledge import skills
from app.knowledge.skills import (
    UnknownSkillError,
    build_skill_section,
    load_all,
    load_skill,
    parse_frontmatter,
)

VALID = """---
name: {name}
description: {desc}
metadata:
  applies-to: {cats}
  version: "1.0"
---

# {name}

正文{extra}
"""


def write_skill(
    root: Path, name: str, cats: str = "characters", desc: str = "测试技能", extra: str = ""
) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(VALID.format(name=name, desc=desc, cats=cats, extra=extra), encoding="utf-8")
    return path


def write_raw(root: Path, name: str, text: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


class FrontmatterTests(unittest.TestCase):
    def test_parses_required_and_nested_fields(self):
        meta, body = parse_frontmatter(VALID.format(name="a-b", desc="说明", cats="props", extra=""))
        self.assertEqual(meta["name"], "a-b")
        self.assertEqual(meta["description"], "说明")
        self.assertEqual(meta["metadata.applies-to"], "props")
        self.assertEqual(meta["metadata.version"], "1.0")
        self.assertTrue(body.startswith("# a-b"))

    def test_missing_frontmatter_raises(self):
        with self.assertRaises(ValueError):
            parse_frontmatter("# 只有正文，没有 frontmatter")

    def test_quoted_values_are_unquoted(self):
        meta, _ = parse_frontmatter('---\nname: "x-y"\ndescription: \'带引号\'\n---\n正文')
        self.assertEqual(meta["name"], "x-y")
        self.assertEqual(meta["description"], "带引号")

    def test_folded_block_scalar_is_joined(self):
        """通用技能常把 description 写成 >- 折叠块，不能丢字。"""
        text = (
            "---\nname: fold-skill\ndescription: >-\n  第一句。\n  第二句。\n"
            "  第三句。\n---\n正文"
        )
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["description"], "第一句。 第二句。 第三句。")

    def test_literal_block_scalar_keeps_newlines(self):
        text = "---\nname: lit-skill\ndescription: |\n  第一行\n  第二行\n---\n正文"
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["description"], "第一行\n第二行")

    def test_multiline_plain_scalar_is_joined(self):
        """缩进续写的普通标量（没写 > 也没写 |）也要能拼回来。"""
        text = (
            "---\nname: plain-skill\ndescription: 前半句。\n  后半句。\n---\n正文"
        )
        meta, _ = parse_frontmatter(text)
        self.assertEqual(meta["description"], "前半句。 后半句。")

    def test_inline_list_and_comment_lines(self):
        """行内列表由 YAML 展开成列表，扁平化后按逗号拼回字符串。"""
        meta, _ = parse_frontmatter(
            "---\n# 注释\nname: a-b\ndescription: 说明\napplies-to: [characters, props]\n---\n正文"
        )
        self.assertEqual(meta["name"], "a-b")
        self.assertEqual(meta["applies-to"], "characters, props")

    def test_scalar_types_are_normalized_to_text(self):
        """YAML 会把 yes/no 读成 bool、1.0 读成 float；元数据字段统一要 str。"""
        meta, _ = parse_frontmatter("---\nname: x-y\ndescription: 说明\nno_ads: yes\n---\n正文")
        self.assertEqual(meta["no_ads"], "true")

    def test_invalid_yaml_is_reported_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "YAML"):
            parse_frontmatter("---\nname: [未闭合\n---\n正文")


class LoadSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_non_kebab_name(self):
        path = write_skill(self.root, "Character_Sheet")
        with self.assertRaisesRegex(ValueError, "kebab-case"):
            load_skill(path)

    def test_name_folder_mismatch_only_warns(self):
        """通用规范用目录名发现、name 引用；不一致不该让技能整条失效。"""
        path = write_skill(self.root, "folder-name")
        text = path.read_text(encoding="utf-8").replace("name: folder-name", "name: other-name")
        path.write_text(text, encoding="utf-8")
        skill = load_skill(path)
        self.assertEqual(skill.name, "other-name")
        self.assertTrue(any("不一致" in note for note in skill.notes))

    def test_missing_applies_to_defaults_to_manual(self):
        path = write_raw(
            self.root, "no-category", "---\nname: no-category\ndescription: 无类别\n---\n\n正文"
        )
        self.assertEqual(load_skill(path).categories, ("manual",))

    def test_unknown_category_warns_instead_of_rejecting(self):
        """别的词表不是错误：只提示，技能照样可用。"""
        path = write_skill(self.root, "other-vocab", cats="wizards")
        skill = load_skill(path)
        self.assertEqual(skill.categories, ("wizards",))
        self.assertTrue(any("词表" in note for note in skill.notes))

    def test_long_body_is_not_truncated(self):
        """正文截断会砍掉「完成标准」这类收尾约束，比不装更糟。"""
        path = write_skill(self.root, "long-skill", extra="长" * 8_000)
        skill = load_skill(path)
        self.assertNotIn("已截断", skill.body)
        self.assertGreater(len(skill.body), 8_000)

    def test_description_falls_back_to_openai_yaml(self):
        path = write_raw(self.root, "no-desc", "---\nname: no-desc\n---\n\n正文")
        agents = self.root / "no-desc" / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(
            'interface:\n  display_name: "展示名"\n  short_description: "来自 openai.yaml"\n',
            encoding="utf-8",
        )
        skill = load_skill(path)
        self.assertEqual(skill.description, "来自 openai.yaml")
        self.assertEqual(skill.display_name, "展示名")

    def test_broken_file_does_not_break_library(self):
        write_skill(self.root, "good-one", cats="props")
        write_raw(self.root, "bad-one", "没有 frontmatter")
        loaded, errors = load_all(self.root)
        self.assertEqual([s.name for s in loaded], ["good-one"])
        self.assertEqual(len(errors), 1)
        self.assertIn("bad-one", errors[0])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_skill(self.root, "char-skill", cats="characters")
        write_skill(self.root, "prop-skill", cats="props")
        write_skill(self.root, "manual-skill", cats="manual")
        write_raw(
            self.root,
            "undeclared-skill",
            "---\nname: undeclared-skill\ndescription: 描述里提到角色和场景但没有声明\n---\n正文",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_auto_match_only_uses_declared_categories(self):
        section = build_skill_section({"characters"}, root=self.root)
        self.assertEqual(section.loaded, ("char-skill",))

    def test_undeclared_skill_never_auto_loaded(self):
        """描述里出现关键词不算声明——否则一次会装进半个库。"""
        section = build_skill_section({"characters", "props", "scenes"}, root=self.root)
        self.assertNotIn("undeclared-skill", section.loaded)

    def test_explicit_names_select_only_those(self):
        section = build_skill_section({"characters"}, ["prop-skill"], root=self.root)
        self.assertEqual(section.loaded, ("prop-skill",))

    def test_undeclared_skill_can_be_selected_by_name(self):
        section = build_skill_section(set(), ["undeclared-skill"], root=self.root)
        self.assertEqual(section.loaded, ("undeclared-skill",))

    def test_unknown_name_raises(self):
        with self.assertRaises(UnknownSkillError) as ctx:
            build_skill_section({"characters"}, ["not-exist"], root=self.root)
        self.assertEqual(ctx.exception.names, ["not-exist"])

    def test_bindings_make_generic_skill_auto_loadable(self):
        """项目侧绑定表：不改技能文件也能让它参与自动装载。"""
        bindings = self.root.parent / "skill-bindings.yaml"
        bindings.write_text("undeclared-skill: [scenes]\n", encoding="utf-8")
        try:
            section = build_skill_section({"scenes"}, root=self.root)
            self.assertIn("undeclared-skill", section.loaded)
        finally:
            bindings.unlink()

    def test_empty_library_returns_blank(self):
        with tempfile.TemporaryDirectory() as empty:
            section = build_skill_section({"characters"}, root=Path(empty))
        self.assertEqual(section.text, "")
        self.assertEqual(section.loaded, ())


class SectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_header_declares_lower_priority_than_hard_rules(self):
        write_skill(self.root, "char-skill", cats="characters")
        section = build_skill_section({"characters"}, root=self.root)
        self.assertIn("优先级低于", section.text)
        self.assertIn('<skill name="char-skill">', section.text)

    def test_budget_overflow_is_skipped_and_reported(self):
        write_skill(self.root, "huge-skill", cats="characters", extra="长" * 3_000)
        write_skill(self.root, "tiny-skill", cats="characters")
        with patch.object(settings, "skills_budget_chars", 2_000):
            section = build_skill_section(
                {"characters"}, ["huge-skill", "tiny-skill"], root=self.root
            )
        self.assertEqual(section.loaded, ("tiny-skill",))
        self.assertEqual(section.skipped, ("huge-skill",))
        self.assertIn("篇幅预算", section.text)

    def test_section_reports_its_own_size(self):
        write_skill(self.root, "char-skill", cats="characters")
        section = build_skill_section({"characters"}, root=self.root)
        self.assertEqual(section.chars, len(section.text))


class RefExpansionTests(unittest.TestCase):
    """references 是第三层渐进披露；没有读取工具时用内联展开降级模拟。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _skill_with_refs(self, body_links: str, files: dict[str, str]) -> Path:
        folder = self.root / "ref-skill"
        (folder / "references").mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        path = folder / "SKILL.md"
        path.write_text(
            f"---\nname: ref-skill\ndescription: 带参考文件\n---\n\n正文{body_links}",
            encoding="utf-8",
        )
        return path

    def test_referenced_markdown_is_inlined(self):
        self._skill_with_refs("见[细则](references/detail.md)", {"references/detail.md": "细则原文内容"})
        section = build_skill_section(set(), ["ref-skill"], root=self.root)
        self.assertIn("细则原文内容", section.text)
        self.assertIn('<ref path="references/detail.md">', section.text)
        # 报告里带技能名前缀，per_type 下多段合并才能正确去重
        self.assertEqual(section.refs, ("ref-skill/references/detail.md",))

    def test_second_level_links_are_followed(self):
        self._skill_with_refs(
            "见[总纲](references/stage.md)",
            {
                "references/stage.md": "总纲正文，细则见[细则](detail.md)",
                "references/detail.md": "第二层细则内容",
            },
        )
        section = build_skill_section(set(), ["ref-skill"], root=self.root)
        self.assertIn("第二层细则内容", section.text)
        self.assertIn("ref-skill/references/detail.md", section.refs)

    def test_non_markdown_and_external_links_are_ignored(self):
        self._skill_with_refs(
            "见[脚本](scripts/tool.py) 与 [外链](https://example.com/x.md)",
            {"scripts/tool.py": "print('不应进入提示词')"},
        )
        section = build_skill_section(set(), ["ref-skill"], root=self.root)
        self.assertNotIn("print(", section.text)
        self.assertEqual(section.refs, ())

    def test_path_traversal_is_blocked(self):
        secret = self.root.parent / "outside-secret.md"
        secret.write_text("不该被读到的内容", encoding="utf-8")
        try:
            self._skill_with_refs("见[越界](../../outside-secret.md)", {})
            section = build_skill_section(set(), ["ref-skill"], root=self.root)
            self.assertNotIn("不该被读到的内容", section.text)
        finally:
            secret.unlink()

    def test_refs_budget_skips_and_reports(self):
        self._skill_with_refs(
            "见[大文件](references/big.md) 与 [小文件](references/small.md)",
            {"references/big.md": "长" * 5_000, "references/small.md": "短内容"},
        )
        section = build_skill_section(set(), ["ref-skill"], root=self.root, refs_budget=1_000)
        self.assertEqual(section.refs, ("ref-skill/references/small.md",))
        self.assertEqual(section.refs_skipped, ("ref-skill/references/big.md",))
        self.assertIn("未展开", section.text)


class RealLibraryTests(unittest.TestCase):
    """针对 backend/skills 真实目录的兼容性回归。"""

    def test_all_bundled_skills_load_without_error(self):
        loaded, errors = load_all()
        self.assertEqual(errors, (), f"技能库存在格式错误：{errors}")
        self.assertGreaterEqual(len(loaded), 1)

    def test_every_bundled_skill_has_name_description_body(self):
        loaded, _ = load_all()
        for skill in loaded:
            with self.subTest(skill=skill.name):
                self.assertTrue(skill.name)
                self.assertTrue(skill.description)
                self.assertTrue(skill.body)
                self.assertNotIn("已截断", skill.body)

    def test_every_declared_ref_exists_on_disk(self):
        loaded, _ = load_all()
        for skill in loaded:
            for ref in skill.refs:
                with self.subTest(skill=skill.name, ref=ref):
                    self.assertTrue((skill.dir / ref).is_file(), f"{skill.name} 引用了不存在的 {ref}")

    def test_binding_table_targets_existing_skills(self):
        loaded, _ = load_all()
        names = {s.name for s in loaded}
        for target in skills.load_bindings():
            with self.subTest(binding=target):
                self.assertIn(target, names, "绑定表里写了不存在的技能名")

    def test_bindings_feed_auto_load(self):
        if not skills.load_bindings():
            self.skipTest("未配置 skill-bindings.yaml")
        section = build_skill_section(("characters", "props", "scenes"))
        self.assertTrue(section.loaded, "绑定表存在但自动装载为空")


class PromptIntegrationTests(unittest.TestCase):
    def test_skill_section_goes_into_system_not_user(self):
        msgs = llm.build_messages(
            "章节原文内容在此，长度足够。", {}, skill_text='## 技能规范\n<skill name="x">规则</skill>'
        )
        system, user = msgs[0]["content"], msgs[1]["content"]
        self.assertIn('<skill name="x">', system)
        self.assertNotIn("<skill name=", user)
        self.assertIn("章节原文内容在此", user)

    def test_no_skill_text_leaves_no_placeholder(self):
        system = llm.build_messages("章节原文内容在此，长度足够。", {})[0]["content"]
        self.assertNotIn("<<SKILL_SECTION>>", system)

    def test_per_type_builds_one_section_per_category(self):
        sections, _visual = llm._sections_for("per_type", None)
        self.assertEqual(set(sections), {"characters", "props", "scenes"})

    def test_auto_builds_one_shared_asset_section(self):
        sections, _visual = llm._sections_for("auto", None)
        self.assertEqual(set(sections), {"*"})

    def test_named_skills_are_split_by_stage(self):
        """点名的技能按类目分流：分镜技能不该被塞进资产段，反之亦然。"""
        asset_names, visual_names = llm._split_named_skills(
            ["short-drama-assets", "short-drama-storyboard"]
        )
        self.assertEqual(asset_names, ["short-drama-assets"])
        self.assertEqual(visual_names, ["short-drama-storyboard"])

    def test_undeclared_skill_goes_to_both_stages(self):
        """类别不明的技能两段都装——用户既然点了，就不替他猜。"""
        asset_names, visual_names = llm._split_named_skills(["short-drama"])
        self.assertIn("short-drama", asset_names)
        self.assertIn("short-drama", visual_names)


class RouterTests(unittest.TestCase):
    def test_menu_returns_metadata_without_body(self):
        from app.api.pipeline import list_skills

        resp = list_skills(None)
        self.assertTrue(resp.skills)
        dumped = resp.skills[0].model_dump()
        self.assertNotIn("body", dumped)
        self.assertIn("refs", dumped)
        self.assertEqual(resp.errors, [])

    def test_menu_filter_uses_declared_categories(self):
        from app.api.pipeline import list_skills

        resp = list_skills("characters")
        self.assertTrue(resp.skills)
        for item in resp.skills:
            self.assertIn("characters", item.categories)

    def test_unknown_skill_yields_400_not_500(self):
        """技能名写错属于请求问题：全局异常处理器给 400，不是 500。"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        res = client.post(
            "/api/pipeline/generate",
            json={"chapter": "林桂芳推开老木门，铜钥匙发烫。", "skills": ["no-such-skill"]},
        )
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIn("未找到技能", res.json()["detail"])

    def test_response_reports_skills_and_refs(self):
        from app.api.pipeline import run
        from app.core.schemas import ExtractAssetsRequest

        with patch.object(llm.settings, "mock", True):
            resp = run(ExtractAssetsRequest(chapter="林桂芳推开老木门，铜钥匙发烫。"))
        self.assertEqual(resp.prompt_version, llm.PROMPT_VERSION)
        self.assertIsInstance(resp.skills_loaded, list)
        self.assertGreaterEqual(resp.skills_refs_expanded, 0)


if __name__ == "__main__":
    unittest.main()
