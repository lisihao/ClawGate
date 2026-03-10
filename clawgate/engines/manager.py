"""引擎管理器 - 自动检测平台并初始化引擎"""

import platform
import yaml
from typing import Dict, Optional, List
from pathlib import Path

from .base import BaseEngine
from .mlx_engine import MLXEngine, MLX_AVAILABLE
from .llamacpp_engine import LlamaCppEngine, LLAMACPP_AVAILABLE


class EngineManager:
    """引擎管理器"""

    def __init__(self, config_path: str = "config/engines.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.engines: Dict[str, BaseEngine] = {}

        # 自动选择并初始化引擎
        if self.config.get("auto_select", True):
            self._auto_initialize()

    def _load_config(self) -> Dict:
        """加载配置"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Engine config not found: {self.config_path}"
            )

        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def _auto_initialize(self):
        """根据平台自动初始化引擎"""

        system = platform.system().lower()
        machine = platform.machine().lower()

        # 确定平台
        if system == "darwin" and "arm" in machine:
            platform_key = "darwin_arm64"  # Apple Silicon
        elif system == "darwin":
            platform_key = "darwin_x86_64"  # Mac Intel
        elif system == "linux":
            platform_key = "linux"
        else:
            platform_key = "windows"

        print(f"🖥️  检测到平台: {platform_key}")

        # 按优先级初始化引擎
        priority = self.config["platform_priority"].get(
            platform_key, ["llamacpp"]
        )

        success = False
        for engine_type in priority:
            if self._try_initialize_engine(engine_type):
                print(f"✅ 成功初始化引擎: {engine_type}")
                success = True
                # 不 break，继续加载其他可用引擎

        if not success:
            raise RuntimeError("无法初始化任何引擎！请检查配置和依赖。")

    def _try_initialize_engine(self, engine_type: str) -> bool:
        """尝试初始化引擎"""

        engine_config = self.config.get(engine_type, {})

        if not engine_config.get("enabled", False):
            print(f"⏭️  跳过禁用的引擎: {engine_type}")
            return False

        try:
            if engine_type == "mlx":
                return self._init_mlx_engines(engine_config)
            elif engine_type == "llamacpp":
                return self._init_llamacpp_engines(engine_config)
            elif engine_type in ["vllm", "sglang"]:
                print(f"ℹ️  {engine_type.upper()} 接口已预留，暂未实现")
                return False
            else:
                print(f"⚠️  未知引擎类型: {engine_type}")
                return False

        except Exception as e:
            print(f"❌ 初始化 {engine_type} 失败: {e}")
            return False

    def _init_mlx_engines(self, config: Dict) -> bool:
        """初始化 MLX 引擎"""

        if not MLX_AVAILABLE:
            print("⏭️  MLX 不可用（需要 Apple Silicon + mlx-lm）")
            return False

        # 检查平台
        if platform.system() != "Darwin" or "arm" not in platform.machine():
            print("⏭️  MLX 仅支持 Apple Silicon")
            return False

        # 加载所有 MLX 模型
        loaded_count = 0
        for model_config in config.get("models", []):
            model_path = model_config["path"]

            # 检查模型路径是否存在
            if not Path(model_path).exists():
                print(f"⚠️  模型路径不存在，跳过: {model_path}")
                continue

            try:
                engine = MLXEngine(
                    model_path=model_path,
                    max_tokens=model_config.get("max_tokens", 2048),
                    temperature=model_config.get("temperature", 0.7),
                )
                self.engines[model_config["name"]] = engine
                loaded_count += 1
                print(f"  ✓ 加载 MLX 模型: {model_config['name']}")
            except Exception as e:
                print(f"  ✗ 加载失败 {model_config['name']}: {e}")

        return loaded_count > 0

    def _init_llamacpp_engines(self, config: Dict) -> bool:
        """初始化 llama.cpp 引擎"""

        if not LLAMACPP_AVAILABLE:
            print("⏭️  llama-cpp-python 不可用")
            return False

        # 加载所有 llama.cpp 模型
        loaded_count = 0
        for model_config in config.get("models", []):
            model_path = model_config["path"]

            # 检查模型路径是否存在
            if not Path(model_path).exists():
                print(f"⚠️  模型路径不存在，跳过: {model_path}")
                continue

            try:
                engine = LlamaCppEngine(
                    model_path=model_path,
                    n_ctx=config.get("n_ctx", 32768),
                    n_gpu_layers=config.get("n_gpu_layers", -1),
                    n_threads=config.get("n_threads", 8),
                )
                self.engines[model_config["name"]] = engine
                loaded_count += 1
                print(f"  ✓ 加载 llama.cpp 模型: {model_config['name']}")
            except Exception as e:
                print(f"  ✗ 加载失败 {model_config['name']}: {e}")

        return loaded_count > 0

    def get_engine(self, model_name: str) -> Optional[BaseEngine]:
        """获取引擎实例"""
        return self.engines.get(model_name)

    def list_engines(self) -> Dict[str, Dict]:
        """列出所有可用引擎"""
        return {name: engine.get_stats() for name, engine in self.engines.items()}

    def get_available_models(self) -> List[str]:
        """获取所有可用模型名称"""
        return list(self.engines.keys())

    def __repr__(self):
        return f"EngineManager(models={len(self.engines)})"
