from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import JSON, or_, text
from ..base.interfaces import BaseKnowledgeRepository
from ..base.session_manager import SessionManager
from ..models import KnowledgeDocument, KnowledgeIndexState


class KnowledgeRepository(BaseKnowledgeRepository):
    """
    知识库数据访问对象
    
    职责：
    1. 知识文档的CRUD操作
    2. 文档搜索和分类
    3. 文档状态管理
    """
    
    def __init__(self, session_manager: SessionManager):
        """
        初始化知识库数据仓库
        
        Args:
            session_manager: 会话管理器
        """
        self.session_manager = session_manager

    def add_document(self, content: str, category: str, keywords: Optional[List[str]] = None,
                    embedding: Optional[List[float]] = None, *,
                    source_id: Optional[str] = None,
                    source_name: Optional[str] = None,
                    title: Optional[str] = None,
                    chunk_index: Optional[int] = None,
                    chunk_count: Optional[int] = None,
                    content_hash: Optional[str] = None,
                    version: Optional[str] = None) -> int:
        """
        添加知识文档
        
        Args:
            content: 文档内容
            category: 文档分类
            keywords: 关键词列表
            embedding: 嵌入向量
            
        Returns:
            新创建的文档ID
        """
        with self.session_manager.session_scope() as session:
            document = KnowledgeDocument(
                content=content,
                category=category,
                keywords=keywords,
                embedding=embedding,
                source_id=source_id,
                source_name=source_name,
                title=title,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                content_hash=content_hash,
                version=version,
            )
            session.add(document)
            session.flush()
            self._bump_index_generation(session)
            return document.id

    def seed_documents_if_empty(self, documents: List[Dict[str, Any]]) -> int:
        """在跨进程写事务中仅为真正的全新知识库写入默认条目。"""
        if not documents:
            return 0
        with self.session_manager.session_scope() as session:
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            if session.query(KnowledgeDocument.id).first() is not None:
                return 0

            session.add_all([
                KnowledgeDocument(**document)
                for document in documents
            ])
            session.flush()
            self._bump_index_generation(session)
            return len(documents)

    def get_document(self, doc_id: int) -> Optional[Dict[str, Any]]:
        """
        获取指定文档
        
        Args:
            doc_id: 文档ID
            
        Returns:
            文档信息字典，如果不存在返回None
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return None
                
            return self._document_to_dict(document)

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """
        获取所有文档
        
        Args:
            include_inactive: 是否包含已删除的文档
            
        Returns:
            文档信息列表
        """
        with self.session_manager.session_scope() as session:
            query = session.query(KnowledgeDocument)
            
            if not include_inactive:
                query = query.filter(KnowledgeDocument.is_active == 1)
                
            documents = query.all()
            return [self._document_to_dict(doc) for doc in documents]

    def update_document(self, doc_id: int, content: Optional[str] = None, category: Optional[str] = None,
                       keywords: Optional[List[str]] = None, embedding: Optional[List[float]] = None,
                       content_hash: Optional[str] = None) -> bool:
        """
        更新文档
        
        Args:
            doc_id: 文档ID
            content: 新内容
            category: 新分类
            keywords: 新关键词
            embedding: 新嵌入向量
            
        Returns:
            更新是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            
            if content is not None:
                document.content = content
            if category is not None:
                document.category = category
            if keywords is not None:
                document.keywords = keywords
            if embedding is not None:
                document.embedding = embedding
            if content_hash is not None:
                document.content_hash = content_hash
            
            document.updated_at = datetime.utcnow()
            self._bump_index_generation(session)
            return True

    def update_embedding_for_index(
        self,
        doc_id: int,
        embedding: List[float],
        expected_updated_at: Optional[datetime],
    ) -> bool:
        """回填缺失 embedding，不改变内容代数。

        该调用只在构建同一代索引时使用；文档内容、过滤元数据和活跃状态均未
        变化，因此不需要触发其他 worker 再次构建。
        """
        with self.session_manager.session_scope() as session:
            updated = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id,
                or_(
                    KnowledgeDocument.embedding.is_(None),
                    KnowledgeDocument.embedding == JSON.NULL,
                    KnowledgeDocument.embedding == [],
                ),
                KnowledgeDocument.updated_at == expected_updated_at,
            ).update(
                {KnowledgeDocument.embedding: embedding},
                synchronize_session=False,
            )
            return updated == 1

    def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """
        删除文档（支持软删除）
        
        Args:
            doc_id: 文档ID
            soft_delete: 是否软删除
            
        Returns:
            删除是否成功
        """
        with self.session_manager.session_scope() as session:
            document = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.id == doc_id
            ).first()
            
            if not document:
                return False
            
            if soft_delete:
                document.is_active = 0
                document.updated_at = datetime.utcnow()
            else:
                session.delete(document)

            self._bump_index_generation(session)
            return True

    def get_index_generation(self) -> int:
        """返回数据库中最新的知识库内容代数。"""
        with self.session_manager.session_scope() as session:
            state = session.query(KnowledgeIndexState).filter(
                KnowledgeIndexState.id == 1
            ).first()
            return int(state.generation) if state else 0

    @staticmethod
    def _bump_index_generation(session) -> None:
        """在当前写事务内原子增加内容代数。"""
        if session.bind.dialect.name == "sqlite":
            session.execute(text(
                "INSERT INTO knowledge_index_state (id, generation, updated_at) "
                "VALUES (1, 1, CURRENT_TIMESTAMP) "
                "ON CONFLICT(id) DO UPDATE SET "
                "generation = knowledge_index_state.generation + 1, "
                "updated_at = CURRENT_TIMESTAMP"
            ))
            return

        state = session.query(KnowledgeIndexState).filter(
            KnowledgeIndexState.id == 1
        ).with_for_update().first()
        if state is None:
            session.add(KnowledgeIndexState(id=1, generation=1))
        else:
            state.generation += 1
            state.updated_at = datetime.utcnow()

    def search_documents_by_category(self, category: str) -> List[Dict[str, Any]]:
        """
        按分类搜索文档
        
        Args:
            category: 文档分类
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.category == category,
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def search_documents_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        按关键词搜索文档
        
        Args:
            keywords: 关键词列表
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).all()
            
            # 简单的关键词匹配
            matched_docs = []
            for doc in documents:
                doc_keywords = doc.keywords or []
                if any(keyword in doc_keywords for keyword in keywords):
                    matched_docs.append(self._document_to_dict(doc))
            
            return matched_docs

    def search_documents_by_content(self, search_text: str) -> List[Dict[str, Any]]:
        """
        按内容搜索文档
        
        Args:
            search_text: 搜索文本
            
        Returns:
            匹配的文档列表
        """
        with self.session_manager.session_scope() as session:
            documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.content.contains(search_text),
                KnowledgeDocument.is_active == 1
            ).all()
            
            return [self._document_to_dict(doc) for doc in documents]

    def get_all_categories(self) -> List[str]:
        """
        获取所有分类
        
        Returns:
            分类列表
        """
        with self.session_manager.session_scope() as session:
            categories = session.query(KnowledgeDocument.category).filter(
                KnowledgeDocument.is_active == 1
            ).distinct().all()
            
            return [cat[0] for cat in categories]

    def get_documents_count(self) -> int:
        """
        获取文档总数
        
        Returns:
            活跃文档数量
        """
        with self.session_manager.session_scope() as session:
            return session.query(KnowledgeDocument).filter(
                KnowledgeDocument.is_active == 1
            ).count()

    def content_hash_exists(self, content_hash: str) -> bool:
        """判断相同内容的活跃分块是否已经导入。"""
        if not content_hash:
            return False
        with self.session_manager.session_scope() as session:
            return session.query(KnowledgeDocument.id).filter(
                KnowledgeDocument.content_hash == content_hash,
                KnowledgeDocument.is_active == 1,
            ).first() is not None

    def get_source_content_hashes(self, source_id: str) -> set[str]:
        """返回指定来源当前活跃分块的内容哈希。"""
        with self.session_manager.session_scope() as session:
            rows = session.query(KnowledgeDocument.content_hash).filter(
                KnowledgeDocument.source_id == source_id,
                KnowledgeDocument.is_active == 1,
                KnowledgeDocument.content_hash.isnot(None),
            ).all()
            return {row[0] for row in rows}

    def sync_source_chunks(self, source_id: str, chunk_payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
        """在单个事务中同步一个来源的完整分块集合。

        相同哈希的分块会保留并刷新元数据；新增分块插入；本次不再出现的旧分块
        会被软删除。事务同时清理同一来源内可能遗留的重复哈希记录。
        """
        now = datetime.utcnow()
        desired_hashes = {payload["content_hash"] for payload in chunk_payloads}
        imported_count = 0
        skipped_count = 0
        deactivated_count = 0
        changed_count = 0
        document_ids: List[int] = []

        with self.session_manager.session_scope() as session:
            # SQLite 的 BEGIN IMMEDIATE 在读取当前来源前取得写锁，避免多个 worker
            # 同时看到“尚不存在”后插入重复的 source/hash 记录。
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            existing_documents = session.query(KnowledgeDocument).filter(
                KnowledgeDocument.source_id == source_id
            ).order_by(
                KnowledgeDocument.is_active.desc(),
                KnowledgeDocument.id.asc(),
            ).all()
            by_hash: Dict[str, List[KnowledgeDocument]] = {}
            for document in existing_documents:
                if document.content_hash:
                    by_hash.setdefault(document.content_hash, []).append(document)

            retained_ids = set()
            for payload in chunk_payloads:
                content_hash = payload["content_hash"]
                matches = by_hash.get(content_hash, [])
                if matches:
                    document = matches[0]
                    was_active = document.is_active == 1
                    skipped_count += int(was_active)
                    imported_count += int(not was_active)
                    fields = (
                        "content", "category", "keywords", "source_name", "title",
                        "chunk_index", "chunk_count", "content_hash", "version",
                    )
                    changed = not was_active
                    for field in fields:
                        value = payload.get(field)
                        if getattr(document, field) != value:
                            setattr(document, field, value)
                            changed = True
                    if payload.get("embedding") is not None:
                        if document.embedding != payload["embedding"]:
                            document.embedding = payload["embedding"]
                            changed = True
                    document.is_active = 1
                    if changed:
                        document.updated_at = now
                        changed_count += 1

                    # 并发约束加入前可能已有重复，只保留一条。
                    for duplicate in matches[1:]:
                        if duplicate.is_active == 1:
                            duplicate.is_active = 0
                            duplicate.updated_at = now
                            deactivated_count += 1
                            changed_count += 1
                    retained_ids.add(document.id)
                    document_ids.append(document.id)
                else:
                    document = KnowledgeDocument(
                        source_id=source_id,
                        is_active=1,
                        **payload,
                    )
                    session.add(document)
                    session.flush()
                    retained_ids.add(document.id)
                    document_ids.append(document.id)
                    imported_count += 1
                    changed_count += 1

            for document in existing_documents:
                if (
                    document.is_active == 1
                    and document.id not in retained_ids
                    and document.content_hash not in desired_hashes
                ):
                    document.is_active = 0
                    document.updated_at = now
                    deactivated_count += 1
                    changed_count += 1

            if changed_count:
                self._bump_index_generation(session)

        return {
            "document_ids": document_ids,
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "deactivated_count": deactivated_count,
            "changed_count": changed_count,
        }

    def get_documents_by_category_count(self) -> Dict[str, int]:
        """
        获取各分类的文档数量
        
        Returns:
            分类和文档数量的字典
        """
        with self.session_manager.session_scope() as session:
            from sqlalchemy import func
            
            result = session.query(
                KnowledgeDocument.category,
                func.count(KnowledgeDocument.id).label('count')
            ).filter(
                KnowledgeDocument.is_active == 1
            ).group_by(KnowledgeDocument.category).all()
            
            return {category: count for category, count in result}

    def _document_to_dict(self, document: KnowledgeDocument) -> Dict[str, Any]:
        """将文档对象转换为字典"""
        return {
            'id': document.id,
            'content': document.content,
            'category': document.category,
            'keywords': document.keywords,
            'embedding': document.embedding,
            'source_id': document.source_id,
            'source_name': document.source_name,
            'title': document.title,
            'chunk_index': document.chunk_index,
            'chunk_count': document.chunk_count,
            'content_hash': document.content_hash,
            'version': document.version,
            'created_at': document.created_at,
            'updated_at': document.updated_at,
            'is_active': bool(document.is_active)
        }
