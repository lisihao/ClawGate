"""Multi-Model Management - 多模型生命周期管理

支持混合模式：
- Always-On: 主模型（30B）常驻内存
- On-Demand + TTL: 辅助模型（1.7B, 0.6B）按需加载，空闲卸载
"""

from .lifecycle_manager import ModelLifecycleManager, ModelConfig, ModelInstance
from .smart_router import SmartModelRouter
from .memory_monitor import MemoryMonitor

__all__ = [
    "ModelLifecycleManager",
    "ModelConfig",
    "ModelInstance",
    "SmartModelRouter",
    "MemoryMonitor",
]
