#!/usr/bin/env python3
"""记忆库体检：检查裸 PII、缺来源/时间戳、重复条目与原子性。

对应 SKILL.md 的自检项：无裸 PII、有来源与时间戳、重复记忆被处理、
关键记忆有保护标记。检查规则全部为确定性判断，不依赖 LLM。

用法：
    python memory_lint.py memories.json
    python memory_lint.py memories.json --check          # 存在 ERROR 时退出码 1
    python memory_lint.py memories.json --format json
    python memory_lint.py memories.json --similarity 0.7 --max-length 200

输入格式：JSON 数组，或 {"memories": [...]}，或 JSONL 逐行一条记忆。
每条记忆建议字段：id / user_id / type / content / created_at / source /
occurred_at / protected / access_count

退出码：0 无 ERROR；1 存在 ERROR（仅 --check）；2 读取或参数错误。
"""

import argparse
import json
import re
import sys
from datetime import datetime

PII_PATTERNS = (
    ("身份证号", "ERROR", re.compile(r"\b\d{17}[\dXx]\b|\b\d{15}\b")),
    ("银行卡号", "ERROR", re.compile(r"\b\d{13,19}\b")),
    ("密码表述", "ERROR", re.compile(r"(密码|口令|passwd|password)\s*(是|为|:|：)\s*\S+", re.I)),
    ("密钥表述", "ERROR", re.compile(r"(api[_\s-]?key|token|secret)\s*(是|为|:|：)\s*\S+", re.I)),
    ("手机号", "WARN", re.compile(r"\b1[3-9]\d{9}\b")),
    ("邮箱", "WARN", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
)

PROTECTED_HINTS = ("过敏", "禁忌", "护照", "证件", "病史", "用药")


def load_memories(path):
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.lstrip()
    if stripped.startswith("["):
        return json.loads(content)
    if stripped.startswith("{"):
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("memories", "records", "items"):
                if key in data:
                    return data[key]
            return [data]
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def as_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def tokenize(text):
    """ASCII 按词切分，中文按二元组切分（中文没有空格，按词切会导致相似句无法比对）。"""
    text = str(text).lower()
    words = set(w for w in re.findall(r"[a-z0-9_]+", text) if len(w) > 1)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    bigrams = set()
    for chunk in cjk:
        for i in range(len(chunk) - 1):
            bigrams.add(chunk[i:i + 2])
    return words | bigrams


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_pii(index, memory, text):
    issues = []
    for label, level, pattern in PII_PATTERNS:
        if pattern.search(text):
            suffix = "需脱敏后再落库" if level == "ERROR" else "按业务确认是否允许明文存储"
            issues.append((level, "PII", "第 %d 条疑似包含%s明文，%s" % (index, label, suffix)))
    return issues


def check_required_fields(index, memory):
    issues = []
    if not memory.get("source"):
        issues.append(("ERROR", "NO_SOURCE", "第 %d 条缺少 source，无法回溯来源会话" % index))
    if not memory.get("created_at"):
        issues.append(("ERROR", "NO_TIMESTAMP", "第 %d 条缺少 created_at，无法按时间取当前事实" % index))
    memory_type = memory.get("type")
    if memory_type and memory_type not in ("episodic", "semantic", "procedural"):
        issues.append(("WARN", "TYPE_UNKNOWN", "第 %d 条 type=%s 不在 episodic/semantic/procedural 中"
                       % (index, memory_type)))
    if not memory_type:
        issues.append(("WARN", "NO_TYPE", "第 %d 条未标注认知类型，无法按类型检索与压缩" % index))
    if memory.get("type") == "episodic" and not memory.get("occurred_at"):
        issues.append(("WARN", "NO_OCCURRED", "第 %d 条为情景记忆但缺 occurred_at，无法按时间线检索" % index))
    return issues


def check_protection(index, memory, text):
    if memory.get("protected"):
        return []
    for hint in PROTECTED_HINTS:
        if hint in text:
            return [("WARN", "NEED_PROTECT", "第 %d 条含「%s」类关键信息但未设 protected，可能被压缩淘汰"
                     % (index, hint))]
    return []


def check_atomicity(index, text, max_length):
    issues = []
    if len(text) > max_length:
        issues.append(("WARN", "TOO_LONG", "第 %d 条内容 %d 字超过 %d，建议拆分为可部分更新的结构化字段"
                       % (index, len(text), max_length)))
    return issues


def check_duplicates(memories, threshold):
    issues = []
    tokens = [(i, tokenize(as_text(m.get("content")))) for i, m in enumerate(memories, start=1)]
    for i in range(len(tokens)):
        for j in range(i + 1, len(tokens)):
            score = jaccard(tokens[i][1], tokens[j][1])
            if score >= threshold:
                issues.append(("WARN", "DUPLICATE",
                               "第 %d 条与第 %d 条相似度 %.2f，建议聚类去重或归档"
                               % (tokens[i][0], tokens[j][0], score)))
    return issues


def lint(memories, similarity, max_length):
    issues = []
    for index, memory in enumerate(memories, start=1):
        text = as_text(memory.get("content"))
        issues.extend(check_pii(index, memory, text))
        issues.extend(check_required_fields(index, memory))
        issues.extend(check_protection(index, memory, text))
        issues.extend(check_atomicity(index, text, max_length))
    issues.extend(check_duplicates(memories, similarity))
    return issues


def report(path, issues, as_json):
    counts = {"ERROR": 0, "WARN": 0}
    for level, _, _ in issues:
        counts[level] += 1

    if as_json:
        print(json.dumps({"file": path, "issues": [
            {"level": lv, "code": code, "message": msg} for lv, code, msg in issues
        ], "counts": counts}, ensure_ascii=False, indent=2))
        return counts

    print("=" * 62)
    print("记忆库体检：" + path)
    print("=" * 62)
    for level, code, message in issues:
        print("[%s] %-14s %s" % (level, code, message))
    if not issues:
        print("未发现问题")
    print("-" * 62)
    print("合计：ERROR %d，WARN %d" % (counts["ERROR"], counts["WARN"]))
    print("结论：" + ("不通过，需修复后复检" if counts["ERROR"] else "通过"))
    return counts


def main():
    parser = argparse.ArgumentParser(description="记忆库体检：PII、来源、时间戳、重复与原子性")
    parser.add_argument("memories", help="记忆库文件（JSON / JSONL）")
    parser.add_argument("--check", action="store_true", help="存在 ERROR 时退出码为 1")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    parser.add_argument("--similarity", type=float, default=0.7, help="重复判定阈值（默认 0.7）")
    parser.add_argument("--max-length", type=int, default=200, help="单条内容长度上限（默认 200）")
    args = parser.parse_args()

    try:
        memories = load_memories(args.memories)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    issues = lint(memories, args.similarity, args.max_length)
    counts = report(args.memories, issues, args.format == "json")
    if args.check and counts["ERROR"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
