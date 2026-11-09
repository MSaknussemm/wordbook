#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wordbook.py —— 本地生词本

左侧输入英文单词 -> 右侧显示「词性 + 意思」-> 点「记下」追加到桌面上的 词表.txt。

只用 Python 3 标准库。日常由 启动词本.vbs 无窗口拉起：
    - 真实端口写进 %LOCALAPPDATA%\\Wordbook\\port.txt，启动器读它打开页面
    - 页面关掉 15 秒（或心跳停 90 秒 / 启动后 60 秒没人连）就自己退出
手动排错时用： python wordbook.py   （会有黑窗口，能看到报错）
"""

from __future__ import annotations

__version__ = "1.4.2"

import argparse
import csv
import errno
import io
import itertools
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _safe_streams() -> None:
    """让 stdout/stderr 遇到编不了的字符时替换，而不是抛异常。

    英文版 Windows 的控制台是 cp1252，直接 print 中文会 UnicodeEncodeError；
    pythonw 下这两个流是 None，也要跳过。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


_safe_streams()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")

WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]{0,47}$")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

NET_TIMEOUT = 10      # 每个词典最多等 10 秒；两个源最坏 20 秒，前端给 30 秒
CACHE_TTL = 900  # 秒；同一个词 15 分钟内不重复打接口

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}

# ---- 「页面还在不在了」判据（双保险）--------------------------------------
# 快路径：SSE 连接数为 0 且心跳停了 FAST_EXIT_SEC 秒 -> 关标签，立刻走
# 慢路径：不管有没有连接，心跳彻底停 SLOW_EXIT_SEC 秒 -> 兜住半开连接的僵尸
_SERVER = None
_INSTANCE_HANDLE = None   # 单实例互斥体句柄，进程活着就一直持有
_SSE_CLIENTS = 0
_SSE_LOCK = threading.Lock()
_LAST_HEARTBEAT = 0.0
FAST_EXIT_SEC = 15
SLOW_EXIT_SEC = 90


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def clean(text) -> str:
    """去 HTML 标签、还原实体、压空白。"""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return WS_RE.sub(" ", text).strip()


def clip(text: str, limit: int) -> str:
    """截断长释义，免得一行糊成一片。"""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def fetch_json(url: str, timeout: int = NET_TIMEOUT):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def normalize_word(word: str) -> str:
    word = (word or "").strip()
    return WS_RE.sub(" ", word).lower()


# --------------------------------------------------------------------------
# 词典源 1：有道公开 JSON（中文释义 + 柯林斯例句 + 发音）
# --------------------------------------------------------------------------

def lookup_youdao(word: str):
    query = urllib.parse.quote(word)
    url = f"https://dict.youdao.com/jsonapi_s?doctype=json&jsonversion=4&q={query}"
    data = fetch_json(url)

    ec = data.get("ec") or {}
    ec_word = ec.get("word") or {}
    trs = as_list(ec_word.get("trs"))

    simple = (data.get("simple") or {}).get("word") or []
    simple_word = simple[0] if simple else {}

    uk = clean(ec_word.get("ukphone") or simple_word.get("ukphone"))
    us = clean(ec_word.get("usphone") or simple_word.get("usphone"))

    entries = []
    for item in trs:
        if not isinstance(item, dict):
            continue
        zh = clean(item.get("tran"))
        if zh:
            entries.append({"pos": clean(item.get("pos")), "zh": zh, "en": ""})

    # ee = WordNet 英文释义
    en_entries = []
    ee_word = (data.get("ee") or {}).get("word") or {}
    for grp in as_list(ee_word.get("trs")):
        if not isinstance(grp, dict):
            continue
        pos = clean(grp.get("pos"))
        for tr in as_list(grp.get("tr")):
            if not isinstance(tr, dict):
                continue
            en = clip(clean(tr.get("tran")), 220)
            if en:
                en_entries.append({"pos": pos, "zh": "", "en": en})

    # 英文义按词性挂到中文义后面
    en_by_pos = defaultdict(list)
    for item in en_entries:
        en_by_pos[item["pos"]].append(item["en"])
    for item in entries:
        bucket = en_by_pos.get(item["pos"])
        if bucket:
            item["en"] = bucket.pop(0)
    # 中文义已经覆盖的场合，英文义只补两条；没有中文义时（Wiktionary 兜底）多给几条
    extra_cap = 2 if entries else 5
    extra = 0
    for pos, leftovers in en_by_pos.items():
        for en in leftovers:
            if extra >= extra_cap:
                break
            entries.append({"pos": pos, "zh": "", "en": en})
            extra += 1

    # 例句：优先带中文翻译的（blng_sents_part），再退回 expand_ec
    examples = []
    pairs = (data.get("blng_sents_part") or {}).get("sentence-pair")
    if isinstance(pairs, dict):
        pairs = [pairs]
    for pair in as_list(pairs):
        if not isinstance(pair, dict):
            continue
        en = clean(pair.get("sentence"))
        zh = clean(pair.get("sentence-translation"))
        if en:
            examples.append({"en": en, "zh": zh})
        if len(examples) >= 3:
            break

    if not examples:
        for wob in as_list((data.get("expand_ec") or {}).get("word")):
            if not isinstance(wob, dict):
                continue
            for tl in as_list(wob.get("transList")):
                content = (tl or {}).get("content") or {}
                for sent in as_list(content.get("sents")):
                    if not isinstance(sent, dict):
                        continue
                    en = clean(sent.get("sentOrig"))
                    zh = clean(sent.get("sentTrans"))
                    if en:
                        examples.append({"en": en, "zh": zh})
                    if len(examples) >= 3:
                        break
                if len(examples) >= 3:
                    break

    # 变形
    forms = []
    for rel in as_list((data.get("rel_word") or {}).get("rels")):
        rel_body = (rel or {}).get("rel") or {}
        pos = clean(rel_body.get("pos"))
        for item in as_list(rel_body.get("words")):
            if not isinstance(item, dict):
                continue
            w = clean(item.get("word"))
            t = clean(item.get("tran"))
            if w:
                forms.append(f"{pos} {w}" + (f"（{t}）" if t else ""))
    for wob in as_list((data.get("expand_ec") or {}).get("word")):
        for wf in as_list((wob or {}).get("wfs")):
            if isinstance(wf, dict) and wf.get("name") and wf.get("value"):
                forms.append(f"{clean(wf['name'])} {clean(wf['value'])}")

    # 考试标签
    exam = []
    for tag in as_list(ec.get("exam_type")):
        t = clean(tag)
        if t and t not in exam:
            exam.append(t)
    for wob in as_list((data.get("expand_ec") or {}).get("word")):
        for tl in as_list((wob or {}).get("transList")):
            for et in as_list(((tl or {}).get("content") or {}).get("examType")):
                if isinstance(et, dict):
                    t = clean(et.get("en") or et.get("zh"))
                    if t and t not in exam:
                        exam.append(t)

    # 网络释义（备胎，主释义缺失时才用）
    web = []
    for item in as_list(ec.get("web_trans")):
        if isinstance(item, str):
            t = clean(item)
        elif isinstance(item, dict):
            t = clean(item.get("value") or "")
        else:
            t = ""
        if t and t not in web:
            web.append(t)

    if not (entries or examples or uk or us):
        return None

    return {
        "word": clean(ec_word.get("return-phrase") or simple_word.get("return-phrase") or word),
        "uk": uk,
        "us": us,
        "phonetic": "",
        "entries": entries,
        "examples": examples,
        "forms": forms,
        "exam": exam,
        "web": web[:4],
        "source": "youdao",
    }


# --------------------------------------------------------------------------
# 词典源 2：dictionaryapi.dev（Wiktionary，纯英文）—— 有道挂了时候的备胎
# --------------------------------------------------------------------------

def lookup_wiktionary(word: str):
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
    data = fetch_json(url)
    if not isinstance(data, list) or not data:
        return None

    head = data[0] if isinstance(data[0], dict) else {}
    phonetic = clean(head.get("phonetic"))

    entries = []
    examples = []
    for block in data:
        if not isinstance(block, dict):
            continue
        for meaning in as_list(block.get("meanings")):
            pos = clean((meaning or {}).get("partOfSpeech"))
            for d in as_list((meaning or {}).get("definitions"))[:4]:
                if not isinstance(d, dict):
                    continue
                en = clean(d.get("definition"))
                if en:
                    entries.append({"pos": pos, "zh": "", "en": en})
                ex = clean(d.get("example"))
                if ex and len(examples) < 3:
                    examples.append({"en": ex, "zh": ""})

    if not entries:
        return None

    return {
        "word": clean(head.get("word") or word),
        "uk": "",
        "us": "",
        "phonetic": phonetic,
        "entries": entries,
        "examples": examples,
        "forms": [],
        "exam": [],
        "web": [],
        "source": "wiktionary",
    }


# --------------------------------------------------------------------------
# 合并 / 缓存
# --------------------------------------------------------------------------

def acquire_single_instance(name: str = "Local\\WordbookSingleton") -> bool:
    """Windows 命名互斥体：同一时间只允许一个实例。

    只对「没指定 --port」的常规启动生效——显式给端口是调试/测试行为，放行。
    返回 False 表示已经有一个在跑了。
    """
    global _INSTANCE_HANDLE
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True                       # 建不出来就放行，别把程序拦死
        if ctypes.get_last_error() == 183:    # ERROR_ALREADY_EXISTS
            return False
        _INSTANCE_HANDLE = handle             # 持住引用，进程退出自动释放
        return True
    except Exception:
        return True


class LookupNetworkError(LookupError):
    """两个词典都是网络层失败——这跟「词典里没有这个词」是两回事。"""


def lookup(word: str) -> dict:
    word = normalize_word(word)
    now = time.time()
    hit = _cache.get(word)
    if hit and now - hit[0] < CACHE_TTL:
        return dict(hit[1])

    errors = []
    payload = None
    net_failed = False

    try:
        payload = lookup_youdao(word)
    except urllib.error.HTTPError as exc:
        errors.append(f"有道 HTTP {exc.code}")
        if exc.code >= 500 or exc.code == 429:
            net_failed = True          # 服务故障/限流，不是「没收录」
    except (urllib.error.URLError, socket.timeout, TimeoutError):
        errors.append("有道连接失败")
        net_failed = True
    except Exception as exc:  # 解析崩了也要能降级
        errors.append(f"有道异常 {type(exc).__name__}")

    if payload is None:
        try:
            payload = lookup_wiktionary(word)
        except urllib.error.HTTPError as exc:
            errors.append(f"wiktionary HTTP {exc.code}")
            if exc.code >= 500 or exc.code == 429:
                net_failed = True
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            errors.append("wiktionary 连接失败")
            net_failed = True
        except Exception as exc:
            errors.append(f"wiktionary 异常 {type(exc).__name__}")

    if payload is None:
        detail = "、".join(errors) if errors else "两个词典都没收录"
        if net_failed:
            raise LookupNetworkError(detail)   # 网络问题，别冤枉这个词
        raise LookupError(detail)

    payload["word"] = payload.get("word") or word
    payload.pop("saved", None)      # 不缓存：用户随时可能手改词表，缓存会让状态过期

    _cache[word] = (now, payload)
    return dict(payload)


# --------------------------------------------------------------------------
# Markdown 词表
# --------------------------------------------------------------------------

def _split_meaning(payload: dict) -> tuple[str, str, str]:
    """拆成 (词性, 中文, 英文) 三段拼好的文本。"""
    pos_list, zh_list, en_list = [], [], []
    for item in payload.get("entries") or []:
        pos = (item.get("pos") or "").strip()
        zh = (item.get("zh") or "").strip()
        en = (item.get("en") or "").strip()
        if pos:
            pos_list.append(pos)
        if zh:
            zh_list.append(zh)
        if en:
            en_list.append(en)
    return (
        " ".join(dict.fromkeys(pos_list)),
        "；".join(dict.fromkeys(zh_list)),
        " ".join(dict.fromkeys(en_list)),
    )


def _meaning_line(payload: dict, show_pos: bool = True) -> str:
    """按词性分组拼「词性 + 意思」，多词性才不会被拼错。"""
    grouped: dict[str, list] = defaultdict(list)
    for item in payload.get("entries") or []:
        text = (item.get("zh") or "").strip()
        if text:
            grouped[(item.get("pos") or "").strip()].append(text)
    if not grouped:                     # 只有英文释义的降级情况
        for item in payload.get("entries") or []:
            text = (item.get("en") or "").strip()
            if text:
                grouped[(item.get("pos") or "").strip()].append(text)

    parts = []
    for pos, texts in grouped.items():
        uniq = [t for t in dict.fromkeys(texts) if t]
        if not uniq:
            continue
        head = (pos + " ") if (pos and show_pos) else ""
        parts.append(head + "；".join(uniq))
    return "；".join(parts)


def _examples_text(payload: dict, limit: int = 3) -> str:
    out = []
    for ex in (payload.get("examples") or [])[:limit]:
        if not ex.get("en"):
            continue
        out.append("- " + ex["en"])
        if ex.get("zh"):
            out.append("  - " + ex["zh"])
    return "\n".join(out)


def _sanitize_block(block: str) -> str:
    """词条内部不能有空行——空行是词条之间的分隔符。

    自定义模板里写 `{{word}}\n\n{{zh}}` 这种，会把一条拆成两条，
    导致数量、导出、判重全乱。这里把内部空行压掉。
    """
    lines = [line.rstrip() for line in block.strip().splitlines()]
    return "\n".join([line for line in lines if line.strip()])


def format_entry(payload: dict, cfg: dict | None = None, lang: str = "zh") -> str:
    """生成一条纯文本词条：第一行单词，第二行「词性 意思」，后面按需追加。

    不写任何 Markdown 标记——记事本打开就是干净的笔记。
    """
    cfg = cfg or load_config()
    fields = cfg.get("fields") or {}
    template = (cfg.get("template") or "").strip()
    lab = LABELS.get(lang, LABELS["zh"])

    word = payload.get("word") or ""
    pos, zh, en = _split_meaning(payload)

    phones = []
    if payload.get("uk"):
        phones.append(f"UK /{payload['uk']}/")
    if payload.get("us"):
        phones.append(f"US /{payload['us']}/")
    if payload.get("phonetic"):
        phones.append(payload["phonetic"])
    phone_text = " · ".join(phones)

    examples = _examples_text(payload)
    forms = "；".join(dict.fromkeys(payload.get("forms") or []))
    exam = " / ".join(payload.get("exam") or [])
    web = "；".join(payload.get("web") or [])
    stamp = time.strftime("%Y-%m-%d %H:%M")

    # 自定义模板优先。占位符：{{word}} {{pos}} {{zh}} {{en}} {{phonetic}}
    #                   {{examples}} {{forms}} {{exam}} {{web}} {{time}}
    if template:
        ctx = {
            "word": word, "pos": pos, "zh": zh, "en": en,
            "phonetic": phone_text, "examples": examples,
            "forms": forms, "exam": exam, "web": web, "time": stamp,
        }
        filled = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(ctx.get(m.group(1).lower(), "")), template)
        body = _sanitize_block(filled)
        lines = body.splitlines()
        # 词表的第一行永远是单词（自动补）。模板里如果自己写了一模一样的单词行，
        # 把重复的那行去掉；但模板只输出一行（比如 "{{word}} | {{zh}}"）时，
        # 仍然要补单词行——否则解析器会把整行当成单词，判重和导出全乱。
        if lines and normalize_word(lines[0].strip()) == normalize_word(word):
            lines = lines[1:]
        body = "\n".join(lines)
        return _sanitize_block(word + (("\n" + body) if body else "")) + "\n\n"

    head = ""
    if fields.get("zh", True):
        head = _meaning_line(payload, show_pos=bool(fields.get("pos", True)))
    lines = [word]
    if head:
        lines.append(head)

    if fields.get("en") and en:
        lines.append(f"{lab['en']}：{en}")
    if fields.get("phonetic") and phone_text:
        lines.append(f"{lab['phonetic']}：{phone_text}")
    if fields.get("examples") and examples:
        lines.append(f"{lab['examples']}：")
        for line in examples.splitlines():
            text = line.strip()
            if text.startswith("- "):
                text = text[2:]
            if text:
                lines.append("  " + text)
    if fields.get("forms") and forms:
        lines.append(f"{lab['forms']}：{forms}")
    if fields.get("exam") and exam:
        lines.append(f"{lab['exam']}：{exam}")
    if fields.get("web") and web:
        lines.append(f"{lab['web']}：{web}")
    if fields.get("time"):
        lines.append(f"{lab['time']}：{stamp}")
    return _sanitize_block("\n".join(lines)) + "\n\n"





def legacy_notes_to_plain(text: str) -> str | None:
    """转换旧版标题式词表，也保留 1.4.2 已追加的纯文本块。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip()
    has_title = bool(re.match(r"^# (?:生词本|Wordbook)(?:\n|$)", text))
    if not has_title and not re.match(r"^##[ \t]+\S", text):
        return None
    sections = re.split(r"(?m)^##[ \t]+([^\n]+)\n?", text)
    entries = [
        block for block in re.split(r"\n[^\S\n]*\n", sections[0].strip())
        if WORD_RE.fullmatch(block.split("\n", 1)[0].strip())
    ]
    for word, section in zip(sections[1::2], sections[2::2]):
        blocks = re.split(r"\n[^\S\n]*\n", section.strip())
        lines = blocks[0].splitlines()
        appended = []
        for block in blocks[1:]:
            first_line = block.split("\n", 1)[0].strip()
            if WORD_RE.fullmatch(first_line):
                appended.append(block.strip())
            else:
                lines.extend(block.splitlines())
        body = []
        pos = ""
        definition = ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            m = re.match(r"^-\s+\*\*(.+?)\*\*\s*[：:](.*)$", stripped)
            if m:
                label, value = m.groups()
                if label in ("词性", "Part of speech"):
                    pos = value.strip()
                elif label in ("中文", "释义", "Chinese", "Definition"):
                    definition = value.strip()
                else:
                    body.append(f"{label}：{value}")
            elif stripped.startswith("- "):
                body.append("  " + stripped[2:])
            else:
                body.append(stripped)
        meaning = " ".join(part for part in (pos, definition) if part)
        if meaning:
            body.insert(0, meaning)
        entries.append("\n".join([word.strip(), *body]))
        entries.extend(appended)
    return "\n\n".join(entries) + ("\n" if entries else "")


def copy_legacy_notes(path: str, converted: str) -> str:
    stem = os.path.splitext(path)[0]
    for index in itertools.count(1):
        suffix = f" ({index})" if index > 1 else ""
        destination = f"{stem} - imported{suffix}.txt"
        try:
            with open(destination, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(converted)
        except FileExistsError:
            continue
        return destination


def migrate_legacy_md(txt_path: str) -> None:
    """把旧的 词表.md 迁成 词表.txt（一次性；原文件改名为 .bak 留着）。"""
    md_path = os.path.splitext(txt_path)[0] + ".md"
    if os.path.exists(txt_path) or not os.path.exists(md_path):
        return
    converted = legacy_notes_to_plain(read_notes(md_path))
    if not converted:
        return
    try:
        with open(txt_path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(converted)
    except FileExistsError:
        return
    try:
        os.replace(md_path, md_path + ".bak")
    except OSError:
        pass
    print(f"已迁移：{md_path} -> {txt_path}（原文件改名 .bak）")


def _decodes_as(raw: bytes, encoding: str) -> bool:
    try:
        raw.decode(encoding)
        return True
    except UnicodeDecodeError:
        return False


def ensure_utf8(path: str) -> bool:
    """追加前把非 UTF-8 的词表转成 UTF-8，并留一份 .bak。

    GBK 文件如果直接拿 UTF-8 追加，会变成混编码的怪文件——读回来一堆乱码。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    if not raw or _decodes_as(raw, "utf-8"):
        return False                      # 空文件 / 本来就是 UTF-8（含 BOM）
    if not _decodes_as(raw, "gbk"):
        return False                      # 认不出的编码，别乱动
    text = raw.decode("gbk")
    try:
        shutil.copy2(path, path + ".bak")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def read_notes(path: str) -> str:
    """读词表：兼容 UTF-8 BOM 与 GBK。

    记事本另存为「UTF-8 with BOM」很常见，BOM 会粘在第一个词前面，
    让判重永远认不出第一条——所以先按 utf-8-sig 解（能自动吃掉 BOM）。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return ""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse_entries(text: str) -> list[tuple[str, str]]:
    """纯文本词表：空行分块，第一行是单词，第二行是释义。"""
    entries = []
    text = text or ""
    converted = legacy_notes_to_plain(text)
    for block in re.split(r"\n\s*\n", (text if converted is None else converted).strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        word = lines[0]
        meaning = lines[1] if len(lines) > 1 else ""
        entries.append((word, meaning))
    return entries


def is_saved(word: str, path: str | None = None) -> bool:
    path = path or NOTES_PATH
    target = normalize_word(word)
    return any(normalize_word(w) == target for w, _ in parse_entries(read_notes(path)))


def save_word(word: str, path: str | None = None, lang: str = "zh") -> dict:
    word = normalize_word(word)
    with _lock:
        require_existing = not path and _CUSTOM_NOTES_ACTIVE
        path = path or NOTES_PATH
        if require_existing and not os.path.isfile(path):
            return {"ok": False, "code": "notes_missing", "error": message("notes_missing", lang)}
        try:
            existing = read_notes(path)
            if any(normalize_word(saved) == word for saved, _ in parse_entries(existing)):
                return {"ok": True, "duplicate": True, "path": path}
            if legacy_notes_to_plain(existing) is not None:
                return {"ok": False, "code": "notes_legacy", "error": message("notes_legacy", lang)}

            payload = lookup(word)
            block = format_entry(payload, load_config(), lang=lang)

            new_file = not os.path.exists(path)
            # 撤销的基准点是正文起点，需要先算出分隔空行。
            size_before = 0
            prefix = ""
            if not new_file:
                existing = read_notes(path)
                if legacy_notes_to_plain(existing) is not None:
                    return {"ok": False, "code": "notes_legacy", "error": message("notes_legacy", lang)}
                if ensure_utf8(path):
                    print("词表原来是 GBK 编码，已转成 UTF-8（旧文件备份为 .bak）")
                size_before = os.path.getsize(path)
                if existing.strip():
                    if not existing.endswith("\n"):
                        prefix = "\n\n"
                    elif not existing.endswith("\n\n"):
                        prefix = "\n"

            def open_notes(filename, flags):
                if require_existing:
                    flags &= ~os.O_CREAT
                return os.open(filename, flags)

            with open(path, "a", encoding="utf-8", newline="\n", opener=open_notes) as fh:
                fh.write(prefix + block)
        except FileNotFoundError:
            if not require_existing:
                raise
            return {"ok": False, "code": "notes_missing", "error": message("notes_missing", lang)}

        block_start = size_before + len(prefix.encode("utf-8"))
        count = len(parse_entries(read_notes(path)))
        _cache.pop(word, None)   # 缓存里还留着 saved=False，不失效会让界面反复横跳

        # 记住正文起点、写完后的长度和那一段内容：撤销时三者都要对得上
        _UNDO.clear()
        _UNDO.update({
            "path": path,
            "created": new_file,
            "size": block_start,
            "word": word,
            "block": block,
            "after": os.path.getsize(path),
        })

        return {"ok": True, "duplicate": False, "path": path, "count": count}


def undo_last(lang: str = "zh") -> dict:
    """撤掉最近写入的那一条。

    只认最近一次，而且必须同时满足：
      - 文件当前长度 == 写完之后记录的长度（说明没人追加过）
      - 文件尾巴 == 当时写进去的那一段（说明没被改过）
    少一样就拒绝——宁可撤不掉，也不能把你自己后来加的词截掉。
    """
    with _lock:
        if not _UNDO:
            return {"ok": False, "code": "undo_none", "error": message("undo_none", lang)}
        path = _UNDO.get("path") or NOTES_PATH
        size = int(_UNDO.get("size") or 0)
        after = int(_UNDO.get("after") or 0)
        word = _UNDO.get("word") or ""
        block = _UNDO.get("block") or ""

        if not os.path.exists(path):
            _UNDO.clear()
            return {"ok": False, "code": "undo_gone", "error": message("undo_gone", lang)}

        cur = os.path.getsize(path)
        if cur != after:
            _UNDO.clear()
            return {"ok": False, "code": "undo_changed", "error": message("undo_changed", lang)}

        with open(path, "rb") as fh:
            fh.seek(size)
            tail = fh.read()
        if tail != block.encode("utf-8"):
            _UNDO.clear()
            return {"ok": False, "code": "undo_mismatch", "error": message("undo_mismatch", lang)}

        created = _UNDO.get("created", False)
        with open(path, "r+b") as fh:
            fh.truncate(size)
        _UNDO.clear()

        # 撤完变成空文件就收掉
        try:
            if created and os.path.getsize(path) == 0:
                os.remove(path)
        except OSError:
            pass

        _cache.pop(word, None)
        count = 0
        if os.path.exists(path):
            count = len(parse_entries(read_notes(path)))
        return {"ok": True, "word": word, "count": count}


def export_anki_csv() -> bytes:
    """把词表导成 Anki 能吃的 CSV（带 BOM，Excel 不乱码）。"""
    rows = [[_csv_safe(w), _csv_safe(m)] for w, m in parse_entries(read_notes(NOTES_PATH))]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["单词", "释义"])
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def _csv_safe(value: str) -> str:
    """防 Excel 把 = + - @ 开头的单元格当公式执行。"""
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------

def default_notes_path() -> str:
    """词表放桌面：程序住在 C:\\生词本，数据留在你看得见的地方。"""
    desk = ""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            desk = os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0])
    except Exception:
        desk = ""
    if not desk or not os.path.isdir(desk):
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desk):
        state_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Wordbook")
        return os.path.join(state_dir if getattr(sys, "frozen", False) else HERE, "词表.txt")
    return os.path.join(desk, "词表.txt")


NOTES_PATH = default_notes_path()
_CLI_NOTES_PATH = False
_CUSTOM_NOTES_ACTIVE = False

# 启动器要按「真实端口」打开页面，而端口可能因为占用而顺延。
# 与其让 VBS 去扫端口（每次建 COM 对象要几百毫秒），不如后端自己写一张小纸条。
PORT_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA") or HERE, "Wordbook", "port.txt"
)


CONFIG_PATH = os.path.join(os.path.dirname(PORT_FILE), "config.json")

# 记录哪些字段。默认保持「两行」，想加自己拧开关。
DEFAULT_FIELDS = {
    "pos": True,
    "zh": True,
    "en": False,
    "phonetic": False,
    "examples": False,
    "forms": False,
    "exam": False,
    "web": False,
    "time": False,
}

DEFAULT_CONFIG = {"fields": dict(DEFAULT_FIELDS), "template": "", "lang": "auto", "notes_path": ""}


def load_config(*, strict: bool = False) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError):
        if strict:
            raise
        return cfg
    if not isinstance(data, dict):
        if strict:
            raise ValueError("Settings must be a JSON object")
        return cfg
    fields = data.get("fields")
    if isinstance(fields, dict):
        for key in DEFAULT_FIELDS:
            if key in fields:
                cfg["fields"][key] = bool(fields[key])
    if isinstance(data.get("template"), str):
        cfg["template"] = data["template"]
    if data.get("lang") in ("auto", "zh", "en"):
        cfg["lang"] = data["lang"]
    if isinstance(data.get("notes_path"), str):
        cfg["notes_path"] = data["notes_path"]
    return cfg


def save_config(cfg: dict) -> None:
    global NOTES_PATH, _CUSTOM_NOTES_ACTIVE
    with _lock:
        clean = load_config(strict=True)
        fields = cfg.get("fields")
        if isinstance(fields, dict):
            for key in DEFAULT_FIELDS.keys() & fields.keys():
                clean["fields"][key] = bool(fields[key])
        if "template" in cfg:
            clean["template"] = str(cfg["template"] or "")
        if "lang" in cfg:
            lang = cfg["lang"]
            clean["lang"] = lang if lang in ("auto", "zh", "en") else "auto"
        update_path = "notes_path" in cfg and not _CLI_NOTES_PATH
        if update_path:
            path = cfg["notes_path"]
            if not isinstance(path, str):
                raise ValueError("Expected a file path")
            path = path.strip().strip('"')
            if path:
                if not os.path.isabs(path) or os.path.splitext(path)[1].lower() not in (".txt", ".md"):
                    raise ValueError("Choose an absolute path to a .txt or .md file")
                if not os.path.isfile(path):
                    raise ValueError("The word list file does not exist")
                path = os.path.normpath(path)
                converted = legacy_notes_to_plain(read_notes(path))
                if converted is not None:
                    path = copy_legacy_notes(path, converted)
            clean["notes_path"] = path
        directory = os.path.dirname(CONFIG_PATH)
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as fh:
            temp_path = fh.name
            try:
                json.dump(clean, fh, ensure_ascii=False, indent=2)
                fh.close()
                os.replace(temp_path, CONFIG_PATH)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
        if update_path:
            active_path = path or default_notes_path()
            if active_path != NOTES_PATH:
                _UNDO.clear()
            NOTES_PATH = active_path
            _CUSTOM_NOTES_ACTIVE = bool(path)


# 撤销只保留「最近一次」，够用又不惹事
_UNDO: dict = {}


# 词条里这些标签会跟着界面语言变
LABELS = {
    "zh": {
        "en": "英文", "phonetic": "音标", "examples": "例句",
        "forms": "变形", "exam": "考试", "web": "网络释义", "time": "收录",
        "none": "（词典没给释义）",
    },
    "en": {
        "en": "English", "phonetic": "Phonetic", "examples": "Examples",
        "forms": "Forms", "exam": "Exam", "web": "Web", "time": "Added",
        "none": "(no definition from the dictionary)",
    },
}

# 给前端用的错误文案（英文界面下不要弹中文）
MESSAGES = {
    "zh": {
        "empty": "先输入一个单词",
        "bad_word": "只认单词或短语，别塞奇怪字符",
        "not_found": "两个词典都没收录",
        "net_error": "词典暂时不可用（网络或服务异常，不是这个词的问题）",
        "cross_site": "拒绝跨站请求",
        "no_api": "没这个接口",
        "bad_json": "请求体不是 JSON",
        "save_failed": "写入失败",
        "open_failed": "打不开词表",
        "undo_none": "没有可撤销的记录",
        "undo_changed": "词表在之后被改动过，撤销会误伤，已放弃",
        "undo_mismatch": "词表内容和记录对不上，撤销已放弃",
        "undo_gone": "词表文件已经不在了",
        "dup": "这个词已经在词表里了",
        "cfg_failed": "保存设置失败",
        "cfg_load_failed": "无法读取设置，请检查设置文件后重试。",
        "export_failed": "无法读取词表。请关闭占用词表的程序后重试导出。",
        "undo_failed": "无法撤销。请关闭占用词表的程序后重试。",
        "notes_missing": "自定义词表文件已找不到。请在「记录设置」中重新选择，或清空路径以使用默认词表。",
        "notes_legacy": "当前词表是旧版 Markdown。请在「记录设置」中选择此文件并保存，导入纯文本副本后即可继续记录。",
    },
    "en": {
        "empty": "Type a word first",
        "bad_word": "Only words or short phrases, please",
        "not_found": "Neither dictionary has this word",
        "net_error": "The dictionaries are unavailable (network or service issue, not the word)",
        "cross_site": "Cross-site request rejected",
        "no_api": "No such endpoint",
        "bad_json": "Request body is not JSON",
        "save_failed": "Could not write the file",
        "open_failed": "Could not open the notes",
        "undo_none": "Nothing to undo",
        "undo_changed": "The notes changed after saving, so undo refused",
        "undo_mismatch": "The notes do not match the last write, so undo refused",
        "undo_gone": "The notes file is gone",
        "dup": "Already in the word list",
        "cfg_failed": "Could not save settings",
        "cfg_load_failed": "Could not read settings. Check the settings file and try again.",
        "export_failed": "Could not read the word list. Close any app using it and try exporting again.",
        "undo_failed": "Could not undo. Close any app using the word list and try again.",
        "notes_missing": "Your custom word list is missing. Select it again in Settings or clear its path to use the default.",
        "notes_legacy": "This is an older Markdown word list. Select it in Settings and save to import a plain-text copy before adding words.",
    },
}


def message(key: str, lang: str = "zh") -> str:
    return MESSAGES.get(lang, MESSAGES["zh"]).get(key) or MESSAGES["zh"].get(key, key)


def resolve_lang(query_lang: str | None, accept: str | None, cfg: dict | None = None) -> str:
    """优先级：?lang= > Accept-Language > 配置 > 中文。"""
    for candidate in (query_lang, (accept or "").split(",")[0].split("-")[0], (cfg or {}).get("lang")):
        if candidate in ("zh", "en"):
            return candidate
    return "zh"


def _write_port_file(port: int) -> None:
    try:
        os.makedirs(os.path.dirname(PORT_FILE), exist_ok=True)
        with open(PORT_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(port))
    except OSError:
        pass


def _clear_port_file(port: int) -> None:
    """兼容旧调用：现在退出时不再删纸条。

    纸条是启动器找实例的唯一线索，删掉反而会让它多花几秒甚至开重。
    失效的纸条会被 ping 校验挡掉，留着无害。
    """
    return


def _track_client(delta: int) -> None:
    global _SSE_CLIENTS
    with _SSE_LOCK:
        _SSE_CLIENTS = max(0, _SSE_CLIENTS + delta)


def _graceful_shutdown(delay: float = 0.25) -> None:
    """先把响应发出去，再把服务停掉。"""
    time.sleep(delay)
    if _SERVER is not None:
        try:
            _SERVER.shutdown()
        except Exception:
            pass


def _idle_watchdog(started: float, port: int) -> None:
    """双判据：快路径管关标签，慢路径管半开连接；另外处理睡眠与「生下来就没人连」。"""
    global _LAST_HEARTBEAT
    last_tick = time.time()
    last_port_write = started
    while True:
        time.sleep(2)
        now = time.time()

        # 纸条自愈：被清理或写失败时，最多 5 秒后重新写回来，
        # 启动器就不会因为找不到线索而白开一份
        if now - last_port_write > 5:
            _write_port_file(port)
            last_port_write = now

        # 一次 tick 跳了 10 秒以上 = 刚从睡眠/挂起回来。
        # 墙钟走了一大截，但页面其实一直开着，必须把心跳时间戳一起拨过来，
        # 否则掀开盖子第一眼就被判成僵尸、当场自杀。
        if now - last_tick > 10:
            if _LAST_HEARTBEAT:
                _LAST_HEARTBEAT = now
            last_tick = now
            continue
        last_tick = now

        # 启动后一直没人连过：60 秒还没人搭理就自己走，避免孤儿进程越积越多
        if not _LAST_HEARTBEAT:
            if now - started > 60:
                print("启动后 60 秒没有任何页面连上来，服务自动退出。")
                _graceful_shutdown(0)
                return
            continue

        idle = now - _LAST_HEARTBEAT
        with _SSE_LOCK:
            clients = _SSE_CLIENTS
        if clients == 0 and idle > FAST_EXIT_SEC:
            print("没有页面连着了，服务自动退出。")
            _graceful_shutdown(0)
            return
        if idle > SLOW_EXIT_SEC:
            print("心跳已经停了 %d 秒，服务自动退出。" % int(idle))
            _graceful_shutdown(0)
            return


class Handler(BaseHTTPRequestHandler):
    server_version = f"Wordbook/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 控制台只留一行简版
        # pythonw 下 sys.stderr 是 None，不判空会把每个请求都砸崩
        try:
            if sys.stderr is not None:
                sys.stderr.write("  · %s\n" % (fmt % args))
        except Exception:
            pass

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # -- routes ----------------------------------------------------------
    def _stream_events(self) -> None:
        """SSE 长连接：页面活着就挂着，标签一关这里就写失败。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        _track_client(+1)
        try:
            while True:
                self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
                time.sleep(5)
        except Exception:
            pass
        finally:
            _track_client(-1)
            self.close_connection = True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        # 读接口也要守 Host：/api/export-anki 能把整份词表吐出来，
        # 只防写入不防读取，等于门锁了窗没关。
        if route.startswith("/api/") and not self._same_site():
            self._json(403, {"ok": False, "code": "cross_site",
                             "error": message("cross_site", "zh")})
            return

        if route in ("/", "/index.html"):
            try:
                with open(INDEX_HTML, "rb") as fh:
                    body = fh.read()
            except FileNotFoundError:
                self._send(500, b"index.html missing next to wordbook.py", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return

        if route == "/api/watch":
            self._stream_events()
            return

        if route == "/api/ping":
            self._json(200, {"ok": True, "app": "wordbook", "version": __version__})
            return

        if route == "/api/config":
            try:
                config = load_config(strict=True)
            except (OSError, ValueError):
                self._json(500, {"ok": False, "code": "cfg_load_failed",
                                 "error": message("cfg_load_failed", self._lang())})
                return
            legacy = False
            if _CUSTOM_NOTES_ACTIVE:
                try:
                    legacy = legacy_notes_to_plain(read_notes(NOTES_PATH)) is not None
                except OSError:
                    pass
            self._json(200, {
                "ok": True,
                "config": config,
                "path": NOTES_PATH,
                "notes_path_locked": _CLI_NOTES_PATH,
                "notes_path_fallback": bool(config["notes_path"] and not _CLI_NOTES_PATH
                                            and not _CUSTOM_NOTES_ACTIVE),
                "notes_path_legacy": legacy,
                "undo_available": bool(_UNDO),   # 刷新页面后按钮还能回来
            })
            return

        if route == "/api/export-anki":
            try:
                body = export_anki_csv()
            except OSError:
                self._json(500, {"ok": False, "code": "export_failed",
                                 "error": message("export_failed", self._lang())})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="wordbook-anki.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/lookup":
            lang = self._lang()
            params = urllib.parse.parse_qs(parsed.query)
            raw = (params.get("word") or [""])[0]
            word = normalize_word(raw)
            if not word:
                self._json(400, {"ok": False, "code": "empty", "error": message("empty", lang)})
                return
            if not WORD_RE.match(word):
                self._json(400, {"ok": False, "code": "bad_word", "error": message("bad_word", lang)})
                return
            try:
                payload = lookup(word)
            except LookupNetworkError as exc:
                self._json(502, {"ok": False, "code": "net_error",
                                 "error": message("net_error", lang), "detail": str(exc)})
                return
            except LookupError as exc:
                self._json(404, {"ok": False, "code": "not_found",
                                 "error": message("not_found", lang), "detail": str(exc)})
                return
            except Exception as exc:
                self._json(502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            # 收录状态每次现算：用户可能刚在别处删掉/加了词，缓存靠不住
            payload["saved"] = is_saved(payload["word"])
            self._json(200, {"ok": True, **payload})
            return

        if route == "/api/notes":
            payload = {
                "ok": True,
                "path": NOTES_PATH,
                "exists": os.path.exists(NOTES_PATH),
                "count": len(parse_entries(read_notes(NOTES_PATH))),
            }
            self._json(200, payload)
            return

        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        self._json(404, {"ok": False, "error": "没这个页面"})

    def _lang(self) -> str:
        """?lang= 优先，其次 Accept-Language，最后配置文件。"""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return resolve_lang(
            (query.get("lang") or [None])[0],
            self.headers.get("Accept-Language"),
            load_config(),
        )

    def _same_site(self) -> bool:
        """挡跨站与 DNS rebinding：Host 必须字面是本机。"""
        port = self.server.server_port
        host = (self.headers.get("Host") or "").strip().lower()
        # Host 是请求方自己带来的，不能拿它当比较基准——evil.com 解析到 127.0.0.1 后，
        # 浏览器会认为 Origin 和 Host 同源，两道检查一起过。所以这里钉死白名单。
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            if origin == "null" or not origin.lower().endswith("://" + host):
                return False
        return True

    def do_POST(self):
        global _LAST_HEARTBEAT
        route = urllib.parse.urlparse(self.path).path

        if not self._same_site():
            self._json(403, {"ok": False, "code": "cross_site",
                             "error": message("cross_site", self._lang())})
            return

        if route == "/api/heartbeat":
            _LAST_HEARTBEAT = time.time()
            self._json(200, {"ok": True})
            return

        if route == "/api/shutdown":
            _LAST_HEARTBEAT = 0.0
            self._json(200, {"ok": True, "bye": True})
            threading.Thread(target=_graceful_shutdown, daemon=True).start()
            return

        if route == "/api/preview":
            lang = self._lang()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8", "replace") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "code": "bad_json", "error": message("bad_json", lang)})
                return

            word = normalize_word(body.get("word") or "")
            if not word or not WORD_RE.match(word):
                self._json(400, {"ok": False, "code": "bad_word", "error": message("bad_word", lang)})
                return

            preview_cfg = {
                "fields": body.get("fields") if isinstance(body.get("fields"), dict) else {},
                "template": body.get("template") or "",
                "lang": lang,
            }
            try:
                payload = lookup(word)
            except LookupNetworkError as exc:
                self._json(502, {"ok": False, "code": "net_error",
                                 "error": message("net_error", lang), "detail": str(exc)})
                return
            except LookupError as exc:
                self._json(404, {"ok": False, "code": "not_found",
                                 "error": message("not_found", lang), "detail": str(exc)})
                return
            self._json(200, {"ok": True, "text": format_entry(payload, preview_cfg, lang=lang).rstrip()})
            return

        if route == "/api/config":
            lang = self._lang()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except ValueError:
                self._json(400, {"ok": False, "code": "bad_json", "error": message("bad_json", lang)})
                return
            try:
                save_config(payload if isinstance(payload, dict) else {})
            except (OSError, ValueError) as exc:
                self._json(500, {"ok": False, "code": "cfg_failed",
                                 "error": f"{message('cfg_failed', lang)}: {exc}"})
                return
            self._json(200, {"ok": True, "config": load_config()})
            return

        if route == "/api/undo":
            lang = self._lang()
            try:
                result = undo_last(lang)
            except OSError:
                self._json(500, {"ok": False, "code": "undo_failed", "error": message("undo_failed", lang)})
                return
            result["undo_available"] = bool(_UNDO)
            self._json(200, result)
            return

        if route == "/api/open-notes":
            lang = self._lang()
            try:
                os.startfile(NOTES_PATH)      # 只有 Windows 有这个
            except Exception as exc:
                self._json(500, {"ok": False, "code": "open_failed",
                                 "error": f"{message('open_failed', lang)}: {exc}"})
                return
            self._json(200, {"ok": True})
            return

        if route != "/api/save":
            self._json(404, {"ok": False, "code": "no_api", "error": message("no_api", self._lang())})
            return

        lang = self._lang()
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "code": "bad_json", "error": message("bad_json", lang)})
            return

        word = normalize_word(body.get("word") or "")
        if not word or not WORD_RE.match(word):
            self._json(400, {"ok": False, "code": "bad_word", "error": message("bad_word", lang)})
            return

        try:
            self._json(200, save_word(word, lang=lang))
        except LookupNetworkError as exc:
            self._json(502, {"ok": False, "code": "net_error",
                             "error": message("net_error", lang), "detail": str(exc)})
        except LookupError as exc:
            self._json(404, {"ok": False, "code": "not_found",
                             "error": message("not_found", lang), "detail": str(exc)})
        except OSError as exc:
            self._json(500, {"ok": False, "code": "save_failed",
                             "error": f"{message('save_failed', lang)}: {exc}"})
        except Exception as exc:
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 上 SO_REUSEADDR 不是「快速重用」，而是允许抢绑别人正在监听的端口。
    # 所以这里既不开它，也不靠它做快速重启。
    allow_reuse_address = False

    def server_bind(self):
        # SO_EXCLUSIVEADDRUSE 才是 Windows 的独占语义：第二个实例会干净地 [WinError 10048]
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def bind_server(preferred: int, tries: int = 20) -> Server:
    for port in range(preferred, min(preferred + tries, 65536)):
        try:
            return Server(("127.0.0.1", port), Handler)
        except OSError as exc:
            # Windows 的独占端口冲突也可能返回 WSAEACCES。
            if (exc.errno not in (errno.EADDRINUSE, errno.EACCES)
                    and getattr(exc, "winerror", None) not in (10013, 10048)):
                raise
    raise SystemExit(f"端口 {preferred}~{min(preferred + tries - 1, 65535)} 不可用，换个 --port 吧")


def main(on_existing=None, open_browser=None) -> None:
    global NOTES_PATH, _SERVER, _CLI_NOTES_PATH, _CUSTOM_NOTES_ACTIVE

    parser = argparse.ArgumentParser(description="本地生词本（左侧查词，右侧释义，记进 Markdown）")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认 8765；显式指定会跳过单实例保护")
    parser.add_argument("--file", default=None, help="指定词表路径；默认使用设置里的词表或桌面上的 词表.txt")
    parser.add_argument("--version", action="version", version=f"wordbook {__version__}")
    parser.add_argument("--no-browser", action="store_true", help="别自动开浏览器")
    args = parser.parse_args()

    # 常规启动（没显式给端口）只允许一个实例：靠命名互斥体，
    # 不依赖 port.txt 是否存在——那张纸条丢了也不会开出第二份
    if args.port is None and not acquire_single_instance():
        if on_existing is not None and not args.no_browser:
            on_existing()
            return
        print("生词本已经在跑了；要再开一份调试，请加 --port <端口>。")
        return

    _CLI_NOTES_PATH = bool(args.file)
    configured_path = load_config()["notes_path"]
    _CUSTOM_NOTES_ACTIVE = not _CLI_NOTES_PATH and bool(configured_path and os.path.isfile(configured_path))
    NOTES_PATH = os.path.abspath(args.file) if args.file else (
        configured_path if _CUSTOM_NOTES_ACTIVE else default_notes_path()
    )
    if not _CUSTOM_NOTES_ACTIVE:
        migrate_legacy_md(NOTES_PATH)

    base_port = args.port if args.port is not None else 8765

    if not os.path.exists(INDEX_HTML):
        raise SystemExit(f"缺少 index.html，得跟 wordbook.py 放一起：{INDEX_HTML}")

    if not 0 <= base_port <= 65535:
        parser.error("--port 必须在 0~65535 之间")
    httpd = bind_server(base_port)
    port = httpd.server_port
    url = f"http://127.0.0.1:{port}/"
    _SERVER = httpd
    _write_port_file(port)
    threading.Thread(target=_idle_watchdog, args=(time.time(), port), daemon=True).start()

    print("生词本已启动：", url)
    print("词表文件：", NOTES_PATH)
    print("关闭这个窗口（或 Ctrl+C）就停。")

    try:
        if not args.no_browser:
            if open_browser is None:
                threading.Timer(0.6, lambda: webbrowser.open(url)).start()
            else:
                threading.Thread(target=open_browser, args=(url,), daemon=True).start()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，收工。")
    finally:
        httpd.server_close()
        _clear_port_file(port)


if __name__ == "__main__":
    main()
