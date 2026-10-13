#!/usr/bin/env python3
"""检索质量评估：recall@k、MRR、nDCG@k、检索失败率，并支持 RRF 融合多路结果。

对应 SKILL.md 第 1 步与「混合检索」一节：以 recall@k 为主指标（最贴近
RAG 需求），MRR 与 nDCG 作为排序质量补充，失败率用于监控长尾。

用法：
    python retrieval_eval.py annotations.json --k 10
    python retrieval_eval.py annotations.json --k 10 --compare fused.json
    python retrieval_eval.py annotations.json --k 10 --format json

输入：JSON 数组，或 {"queries": [...]}，或 JSONL。每条：
    {"query": "...", "type": "语义相似", "relevant": ["doc_1", ...],
     "grades": {"doc_1": 2},  "dense": [...], "sparse": [...], "fused": [...]}

- relevant：相关文档 ID 列表（必需）
- grades：可选的相关度分级，用于 nDCG；缺省相关文档一律记 2 分
- dense / sparse / fused：各路的检索结果 ID 列表（按排名顺序）；
  同时给出 dense 与 sparse 时会自动用 RRF 融合并与 fused 对照

退出码：0 正常；2 读取或参数错误。
"""

import argparse
import json
import math
import os
import sys


def load_records(path):
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.lstrip()
    if stripped.startswith("["):
        return json.loads(content)
    if stripped.startswith("{"):
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("queries", "annotations", "items"):
                if key in data:
                    return data[key]
            return [data]
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def rrf_fuse(rankings, k=60):
    """倒数排名融合：score = Σ 1/(k + rank)，rank 从 1 开始。"""
    scores = {}
    for ranking in rankings:
        for position, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: -item[1])]


def resolve_ranking(record, rrf_k):
    """优先用给定的 fused；否则由 dense 与 sparse 用 RRF 融合。"""
    if record.get("fused") is not None:
        return list(record["fused"])
    rankings = []
    for key in ("dense", "sparse"):
        if record.get(key):
            rankings.append(list(record[key]))
    return rrf_fuse(rankings, rrf_k) if rankings else []


def recall_at_k(ranking, relevant, k):
    top = set(ranking[:k])
    return 1.0 if top & set(relevant) else 0.0


def reciprocal_rank(ranking, relevant):
    wanted = set(relevant)
    for position, doc_id in enumerate(ranking, start=1):
        if doc_id in wanted:
            return 1.0 / position
    return 0.0


def dcg(grades):
    total = 0.0
    for position, grade in enumerate(grades, start=1):
        total += grade / (1.0 if position == 1 else math.log2(position + 1))
    return total


def ndcg_at_k(ranking, grades, k):
    actual = [grades.get(doc_id, 0) for doc_id in ranking[:k]]
    ideal = sorted(grades.values(), reverse=True)[:k]
    best = dcg(ideal)
    if best <= 0:
        return 0.0
    return dcg(actual) / best


def evaluate(records, k, rrf_k=60):
    per_type = {}
    totals = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0, "failure": 0.0}
    count = 0

    for record in records:
        relevant = list(record.get("relevant") or [])
        grades = record.get("grades") or {doc_id: 2 for doc_id in relevant}
        ranking = resolve_ranking(record, rrf_k)

        recall = recall_at_k(ranking, relevant, k)
        mrr = reciprocal_rank(ranking, relevant)
        ndcg = ndcg_at_k(ranking, grades, k)
        failure = 1.0 if recall_at_k(ranking, relevant, 20) == 0.0 else 0.0

        totals["recall"] += recall
        totals["mrr"] += mrr
        totals["ndcg"] += ndcg
        totals["failure"] += failure
        count += 1

        bucket = per_type.setdefault(record.get("type") or "未分类",
                                     {"count": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0})
        bucket["count"] += 1
        bucket["recall"] += recall
        bucket["mrr"] += mrr
        bucket["ndcg"] += ndcg

    if count == 0:
        return None, None
    overall = {
        "queries": count,
        "recall@k": totals["recall"] / count,
        "MRR": totals["mrr"] / count,
        "nDCG@k": totals["ndcg"] / count,
        "failure@20": totals["failure"] / count,
    }
    by_type = []
    for name, bucket in sorted(per_type.items()):
        n = bucket["count"]
        by_type.append({
            "type": name,
            "queries": n,
            "recall@k": bucket["recall"] / n,
            "MRR": bucket["mrr"] / n,
            "nDCG@k": bucket["ndcg"] / n,
        })
    return overall, by_type


def report(path, k, overall, by_type, as_json):
    if as_json:
        print(json.dumps({"file": path, "k": k, "overall": overall, "by_type": by_type},
                         ensure_ascii=False, indent=2))
        return
    print("=" * 62)
    print("检索质量评估：" + path)
    print("=" * 62)
    if overall is None:
        print("未解析到任何查询记录")
        return
    print("查询数：%d，k=%d" % (overall["queries"], k))
    print("-" * 62)
    print("recall@%d   %.3f    （主指标：该找的找到了吗）" % (k, overall["recall@k"]))
    print("MRR         %.3f    （找到得够不够靠前）" % overall["MRR"])
    print("nDCG@%d     %.3f    （整个排序列表质量）" % (k, overall["nDCG@k"]))
    print("失败率@20   %.3f    （top-20 未命中比例）" % overall["failure@20"])
    if by_type:
        print("-" * 62)
        print("按查询类型拆解：")
        print("  %-10s %6s %8s %8s %8s" % ("类型", "查询数", "recall", "MRR", "nDCG"))
        for row in by_type:
            print("  %-10s %6d %8.3f %8.3f %8.3f"
                  % (row["type"], row["queries"], row["recall@k"], row["MRR"], row["nDCG@k"]))
    print("-" * 62)
    print("提示：recall@k 是主指标；某类型明显偏低时，定位是稠密路还是稀疏路的问题。")


def main():
    parser = argparse.ArgumentParser(description="检索质量评估：recall@k / MRR / nDCG / 失败率")
    parser.add_argument("annotations", help="标注查询集文件（JSON / JSONL）")
    parser.add_argument("--k", type=int, default=10, help="recall 与 nDCG 的 k 值（默认 10）")
    parser.add_argument("--rrf-k", type=int, default=60, help="RRF 平滑常数（默认 60）")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    if not os.path.exists(args.annotations):
        print("文件不存在：" + args.annotations)
        return 2
    try:
        records = load_records(args.annotations)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    overall, by_type = evaluate(records, args.k, args.rrf_k)
    report(args.annotations, args.k, overall, by_type, args.format == "json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
