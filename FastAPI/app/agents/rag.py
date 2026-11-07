"""多模态知识库：切分 → 向量化 → 检索。

三级检索策略（自动降级）：
1. PGVector（配了 DSN 且装了 psycopg2）—— 生产用；
2. 本地向量库 `data/vector_store.json`（有 Embedding Key 时写入真实向量，余弦相似度）；
3. 关键词 / 二元组打分（零 Key 也能检索，保证 demo 可用）。
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from .. import config, db
from . import llm

_CHUNK_SIZE = 220
_OVERLAP = 40


# ------------------------------------------------------------------ 切分
def split_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        # 尽量在句号处断开
        if end < len(text):
            for punct in ("。", "；", "！", "？", ".", ";", " "):
                pos = text.rfind(punct, start + size // 2, end)
                if pos != -1:
                    end = pos + 1
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def keywords_of(text: str, top: int = 12) -> list[str]:
    """轻量中文关键词抽取：二元组 + 高频单字，够用且零依赖。"""
    text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    freq: dict[str, int] = {}
    for i in range(len(text) - 1):
        bg = text[i:i + 2]
        freq[bg] = freq.get(bg, 0) + 1
    stop = {"的", "了", "是", "在", "有", "和", "与", "者", "其", "之", "而", "为", "以", "于", "也", "不"}
    items = [(k, v) for k, v in freq.items() if not any(ch in stop for ch in k)]
    items.sort(key=lambda x: (-x[1], -len(x[0])))
    return [k for k, _ in items[:top]]


# ------------------------------------------------------------------ 入库
def ingest_text(title: str, category: str, content: str, source: str = "upload") -> dict:
    doc_id = db.insert("knowledge_doc", {
        "title": title, "category": category, "source": source, "content": content,
        "chunk_count": 0, "status": "indexed", "created_at": db.now(),
    })
    chunks = split_text(content)
    vectors = llm.embed(chunks) if chunks else None
    for idx, chunk in enumerate(chunks):
        db.insert("knowledge_chunk", {
            "doc_id": doc_id, "idx": idx, "content": chunk,
            "keywords": db.jdumps(keywords_of(chunk)),
            "embedding": db.jdumps(vectors[idx]) if vectors and idx < len(vectors) else "",
            "created_at": db.now(),
        })
    db.update("knowledge_doc", {"chunk_count": len(chunks)}, "id=?", (doc_id,))
    _sync_vector_store()
    return {"doc_id": doc_id, "chunks": len(chunks), "vectorized": bool(vectors)}


def _sync_vector_store() -> None:
    """把有向量的切片落盘成 vector_store.json（PG 不可用时的持久化替身）。"""
    rows = db.query("SELECT id, doc_id, content, embedding FROM knowledge_chunk WHERE embedding != ''")
    store = []
    for r in rows:
        vec = db.jloads(r["embedding"], [])
        if vec:
            store.append({"id": r["id"], "doc_id": r["doc_id"], "text": r["content"], "vec": vec})
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.VECTOR_JSON.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")


def load_vector_store() -> list[dict]:
    if not config.VECTOR_JSON.exists():
        return []
    try:
        return json.loads(config.VECTOR_JSON.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


# ------------------------------------------------------------------ 检索
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a[:n]))
    nb = math.sqrt(sum(x * x for x in b[:n]))
    return dot / (na * nb) if na and nb else 0.0


# 法典术语同义扩展：让口语化的案情描述也能命中法典条文
_SYNONYMS = {
    "盗伐": ["盗窃", "盗", "林木"], "偷": ["盗窃"], "窃": ["盗窃"],
    "侵吞": ["贪污", "公帑"], "赈灾": ["贪污", "粮款"], "贪": ["贪污"],
    "误伤致死": ["杀人", "致死"], "打死": ["杀人"], "毙命": ["杀人"],
    "斗殴": ["伤人", "斗殴"], "打伤": ["伤人"], "骨裂": ["伤人"],
    "不孝": ["忤逆"], "忤逆": ["忤逆"], "辱骂父母": ["忤逆"],
    "骗": ["欺诈"], "作假": ["欺诈"], "伪造": ["欺诈", "伪造"],
    "纵火": ["纵火"], "放火": ["纵火"], "逃税": ["逃税"], "漏税": ["逃税"],
    "量刑": ["量刑", "刑期"], "功德": ["功德", "善恶"], "减刑": ["减罪", "自首"],
}


def _expand(query: str) -> list[str]:
    """查询词 + 同义扩展，返回候选词列表。"""
    q = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", query or "")
    terms = [q] if q else []
    for k, vs in _SYNONYMS.items():
        if k in q:
            terms += [v for v in vs if v not in terms]
    return terms


def _bigrams(text: str) -> set[str]:
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _keyword_score(query: str, content: str, kws: list[str], title: str = "",
                   idf: dict[str, float] | None = None) -> float:
    """关键词打分：完整查询命中 > 同义词命中 > 二元组（按 IDF 加权）。"""
    idf = idf or {}
    score = 0.0
    for term in _expand(query):
        if not term:
            continue
        if term in title:
            score += 6.0 * (1 + min(len(term), 6) / 6)
        if term in content:
            score += 3.0 * (1 + min(len(term), 6) / 6)
    qbg = _bigrams(query)
    cbg = _bigrams(content)
    for bg in qbg & cbg:
        score += 0.4 + 0.9 * idf.get(bg, 0.0)
    for kw in kws:
        if kw and kw in query:
            score += 1.2
    return score


def _build_idf(chunks: list[dict], docs: dict) -> dict[str, float]:
    """按切片统计二元组 IDF：越罕见越有区分度。"""
    df: dict[str, int] = {}
    for c in chunks:
        for bg in _bigrams(c["content"] + " " + docs.get(c["doc_id"], {}).get("title", "")):
            df[bg] = df.get(bg, 0) + 1
    n = max(1, len(chunks))
    return {bg: math.log((n + 1) / (cnt + 0.5)) for bg, cnt in df.items()}


def search(query: str, top_k: int = 4, category: str | None = None) -> list[dict]:
    """统一检索入口：向量优先，失败自动降级为关键词匹配（带 IDF 加权）。"""
    docs = {d["id"]: d for d in db.query("SELECT * FROM knowledge_doc")}
    chunks = db.query("SELECT * FROM knowledge_chunk")
    if category:
        chunks = [c for c in chunks if docs.get(c["doc_id"], {}).get("category") == category]
    if not chunks:
        return []
    idf = _build_idf(chunks, docs)

    qvec = None
    if any(c.get("embedding") for c in chunks):
        vecs = llm.embed([query])
        if vecs:
            qvec = vecs[0]

    scored: list[tuple[float, dict]] = []
    for c in chunks:
        title = docs.get(c["doc_id"], {}).get("title", "")
        score = _keyword_score(query, c["content"], db.jloads(c.get("keywords"), []), title, idf)
        if qvec and c.get("embedding"):
            score += _cosine(qvec, db.jloads(c["embedding"], [])) * 10
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])

    results = []
    for score, c in scored[:top_k]:
        doc = docs.get(c["doc_id"], {})
        results.append({
            "chunk_id": c["id"], "doc_id": c["doc_id"], "title": doc.get("title", ""),
            "category": doc.get("category", ""), "content": c["content"],
            "score": round(score, 2),
            "mode": "vector+keyword" if qvec else "keyword",
        })
    return results


def stats() -> dict:
    return {
        "docs": db.count("knowledge_doc"),
        "chunks": db.count("knowledge_chunk"),
        "vectorized": db.count("knowledge_chunk", "embedding != ''"),
        "mode": "pgvector" if config.PGVECTOR_DSN else (
            "local-vector" if db.count("knowledge_chunk", "embedding != ''") else "keyword-fallback"),
        "store_file": str(config.VECTOR_JSON),
    }


def rebuild() -> dict:
    """重建全部切片与向量（切换 Embedding 模型后使用）。"""
    docs = db.query("SELECT * FROM knowledge_doc")
    db.execute("DELETE FROM knowledge_chunk")
    total = 0
    for d in docs:
        chunks = split_text(d["content"])
        vecs = llm.embed(chunks) if chunks else None
        for idx, chunk in enumerate(chunks):
            db.insert("knowledge_chunk", {
                "doc_id": d["id"], "idx": idx, "content": chunk,
                "keywords": db.jdumps(keywords_of(chunk)),
                "embedding": db.jdumps(vecs[idx]) if vecs and idx < len(vecs) else "",
                "created_at": db.now(),
            })
        db.update("knowledge_doc", {"chunk_count": len(chunks)}, "id=?", (d["id"],))
        total += len(chunks)
    _sync_vector_store()
    return {"docs": len(docs), "chunks": total, "vectorized": bool(vecs) if docs else False}
