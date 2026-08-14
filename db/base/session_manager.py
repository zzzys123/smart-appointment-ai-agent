from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker, scoped_session
from ..models import Base


class SessionManager:
    """
    数据库会话管理器
    
    职责：
    1. 管理数据库连接和会话
    2. 提供统一的会话上下文管理
    3. 处理事务和异常回滚
    """
    
    def __init__(self, db_path='sqlite:///data/smart_appointment.db'):
        """
        初始化会话管理器
        
        Args:
            db_path: 数据库连接路径
        """
        self._ensure_sqlite_directory(db_path)
        self.engine = create_engine(db_path)
        Base.metadata.create_all(self.engine)
        self._migrate_knowledge_document_columns()
        self.Session = scoped_session(sessionmaker(bind=self.engine))

    @staticmethod
    def _ensure_sqlite_directory(db_path: str) -> None:
        """创建 SQLite 文件的父目录，保证 fresh clone 可以直接启动。"""
        if not db_path.startswith("sqlite:///"):
            return
        database_path = db_path[len("sqlite:///"):]
        if not database_path or database_path == ":memory:":
            return
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    def _migrate_knowledge_document_columns(self) -> None:
        """为已有 SQLite 数据库补充分块字段。

        项目没有引入 Alembic，因此这里只处理本次新增的可空字段；新数据库仍由
        SQLAlchemy metadata 创建。迁移语句保持幂等，不改写已有数据。
        """
        if self.engine.dialect.name != "sqlite":
            return
        inspector = inspect(self.engine)
        if "knowledge_documents" not in inspector.get_table_names():
            return

        existing = {
            column["name"]
            for column in inspector.get_columns("knowledge_documents")
        }
        columns = {
            "source_id": "VARCHAR(64)",
            "source_name": "VARCHAR(255)",
            "title": "VARCHAR(255)",
            "chunk_index": "INTEGER",
            "chunk_count": "INTEGER",
            "content_hash": "VARCHAR(64)",
            "version": "VARCHAR(64)",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                try:
                    with self.engine.begin() as connection:
                        connection.execute(
                            text(f"ALTER TABLE knowledge_documents ADD COLUMN {name} {sql_type}")
                        )
                except OperationalError as exc:
                    # 多 worker 可能在 inspect 后由另一个进程先完成 ALTER。
                    if "duplicate column name" not in str(exc).lower():
                        raise
                    refreshed = {
                        column["name"]
                        for column in inspect(self.engine).get_columns("knowledge_documents")
                    }
                    if name not in refreshed:
                        raise
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_source_id "
                    "ON knowledge_documents (source_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_content_hash "
                    "ON knowledge_documents (content_hash)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_source_hash "
                    "ON knowledge_documents (source_id, content_hash)"
                )
            )

    @contextmanager
    def session_scope(self):
        """
        提供会话上下文管理
        
        自动处理：
        - 会话创建和关闭
        - 事务提交和回滚
        - 异常处理
        """
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        """关闭会话管理器"""
        self.Session.remove()
        self.engine.dispose()
