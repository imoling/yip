"""真模型准确率：在「模型服务」里配好模型后才跑。"""
import subprocess
import sys
from pathlib import Path

import pytest

from server import settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not settings.configured(), reason="未配置模型服务")
def test_model_accuracy_at_least_90():
    r = subprocess.run([sys.executable, str(ROOT / "scripts/eval_classify.py")], capture_output=True, text=True)
    print(r.stdout)
    assert r.returncode == 0, r.stdout[-1500:]
