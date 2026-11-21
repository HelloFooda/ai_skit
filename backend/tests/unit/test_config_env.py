"""配置加载回归：全项目只读 backend/.env，不受工作目录影响，且不存在第二份模板。"""

import os
import tempfile
import unittest
from pathlib import Path

from app.core.config import ENV_FILE, Settings


class ConfigEnvFileTests(unittest.TestCase):
    def test_env_file_is_absolute_and_anchored_to_backend(self):
        """env_file 必须是绝对路径，且指向 backend/ 下的 .env。"""
        backend_dir = Path(__file__).resolve().parents[2]
        self.assertTrue(ENV_FILE.is_absolute())
        self.assertEqual(ENV_FILE.parent, backend_dir)
        self.assertEqual(ENV_FILE.name, ".env")
        self.assertEqual(Settings.model_config["env_file"], str(ENV_FILE))

    def test_no_second_env_template(self):
        """合并后不应再有 .env.example 之类的第二份配置文件。"""
        self.assertFalse((ENV_FILE.parent / ".env.example").exists())

    def test_cwd_does_not_change_config_source(self):
        """在别的工作目录放一份 .env 诱导加载，配置值不应被它改写。"""
        poison = "__cwd_poison_model__"
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env").write_text(f"LLM_MODEL={poison}\n", encoding="utf-8")
            try:
                os.chdir(tmp)
                loaded = Settings()
            finally:
                os.chdir(original_cwd)
        self.assertNotEqual(loaded.model, poison)


if __name__ == "__main__":
    unittest.main()
