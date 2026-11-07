"""初始化种子数据：角色、辖区、官吏、字典、法典/判例/善恶簿知识库、模型配置、演示卷宗。"""
from __future__ import annotations

from . import config, db, security
from .agents import rag

DICT_SEED: dict[str, list[tuple[str, str]]] = {
    "crime": [
        ("盗窃", "盗取他人财物，阳寿有损"),
        ("杀人", "夺人性命，罪业深重"),
        ("欺诈", "诓骗钱财，欺瞒良善"),
        ("贪污", "侵吞公帑，蠹国害民"),
        ("忤逆", "不孝父母，悖逆人伦"),
        ("邪淫", "败坏风俗，淫乱纲常"),
        ("诽谤", "捏造虚实，污人名节"),
        ("逃税", "隐匿税赋，欺瞒官府"),
        ("伤人", "斗殴致伤，恃强凌弱"),
        ("纵火", "焚毁屋舍，害及生灵"),
    ],
    "punishment": [
        ("刀山狱", "刀山剑树，割裂罪身"),
        ("油锅狱", "沸油煎熬，涤荡罪业"),
        ("拔舌狱", "拔舌犁耕，惩其妄语"),
        ("石压狱", "巨石压身，偿其重负"),
        ("寒冰狱", "寒冰困锁，冷其恶念"),
        ("血池狱", "血池沉沦，还其杀债"),
        ("磨盘狱", "碾磨其身，消其贪念"),
        ("铜柱狱", "铜柱烙体，惩其邪淫"),
    ],
    "realm": [
        ("天道", "功德极盛，超升天道"),
        ("人道", "功过相抵，复为人身"),
        ("阿修罗道", "嗔心未泯，生于修罗"),
        ("畜生道", "愚痴罪重，堕为畜生"),
        ("饿鬼道", "贪吝成性，堕为饿鬼"),
        ("地狱道", "罪业滔天，堕入地狱"),
    ],
    "jurisdiction": [
        ("东岳", "东岳大帝所辖"),
        ("酆都", "酆都鬼城所辖"),
        ("城隍", "阳世城隍所辖"),
        ("十殿", "十殿阎罗所辖"),
    ],
    "merit_level": [
        ("甲等", "功德分 ≥ 90，可超升天道"),
        ("乙等", "功德分 70-89，复为人身"),
        ("丙等", "功德分 50-69，需再修行"),
        ("丁等", "功德分 30-49，堕畜生道"),
        ("戊等", "功德分 < 30，堕地狱道"),
    ],
    "risk_level": [
        ("低", "常规案件，Agent 自动办结"),
        ("中", "需判官复核量刑"),
        ("高", "重大疑难，须阎王亲批"),
    ],
    "case_status": [
        ("pending", "待受理"), ("accepted", "已受理"), ("judging", "审理中"),
        ("reviewing", "待复核"), ("executing", "刑罚执行中"), ("reincarnating", "轮回判定中"),
        ("completed", "已归档"), ("rejected", "已驳回"), ("suspended", "已挂起"),
    ],
}

KNOWLEDGE_SEED = [
    {
        "title": "《酆都法典》· 盗窃罪条",
        "category": "法典库",
        "content": "凡盗取他人财物者，计赃论罪。赃满十贯，杖一百，罚入刀山狱服刑一载；"
                   "赃满五十贯，徒二年，罚入磨盘狱三载，功德分削三十；赃满百贯，"
                   "加役三年，堕畜生道。若盗赈灾之物、盗孤寡之财者，各加一等，罪加三分。"
                   "自首还赃者，减罪二等；屡犯不改者，累加其罪。",
    },
    {
        "title": "《酆都法典》· 杀人罪条",
        "category": "法典库",
        "content": "凡夺人性命者，罪莫大焉。故杀一人，堕地狱道，入血池狱九载，功德分清零；"
                   "斗殴误杀者，减一等，入油锅狱三载，功德分削八十；谋财害命者，加二等，"
                   "入刀山狱与血池狱并罚。若为救他人而不得已致死者，酌情减三等，须阎王亲裁。",
    },
    {
        "title": "《酆都法典》· 忤逆罪条",
        "category": "法典库",
        "content": "凡不孝父母、悖逆人伦者，入拔舌狱三载，功德分削五十，堕畜生道；"
                   "若殴伤尊长者，加二等，入石压狱五载，堕饿鬼道；致使尊长含恨而终者，"
                   "与杀人同罪论处。",
    },
    {
        "title": "《酆都法典》· 贪污罪条",
        "category": "法典库",
        "content": "凡侵吞公帑、蠹国害民者，按赃论罪。赃满百贯，入油锅狱五载，功德分削六十；"
                   "赃满千贯，入磨盘狱十载，堕饿鬼道；致使百姓流离失所者，加三等，"
                   "堕地狱道，永不超生。退还赃款、举报同党者，减罪一等。",
    },
    {
        "title": "《酆都法典》· 功德核算通则",
        "category": "法典库",
        "content": "功德分以百分为满。救人性命一命，记功德二十；济贫助困百贯，记功德十；"
                   "修桥铺路、施药救人，各记功德五；孝养父母终老，记功德三十；"
                   "教化乡里、劝人向善，记功德十五。功德分九十以上者，可超升天道；"
                   "七十以上者，复为人身；五十以上者，再修行一世；三十以上者，堕畜生道；"
                   "不足三十者，堕地狱道。",
    },
    {
        "title": "判例· 张某盗赈灾粮案",
        "category": "判例库",
        "content": "万历年间，张某盗取赈灾粮五十石，折银八十贯，致灾民饿毙三人。"
                   "判官认定：盗赈灾之物，罪加一等；致人饿毙，与杀人罪并论。"
                   "判入刀山狱六载、血池狱三载，功德分清零，堕饿鬼道。"
                   "此案确立「盗赈灾之物致人死亡，数罪并罚」之判例。",
    },
    {
        "title": "判例· 李某斗殴误杀案",
        "category": "判例库",
        "content": "李某因田界争执，持锄误杀邻人王某，事后自首并厚葬之。"
                   "判官认定：斗殴误杀，减故杀一等；自首减二等；厚葬补偿记功德五。"
                   "判入油锅狱一载，功德分削四十，复为人身。此案确立「误杀+自首」减刑判例。",
    },
    {
        "title": "判例· 王氏忤逆案",
        "category": "判例库",
        "content": "王氏常年辱骂老母，断其衣食，致老母冻饿而亡。"
                   "判官认定：忤逆致使尊长含恨而终，与杀人同罪。"
                   "判入拔舌狱三载、石压狱五载，功德分清零，堕地狱道。",
    },
    {
        "title": "善恶簿· 善行条目",
        "category": "善恶簿库",
        "content": "救人性命：功德 +20/命；济贫助困：功德 +10/百贯；修桥铺路：功德 +5/件；"
                   "施药救人：功德 +5/人；孝养父母终老：功德 +30；教化乡里劝善：功德 +15；"
                   "拾金不昧：功德 +3；放生护生：功德 +2/次；赈灾施粥：功德 +8/次。",
    },
    {
        "title": "善恶簿· 恶行条目",
        "category": "善恶簿库",
        "content": "盗窃：罪业 +10/十贯；杀人：罪业 +100/命；欺诈：罪业 +15；贪污：罪业 +40/百贯；"
                   "忤逆：罪业 +60；邪淫：罪业 +25；诽谤：罪业 +12；伤人：罪业 +30；"
                   "纵火：罪业 +50；逃税：罪业 +20。罪业逾百者，须阎王复核。",
    },
    {
        "title": "证据规则· 多模态证据采信",
        "category": "证据库",
        "content": "证据分书证、物证、人证、影像四类。影像证据须经视觉模型识别并转写为文字，"
                   "方可入卷；音证须经语音转写，注明说话人；书证须完整，缺页者须补证。"
                   "证据须与罪名一一对应，不得孤证定案；重大案件须三类以上证据相互印证。"
                   "证据链断裂者，退回阴差补证，不得径行判决。",
    },
    {
        "title": "办案规程· 卷宗流转",
        "category": "证据库",
        "content": "亡魂到案，阴差受理，验明正身，核其生平善恶，录其罪证，材料齐备方移交判官。"
                   "判官依《酆都法典》认定罪名、核算功德、量定刑罚，出具判决书。"
                   "重大疑难案件（风险高）须呈阎王复核，阎王批准后方可执行。"
                   "刑罚司依判决执行，刑期届满，轮回司按功德匹配六道，孟婆登记忘川，"
                   "最后归档，判例回灌知识库。",
    },
]

DEMO_DECEASED = [
    {"name": "赵德柱", "gender": "男", "birth_year": 1965, "death_date": "2026-09-12",
     "cause_of_death": "病故", "hometown": "东岳·青州府", "jurisdiction_id": 1,
     "merit_score": 62.0, "sin_score": 18.0, "merit_level": "丙等",
     "life_summary": "一生务农，孝养父母，曾捐资修桥一座；晚年因田界争执伤及邻人，"
                     "盗伐村中林木变卖。善恶相抵，功过参半。"},
    {"name": "钱氏", "gender": "女", "birth_year": 1978, "death_date": "2026-09-20",
     "cause_of_death": "意外", "hometown": "酆都·丰都县", "jurisdiction_id": 2,
     "merit_score": 12.0, "sin_score": 96.0, "merit_level": "戊等",
     "life_summary": "早年济贫施粥数次；中年起侵占赈灾粮款，致灾民饿毙三人，"
                     "并伪造账册欺瞒上官。罪业滔天。"},
    {"name": "孙文才", "gender": "男", "birth_year": 1990, "death_date": "2026-10-02",
     "cause_of_death": "斗殴致死", "hometown": "城隍·苏州府", "jurisdiction_id": 3,
     "merit_score": 78.0, "sin_score": 42.0, "merit_level": "乙等",
     "life_summary": "教书育人十余载，教化乡里，赈灾施粥；因醉酒斗殴，误伤致死一人，"
                     "事后自首并厚葬之。"},
]


def seed(force: bool = False) -> dict:
    """幂等初始化；force=True 时重建演示数据。"""
    stats = {"users": 0, "knowledge": 0, "cases": 0}
    if db.count("role") == 0:
        for code, name in security.ROLE_NAMES.items():
            db.insert("role", {
                "code": code, "name": name,
                "description": f"{name}角色",
                "permissions": db.jdumps(security.ROLE_PERMISSIONS[code]),
                "created_at": db.now(),
            })

    if db.count("jurisdiction") == 0:
        for i, (name, desc) in enumerate(
            [("东岳", "东岳大帝所辖"), ("酆都", "酆都鬼城所辖"),
             ("城隍", "阳世城隍所辖"), ("十殿", "十殿阎罗所辖")], start=1
        ):
            db.insert("jurisdiction", {
                "code": f"J{i}", "name": name, "level": "州", "parent_id": 0,
                "description": desc, "created_at": db.now(),
            })

    if db.count("user") == 0:
        # 🔴 口令不再写死在源码里：正常模式由 security.initial_password() 生成随机强口令
        #    （汇总写到 .run/credentials.txt），只有 DIFU_DEMO_MODE=1 才回落历史弱口令。
        users = [
            ("admin", "系统管理员", "admin", 0),
            ("yanwang", "阎罗天子", "king", 0),
            ("panguan", "崔判官", "judge", 1),
            ("yinchai", "牛头阴差", "clerk", 1),
            ("lunhui", "轮回司主", "reincarnation", 0),
            ("chenghuang", "苏州城隍", "chenghuang", 3),
        ]
        for username, real_name, role, juri in users:
            pwd = security.initial_password(username, real_name)
            db.insert("user", {
                "username": username, "password_hash": security.hash_password(pwd),
                "real_name": real_name, "role_code": role, "jurisdiction_id": juri,
                "avatar": "", "status": "active",
                "created_at": db.now(), "updated_at": db.now(),
            })
            stats["users"] += 1

    if db.count("sys_dict") == 0:
        for dtype, items in DICT_SEED.items():
            for i, (k, v) in enumerate(items, start=1):
                db.insert("sys_dict", {
                    "dict_type": dtype, "dict_key": k, "dict_value": v,
                    "sort": i, "remark": "", "created_at": db.now(),
                })

    if db.count("model_config") == 0:
        seeds = [
            ("默认文本模型", "text", "DeepSeek", config.LLM_BASE_URL, config.LLM_MODEL,
             config.LLM_API_KEY, 1),
            ("视觉理解模型", "vision", "通义千问", config.VISION_BASE_URL or "https://dashscope.aliyuncs.com/compatible-mode/v1",
             config.VISION_MODEL, config.VISION_API_KEY, 0),
            ("语音转写模型", "asr", "本地 Whisper", config.ASR_BASE_URL, "whisper-1",
             config.ASR_API_KEY, 0),
            ("向量模型", "embedding", "OpenAI 兼容", config.EMBEDDING_BASE_URL, config.EMBEDDING_MODEL,
             config.EMBEDDING_API_KEY, 0),
        ]
        for name, purpose, provider, base_url, model, key, active in seeds:
            # 🔴 api_key 一律留空：Key 只放 .run/secrets.env（环境变量），
            #    llm.active_config() 会自动从环境变量回落，功能完全一样，
            #    但数据库文件外传/提交 git 时不再等于把 Key 送出去。
            db.insert("model_config", {
                "name": name, "purpose": purpose, "provider": provider, "base_url": base_url,
                "model": model, "api_key": "", "temperature": 0.3, "max_tokens": 2048,
                "is_active": active, "status": "ready" if key else "unconfigured",
                "last_test": "", "created_at": db.now(), "updated_at": db.now(),
            })

    if db.count("knowledge_doc") == 0:
        for doc in KNOWLEDGE_SEED:
            rag.ingest_text(doc["title"], doc["category"], doc["content"], source="seed")
            stats["knowledge"] += 1

    if db.count("deceased") == 0:
        for d in DEMO_DECEASED:
            db.insert("deceased", {
                **d, "status": "待审", "created_at": db.now(), "updated_at": db.now(),
            })

    return stats
