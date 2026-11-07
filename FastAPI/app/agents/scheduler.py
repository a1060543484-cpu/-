"""APScheduler 替身：asyncio 定时循环，巡检服刑期满并自动触发轮回流转。"""
from __future__ import annotations

import asyncio
import time

from .. import config, db, security
from . import graph


class Scheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.running = False
        self.interval = config.SCHEDULER_INTERVAL
        self.last_run = ""
        self.last_summary: dict = {}
        self.engine = "apscheduler" if _has_apscheduler() else "asyncio-loop"

    async def start(self) -> None:
        if not config.SCHEDULER_ENABLED or self._task:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self.running:
            try:
                self.tick()
            except Exception as exc:                  # 定时任务异常不影响主服务
                self.last_summary = {"error": str(exc)}
            await asyncio.sleep(self.interval)

    def tick(self) -> dict:
        """扫描服刑中的案件：期满则从检查点 continue_flow。"""
        rows = db.query("SELECT * FROM execution WHERE status='服刑中'")
        expired, remain, flowed = [], [], []
        for ex in rows:
            remain_days = max(0, (db.parse_ts(ex["end_date"]) - db.parse_ts(db.now())).days)
            db.update("execution", {"remaining_days": remain_days,
                                    "status": "已期满" if remain_days <= 0 else "服刑中",
                                    "updated_at": db.now()}, "id=?", (ex["id"],))
            case = db.get("case_file", ex["case_id"])
            if not case:
                continue
            if remain_days <= 0:
                expired.append(case["case_no"])
                try:
                    res = graph.continue_flow(ex["case_id"], force=True)
                    flowed.append({"case_no": case["case_no"], "message": res.get("message", "")})
                except Exception as exc:
                    flowed.append({"case_no": case["case_no"], "error": str(exc)})
            else:
                remain.append({"case_no": case["case_no"], "remaining_days": remain_days})
        self.last_run = db.now()
        self.last_summary = {"expired": expired, "flowed": flowed, "remain": remain,
                            "checked": len(rows), "engine": self.engine}
        if expired:
            security.audit(None, "定时巡检", "execution", 0,
                           {"expired": expired, "flowed": flowed}, source="scheduler")
        return self.last_summary


def _has_apscheduler() -> bool:
    import importlib.util
    return importlib.util.find_spec("apscheduler") is not None


SCHEDULER = Scheduler()
