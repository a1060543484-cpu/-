"""演示数据：用种子里的三位亡魂各建一份卷宗，跑通三条典型路径。

1. 赵德柱 —— 材料齐全、风险中 → 一路跑到服刑（停在 execution）
2. 钱氏   —— 贪污重罪、风险高 → 判官判决后挂起等阎王复核
3. 孙文才 —— 补一件证据后受理 → 斗殴误杀，自首减刑 → 堕入轮回
"""
from __future__ import annotations

import time

from . import db, security
from .agents import graph, rag


def _case_no() -> str:
    return f"DF-{time.strftime('%Y%m%d')}-{db.count('case_file') + 1:04d}"


def create_case(deceased_id: int, title: str, priority: str = "normal",
                judge_id: int = 0, clerk_id: int = 0, source: str = "manual") -> dict:
    dec = db.get("deceased", deceased_id)
    if not dec:
        raise ValueError("亡魂档案不存在")
    case_no = _case_no()
    cid = db.insert("case_file", {
        "case_no": case_no, "title": title or f"{dec['name']}亡魂案",
        "deceased_id": deceased_id, "jurisdiction_id": dec.get("jurisdiction_id") or 1,
        "status": "pending", "priority": priority, "risk_level": "待定",
        "current_node": "intake", "progress": 0, "judge_id": judge_id, "clerk_id": clerk_id,
        "flow_state": "{}", "summary": dec.get("life_summary", ""), "verdict": "",
        "source": source, "created_at": db.now(), "updated_at": db.now(), "closed_at": "",
    })
    return db.get("case_file", cid)


def add_evidence(case_id: int, evi_type: str, file_name: str, ocr_text: str, summary: str) -> int:
    case = db.get("case_file", case_id) or {}
    return db.insert("evidence", {
        "case_id": case_id, "deceased_id": case.get("deceased_id"), "evi_type": evi_type,
        "file_name": file_name, "file_path": "", "mime": "text/plain", "size": len(ocr_text),
        "ocr_text": ocr_text, "summary": summary, "chunk_id": 0, "status": "已解析",
        "created_at": db.now(),
    })


def build_demo() -> dict:
    """幂等：已有演示卷宗则跳过。"""
    if db.count("case_file") > 0:
        return {"skipped": True, "cases": db.count("case_file")}

    decs = {d["name"]: d for d in db.query("SELECT * FROM deceased")}
    created = []

    # 承办人按角色动态取：写死 id 会因为重建库后自增号变化而指到错误的人，
    # 导致判官登录后「自己承办的卷宗」一条都看不到（行级过滤按 judge_id 匹配）。
    def _uid(role: str) -> int:
        row = db.query_one("SELECT id FROM user WHERE role_code=? ORDER BY id", (role,))
        return int(row["id"]) if row else 0

    JUDGE, CLERK = _uid("judge"), _uid("clerk")

    # ---- 案例一：赵德柱（材料齐 → 中风险 → 服刑中）
    if "赵德柱" in decs:
        d = decs["赵德柱"]
        case = create_case(d["id"], "赵德柱盗伐林木、斗殴伤人案", judge_id=JUDGE, clerk_id=CLERK)
        add_evidence(case["id"], "text", "田界纠纷笔录.txt",
                     "赵德柱与邻人李某因田界争执，持锄伤人，李某左臂骨裂。"
                     "村中保长证言：赵某事后赔偿医药钱二十贯，并请人修补田埂。",
                     "斗殴伤人书证，含赔偿情节。")
        add_evidence(case["id"], "image", "林木盗伐现场.png",
                     "现场影像：村东杉木林被伐倒七株，树桩新鲜，地上留有独轮车辙印。",
                     "盗伐林木现场影像证据。")
        created.append({"name": "赵德柱", "case_no": case["case_no"], "path": "受理→判官→服刑"})
        graph.start_case(case["id"])

    # ---- 案例二：钱氏（贪污重罪 → 高风险 → 挂起待复核）
    if "钱氏" in decs:
        d = decs["钱氏"]
        case = create_case(d["id"], "钱氏侵吞赈灾粮款案", priority="high", judge_id=JUDGE, clerk_id=CLERK)
        add_evidence(case["id"], "text", "赈灾账册抄本.txt",
                     "丰都县赈灾账册抄本：万历四十四年拨粮二百石，实发八十石，"
                     "余一百二十石去向不明，账上批注为「损耗」，笔迹与钱氏相符。",
                     "赈灾账册缺额书证，指向侵吞。")
        add_evidence(case["id"], "audio", "灾民口述录音.mp3",
                     "灾民王氏口述：那年冬月，粮没发下来，我男人和娃都饿死了，"
                     "去县里问，钱主簿说账上只剩这些。",
                     "灾民口述录音，佐证饿毙事实。")
        add_evidence(case["id"], "image", "伪造账册页.png",
                     "影像：账册页面有明显涂改痕迹，数目由「二百石」改为「八十石」，"
                     "墨色新旧不一，疑为后改。",
                     "伪造账册影像，佐证篡改。")
        created.append({"name": "钱氏", "case_no": case["case_no"], "path": "受理→判官→挂起待复核"})
        graph.start_case(case["id"])

    # ---- 案例三：孙文才（先缺证据 → 补证 → 受理 → 轮回）
    if "孙文才" in decs:
        d = decs["孙文才"]
        case = create_case(d["id"], "孙文才醉酒误伤致死案", judge_id=JUDGE, clerk_id=CLERK)
        add_evidence(case["id"], "text", "自首呈文.txt",
                     "孙文才自首呈文：醉后与邻人争执，推搡致其跌倒撞石而亡，"
                     "愿以全部家资厚葬，并抚养其幼子成人。",
                     "自首与赔偿书证。")
        res = graph.start_case(case["id"])
        # 若因证据链不足挂起，补一件证据后继续
        if res.get("halt") and res.get("status") == "suspended":
            add_evidence(case["id"], "text", "邻人证言.txt",
                         "邻人张某证言：当日孙某醉态明显，王某先出言挑衅，"
                         "二人推搡间王某后退踩空，后脑撞石。孙某当即呼救并送医。",
                         "目击者证言，补足证据链。")
            graph.continue_flow(case["id"])
        # 轻罪案件直接把刑期推到届满，让演示里有一条「跑完全程」的卷宗，
        # 便于展示六道轮回判定与判例回灌知识库
        graph.simulate_expiry(case["id"])
        created.append({"name": "孙文才", "case_no": case["case_no"],
                        "path": "受理→判官→服刑→六道轮回→孟婆登记→归档"})

    return {"skipped": False, "cases": db.count("case_file"), "created": created}


def reset_and_build() -> dict:
    db.reset_db()
    from . import seed as seed_mod
    seed_mod.seed(force=True)
    return build_demo()
