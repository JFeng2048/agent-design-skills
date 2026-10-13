#!/usr/bin/env python3
"""分块质量体检：块大小分布、实际重叠率、结构边界是否被截断。

对应 SKILL.md「分块」一节的检查项：块集中在目标区间、重叠达到设定值、
无大量过小/超大块、结构化内容未被拦腰截断。

用法：
    python chunk_inspector.py chunks.json
    python chunk_inspector.py chunks.json --target 512 --overlap 0.15
    python chunk_inspector.py chunks.json --format json

输入：JSON 数组，或 {"chunks": [...]}，或 JSONL 逐行一条。
每条建议字段：id / content / source / title_path / token_count（可选，缺省按字符估算）

退出码：0 正常；2 读取或参数错误。
"""

import argparse
import json
import os
import re
import sys

FENCE_RE = re.compile(r"^\s*```")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\S")
OPEN_TAG_RE = re.compile(r"<([a-zA-Z_][\w.-]*)(?:\s[^>]*)?>")
CLOSE_TAG_RE = re.compile(r"</([a-zA-Z_][\w.-]*)\s*>")
INDENT_RE = re.compile(r"^\s{2,}\S")


def load_chunks(path):
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.lstrip()
    if stripped.startswith("["):
        return json.loads(content)
    if stripped.startswith("{"):
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("chunks", "documents", "items"):
                if key in data:
                    return data[key]
            return [data]
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def text_of(chunk):
    value = chunk.get("content") if isinstance(chunk, dict) else chunk
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def estimate_tokens(text):
    """粗略估算：中文按 1 字 ≈ 1 token，其余按 4 字符 ≈ 1 token。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return int(cjk + other / 4.0)


def size_of(chunk, text):
    explicit = chunk.get("token_count") if isinstance(chunk, dict) else None
    if isinstance(explicit, (int, float)) and explicit > 0:
        return int(explicit)
    return estimate_tokens(text)


def percentile(values, ratio):
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return ordered[index]


def detect_breaks(text):
    """检测结构边界被截断的情况，返回问题列表。"""
    issues = []
    lines = text.splitlines()
    if not lines:
        return issues

    fences = sum(1 for line in lines if FENCE_RE.match(line))
    if fences % 2 == 1:
        issues.append("代码块围栏未闭合（``` 数量为奇数）")

    open_tags = [tag for tag in OPEN_TAG_RE.findall(text)]
    close_tags = [tag for tag in CLOSE_TAG_RE.findall(text)]
    remainder = list(open_tags)
    for tag in close_tags:
        if tag in remainder:
            remainder.remove(tag)
    if remainder:
        issues.append("XML/HTML 标签未闭合：" + "、".join(sorted(set(remainder))[:5]))

    table_rows = [i for i, line in enumerate(lines) if TABLE_ROW_RE.match(line)]
    if table_rows and lines[0].strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[1] if len(lines) > 1 else ""):
        issues.append("表格缺表头分隔行（块中部开始的表格）")

    if LIST_RE.match(lines[0]) and len(lines) == 1:
        issues.append("列表被切成单条")

    if INDENT_RE.match(lines[0]) and HEADING_RE.match(lines[0]) is None:
        issues.append("以缩进开始，可能从代码块中部截断")

    if HEADING_RE.match(lines[-1]):
        issues.append("以标题结尾，其后内容被切到下一块")
    return issues


def overlap_ratio(prev_text, cur_text):
    """用尾部与头部的公共前缀长度估算重叠比例。"""
    if not prev_text or not cur_text:
        return 0.0
    max_len = min(len(prev_text), len(cur_text))
    best = 0
    limit = min(max_len, 400)
    for length in range(limit, 8, -1):
        if prev_text[-length:] == cur_text[:length]:
            best = length
            break
    return best / float(len(cur_text)) if cur_text else 0.0


def inspect(chunks, target, expected_overlap):
    sizes = []
    items = []
    prev_text = ""
    prev_source = None
    overlaps = []
    breaks = []

    for index, chunk in enumerate(chunks, start=1):
        text = text_of(chunk)
        size = size_of(chunk, text)
        sizes.append(size)
        source = chunk.get("source") if isinstance(chunk, dict) else None
        if source is not None and source == prev_source:
            overlaps.append(overlap_ratio(prev_text, text))
        found = detect_breaks(text)
        if found:
            breaks.append((index, found))
        items.append((index, size, text))
        prev_text = text
        prev_source = source

    small = [s for s in sizes if s < target * 0.5]
    large = [s for s in sizes if s > target * 1.5]
    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

    issues = []
    if small:
        issues.append(("WARN", "SMALL_CHUNK", "%d 个块小于目标的一半（< %d token），可能信息不完整"
                       % (len(small), int(target * 0.5))))
    if large:
        issues.append(("WARN", "LARGE_CHUNK", "%d 个块超过目标的 1.5 倍（> %d token），可能混杂多主题"
                       % (len(large), int(target * 1.5))))
    if overlaps and avg_overlap + 0.02 < expected_overlap:
        issues.append(("WARN", "LOW_OVERLAP", "实际平均重叠 %.1f%% 低于设定 %.1f%%，关键句可能在边界被切断"
                       % (avg_overlap * 100, expected_overlap * 100)))
    if overlaps and avg_overlap > expected_overlap * 2.5 + 0.05:
        issues.append(("WARN", "HIGH_OVERLAP", "实际平均重叠 %.1f%% 显著高于设定 %.1f%%，冗余与存储成本上升"
                       % (avg_overlap * 100, expected_overlap * 100)))
    for index, found in breaks[:10]:
        issues.append(("ERROR" if "未闭合" in found[0] else "WARN", "STRUCT_BREAK",
                       "第 %d 块疑似结构截断：%s" % (index, "；".join(found))))
    if len(breaks) > 10:
        issues.append(("WARN", "STRUCT_BREAK", "另有 %d 个块存在结构截断迹象" % (len(breaks) - 10)))

    stats = {
        "count": len(sizes),
        "min": min(sizes) if sizes else 0,
        "median": percentile(sizes, 0.5),
        "p95": percentile(sizes, 0.95),
        "max": max(sizes) if sizes else 0,
        "avg_overlap": avg_overlap,
        "small": len(small),
        "large": len(large),
        "breaks": len(breaks),
    }
    return stats, issues


def report(path, stats, issues, target, expected_overlap, as_json):
    if as_json:
        print(json.dumps({"file": path, "target": target, "expected_overlap": expected_overlap,
                          "stats": stats,
                          "issues": [{"level": lv, "code": code, "message": msg} for lv, code, msg in issues]},
                         ensure_ascii=False, indent=2))
        return
    print("=" * 62)
    print("分块质量体检：" + path)
    print("=" * 62)
    print("块数量：%d" % stats["count"])
    print("块大小（token）：最小 %d · 中位 %d · P95 %d · 最大 %d"
          % (stats["min"], stats["median"], stats["p95"], stats["max"]))
    print("目标区间：%d（过小 < %d，过大 > %d）"
          % (target, int(target * 0.5), int(target * 1.5)))
    print("实际平均重叠：%.1f%%（设定 %.1f%%）" % (stats["avg_overlap"] * 100, expected_overlap * 100))
    print("-" * 62)
    for level, code, message in issues:
        print("[%s] %-14s %s" % (level, code, message))
    if not issues:
        print("未发现问题")
    print("-" * 62)
    print("过小块 %d · 超大块 %d · 结构截断 %d" % (stats["small"], stats["large"], stats["breaks"]))
    print("结论：" + ("需修复后复检" if any(lv == "ERROR" for lv, _, _ in issues) else "通过"))


def main():
    parser = argparse.ArgumentParser(description="分块质量体检：大小分布、重叠率、结构截断")
    parser.add_argument("chunks", help="分块结果文件（JSON / JSONL）")
    parser.add_argument("--target", type=int, default=512, help="目标块大小（token，默认 512）")
    parser.add_argument("--overlap", type=float, default=0.15, help="设定重叠比例（默认 0.15）")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    if not os.path.exists(args.chunks):
        print("文件不存在：" + args.chunks)
        return 2
    try:
        chunks = load_chunks(args.chunks)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    stats, issues = inspect(chunks, args.target, args.overlap)
    report(args.chunks, stats, issues, args.target, args.overlap, args.format == "json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
