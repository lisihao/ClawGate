"""Model Selector - 模型选择器"""

from typing import List, Dict, Optional
from pathlib import Path
import yaml
import logging

logger = logging.getLogger("clawgate.router.selector")


class ModelSelector:
    """模型选择器 - Quality-Cost 权衡优化"""

    def __init__(self, config_path: str = "config/models.yaml"):
        # 加载配置
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                self.config = yaml.safe_load(f)
        else:
            self.config = {}

        # 模型配置
        self.cloud_models = {
            model["name"]: model for model in self.config.get("cloud_models", [])
        }

        # Agent 配置
        self.agent_profiles = self.config.get("agent_profiles", {})

        # 默认模型排序（质量优先）
        self.quality_ranking = [
            "deepseek-r1",
            "gpt-4o",
            "deepseek-v3",
            "glm-5",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "glm-4-flash",
        ]

        # 成本排序（成本优先）
        self.cost_ranking = [
            "glm-4-flash",
            "gemini-2.5-flash",
            "deepseek-v3",
            "glm-5",
            "deepseek-r1",
            "gemini-2.5-pro",
            "gpt-4o",
        ]

        # 模型内容宽容度 (content tolerance)
        # lenient = 宽松, moderate = 中等, strict = 严格
        # 敏感内容应路由到宽松模型, 避免严格模型拒绝回答
        self.model_tolerance = {
            # 本地模型: 无审查，最宽松
            "qwen-1.7b": "lenient",
            # Gemini: 相对宽松
            "gemini-2.5-pro": "lenient",
            "gemini-2.5-flash": "lenient",
            "gemini-2-flash": "lenient",
            "gemini-2-pro": "lenient",
            # GPT: 中等
            "gpt-4o": "moderate",
            "gpt-5.2": "moderate",
            "gpt-5.1": "moderate",
            # DeepSeek: 严格 (中国模型, NSFW/政治敏感)
            "deepseek-r1": "strict",
            "deepseek-v3": "strict",
            # GLM: 非常严格 (中国模型, 审查最严)
            "glm-5": "strict",
            "glm-4-plus": "strict",
            "glm-4-flash": "strict",
        }

        # 每个宽容度等级能处理的敏感内容类型
        self.tolerance_capabilities = {
            "lenient": ["nsfw", "violence", "politics", "drugs"],  # 几乎都能处理
            "moderate": ["drugs", "violence"],  # 部分可以
            "strict": [],  # 基本都拒绝
        }

    def select(
        self,
        task_info: Dict,
        agent_type: Optional[str] = None,
        available_models: Optional[List[str]] = None,
        optimize_for: str = "quality",
        load_info: Optional[Dict[str, dict]] = None,
    ) -> str:
        """
        选择最优模型

        Args:
            task_info: 任务信息（来自 TaskClassifier）
            agent_type: Agent 类型 (judge/builder/flash)
            available_models: 可用模型列表
            optimize_for: 优化目标 ("quality" / "cost" / "balanced")
            load_info: 模型负载信息 (来自 QueueManager.get_all_loads())

        Returns:
            模型名称
        """
        logger.info(
            f"[选择] 开始选模型 | task={task_info} | agent={agent_type} "
            f"| optimize={optimize_for} | 可用={available_models}"
        )

        # 0. 敏感内容检查 - 过滤不适合的模型
        sensitivity = task_info.get("sensitivity", {})
        sensitivity_level = sensitivity.get("level", "none")
        sensitivity_categories = sensitivity.get("categories", [])

        if sensitivity_level != "none" and available_models:
            filtered = self._filter_by_tolerance(available_models, sensitivity_categories)
            if filtered:
                logger.warning(
                    f"[选择] ⚠️ 敏感内容检出! level={sensitivity_level} "
                    f"categories={sensitivity_categories} | "
                    f"过滤前={available_models} → 过滤后={filtered}"
                )
                available_models = filtered
            else:
                logger.warning(
                    f"[选择] ⚠️ 敏感内容检出但无宽容模型可用! "
                    f"保持原列表={available_models}"
                )

        # 1. 负载感知重排序 (跳过熔断模型, 降低高负载模型优先级)
        if load_info and available_models:
            available_models = self._rerank_by_load(available_models, load_info)
            logger.debug(f"[选择] 负载重排序后: {available_models}")

        # 2. 如果指定了 Agent 类型，使用 Agent 配置
        if agent_type and agent_type in self.agent_profiles:
            preferred_models = self.agent_profiles[agent_type].get("preferred_models", [])
            logger.debug(f"[选择] Agent={agent_type} 偏好模型: {preferred_models}")
            # 选择第一个可用的模型
            for model in preferred_models:
                if not available_models or model in available_models:
                    logger.info(f"[选择] ✅ Agent模式命中: {model} (agent={agent_type})")
                    return model
            logger.warning(f"[选择] Agent={agent_type} 偏好模型均不可用，降级到任务路由")

        # 2. 根据任务类型选择
        task_type = task_info.get("task_type", "qa")
        complexity = task_info.get("complexity", "medium")

        # 3. 敏感内容优先路由 (high level 时绕过常规策略)
        if sensitivity_level == "high" and available_models:
            selected = self._select_for_sensitive(available_models, sensitivity_categories)
            if selected:
                logger.info(
                    f"[选择] ✅ 敏感路由: {selected} | "
                    f"tolerance={self.model_tolerance.get(selected, 'unknown')} | "
                    f"categories={sensitivity_categories}"
                )
                return selected

        # 4. 根据优化目标选择
        if optimize_for == "quality":
            selected = self._select_by_quality(task_type, complexity, available_models)
        elif optimize_for == "cost":
            selected = self._select_by_cost(task_type, complexity, available_models)
        else:  # balanced
            selected = self._select_balanced(task_type, complexity, available_models)

        logger.info(
            f"[选择] ✅ 最终选择: {selected} | 策略={optimize_for} "
            f"| task={task_type} complexity={complexity}"
        )
        return selected

    def _rerank_by_load(self, models: List[str], load_info: Dict[str, dict]) -> List[str]:
        """按负载重排序: 跳过熔断模型, 降低高负载模型优先级"""
        scored = []
        for model in models:
            info = load_info.get(model, {})
            # 熔断的模型直接跳过
            if info.get("circuit_state") == "open":
                logger.debug(f"[选择] 跳过熔断模型: {model}")
                continue
            # 负载得分: queue_depth * 2 + in_flight (越低越好)
            penalty = info.get("queue_depth", 0) * 2 + info.get("in_flight", 0)
            scored.append((model, penalty))

        if not scored:
            return models  # 全熔断时保留原列表

        scored.sort(key=lambda x: x[1])
        return [m for m, _ in scored]

    def _filter_by_tolerance(self, models: List[str], categories: List[str]) -> List[str]:
        """过滤掉无法处理敏感内容的模型"""
        result = []
        for model in models:
            tolerance = self.model_tolerance.get(model, "moderate")
            capabilities = self.tolerance_capabilities.get(tolerance, [])
            # 模型能处理所有检出的敏感类别
            if all(cat in capabilities for cat in categories):
                result.append(model)
        return result

    def _select_for_sensitive(self, models: List[str], categories: List[str]) -> Optional[str]:
        """为敏感内容选择最合适的模型 (优先宽松模型)"""
        # 按宽容度排序: lenient > moderate > strict
        tolerance_order = {"lenient": 0, "moderate": 1, "strict": 2}

        scored = []
        for model in models:
            tolerance = self.model_tolerance.get(model, "moderate")
            capabilities = self.tolerance_capabilities.get(tolerance, [])
            can_handle = all(cat in capabilities for cat in categories)
            score = tolerance_order.get(tolerance, 1)
            scored.append((model, score, can_handle))

        # 先选能处理的，再按宽容度排序
        capable = [(m, s) for m, s, c in scored if c]
        if capable:
            capable.sort(key=lambda x: x[1])
            return capable[0][0]

        # 都不能处理，返回最宽松的
        scored.sort(key=lambda x: x[1])
        return scored[0][0] if scored else None

    def _select_by_quality(
        self, task_type: str, complexity: str, available_models: Optional[List[str]]
    ) -> str:
        """质量优先选择"""

        # 高复杂度任务 → 高质量模型
        if complexity == "high":
            candidates = ["deepseek-r1", "gpt-4o", "gemini-2.5-pro"]
        elif complexity == "medium":
            candidates = ["deepseek-v3", "glm-5", "gemini-2.5-pro"]
        else:
            candidates = ["gemini-2.5-flash", "glm-4-flash"]

        # 选择第一个可用的
        for model in candidates:
            if not available_models or model in available_models:
                return model

        # 降级：如果没有匹配的候选模型，从可用模型中选择第一个
        if available_models:
            return available_models[0]

        # 最终降级到默认
        return "gemini-2.5-flash"

    def _select_by_cost(
        self, task_type: str, complexity: str, available_models: Optional[List[str]]
    ) -> str:
        """成本优先选择"""

        # 低复杂度 → 低成本模型
        if complexity == "low":
            candidates = ["glm-4-flash", "gemini-2.5-flash"]
        elif complexity == "medium":
            candidates = ["gemini-2.5-flash", "glm-5", "deepseek-v3"]
        else:
            candidates = ["glm-5", "deepseek-v3", "deepseek-r1"]

        # 选择第一个可用的
        for model in candidates:
            if not available_models or model in available_models:
                return model

        # 降级：如果没有匹配的候选模型，从可用模型中选择第一个
        if available_models:
            return available_models[0]

        return "glm-4-flash"

    def _select_balanced(
        self, task_type: str, complexity: str, available_models: Optional[List[str]]
    ) -> str:
        """平衡选择"""

        # 推理任务 → 高质量
        if task_type == "reasoning":
            return self._select_by_quality(task_type, complexity, available_models)
        # 简单任务 → 低成本
        elif complexity == "low":
            return self._select_by_cost(task_type, complexity, available_models)
        # 其他 → 中等模型
        else:
            candidates = ["gemini-2.5-flash", "glm-5", "deepseek-v3"]
            for model in candidates:
                if not available_models or model in available_models:
                    return model

            # 降级：如果没有匹配的候选模型，从可用模型中选择第一个
            if available_models:
                return available_models[0]

            return "gemini-2.5-flash"

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        估算成本

        Args:
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数

        Returns:
            成本（美元）
        """
        if model not in self.cloud_models:
            return 0.0

        model_config = self.cloud_models[model]
        cost_per_1k = model_config.get("cost_per_1k", 0.001)

        total_tokens = input_tokens + output_tokens
        return (total_tokens / 1000) * cost_per_1k
