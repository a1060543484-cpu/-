"""五个 Agent 的办案逻辑。

每个 Agent 都是「先思考（可调大模型）→ 再动手（调工具真写库）」：
有模型 Key 时由大模型给出结构化决策；无 Key 时走内置规则推理，
两条路径产出的字段完全一致，流程不会因缺 Key 而中断。
"""
from __future__ import annotations

import json
import re
import time

from .. import db, security
from . import llm, rag, tools


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}


# ---------------------------------------------------------------- 判词天数对齐
# 判官是「模型写判词 + 规则算刑期」两条线并行的：模型会在判词里自己写一段
# 「沸油煎熬涤荡罪业二千九百二十日」，而这个数字往往是它从上下文里抄来的
# 规则基准；真正落库的 sentence_days 又是按最终罪名重算的另一份。
# 两边各写各的，卷宗上就会出现「判词 2920 日 / 刑期 3285 天」，审计链自相矛盾。
# 下面这几个函数只做一件事：把判词里的天数改到与落库刑期一致。
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}
_CN_CHARS = "零〇一二三四五六七八九十百千万两"
# 「日 / 天」前的数字：阿拉伯数字或中文数字，两条分支合成一个正则，
# 替换时保留原有数字风格（中文判词就写中文数字）与中间的空格。
_DAY_NUM_RE = re.compile(r"(\d{1,5})(\s*)([日天])|([" + _CN_CHARS + r"]{2,8})(\s*)([日天])")


def _cn_to_int(text: str) -> int | None:
    """中文数字转整数，支持到「万」级（判词里的刑期不会更大）。"""
    total, section, num = 0, 0, 0
    for ch in text or "":
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if unit == 10000:
                total += (section + num) * unit
                section, num = 0, 0
            else:
                section += (num or 1) * unit
                num = 0
        else:
            return None
    return total + section + num


def _int_to_cn(n: int) -> str:
    """整数转中文数字（3285 → 三千二百八十五），与判词的行文风格保持一致。"""
    if n <= 0:
        return "零"
    units = ["", "十", "百", "千"]

    def four(x: int) -> str:
        out, zero = "", False
        for i in range(3, -1, -1):
            d = (x // (10 ** i)) % 10
            if d == 0:
                zero = bool(out)
                continue
            if zero:
                out += "零"
                zero = False
            out += "一二三四五六七八九"[d - 1] + units[i]
        return out

    if n < 10000:
        s = four(n)
        return s[1:] if s.startswith("一十") else s   # 一十 → 十
    hi, lo = divmod(n, 10000)
    s = four(hi) + "万"
    if lo:
        s += ("零" if lo < 1000 else "") + four(lo)
    return s


def _align_desc_days(desc: str, days: int, fallback: str | None = None) -> str:
    """把判词里写的天数对齐到落库刑期。

    - `fallback` 给了（用于 penalty_desc）时，判词为空、或写着「免刑」却又有刑期，
      就整段换成规则给的刑罚条款——这类判词已经自相矛盾，改数字救不回来。
    - `fallback` 为 None（用于判决理由）时只改数字，不动文字。
    - 只处理「日 / 天」前的数字，「六载」「三载」这类并科刑的次要刑期不碰。
    """
    desc = (desc or "").strip()
    if days <= 0:
        # 免刑判决不该出现「服刑 N 日」字样
        return desc if fallback is None else (fallback or desc)
    if fallback is not None:
        if not desc or "免刑" in desc or "功过相抵" in desc:
            return fallback
    if not desc:
        return desc

    def _sub(m: re.Match) -> str:
        arabic, gap1, suf1, cn, gap2, suf2 = m.groups()
        raw, gap, suf = (arabic, gap1, suf1) if arabic else (cn, gap2, suf2)
        value = int(raw) if arabic else _cn_to_int(raw)
        if not value or value == days:
            return m.group(0)          # 本来就是对的，一个字符都不动
        # 「三日后」「七天之内」这类是时间副词不是刑期，改它会毁掉原文；
        # 同理「11月4日」这种日期。刑期最小也有 9 天（180×0.05），
        # 所以 30 以下一律当时间词放过。
        if value < 30:
            return m.group(0)
        nxt = desc[m.end():m.end() + 1]
        if nxt in ("后", "前", "内", "之", "以", "左", "右"):
            return m.group(0)
        fixed = str(days) if arabic else _int_to_cn(days)
        return fixed + (gap or "") + suf

    return _DAY_NUM_RE.sub(_sub, desc)


class Agent:
    code = "agent"
    name = "Agent"
    duty = ""
    node = ""

    def think(self, prompt: str, context: str, schema_hint: str) -> tuple[dict, dict]:
        """返回 (决策字典, 元信息)。无 Key 时返回空字典，由子类规则兜底。"""
        started = time.time()
        result = llm.chat([
            {"role": "system", "content": f"你是阴司「{self.name}」，职责：{self.duty}。"
                                          "严格输出 JSON，不要任何解释。"},
            {"role": "user", "content": f"【案情材料】\n{context}\n\n【任务】\n{prompt}\n"
                                        f"【输出 JSON 结构】\n{schema_hint}"},
        ])
        if not result:
            return {}, {"model": "本地规则引擎（未配置模型 Key）", "latency_ms": 0, "tokens": 0,
                        "degraded": True}
        return _extract_json(result.text), {"model": result.model, "latency_ms": result.latency_ms,
                                            "tokens": result.tokens, "degraded": False}

    def log(self, case_id: int, action: str, thought: str, meta: dict,
            tool_name: str = "", tool_input=None, tool_output=None) -> None:
        security.agent_log(case_id, self.code, self.name, self.node, action, thought,
                           tool_name, tool_input, tool_output,
                           meta.get("model", ""), meta.get("latency_ms", 0), meta.get("tokens", 0))

    def use(self, ctx: dict, name: str, **kwargs):
        """调工具 + 记录一条 agent_log（工具入参/出参都可回放）。"""
        out = tools.call(name, ctx, **kwargs)
        # 工具注册表里本来就写了中文描述，日志直接用它当动作名，
        # 不再把 "调用工具 evidence.check_material" 这种代码串写给人看。
        # 机器标识仍完整保留在 tool_name / tool_input / tool_output 里，可回溯。
        label = tools.TOOLS.get(name, {}).get("description", "业务操作")
        self.log(ctx.get("case_id", 0), label,
                 f"已读写业务库，完成「{label}」。", {"model": "tool"},
                 tool_name=name, tool_input=kwargs, tool_output=out)
        return out


# ------------------------------------------------------------------ 阴差 Agent
class ClerkAgent(Agent):
    code, name, node = "clerk", "阴差 Agent", "intake"
    duty = "案件受理、验明正身、材料齐全性校验、证据归档"

    def run(self, case_id: int) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        case = tools.call("case.get", ctx, case_id=case_id)
        dec = case.get("deceased") or {}
        evis = case.get("evidence") or []
        context = (f"亡魂：{dec.get('name','')}，{dec.get('gender','')}，卒于 {dec.get('death_date','')}，"
                   f"死因：{dec.get('cause_of_death','')}，籍贯：{dec.get('hometown','')}。\n"
                   f"生平：{dec.get('life_summary','')}\n"
                   f"证据：{len(evis)} 件 —— " + "；".join(
                       f"{e.get('evi_type')}:{(e.get('summary') or e.get('file_name'))[:40]}" for e in evis))
        check = tools.call("evidence.check_material", ctx, case_id=case_id)
        decision, meta = self.think(
            "判断卷宗材料是否齐全，并给出受理结论。",
            context,
            '{"complete": true/false, "missing": ["缺失项"], "comment": "受理意见"}',
        )
        # 材料齐全与否是「硬门槛」，必须以规则检查为准，不能让模型单方面否决：
        # 接真模型后实测过这个坑——规则判「齐全」、模型却说「缺证人证词正文」，
        # 补完再问又冒出「缺官方验状」，每轮都能发明新缺失项，卷宗永远进不了审理。
        # 所以规则说齐全就齐全；规则说不齐时，缺项也只采信规则那份可枚举清单。
        # 模型的价值保留在「受理意见」里，作为附注写进卷宗备注。
        complete = bool(check["complete"])
        missing = list(check["missing"])
        advisory = [str(m) for m in (decision.get("missing") or [])
                    if str(m) and str(m) not in missing]
        if advisory and not complete:
            self.log(case_id, "补充建议",
                     "规则清单之外，模型另建议补充：" + "、".join(advisory[:3]), meta)
        thought = (f"材料校验：{'齐全' if complete else '不全'}；"
                   f"{'缺：' + '、'.join(missing) if missing else '身份、生平、证据三项齐备'}。")
        self.log(case_id, "材料校验", thought, meta,
                 tool_name="evidence.check_material", tool_input={"case_id": case_id}, tool_output=check)

        if not complete:
            out = self.use(ctx, "evidence.supplement_notice", case_id=case_id, missing=missing)
            self.log(case_id, "受理挂起", "材料不全，生成补证通知并挂起，等待阴差补证。", meta)
            return {"halt": True, "status": "suspended", "progress": 20,
                    "message": f"材料不全，已发补证通知（{'、'.join(missing)}）", "goto": "intake"}

        self.use(ctx, "case.update", case_id=case_id,
                 fields={"status": "judging", "current_node": "judge", "progress": 35,
                         "clerk_id": ctx.get("clerk_id", 0)})
        self.use(ctx, "case.note", case_id=case_id, node="intake",
                 content=decision.get("comment") or "验明正身，材料齐备，移交判官审理。",
                 actor_type="agent")
        db.update("deceased", {"status": "审理中", "updated_at": db.now()},
                  "id=?", (case.get("deceased_id"),)) if case.get("deceased_id") else None
        self.log(case_id, "受理通过", "材料齐备，移交判官 Agent 审理。", meta)
        return {"halt": False, "goto": "judge", "status": "judging", "progress": 35,
                "message": "受理通过，移交判官"}


# ------------------------------------------------------------------ 判官 Agent
class JudgeAgent(Agent):
    code, name, node = "judge", "判官 Agent", "judge"
    duty = "罪名认定、功德分核算、量刑建议、风险分级、出具判决书"

    def run(self, case_id: int) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        case = tools.call("case.get", ctx, case_id=case_id)
        dec = case.get("deceased") or {}
        evis = case.get("evidence") or []
        evi_text = " ".join(f"{e.get('ocr_text','')} {e.get('summary','')}" for e in evis)

        # 善恶簿基线（入册时登记的功德/罪业）必须带进来：否则证据里关键词没覆盖到的
        # 善恶行径会被整体抹掉，好人也会因为「证据没提到」而被算成零功德。
        merit = self.use(ctx, "merit.calculate", life_summary=dec.get("life_summary", ""),
                         evidence_text=evi_text,
                         merit_base=dec.get("merit_score") or 0,
                         sin_base=dec.get("sin_score") or 0)
        crime = self.use(ctx, "law.match_crime", text=f"{dec.get('life_summary','')} {evi_text}")
        # 罪名可能为空（善恶簿上无实罪）——检索词退回案情本身，避免空查询
        q = f"{crime['crime']} 量刑 {dec.get('life_summary','')[:60]}" if crime["crime"] \
            else f"功德 轮回 {dec.get('life_summary','')[:60]}"
        hits = self.use(ctx, "knowledge.search", query=q)
        # 判决理由要引法条，别引到证据切片上：优先法典库/判例库
        hits = sorted(hits, key=lambda h: 0 if h.get("category") in ("法典库", "判例库") else 1)
        penalty = self.use(ctx, "law.match_penalty", crime=crime["crime"], sin_score=merit["sin_score"])

        context = (f"亡魂：{dec.get('name','')}（功德 {dec.get('merit_score',0)}，罪业 {dec.get('sin_score',0)}）\n"
                   f"生平：{dec.get('life_summary','')}\n证据摘录：{evi_text[:600]}\n"
                   f"规则初判：罪名={crime['crime']}；功德={merit['merit_score']}；罪业={merit['sin_score']}；"
                   f"刑罚={penalty['penalty_desc']}（{penalty['sentence_days']}天）\n"
                   f"法典检索：{hits[0]['content'][:200] if hits else '无'}")
        decision, meta = self.think(
            "依《酆都法典》认定罪名、核算功德分与罪业分、给出量刑与风险等级（低/中/高）。",
            context,
            '{"crime":"","law_article":"","merit_score":0,"sin_score":0,"sentence_days":0,'
            '"penalty_desc":"","risk_level":"低|中|高","reason":"判决理由","next_life_hint":""}',
        )

        crime_name = str(decision.get("crime") or crime["crime"] or "无咎").strip()
        # 模型对无罪的表述五花八门（「无」「无罪」「无罪名可认」），
        # 统一收敛成「无咎」，否则卷宗里的罪名栏会出现一个孤零零的「无」字，
        # 也绕过了下面 crime_name == "无咎" 的免复核判断。
        if crime_name in ("", "无", "无罪", "无罪名", "无罪名可认", "None", "null"):
            crime_name = "无咎"
        merit_score = float(decision.get("merit_score", merit["merit_score"]))
        sin_score = float(decision.get("sin_score", merit["sin_score"]))
        # 模型给的罪名是它自己拟的（「伪造地契夺田产、逼死邻人一家三口」这类复合罪名），
        # 而上面那份 penalty 是按规则罪名算的。若不按最终罪名重算，模型改判重罪时
        # 刑罚会沿用规则的「无咎→免刑」，出现「罪业 90 却免刑」这种自相矛盾的判决。
        penalty = self.use(ctx, "law.match_penalty", crime=crime_name, sin_score=sin_score)
        days = int(decision.get("sentence_days") or penalty["sentence_days"] or 0)
        risk = decision.get("risk_level") or (
            "高" if sin_score >= 80 or crime_name in ("杀人", "贪污") and sin_score >= 60
            else "中" if sin_score >= 40 else "低")
        if crime_name == "无咎":
            risk = "低"                        # 无实罪者不该被提请复核
        model_risk = risk
        if sin_score >= 80:
            risk = "高"
        reason = decision.get("reason") or (
            f"依据《{hits[0]['title']}》与《功德核算通则》：{dec.get('name','该魂')}功德分 {merit_score}、"
            f"罪业分 {sin_score}，{penalty['penalty_desc']}，判 {penalty['prison']} 服刑 {days} 日。")
        # 模型写的理由里常自带「风险等级为中」这类结论，而罪业分触顶时规则会把风险强制升为高。
        # 两者不一致会让审计链自相矛盾（判决理由说中、字段却是高），所以这里显式补一句说明，
        # 而不是悄悄改字段——读卷宗的人要能看懂「为什么模型说中、系统标高」。
        if risk != model_risk and "风险" in reason:
            reason = reason.rstrip("。") + (
                f"（注：罪业分已达 {sin_score}，依《酆都法典》触顶条款风险强制升为「高」，须阎王复核后执行。）")
        years = round(days / 365, 2)
        # 判词与判决理由里的天数统一按落库刑期改一遍：模型常把上下文里的
        # 规则基准（如「二千九百二十日」）抄进判词，而 days 是按最终罪名重算的，
        # 两边不一致会让卷宗自相矛盾（实测「判词 2920 / 刑期 3285」）。
        desc = _align_desc_days(decision.get("penalty_desc") or "", days,
                               penalty["penalty_desc"])
        reason = _align_desc_days(reason, days)

        self.use(ctx, "deceased.update_merit", deceased_id=case.get("deceased_id") or 0,
                 merit_score=merit_score, sin_score=sin_score)
        judgment = self.use(ctx, "judgment.create", case_id=case_id, crime=crime_name,
                            law_article=decision.get("law_article") or penalty["law_article"],
                            merit_score=merit_score, sin_score=sin_score,
                            sentence_type="有期徒刑" if days else "免刑",
                            sentence_years=years, sentence_days=days,
                            penalty_desc=desc,
                            risk_level=risk, reason=reason,
                            evidence_refs=[e["id"] for e in evis])
        self.log(case_id, "出具判决书",
                 f"罪名 {crime_name}，功德 {merit_score}，罪业 {sin_score}，风险 {risk}，"
                 f"量刑 {days} 日。理由：{reason[:80]}", meta)

        if risk == "高":
            appr = self.use(ctx, "approval.create", case_id=case_id,
                            title=f"疑难案件复核·{case.get('case_no')}",
                            reason=f"风险等级「高」（罪业 {sin_score}，罪名 {crime_name}），须阎王复核后执行。",
                            risk_level=risk)
            self.use(ctx, "case.update", case_id=case_id,
                     fields={"risk_level": risk, "verdict": crime_name})
            self.log(case_id, "申请复核",
                     f"风险高，生成待办审批单 {appr['approval_no']}，流程挂起等待阎王批复。", meta)
            return {"halt": True, "status": "reviewing", "progress": 60,
                    "message": f"重大疑难案件，已挂起待阎王复核（{appr['approval_no']}）",
                    "goto": "review"}

        self.use(ctx, "case.update", case_id=case_id,
                 fields={"status": "executing", "current_node": "execution", "progress": 65,
                         "risk_level": risk, "verdict": crime_name})
        return {"halt": False, "goto": "execution", "status": "executing", "progress": 65,
                "message": f"判决完成（{crime_name}，风险{risk}），移交刑罚司"}


# ------------------------------------------------------------------ 刑罚司 Agent
class ExecutionAgent(Agent):
    code, name, node = "execution", "刑罚司 Agent", "execution"
    duty = "匹配刑罚条款、创建服刑记录、跟踪刑期与剩余天数"

    def run(self, case_id: int, force: bool = False) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        case = tools.call("case.get", ctx, case_id=case_id)
        judgment = case.get("judgment") or {}
        dec = case.get("deceased") or {}
        if not judgment:
            return {"halt": False, "goto": "judge", "status": "judging", "progress": 40,
                    "message": "缺少判决书，退回判官重审"}
        existing = case.get("execution")
        if existing:
            # 已有服刑记录时绝不重复建档：先刷新进度，期满才放行
            prog = self.use(ctx, "execution.progress", case_id=case_id)
            if prog.get("expired"):
                self.log(case_id, "刑期届满",
                         f"{existing['prison']} 刑期已满，移交轮回司判定六道。", {"model": "定时巡检"})
                return {"halt": False, "goto": "reincarnation", "status": "reincarnating",
                        "progress": 80, "message": "刑期届满，移交轮回司"}
            self.log(case_id, "服刑中",
                     f"{existing['prison']} 服刑，剩余 {prog.get('remaining_days', 0)} 天，等待期满自动流转。",
                     {"model": "定时巡检"})
            return {"halt": True, "status": "executing", "progress": 70,
                    "message": f"{existing['prison']}服刑中，剩余 {prog.get('remaining_days', 0)} 天"
                               "（可点「模拟期满」继续）",
                    "goto": "execution"}

        # 优先用判决里存的天数：sentence_years 只保留两位小数，
        # 180 天存成 0.49 年再乘回 365 就成了 178 天，判词写「一百八十日」、
        # 服刑记录却少两天，对不上账。
        days = int(judgment.get("sentence_days") or 0) or int(judgment.get("sentence_years", 0) * 365) or 0
        if days <= 0:                          # 免刑：不建服刑记录，直接入轮回
            self.log(case_id, "免刑放行", "判决免刑，无服刑记录，直接移交轮回司。", {"model": "工具"})
            return {"halt": False, "goto": "reincarnation", "status": "reincarnating",
                    "progress": 80, "message": "免刑，直接进入轮回判定"}
        out = self.use(ctx, "execution.create", case_id=case_id, judgment_id=judgment["id"],
                       prison=self._prison_of(ctx, judgment), sentence_days=days,
                       punishment_desc=judgment.get("penalty_desc", ""))
        self.log(case_id, "创建服刑记录",
                 f"{out['prison']}，刑期 {days} 天，至 {out['end_date']}。", {"model": "工具"})
        return {"halt": True, "status": "executing", "progress": 70,
                "message": f"{out['prison']}服刑中，刑期 {days} 天（可点「模拟期满」继续）",
                "goto": "execution"}

    @staticmethod
    def _prison_of(ctx: dict, judgment: dict) -> str:
        desc = judgment.get("penalty_desc", "") or ""
        # 按罪名查刑罚表：这是主刑的牢狱，与判官量刑用的是同一张映射。
        # 判词里常并科好几个牢狱（「沸油煎熬…期满转刀山狱三载、血池狱二载」），
        # 若只取「判词里第一个出现的牢狱」，贪污案的主刑就会被记成次要的刀山狱。
        fallback = tools.call("law.match_penalty", ctx,
                              crime=judgment.get("crime", ""),
                              sin_score=float(judgment.get("sin_score") or 0))
        main = fallback.get("prison") or "刀山狱"
        # 判词未必点狱名：石压狱的判词写的是「巨石压身，偿其重负」，
        # 只按 dict_key 匹配会漏掉主刑、只剩并科的「刀山狱」被判成唯一命中。
        # 所以狱名与刑罚描述两种写法都算命中。
        found = []
        for p in db.all_rows("sys_dict", "dict_type='punishment'", order="sort ASC"):
            key, val = p["dict_key"], (p["dict_value"] or "").split("，")[0]
            if key in desc or (val and val in desc):
                found.append(key)
        if main in found:                 # 主刑牢狱在判词里出现过 → 就用它
            return main
        if len(found) == 1:               # 判词只点了唯一一个牢狱 → 尊重判词
            return found[0]
        return main                       # 都没提到或并科多个 → 按罪名定的主刑


# ------------------------------------------------------------------ 轮回司 Agent
class ReincarnationAgent(Agent):
    code, name, node = "reincarnation", "轮回司 Agent", "reincarnation"
    duty = "按功德分匹配六道、生成转世文书"

    def run(self, case_id: int) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        case = tools.call("case.get", ctx, case_id=case_id)
        dec = case.get("deceased") or {}
        merit = float(dec.get("merit_score") or 0)
        sin = float(dec.get("sin_score") or 0)
        realm = self.use(ctx, "realm.match", merit_score=merit, sin_score=sin)
        decision, meta = self.think(
            "依据功德分与罪业分判定六道归属，并给出一句来世安排。",
            f"亡魂：{dec.get('name','')}；功德 {merit}；罪业 {sin}；规则判定：{realm['realm_reason']}",
            '{"realm":"天道|人道|阿修罗道|畜生道|饿鬼道|地狱道","next_life":"来世安排一句话"}',
        )
        realm_code = decision.get("realm") or realm["realm"]
        next_life = decision.get("next_life") or self._next_life(realm_code, dec.get("name", ""))
        out = self.use(ctx, "reincarnation.create", case_id=case_id, merit_score=merit,
                       realm=realm_code, realm_reason=realm["realm_reason"], next_life=next_life)
        self.log(case_id, "生成转世文书",
                 f"功德 {merit} → {realm_code}；{next_life}", meta)
        return {"halt": False, "goto": "mengpo", "status": "reincarnating", "progress": 88,
                "message": f"判定为{realm_code}，转世文书已生成"}

    @staticmethod
    def _next_life(realm: str, name: str) -> str:
        return {
            "天道": f"{name}功德圆满，超升天道，享清静之福。",
            "人道": f"{name}复为人身，投生积善之家，续修功德。",
            "阿修罗道": f"{name}嗔心未泯，生于修罗，须再历战火以消嗔业。",
            "畜生道": f"{name}愚痴罪重，堕为耕牛，以苦力偿旧债。",
            "饿鬼道": f"{name}贪吝成性，堕为饿鬼，饥火焚身，须施食百次方得解脱。",
            "地狱道": f"{name}罪业滔天，堕入地狱，历劫方有出期。",
        }.get(realm, f"{name}依功德另作安排。")


# ------------------------------------------------------------------ 孟婆 Agent
class MengpoAgent(Agent):
    code, name, node = "mengpo", "孟婆 Agent", "mengpo"
    duty = "忘川登记、奉孟婆汤、注销前尘"

    def run(self, case_id: int) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        out = self.use(ctx, "mengpo.register", case_id=case_id)
        self.log(case_id, "忘川登记", "孟婆汤一盏，前尘尽忘，登记入册。", {"model": "工具"})
        return {"halt": False, "goto": "archive", "status": "completed", "progress": 95,
                "message": "忘川登记完成，转入归档"}


# ------------------------------------------------------------------ 归档 Agent（判例回灌）
class ArchiveAgent(Agent):
    code, name, node = "archive", "卷宗司 Agent", "archive"
    duty = "卷宗归档、判例回灌知识库"

    def run(self, case_id: int) -> dict:
        ctx = {"case_id": case_id, "agent": self.code, "agent_name": self.name, "node": self.node}
        case = tools.call("case.get", ctx, case_id=case_id)
        out = self.use(ctx, "case.archive", case_id=case_id, verdict=case.get("verdict", ""))
        self.log(case_id, "归档", f"卷宗 {case.get('case_no')} 已归档，判例回灌知识库。", {"model": "工具"})
        return {"halt": True, "goto": "END", "status": "completed", "progress": 100,
                "message": "已归档，判例已回灌知识库"}


AGENTS = {
    "intake": ClerkAgent, "judge": JudgeAgent, "execution": ExecutionAgent,
    "reincarnation": ReincarnationAgent, "mengpo": MengpoAgent, "archive": ArchiveAgent,
}
