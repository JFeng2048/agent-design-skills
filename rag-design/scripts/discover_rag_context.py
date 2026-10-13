#!/usr/bin/env python3
"""从项目里勘察 RAG 管线设计所需的事实，并给出默认方案。

目的：设计所需的信息尽量从项目里读到，而不是抛一堆问题给用户。
输出「事实 + 证据（文件:行）」与「推断的默认方案」，供填写
assets/requirements_intake.md 使用。区分代码命中与文档提及。

用法：
    python discover_rag_context.py <项目目录>
    python discover_rag_context.py <项目目录> --json
    python discover_rag_context.py <项目目录> --exclude scripts/discover_rag_context.py

退出码：0 正常；2 路径或读取错误。
"""

import argparse
import json
import os
import re
import sys

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea", ".vscode"}
SOURCE_EXT = {".py", ".ts", ".js", ".go", ".java", ".json", ".yaml", ".yml", ".toml", ".md", ".txt"}

PATTERNS = {
    "vector_store": (r"chroma|faiss|qdrant|milvus|weaviate|pgvector|pinecone|annoy|hnsw|VectorStore", "向量库与索引"),
    "embedding": (r"embedding_model|SentenceTransformer|bge-|text-embedding|embed_query|embed_documents|OpenAIEmbeddings", "嵌入模型"),
    "chunking": (r"chunk_size|chunk_overlap|RecursiveCharacterTextSplitter|split_text|split_documents|分块", "分块配置"),
    "sparse": (r"bm25|BM25|tfidf|TfidfVectorizer|inverted_index|rank_bm25", "稀疏检索"),
    "rerank": (r"rerank|cross_encoder|CrossEncoder|bge-reranker|cohere.rerank", "重排序"),
    "retrieval": (r"similarity_search|retrieve|top_k|retriever|as_retriever", "检索入口"),
    "metric": (r"cosine|inner_product|l2|metric\s*=|score_threshold", "相似度度量"),
    "llm": (r"model\s*=|claude-|gpt-|ChatOpenAI|llm\.generate", "生成端模型"),
}


def iter_source_files(root, excludes=()):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if any(token in path for token in excludes):
                continue
            if os.path.splitext(name)[1].lower() in SOURCE_EXT:
                yield path


def scan(root, excludes=()):
    hits = {key: {"code": [], "doc": []} for key in PATTERNS}
    for path in iter_source_files(root, excludes):
        is_doc = os.path.splitext(path)[1].lower() in (".md", ".txt")
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        if len(lines) > 4000:
            continue
        for no, line in enumerate(lines, start=1):
            for key, (pattern, _) in PATTERNS.items():
                if re.search(pattern, line, re.I):
                    rel = os.path.relpath(path, root)
                    hits[key]["doc" if is_doc else "code"].append(("%s:%d" % (rel, no), line.strip()[:120]))
    return hits


def has_code(hits, key):
    return bool(hits[key]["code"])


def recommend(hits):
    plan = []

    if has_code(hits, "vector_store"):
        joined = " ".join(line for _, line in hits["vector_store"]["code"]).lower()
        kind = "HNSW 类（支持增量）" if "hnsw" in joined else ("ANNOY 类（静态）" if "annoy" in joined else "已配置向量库")
        plan.append(("向量库与索引", "已有实现（%s），沿用；确认索引类型与数据更新频率是否匹配"
                     % hits["vector_store"]["code"][0][0]) + ("；识别为 %s" % kind if kind else ""))
    else:
        plan.append(("向量库与索引", "未发现向量库，需选型：静态数据用 ANNOY，需实时写入用 HNSW"))

    if has_code(hits, "embedding"):
        plan.append(("嵌入模型", "已配置（%s），复核维度、语言支持与最大输入长度"
                     % hits["embedding"]["code"][0][0]))
    else:
        plan.append(("嵌入模型", "未发现嵌入模型配置，需选型（上下文感知的句向量模型，注意长度上限）"))

    if has_code(hits, "chunking"):
        joined = " ".join(line for _, line in hits["chunking"]["code"])
        size = re.search(r"chunk_size\s*=\s*(\d+)", joined)
        overlap = re.search(r"chunk_overlap\s*=\s*(\d+)", joined)
        detail = "块大小=%s，重叠=%s" % (size.group(1) if size else "未显式设置",
                                    overlap.group(1) if overlap else "未显式设置")
        plan.append(("分块", "已有配置（%s）；%s，对照 256-1024 token / 重叠 10-20%% 检查"
                     % (hits["chunking"]["code"][0][0], detail)))
    else:
        plan.append(("分块", "未发现分块配置，默认递归/结构感知切分，块 256-1024 token、重叠 10-20%"))

    if has_code(hits, "sparse"):
        plan.append(("稀疏检索", "已有 BM25/TF-IDF 实现，复核 k1、b 与 IDF 下限"))
    else:
        plan.append(("稀疏检索", "未发现稀疏检索，需补 BM25（精确名称与技术代码类查询的短板）"))

    if has_code(hits, "rerank"):
        plan.append(("重排序", "已引入重排序，确认精排范围（Top-N）与超时降级策略"))
    else:
        plan.append(("重排序", "未发现重排序，建议对融合后 Top-50 加跨编码器精排（收益通常最直接）"))

    dense_and_sparse = has_code(hits, "embedding") and has_code(hits, "sparse")
    plan.append(("融合", "两路并存，默认用 RRF（k=60）按排名融合，不要直接相加原始得分"
                 if dense_and_sparse else "目前为单路检索，补上另一路后再做 RRF 融合"))

    plan.append(("度量", "建 30-50 条标注查询，以 recall@k 为主指标，用 retrieval_eval.py 基线化"))
    return plan


def report(root, hits, plan, as_json):
    if as_json:
        print(json.dumps({
            "root": root,
            "hits": {k: {"code": v["code"][:5], "doc": v["doc"][:5]} for k, v in hits.items()},
            "recommended_plan": [{"item": k, "value": v} for k, v in plan],
        }, ensure_ascii=False, indent=2))
        return
    print("=" * 62)
    print("RAG 项目调研：" + root)
    print("=" * 62)
    for key, (_, label) in PATTERNS.items():
        code_hits = hits[key]["code"]
        doc_hits = hits[key]["doc"]
        print("-" * 62)
        if code_hits:
            print("%s：代码中 %d 处" % (label, len(code_hits)))
            for where, line in code_hits[:3]:
                print("  [代码] %s  %s" % (where, line))
        elif doc_hits:
            print("%s：仅在文档中提及 %d 处（不构成实现证据）" % (label, len(doc_hits)))
            for where, line in doc_hits[:2]:
                print("  [文档] %s  %s" % (where, line))
        else:
            print("%s：未发现" % label)
    print("-" * 62)
    print("推断的默认方案：")
    for item, value in plan:
        print("  · %s：%s" % (item, value))
    print("-" * 62)
    print("下一步：把以上事实填入 assets/requirements_intake.md 第 1、2 部分，")
    print("        只有知识范围、更新频率、成本与延迟权重、权限边界类问题才列入第 3 部分找用户确认。")


def main():
    parser = argparse.ArgumentParser(description="从项目里勘察 RAG 管线事实并给出默认方案")
    parser.add_argument("project", help="项目目录")
    parser.add_argument("--exclude", action="append", default=[], help="排除路径子串（可重复）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    if not os.path.isdir(args.project):
        print("目录不存在：" + args.project)
        return 2
    try:
        hits = scan(args.project, args.exclude)
    except OSError as error:
        print("读取失败：" + str(error))
        return 2

    report(args.project, hits, recommend(hits), args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
