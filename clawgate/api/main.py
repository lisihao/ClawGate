"""FastAPI 主应用"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import asyncio
import time

from ..engines.manager import EngineManager
from ..engines.base import GenerationRequest
from ..storage.sqlite_store import SQLiteStore

# 创建 FastAPI 应用
app = FastAPI(
    title="OpenClaw Gateway",
    description="Intelligent LLM Router & Scheduler with Continuous Batching",
    version="0.1.0",
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局实例
engine_manager: Optional[EngineManager] = None
db_store: Optional[SQLiteStore] = None


# ========== 请求模型 ==========


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAIRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048
    stream: Optional[bool] = False

    # OpenClaw 扩展字段
    priority: Optional[int] = 1
    agent_type: Optional[str] = None
    task_id: Optional[str] = None


# ========== 启动/关闭事件 ==========


@app.on_event("startup")
async def startup_event():
    """启动时初始化"""
    global engine_manager, db_store

    print("\n" + "=" * 60)
    print("🚀 OpenClaw Gateway 启动中...")
    print("=" * 60)

    # 初始化数据库
    print("\n📊 初始化数据库...")
    db_store = SQLiteStore()

    # 初始化引擎管理器
    print("\n🔧 初始化推理引擎...")
    try:
        engine_manager = EngineManager()
        available_models = engine_manager.get_available_models()
        print(f"\n✅ 可用模型: {', '.join(available_models)}")
    except Exception as e:
        print(f"\n❌ 引擎初始化失败: {e}")
        print("⚠️  服务将以降级模式启动（仅云端模型）")
        engine_manager = None

    print("\n" + "=" * 60)
    print("✅ OpenClaw Gateway 启动完成！")
    print("=" * 60)
    print("\n📖 API 文档: http://localhost:8000/docs")
    print("🔍 健康检查: http://localhost:8000/health\n")


@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理"""
    print("\n👋 OpenClaw Gateway 关闭中...")


# ========== 健康检查 ==========


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "engines": engine_manager.list_engines() if engine_manager else {},
    }


@app.get("/models")
async def list_models():
    """列出可用模型"""
    if not engine_manager:
        return {"local_models": [], "cloud_models": ["deepseek-r1", "glm-5"]}

    return {
        "local_models": engine_manager.get_available_models(),
        "cloud_models": ["deepseek-r1", "deepseek-v3", "glm-5", "glm-4-flash"],
    }


# ========== OpenAI 兼容接口 ==========


@app.post("/v1/chat/completions")
async def chat_completions(request: OpenAIRequest):
    """OpenAI 兼容的聊天接口"""

    # 转换消息格式
    messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

    # 选择引擎
    engine = None
    if engine_manager:
        engine = engine_manager.get_engine(request.model)

    if not engine:
        # 本地引擎不可用，返回错误
        raise HTTPException(
            status_code=404,
            detail=f"Model '{request.model}' not found. Available: {engine_manager.get_available_models() if engine_manager else []}",
        )

    # 创建生成请求
    gen_request = GenerationRequest(
        messages=messages,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        stream=request.stream,
        priority=request.priority,
        agent_type=request.agent_type,
        task_id=request.task_id,
    )

    # 流式响应
    if request.stream:
        return StreamingResponse(
            _generate_stream(engine, gen_request, request.model),
            media_type="text/event-stream",
        )

    # 非流式响应
    else:
        start_time = time.time()
        response = await engine.generate(gen_request)
        end_time = time.time()

        # 记录请求
        if db_store:
            db_store.log_request(
                {
                    "model": request.model,
                    "messages": messages,
                    "priority": request.priority,
                    "agent_type": request.agent_type,
                    "task_id": request.task_id,
                    "response": {"content": response.content},
                    "status": "success",
                    "ttft": response.ttft,
                    "total_time": end_time - start_time,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                }
            )

        # OpenAI 格式响应
        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": response.input_tokens,
                "completion_tokens": response.output_tokens,
                "total_tokens": response.input_tokens + response.output_tokens,
            },
        }


async def _generate_stream(engine, request: GenerationRequest, model: str):
    """流式生成助手函数"""
    async for chunk in engine.generate_stream(request):
        # SSE 格式
        data = {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {JSONResponse(data).body.decode()}\n\n"

    # 结束标记
    yield "data: [DONE]\n\n"


# ========== 统计接口 ==========


@app.get("/stats")
async def get_stats():
    """获取统计信息"""
    if not db_store:
        return {"error": "Database not initialized"}

    recent_requests = db_store.get_request_history(limit=100)

    return {
        "total_requests": len(recent_requests),
        "engines": engine_manager.list_engines() if engine_manager else {},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
