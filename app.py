"""
FastAPI应用程序

主应用程序入口，配置中间件、路由和异常处理
自动初始化知识库和技师数据
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from starlette.exceptions import HTTPException as StarletteHTTPException
from services.knowledge_service import get_shared_knowledge_service
from services.technician_service import TechnicianService
from services.recommendation_service import RecommendationService
from typing import List, Optional
import logging
import asyncio

# 导入路由
from api import api_routers
from api.core.exceptions import api_exception_handler, general_exception_handler, BusinessException
from web import router as web_router

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic模型
from pydantic import BaseModel

class KnowledgeRequest(BaseModel):
    content: str
    category: str
    keywords: List[str] = []

class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    category: Optional[str] = None


class SPAStaticFiles(StaticFiles):
    """Serve the React entry point when a client-side route is refreshed."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)

async def initialize_system():
    """系统启动时自动初始化"""
    try:
        logger.info("🚀 正在初始化智能预约系统...")

        # Redis is optional. Connection failures fall back to local coordination.
        from services.redis_service import get_redis_service
        await get_redis_service().connect()
        
        # 初始化知识库服务
        logger.info("📚 初始化知识库服务...")
        await get_shared_knowledge_service()
        
        # 初始化技师服务
        logger.info("👨‍⚕️ 初始化技师服务...")
        technician_service = TechnicianService()
        technician_service.initialize_default_technicians()
        
        # 初始化推荐服务
        logger.info("🎯 启动推荐调度服务...")
        recommendation_service = RecommendationService()
        if recommendation_service.start_scheduler():
            logger.info("✅ 推荐调度服务启动成功")
        else:
            logger.warning("⚠️ 推荐调度服务启动失败")
        
        logger.info("✅ 系统初始化完成！")
        
    except Exception as e:
        logger.error(f"❌ 系统初始化失败: {e}")
        raise

def create_app() -> FastAPI:
    """创建FastAPI应用实例"""
    
    app = FastAPI(
        title="智能预约AI代理",
        description="提供预约管理、智能咨询、用户行为分析等功能的API服务",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc"
    )

    # 添加CORS中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境中应该设置具体的域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Session-ID"],
    )

    # 注册异常处理器
    app.add_exception_handler(BusinessException, api_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # 注册API路由
    for router in api_routers:
        app.include_router(router)

    # 注册Web界面路由
    app.include_router(web_router)

    # 静态文件
    app.mount("/static", StaticFiles(directory="web/static"), name="static")

    # React 生产构建。开发时由 Vite 提供 /ui/，构建后由 FastAPI 直接托管。
    frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount(
            "/ui",
            SPAStaticFiles(directory=str(frontend_dist), html=True),
            name="react-ui",
        )

    # 添加启动事件
    @app.on_event("startup")
    async def startup_event():
        """应用启动时自动初始化系统"""
        await initialize_system()

    @app.on_event("shutdown")
    async def shutdown_event():
        from services.redis_service import get_redis_service
        await get_redis_service().close()

    return app

# 创建应用实例
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)
