#!/usr/bin/env python3
"""多文档配额分配：把 Top-K 名额按文档相关性比例分配（纯算法、零 LLM 调用）。

解决的问题：直接按分数取 Top-K 会导致单文档霸屏；硬性「单文档最多 N 条」
在只有该文档相关时又导致信息不足；候选不足时名额被浪费。

流程：
    rel(d) = max(该文档候选片段分数)
    w_d    = rel(d)^alpha / Σ rel(d)^alpha
    q_d    = floor(K * w_d)，余位按小数部分降序 +1（最大余额法）
    钳制   q_d ∈ [QUOTA_MIN, QUOTA_MAX]
    取片段 每文档内按分数取前 q_d 条
    回填   候选不足的缺口按顺位分给其他文档

用法：
    python quota_allocator.py candidates.json --k 8
    python quota_allocator.py candidates.json --k 8 --alpha 0.5 --max-quota 3
    python quota_allocator.py candidates.json --k 8 --alpha-sweep
    python quota_allocator.py candidates.json --k 8 --format json

输入：JSON 数组 / JSONL，每条 {doc_id, chunk_id, score}
     也接受 {"candidates": [...]}；chunk_id 缺省时用下标。

退出码：0 正常；2 读取或参数错误。
"""

import argparse
import json
import os
import sys


def load_candidates(path):
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.lstrip()
    if stripped.startswith("["):
        return json.loads(content)
    if stripped.startswith("{"):
        data = json.loads(content)
        if isinstance(data, dict):
            for key in ("candidates", "chunks", "items"):
                if key in data:
                    return data[key]
            return [data]
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def group_by_doc(candidates):
    """按文档分组，组内按分数降序；返回 [(doc_id, [(chunk_id, score)])]。"""
    docs = {}
    for index, item in enumerate(candidates):
        doc_id = str(item.get("doc_id", "unknown"))
        chunk_id = item.get("chunk_id", "%s#%d" % (doc_id, index))
        score = float(item.get("score", 0.0))
        docs.setdefault(doc_id, []).append((chunk_id, score))
    for chunks in docs.values():
        chunks.sort(key=lambda pair: -pair[1])
    return list(docs.items())


def representative_scores(docs):
    """rel(d) = max(该文档候选片段分数)。"""
    return [max(score for _, score in chunks) for _, chunks in docs]


def shift_positive(values):
    """分数可能为负（余弦 -1~1），平移为正以保证 rel^alpha 有定义。"""
    lowest = min(values)
    if lowest > 0:
        return list(values)
    return [value - lowest + 1e-6 for value in values]


def weights(rels, alpha):
    """w_d = rel^alpha / Σ rel^alpha；alpha 为 0 时完全平均。"""
    if alpha == 0 or len(rels) == 1:
        share = 1.0 / len(rels)
        return [share] * len(rels)
    powered = [value ** alpha for value in rels]
    total = sum(powered)
    if total <= 0:
        share = 1.0 / len(rels)
        return [share] * len(rels)
    return [value / total for value in powered]


def largest_remainder(weights_list, k):
    """最大余额法（Hare-Niemeyer）：严格保证总额等于 k。"""
    raw = [w * k for w in weights_list]
    quotas = [int(value) for value in raw]
    remainders = [raw[i] - quotas[i] for i in range(len(raw))]
    left = k - sum(quotas)
    order = sorted(range(len(remainders)), key=lambda i: (-remainders[i], -weights_list[i]))
    for i in order[:left]:
        quotas[i] += 1
    return quotas, [remainders[i] for i in range(len(raw))]


def clamp(quotas, available, min_quota, max_quota, k, priority):
    """钳制到 [min, max] 与可用片段数，并把差额还给未受钳制的文档。

    缺口回填（total < k）优先给 priority（小数余额）最大的文档——即
    「最该拿到但未拿满」的文档；回收（total > k）时从配额最大者开始减。
    """
    quotas = list(quotas)
    upper_of = lambda i: available[i] if max_quota is None else min(available[i], max_quota)
    for i in range(len(quotas)):
        if quotas[i] < min_quota:
            quotas[i] = min(min_quota, available[i])
        if quotas[i] > upper_of(i):
            quotas[i] = upper_of(i)

    locked = set()
    for _ in range(len(quotas)):
        total = sum(quotas)
        if total == k:
            break
        if total < k:
            candidates = [i for i in range(len(quotas)) if i not in locked and quotas[i] < upper_of(i)]
            if not candidates:
                break
            candidates.sort(key=lambda i: (-priority[i], -quotas[i]))
            quotas[candidates[0]] += 1
        else:
            candidates = [i for i in range(len(quotas)) if i not in locked and quotas[i] > min_quota]
            if not candidates:
                break
            candidates.sort(key=lambda i: -quotas[i])
            quotas[candidates[0]] -= 1
            locked.add(candidates[0])
    return quotas


def allocate(docs, k, alpha, min_quota, max_quota):
    """返回 (每个文档的配额, 权重, 代表分数)。"""
    rels = representative_scores(docs)
    shifted = shift_positive(rels)
    weights_list = weights(shifted, alpha)
    available = [len(chunks) for _, chunks in docs]
    quotas, remainders = largest_remainder(weights_list, k)
    quotas = clamp(quotas, available, min_quota, max_quota, k, remainders)
    return quotas, weights_list, rels


def select(docs, quotas):
    picked = []
    for (doc_id, chunks), quota in zip(docs, quotas):
        for chunk_id, score in chunks[:quota]:
            picked.append({"doc_id": doc_id, "chunk_id": chunk_id, "score": score})
    picked.sort(key=lambda item: -item["score"])
    return picked


def summarize(docs, quotas, picked):
    covered = sum(1 for quota in quotas if quota > 0)
    counts = {}
    for item in picked:
        counts[item["doc_id"]] = counts.get(item["doc_id"], 0) + 1
    top_share = max(counts.values()) / float(len(picked)) if picked else 0.0
    return {
        "docs_total": len(docs),
        "docs_covered": covered,
        "selected": len(picked),
        "top_doc_share": round(top_share, 3),
    }


def report(path, docs, quotas, weights_list, rels, picked, k, alpha, as_json):
    stats = summarize(docs, quotas, picked)
    if as_json:
        print(json.dumps({
            "file": path, "k": k, "alpha": alpha,
            "quota": [{"doc_id": doc_id, "rel": round(rel, 4), "weight": round(w, 4),
                       "quota": q, "available": len(chunks)}
                      for (doc_id, chunks), rel, w, q in zip(docs, rels, weights_list, quotas)],
            "selected": picked, "stats": stats,
        }, ensure_ascii=False, indent=2))
        return

    print("=" * 70)
    print("多文档配额分配：" + path)
    print("=" * 70)
    print("K=%d，α=%.2f" % (k, alpha))
    print("-" * 70)
    print("%-14s %8s %8s %8s %8s" % ("文档", "代表分", "权重", "配额", "可用"))
    for (doc_id, chunks), rel, w, q in zip(docs, rels, weights_list, quotas):
        print("%-14s %8.4f %8.4f %8d %8d" % (doc_id[:14], rel, w, q, len(chunks)))
    print("-" * 70)
    print("最终入选 %d 条（文档覆盖 %d/%d，单文档最大占比 %.1f%%）："
          % (stats["selected"], stats["docs_covered"], stats["docs_total"], stats["top_doc_share"] * 100))
    for item in picked:
        print("  %-14s %-16s %.4f" % (item["doc_id"][:14], str(item["chunk_id"])[:16], item["score"]))
    print("-" * 70)
    print("总名额：%d / %d" % (stats["selected"], k))


def report_sweep(path, docs, k, min_quota, max_quota):
    print("=" * 70)
    print("α 敏感性扫描：" + path + "  K=%d" % k)
    print("=" * 70)
    print("%-8s %s" % ("α", "各文档配额"))
    for alpha in (0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 4.0):
        quotas, _, _ = allocate(docs, k, alpha, min_quota, max_quota)
        distribution = "  ".join("%s:%d" % (doc_id[:10], q) for (doc_id, _), q in zip(docs, quotas))
        print("%-8s %s" % (alpha, distribution))
    print("-" * 70)
    print("α=0 完全平均；α<1 向低分文档倾斜；α>1 向高分文档倾斜。")


def main():
    parser = argparse.ArgumentParser(description="多文档配额分配：按相关性比例分配 Top-K 名额")
    parser.add_argument("candidates", help="候选片段文件（JSON / JSONL）")
    parser.add_argument("--k", type=int, default=8, help="总名额数（默认 8）")
    parser.add_argument("--alpha", type=float, default=1.0, help="集中度旋钮（默认 1.0）")
    parser.add_argument("--min-quota", type=int, default=1, help="每文档最低配额（默认 1）")
    parser.add_argument("--max-quota", type=int, default=None, help="每文档最高配额（默认不限）")
    parser.add_argument("--alpha-sweep", action="store_true", help="扫描不同 α 下的分配结果")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    if not os.path.exists(args.candidates):
        print("文件不存在：" + args.candidates)
        return 2
    try:
        candidates = load_candidates(args.candidates)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    docs = group_by_doc(candidates)
    if args.alpha_sweep:
        report_sweep(args.candidates, docs, args.k, args.min_quota, args.max_quota)
        return 0

    quotas, weights_list, rels = allocate(docs, args.k, args.alpha, args.min_quota, args.max_quota)
    picked = select(docs, quotas)
    report(args.candidates, docs, quotas, weights_list, rels, picked, args.k, args.alpha,
           args.format == "json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
