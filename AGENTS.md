# 协作契约 · 地府阴差管理系统

这个项目由 AI Agent 写代码。下面这些是**为了让代码不变成黑箱**而定的硬规则。
每次会话开始会被自动注入，不需要用户重复交代。

---

## 一、四条不可协商的规则

### 1. 契约先行：先给签名，再给实现

改任何函数之前，先在回复里写出：

```
函数：law_match_penalty(ctx: dict, crime: str = "", sin_score: float = 0) -> dict
返回：{"prison": str, "sentence_days": int, "penalty_desc": str, "matched": str}
边界：crime 为空 → days=0；sin_score<=0 → days=0；复合罪名 "A、B" → 取最高刑
```

用户 review 完这个契约，再动手写实现。**不要**一边想一边直接把代码写完。

review 一个签名的成本是 review 实现的 1/10，而签名错了实现必然全错。

### 2. 验收标准先行：动手前写清「怎么算做完」

反例（禁止）：「优化一下量刑逻辑」「完善一下错误处理」——**这种模糊描述本身就是黑箱的开始**。

正例：

```
验收：
  law_match_penalty("盗窃", 60).sentence_days == 365
  law_match_penalty("杀人", 60).sentence_days == 3285
  law_match_penalty("无咎", 0).sentence_days == 0
  law_match_penalty("贪污", 96).sentence_days == 2920   # 1825 × 1.6 封顶
```

判据必须能被一条命令跑出来。

### 3. 只交「命令 + 输出」，不交「我改好了」

**「改好了」「已修复」「应该没问题」这三句话在本项目里是无效输出。**

汇报必须长这样：

```
$ ./difu.sh verify
✓ 语法 22 个 py
✓ 路由 51 条
✓ 业务规则 34 passed
✗ 双引擎等价性  case 91: builtin=3285 langgraph=2920

真实输出：
  FAIL: test_penalty_cap (tests.test_rules)
```

任何没有可复现输出的完成声明，**按未完成处理**。

### 4. 一步一 commit，message 写「为什么」

改完一个独立逻辑就提交，不要攒成一坨。

```
✗ 6ae62a9 feat: 酆都阴司 AI 办案系统（LangGraph 双引擎）   ← 一次进了整个系统
✓ fix(penalty): 复合罪名落空导致重罪被判免刑
    模型给「伪造地契夺田产、逼死邻人」这类复合罪名，整串查字典必然落空，
    落空后 days=0，一个 sin=90 的谋财害命案就被判「免刑」。改为在罪名串里
    按别名找已知罪名，取最长匹配。
```

body 写清 **现象 → 根因 → 为什么这样修**。改错了 `git revert` 单个 commit 就能退，否则你面对的是一团混在一起的 diff。

---

## 二、本项目踩过的坑（改代码前先看）

这些都是真实踩过并修好的，重犯一次就是纯浪费。

### 后端

- **`case` 是 SQL 保留字** —— 一律用 `case_file`，写 `case` 会 `sqlite3.OperationalError: near "case": syntax error`。
- **`db.execute()` 必须无条件 `commit()`** —— 曾经只在 MySQL 分支提交，SQLite 下插入静默丢失，表现为「数据存不进去但也不报错」。
- **`security.case_scope()` 返回裸条件**（`judge_id=N OR ...`），不带 `AND` 前缀。带前缀会在 `where` 为空时拼成 `SELECT * FROM case_file AND ...` → `near "AND": syntax error`。
- **`judge_id` 默认 0 不是 NULL** —— 行级过滤要写成 `(judge_id={id} OR judge_id=0 OR judge_id IS NULL)`，否则新立卷宗判官一条都看不见。
- **`reset_db()` 只清 16 张业务表**（`db.BUSINESS_TABLES`），保留 `user`/`role`/`jurisdiction`/`sys_dict`/`model_config`。清 `user` 会让所有 token 失效，清 `model_config` 会冲掉用户配的 Key。
- **审批是 review 节点的唯一出口** —— 任何「推进流程」在 `node=="review"` 时必须先查 `SELECT id FROM approval WHERE case_id=? AND status='pending'`，有挂起就原地不动。曾经「推进流程」直接绕过复核，是最严重的一次缺陷。
- **LLM 的 `complete`/`missing` 只能当建议** —— 规则检查 `check["complete"]` 才是硬门槛。否则模型单方面说「材料不全」会造成补证死循环。
- **`merit_calculate` 必须传 `merit_base`/`sin_base`** —— 先与善恶簿基线取大，再算自首减半（`sin *= 0.5`）。顺序反了自首就白减。
- **兜底 OCR 是「模拟文本」** —— `【离线…模拟】…。` 整段剔除再用，里面的「收据、借据、账册」是模板词，混进量刑会把施粥善人判成贪官。

### 业务不变量（改完必须重新验证）

```
1. 善人不能被误判：merit 高、sin=0 的亡魂 → 罪名「无咎」、免刑、轮回判人道
2. 判词天数 == sentence_days：_align_desc_days() 保证，改量刑逻辑后必测
3. 同一案子 builtin 与 langgraph 两个引擎的判决必须一致
4. 无 Key 时全流程必须走通（降级不是异常路径，是主路径之一）
5. 判词天数会因模型每次生成而变（2920/3285/3650 都出现过），但必须落在同一值域
```

### 前端

- `Mini.toDom(vnode)` 才能拿到真实 DOM，`appendChild(h(...))` 会 `TypeError: parameter 1 is not of type 'Node'`。
- `patchChildren` 用显式 `null/undefined` 判断，别用 falsy —— `''` 不是「无节点」。
- 弹窗 body 必须是真实 `document.createElement('div')`，且先 `UI.openModal` 再 `paint()`（`paint()` 依赖 `body.isConnected`）。
- 深链 `captureDeepLink()` 必须在 `go()` **之前**调用。

### 环境

- 系统 `python3`（3.14.5）**没装 langgraph**；`/Users/a11/Desktop/waresoft/Python/.venv/` 里有（1.2.11）。必须走 `./start.sh` / `./difu.sh`，它们会探测解释器。
- Python 3.14 的 openssl 目录是空的 → `SSL: CERTIFICATE_VERIFY_FAILED`，`llm.py` 里走 `_ssl_context()` 用 `/etc/ssl/cert.pem`。
- 无 node / 无 pnpm，前端语法校验用：`osascript -l JavaScript -e 'new Function(...)'`。

---

## 三、技术约束

- **零依赖是底线**：`./start.sh` 不装任何东西必须能跑。新增第三方依赖要先问，且必须有降级路径。
- **标准库优先**：HTTP 层 `http.server`、DAL `sqlite3`、测试 `unittest`。
- **降级不是异常**：无 Key → 规则引擎；无 langgraph → 内置状态机；无 numpy → 纯 Python 打分。每条都要能跑通。
- **所有副作用要留痕**：工具调用写 `tool_call`、Agent 推理写 `agent_log`、人工操作写 `audit_log`。日志三表**不参与级联删除**（留痕不能撤）。
- **业务规则进 `sys_dict`**：加罪名不用改代码。硬编码只保留在 `tools.py` 的基准表里，且必须在测试里锁住。

---

## 四、交付前自检

```bash
./difu.sh verify          # 一条命令跑完全部不变量
python3 -m unittest discover -s tests -v    # 只跑规则单测
```

两个都绿才算做完。**不要**用「我手工点了页面没问题」代替。
