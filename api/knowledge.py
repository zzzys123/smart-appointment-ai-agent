"""知识库管理与文档导入 API。"""

from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from services.document_ingestion_service import DocumentIngestionService
from services.knowledge_service import KnowledgeService, get_shared_knowledge_service


router = APIRouter(prefix="/api/knowledge", tags=["知识库管理"])
MAX_UPLOAD_BYTES = 2 * 1024 * 1024


class KnowledgeItem(BaseModel):
    """兼容新管理页 content 格式和旧版 question/answer 格式。"""

    id: Optional[int] = None
    content: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    question: Optional[str] = None
    answer: Optional[str] = None
    category: str = "general"

    def normalized_content(self) -> str:
        if self.content and self.content.strip():
            return self.content.strip()
        if self.question and self.answer:
            return f"问题: {self.question.strip()}\n答案: {self.answer.strip()}"
        raise ValueError("请提供 content，或同时提供 question 和 answer")


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    category: Optional[str] = None


async def _get_knowledge_service() -> KnowledgeService:
    return await get_shared_knowledge_service()


@router.get("/")
async def get_all_knowledge():
    """获取所有知识条目（包含来源与分块元数据）。"""
    try:
        knowledge_service = await _get_knowledge_service()
        entries = knowledge_service.get_all_documents()
        try:
            categories = knowledge_service.get_all_categories()
        except Exception:
            categories = []
        return {
            "documents": entries or [],
            "categories": categories or [],
            "total_count": len(entries) if entries else 0,
            "status": "success",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取知识库失败: {exc}") from exc


@router.post("/import")
async def import_knowledge_document(
    file: UploadFile = File(...),
    category: str = Form(default="general"),
    source_id: str = Form(default=""),
    chunk_size: int = Form(default=500),
    chunk_overlap: int = Form(default=100),
):
    """导入一个 UTF-8 Markdown/TXT 文件并建立分块索引。"""
    try:
        payload = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="上传文件不能超过 2 MB")

        knowledge_service = await _get_knowledge_service()
        result = await DocumentIngestionService(knowledge_service).import_document(
            filename=file.filename or "",
            content=payload,
            category=category,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return {
            "status": "success",
            "message": (
                f"文档导入完成：新增 {result['imported_count']} 个分块，"
                f"跳过 {result['skipped_count']} 个重复分块"
            ),
            "data": result,
            # 顶层别名便于管理页展示，同时 data 保持结构化响应。
            "total_chunks": result["chunk_count"],
            "created_chunks": result["imported_count"],
            "skipped_chunks": result["skipped_count"],
            "deactivated_chunks": result["deactivated_count"],
            "removed_chunks": result["deactivated_count"],
            "changed_chunks": result["changed_count"],
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"导入文档失败: {exc}") from exc
    finally:
        await file.close()


# 静态路由必须放在 /{knowledge_id} 之前。
@router.post("/search")
async def search_knowledge(request: SearchRequest):
    """搜索知识库，兼容旧 data/count 与管理页 results 合约。"""
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    try:
        knowledge_service = await _get_knowledge_service()
        results = await knowledge_service.search(
            query,
            top_k=request.top_k,
            category=request.category,
        )
        return {
            "status": "success",
            "data": results,
            "count": len(results),
            "results": results,
            "query": query,
            "total_found": len(results),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"搜索知识库失败: {exc}") from exc


@router.get("/{knowledge_id}")
async def get_knowledge(knowledge_id: int):
    """获取特定知识条目。"""
    try:
        knowledge_service = await _get_knowledge_service()
        entry = knowledge_service.get_document(knowledge_id)
        if not entry:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "data": entry}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取知识条目失败: {exc}") from exc


@router.post("/")
async def add_knowledge(item: KnowledgeItem):
    """添加新知识条目。"""
    try:
        content = item.normalized_content()
        knowledge_service = await _get_knowledge_service()
        result = await knowledge_service.add_document(
            content=content,
            category=item.category,
            keywords=item.keywords,
        )
        if not result:
            raise HTTPException(status_code=500, detail="知识条目添加失败")
        return {"status": "success", "message": "知识条目添加成功", "data": result}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"添加知识条目失败: {exc}") from exc


@router.put("/{knowledge_id}")
async def update_knowledge(knowledge_id: int, item: KnowledgeItem):
    """更新知识条目。"""
    try:
        content = item.normalized_content()
        knowledge_service = await _get_knowledge_service()
        result = await knowledge_service.update_document(
            doc_id=knowledge_id,
            content=content,
            category=item.category,
            keywords=item.keywords,
        )
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "message": "知识条目更新成功", "data": result}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新知识条目失败: {exc}") from exc


@router.delete("/{knowledge_id}")
async def delete_knowledge(knowledge_id: int):
    """软删除知识条目。"""
    try:
        knowledge_service = await _get_knowledge_service()
        result = await knowledge_service.delete_document(knowledge_id)
        if not result:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        return {"status": "success", "message": "知识条目删除成功"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除知识条目失败: {exc}") from exc
