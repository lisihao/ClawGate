"""FastAPI 主应用 v2 - 集成 CB + ContextEngine + 云端路由 + ClawGate 7"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional, Any, Union
import asyncio
import time
import os
import logging

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clawgate.api")

from ..engines.manager import EngineManager
from ..engines.base import GenerationRequest
from ..storage.sqlite_store import SQLiteStore
from ..context.manager import ContextManager
from ..context.semantic_cache import SemanticCache
from ..router.classifier import TaskClassifier
from ..router.selector import ModelSelector
from ..scheduler.continuous_batching import ContinuousBatchingScheduler, Request as CBRequest
from ..backends.cloud.glm import GLMBackend
from ..backends.cloud.openai import OpenAIBackend
from ..backends.cloud.deepseek import DeepSeekBackend
from ..backends.cloud.chatgpt_backend import ChatGPTBackend
from ..backends.cloud.gemini import GeminiBackend
from ..backends.cloud.dispatcher import CloudDispatcher
from ..scheduler.queue_manager import QueueManager, ScheduledRequest, DurationEstimate, AdmissionError
from .dashboard import router as dashboard_router, init_dashboard

# 创建 FastAPI 应用
app = FastAPI(
    title="OpenClaw Gateway v2",
    description="Intelligent LLM Router with Continuous Batching, ContextEngine & Cloud Routing",
    version="0.2.0",
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
context_manager: Optional[ContextManager] = None
task_classifier: Optional[TaskClassifier] = None
model_selector: Optional[ModelSelector] = None
cb_scheduler: Optional[ContinuousBatchingScheduler] = None
cloud_dispatcher: Optional[CloudDispatcher] = None
queue_manager: Optional[QueueManager] = None
semantic_cache: Optional[SemanticCache] = None

# 云端后端
cloud_backends: Dict = {}


# ========== 请求模型 ==========


class OpenAIMessage(BaseModel):
    role: str
    content: Union[str, List[Any], None] = ""


class OpenAIRequest(BaseModel):
    model: str = "auto"
    messages: List[OpenAIMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048
    stream: Optional[bool] = False

    # OpenClaw 扩展字段
    priority: Optional[int] = 1  # 0=紧急, 1=正常, 2=后台
    agent_type: Optional[str] = None  # judge/builder/flash
    agent_id: Optional[str] = None  # agent 唯一标识 (调度公平性)
    task_id: Optional[str] = None
    enable_context_compression: Optional[bool] = False  # 启用上下文压缩
    target_context_tokens: Optional[int] = None  # 目标上下文 token 数

    class Config:
        extra = "ignore"  # 忽略未知字段


# ========== 启动/关闭事件 ==========


@app.on_event("startup")
async def startup_event():
    """启动时初始化"""
    global engine_manager, db_store, context_manager, task_classifier, model_selector, cb_scheduler, cloud_backends, cloud_dispatcher, queue_manager, semantic_cache

    print("\n" + "=" * 60)
    print("🚀 OpenClaw Gateway v2 启动中...")
    print("=" * 60)

    # 1. 初始化数据库
    print("\n📊 初始化数据库...")
    db_store = SQLiteStore()

    # 2. 初始化本地引擎
    print("\n🔧 初始化本地推理引擎...")
    try:
        engine_manager = EngineManager()
        available_models = engine_manager.get_available_models()
        print(f"✅ 本地模型: {', '.join(available_models)}")
    except Exception as e:
        print(f"⚠️  本地引擎初始化失败: {e}")
        print("   继续使用云端模型...")
        engine_manager = None

    # 3. 初始化云端后端
    print("\n☁️  初始化云端后端...")
    cloud_count = 0

    if os.getenv("GLM_API_KEY"):
        try:
            cloud_backends["glm"] = GLMBackend()
            print("✅ GLM 后端已启用")
            cloud_count += 1
        except Exception as e:
            print(f"⚠️  GLM 后端失败: {e}")

    if os.getenv("OPENAI_API_KEY"):
        try:
            cloud_backends["openai"] = OpenAIBackend()
            print("✅ OpenAI 后端已启用")
            cloud_count += 1
        except Exception as e:
            print(f"⚠️  OpenAI 后端失败: {e}")

    if os.getenv("DEEPSEEK_API_KEY"):
        try:
            cloud_backends["deepseek"] = DeepSeekBackend()
            print("✅ DeepSeek 后端已启用")
            cloud_count += 1
        except Exception as e:
            print(f"⚠️  DeepSeek 后端失败: {e}")

    if os.getenv("CHATGPT_ACCESS_TOKEN"):
        try:
            cloud_backends["chatgpt"] = ChatGPTBackend()
            print("✅ ChatGPT 订阅账户后端已启用（使用 chatgpt.com/backend-api）")
            cloud_count += 1
        except Exception as e:
            print(f"⚠️  ChatGPT 后端失败: {e}")

    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        try:
            cloud_backends["gemini"] = GeminiBackend()
            print("✅ Gemini 后端已启用（内容审查宽松，敏感路由首选）")
            cloud_count += 1
        except Exception as e:
            print(f"⚠️  Gemini 后端失败: {e}")

    if cloud_count == 0:
        print("⚠️  无云端后端（设置 API Key 以启用）")

    # 3b. 初始化 CloudDispatcher (F1+F2: retry + fallback + circuit breaker)
    if cloud_backends:
        cloud_dispatcher = CloudDispatcher(backends=cloud_backends, max_retries=3)
        print(f"✅ CloudDispatcher 已启用 (retry=3, fallback, circuit_breaker)")

    # 4. 初始化 ContextEngine
    print("\n🧠 初始化 ContextEngine...")
    try:
        context_manager = ContextManager(db_store=db_store)
        print("✅ ContextEngine 已启用（支持压缩、摘要、缓存）")
    except Exception as e:
        print(f"⚠️  ContextEngine 失败: {e}")

    # 5. 初始化智能路由
    print("\n🎯 初始化智能路由...")
    task_classifier = TaskClassifier()
    model_selector = ModelSelector()
    print("✅ 任务分类器 + 模型选择器已启用")

    # 6. 初始化 Continuous Batching 调度器
    print("\n⚡ 初始化 Continuous Batching 调度器...")
    cb_scheduler = ContinuousBatchingScheduler(
        max_batch_size=8,
        base_chunk_size=256,
        enable_sjf=True,
    )
    print("✅ CB 调度器已启用（支持优先级 + SJF）")

    # 7. 初始化 SemanticCache (F6)
    if db_store:
        semantic_cache = SemanticCache(db_store=db_store, threshold=0.85, max_size=500, ttl_hours=4)
        print("✅ SemanticCache 已启用 (Jaccard>=0.85, max=500, TTL=4h)")

    # 8. 初始化 QueueManager (智能队列调度)
    print("\n📋 初始化 QueueManager...")
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        _models_cfg_path = _Path("config/models.yaml")
        _scheduling_cfg = {}
        if _models_cfg_path.exists():
            with open(_models_cfg_path) as _f:
                _models_cfg = _yaml.safe_load(_f)
                _scheduling_cfg = _models_cfg.get("scheduling", {})
        queue_manager = QueueManager(concurrency_config=_scheduling_cfg)
        await queue_manager.start()
        print(f"✅ QueueManager 已启用 (三车道调度 + per-model 信号量)")
    except Exception as e:
        print(f"⚠️  QueueManager 初始化失败: {e}")
        queue_manager = None

    # 9. 初始化 Dashboard (F4)
    init_dashboard(db_store, cloud_dispatcher, context_manager, queue_manager)
    app.include_router(dashboard_router)
    print("✅ Dashboard 已注册 (/dashboard/*)")

    # 10. 清理过期会话段 + 长期记忆
    if context_manager and context_manager.conversation_store:
        deleted = context_manager.conversation_store.cleanup_expired()
        if deleted > 0:
            print(f"🧹 清理过期会话段: {deleted} 条")
        deleted_ltm = context_manager.conversation_store.cleanup_expired_ltm()
        if deleted_ltm > 0:
            print(f"🧹 清理过期长期记忆: {deleted_ltm} 条")

    print("\n" + "=" * 60)
    print("✅ OpenClaw Gateway v2 启动完成！")
    print("=" * 60)
    print("\n📖 API 文档: http://localhost:8000/docs")
    print("🔍 健康检查: http://localhost:8000/health")
    print("\n🎯 特性:")
    print("  - ⚡ Continuous Batching（预期 6× TTFT 提升）")
    print("  - 🧠 ContextEngine（压缩/摘要/缓存 + 会话记忆 + Prompt Cache）")
    print("  - ☁️  云端路由（GLM/OpenAI/DeepSeek + Retry + Fallback）")
    print("  - 🎯 智能调度（任务分类 + 模型选择）")
    print("  - 📊 可观测性仪表盘 (/dashboard/*)")
    print("  - 🔄 语义缓存 (Jaccard 相似度)")
    print("  - 📋 智能队列调度 (三车道 + 信号量 + Agent 公平)")
    print("  - 📦 云端上下文适配 (F7)\n")


@app.on_event("shutdown")
async def shutdown_event():
    """关闭时清理"""
    print("\n👋 OpenClaw Gateway v2 关闭中...")

    # 关闭 QueueManager
    if queue_manager:
        await queue_manager.stop()

    # 关闭云端后端
    for name, backend in cloud_backends.items():
        try:
            await backend.close()
        except:
            pass


# ========== 健康检查 ==========


@app.get("/health")
async def health_check():
    """健康检查"""
    local_models = engine_manager.get_available_models() if engine_manager else []

    return {
        "status": "healthy",
        "version": "0.3.0",
        "features": {
            "continuous_batching": cb_scheduler is not None,
            "context_engine": context_manager is not None,
            "smart_routing": task_classifier is not None,
            "cloud_dispatcher": cloud_dispatcher is not None,
            "semantic_cache": semantic_cache is not None,
            "queue_manager": queue_manager is not None,
            "dashboard": True,
        },
        "local_models": local_models,
        "cloud_backends": list(cloud_backends.keys()),
        "cloud_health": cloud_dispatcher.get_health() if cloud_dispatcher else {},
    }


@app.get("/models")
async def list_models():
    """列出可用模型"""
    local_models = engine_manager.get_available_models() if engine_manager else []

    cloud_models = []
    if "glm" in cloud_backends:
        cloud_models.extend(["glm-4-flash", "glm-4-plus", "glm-5"])
    if "openai" in cloud_backends:
        cloud_models.extend(["gpt-4o", "gpt-4o-mini"])
    if "chatgpt" in cloud_backends:
        cloud_models.extend(["gpt-5.2", "gpt-5.1"])
    if "deepseek" in cloud_backends:
        cloud_models.extend(["deepseek-r1", "deepseek-v3"])
    if "gemini" in cloud_backends:
        cloud_models.extend(["gemini-2.5-flash", "gemini-2.5-pro"])

    return {
        "local_models": local_models,
        "cloud_models": cloud_models,
    }


# ========== OpenAI 兼容接口 ==========


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 兼容的聊天接口 - v2 增强版"""
    # 先读取原始 body 做调试
    raw_body = await request.body()
    import json as _json
    try:
        raw_data = _json.loads(raw_body)
        logger.debug(f"[原始请求] keys={list(raw_data.keys())} | model={raw_data.get('model')} | messages_count={len(raw_data.get('messages', []))}")
    except Exception:
        logger.error(f"[原始请求] 无法解析 JSON: {raw_body[:200]}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 兼容处理: 去掉不认识的字段，避免 422
    known_fields = {"model", "messages", "temperature", "max_tokens", "stream", "priority", "agent_type", "agent_id", "task_id", "enable_context_compression", "target_context_tokens"}
    filtered_data = {k: v for k, v in raw_data.items() if k in known_fields}

    # 兼容: model 可能是 "gateway/auto" 格式，提取实际 model
    model_val = filtered_data.get("model", "auto")
    if "/" in model_val:
        filtered_data["model"] = model_val.split("/")[-1]
        logger.debug(f"[兼容] model 格式转换: {model_val} → {filtered_data['model']}")

    # 兼容: messages.content 可能是列表格式 [{"type":"text","text":"..."}]
    # 需要转换为纯字符串
    if "messages" in filtered_data:
        for msg in filtered_data["messages"]:
            if isinstance(msg.get("content"), list):
                # 提取所有 text 部分拼接
                text_parts = []
                for part in msg["content"]:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                msg["content"] = "".join(text_parts)
                logger.debug(f"[兼容] content 列表→字符串: {msg['content'][:50]}...")

    try:
        request = OpenAIRequest(**filtered_data)
    except Exception as e:
        logger.error(f"[解析失败] {e} | filtered_data={filtered_data}")
        raise HTTPException(status_code=422, detail=str(e))

    return await _chat_completions_inner(request)


async def _chat_completions_inner(request: OpenAIRequest):
    """OpenAI 兼容的聊天接口 - v2 增强版"""
    req_start = time.time()
    last_user_msg = next((m.content[:80] for m in reversed(request.messages) if m.role == "user"), "N/A")
    logger.info(
        f"{'='*60}\n"
        f"[请求] model={request.model} stream={request.stream} priority={request.priority} "
        f"agent={request.agent_type}\n"
        f"[请求] 消息数={len(request.messages)} | 最后用户消息: {last_user_msg}..."
    )

    # 转换消息格式
    messages = [{"role": msg.role, "content": msg.content} for msg in request.messages]

    # 1. 上下文压缩（可选）
    if request.enable_context_compression and context_manager:
        target_tokens = request.target_context_tokens or 4096

        # 先检查缓存
        cached = context_manager.get_cached_context(messages)
        if cached:
            messages, meta = cached
            print(f"✅ 使用缓存压缩（命中 {meta.get('hit_count', 0)} 次）")
        else:
            # 压缩
            compressed, meta = context_manager.compress(
                messages=messages,
                target_tokens=target_tokens,
                agent_type=request.agent_type,
            )
            messages = compressed

            # 缓存结果
            context_manager.cache_context(
                messages=messages,
                compressed_messages=compressed,
                metadata=meta,
            )
            print(
                f"✅ 上下文压缩: {meta['original_tokens']} → {meta['compressed_tokens']} tokens"
            )

    # 2. 任务分类（智能路由）
    task_info = None  # 保存分类结果供后续 QueueManager 使用
    if task_classifier and (not request.model or request.model == "auto"):
        task_info = task_classifier.classify(messages)
        sensitivity = task_info.get("sensitivity", {})
        sensitivity_level = sensitivity.get("level", "none")
        print(f"📊 任务分类: {task_info['task_type']} (复杂度: {task_info['complexity']})")
        if sensitivity_level != "none":
            logger.warning(
                f"[路由] ⚠️ 敏感内容! level={sensitivity_level} "
                f"categories={sensitivity.get('categories', [])}"
            )

        # 检查强制路由标签
        force_route = task_info.get("force_route")
        if force_route:
            resolved = _resolve_force_route(force_route)
            if resolved:
                request.model = resolved
                logger.info(f"[路由] 🏷️ 强制路由 [[{force_route}]] → {resolved}")
                print(f"🏷️ 强制路由: [[{force_route}]] → {resolved}")
            else:
                logger.warning(f"[路由] 🏷️ 强制路由 [[{force_route}]] 无法解析，降级到自动路由")

        # 如果强制路由未生效，走自动选择
        if not request.model or request.model == "auto":
            # 获取可用模型列表（本地 + 云端）
            available_models = []
            if engine_manager:
                available_models.extend(engine_manager.get_available_models())

            # 添加可用的云端模型
            if "glm" in cloud_backends:
                available_models.extend(["glm-5", "glm-4-flash"])
            if "deepseek" in cloud_backends:
                available_models.extend(["deepseek-v3", "deepseek-r1"])
            if "chatgpt" in cloud_backends:
                available_models.extend(["gpt-5.2", "gpt-5.1"])
            if "openai" in cloud_backends:
                available_models.extend(["gpt-4o"])
            if "gemini" in cloud_backends:
                available_models.extend(["gemini-2.5-flash", "gemini-2.5-pro"])

            print(f"🔍 可用模型: {', '.join(available_models)}")

            # 自动选择模型（带敏感度感知 + 负载感知）
            load_info = queue_manager.get_all_loads() if queue_manager else None
            selected_model = model_selector.select(
                task_info=task_info,
                agent_type=request.agent_type,
                available_models=available_models if available_models else None,
                optimize_for="balanced",
                load_info=load_info,
            )
            request.model = selected_model
            print(f"🎯 自动选择模型: {selected_model}")
            if sensitivity_level != "none":
                tolerance = model_selector.model_tolerance.get(selected_model, "unknown")
                logger.info(f"[路由] 敏感路由结果: {selected_model} (tolerance={tolerance})")

    # 3. 选择后端（本地 vs 云端）
    local_models = engine_manager.get_available_models() if engine_manager else []
    is_cloud_model = request.model not in local_models
    logger.info(f"[路由] model={request.model} | 本地模型={local_models} | 走云端={is_cloud_model}")

    # 4. 上下文适配 (本地 + 云端, F7: cloud auto-fit)
    if context_manager:
        messages, fit_meta = context_manager.auto_fit(
            messages=messages,
            model=request.model,
            reserve_tokens=request.max_tokens or 512,
        )
        if fit_meta.get("strategy") != "none":
            print(
                f"📦 上下文适配: {fit_meta.get('original_tokens')}→{fit_meta.get('compressed_tokens')} tokens "
                f"(策略: {fit_meta.get('strategy')})"
            )

    # F6: Semantic cache lookup (non-streaming, cloud only)
    if is_cloud_model and not request.stream and semantic_cache:
        last_user_msg_cache = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if last_user_msg_cache:
            cached = semantic_cache.lookup(last_user_msg_cache, request.model)
            if cached:
                logger.info(f"[完成] 语义缓存命中 | model={request.model}")
                return cached["response"]

    # 构建 handler
    if is_cloud_model:
        handler = lambda: _handle_cloud_request(request, messages)
    else:
        handler = lambda: _handle_local_request(request, messages)

    # 通过 QueueManager 调度 (如果可用)
    if queue_manager:
        # 计算消息总长度 (用于时长估算)
        msg_length = sum(len(m.get("content", "") or "") for m in messages)

        scheduled = ScheduledRequest(
            request_id=f"req-{int(time.time() * 1000)}",
            model=request.model,
            priority=request.priority or 1,
            agent_id=request.agent_id,
            agent_type=request.agent_type,
            duration_estimate=queue_manager.estimate_duration(
                task_info, msg_length, request.stream or False,
            ),
            is_stream=request.stream or False,
        )

        try:
            result = await queue_manager.submit(scheduled, handler)
            logger.info(
                f"[完成] 调度请求耗时 {time.time() - req_start:.2f}s | "
                f"model={request.model} cloud={is_cloud_model}"
            )
            return result
        except AdmissionError:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "message": "Too many requests, queue is full",
                        "type": "too_many_requests",
                    }
                },
            )
    else:
        # fallback: 无 QueueManager 时直接调用 (向后兼容)
        result = await handler()
        logger.info(
            f"[完成] 直接请求耗时 {time.time() - req_start:.2f}s | "
            f"model={request.model} cloud={is_cloud_model}"
        )
        return result


def _resolve_force_route(tag: str) -> Optional[str]:
    """解析强制路由标签到具体模型名

    支持:
        [[gemini]] → gemini-2.5-flash (或 gemini-2.5-pro)
        [[deepseek]] → deepseek-v3
        [[deepseek-r1]] → deepseek-r1
        [[glm]] → glm-5
        [[gpt]] → gpt-5.2 (chatgpt) 或 gpt-4o (openai)
        [[local]] → 本地模型
        [[gpt-5.2]] → gpt-5.2 (精确匹配)
    """
    tag = tag.lower().strip()

    # 精确模型名匹配
    exact_models = [
        "deepseek-r1", "deepseek-v3", "glm-5", "glm-4-flash", "glm-4-plus",
        "gpt-4o", "gpt-5.2", "gpt-5.1", "gemini-2.5-pro", "gemini-2.5-flash",
        "gemini-2-flash", "gemini-2-pro",
    ]
    if tag in exact_models:
        return tag

    # 别名映射 (模糊标签 → 默认模型)
    alias_map = {
        "gemini": "gemini-2.5-flash",
        "deepseek": "deepseek-v3",
        "ds": "deepseek-v3",
        "glm": "glm-5",
        "zhipu": "glm-5",
        "智谱": "glm-5",
        "gpt": "gpt-5.2",
        "openai": "gpt-4o",
        "chatgpt": "gpt-5.2",
        "local": None,  # 特殊处理
    }

    if tag in alias_map:
        if tag == "local":
            # 返回本地模型
            if engine_manager:
                local_models = engine_manager.get_available_models()
                return local_models[0] if local_models else None
            return None
        return alias_map[tag]

    logger.warning(f"[强制路由] 未知标签: [[{tag}]]")
    return None


async def _handle_local_request(request: OpenAIRequest, messages: List[Dict]):
    """处理本地请求（Continuous Batching）"""

    engine = engine_manager.get_engine(request.model)
    if not engine:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{request.model}' not found",
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
            _generate_stream_local(engine, gen_request, request.model),
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
                    "agent_id": request.agent_id,
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


async def _handle_cloud_request(request: OpenAIRequest, messages: List[Dict]):
    """处理云端请求 (使用 CloudDispatcher: retry + fallback + circuit breaker)"""
    logger.debug(f"[云端] 开始处理 model={request.model} | 可用后端={list(cloud_backends.keys())}")

    if not cloud_dispatcher:
        raise HTTPException(
            status_code=404,
            detail=f"Cloud model '{request.model}' not available (no cloud backends)",
        )

    # 创建生成请求
    gen_request = GenerationRequest(
        messages=messages,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        stream=request.stream,
    )

    # 流式请求
    if request.stream:
        try:
            stream, backend_name = await cloud_dispatcher.dispatch_stream(
                gen_request, request.model
            )
            logger.info(f"[云端] 流式请求 → backend={backend_name}")
            return StreamingResponse(
                _generate_stream_cloud_dispatched(
                    stream, request.model, backend_name, messages
                ),
                media_type="text/event-stream",
            )
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

    # 非流式请求
    call_start = time.time()
    try:
        response, backend_name = await cloud_dispatcher.dispatch(
            gen_request, request.model
        )
        call_time = time.time() - call_start
        logger.info(
            f"[云端] ✅ 响应成功 | model={request.model} backend={backend_name} | "
            f"耗时={call_time:.2f}s | "
            f"in={response.input_tokens} out={response.output_tokens} tokens"
        )
    except RuntimeError as e:
        call_time = time.time() - call_start
        logger.error(
            f"[云端] ❌ 所有后端失败 | model={request.model} | "
            f"耗时={call_time:.2f}s | 错误: {e}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"All backends exhausted for model '{request.model}': {str(e)[:200]}",
        )

    # Cloud Request Logging (F4 前置)
    if db_store:
        db_store.log_request({
            "model": request.model,
            "messages": messages,
            "priority": request.priority,
            "agent_type": request.agent_type,
            "agent_id": request.agent_id,
            "task_id": request.task_id,
            "response": {"content": response.content[:500]},
            "status": "success",
            "ttft": response.ttft,
            "total_time": call_time,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "metadata": {"backend": backend_name},
        })

    result = {
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

    # F6: Store in semantic cache (non-streaming only)
    if semantic_cache:
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if last_user_msg:
            semantic_cache.store(last_user_msg, request.model, result)

    return result


async def _generate_stream_local(engine, request: GenerationRequest, model: str):
    """本地流式生成"""
    async for chunk in engine.generate_stream(request):
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

    yield "data: [DONE]\n\n"


async def _generate_stream_cloud_dispatched(
    stream, model: str, backend_name: str, messages: List[Dict]
):
    """云端流式生成 (via CloudDispatcher, with request logging)"""
    chunks_collected = []
    stream_start = time.time()
    ttft = None

    try:
        async for chunk in stream:
            if ttft is None:
                ttft = time.time() - stream_start
            chunks_collected.append(chunk)
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

        yield "data: [DONE]\n\n"

        # Stream completed successfully
        if cloud_dispatcher:
            cloud_dispatcher.record_stream_success(backend_name)

        # Cloud Request Logging (stream: log after completion)
        total_time = time.time() - stream_start
        full_content = "".join(chunks_collected)
        if db_store:
            db_store.log_request({
                "model": model,
                "messages": messages,
                "response": {"content": full_content[:500]},
                "status": "success",
                "ttft": ttft,
                "total_time": total_time,
                "output_tokens": len(full_content) // 4,  # rough estimate
                "metadata": {"backend": backend_name, "stream": True},
            })

    except Exception as e:
        if cloud_dispatcher:
            cloud_dispatcher.record_stream_failure(backend_name)
        logger.error(f"[云端流式] ❌ 中断: {e}")
        # Log failed stream
        if db_store:
            db_store.log_request({
                "model": model,
                "messages": messages,
                "status": "error",
                "error": str(e)[:200],
                "total_time": time.time() - stream_start,
                "metadata": {"backend": backend_name, "stream": True},
            })
        raise


# ========== 统计接口 ==========


@app.get("/stats")
async def get_stats():
    """获取统计信息"""
    if not db_store:
        return {"error": "Database not initialized"}

    recent_requests = db_store.get_request_history(limit=100)

    stats = {
        "total_requests": len(recent_requests),
        "cb_scheduler": cb_scheduler.get_stats() if cb_scheduler else {},
    }

    return stats


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
