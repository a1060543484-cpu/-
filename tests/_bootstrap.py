"""在导入 app 之前把环境变量指到临时目录。

⚠️ 关键：`app/config.py` 在**模块导入时**就读取 `SQLITE_PATH` / `DIFU_RUN_DIR`，
   所以必须比任何 `from app import ...` 都早执行。测试文件顶部第一行就
   `import _bootstrap`，靠这个保证顺序。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="difu-test-"))

# 隔离数据库：绝不碰项目里真实的 FastAPI/data/difu.db
os.environ["SQLITE_PATH"] = str(_TMP / "difu.db")
# 隔离运行目录：token 密钥 / credentials.txt 都写这里，不污染 .run/
os.environ["DIFU_RUN_DIR"] = str(_TMP / "run")
# 断网：规则测试不该依赖真实模型，也不该因为本机配了 Key 就跑出不同结果
os.environ.pop("LLM_API_KEY", None)
os.environ["LLM_API_KEY"] = ""
# 强制内置状态机，测试与 langgraph 是否安装无关
os.environ["DIFU_FLOW_ENGINE"] = "builtin"

TMP_DIR = _TMP


def cleanup() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)
