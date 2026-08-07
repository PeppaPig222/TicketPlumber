#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
商户 PDF 架构文档切分工具。

支持：
- 从 .pdf 提取文本并按段落切分
- 从 .txt 直接读取并按段落切分（便于测试）
- 对复杂架构图页面生成结构化占位 chunk（提取图上文字碎片 + 上下文关联）

复杂架构图处理策略：
1. 多维度检测图页：字数少、pdfplumber 检测到图片对象、或存在大量绘制元素。
2. 提取图上文字碎片：通过 extract_words() 捞出图上模块名、标签、箭头文字。
3. 上下文关联：把图页与前后相邻页的主题/关键词拼在一起，提升检索命中率。
4. 可扩展 OCR：占位 chunk 中预留说明，后续可接入 PaddleOCR / tesseract 生成图片描述。
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# 文本切分
# ---------------------------------------------------------------------------

def split_text_into_chunks(
    text: str,
    max_chars: int = 600,
    overlap: int = 100,
) -> List[str]:
    """
    按段落切分文本，超长段落使用滑动窗口兜底。

    Args:
        text: 原始文本
        max_chars: 每个 chunk 最大字符数
        overlap: 滑动窗口重叠字符数

    Returns:
        chunk 字符串列表
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_chars:
            current_chunk = (current_chunk + "\n\n" + para).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)

            if len(para) > max_chars:
                start = 0
                while start < len(para):
                    end = start + max_chars
                    chunks.append(para[start:end])
                    start = end - overlap
                    if start >= len(para):
                        break
                current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ---------------------------------------------------------------------------
# PDF 页面特征提取
# ---------------------------------------------------------------------------

def _extract_page_features(page: Any) -> Dict[str, Any]:
    """
    提取单页的结构化特征，用于判断是否为复杂架构图。

    Returns:
        {
            "text": str,              # extract_text 结果
            "words": List[Dict],      # extract_words 结果，含坐标
            "char_count": int,        # 文本字符数
            "image_count": int,       # 检测到的图片对象数
            "draw_count": int,        # 绘制对象数（线、矩形、曲线等）
            "table_count": int,       # 表格数
        }
    """
    text = (page.extract_text() or "").strip()
    words = page.extract_words() or []

    image_count = 0
    draw_count = 0
    table_count = 0

    try:
        images = page.images or []
        image_count = len(images)
    except Exception:
        pass

    try:
        # pdfplumber 中 page.objects 包含 line、rect、curve 等绘制元素
        objects = page.objects or {}
        draw_keys = ("line", "rect", "curve", "polyline", "figure")
        draw_count = sum(len(objects.get(k, [])) for k in draw_keys)
    except Exception:
        pass

    try:
        tables = page.find_tables() or []
        table_count = len(tables)
    except Exception:
        pass

    return {
        "text": text,
        "words": words,
        "char_count": len(text),
        "image_count": image_count,
        "draw_count": draw_count,
        "table_count": table_count,
    }


def _is_diagram_page(
    features: Dict[str, Any],
    min_text_chars: int = 100,
    image_draw_threshold: int = 20,
) -> bool:
    """
    判断一页是否为以图/架构图为主的页面。

    判定条件（满足任一即可）：
    1. 文本字符数低于阈值（传统规则）。
    2. 检测到图片对象。
    3. 绘制元素数量超过阈值（典型架构图/流程图）。
    4. 字数很少但绘制元素较多（强图页信号）。
    """
    char_count = features.get("char_count", 0)
    image_count = features.get("image_count", 0)
    draw_count = features.get("draw_count", 0)

    if char_count < min_text_chars:
        return True
    if image_count > 0:
        return True
    if draw_count >= image_draw_threshold:
        return True
    if char_count < min_text_chars * 2 and draw_count >= image_draw_threshold // 2:
        return True
    return False


# ---------------------------------------------------------------------------
# 关键词与上下文提取
# ---------------------------------------------------------------------------

def _extract_keywords(text: str, top_k: int = 15) -> List[str]:
    """简单关键词提取：按中文/英文词拆分，过滤常见停用词，返回高频词。"""
    if not text:
        return []

    # 中文按字/词简单处理；英文按单词提取
    tokens = re.findall(r"[a-zA-Z_]+|[\u4e00-\u9fa5]{2,8}", text)
    stopwords = {
        "的", "了", "和", "是", "在", "有", "与", "及", "等", "为", "对",
        "将", "从", "到", "进行", "一个", "需要", "可以", "通过", "根据",
        "the", "and", "of", "to", "in", "for", "is", "on", "with", "as",
    }
    filtered = [t for t in tokens if t.lower() not in stopwords and len(t) > 1]
    counter = Counter(filtered)
    return [word for word, _ in counter.most_common(top_k)]


def _first_meaningful_line(text: str) -> str:
    """取第一段非空且有意义的文本作为标题候选。"""
    for line in text.splitlines():
        line = line.strip()
        if line and len(line) > 3:
            return line[:80]
    return ""


def _build_page_contexts(
    pages: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    为每页生成上下文摘要（标题 + 关键词），用于图页关联。
    """
    contexts = []
    for p in pages:
        text = p.get("text", "")
        contexts.append({
            "page": p.get("page", 0),
            "title": _first_meaningful_line(text),
            "keywords": _extract_keywords(text, top_k=10),
            "char_count": len(text),
        })
    return contexts


def _build_diagram_summary(
    page_num: int,
    features: Dict[str, Any],
    contexts: List[Dict[str, Any]],
    file_name: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    为架构图页生成结构化占位 chunk。

    Returns:
        (content, metadata)
    """
    words = features.get("words", [])
    # 按 y 坐标分组，模拟图上文字的行结构
    rows: Dict[int, List[str]] = {}
    for w in words:
        try:
            top = int(float(w.get("top", 0)))
        except (TypeError, ValueError):
            top = 0
        # 把相近的 y 坐标归到同一行（容差 5px）
        row_key = (top // 5) * 5
        rows.setdefault(row_key, []).append(w.get("text", ""))

    # 合并成行
    diagram_lines = []
    for row_key in sorted(rows.keys()):
        line_text = " ".join(rows[row_key])
        if line_text.strip():
            diagram_lines.append(line_text)

    # 图上可提取的关键词
    diagram_text = " ".join(diagram_lines)
    diagram_keywords = _extract_keywords(diagram_text, top_k=20)

    # 前后页上下文
    context_parts = []
    for ctx in contexts:
        if abs(ctx["page"] - page_num) <= 1 and ctx["page"] != page_num:
            title = ctx["title"]
            keywords = ctx["keywords"]
            parts = []
            if title:
                parts.append(f"第{ctx['page']}页：{title}")
            if keywords:
                parts.append(f"关键词：{', '.join(keywords[:8])}")
            if parts:
                context_parts.append("；".join(parts))

    lines = [f"[架构图/流程图：第 {page_num} 页]"]
    lines.append(f"来源文档：{file_name}")

    if diagram_keywords:
        lines.append(f"图上可识别的系统组件/关键词：{', '.join(diagram_keywords)}")

    if diagram_lines:
        # 保留最多 10 行图上文字，避免 chunk 过长
        visible_lines = diagram_lines[:10]
        lines.append("图上文字碎片：")
        lines.extend(f"  - {ln}" for ln in visible_lines)

    if context_parts:
        lines.append("相邻页上下文：")
        lines.extend(f"  - {cp}" for cp in context_parts)

    lines.append(
        "说明：本页主要内容来自复杂架构图/流程图，文本无法完整自动提取。"
        "建议后续通过 OCR 或人工标注补充完整流程说明。"
    )

    metadata = {
        "has_diagram": True,
        "diagram_keywords": diagram_keywords,
        "diagram_text_lines": len(diagram_lines),
        "image_count": features.get("image_count", 0),
        "draw_count": features.get("draw_count", 0),
    }
    return "\n".join(lines), metadata


# ---------------------------------------------------------------------------
# 文档加载
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> List[Dict[str, Any]]:
    """
    使用 pdfplumber 逐页提取 PDF 文本与页面特征。

    Returns:
        每页一个 dict：{
            "page": page_num,
            "text": text,
            "features": {...},
            "is_diagram": bool,
        }
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError("请先安装 pdfplumber: pip install pdfplumber") from e

    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            features = _extract_page_features(page)
            is_diagram = _is_diagram_page(features)
            pages.append({
                "page": i,
                "text": features["text"],
                "features": features,
                "is_diagram": is_diagram,
            })
    return pages


def extract_text_from_txt(txt_path: Path) -> List[Dict[str, Any]]:
    """从 .txt 文件读取全部文本，模拟单页 PDF。"""
    content = txt_path.read_text(encoding="utf-8").strip()
    return [{
        "page": 1,
        "text": content,
        "features": {
            "text": content,
            "words": [],
            "char_count": len(content),
            "image_count": 0,
            "draw_count": 0,
            "table_count": 0,
        },
        "is_diagram": False,
    }]


# ---------------------------------------------------------------------------
# 主处理流程
# ---------------------------------------------------------------------------

def process_document(
    file_path: Path,
    max_chars: int = 600,
    overlap: int = 100,
    min_text_chars: int = 100,
) -> List[Dict[str, Any]]:
    """
    处理单个文档，返回可用于 RAG 的 chunk 列表。

    Args:
        file_path: PDF 或 TXT 文件路径
        max_chars: 每个 chunk 最大字符数
        overlap: 滑动窗口重叠字符数
        min_text_chars: 页面文本量低于该值视为以图为主

    Returns:
        每个元素包含 id / content / metadata 的 dict 列表
    """
    if not file_path.exists():
        raise FileNotFoundError(f"文档不存在: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        pages = extract_text_from_pdf(file_path)
    elif suffix == ".txt":
        pages = extract_text_from_txt(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}，仅支持 .pdf 和 .txt")

    title = file_path.stem

    # 如果整个文档没有任何有效文本，直接返回空列表
    total_text = "\n".join(p.get("text", "") for p in pages).strip()
    if not total_text and not any(p.get("is_diagram") for p in pages):
        return []

    contexts = _build_page_contexts(pages)
    documents: List[Dict[str, Any]] = []

    for page_info in pages:
        page_num = page_info["page"]
        text = page_info["text"]
        is_diagram = page_info["is_diagram"]
        features = page_info.get("features", {})

        if is_diagram or len(text) < min_text_chars:
            content, diagram_meta = _build_diagram_summary(
                page_num=page_num,
                features=features,
                contexts=contexts,
                file_name=file_path.name,
            )
            metadata = {
                "source": file_path.name,
                "page": page_num,
                "category": "商户架构文档",
                "title": f"{title} (第 {page_num} 页 架构图)",
                "has_diagram": True,
            }
            metadata.update(diagram_meta)
            documents.append({
                "id": f"{title}_p{page_num}_diagram",
                "content": content,
                "metadata": metadata,
            })
            continue

        chunks = split_text_into_chunks(text, max_chars=max_chars, overlap=overlap)
        keywords = _extract_keywords(text, top_k=10)
        for i, chunk in enumerate(chunks, start=1):
            documents.append({
                "id": f"{title}_p{page_num}_chunk{i}",
                "content": chunk,
                "metadata": {
                    "source": file_path.name,
                    "page": page_num,
                    "category": "商户架构文档",
                    "title": f"{title} (第 {page_num} 页，片段 {i})",
                    "has_diagram": False,
                    "page_keywords": keywords,
                },
            })

    return documents


def main():
    parser = argparse.ArgumentParser(description="切分商户 PDF/TXT 文档为 RAG chunks")
    parser.add_argument("input", type=Path, help="输入文件路径 (.pdf 或 .txt)")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSON 文件路径（可选，默认打印到 stdout）",
    )
    parser.add_argument("--max-chars", type=int, default=600, help="每 chunk 最大字符数")
    parser.add_argument("--overlap", type=int, default=100, help="滑动窗口重叠字符数")
    args = parser.parse_args()

    docs = process_document(args.input, max_chars=args.max_chars, overlap=args.overlap)
    report = {
        "source": str(args.input),
        "total_chunks": len(docs),
        "diagram_chunks": sum(1 for d in docs if d["metadata"].get("has_diagram")),
        "documents": docs,
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已生成 {len(docs)} 个 chunks，保存到 {args.output}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
