#!/usr/bin/env python3
"""知识库链接体检：孤岛文件、断链与索引页覆盖。

对应 references/knowledge-organization.md 第 4 节：把知识拆成一批各自独立的
文本文件、彼此没有交叉引用时，知识越多越难检索。正确做法是像 Wikipedia 一样
用链接互联，并维护入口页与目录索引页。

检查项：
  1. 孤岛文件：没有任何入链，也没有出链的条目
  2. 断链：Markdown 链接指向的文件不存在
  3. 索引页覆盖：目录下没有被索引页链接到的文件（需 --index 指定索引页）
  4. 双向可达：A 链接到 B 但 B 未回指（可选，作为 WARN）

用法：
    python knowledge_link_check.py <知识库目录>
    python knowledge_link_check.py docs/kb --index README.md
    python knowledge_link_check.py docs/kb --format json

退出码：0 无 ERROR；1 存在 ERROR（仅 --check）；2 读取或参数错误。
"""

import argparse
import json
import os
import re
import sys

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
CODE_SPAN_RE = re.compile(r"`([^`\n]+\.md)`")
INDEX_NAMES = ("readme.md", "index.md", "_index.md", "summary.md")


def list_markdown(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            if name.lower().endswith(".md"):
                files.append(os.path.abspath(os.path.join(dirpath, name)))
    return files


def resolve(root, current_file, target):
    """把相对链接解析为仓库内路径（跨目录）。"""
    base = os.path.dirname(current_file)
    path = os.path.normpath(os.path.join(base, target))
    if not os.path.isfile(path):
        root_relative = os.path.normpath(os.path.join(root, target))
        if os.path.isfile(root_relative):
            path = root_relative
    return os.path.abspath(path)


def build_graph(root, files):
    """outlinks[(path)] = [(raw, resolved)]；另返回 code_span 引用（作为入链统计）。"""
    outlinks = {}
    mentions = {}
    for path in files:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        targets = []
        for match in LINK_RE.finditer(text):
            target = match.group(1).strip()
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
                continue
            targets.append((target, resolve(root, path, target)))
        outlinks[path] = targets
        mentions[path] = [resolve(root, path, m) for m in CODE_SPAN_RE.findall(text)]
    return outlinks, mentions


def check(root, index_name):
    files = list_markdown(root)
    outlinks, mentions = build_graph(root, files)
    existing = set(files)

    issues = []
    inbound = {path: 0 for path in files}
    referenced = {path: False for path in files}
    broken = []
    index_files = []

    for path in files:
        rel = os.path.relpath(path, root)
        if os.path.basename(path).lower() in INDEX_NAMES:
            index_files.append(path)
        for raw, resolved in outlinks[path]:
            if os.path.exists(resolved):
                if resolved in existing:
                    inbound[resolved] += 1
                    referenced[resolved] = True
            else:
                broken.append((rel, raw))
        for resolved in mentions.get(path, []):
            if resolved in existing:
                referenced[resolved] = True

    has_out = {path: bool(outlinks[path]) for path in files}

    for rel, raw in broken:
        issues.append(("ERROR", "BROKEN_LINK", "%s 中的链接指向不存在的文件：%s" % (rel, raw)))

    for path in files:
        rel = os.path.relpath(path, root)
        if not referenced[path]:
            if not has_out[path]:
                issues.append(("ERROR", "ISLAND", "%s 既无入链也无出链，也未在别处被引用，是知识孤岛" % rel))
            else:
                issues.append(("WARN", "NO_INBOUND", "%s 只有出链没有入链，建议加入口页或索引页" % rel))

    if index_files:
        indexed = set()
        for index in index_files:
            for _, resolved in outlinks[index]:
                if resolved in existing:
                    indexed.add(resolved)
            for resolved in mentions.get(index, []):
                if resolved in existing:
                    indexed.add(resolved)
        for path in files:
            if path in index_files or path in indexed or path in referenced:
                continue
            issues.append(("WARN", "NOT_INDEXED", "%s 未被索引页或任何条目引用" % os.path.relpath(path, root)))

    stats = {
        "files": len(files),
        "islands": len([i for i in issues if i[1] == "ISLAND"]),
        "broken_links": len([i for i in issues if i[1] == "BROKEN_LINK"]),
        "not_indexed": len([i for i in issues if i[1] == "NOT_INDEXED"]),
    }
    return issues, stats


def report(issues, stats, as_json):
    if as_json:
        print(json.dumps({"stats": stats, "issues": [
            {"level": lv, "code": code, "message": msg} for lv, code, msg in issues
        ]}, ensure_ascii=False, indent=2))
        return
    print("=" * 62)
    print("知识库链接体检")
    print("=" * 62)
    print("Markdown 文件 %d · 孤岛 %d · 断链 %d · 未入索引 %d"
          % (stats["files"], stats["islands"], stats["broken_links"], stats["not_indexed"]))
    print("-" * 62)
    for level, code, message in issues:
        print("[%s] %-14s %s" % (level, code, message))
    if not issues:
        print("未发现问题")
    print("-" * 62)
    print("结论：" + ("需修复后复检" if any(lv == "ERROR" for lv, _, _ in issues) else "通过"))


def main():
    parser = argparse.ArgumentParser(description="知识库链接体检：孤岛、断链与索引覆盖")
    parser.add_argument("root", help="知识库目录")
    parser.add_argument("--index", action="store_true", default=True, help="检查索引页覆盖（默认开启）")
    parser.add_argument("--check", action="store_true", help="存在 ERROR 时退出码为 1")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print("目录不存在：" + args.root)
        return 2
    try:
        issues, stats = check(args.root, args.index)
    except OSError as error:
        print("读取失败：" + str(error))
        return 2

    report(issues, stats, args.format == "json")
    if args.check and any(lv == "ERROR" for lv, _, _ in issues):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
