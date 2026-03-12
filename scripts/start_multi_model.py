#!/usr/bin/env python3
"""ClawGate Multi-Model Startup Script

启动多模型生命周期管理系统：
- 加载配置
- 初始化 ModelLifecycleManager
- 初始化 SmartModelRouter
- 初始化 MemoryMonitor
- 启动 Always-On 模型
- 启动内存监控

Usage:
    python scripts/start_multi_model.py [--config config/multi_model.yaml]
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Dict

import yaml

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawgate.models import (
    ModelLifecycleManager,
    ModelConfig,
    SmartModelRouter,
    MemoryMonitor,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> Dict:
    """加载配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.info(f"加载配置文件: {config_path}")
    return config


def create_model_configs(config: Dict) -> Dict[str, ModelConfig]:
    """创建模型配置

    Args:
        config: 配置字典

    Returns:
        模型配置字典
    """
    model_configs = {}

    for name, model_config in config["models"].items():
        model_configs[name] = ModelConfig(
            name=model_config["name"],
            model_path=model_config["model_path"],
            port=model_config["port"],
            mode=model_config["mode"],
            n_ctx=model_config.get("n_ctx", 8192),
            n_gpu_layers=model_config.get("n_gpu_layers", 99),
            ttl_seconds=model_config.get("ttl_seconds", 0),
            startup_timeout=model_config.get("startup_timeout", 60),
        )

    logger.info(f"创建了 {len(model_configs)} 个模型配置")
    return model_configs


async def start_multi_model_system(config_path: str = "config/multi_model.yaml"):
    """启动多模型系统

    Args:
        config_path: 配置文件路径
    """
    try:
        # 1. 加载配置
        logger.info("=" * 80)
        logger.info("ClawGate Multi-Model System 启动中...")
        logger.info("=" * 80)

        config = load_config(config_path)

        # 2. 创建模型配置
        model_configs = create_model_configs(config)

        # 3. 初始化 ModelLifecycleManager
        logger.info("\n初始化 ModelLifecycleManager...")
        lifecycle_manager = ModelLifecycleManager(model_configs)

        # 4. 初始化 SmartModelRouter
        logger.info("初始化 SmartModelRouter...")
        routing_config = config.get("routing", {}).get("task_to_model", {})
        smart_router = SmartModelRouter(lifecycle_manager, routing_config)

        # 5. 初始化 MemoryMonitor
        logger.info("初始化 MemoryMonitor...")
        memory_config = config.get("memory_monitor", {})
        memory_monitor = None

        if memory_config.get("enabled", True):
            memory_monitor = MemoryMonitor(
                lifecycle_manager=lifecycle_manager,
                threshold_gb=memory_config.get("threshold_gb", 42.0),
                check_interval_sec=memory_config.get("check_interval_sec", 60),
                enabled=True,
            )

        # 6. 启动 Always-On 模型
        logger.info("\n" + "=" * 80)
        logger.info("启动 Always-On 模型...")
        logger.info("=" * 80)

        if config.get("startup", {}).get("preload_always_on", True):
            # 顺序启动（避免内存峰值）
            if config.get("startup", {}).get("sequential_startup", True):
                delay = config.get("startup", {}).get("startup_delay_sec", 5)
                for name, model_config in model_configs.items():
                    if model_config.mode == "always_on":
                        logger.info(f"\n启动模型: {name}")
                        await lifecycle_manager.get_model(name)
                        if delay > 0:
                            logger.info(f"等待 {delay} 秒后启动下一个模型...")
                            await asyncio.sleep(delay)
            else:
                # 并行启动
                await lifecycle_manager.start_always_on_models()
        else:
            logger.info("跳过 Always-On 模型预加载（配置禁用）")

        # 7. 启动内存监控
        if memory_monitor:
            logger.info("\n" + "=" * 80)
            logger.info("启动内存监控...")
            logger.info("=" * 80)
            await memory_monitor.start()

        # 8. 显示系统状态
        logger.info("\n" + "=" * 80)
        logger.info("系统状态")
        logger.info("=" * 80)

        stats = lifecycle_manager.get_stats()
        logger.info(f"\n已加载模型数: {stats['loaded_count']} / {stats['total_models']}")

        for model_info in stats["loaded_models"]:
            logger.info(
                f"  - {model_info['name']}: "
                f"mode={model_info['mode']}, "
                f"port={model_info['port']}, "
                f"pid={model_info['pid']}, "
                f"idle={model_info['idle_time']:.0f}s"
            )

        routing_table = smart_router.get_routing_table()
        logger.info("\n路由配置:")
        for task, model in routing_table["routing_config"].items():
            logger.info(f"  - {task} → {model}")

        if memory_monitor:
            mem_stats = memory_monitor.get_memory_stats()
            if "error" not in mem_stats:
                logger.info(
                    f"\n内存使用: "
                    f"{mem_stats['used_gb']:.1f}GB / {mem_stats['total_gb']:.1f}GB "
                    f"({mem_stats['percent']:.1f}%)"
                )
                logger.info(
                    f"内存阈值: {mem_stats['threshold_gb']:.1f}GB "
                    f"({'超限' if mem_stats['over_threshold'] else '正常'})"
                )

        # 9. 保持运行
        logger.info("\n" + "=" * 80)
        logger.info("多模型系统启动完成！")
        logger.info("按 Ctrl+C 停止")
        logger.info("=" * 80)

        # 保持运行，等待信号
        try:
            while True:
                await asyncio.sleep(60)

                # 定期显示状态
                if logger.level <= logging.DEBUG:
                    stats = lifecycle_manager.get_stats()
                    logger.debug(f"已加载模型: {stats['loaded_count']}")

                    if memory_monitor:
                        mem_stats = memory_monitor.get_memory_stats()
                        if "error" not in mem_stats:
                            logger.debug(
                                f"内存: {mem_stats['used_gb']:.1f}GB / "
                                f"{mem_stats['total_gb']:.1f}GB"
                            )

        except KeyboardInterrupt:
            logger.info("\n\n收到停止信号...")

    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        raise

    finally:
        # 清理资源
        logger.info("\n关闭多模型系统...")

        if memory_monitor:
            await memory_monitor.stop()

        await lifecycle_manager.shutdown_all()

        logger.info("系统已关闭")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="ClawGate Multi-Model System Startup Script"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/multi_model.yaml",
        help="配置文件路径（默认: config/multi_model.yaml）",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别（默认: INFO）",
    )

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # 运行系统
    asyncio.run(start_multi_model_system(args.config))


if __name__ == "__main__":
    main()
