"""Check that the version numbers agree.

    python tools/check_version.py           # pyproject.toml and prusa2panda/__init__.py agree
    python tools/check_version.py v0.2.0    # ...and match this release tag, with a
                                            # "## 0.2.0" section in CHANGELOG.md
"""

import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _find(path, pattern):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        m = re.search(pattern, f.read(), re.M)
    if not m:
        sys.exit(f"{path}: no version found")
    return m.group(1)


if __name__ == "__main__":
    project = _find("pyproject.toml", r'^version\s*=\s*"([^"]+)"')
    package = _find("prusa2panda/__init__.py", r'^__version__\s*=\s*"([^"]+)"')
    errors = []
    if project != package:
        errors.append(f"pyproject.toml says {project}, prusa2panda/__init__.py says {package}")
    if len(sys.argv) > 1:
        tag = sys.argv[1].removeprefix("v")
        if tag != project:
            errors.append(f"release tag {sys.argv[1]} does not match version {project} "
                          f"in pyproject.toml / prusa2panda/__init__.py")
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            if not re.search(rf"^## {re.escape(tag)}\s*$", f.read(), re.M):
                errors.append(f'CHANGELOG.md has no "## {tag}" section')
    for e in errors:
        print(f"::error::{e}")
    if errors:
        sys.exit(1)
    print(f"version {project} OK")
