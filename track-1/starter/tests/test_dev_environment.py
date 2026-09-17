"""Check development key loading without reading real credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class DevelopmentEnvironmentTests(unittest.TestCase):
    def test_dotenv_loading_and_exported_override(self):
        source = Path(__file__).resolve().parents[2] / "scripts" / "with_env.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "track-1" / "scripts" / "with_env.py"
            script.parent.mkdir(parents=True)
            shutil.copyfile(source, script)
            (root / ".env").write_text('FEATHERLESS_API_KEY="test-file-key"\n')
            environment = dict(os.environ)
            environment.pop("FEATHERLESS_API_KEY", None)
            environment.pop("PYTHON_DOTENV_DISABLED", None)
            command = [sys.executable, str(script), sys.executable, "-c",
                       "import os; print(os.environ['FEATHERLESS_API_KEY'])"]
            result = subprocess.run(command, env=environment, capture_output=True,
                                    text=True, check=True, cwd=root)
            self.assertEqual(result.stdout.strip(), "test-file-key")
            environment["FEATHERLESS_API_KEY"] = "test-exported-key"
            result = subprocess.run(command, env=environment, capture_output=True,
                                    text=True, check=True, cwd=root)
            self.assertEqual(result.stdout.strip(), "test-exported-key")


if __name__ == "__main__":
    unittest.main()
