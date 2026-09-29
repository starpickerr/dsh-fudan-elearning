import tempfile
import unittest
from pathlib import Path

from src import utils
from src.errors import SyncError


class ConfigTests(unittest.TestCase):
    def test_yaml_preserves_unicode_null_and_course_ids(self):
        config = {"base_url": "https://elearning.fudan.edu.cn", "courses": ["00123", "计算机网络"],
                  "include_patterns": ["*Lecture*"], "only_file_ids": None, "ignore_file_ids": [],
                  "output_dir": "data/output", "request_interval": 0.4}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config/config.yaml"
            utils.save_config(path, config)
            self.assertEqual(utils.load_config(path), config)

    def test_invalid_yaml_and_python_tags_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for text in ("base_url: [", "!!python/object/apply:builtins.str ['unsafe']", "- a\n- b"):
                path.write_text(text)
                with self.assertRaises(SyncError):
                    utils.load_config(path)

    def test_bad_configuration_reports_actionable_errors(self):
        base = {"base_url": "https://elearning.fudan.edu.cn"}
        invalid = ({"courses": "42"}, {"only_file_ids": "10"}, {"request_interval": -1},
                   {"output_dir": ""}, {"base_url": "http://example.com"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            for change in invalid:
                utils.save_config(path, dict(base, **change))
                with self.assertRaises(SyncError):
                    utils.load_config(path)


if __name__ == "__main__":
    unittest.main()
