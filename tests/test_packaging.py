import tempfile
import unittest
from pathlib import Path

from build_version import generate, version_parts
from version import APP_VERSION


class PackagingTests(unittest.TestCase):
    def test_windows_metadata_is_generated_from_the_application_version(self):
        with tempfile.TemporaryDirectory() as directory:
            inno, resource = generate(Path(directory))
            self.assertIn(f'AppVersion "{APP_VERSION}"', inno.read_text(encoding="utf-8"))
            content = resource.read_text(encoding="utf-8")
            self.assertIn(f"FileVersion', '{APP_VERSION}", content)
            self.assertIn(f"ProductVersion', '{APP_VERSION}", content)

    def test_version_must_follow_semantic_three_part_format(self):
        self.assertEqual(version_parts("1.2.3"), (1, 2, 3, 0))
        with self.assertRaises(ValueError):
            version_parts("1.2")
