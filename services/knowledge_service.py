# services/knowledge_service.py

import asyncio
from typing import List, Dict, Optional
from db.db_router import DatabaseRouter
from .text_embedding import embed_input
from .retriever import create_retriever
import logging

logger = logging.getLogger(__name__)

# 向后兼容：保留 RRF_K / _tokenize_zh 的引用（真实实现已迁移到 retriever 模块）
from .retriever import RRF_K, _tokenize_zh  # noqa: E402,F401


class KnowledgeService:
    """知识库服务类 - 结合数据库存储和向量检索"""
    
    def __init__(self, db_path: str = 'sqlite:///data/smart_appointment.db'):
        # 使用统一的DatabaseRouter，符合架构设计
        self.db_router = DatabaseRouter(db_path)
        self.db = self.db_router.knowledge  # 通过router访问knowledge repository
        self.initialized = False
        self._index_generation: Optional[int] = None
        self._initialize_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()

        # 可插拔检索器（dense / hybrid，由 RETRIEVER_STRATEGY 决定）
        # 检索逻辑（向量/BM25/RRF/rerank）全部委托给检索器，本类只负责数据管理
        self.retriever = create_retriever()
        
        # 默认知识库内容
        self.default_knowledge = [
            {
                "content": "我们推拿房的营业时间是每天上午9点到晚上10点，全年无休。",
                "category": "营业时间",
                "keywords": ["营业时间", "开门", "关门", "几点", "时间"]
            },
            {
                "content": "我们提供多种推拿服务：全身推拿（120元/60分钟）、肩颈推拿（80元/30分钟）、足底按摩（100元/45分钟）、背部推拿（90元/40分钟）。",
                "category": "服务项目",
                "keywords": ["服务", "推拿", "按摩", "价格", "收费", "多少钱"]
            },
            {
                "content": "我们有专业的男女技师为您服务。所有技师都经过专业培训，持有相关资格证书。您可以根据个人喜好选择男技师或女技师。",
                "category": "技师信息",
                "keywords": ["技师", "师傅", "男", "女", "专业", "资格"]
            },
            {
                "content": "我们店的位置位于北京海淀区中关村大街27号，交通便利，地铁2号线A口向北步行100米即可到达",
                "category": "门店地址",
                "keywords": ["地址", "门店信息", "到达方式", "交通"]
            },
            {
                "content": "全身推拿能够舒缓全身肌肉疲劳，促进血液循环，缓解压力。特别适合久坐办公室的上班族和体力劳动者。",
                "category": "服务介绍",
                "keywords": ["全身推拿", "效果", "作用", "好处", "适合"]
            },
            {
                "content": "肩颈推拿专门针对颈椎和肩部问题，能有效缓解颈椎疼痛、肩膀僵硬等问题。特别推荐给长期使用电脑的人群。",
                "category": "服务介绍",
                "keywords": ["肩颈推拿", "颈椎", "肩膀", "疼痛", "僵硬"]
            },
            {
                "content": "足底按摩通过刺激足部穴位，能够调节全身气血运行，缓解疲劳，改善睡眠质量。",
                "category": "服务介绍",
                "keywords": ["足底按摩", "脚", "穴位", "睡眠", "疲劳"]
            },
            {
                "content": "我们的技师都有3年以上的专业经验，定期接受培训以确保服务质量。我们注重客户体验，力求为每位客户提供最舒适的服务。",
                "category": "服务质量",
                "keywords": ["经验", "专业", "培训", "质量", "舒适"]
            },
            {
                "content": "如需取消或更改预约，请提前至少2小时通知我们。临时取消可能会产生一定的费用。",
                "category": "预约政策",
                "keywords": ["取消", "更改", "改期", "退约", "政策"]
            },
            {
                "content": "我们提供会员卡服务，充值500元送50元，充值1000元送150元。会员还可享受预约优先权和生日优惠。",
                "category": "会员服务",
                "keywords": ["会员", "充值", "优惠", "折扣", "生日"]
            }
        ]

    async def initialize(self):
        """初始化知识库服务"""
        if self.initialized:
            return
        async with self._initialize_lock:
            if self.initialized:
                return
            try:
                # 检查数据库中是否已有数据
                # 包含软删除记录：用户主动清空知识库后，重启不应再次灌入默认项。
                existing_docs = await asyncio.to_thread(
                    self.db.get_all_documents, True
                )

                if not existing_docs:
                    logger.info("数据库为空，初始化默认知识库")
                    await self._create_default_knowledge()
                else:
                    active_count = sum(
                        1 for document in existing_docs
                        if document.get("is_active", True)
                    )
                    logger.info(f"从数据库加载了 {active_count} 条活跃知识")

                # 构建向量索引
                await self._build_vector_index()
                self.initialized = True
                logger.info("知识库服务初始化完成")

            except Exception as e:
                logger.error(f"知识库服务初始化失败: {e}")
                raise

    async def _create_default_knowledge(self):
        """生成默认向量后，用跨进程原子判空事务写入一次。"""
        documents = []
        for knowledge in self.default_knowledge:
            text_for_embedding = f"{knowledge['content']} {' '.join(knowledge['keywords'])}"
            embedding = await asyncio.to_thread(embed_input, text_for_embedding)
            documents.append({
                "content": knowledge["content"],
                "category": knowledge["category"],
                "keywords": knowledge["keywords"],
                "embedding": embedding,
            })

        inserted = await asyncio.to_thread(
            self.db.seed_documents_if_empty, documents
        )
        if inserted:
            logger.info("已写入 %s 条默认知识", inserted)
        else:
            logger.info("另一个进程已完成默认知识初始化，跳过重复写入")

    async def _build_vector_index(self):
        """确保文档 embedding 就绪，并委托检索器构建索引。

        数据管理职责（生成/回写 embedding）留在本类；索引构建交给可插拔检索器。
        """
        try:
            for attempt in range(1, 4):
                # 先记录目标代数，再读取文档。构建结束前会复查代数；发生并发变更
                # 时丢弃这个快照并重试，不把旧快照发布成最新索引。
                target_generation = await asyncio.to_thread(
                    self.db.get_index_generation
                )
                documents = await asyncio.to_thread(self.db.get_all_documents)
                stale_snapshot = False

                # 确保每条文档都有 embedding（缺失则进行带快照条件的回写）。
                for doc in documents:
                    if not doc.get('embedding'):
                        logger.warning(f"文档 {doc['id']} 缺少嵌入向量，正在生成...")
                        text_for_embedding = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
                        embedding = await asyncio.to_thread(embed_input, text_for_embedding)
                        updated = await asyncio.to_thread(
                            self.db.update_embedding_for_index,
                            doc['id'],
                            embedding,
                            doc.get('updated_at'),
                        )
                        if not updated:
                            stale_snapshot = True
                            logger.info("文档 %s 已并发变化，丢弃当前索引快照", doc['id'])
                            break
                        doc['embedding'] = embedding

                if stale_snapshot:
                    continue

                if not documents:
                    logger.warning("没有文档可用于构建索引")

                new_retriever = create_retriever()
                await asyncio.to_thread(new_retriever.build_index, documents)
                latest_generation = await asyncio.to_thread(
                    self.db.get_index_generation
                )
                if latest_generation != target_generation:
                    logger.info(
                        "索引构建期间代数由 %s 变为 %s，重试第 %s 次",
                        target_generation,
                        latest_generation,
                        attempt,
                    )
                    continue

                # 新对象完整构建并复核后再原子替换，查询始终只看到完整索引。
                self.retriever = new_retriever
                self._index_generation = target_generation
                logger.info(
                    f"索引构建完成：strategy={self.retriever.name}, docs={self.retriever.num_docs}"
                )
                return

            raise RuntimeError("知识库在索引构建期间持续变化，请稍后重试")
        except Exception as e:
            logger.error(f"构建索引失败: {e}")
            raise

    async def search(self, query: str, top_k: int = 3, category: str = None) -> List[Dict]:
        """搜索相关文档（委托给可插拔检索器）。

        检索策略与是否精排由检索器决定：
        - RETRIEVER_STRATEGY=dense|hybrid
        - RERANK_ENABLED=true|false
        """
        if not self.initialized:
            logger.warning("知识库服务未初始化")
            return []
        await self._ensure_index_current()
        return await self.retriever.search(query, top_k=top_k, category=category)

    async def _ensure_index_current(self) -> None:
        """多 worker 场景下在查询前惰性追平数据库中的最新内容代数。"""
        current_generation = await asyncio.to_thread(
            self.db.get_index_generation
        )
        if current_generation == self._index_generation:
            return

        async with self.mutation_lock:
            current_generation = await asyncio.to_thread(
                self.db.get_index_generation
            )
            if current_generation != self._index_generation:
                logger.info(
                    "检测到知识库索引代数变化：%s -> %s，正在刷新",
                    self._index_generation,
                    current_generation,
                )
                await self._build_vector_index()

    async def refresh_index(self) -> None:
        """在变更锁内安全重建共享检索索引，供批量导入完成后调用。"""
        async with self.mutation_lock:
            await self._build_vector_index()

    def get_last_trace(self):
        """返回最近一次检索的链路追踪（RetrievalTrace），未检索过则为 None。"""
        return getattr(self.retriever, "last_trace", None)

    async def add_document(self, content: str, category: str, keywords: List[str] = None) -> bool:
        """添加新文档"""
        try:
            if keywords is None:
                keywords = []
            
            # 生成嵌入向量
            text_for_embedding = f"{content} {' '.join(keywords)}"
            embedding = await asyncio.to_thread(embed_input, text_for_embedding)

            async with self.mutation_lock:
                # 保存到数据库
                doc_id = await asyncio.to_thread(
                    self.db.add_document,
                    content,
                    category,
                    keywords,
                    embedding,
                )

                # 重建索引
                await self._build_vector_index()
            
            logger.info(f"成功添加文档 {doc_id}: {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"添加文档失败: {e}")
            return False

    async def update_document(self, doc_id: int, content: str = None, category: str = None, keywords: List[str] = None) -> bool:
        """更新文档"""
        try:
            async with self.mutation_lock:
                return await self._update_document_locked(
                    doc_id, content, category, keywords
                )
        except Exception as e:
            logger.error(f"更新文档失败: {e}")
            return False

    async def _update_document_locked(
        self, doc_id: int, content: str = None,
        category: str = None, keywords: List[str] = None,
    ) -> bool:
        """调用方持有 mutation_lock 时执行更新。"""
        try:
            # 如果更新了内容或关键词，需要重新生成嵌入向量
            embedding = None
            content_hash = None
            if content is not None or keywords is not None:
                # 获取当前文档信息
                current_doc = await asyncio.to_thread(
                    self.db.get_document, doc_id
                )
                if not current_doc:
                    return False
                
                # 使用新值或保持原值
                final_content = content if content is not None else current_doc['content']
                final_keywords = keywords if keywords is not None else current_doc.get('keywords', [])
                
                # 生成新的嵌入向量
                text_for_embedding = f"{final_content} {' '.join(final_keywords)}"
                embedding = await asyncio.to_thread(embed_input, text_for_embedding)
                # 导入生成的分块在编辑后必须同步哈希，否则重复导入判断会失真。
                if content is not None and current_doc.get('content_hash'):
                    from .document_ingestion_service import DocumentIngestionService
                    content_hash = DocumentIngestionService._content_hash(final_content)
            
            # 更新数据库
            success = await asyncio.to_thread(
                self.db.update_document,
                doc_id,
                content,
                category,
                keywords,
                embedding,
                content_hash,
            )
            
            if success:
                # category 等元数据也缓存在检索器中，成功后统一刷新。
                await self._build_vector_index()
            
            return success
            
        except Exception:
            raise

    async def delete_document(self, doc_id: int, soft_delete: bool = True) -> bool:
        """删除文档"""
        try:
            async with self.mutation_lock:
                success = await asyncio.to_thread(
                    self.db.delete_document, doc_id, soft_delete
                )

                if success:
                    await self._build_vector_index()

                return success
            
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            return False

    def get_all_documents(self, include_inactive: bool = False) -> List[Dict]:
        """获取所有文档"""
        return self.db.get_all_documents(include_inactive)

    def get_document(self, doc_id: int) -> Dict:
        """获取指定文档"""
        return self.db.get_document(doc_id)

    def get_all_categories(self) -> List[str]:
        """获取所有分类"""
        return self.db.get_all_categories()

    def get_documents_count(self) -> int:
        """获取文档总数"""
        return self.db.get_documents_count()

    def search_by_category(self, category: str) -> List[Dict]:
        """按分类搜索文档"""
        return self.db.search_documents_by_category(category)

    def search_by_keywords(self, keywords: List[str]) -> List[Dict]:
        """按关键词搜索文档"""
        return self.db.search_documents_by_keywords(keywords)

    async def import_document(
        self,
        filename: str,
        content,
        category: str = "general",
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        rebuild_index: bool = True,
        source_id: Optional[str] = None,
    ) -> Dict:
        """导入并分块 Markdown/TXT 文档的便捷入口。"""
        from .document_ingestion_service import DocumentIngestionService

        return await DocumentIngestionService(self).import_document(
            filename=filename,
            content=content,
            category=category,
            source_id=source_id,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            rebuild_index=rebuild_index,
        )


_shared_knowledge_services: Dict[str, KnowledgeService] = {}


async def get_shared_knowledge_service(
    db_path: str = 'sqlite:///data/smart_appointment.db',
) -> KnowledgeService:
    """返回进程内按数据库路径共享、已初始化的知识库服务。"""
    service = _shared_knowledge_services.get(db_path)
    if service is None:
        # 首次 await 之前完成注册，同一事件循环内不会创建两个实例。
        service = KnowledgeService(db_path=db_path)
        _shared_knowledge_services[db_path] = service
    if not service.initialized:
        await service.initialize()
    return service
