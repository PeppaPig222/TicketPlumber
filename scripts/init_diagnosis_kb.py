#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断域 RAG 知识库初始化脚本。

从 data/documents/ 下的商户 PDF/TXT 文档提取 chunks，写入 Milvus Lite。

用法：
    python scripts/init_diagnosis_kb.py
    python scripts/init_diagnosis_kb.py --pdf data/documents/merchant_architecture.pdf --reset
"""
import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config import RAG_CONFIG
from scripts.process_merchant_pdf import process_document


KNOWLEDGE_BASE_PATH = project_root / "data" / "rag_knowledge"
DEFAULT_PDF_PATH = project_root / "data" / "documents" / "merchant_architecture.pdf"
KNOWLEDGE_DIR = project_root / "data" / "knowledge"
COLLECTION_NAME = "ticket_diagnosis_knowledge"

# 文件名 -> category 映射
CATEGORY_MAP = {
    "merchant_architecture": "商户架构文档",
    "diagnosis_manual": "诊断手册",
    "history_tickets": "历史工单经验",
    "faq_policy": "FAQ政策指引",
}


def load_rag_agent_class():
    """动态加载 .claude/skills/ask-question/script/agent.py 中的 RAGKnowledgeAgent。"""
    agent_script = project_root / ".claude" / "skills" / "ask-question" / "script" / "agent.py"
    if not agent_script.exists():
        raise FileNotFoundError(f"RAGKnowledgeAgent 脚本不存在: {agent_script}")

    spec = importlib.util.spec_from_file_location("RAGKnowledgeAgentModule", agent_script)
    assert spec is not None, f"Failed to load spec from {agent_script}"
    assert spec.loader is not None, "Spec has no loader"

    module = importlib.util.module_from_spec(spec)
    sys.modules["RAGKnowledgeAgentModule"] = module
    spec.loader.exec_module(module)
    return module.RAGKnowledgeAgent


def validate_embedding_model() -> str:
    """检查 embedding 模型路径，返回可用的模型路径或 ID。"""
    model_path = RAG_CONFIG.get("embedding_model", "BAAI/bge-small-zh-v1.5")
    path_obj = Path(model_path).expanduser()
    if not path_obj.is_absolute():
        path_obj = project_root / path_obj

    if path_obj.exists():
        return str(path_obj.resolve())

    print(f"⚠️  本地 embedding 模型未找到: {model_path}")
    print("将尝试从 HuggingFace 下载 BAAI/bge-small-zh-v1.5，请确保网络可用。")
    return "BAAI/bge-small-zh-v1.5"


def _discover_documents(extra_paths: List[Path]) -> List[Path]:
    """发现所有待导入的 PDF/TXT 文档。"""
    docs: List[Path] = []
    seen: set = set()

    candidates: List[Path] = []
    if KNOWLEDGE_DIR.exists():
        candidates.extend(sorted(KNOWLEDGE_DIR.iterdir()))
    for p in extra_paths:
        if p.is_dir():
            candidates.extend(sorted(p.iterdir()))
        elif p.exists():
            candidates.append(p)

    for p in candidates:
        if p.suffix.lower() not in {".pdf", ".txt"}:
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        docs.append(p)
    return docs


def _apply_category(documents: List[Dict[str, Any]], file_path: Path) -> List[Dict[str, Any]]:
    """根据文件名覆盖文档 category。"""
    category = CATEGORY_MAP.get(file_path.stem, "诊断知识库")
    for doc in documents:
        doc["metadata"] = doc.get("metadata", {})
        doc["metadata"]["category"] = category
        title = doc["metadata"].get("title", "")
        if title:
            doc["metadata"]["title"] = f"[{category}] {title}"
    return documents


async def init_knowledge_base(
    pdf_path: Path,
    reset: bool = False,
) -> Dict[str, Any]:
    """初始化诊断域知识库。"""
    RAGKnowledgeAgent = load_rag_agent_class()
    embedding_model = validate_embedding_model()

    KNOWLEDGE_BASE_PATH.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("初始化诊断域 RAG 知识库")
    print("=" * 70)
    print(f"主文档路径: {pdf_path}")
    print(f"知识目录: {KNOWLEDGE_DIR}")
    print(f"知识库存储: {KNOWLEDGE_BASE_PATH}")
    print(f"Embedding 模型: {embedding_model}")
    print()

    # 发现所有文档
    print("1. 发现并切分文档...")
    all_documents: List[Dict[str, Any]] = []
    doc_files = _discover_documents([pdf_path.parent if pdf_path.exists() else project_root / "data" / "documents"])
    # 确保主文档优先被处理（即使与目录扫描重复也会被去重）
    if pdf_path.exists():
        doc_files.insert(0, pdf_path)

    for doc_path in doc_files:
        try:
            docs = process_document(doc_path)
            docs = _apply_category(docs, doc_path)
            all_documents.extend(docs)
            print(f"   ✓ {doc_path.name}: {len(docs)} chunks")
        except Exception as e:
            print(f"   ⚠️  {doc_path.name} 处理失败: {e}")

    if not all_documents:
        print("❌ 未从任何文档中提取到内容")
        return {"status": "error", "message": "empty documents"}

    print(f"✓ 共生成 {len(all_documents)} 个 chunks")
    print(f"   - 文本 chunks: {sum(1 for d in all_documents if not d['metadata'].get('has_diagram'))}")
    print(f"   - 架构图占位 chunks: {sum(1 for d in all_documents if d['metadata'].get('has_diagram'))}")
    print()

    # 初始化 RAG Agent（model=None，仅用于 embedding 和检索）
    print("2. 初始化 RAG Agent...")
    rag_agent = RAGKnowledgeAgent(
        name="RAGKnowledgeAgent",
        model=None,
        knowledge_base_path=str(KNOWLEDGE_BASE_PATH),
        collection_name=COLLECTION_NAME,
        embedding_model=embedding_model,
        top_k=3,
    )

    if not getattr(rag_agent, "initialized", False):
        print("❌ RAG Agent 初始化失败，请检查 pymilvus / sentence-transformers 是否已安装")
        return {"status": "error", "message": "rag agent init failed"}

    print("✓ RAG Agent 初始化成功")
    print()

    # 如需重建 collection
    if reset and rag_agent.milvus_client.has_collection(COLLECTION_NAME):
        print("3. 删除旧 collection 并重建...")
        rag_agent.milvus_client.drop_collection(COLLECTION_NAME)
        rag_agent.milvus_client.create_collection(
            collection_name=COLLECTION_NAME,
            dimension=rag_agent.embedding_dim,
            metric_type="COSINE",
            auto_id=False,
        )
        print("✓ Collection 重建完成")
        print()

    # 添加文档
    print("4. 写入知识库...")
    result = await rag_agent.add_documents(all_documents)
    if result.get("status") != "success":
        print(f"❌ 写入失败: {result.get('message', 'unknown error')}")
        return {"status": "error", "message": result.get("message")}

    print(f"✓ 成功添加 {result['added_count']} 个片段")
    print(f"✓ 知识库总文档数: {result['total_count']}")
    print()

    # 统计信息
    print("5. 知识库统计...")
    stats = await rag_agent.get_stats()
    if stats.get("status") == "success":
        print(f"   Collection: {stats.get('collection_name')}")
        print(f"   总文档数: {stats.get('total_documents')}")
    print()

    # 测试检索
    print("6. 测试检索...")
    test_queries = [
        "订单支付成功但状态显示待退款怎么排查？",
        "资产分配提示余额不足是什么原因？",
        "结算金额和合同分润比例不一致怎么办？",
    ]
    for query in test_queries:
        results = await rag_agent.search_knowledge(query, top_k=2)
        print(f"   查询: {query}")
        if results:
            for i, doc in enumerate(results, 1):
                metadata = doc.get("metadata", {})
                if isinstance(metadata, str):
                    try:
                        import json
                        metadata = json.loads(metadata)
                    except Exception:
                        metadata = {}
                title = metadata.get("title", "Unknown")
                distance = doc.get("distance", 0.0)
                print(f"      [{i}] {title} (相似度: {1 - distance:.3f})")
        else:
            print("      ❌ 未找到相关文档")
    print()

    rag_agent.close()
    print("=" * 70)
    print("知识库初始化完成")
    print("=" * 70)

    return {
        "status": "success",
        "added_count": result["added_count"],
        "total_count": result["total_count"],
    }


def main():
    parser = argparse.ArgumentParser(description="初始化诊断域 RAG 知识库")
    parser.add_argument(
        "--pdf",
        type=Path,
        default=DEFAULT_PDF_PATH,
        help="商户 PDF 文档路径",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="删除重建 collection",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"❌ 文档不存在: {args.pdf}")
        print("请将商户 PDF 放到 data/documents/merchant_architecture.pdf，")
        print("或通过 --pdf 指定路径。")
        sys.exit(1)

    result = asyncio.run(init_knowledge_base(args.pdf, reset=args.reset))
    if result.get("status") != "success":
        sys.exit(1)


if __name__ == "__main__":
    main()
