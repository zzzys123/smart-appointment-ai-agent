"""共享知识库服务不调用外部模型的生命周期测试。"""

import asyncio

from services import knowledge_service as knowledge_module


def test_shared_service_reuses_initialized_instance(tmp_path, monkeypatch):
    db_path = f"sqlite:///{(tmp_path / 'shared.db').as_posix()}"
    initialize_calls = []

    async def fake_initialize(self):
        initialize_calls.append(self)
        self.initialized = True

    monkeypatch.setattr(
        knowledge_module.KnowledgeService,
        "initialize",
        fake_initialize,
    )

    async def scenario():
        first = await knowledge_module.get_shared_knowledge_service(db_path)
        second = await knowledge_module.get_shared_knowledge_service(db_path)
        return first, second

    try:
        first, second = asyncio.run(scenario())
        assert first is second
        assert initialize_calls == [first]
    finally:
        service = knowledge_module._shared_knowledge_services.pop(db_path, None)
        if service is not None:
            service.db_router.close()


def test_stale_database_generation_retries_index_refresh():
    class FakeRepository:
        generation = 2

        def get_index_generation(self):
            return self.generation

    service = knowledge_module.KnowledgeService.__new__(
        knowledge_module.KnowledgeService
    )
    service.db = FakeRepository()
    service._index_generation = 1
    service.mutation_lock = asyncio.Lock()
    attempts = []

    async def fake_build():
        attempts.append(service.db.generation)
        if len(attempts) == 1:
            raise RuntimeError("temporary index build failure")
        service._index_generation = service.db.generation

    service._build_vector_index = fake_build

    async def scenario():
        try:
            await service._ensure_index_current()
        except RuntimeError:
            pass
        assert service._index_generation == 1
        await service._ensure_index_current()
        await service._ensure_index_current()

    asyncio.run(scenario())
    assert attempts == [2, 2]
