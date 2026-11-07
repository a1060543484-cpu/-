# 架构说明 · 酆都阴司 AI 智能地府管理系统

> 本文回答「这套系统是怎么搭起来的、为什么这么搭」。面向要读代码或要改代码的人。
> 只想跑起来看效果，看 [README](../README.md) 就够了。

---

## 一、一句话架构

**一条状态机驱动的办案流水线**：HTTP 请求进来 → 路由层校验身份与权限 → 业务层调工具读写 SQLite →
Agent 层用「大模型 or 规则引擎」做决策 → 状态机层把七个节点串起来、在三个地方挂起等人 →
所有动作落进 `agent_log` / `audit_log` / `tool_call` 三张日志表。

```
┌──────────────────────────────────────────────────────────────────────┐
│ 浏览器（frontVue3，零依赖手写 vdom）                                  │
│   登录 / 大殿 / 卷宗 / 复核 / 亡魂 / 证据 / 问案 / 典籍 / 编排 / 日志 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  JSON + SSE（Bearer Token）
┌───────────────────────────────▼──────────────────────────────────────┐
│ app/server.py        ThreadingHTTPServer（替代 uvicorn）              │
│   ├─ 静态托管  →  frontVue3/                                          │
│   ├─ SSE 流式  →  chat.py 生成器                                      │
│   ├─ multipart →  evidence 上传                                       │
│   └─ 巡检线程  →  scheduler.tick()  每 60s                            │
├──────────────────────────────────────────────────────────────────────┤
│ app/api/           路由 + 鉴权 + 统一响应                             │
│   base.py  Request / Response / Router / serve_static / SSEStream     │
│   auth.py  登录、会话、大殿统计                                        │
│   routes.py 业务 44 条（卷宗/亡魂/证据/审批/典籍/编排/日志/设置…）    │
├──────────────────────────────────────────────────────────────────────┤
│ app/agents/        AI 编排                                            │
│   graph.py    引擎分派 + 共用节点执行 + 检查点（挂起/恢复）← 核心     │
│   langgraph_engine.py StateGraph 真执行引擎 + 降级循环              │
│   crew.py     六个 Agent（think → 决策 → use 工具）                   │
│   tools.py    21 个工具（真正读写业务库）                             │
│   llm.py      OpenAI 兼容客户端 + 降级                                 │
│   rag.py      切片 / 检索 / 三级降级                                  │
│   multimodal.py OCR / ASR / PDF 抽文                                  │
│   scheduler.py 定时巡检                                               │
│   chat.py     问案判官（SSE）                                         │
├──────────────────────────────────────────────────────────────────────┤
│ app/db.py         sqlite3 DAL（`?`→`%s`、AUTOINCREMENT 适配 MySQL）   │
│ app/schema.py     21 张表 DDL + 索引                                  │
│ app/security.py   口令、Token、RBAC、行级过滤、双日志、通知           │
│ app/seed.py       角色/账号/字典/知识库/亡魂 种子                     │
│ app/demo.py       三条演示卷宗                                        │
└──────────────────────────────────────────────────────────────────────┘
                                │
                       FastAPI/data/difu.db
```

---

## 二、分层职责与边界

| 层 | 文件 | 职责 | 不该做的事 |
|---|---|---|---|
| 传输层 | `server.py` | 解析 HTTP、分派、SSE、静态文件、起巡检线程 | 不碰业务规则 |
| 接口层 | `api/base.py` | 请求解析、统一响应壳、路由匹配、静态托管、异常→JSON | 不做鉴权判断（交给 security） |
| 接口层 | `api/auth.py` `api/routes.py` | 参数取值、调 `require()` 鉴权、调业务/工具、组装返回 | 不直接写 SQL（除查询列表） |
| 编排层 | `agents/graph.py` | 引擎分派、护栏、检查点、挂起恢复、审批决策、降级循环 | 不写业务规则 |
| 编排层 | `agents/langgraph_engine.py` | StateGraph 图构建 / 条件边 / 状态合并 / checkpointer | 不写业务规则（节点执行统一回调 `graph._execute_step`） |
| 编排层 | `agents/crew.py` | 每个节点的「决策 + 调工具 + 记日志」 | 不直接改别的节点负责的表 |
| 能力层 | `agents/tools.py` | 21 个原子业务操作，全部落 `tool_call` 留痕 | 不做流程跳转 |
| 能力层 | `agents/llm.py` `rag.py` `multimodal.py` | 模型调用、检索、文件解析；**每个都有降级路径** | 不因外部不可用而抛异常中断办案 |
| 数据层 | `db.py` `schema.py` | 表结构、CRUD、事务、时间/JSON 小工具 | 不含业务判断 |

**关键约定**：跨层调用只能「向下」。`tools.py` 可以 `db`，`crew.py` 只能通过 `self.use()` 调工具，
`graph.py` 只能调 `crew.AGENTS`，`routes.py` 只能调 `tools.call` / `graph` / `db` 查询。

---

## 三、状态机（核心）

### 3.1 七个节点

| # | 节点 key | 名称 | 承办 | 进度 | 主要产出 |
|---|---|---|---|---|---|
| 1 | `intake` | 阴差受理 | 阴差 Agent | 20 | 验明正身、材料校验、`case.update` |
| 2 | `judge` | 判官审理 | 判官 Agent | 45 | 罪名、功德、量刑、`judgment` |
| 3 | `review` | 阎王复核 | 人工 | 60 | 批准 / 驳回（`approval`） |
| 4 | `execution` | 刑罚执行 | 刑罚司 Agent | 70 | `execution` 服刑记录 |
| 5 | `reincarnation` | 六道轮回 | 轮回司 Agent | 85 | `reincarnation` 转世文书 |
| 6 | `mengpo` | 孟婆登记 | 孟婆 Agent | 92 | 忘川登记 |
| 7 | `archive` | 卷宗归档 | 卷宗司 Agent | 100 | 归档 + 判例回灌知识库 |

### 3.2 条件边

```
intake ──材料齐备──────────────▶ judge
   └────材料不全 → END（挂起，发补证通知）

judge ──风险低/中─────────────▶ execution
   └───风险高───────────────▶ review（挂起，生成审批单）

review ──阎王批准─────────────▶ execution
   └───阎王驳回──────────────▶ judge（重审）

execution ──刑期届满──────────▶ reincarnation
   └──────服刑中 → END（挂起，等巡检）

reincarnation ──▶ mengpo ──▶ archive ──▶ END
```

### 3.3 检查点（MemorySaver 的替身）

`graph.py` 里 `_MEMORY: dict[str, dict]` 是进程内检查点，同时镜像到 `checkpoint` 表——
**内存丢失也能从表里恢复**（重启服务后继续办案依赖这一点）。

```python
thread_id(case) = f"case-{id}-{case_no}"     # 一个卷宗一条线

save_checkpoint(case, state, status)         # 有行则 update，无则 insert
load_checkpoint(case)                        # 先查内存，再查表
clear_checkpoint(case)                       # 重新开案时清掉
```

`state` 里除了 `node/status/progress/message`，还累积 `steps`（每一步的结果），
以及 `halt_node`（挂起时「该去而未去」的节点）与 `review_round` / `review_decision`。

### 3.4 三个挂起点与恢复入口

| 挂起原因 | 检查点停在哪 | 谁恢复 | 恢复入口 |
|---|---|---|---|
| 材料不全 | `intake` | 阴差补证后手动点「推进」 | `POST /api/cases/{id}/run` → `continue_flow` |
| 风险高（重大疑难） | `review` | 阎王在「阎王复核」页裁决 | `POST /api/approvals/{id}/decide` |
| 服刑未满 | `execution` | 定时巡检线程 | `scheduler.tick()` → `continue_flow(force=True)` |

**踩过的坑（写在这里防回归）**：

1. 判官因风险高挂起时，若检查点停在 `judge`，`continue_flow` 会把判官重跑一遍，
   重复出判决与审批单。所以 `run_flow` 里挂起时做 `state["node"] = "review" if current == "judge" else current`。
2. `decide_approval` 批准分支不能简单调 `continue_flow`（它按 `review→execution` 映射，
   但检查点里可能还指着 `judge`），必须显式 `state.update({"node": "execution", ...})` +
   `save_checkpoint` + `run_flow(start="execution")`。
3. `ExecutionAgent` 在已有服刑记录时，`force=True` 不能重复建记录——只刷新进度，
   期满放行去 `reincarnation`，否则挂起。
4. **复核节点不能被「推进流程」跳过**。原实现 `continue_flow` 里写死
   `nxt = {"review": "execution"}.get(node, node)`，于是只要有 `case:run` 权限，
   点一下「推进流程」就绕过阎王复核直接进刑罚执行（实测：审批单仍是 `pending`，
   卷宗却已建 3650 天服刑记录）。现在 `node == "review"` 时先查
   `SELECT id FROM approval WHERE case_id=? AND status='pending'`，有待办就原地挂起并返回
   「尚有未裁决的复核待办，请由阎王批准或驳回后再推进」。**审批是唯一出口。**
5. `judge` 挂起时检查点必须停 `review`（第 1 条），但 `review` 又只能由审批推进（第 4 条），
   两者配合才既不会重复出判决、也不会漏掉复核。

### 3.5 双执行引擎（2026-11-05 起）

```
POST /api/cases/{id}/run
  └─ graph.run_flow(case_id, start, max_steps, force)   ← 分派器
       ├ 前置护栏（两条路径共用）：卷宗存在 / 已归档短路 / review 人工闸门
       ├ LangGraph 可用 → langgraph_engine.run()
       │    └ StateGraph: START 条件边 → 7 节点 → 条件边(halt|goto) → END
       │         └ 每个节点回调 graph._execute_step()  ← 共用业务执行
       └ 不可用 / 运行异常 → graph._run_flow_builtin()
            └ 原纯标准库循环，同样调 _execute_step()
       └ 收尾：graph.save_checkpoint() 落 checkpoint 表（两路径一致）
```

- `DIFU_FLOW_ENGINE`：`auto`（默认，装了就用）/ `langgraph` / `builtin`。
- `engine_name()` 如实返回 `langgraph` 或 `builtin-state-machine`；
  `engine_capability()` 额外给出 `langgraph_installed` / `langgraph_used` / `checkpointer`，
  降级矩阵与前端标签按这些字段渲染，杜绝「装了包就显示在用」。
- 两条路径**共用** `_execute_step()`，节点写库副作用逐字一致；
  新增节点只需改 `NODE_SEQUENCE` / `NODE_META` / `crew.AGENTS`，图会自动重建。
- **持久化分工**：LangGraph 的 checkpointer 记单次执行的 super-step 轨迹，
  项目 `checkpoint` 表是业务状态权威源（跨请求、跨重启、审批恢复都靠它）。
  不用独立的 `langgraph.db`，否则 `/api/demo/reset`、删卷宗事务会留下孤儿检查点。
- 运行期 LangGraph 抛异常会记一条 `agent_log` 并自动降级，办案不中断。

**LangGraph 1.2.11 三个必须避开的坑**：

| 坑 | 症状 | 解法 |
| --- | --- | --- |
| `StateGraph(dict)` | `get_graph()` 抛 `InvalidUpdateError: At key '__root__'`；`invoke()` 报 `Can receive only one value per step` | `FlowState(TypedDict)` + 每字段 `Annotated[..., reducer]` |
| 未声明字段被静默丢弃 | `review_decision` 等丢失、恢复时 `KeyError`（和 `online→outline` 同源） | `payload` 通道背住整个业务 state |
| `recursion_limit` 写成 `invoke()` kwarg | 静默失效，回落默认 10007，死循环跑飞 | 放进 `config={"recursion_limit": ...}` |
| 同 thread 重复 invoke + 累加型 steps reducer | 第二次返回的步骤把上次的又算一遍 | 覆盖型 reducer + 节点自己维护列表 + `_this_run_steps()` 切片 |

**回归结论**：在两份全新隔离库上分别跑 `langgraph` / `builtin`，
7 条典型路径（含补证恢复、未裁决复核拦截、审批后从 execution 续跑、期满归档）
的流程返回与落库快照归一化后 **0 项差异**；无 langgraph 的解释器（Python 3.13.12）
自动降级且完整流程无异常。

---

## 四、Agent 与工具

### 4.1 Agent 基类

```python
class Agent:
    code, name, node, duty = ...
    def think(prompt, context, schema_hint) -> (decision: dict, meta: {model, latency_ms, tokens, degraded})
    def log(case_id, action, thought, meta, tool_name, tool_input, tool_output)
    def use(ctx, name, **kwargs)   # 调工具 + 记一条 agent_log
```

`think()` 的行为分叉：

- **有模型 Key**（`model_config` 里 `is_active=1` 且有 `api_key`）→ 走 `llm.chat()`，
  让模型输出 JSON 决策；解析失败或超时则 `decision = {}`，落到规则分支。
- **无 Key / 调用失败** → 直接返回 `{}`，子类用规则引擎兜底，`meta.degraded = True`。

**这就是「全链路降级」的含义**：没有模型时系统不是「报错」，而是「用确定性规则跑完整个办案流程」，
前端会明确标注「本地规则引擎（未配置模型 Key）」。

### 4.2 六个 Agent 的决策要点

| Agent | 决策 | 规则兜底逻辑 |
|---|---|---|
| `ClerkAgent` | 材料是否齐备 | `evidence.check_material` → 缺则 `evidence.supplement_notice` + 挂起 |
| `JudgeAgent` | 罪名 / 功德 / 量刑 / 风险 | `merit.calculate` → `law.match_crime` → `knowledge.search` → `law.match_penalty` → 风险分级 |
| `ExecutionAgent` | 服刑记录 | `execution.create` / `execution.progress`；`days<=0` 直接去轮回。牢狱取值见下 |
| `ReincarnationAgent` | 六道归属 | `realm.match` → `reincarnation.create` |
| `MengpoAgent` | 忘川登记 | `mengpo.register` |
| `ArchiveAgent` | 归档 | `case.archive` → 判例回灌 `rag.ingest_text(..., "判例库")` |

**牢狱取哪一间**（`ExecutionAgent._prison_of`）：判词常并科好几个牢狱（「沸油煎熬…期满转刀山狱三载、血池狱三载」），
早先的实现是「取判词里第一个出现的牢狱」，贪污案的主刑就被记成了次要的刀山狱。
现在先按罪名走 `law.match_penalty` 拿到主刑牢狱：主刑在判词里出现过就用它；判词只点了唯一一间就尊重判词；
一间都没提（模型只写「打入地狱道」这类去处）或并科多间时，按罪名定的主刑。
「出现过」既认狱名（`石压狱`）也认刑罚描述（`sys_dict.punishment` 的 `dict_value` 首句，如 `巨石压身`）——
判词常只写描述不写狱名（「巨石压身，偿其重负，刑期二百一十日」），只按狱名匹配会把并科的刀山狱当成唯一命中，
结果伤人案被判去刀山狱服刑。


### 4.3 21 个工具

分五类：

- **卷宗**：`case.get` `case.update` `case.note` `case.archive`
- **证据**：`evidence.list` `evidence.check_material` `evidence.supplement_notice`
- **亡魂**：`deceased.get` `deceased.update_merit`
- **法典/知识**：`knowledge.search` `law.match_crime` `law.match_penalty` `merit.calculate`
- **判决/执行/轮回**：`judgment.create` `execution.create` `execution.progress`
  `realm.match` `reincarnation.create` `mengpo.register`
- **审批/通知**：`approval.create` `notification.send`

每个工具都经 `tools.call()` 统一入口，**自动写 `tool_call` 表**（入参、出参、成功与否、耗时），
前端「工具调用留痕」页直接读这张表。

### 4.4 规则引擎的业务阈值（改这里等于改判案标准）

**量刑**（`law.match_penalty`）：

```python
base_days = {盗窃:365, 杀人:3285, 欺诈:540, 贪污:1825, 忤逆:1825,
             邪淫:720, 诽谤:365, 逃税:730, 伤人:180, 纵火:1095}
ratio = clamp(sin_score / 60, 0.05, 1.6)
if not crime or sin_score <= 0:   → 免刑（功过相抵）
```

罪名先按原词精确匹配，未命中再走**别名最长匹配**（`_CRIME_ALIAS`：`杀人` ← 害命/毙命/夺命/命案/谋杀/故杀/逼死/致死；`伤人` ← 误杀/误伤；`盗窃` ← 盗伐；`贪污` ← 侵夺/夺产…）。返回值的 `matched` 键说明这次是按哪个基准罪名算的——接真模型后模型自拟的复合罪名（「谋夺田产、伪造地契、逼死一家三口」）全靠它落到具体刑档。

刑期以**天**为准：`judgment.sentence_days` 存原始天数，`sentence_years` 只是给人看的两位小数。180 天存成 `0.49` 年再乘回 365 就是 178 天，判词写「一百八十日」、服刑记录却少两天——所以执行环节读 `sentence_days`，`sentence_years` 只在它缺失时兜底。

**判词里的数字也要对齐**（`crew.py` 的 `_align_desc_days`）：判官是「模型写判词 + 规则算刑期」两条线并行的，
模型会把上下文里的规则基准抄进判词（「沸油煎熬涤荡罪业二千九百二十日」），而落库的 `sentence_days`
是按最终罪名重算的另一份，卷宗上就会出现「判词 2920 日 / 刑期 3285 天」。
落库前统一改一遍：数字改成与 `days` 一致且**保持原文风格**（中文判词写中文数字，3285 → 三千二百八十五），
判词写着「免刑」却又有刑期、或判词为空时整段换成规则给的刑罚条款。
两处例外不动：「三日后 / 七日内 / 11月4日」这类时间词（看数字后面那个字），以及 30 以下的数字
（刑期最小是 `180 × 0.05 = 9` 天，不会误伤）。并科的次要刑期（「刀山狱三载」的「三载」）不在处理范围内。


**功德核算**（`merit.calculate`）：

```
善：救+20 济贫+10 施粥+8 修桥+5 施教/教化+15 孝+30 厚葬+5 赔偿+5 自首+8
恶：盗+10 杀+100 毙+100 致死+60 贪污/侵吞+40 忤逆+60 伤/斗殴+30 纵火/焚+50 淫+25 伪造+20
自首：sin *= 0.5（二等减罪）
```

**顺序很重要**：先 `max(关键词得分, 善恶簿基线)`，**再**减罪。
反过来的话「自首减半」会被基线直接盖掉，等于自首不减刑。

**六道判定**（`realm.match`）：

```
merit >= 90            → 天道
merit >= 70            → 人道
merit >= 50            → 阿修罗道 if sin >= 50 else 人道
merit >= 30            → 畜生道
sin   >= 90            → 地狱道
否则                    → 饿鬼道
```

**罪名匹配**（`law.match_crime`）：

- 罪名原词命中 **+4.0**；别名按权重加分（`盗伐 2.5`、`侵吞 3.0`、`致死 2.5`、`斗殴 2.0`…）。
- 得分 **< 2.0** 视为「无显著罪名」，输出 `crime: ""`，后续走免刑路径。
- **兜底 OCR / ASR 产生的「模拟文本」必须整段剔除**：
  `re.sub(r"【离线[^】]*模拟】[^。]*。", " ", haystack)`。
  否则模板词（「账册」「收据」）会把施粥善人判成贪污犯。

---

## 五、降级矩阵

系统对外部依赖全部做了「可缺失」设计，`config.DEGRADE` 在启动时用
`importlib.util.find_spec` 探测，`/api/dashboard/system` 与前端「阴阳设置」页都会展示。

| 能力 | 首选 | 缺失时 |
|---|---|---|
| 编排引擎 | **LangGraph `StateGraph`**（`langgraph_engine.py`，真执行）+ checkpointer | 内置状态机 `builtin-state-machine` + `_MEMORY`/`checkpoint` 表 |
| Web 服务 | uvicorn + FastAPI | `ThreadingHTTPServer` + 自写 `Request/Response/Router` |
| 定时任务 | APScheduler | `asyncio` 循环 / daemon 线程 `Scheduler.tick()` |
| ORM | Tortoise ORM | 手写 `sqlite3` DAL（`?`→`%s` 适配 MySQL） |
| 大模型 | OpenAI 兼容 `/chat/completions` | 规则引擎 + 知识库拼装答复 |
| 向量检索 | pgvector | 本地 `vector_store.json` 余弦 → 关键词/二元组 + IDF |
| OCR | 视觉模型 | `_offline_image` 生成模拟文本（**注意：这类文本不参与量刑**） |
| ASR | Whisper 兼容接口 | 离线模拟转写 |

**判定语义**：`DEGRADE[k] === True` 表示「该依赖缺失、已降级」，不是「已安装」。
（前端曾把语义渲染反了，后来统一成 `降级`/`已安装` 两种文案。）

**前五项看包、后四项看 Key**：`langgraph` / `apscheduler` / `tortoise` / `fastapi` / `vector_pg`
靠 `importlib.util.find_spec` 判定；`llm` / `vision` / `asr` / `embedding` 则是「有没有可用 Key」——
每次调用 `refresh_degrade()` 都查一遍 `model_config`（`is_active=1 AND api_key<>''`），
再回落到环境变量。这一条曾经是错的：早期只按环境变量算一次，导致在「阴阳设置」页填了 Key
之后顶栏仍显示「降级」，属于谎报。

注意**命名不是一套**：降级矩阵的键叫 `llm`，而 `model_config.purpose` 里叫 `text`
（`llm.active_config()` 默认 `purpose="text"`），代码里用 `purpose_of = {"llm": "text"}` 做映射。

---

## 六、RAG 设计

```
文档 → split_text(220 字 / 重叠 40，句读优先断句) → knowledge_chunk
     ├─ keywords: 中文二元组 + 去停用词
     └─ embedding: 有向量模型时落盘 data/vector_store.json
```

检索三级降级（`rag.search`）：

1. **向量**：有 `embedding` 且模型可用 → 余弦相似度。
2. **本地向量文件**：`vector_store.json` 里的历史向量。
3. **关键词**：`_keyword_score(query, content, kws, title, idf)`
   - 完整词命中标题 `+6×`、命中正文 `+3×`
   - 二元组命中 `+0.4 + 0.9 * idf`

同义词扩展 `_SYNONYMS`（盗伐→盗窃/盗/林木、侵吞→贪污/公帑、赈灾→贪污/粮款、
误伤致死→杀人、斗殴→伤人、不孝→忤逆…）让「口语案情」也能命中法条。

实测分数：`盗伐林木 → 盗窃罪条 18.5`、`侵吞赈灾粮款 → 贪污罪条 15.54`、
`忤逆不孝 → 王氏忤逆案 13.58`、`功德核算 → 功德核算通则 23.79`。

**判例回灌**：卷宗归档时 `case.archive` 把结案摘要写进「判例库」，下个案子的检索就能引到它。

---

## 七、安全与审计

### 7.1 身份

- 口令：**每账号随机盐 + PBKDF2-HMAC-SHA256（120000 轮）**，格式
  `pbkdf2_sha256$<迭代数>$<盐hex>$<摘要hex>`；`password_hash` **绝不出接口**（`Request.user()` 里 `pop`）。
  旧库的 64 位裸 hex（`sha256(b"difu-salt"+pwd)`）只在校验时临时兼容，首次启动会强制轮换掉。
- 首次启动自动生成 6 组随机口令写入 `.run/credentials.txt`（权限 600、已被 `.gitignore` 忽略），
  **源码 / README / 登录页都不再含弱口令**；只有显式 `DIFU_DEMO_MODE=1` 才保留课堂演示用的历史弱口令。
- `APP_SECRET`：**源码里没有默认值**，首次启动随机生成并持久化到 `.run/app_secret`（权限 600），
  也可由环境变量 `APP_SECRET` 显式指定。因此无法再用「公开的密钥」离线伪造管理员 token。
- Token：`base64(JSON{uid,role,exp,jti}) + "." + hmac-sha256(前32位, APP_SECRET)`，默认 TTL 86400s。
  `jti` 用于**服务端吊销**：logout 把 jti 写进 `.run/revoked_tokens.json`（落盘，重启后仍有效），
  之后该 token 立刻失效 —— 不再是「假登出」。
- Token **只认 `Authorization: Bearer` 请求头**，不再接受 `?token=`（避免明文进访问日志 / Referer）；
  访问日志也会脱敏。CORS 默认同源，不再全响应 `Access-Control-Allow-Origin: *`。
- 服务端异常统一返回脱敏消息，不回显 Python 原生报错栈；未捕获异常只写服务端日志。
- 前端存 `localStorage`（`difu.token` / `difu.user`），401 自动清 token 回登录页。

### 7.2 权限

`security.ROLE_PERMISSIONS` 六角色：

| 角色 | 权限要点 |
|---|---|
| `admin` | `["*"]` |
| `king` | 全部业务 `*:*`，含 `approval:decide` |
| `judge` | 判案 + `approval:decide` |
| `clerk` | 受理/证据，**无** `approval:decide` |
| `reincarnation` | `reincarnation:*` |
| `chenghuang` | 只 `case:read/create` + 辖区行级过滤 |

### 7.3 行级过滤

`security.case_scope(user)` 返回**裸条件**（不带 `AND` 前缀）：

- `judge` → `(judge_id=N OR judge_id=0 OR judge_id IS NULL)`
  （`judge_id=0` 是「尚未指派承办人」，`schema.py` 里该列默认就是 0 而非 NULL；
  漏掉这一项时，阴差或管理员新立的卷宗判官一条都看不见）
- `chenghuang` → `jurisdiction_id=N`
- 其他 → `""`

调用方必须是 `if scope: where.append(scope)`。
**踩过的坑**：早期 `case_scope` 自带 `AND` 前缀，`where` 为空时拼成
`SELECT * FROM case_file AND ...` → `near "AND": syntax error`（非 admin 账号访问卷宗列表直接 500）。

### 7.4 三张日志表

| 表 | 写入者 | 内容 |
|---|---|---|
| `agent_log` | Agent（`security.agent_log`） | `thought`、`tool_name`、`tool_input`、`tool_output`、`model`、`latency_ms`、`tokens` |
| `audit_log` | 人工与系统（`security.audit`） | 谁、什么动作、哪个对象、详情、`source ∈ {human, scheduler, agent, system}` |
| `tool_call` | `tools.call()` | 每次工具调用的入参/出参/成功/耗时 |

前端「双日志」页三个 tab 分别对应这三张表。

### 7.5 通知的可见范围

`security.notify(user_id, ...)` 写 `notification` 表；`user_id=0` 表示**广播**（所有人可见）。
`GET /api/notifications` 的可见规则：

- `admin` → 全量（含各岗位的补证通知、待办复核）
- 其他角色 → 自己的 + 广播（`user_id=? OR user_id=0`）

**为什么 admin 要看全量**：补证通知发给的是阴差/城隍（`role_code IN ('clerk','chenghuang')`），
待办复核发给阎王。若 admin 也按 `user_id` 过滤，用默认演示账号登录时**一条补证通知都看不到**，
很容易误判成「通知根本没写进库」。调试期就踩过这个坑。

**去重**：`evidence.supplement_notice` 发通知前按 `(user_id, link, is_read=0)` 查重，
同一卷宗反复点「推进流程」不会刷出一串重复通知（实测连点 3 次只留 1 条/人）。

**收件人回填**：接口返回前给每行补一个 `user_name`（`user_id=0` → `"全体"`，否则查 `user.real_name`）。
因为 admin 看到的是全量，一条补证通知会因为收件人不同（阴差一份、城隍一份）出现两行，
不标收件人就会被当成重复通知。前端通知面板把它渲染成标题右侧的灰色 tag「→ 苏州城隍」。

---

## 八、数据模型

21 张表，按用途分五组：

- **组织与权限**：`role` `jurisdiction` `user`
- **办案主体**：`deceased` `case_file` `case_note` `evidence`
- **办案结果**：`judgment` `execution` `reincarnation` `approval`
- **知识与对话**：`knowledge_doc` `knowledge_chunk` `chat_message`
- **系统与审计**：`agent_log` `audit_log` `notification` `model_config` `sys_dict` `tool_call` `checkpoint`

**命名坑**：表名不能叫 `case`（SQL 保留字，`near "case": syntax error`），已全量改为 `case_file`。

**重置语义**：`db.reset_db()` 只清 `BUSINESS_TABLES`（16 张业务表），
**保留** `user` / `role` / `jurisdiction` / `sys_dict` / `model_config`——
账号 id 不变则登录态仍有效，不会把人踢回登录页；基础配置重建会冲掉用户改过的模型 Key。

**加列的迁移**：`CREATE TABLE IF NOT EXISTS` 不会给老库补列。后加的列（目前只有
`judgment.sentence_days`）登记在 `db._EXTRA_COLUMNS`，`init()` 建表后调 `_migrate()`
做一次幂等的「缺列就补」（sqlite 走 `PRAGMA table_info`、mysql 走 `SHOW COLUMNS`），
升级后直接跑不会报 `no such column`。

**删除语义**：`DELETE /api/cases/{id}` 级联清 `case_note` / `evidence` / `judgment` /
`execution` / `reincarnation` / `approval` / `checkpoint` 七张表，但**刻意保留三张日志表**
（`agent_log` / `tool_call` / `audit_log`）。理由：卷宗可以撤，留痕不能撤——
否则删掉一份卷宗就能顺手抹掉自己办过案的证据。双日志页里这些记录仍显示原案号。

---

## 九、HTTP 层实现要点

自写 `api/base.py` 承担了 FastAPI 的一部分职责：

- `Request`：解析 path/query/JSON/multipart；`param(name, default, cast)` 按
  path → query → JSON → form 的顺序取值；`user()` 解析 Bearer Token 并附 `role_name` / `permissions`。
- `Router`：`@router.get("/cases/{id}")` 把 `{id}` 编译成 `(?P<id>[^/]+)`，支持 `match` / `allowed`。
- 统一响应 `{code:0, msg, data}`，异常按类型映射：`HTTPError→其 status`、`ValueError→400`、其他→500。
- `SSEStream`：`server.py` 特殊处理，逐帧写 `event:` / `data:` 并 flush。
- `serve_static`：从 `config.FRONT_DIR` 取文件，`Path.relative_to` 防目录穿越，目录 → `index.html`。

---

## 十、前端（零依赖）

不用构建工具，手写一个 Vue3 风格的极小运行时：

- `js/core.js`：`h()` 建 vnode、`reactive()` 用 Proxy 深响应（`requestAnimationFrame` 合并重渲染）、
  `mount()` 走 vdom diff、`createDom` / `toDom`（把 vnode 转真实 DOM，供弹窗等原生容器）。
- `js/ui.js`：`toast` / `modal` / `tag` / `barChart` / `pagination` 等组件。
- `js/app.js`：路由（hash）、侧边导航、通知轮询、深链（`#cases?case=<id>`）。
- `js/pages/*.js`：十个页面，每个导出 `render()` 与 `load()`。

**踩过的坑（三条都与 vdom diff 有关）**：

1. **响应式 push 不包装新对象**：`reactive()` 的 `get` 只在读取已存在对象时包装，
   `arr.push({...})` 进去的裸对象后续赋值不触发重渲染。修法：`set` 陷阱与数组变异器都对写入值调 `reactive()`，
   并用 `WeakSet` 防多层代理。
2. **falsy 判断导致文本节点重复**：`if (!nv)` 把空字符串当成「无节点」，
   「空串→文字」被当成新增子节点 append，命中溯源行出现两份。改成显式 `nv == null` 判断。
3. **删除节点下标错位**：`for i < total` 里 `remove()` 后再取 `childNodes[i]` 会错位，
   切页后残留上一页卡片。改成「先 patch 公共前缀 → 从尾部 append → 从后往前删」。

**弹窗**：`UI.openModal({body})` 的 `body` 可以是 vnode，内部用 `Mini.toDom()` 转 DOM；
有 `paint()` 手工重绘的弹窗必须传**真实 DOM 容器**，且先 `openModal` 再 `paint()`（`paint` 依赖 `body.isConnected`）。

---

## 十一、验证方式（无浏览器自动化时）

环境里没有 node / playwright，验证靠三样：

1. **语法校验**（JavaScriptCore，经 `osascript`）：
   ```bash
   osascript -l JavaScript -e 'ObjC.import("Foundation");
     var p="js/pages/cases.js";
     var s=$.NSString.stringWithContentsOfFileEncodingError(p,$.NSUTF8StringEncoding,null).js;
     try { new Function(s); "PARSE_OK" } catch(e) { "PARSE_ERR: "+e.message+" @line "+e.line }'
   ```
2. **CDP 真实时间驱动**：`--headless=new --remote-debugging-port=9222` 启 Edge，
   手写 WebSocket 客户端（`Page.navigate` / `Runtime.evaluate` / `Page.captureScreenshot`）。
   **纯 `--virtual-time-budget` 截图会停在流式中途**，SSE 页面必须用真实时间。
3. **弹窗必须关动画**：`.modal-mask { animation: fade .18s }` 的 `from { opacity: 0 }`
   在 headless 虚拟时间下不推进，截图里弹窗不可见（DOM 其实在）。注入
   `<style>*,*::before,*::after{animation:none!important;transition:none!important}</style>`。

后端验证直接 `curl`（登录取 token 后带 `Authorization: Bearer`）。
`/api/dashboard/system` 返回 `python` / `engine` / `degrade` / `db` / `scheduler` / `tools`，可一眼确认降级状态。

---

## 十二、如果要继续扩展

按「改动量从小到大」排：

1. **加一条法条 / 一个罪名**：不用改代码，两条路都行——
   `seed.py` 的 `DICT_SEED` / `KNOWLEDGE_SEED`（改完重播种），
   或运行时在「阴阳设置 → 字典管理」新增 `dict_type='crime'` 一项 + 知识库新增一篇法条。
   `law.match_crime` 每次调用都从 `sys_dict` 读罪名，新增的罪名立刻可判（原词命中 +4.0）；
   `law.match_penalty` 对没配过的罪名用默认基准 365 天 / 刀山狱，要自定义量刑再补 `base_days` 与 `mapping` 两张表。
2. **加一个业务动作**：`tools.py` 里 `@tool(...)` 注册，`crew.py` 里 `self.use()` 调用，前端加按钮。
3. **加一个流程节点**：`graph.py` 的 `NODE_SEQUENCE` / `NODE_META` / `NODE_PROGRESS` / `graph_def()` 边定义，
   `crew.py` 里加 Agent 并注册进 `AGENTS`。
4. **LangGraph 已真接入（2026-11-05）**：`run_flow()` 现在是分派器，装了 `langgraph`
   且 `DIFU_FLOW_ENGINE≠builtin` 就走 `agents/langgraph_engine.py` 的 StateGraph，
   否则降级 `_run_flow_builtin()`（原纯标准库循环）。加节点时**两个引擎共用**
   `NODE_SEQUENCE`，图会在 `get_compiled()` 里按它自动挂节点与条件边，不用改第二份。
   三条硬约束别踩：① state 必须用 `TypedDict` + reducer，不能用 `StateGraph(dict)`；
   ② 任何新写入业务 state 的字段要么在 `FlowState` 声明、要么走 `payload` 通道，
   否则会被 StateGraph 静默丢弃；③ `recursion_limit` 必须放 `config` 里。
   想切回旧引擎对比：`DIFU_FLOW_ENGINE=builtin ./start.sh`。
5. **换 MySQL**：`DB_ENGINE=mysql`，`db.adapt()` 已做 `?`→`%s` 与 `AUTOINCREMENT`→`AUTO_INCREMENT`。
6. **接真模型**：前端「阴阳设置 → 模型配置」点**编辑**，填 Base URL / Model / API Key 保存，
   `llm.active_config()` 与 `config.refresh_degrade()` 都立刻生效，无需重启。
   对应的接口是 `PUT /api/models/{id}`（`model:write` 权限、`audit_log` 留痕、
   `api_key` 留空或传脱敏串都不覆盖原 Key）。

---

## 十三、目录

```
地府阴差管理系统/
├── README.md                 交付说明（16 章）
├── start.sh                  一键启动
├── docs/
│   ├── ARCHITECTURE.md       本文
│   └── screenshots/          12 张页面截图
├── FastAPI/
│   ├── run.py                入口（argparse → server.serve）
│   ├── app/
│   │   ├── config.py         全部 env 配置 + 降级探测
│   │   ├── schema.py         21 张表 DDL + 索引
│   │   ├── db.py             sqlite3 DAL
│   │   ├── security.py       口令/Token/RBAC/行级过滤/双日志/通知
│   │   ├── seed.py           种子数据
│   │   ├── demo.py           三条演示卷宗
│   │   ├── server.py         HTTP 服务 + 巡检线程
│   │   ├── api/              base / auth / routes
│   │   └── agents/           graph / crew / tools / llm / rag / multimodal / scheduler / chat
│   ├── data/                 difu.db + vector_store.json
│   └── uploads/              上传的证据文件
└── frontVue3/
    ├── index.html
    ├── css/main.css
    └── js/                   core / api / ui / app + pages/
```
