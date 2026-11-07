"""多模态证据解析：图片 OCR/视觉理解、音频 ASR、PDF/TXT 解析。

有 Key → 调视觉 / 语音模型；无 Key → 离线模拟解析（仍产出可入库的文本与摘要），
保证「零配置也能演示完整证据闭环」。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import urllib.request
import zlib
from pathlib import Path

from .. import config
from . import llm

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
TEXT_EXT = {".txt", ".md", ".log", ".csv"}


def detect_type(filename: str, mime: str = "") -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT or (mime or "").startswith("image/"):
        return "image"
    if ext in AUDIO_EXT or (mime or "").startswith("audio/"):
        return "audio"
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in TEXT_EXT or (mime or "").startswith("text/"):
        return "text"
    return "other"


# ------------------------------------------------------------------ 图片
def ocr_image(path: Path, extra_hint: str = "") -> dict:
    cfg = llm.active_config("vision")
    if cfg:
        try:
            b64 = base64.b64encode(path.read_bytes()).decode()
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            prompt = ("你是地府阴司的证据官。请识别这张罪证图片：先完整转写图中所有文字（OCR），"
                      "再用一句话概括图片内容（人物、场景、可疑之处）。"
                      "严格输出 JSON：{\"text\": \"...\", \"summary\": \"...\"}")
            result = llm.chat([{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}], purpose="vision")
            if result and result.text:
                data = _extract_json(result.text)
                return {"ocr_text": data.get("text") or result.text,
                        "summary": data.get("summary") or "视觉模型已解析",
                        "mode": "vision-model", "model": result.model}
        except Exception:
            pass
    return _offline_image(path, extra_hint)


def _offline_image(path: Path, extra_hint: str) -> dict:
    size_kb = max(1, path.stat().st_size // 1024)
    name = path.stem
    guessed = re.sub(r"[_\-\d]+", " ", name).strip() or "未命名影像"
    text = (f"【离线 OCR 模拟】影像文件 {path.name}（{size_kb}KB）。"
            f"画面主体：{guessed}。图中可辨文字：收据、借据、账册、往来款项、"
            f"日期、署名等要素。{('补充说明：' + extra_hint) if extra_hint else ''}")
    summary = f"疑似「{guessed}」相关书证影像，含文字要素，可作判决参考。"
    return {"ocr_text": text, "summary": summary, "mode": "offline-simulate", "model": "本地模拟"}


# ------------------------------------------------------------------ 音频
def transcribe_audio(path: Path) -> dict:
    cfg = llm.active_config("asr")
    if cfg:
        try:
            b64 = base64.b64encode(path.read_bytes()).decode()
            body = json.dumps({
                "model": cfg.get("model") or "whisper-1",
                "file": b64, "filename": path.name,
                "language": "zh", "response_format": "json",
            }).encode()
            req = urllib.request.Request(
                f"{(cfg.get('base_url') or '').rstrip('/')}/audio/transcriptions",
                data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {cfg['api_key']}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {"ocr_text": data.get("text", ""), "summary": "语音已转写",
                    "mode": "asr-model", "model": cfg.get("model")}
        except Exception:
            pass
    dur = max(1, path.stat().st_size // 16000)
    text = (f"【离线 ASR 模拟】音频 {path.name} 时长约 {dur} 秒。转写内容："
            "「……那日夜里，我确实去了城西仓库，账上的数目是后来改的，"
            "我只是想给家里留些口粮，没想到会出人命……」"
            "（说话人：当事人；语气：迟疑；可信度：中）")
    return {"ocr_text": text, "summary": "当事人自述录音，含认罪与辩解混合陈述。",
            "mode": "offline-simulate", "model": "本地模拟"}


# ------------------------------------------------------------------ 文档
def parse_document(path: Path) -> dict:
    ext = path.suffix.lower()
    if ext == ".pdf":
        text = extract_pdf_text(path)
        if not text.strip():
            text = (f"【离线 PDF 解析模拟】{path.name}：含卷宗正文、署名与日期，"
                    "疑似书面证词或账册影印件。")
            mode = "offline-simulate"
        else:
            mode = "local-pdf-parse"
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            mode = "local-text-parse"
        except OSError:
            text = f"【离线解析模拟】{path.name} 无法按文本读取，已按二进制归档。"
            mode = "offline-simulate"
    summary = (text.strip().replace("\n", " ")[:120] + "…") if text.strip() else "空文档"
    return {"ocr_text": text[:8000], "summary": summary, "mode": mode, "model": "本地解析"}


def extract_pdf_text(path: Path) -> str:
    """极简 PDF 文本抽取（解压 FlateDecode 流 + 提取 Tj/TJ 字符串），不依赖三方库。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    out: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except zlib.error:
            continue
        for tm in re.finditer(rb"\((.*?)\)\s*Tj|\[(.*?)\]\s*TJ", data, re.S):
            chunk = tm.group(1) or tm.group(2) or b""
            for sm in re.finditer(rb"\((?:\\.|[^\\)])*\)", chunk):
                s = sm.group(0)[1:-1]
                s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
                out.append(s.decode("utf-8", "ignore"))
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ------------------------------------------------------------------ 统一入口
def parse_file(path: Path, hint: str = "") -> dict:
    kind = detect_type(path.name, mimetypes.guess_type(path.name)[0] or "")
    if kind == "image":
        res = ocr_image(path, hint)
    elif kind == "audio":
        res = transcribe_audio(path)
    elif kind in ("pdf", "text"):
        res = parse_document(path)
    else:
        res = {"ocr_text": f"【未知类型】{path.name} 已归档，需人工核验。",
               "summary": "未知类型证据", "mode": "manual", "model": "人工"}
    res["evi_type"] = kind
    return res


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except ValueError:
        return {}
