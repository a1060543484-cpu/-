"""LangGraph 执行引擎（真接入版，2026-11-05）。

这一层**真正接管**办案流程：StateGraph 负责节点编排、条件边跳转、状态合并，
项目自己的 `checkpoint` 表负责跨请求 / 跨重启的业务持久化。

分工说明（避免"装了包 ≠ 用了框架"的老毛病）：
┌─────────────────────────┬────────────────────────────────────────┐
│ LangGraph StateGraph    │ 节点执行顺序、条件边、状态合并、单步 checkpoint │
│ 项目 checkpoint 表       │ 业务状态权威源（中断/恢复、审批、重启续跑）      │
│ 内置状态机 graph.py      │ import 失败 / 运行异常时的降级路径             │
└─────────────────────────┴────────────────────────────────────────┘

为什么不用 langgraph-checkpoint-sqlite 直接持久化：
- 项目 db 层同时支持 SQLite / MySQL，检查点必须和业务数据同库，reset/backup/删除卷宗
  才能一起走事务；独立一个 langgraph.db 会在"删卷宗"时留下孤儿检查点。
- 本项目业务恢复依赖 approval / execution / evidence 等表，光恢复 LangGraph 的
  channel state 不够。所以让框架的 checkpointer 管执行轨迹，业务表管恢复语义。

🔴 三个踩过的坑（都在这份实现里规避了）：
1. `StateGraph(dict)`：裸 dict 当 schema 会把整个 state 塞进 `__root__` 单通道，
   langgraph 1.2.11 下 `get_graph()` 报 InvalidUpdateError、
   `invoke()` 报 "Can receive only one value per step"。→ 必须用 TypedDict + reducer。
2. 未声明字段会被 StateGraph **静默丢弃**（和课程里 `online` → `outline` 那个坑同源）。
   业务 state 里有 review_decision / review_round / halt_node 等自定义字段，
   所以用一个 `payload` 通道把整个业务 state 原样背过去，节点不碰它。
3. `recursion_limit` 作为 `invoke()` 关键字参数会被忽略（静默回落到 10007），
   必须写进 `config={"recursion_limit": ...}`。
"""
from __future__ import annotations

import threading
import uuid
from typing import Annotated, Any, TypedDict

from .. import config, db

# ---------------------------------------------------------------- 可选依赖探测
try:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
    LANGGRAPH_IMPORT_ERROR = ""
except Exception as _exc:                       # 没装 → 整个模块仍可导入，只是不启用
    LANGGRAPH_AVAILABLE = False
    LANGGRAPH_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"


# ---------------------------------------------------------------- 状态定义
def _last(_old: Any, new: Any) -> Any:
    """覆盖型 reducer：后写覆盖前写。线性链里每个字段只有一个节点写，不会冲突。"""
    return new


def _steps(_old: Any, new: Any) -> Any:
    """steps 也用覆盖型：节点函数自己读旧值再 append。

    如果用累加型 reducer `(a or [])+(b or [])`，同一 thread 第二次 invoke
    会把上一次的步骤又加一遍（探针实测：run2 = run1 + run2）。
    由节点自己维护列表 + 覆盖型 reducer，就能做到「invoke 传入的初始 steps」
    完全决定本次累积起点，跨请求不会串状态。
    """
    return new


class FlowState(TypedDict, total=False):
    """LangGraph 的状态 schema。

    ⚠️ 凡是业务代码可能写进 state 的字段，要么在这里声明，要么放 payload。
       漏声明 = 静默丢弃，排查起来极痛苦。
    """
    # --- 本次运行的输入 ---
    entry: Annotated[str, _last]                # 从哪个节点起跑（对应 run_flow 的 start）
    case_id: Annotated[int, _last]
    force: Annotated[bool, _last]               # 服刑节点是否强制视为刑期届满
    # --- 路由字段（条件边读它们）---
    node: Annotated[str, _last]
    halt: Annotated[bool, _last]
    goto: Annotated[str, _last]
    # --- 业务结果（写回 checkpoint 表 / 返回给前端）---
    status: Annotated[str | None, _last]
    progress: Annotated[int | None, _last]
    message: Annotated[str, _last]
    halt_node: Annotated[str, _last]
    steps: Annotated[list, _steps]
    # --- 兜底：把整个业务 state 原样背过去，防止自定义字段被静默丢弃 ---
    payload: Annotated[dict, _last]


# ---------------------------------------------------------------- 图构建
_COMPILED = None
_BUILD_LOCK = threading.RLock()
# 同一卷宗不允许并发跑两条流程（ThreadingHTTPServer 是多线程的），
# 否则两条线会互相覆盖 checkpoint。用一把可重入锁串行化执行段。
_FLOW_LOCK = threading.RLock()
# 每次 run 用独立的 thread_id（uuid 后缀，无状态、天然线程安全）：
# MemorySaver 会为每个 thread 保存每个 super-step 的快照，若一个卷宗反复
# 用同一个 thread，快照会无限累积占内存。业务恢复本来就不依赖它
# （靠项目 checkpoint 表），所以按次隔离更合适。


def _make_checkpointer():
    """创建 checkpointer。

    优先用官方 SqliteSaver（如果以后装了 langgraph-checkpoint-sqlite），
    否则用进程内 MemorySaver。两者都真实启用 LangGraph 的 checkpoint 机制：
    invoke 过程中每个 super-step 都会落一次检查点，可 get_state / get_state_history。
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore

        path = str(config.DATA_DIR / "langgraph_checkpoints.db")
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        return SqliteSaver.from_conn_string(path), "sqlite-saver"
    except Exception:
        return MemorySaver(), "memory-saver"


def available() -> bool:
    """当前是否该用 LangGraph 作为执行引擎。"""
    mode = (config.FLOW_ENGINE or "auto").lower()
    if mode == "builtin":
        return False
    return LANGGRAPH_AVAILABLE


def checkpointer_name() -> str:
    global _COMPILED
    if not LANGGRAPH_AVAILABLE:
        return "none"
    get_compiled()
    return _COMPILED[1]


def get_compiled():
    """编译并缓存图（模块级单例）。返回 (compiled_graph, checkpointer_name)。"""
    global _COMPILED
    if not LANGGRAPH_AVAILABLE:
        raise RuntimeError(f"未安装 langgraph：{LANGGRAPH_IMPORT_ERROR}")
    with _BUILD_LOCK:
        if _COMPILED is not None:
            return _COMPILED

        from . import graph as _graph         # 延迟导入，避开 graph ↔ engine 循环

        nodes = list(_graph.NODE_SEQUENCE)

        def make_node(name: str):
            def fn(state: FlowState) -> dict:
                from . import graph           # 延迟导入

                case_id = int(state["case_id"])
                force = bool(state.get("force"))
                case = db.get("case_file", case_id)

                if name == "review":
                    # 人工闸门：没有对应 Agent，进入即挂起（与 graph.run_flow 的护栏一致）
                    result = {"halt": True, "goto": "END", "status": "reviewing",
                              "progress": 60, "message": "已挂起，等待阎王复核"}
                else:
                    # 节点执行 + 全部业务副作用由 graph._execute_step 统一负责，
                    # 保证 LangGraph 路径和内置状态机路径写库行为完全一致。
                    result = graph._execute_step(name, case_id, force)

                halt = bool(result.get("halt"))
                step = {
                    "node": name,
                    "name": _graph.NODE_META.get(name, {}).get("name", name),
                    "status": result.get("status"),
                    "message": result.get("message", ""),
                    "halt": halt,
                }
                prev_steps = state.get("steps") or []
                return {
                    "node": name,
                    "halt": halt,
                    "goto": result.get("goto") or "END",
                    "status": result.get("status"),
                    "progress": result.get("progress"),
                    "message": result.get("message", ""),
                    "halt_node": name if halt else "",
                    "steps": prev_steps + [step],
                    "payload": state.get("payload") or {},
                }
            return fn

        def route(state: FlowState) -> str:
            """条件边：halt → END，否则按 goto 跳（等价 graph._next_of）。"""
            if state.get("halt"):
                return END
            goto = state.get("goto")
            return goto if goto in nodes else END

        builder = StateGraph(FlowState)
        for n in nodes:
            builder.add_node(n, make_node(n))

        # START 直接做条件边，就能支持"从任意节点起跑"（start=execution / judge / intake），
        # 不需要额外挂一个 __entry__ 节点再 Command(goto=...)。
        builder.add_conditional_edges(START, lambda s: s.get("entry") or "intake",
                                      {n: n for n in nodes})
        for n in nodes:
            builder.add_conditional_edges(n, route, {k: k for k in nodes} | {END: END})

        ckpt, ckpt_name = _make_checkpointer()
        _COMPILED = (builder.compile(checkpointer=ckpt), ckpt_name)
        return _COMPILED


def build_graph():
    """返回编译好的图（供 graph.build_langgraph / 画 mermaid 用）。"""
    return get_compiled()[0]


# ---------------------------------------------------------------- 执行入口
def run(case: dict, state: dict, start: str, force: bool, max_steps: int) -> dict:
    """用 LangGraph 跑一段流程，返回与内置状态机**完全相同结构**的 dict。

    参数与返回契约对齐 graph.run_flow，前端 / demo.py / 审批接口零改动。
    """
    compiled, _ = get_compiled()
    case_id = int(case["id"])
    thread = f"{graph_thread_id(case)}:run{uuid.uuid4().hex[:8]}"

    # payload 背住整个业务 state（含 review_decision 等自定义字段），
    # 节点不修改它；执行结束后以它为底，再叠加本次结果字段。
    payload = dict(state or {})
    initial: dict = {
        "entry": start,
        "case_id": case_id,
        "force": bool(force),
        "halt": False,
        "goto": "",
        "steps": list(payload.get("steps") or []),   # 累积起点 = 历史步骤
        "payload": payload,
        "node": start,
        "status": payload.get("status"),
        "progress": payload.get("progress"),
        "message": payload.get("message", ""),
    }
    cfg = {
        "configurable": {"thread_id": thread},
        "recursion_limit": max_steps + 2,           # 🔴 必须放 config，kwarg 会被忽略
    }

    with _FLOW_LOCK:                                # 串行化：避免并发覆盖 checkpoint
        try:
            final = compiled.invoke(initial, config=cfg)
        except GraphRecursionError:
            # 达到单次最大步数：与内置状态机返回同一句话，调用方可以继续续跑
            try:
                snap = compiled.get_state(cfg).values
            except Exception:
                snap = {}
            done = snap.get("steps") or []
            return {
                "node": snap.get("node") or start,
                "next": snap.get("node") or start,
                "status": "running",
                "progress": None,
                "message": "达到单次最大步数，可继续执行",
                "steps": _this_run_steps(done, payload),
            }

    all_steps = final.get("steps") or []
    steps = _this_run_steps(all_steps, payload)
    halt = bool(final.get("halt"))
    last_node = final.get("node") or start

    # 以 payload 为底叠加本次结果：任何未声明的自定义业务字段都不会丢
    new_state = dict(final.get("payload") or payload)
    new_state.update({
        "node": last_node,
        "status": final.get("status"),
        "progress": final.get("progress"),
        "message": final.get("message", ""),
        "steps": all_steps,
    })
    if halt:
        # 与内置状态机完全一致：判官因高风险挂起时，检查点停在 review（等阎王批），
        # 而不是停在 judge；其余情况停在最后执行的节点。
        new_state["node"] = "review" if last_node == "judge" else last_node
        new_state["halt_node"] = last_node

    # 业务持久化：项目自己的 checkpoint 表（跨重启可恢复，比 MemorySaver 强）
    from . import graph
    graph.save_checkpoint(case, new_state, "interrupted" if halt else "ended")
    if not halt:
        db.update("case_file", {
            "status": final.get("status") or case.get("status"),
            "progress": final.get("progress") or 0,
            "updated_at": db.now(),
        }, "id=?", (case_id,))

    return {
        "node": last_node,
        "next": "END",
        "status": final.get("status"),
        "progress": final.get("progress"),
        "message": final.get("message"),
        "halt": halt,
        "steps": steps,
    }


def _this_run_steps(all_steps: list, payload: dict) -> list:
    """从累积步骤里切出「本次执行」的部分（返回给前端的 steps 只含本次）。"""
    history = len(payload.get("steps") or [])
    return list(all_steps[history:])


def graph_thread_id(case: dict) -> str:
    return f"case-{case['id']}-{case.get('case_no', '')}"
