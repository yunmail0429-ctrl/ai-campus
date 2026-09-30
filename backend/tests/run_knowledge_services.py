import os
import subprocess
import sys
from pathlib import Path

from knowledge_services import services


def main():
    root = Path(__file__).resolve().parents[1]
    with services() as environment:
        environment = {**os.environ, **environment, "PYTEST_DEBUG_TEMPROOT": str(root / "local-data")}
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_knowledge_services.py",
            "tests/test_resource_services.py",
            "-q",
            "--tb=short",
        ]
        result = subprocess.run(command, cwd=root, env=environment, check=False)
        if result.returncode or "--browser" not in sys.argv:
            return result.returncode
        npm = "D:/Software/nodejs/npm.cmd" if os.name == "nt" else "npm"
        return subprocess.run(
            [npm, "run", "test:live"], cwd=root.parent / "frontend", env=environment, check=False
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
