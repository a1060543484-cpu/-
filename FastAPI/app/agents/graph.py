"""办案状态机：LangGraph 主引擎 + 内置状态机降级 + 检查点持久化。

🔴 2026-11-05 改造：LangGraph **真正接管执行**（不再是"只导出图定义"）。
- 装了 langgraph 且 DIFU_FLOW_ENGINE≠builtin → `run_flow` 走
  `langgraph_engine.run()`：StateGraph 负责节点编排 / 条件边 / 状态合并。
- 没装、import 失败、或运行期抛异常 → 自动降级到 `_run_flow_builtin()`，
  也就是原来那份纯标准库循环，行为完全不变。
- `engine_name()` 如实返回**实际执行**的引擎，不再装了包就谎报。

持久化分工：项目自己的 `checkpoint` 表是业务状态权威源（跨请求、跨重启、
审批恢复都靠它）；LangGraph 的 checkpointer 负责单次执行内的 super-step 快照。

中断点（interrupt）三类：
1. 材料不全 → 阴差生成补证通知后挂起，补证后从 intake 恢复；
2. 重大疑难（风险高）→ 判官生成待办审批单后挂起，阎王批准/驳回后从 review 恢复；
3. 服刑未满 → 停在 execution，由定时巡检在期满时 `continue_flow` 恢复。
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .. import config, db, security
from . import crew, langgraph_engine

NODE_SEQUENCE = ["intake", "judge", "review", "execution", "reincarnation", "mengpo", "archive"]

NODE_META = {
    "intake": {"name": "阴差受理", "agent": "阴差 Agent", "desc": "验明正身、材料校验、证据归档"},
    "judge": {"name": "判官审理", "agent": "判官 Agent", "desc": "罪名认定、功德核算、量刑建议"},
    "review": {"name": "阎王复核", "agent": "人工复核", "desc": "重大疑难案件挂起待批"},
    "execution": {"name": "刑罚执行", "agent": "刑罚司 Agent", "desc": "建服刑记录、跟踪刑期"},
    "reincarnation": {"name": "六道轮回", "agent": "轮回司 Agent", "desc": "按功德匹配六道"},
    "mengpo": {"name": "孟婆登记", "agent": "孟婆 Agent", "desc": "忘川登记、饮汤忘前尘"},
    "archive": {"name": "卷宗归档", "agent": "卷宗司 Agent", "desc": "归档、判例回灌知识库"},
}

NODE_PROGRESS = {"intake": 20, "judge": 45, "review": 60, "execution": 70,
                 "reincarnation": 85, "mengpo": 92, "archive": 100}

# 业务检查点的进程内缓存：thread_id -> state，落库镜像到 checkpoint 表。
# 重启后 _MEMORY 为空但表还在 → load_checkpoint 会自动回填，中断的案子能接着办。
_MEMORY: dict[str, dict] = {}


def engine_name() -> str:
    """如实报告**实际执行**的引擎。

    历史上有两次"谎报"：
    - 早期版本：只要 `import langgraph` 成功就返回 "langgraph"，但 run_flow 里
      0 处调用图 → 装了包就说在用；
    - 上一版：修成永远返回 "builtin-state-machine"，但那时确实没在用，没撒谎。
    现在 LangGraph 真的接管了执行，所以按 `available()` 动态返回。
    """
    return "langgraph" if langgraph_engine.available() else "builtin-state-machine"


def engine_capability() -> dict:
    """能力探测与「是否真的在用」分开，避免「装了 = 用了」的误读。"""
    used = langgraph_engine.available()
    return {
        "engine": engine_name(),
        "langgraph_installed": langgraph_engine.LANGGRAPH_AVAILABLE,
        "langgraph_used": used,
        "flow_engine": (config.FLOW_ENGINE or "auto").lower(),
        "checkpointer": langgraph_engine.checkpointer_name() if used else "checkpoint 表（内置状态机）",
        "note": ("LangGraph StateGraph 驱动节点与条件边；项目 checkpoint 表负责跨重启恢复"
                 if used else "未启用 LangGraph，办案流程跑内置状态机（纯标准库）"),
    }


def thread_id(case: dict) -> str:
    return f"case-{case['id']}-{case.get('case_no', '')}"


# ------------------------------------------------------------------ 检查点
def save_checkpoint(case: dict, state: dict, status: str = "running") -> None:
    tid = thread_id(case)
    _MEMORY[tid] = state
    row = db.query_one("SELECT id FROM checkpoint WHERE thread_id=?", (tid,))
    payload = db.jdumps(state)
    if row:
        db.update("checkpoint", {"node": state.get("node", ""), "state": payload,
                                 "status": status, "updated_at": db.now()}, "id=?", (row["id"],))
    else:
        db.insert("checkpoint", {"thread_id": tid, "case_id": case["id"],
                                 "node": state.get("node", ""), "state": payload,
                                 "status": status, "created_at": db.now(), "updated_at": db.now()})


def load_checkpoint(case: dict) -> dict | None:
    tid = thread_id(case)
    if tid in _MEMORY:
        return _MEMORY[tid]
    row = db.query_one("SELECT * FROM checkpoint WHERE thread_id=?", (tid,))
    if row:
        state = db.jloads(row["state"], {})
        _MEMORY[tid] = state
        return state
    return None


def clear_checkpoint(case: dict) -> None:
    tid = thread_id(case)
    _MEMORY.pop(tid, None)
    db.delete("checkpoint", "thread_id=?", (tid,))


# ------------------------------------------------------------------ 节点执行
def _run_node(node: str, case_id: int, force: bool = False) -> dict:
    cls = crew.AGENTS.get(node)
    if not cls:
        return {"halt": True, "goto": "END", "status": "completed", "progress": 100,
                "message": "未知节点"}
    agent = cls()
    try:
        if node == "execution":
            return agent.run(case_id, force=force)
        return agent.run(case_id)
    except Exception as exc:                       # 节点异常 → 挂起，人工介入，不炸整条流程
        security.agent_log(case_id, agent.code, agent.name, node, "节点异常", str(exc), model="error")
        db.update("case_file", {"status": "suspended", "updated_at": db.now()}, "id=?", (case_id,))
        return {"halt": True, "goto": node, "status": "suspended", "progress": None,
                "message": f"节点异常已挂起：{exc}"}


def _next_of(node: str, result: dict) -> str:
    """条件边：halt → END（中断），否则走 goto。"""
    if result.get("halt"):
        return "END"
    goto = result.get("goto") or "END"
    return goto


def _execute_step(node: str, case_id: int, force: bool = False) -> dict:
    """执行单个节点 + 落地它该有的全部副作用。

    🔴 这是 LangGraph 引擎和内置状态机**共用**的唯一执行入口：
       两条路径写库行为必须逐字一致，否则"切引擎"会悄悄改变业务结果。
       返回的就是 `_run_node` 的原生结果 dict（halt / goto / status / ...）。
    """
    db.update("case_file", {"current_node": node, "updated_at": db.now()}, "id=?", (case_id,))
    return _run_node(node, case_id, force=force)


def run_flow(case_id: int, start: str | None = None, max_steps: int = 12,
             force: bool = False) -> dict:
    """从 start（默认按检查点/当前节点）开始跑，直到中断（halt）或 END。

    2026-11-05：改成**引擎分派器**。前置护栏（卷宗存在 / 已归档 / review 人工闸门）
    两条路径共用，之后按 DIFU_FLOW_ENGINE 决定走 LangGraph 还是内置状态机。
    LangGraph 运行期抛异常会自动降级，保证演示不因框架问题中断。
    """
    case = db.get("case_file", case_id)
    if not case:
        raise ValueError(f"卷宗不存在：{case_id}")
    state = load_checkpoint(case) or {}
    node = start or state.get("node") or case.get("current_node") or "intake"
    if node == "END" or case.get("status") == "completed" and start is None:
        return {"node": "END", "status": "completed", "progress": 100,
                "message": "卷宗已归档", "steps": []}
    if node == "review":                       # review 节点由人工审批驱动，进入即挂起
        state.update({"node": "review", "status": "reviewing", "progress": 60})
        save_checkpoint(case, state, "interrupted")
        db.update("case_file", {"status": "reviewing", "current_node": "review", "progress": 60,
                           "updated_at": db.now()}, "id=?", (case_id,))
        return {"node": "review", "status": "reviewing", "progress": 60, "steps": [],
                "message": "已挂起，等待阎王复核"}

    if langgraph_engine.available():
        try:
            return langgraph_engine.run(case, state, node, force, max_steps)
        except Exception as exc:
            # 降级不阻断办案：记一条日志，然后跑纯标准库那份循环
            security.agent_log(case_id, "system", "流程引擎", node, "LangGraph 异常已降级",
                               f"{type(exc).__name__}: {exc}", model="fallback")
            print(f"[流程引擎] LangGraph 执行失败，降级内置状态机：{type(exc).__name__}: {exc}",
                  flush=True)
    return _run_flow_builtin(case, state, node, max_steps, force)


def _run_flow_builtin(case: dict, state: dict, node: str, max_steps: int,
                      force: bool) -> dict:
    """降级路径：纯标准库循环（改造前 run_flow 的原逻辑，逐字保留）。"""
    case_id = int(case["id"])
    steps: list[dict] = []
    current = node
    for _ in range(max_steps):
        result = _execute_step(current, case_id, force=force)
        steps.append({"node": current, "name": NODE_META.get(current, {}).get("name", current),
                      "status": result.get("status"), "message": result.get("message", ""),
                      "halt": bool(result.get("halt"))})
        state.update({"node": current, "status": result.get("status"),
                      "progress": result.get("progress"), "message": result.get("message", ""),
                      "steps": (state.get("steps") or []) + steps[-1:]})
        nxt = _next_of(current, result)
        if nxt == "END":
            # 中断时把检查点停在「该去而未去」的节点，恢复时才能接上
            if result.get("halt"):
                # 判官因风险高挂起时，检查点应停在 review（等阎王批），而不是停在 judge
                state["node"] = "review" if current == "judge" else current
                state["halt_node"] = current
            save_checkpoint(case, state, "interrupted" if result.get("halt") else "ended")
            return {"node": current, "next": "END", "status": result.get("status"),
                    "progress": result.get("progress"), "message": result.get("message"),
                    "halt": bool(result.get("halt")), "steps": steps}
        current = nxt
        db.update("case_file", {"status": result.get("status") or case.get("status"),
                           "progress": result.get("progress") or 0,
                           "updated_at": db.now()}, "id=?", (case_id,))
        save_checkpoint(case, {**state, "node": current}, "running")
    return {"node": current, "next": current, "status": "running", "progress": None,
            "message": "达到单次最大步数，可继续执行", "steps": steps}


def continue_flow(case_id: int, force: bool = False) -> dict:
    """从检查点继续（供审批通过、补证完成、刑期届满、手动续跑调用）。"""
    case = db.get("case_file", case_id)
    if not case:
        raise ValueError(f"卷宗不存在：{case_id}")
    state = load_checkpoint(case) or {}
    node = state.get("node") or case.get("current_node") or "intake"
    if case.get("status") == "completed":
        return {"node": "END", "status": "completed", "progress": 100,
                "message": "卷宗已归档", "steps": []}
    # 复核节点由「阎王裁决」驱动，不能靠点「推进流程」跳过：
    # 之前这里是 nxt = {"review": "execution"}.get(node, node)，
    # 于是只要点一下「推进流程」，未裁决的待复核卷宗就直接进刑罚执行了
    # （实测：卷宗 0002 审批单仍是 pending，却已建服刑记录 3650 天）。
    # 现在只要有待处理的复核单就原地挂起，必须走审批接口。
    if node == "review":
        pending = db.query("SELECT id FROM approval WHERE case_id=? AND status='pending'",
                           (case_id,))
        if pending:
            return {"node": "review", "status": "reviewing", "progress": 60, "steps": [],
                    "halt": True,
                    "message": "尚有未裁决的复核待办，请由阎王批准或驳回后再推进"}
    nxt = node
    return run_flow(case_id, start=nxt, force=force)


# ------------------------------------------------------------------ 审批决策
def decide_approval(approval_id: int, approve: bool, opinion: str, approver: dict) -> dict:
    """阎王批准 / 驳回：结果回灌流程，从检查点恢复继续。"""
    appr = db.get("approval", approval_id)
    if not appr:
        raise ValueError("待办不存在")
    if appr.get("status") != "pending":
        raise ValueError("该待办已处理")
    case_id = int(appr["case_id"])
    case = db.get("case_file", case_id)
    state = load_checkpoint(case) or {}
    state["review_round"] = int(state.get("review_round", 0)) + 1
    state["review_decision"] = "approve" if approve else "reject"
    state["review_opinion"] = opinion
    save_checkpoint(case, state, "running")

    db.update("approval", {
        "status": "approved" if approve else "rejected",
        "approver_id": approver.get("id", 0), "approver_name": approver.get("real_name", ""),
        "opinion": opinion, "decided_at": db.now(),
    }, "id=?", (approval_id,))
    db.insert("case_note", {
        "case_id": case_id, "node": "review", "actor_type": "human",
        "actor_name": approver.get("real_name", "阎王"),
        "content": f"{'批准' if approve else '驳回'}：{opinion or '无意见'}",
        "created_at": db.now(),
    })
    judgment = db.query_one("SELECT * FROM judgment WHERE case_id=? ORDER BY id DESC", (case_id,))
    if judgment and approve:
        # 只有批准才盖「已核准」章；驳回时这份判决是被否掉的旧稿，
        # 盖章会让卷宗里同时出现「已核准」和「已驳回」两份判决，说不清以哪份为准。
        db.update("judgment", {"approved_by": approver.get("real_name", ""),
                               "approved_at": db.now()}, "id=?", (judgment["id"],))
    security.audit(approver, "审批" + ("通过" if approve else "驳回"), "approval", approval_id,
                   {"opinion": opinion, "case_id": case_id})
    security.notify(0, "复核结果", f"{appr['approval_no']} 已{'批准' if approve else '驳回'}：{opinion}",
                    "approval", f"case:{case_id}")

    if approve:
        db.update("case_file", {"status": "executing", "current_node": "execution",
                           "progress": 65, "updated_at": db.now()}, "id=?", (case_id,))
        state.update({"node": "execution", "status": "executing", "progress": 65})
        save_checkpoint(case, state, "running")
        return {"approved": True, "flow": run_flow(case_id, start="execution")}
    # 驳回 → 退回判官重审
    db.update("case_file", {"status": "judging", "current_node": "judge", "progress": 45,
                       "updated_at": db.now()}, "id=?", (case_id,))
    state.update({"node": "judge", "status": "judging"})
    save_checkpoint(case, state, "running")
    return {"approved": False, "flow": run_flow(case_id, start="judge")}


# ------------------------------------------------------------------ 启动 / 重跑
def start_case(case_id: int) -> dict:
    case = db.get("case_file", case_id)
    if not case:
        raise ValueError(f"卷宗不存在：{case_id}")
    clear_checkpoint(case)
    db.update("case_file", {"status": "accepted", "current_node": "intake", "progress": 10,
                       "flow_state": db.jdumps({}), "updated_at": db.now()}, "id=?", (case_id,))
    # 给人看的一句话只讲「谁在办、断了能不能续」；thread_id 与引擎标识属于
    # 排障信息，放进 tool_output，由前端折叠在「技术详情」里，不再糊在脸上。
    used = engine_name()
    label = "LangGraph 编排引擎" if used == "langgraph" else "内置状态机（离线可用）"
    security.agent_log(
        case_id, "system", "流程引擎", "intake", "启动流程",
        f"本次办案由{label}接手，卷宗流转轨迹已单独存档，"
        "中途挂起或服务重启后都能从原处继续。",
        model=used,
        tool_output={"thread_id": thread_id(case), "engine": used},
    )
    return run_flow(case_id, start="intake")


def graph_def() -> dict:
    """给前端画流程图用的结构定义。"""
    return {
        "nodes": [{"key": k, "name": v["name"], "agent": v["agent"], "desc": v["desc"]}
                  for k, v in NODE_META.items()],
        "edges": [{"from": "intake", "to": "judge", "label": "材料齐备"},
                  {"from": "intake", "to": "END", "label": "材料不全→挂起补证"},
                  {"from": "judge", "to": "review", "label": "风险高"},
                  {"from": "judge", "to": "execution", "label": "风险低/中"},
                  {"from": "review", "to": "execution", "label": "阎王批准"},
                  {"from": "review", "to": "judge", "label": "阎王驳回→重审"},
                  {"from": "execution", "to": "reincarnation", "label": "刑期届满"},
                  {"from": "reincarnation", "to": "mengpo", "label": "六道已定"},
                  {"from": "mengpo", "to": "archive", "label": "忘川登记完成"},
                  {"from": "archive", "to": "END", "label": "归档"}],
        "engine": engine_name(),
    }


def build_langgraph():
    """返回**实际在跑**的那张 StateGraph（编译后的 CompiledStateGraph）。

    🔴 旧版这里是一份坏掉的"等价定义"：`StateGraph(dict)` 在 langgraph 1.2.11
       下 compile 能过，但 get_graph() 报 InvalidUpdateError、invoke() 报
       "Can receive only one value per step"，而且返回整个 state 会并发写同一通道。
       现在直接返回 `langgraph_engine` 里主路径用的那张图（TypedDict + reducer），
       能编译、能画 mermaid、能 invoke，不再有"摆设图"。
    """
    if not langgraph_engine.LANGGRAPH_AVAILABLE:
        raise RuntimeError("未安装 langgraph，当前使用内置状态机（功能等价）")
    return langgraph_engine.build_graph()


def simulate_expiry(case_id: int) -> dict:
    """演示用：把刑期直接改到已满，然后从检查点续跑。"""
    ex = db.query_one("SELECT * FROM execution WHERE case_id=? ORDER BY id DESC", (case_id,))
    if not ex:
        raise ValueError("该案件暂无服刑记录")
    db.update("execution", {"end_date": db.now(), "remaining_days": 0, "status": "已期满",
                            "updated_at": db.now()}, "id=?", (ex["id"],))
    security.agent_log(case_id, "system", "定时巡检", "execution", "模拟期满",
                       "刑期已记为期满，从上次中断处继续办理后续流程。", model="定时巡检")
    return continue_flow(case_id, force=True)
