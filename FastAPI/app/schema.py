"""21 张业务表的 DDL。

方言处理：SQLite 与 MySQL 共用同一份 SQL，`db.init()` 会把
`INTEGER PRIMARY KEY AUTOINCREMENT` 替换成 MySQL 的 `INT AUTO_INCREMENT PRIMARY KEY`。
"""
from __future__ import annotations

SCHEMA_SQL: list[str] = [
    # 1. 角色
    """CREATE TABLE IF NOT EXISTS role (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE, name TEXT, description TEXT, permissions TEXT,
        created_at TEXT
    )""",
    # 2. 辖区
    """CREATE TABLE IF NOT EXISTS jurisdiction (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE, name TEXT, level TEXT, parent_id INTEGER,
        description TEXT, created_at TEXT
    )""",
    # 3. 官吏（用户）
    """CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, password_hash TEXT, real_name TEXT,
        role_code TEXT, jurisdiction_id INTEGER, avatar TEXT, status TEXT,
        created_at TEXT, updated_at TEXT
    )""",
    # 4. 亡魂档案
    """CREATE TABLE IF NOT EXISTS deceased (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, gender TEXT, birth_year INTEGER, death_date TEXT,
        cause_of_death TEXT, hometown TEXT, jurisdiction_id INTEGER,
        merit_score REAL, sin_score REAL, life_summary TEXT,
        merit_level TEXT, status TEXT, created_at TEXT, updated_at TEXT
    )""",
    # 5. 案件卷宗
    """CREATE TABLE IF NOT EXISTS case_file (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_no TEXT UNIQUE, title TEXT, deceased_id INTEGER, jurisdiction_id INTEGER,
        status TEXT, priority TEXT, risk_level TEXT, current_node TEXT, progress INTEGER,
        judge_id INTEGER, clerk_id INTEGER, flow_state TEXT, summary TEXT, verdict TEXT,
        source TEXT, created_at TEXT, updated_at TEXT, closed_at TEXT
    )""",
    # 6. 卷宗流转批注
    """CREATE TABLE IF NOT EXISTS case_note (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, node TEXT, actor_type TEXT, actor_name TEXT,
        content TEXT, created_at TEXT
    )""",
    # 7. 证据（多模态）
    """CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, deceased_id INTEGER, evi_type TEXT, file_name TEXT,
        file_path TEXT, mime TEXT, size INTEGER, ocr_text TEXT, summary TEXT,
        chunk_id INTEGER, status TEXT, created_at TEXT
    )""",
    # 8. 判决书
    """CREATE TABLE IF NOT EXISTS judgment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, crime TEXT, law_article TEXT, merit_score REAL, sin_score REAL,
        sentence_type TEXT, sentence_years REAL, penalty_desc TEXT, risk_level TEXT,
        sentence_days INTEGER DEFAULT 0,    -- 原始天数：years 是给人看的，落库别丢精度
        reason TEXT, evidence_refs TEXT, judge_agent TEXT, approved_by TEXT, approved_at TEXT,
        created_at TEXT
    )""",
    # 9. 服刑记录
    """CREATE TABLE IF NOT EXISTS execution (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, judgment_id INTEGER, prison TEXT, sentence_days INTEGER,
        start_date TEXT, end_date TEXT, remaining_days INTEGER, status TEXT,
        punishment_desc TEXT, created_at TEXT, updated_at TEXT
    )""",
    # 10. 轮回转世
    """CREATE TABLE IF NOT EXISTS reincarnation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, merit_score REAL, realm TEXT, realm_reason TEXT,
        next_life TEXT, document TEXT, status TEXT, mengpo_registered INTEGER,
        created_at TEXT
    )""",
    # 11. 待办审批
    """CREATE TABLE IF NOT EXISTS approval (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, approval_no TEXT, appr_type TEXT, title TEXT, reason TEXT,
        status TEXT, applicant TEXT, approver_id INTEGER, approver_name TEXT,
        opinion TEXT, created_at TEXT, decided_at TEXT
    )""",
    # 12. Agent 推理日志
    """CREATE TABLE IF NOT EXISTS agent_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, agent_code TEXT, agent_name TEXT, node TEXT, action TEXT,
        thought TEXT, tool_name TEXT, tool_input TEXT, tool_output TEXT,
        model TEXT, latency_ms INTEGER, tokens INTEGER, created_at TEXT
    )""",
    # 13. 人工操作审计日志
    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, user_name TEXT, action TEXT, target_type TEXT, target_id INTEGER,
        detail TEXT, ip TEXT, source TEXT, created_at TEXT
    )""",
    # 14. 消息通知
    """CREATE TABLE IF NOT EXISTS notification (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, title TEXT, content TEXT, ntype TEXT, is_read INTEGER,
        link TEXT, created_at TEXT
    )""",
    # 15. 知识库文档
    """CREATE TABLE IF NOT EXISTS knowledge_doc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, category TEXT, source TEXT, content TEXT, chunk_count INTEGER,
        status TEXT, created_at TEXT
    )""",
    # 16. 知识库切片（向量）
    """CREATE TABLE IF NOT EXISTS knowledge_chunk (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER, idx INTEGER, content TEXT, keywords TEXT, embedding TEXT,
        created_at TEXT
    )""",
    # 17. 对话消息
    """CREATE TABLE IF NOT EXISTS chat_message (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT, role TEXT, content TEXT, case_id INTEGER,
        tool_calls TEXT, created_at TEXT
    )""",
    # 18. 模型配置
    """CREATE TABLE IF NOT EXISTS model_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, purpose TEXT, provider TEXT, base_url TEXT, model TEXT, api_key TEXT,
        temperature REAL, max_tokens INTEGER, is_active INTEGER, status TEXT,
        last_test TEXT, created_at TEXT, updated_at TEXT
    )""",
    # 19. 系统字典
    """CREATE TABLE IF NOT EXISTS sys_dict (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dict_type TEXT, dict_key TEXT, dict_value TEXT, sort INTEGER, remark TEXT,
        created_at TEXT
    )""",
    # 20. 工具调用留痕
    """CREATE TABLE IF NOT EXISTS tool_call (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER, agent_code TEXT, tool_name TEXT, args TEXT, result TEXT,
        success INTEGER, duration_ms INTEGER, created_at TEXT
    )""",
    # 21. 流程检查点（MemorySaver 的持久化替身）
    """CREATE TABLE IF NOT EXISTS checkpoint (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT, case_id INTEGER, node TEXT, state TEXT, status TEXT,
        created_at TEXT, updated_at TEXT
    )""",
]

INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_case_status ON case_file(status)",
    "CREATE INDEX IF NOT EXISTS idx_case_deceased ON case_file(deceased_id)",
    "CREATE INDEX IF NOT EXISTS idx_agentlog_case ON agent_log(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_chunk_doc ON knowledge_chunk(doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_notify_user ON notification(user_id)",
]
