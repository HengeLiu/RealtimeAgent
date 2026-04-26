"""统一服务端测试导入路径。"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_PYTHON = REPO_ROOT / "openaiglass-sdk/python"
BLIND_APP = REPO_ROOT / "openaiglass-for-blind"

for path in (REPO_ROOT, SDK_PYTHON, BLIND_APP):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
