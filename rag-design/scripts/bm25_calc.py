#!/usr/bin/env python3
"""BM25 手算与参数校验：输出分词、IDF、各词贡献与最终排序。

教学与校验用途：完整展示 BM25 的内部计算过程，便于与实现对照、验证参数
（k1、b、IDF 下限）是否符合预期。中文按二元组切分，英文按词切分。

用法：
    python bm25_calc.py corpus.json --query "模型 蒸馏"
    python bm25_calc.py corpus.json --query "退款流程" --k1 1.5 --b 0.75
    python bm25_calc.py corpus.json --query "模型 蒸馏" --idf-floor 0.0 --format json

输入：JSON 数组（每条含 id 与 content），或 {"documents": [...]}，或 JSONL。

退出码：0 正常；2 读取或参数错误。
"""

import argparse
import json
import math
import os
import re
import sys

STOPWORDS = set("的了是在和与及为对於于把被给从向而或这那你我他她它们个之其".strip())


def load_documents(path):
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.lstrip()
    if stripped.startswith("["):
        return json.loads(content)
    if stripped.startswith("{"):
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("documents", "docs", "items"):
                if key in data:
                    return data[key]
            return [data]
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def text_of(doc):
    value = doc.get("content") if isinstance(doc, dict) else doc
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def tokenize(text):
    """中文按二元组切分，英文与数字按词切分；过滤停用词。"""
    text = str(text).lower()
    words = [w for w in re.findall(r"[a-z0-9_]+", text) if len(w) > 1 and w not in STOPWORDS]
    bigrams = []
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) == 1:
            bigrams.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            pair = chunk[i:i + 2]
            if not (pair[0] in STOPWORDS or pair[1] in STOPWORDS):
                bigrams.append(pair)
    return words + bigrams


def build_index(documents):
    """构建倒排索引：词 → [(文档下标, 词频)]，并记录文档长度。"""
    index = {}
    lengths = []
    for position, doc in enumerate(documents):
        tokens = tokenize(text_of(doc))
        lengths.append(len(tokens))
        counts = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            index.setdefault(token, []).append((position, count))
    return index, lengths


def idf(total, df, floor):
    value = math.log((total - df + 0.5) / (df + 0.5))
    return max(value, floor)


def contribution(tf, length, avgdl, k1, b):
    """BM25 中单个词的贡献（不含 IDF）。"""
    denom = tf + k1 * (1 - b + b * length / avgdl)
    return tf * (k1 + 1) / denom if denom else 0.0


def search(documents, query, k1, b, floor, top_k):
    index, lengths = build_index(documents)
    total = len(documents)
    avgdl = sum(lengths) / float(total) if total else 0.0
    terms = tokenize(query)
    scores = {}
    trace = []

    for term in terms:
        postings = index.get(term)
        if not postings:
            trace.append({"term": term, "df": 0, "idf": 0.0, "hits": []})
            continue
        df = len(postings)
        idf_value = idf(total, df, floor)
        hits = []
        for position, tf in postings:
            length = lengths[position] or avgdl or 1
            part = contribution(tf, length, avgdl, k1, b)
            value = idf_value * part
            scores[position] = scores.get(position, 0.0) + value
            hits.append({"doc": position, "tf": tf, "length": length, "contribution": round(value, 4)})
        trace.append({"term": term, "df": df, "idf": round(idf_value, 4), "hits": hits})

    ranked = sorted(scores.items(), key=lambda item: -item[1])
    if top_k:
        ranked = ranked[:top_k]
    return {
        "total_docs": total,
        "avgdl": round(avgdl, 1),
        "terms": trace,
        "ranking": [{"doc": pos, "score": round(score, 4)} for pos, score in ranked],
    }


def report(path, documents, result, as_json, show_hits):
    if as_json:
        print(json.dumps({"file": path, "result": result}, ensure_ascii=False, indent=2))
        return
    print("=" * 62)
    print("BM25 计算过程：" + path)
    print("=" * 62)
    print("文档总数 N=%d，平均文档长度 avgdl=%.1f 词" % (result["total_docs"], result["avgdl"]))
    print("-" * 62)
    for item in result["terms"]:
        if item["df"] == 0:
            print("词 「%s」 → 倒排索引未命中" % item["term"])
            continue
        print("词 「%s」 → 命中 %d 篇，IDF=%.4f" % (item["term"], item["df"], item["idf"]))
        if show_hits:
            for hit in item["hits"]:
                print("    doc_%d: TF=%d, 长度=%d, 贡献=%.4f"
                      % (hit["doc"] + 1, hit["tf"], hit["length"], hit["contribution"]))
    print("-" * 62)
    print("最终排序：")
    for row in result["ranking"]:
        doc_id = documents[row["doc"]].get("id") if isinstance(documents[row["doc"]], dict) else None
        label = doc_id if doc_id else "doc_%d" % (row["doc"] + 1)
        preview = text_of(documents[row["doc"]])[:30]
        print("  %-14s %.4f   %s" % (label, row["score"], preview))
    print("-" * 62)
    print("提示：IDF 在 DF > N/2 时会变为负值，实现中需设下限（--idf-floor）。")


def main():
    parser = argparse.ArgumentParser(description="BM25 手算与参数校验")
    parser.add_argument("corpus", help="语料文件（JSON / JSONL）")
    parser.add_argument("--query", required=True, help="查询文本")
    parser.add_argument("--k1", type=float, default=1.5, help="词频饱和参数（默认 1.5）")
    parser.add_argument("--b", type=float, default=0.75, help="长度归一化参数（默认 0.75）")
    parser.add_argument("--idf-floor", type=float, default=0.0,
                        help="IDF 下限（默认 0.0；DF > N/2 时 IDF 为负，必须设下限）")
    parser.add_argument("--top-k", type=int, default=10, help="返回条数（默认 10）")
    parser.add_argument("--show-hits", action="store_true", help="打印每个词的命中明细")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    if not os.path.exists(args.corpus):
        print("文件不存在：" + args.corpus)
        return 2
    try:
        documents = load_documents(args.corpus)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    result = search(documents, args.query, args.k1, args.b, args.idf_floor, args.top_k)
    report(args.corpus, documents, result, args.format == "json", args.show_hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
