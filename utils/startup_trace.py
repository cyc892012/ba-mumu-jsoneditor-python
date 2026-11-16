"""仅 MOMO_STARTUP_TRACE=1 时由入口导入的启动阶段记录。"""
import os
import time

_started = time.perf_counter()
_stages = []


def mark(stage):
    _stages.append({"stage": stage, "seconds": time.perf_counter() - _started})


def flush():
    path = os.environ.get("MOMO_STARTUP_TRACE_FILE", "")
    if not path:
        return
    try:
        import json
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(_stages, stream, ensure_ascii=False, indent=2)
    except OSError:
        # 可选诊断失败不影响正常启动。
        pass
