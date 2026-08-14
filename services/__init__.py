"""业务服务层的延迟导出。

不要在包导入阶段加载 FAISS、LangChain 或模型 SDK。这样文档分块器和离线评估
可以只依赖 Python 标准库；现有 ``from services import KnowledgeService`` 等写法仍
通过模块级 ``__getattr__`` 保持兼容。
"""

from importlib import import_module

__all__ = [
    'embed_input',
    'find_best_match_indices',
    'save_technician_embeddings',
    'load_technician_embeddings',
    'KnowledgeService',
    'TechnicianService',
    'AppointmentService',
    'UserBehaviorService',
    'RecommendationService'
]


_EXPORTS = {
    'embed_input': ('.text_embedding', 'embed_input'),
    'find_best_match_indices': ('.text_embedding', 'find_best_match_indices'),
    'save_technician_embeddings': ('.text_embedding', 'save_technician_embeddings'),
    'load_technician_embeddings': ('.text_embedding', 'load_technician_embeddings'),
    'KnowledgeService': ('.knowledge_service', 'KnowledgeService'),
    'TechnicianService': ('.technician_service', 'TechnicianService'),
    'AppointmentService': ('.appointment_service', 'AppointmentService'),
    'UserBehaviorService': ('.user_behavior_service', 'UserBehaviorService'),
    'RecommendationService': ('.recommendation_service', 'RecommendationService'),
}


def __getattr__(name):
    """首次访问公开符号时再加载对应模块。"""
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
