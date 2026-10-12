#!/usr/bin/env python3
"""记忆重要性评分：按四因素打分，给出压缩与淘汰建议。

四因素（见 references/compression-privacy.md）：
    访问频率  正向  经常被检索的记忆更重要
    时间衰减  负向  越久远的记忆越容易被遗忘
    情感强度  正向  带有强烈情感标记的记忆更易保留
    信息独特性 正向 重复信息的重要性降低

评分：score = w_freq*n(freq) + w_recency*exp(-age/half_life) + w_emo*emotion + w_uniq*(1/(1+dup))
低于阈值的记忆标记为可压缩或删除；protected 的记忆永不淘汰。

用法：
    python importance_scoring.py memories.json
    python importance_scoring.py memories.json --threshold 0.3 --half-life 60
    python importance_scoring.py memories.json --top 20 --format csv
    python importance_scoring.py memories.json --format json

输入字段：id / access_count / created_at / emotion(0-1) / duplicates / protected / content

退出码：0 正常；2 读取或参数错误。
"""

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone


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


def parse_time(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def norm_freq(count, cap):
    """访问频率归一化，达到 cap 后饱和。"""
    if cap <= 0:
        return 0.0
    return min(1.0, float(count or 0) / cap)


def recency_score(created_at, now, half_life):
    """时间衰减：exp(-age/half_life)，半衰期后衰减到 0.5。"""
    if created_at is None or half_life <= 0:
        return 0.5
    age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    return math.exp(-age_days / float(half_life))


def uniqueness_score(dup):
    """独特性：重复越多分越低。"""
    return 1.0 / (1.0 + float(dup or 0))


def score_one(memory, now, weights, freq_cap, half_life):
    created_at = parse_time(memory.get("created_at"))
    freq = norm_freq(memory.get("access_count", 0), freq_cap)
    recency = recency_score(created_at, now, half_life)
    emotion = float(memory.get("emotion", 0) or 0)
    emotion = min(1.0, max(0.0, emotion))
    uniqueness = uniqueness_score(memory.get("duplicates", 0))
    total = (weights[0] * freq + weights[1] * recency
             + weights[2] * emotion + weights[3] * uniqueness)
    weight_sum = sum(weights) or 1.0
    return min(1.0, total / weight_sum), {
        "freq": round(freq, 3),
        "recency": round(recency, 3),
        "emotion": round(emotion, 3),
        "uniqueness": round(uniqueness, 3),
    }


def decide(score, protected, threshold, compress_margin):
    if protected:
        return "保留（受保护）"
    if score < threshold:
        return "可删除"
    if score < threshold + compress_margin:
        return "可压缩"
    return "保留"


def build_rows(memories, now, weights, freq_cap, half_life, threshold, compress_margin):
    rows = []
    for index, memory in enumerate(memories, start=1):
        score, parts = score_one(memory, now, weights, freq_cap, half_life)
        protected = bool(memory.get("protected"))
        rows.append({
            "index": index,
            "id": memory.get("id", ""),
            "score": round(score, 4),
            "freq": parts["freq"],
            "recency": parts["recency"],
            "emotion": parts["emotion"],
            "uniqueness": parts["uniqueness"],
            "protected": protected,
            "action": decide(score, protected, threshold, compress_margin),
            "preview": str(memory.get("content", ""))[:40],
        })
    rows.sort(key=lambda r: r["score"])
    return rows


def report(path, rows, threshold, as_json, as_csv, top):
    counts = {"可删除": 0, "可压缩": 0, "保留": 0, "保留（受保护）": 0}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1

    shown = rows[:top] if top else rows

    if as_json:
        print(json.dumps({"file": path, "threshold": threshold,
                          "counts": counts, "rows": shown}, ensure_ascii=False, indent=2))
        return counts

    if as_csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(["index", "id", "score", "freq", "recency", "emotion",
                         "uniqueness", "protected", "action", "preview"])
        for row in shown:
            writer.writerow([row["index"], row["id"], row["score"], row["freq"],
                             row["recency"], row["emotion"], row["uniqueness"],
                             row["protected"], row["action"], row["preview"]])
        return counts

    print("=" * 74)
    print("记忆重要性评分：" + path)
    print("=" * 74)
    print("阈值 %.2f（低于即建议删除，+%.2f 区间内建议压缩）" % (threshold, 0.15))
    print("-" * 74)
    print("序号  记忆ID            得分     频率  时效  情感  独特  建议        内容预览")
    for row in shown:
        print("%-5s %-16s %-8s %-5s %-5s %-5s %-5s %-11s %s"
              % (row["index"], row["id"][:16], row["score"], row["freq"], row["recency"],
                 row["emotion"], row["uniqueness"], row["action"], row["preview"]))
    print("-" * 74)
    print("统计：可删除 %d · 可压缩 %d · 保留 %d · 受保护保留 %d"
          % (counts["可删除"], counts["可压缩"], counts["保留"], counts["保留（受保护）"]))
    print("提示：受保护记忆（证件、过敏、禁忌）不参与淘汰；删除前确认备份与索引清理覆盖。")
    return counts


def main():
    parser = argparse.ArgumentParser(description="记忆重要性评分与压缩淘汰建议")
    parser.add_argument("memories", help="记忆库文件（JSON / JSONL）")
    parser.add_argument("--threshold", type=float, default=0.30, help="删除阈值（默认 0.30）")
    parser.add_argument("--compress-margin", type=float, default=0.15, help="可压缩区间宽度（默认 0.15）")
    parser.add_argument("--half-life", type=float, default=60.0, help="时间衰减半衰期（天，默认 60）")
    parser.add_argument("--freq-cap", type=float, default=10.0, help="访问频率饱和值（默认 10）")
    parser.add_argument("--w-freq", type=float, default=1.0, help="访问频率权重")
    parser.add_argument("--w-recency", type=float, default=1.0, help="时间衰减权重")
    parser.add_argument("--w-emotion", type=float, default=0.8, help="情感强度权重")
    parser.add_argument("--w-uniqueness", type=float, default=0.8, help="信息独特性权重")
    parser.add_argument("--top", type=int, default=0, help="只显示得分最低的 N 条（0 为全部）")
    parser.add_argument("--format", choices=["text", "json", "csv"], default="text", help="输出格式")
    args = parser.parse_args()

    try:
        memories = load_memories(args.memories)
    except (OSError, json.JSONDecodeError) as error:
        print("读取失败：" + str(error))
        return 2

    weights = (args.w_freq, args.w_recency, args.w_emotion, args.w_uniqueness)
    now = datetime.now(timezone.utc)
    rows = build_rows(memories, now, weights, args.freq_cap, args.half_life,
                      args.threshold, args.compress_margin)
    report(args.memories, rows, args.threshold, args.format == "json",
           args.format == "csv", args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
