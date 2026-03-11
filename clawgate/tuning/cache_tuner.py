"""Cache Tuning - 自动缓存调优

从 ThunderLLAMA thunder_service.py 迁移，用于 ClawGate 的 ThunderLLAMA Engine。

核心功能：
- 基于24h性能数据自动调整 cache_ram_mb
- 启发式评分：50% 吞吐量 + 35% 延迟 + 15% 可靠性
- 冷却机制：避免频繁切换
- 最小改进阈值：只有显著提升才切换

参考: ThunderLLAMA/tools/thunder-service/thunder_service.py L779-931
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CacheTuner(ABC):
    """缓存调优器基类"""

    @abstractmethod
    async def recommend_cache_size(
        self,
        metrics: List[Dict[str, Any]],
        current_cache_mb: Optional[int] = None
    ) -> Optional[int]:
        """
        基于性能数据推荐缓存大小

        Args:
            metrics: 性能指标列表（每条包含 cache_ram_mb, throughput_rps, avg_latency_ms, failure_rate, total 等字段）
            current_cache_mb: 当前缓存大小（MB）

        Returns:
            推荐的缓存大小（MB），如果无法推荐则返回 None
        """
        pass


class HeuristicCacheTuner(CacheTuner):
    """启发式缓存调优器

    基于24h性能数据的启发式评分算法：
    - 评分公式：50% 吞吐量 + 35% 延迟 + 15% 可靠性
    - 选择评分最高的缓存大小
    - 考虑冷却期和最小改进阈值，避免频繁切换

    从 ThunderLLAMA thunder_service.py AutoCacheRamTuner 迁移
    """

    def __init__(
        self,
        candidates_mb: List[int] = None,
        lookback_sec: int = 86400,  # 24小时
        min_samples: int = 20,
        cooling_period_sec: int = 300,  # 5分钟
        min_improve_score: float = 0.05  # 5% 改进阈值
    ):
        """
        初始化启发式调优器

        Args:
            candidates_mb: 候选缓存大小列表（MB），默认 [2048, 4096, 6144, 8192]
            lookback_sec: 回溯时间窗口（秒），默认 24 小时
            min_samples: 最少样本数，默认 20
            cooling_period_sec: 冷却期（秒），避免频繁切换
            min_improve_score: 最小改进阈值，只有评分提升超过此值才切换
        """
        self.candidates_mb = candidates_mb or [2048, 4096, 6144, 8192]
        self.candidates_mb = sorted(set(x for x in self.candidates_mb if x > 0))
        self.lookback_sec = max(300, lookback_sec)  # 最少 5 分钟
        self.min_samples = max(1, min_samples)
        self.cooling_period_sec = max(60, cooling_period_sec)  # 最少 1 分钟
        self.min_improve_score = max(0.0, min_improve_score)

        self.last_switch_time: Optional[float] = None
        self.last_decision: Dict[str, Any] = {"status": "idle"}

        logger.info(
            f"HeuristicCacheTuner 初始化: candidates={self.candidates_mb}, "
            f"lookback={self.lookback_sec}s, min_samples={self.min_samples}, "
            f"cooling={self.cooling_period_sec}s, min_improve={self.min_improve_score}"
        )

    @staticmethod
    def _normalize(values: List[float]) -> List[float]:
        """归一化数值到 [0, 1] 区间"""
        if not values:
            return []

        lo = min(values)
        hi = max(values)

        if hi - lo < 1e-9:
            return [0.5 for _ in values]

        return [(v - lo) / (hi - lo) for v in values]

    def _calculate_scores(
        self,
        metrics: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        计算每个缓存大小的评分

        评分公式：50% 吞吐量 + 35% (1 - 延迟) + 15% (1 - 失败率)

        Args:
            metrics: 性能指标列表

        Returns:
            带有 score 字段的指标列表
        """
        # 过滤掉样本数不足的候选
        valid = [m for m in metrics if int(m.get("total", 0)) >= self.min_samples]

        if not valid:
            logger.warning(
                f"没有足够样本数的候选（min_samples={self.min_samples}），"
                f"总候选数: {len(metrics)}"
            )
            return []

        # 提取指标并归一化
        throughputs = self._normalize([float(m.get("throughput_rps", 0.0)) for m in valid])
        latencies = self._normalize([float(m.get("avg_latency_ms", 0.0)) for m in valid])
        failure_rates = self._normalize([float(m.get("failure_rate", 0.0)) for m in valid])

        # 计算评分
        for i, m in enumerate(valid):
            score = (
                0.50 * throughputs[i] +           # 50% 吞吐量（越高越好）
                0.35 * (1.0 - latencies[i]) +     # 35% 延迟（越低越好）
                0.15 * (1.0 - failure_rates[i])   # 15% 失败率（越低越好）
            )
            m["score"] = score

            logger.debug(
                f"Cache {m.get('cache_ram_mb')}MB: "
                f"throughput={throughputs[i]:.3f}, latency={latencies[i]:.3f}, "
                f"failure={failure_rates[i]:.3f} → score={score:.3f}"
            )

        return valid

    async def recommend_cache_size(
        self,
        metrics: List[Dict[str, Any]],
        current_cache_mb: Optional[int] = None
    ) -> Optional[int]:
        """
        推荐缓存大小

        Args:
            metrics: 性能指标列表
            current_cache_mb: 当前缓存大小（MB）

        Returns:
            推荐的缓存大小（MB），如果无法推荐则返回 None
        """
        now = time.time()

        # 计算评分
        scored = self._calculate_scores(metrics)

        if not scored:
            self.last_decision = {
                "status": "insufficient_samples",
                "at": now,
                "reason": f"样本数不足（需要至少 {self.min_samples} 条）"
            }
            logger.warning(self.last_decision["reason"])
            return None

        # 选择评分最高的候选
        best = max(scored, key=lambda x: float(x.get("score", 0.0)))
        target_cache_mb = int(best["cache_ram_mb"])
        target_score = float(best["score"])

        # 构建决策信息
        decision: Dict[str, Any] = {
            "status": "analyzed",
            "at": now,
            "current_cache_mb": current_cache_mb,
            "target_cache_mb": target_cache_mb,
            "target_score": target_score,
            "scored_candidates": scored
        }

        # 检查是否已经是最优
        if current_cache_mb == target_cache_mb:
            decision["recommendation"] = "keep_current"
            decision["reason"] = "已经是最优配置"
            self.last_decision = decision
            logger.info(
                f"Cache 调优: 当前 {current_cache_mb}MB 已是最优（评分: {target_score:.3f}）"
            )
            return None

        # 检查冷却期
        if self.last_switch_time is not None:
            elapsed = now - self.last_switch_time
            if elapsed < self.cooling_period_sec:
                decision["recommendation"] = "wait_cooling"
                decision["reason"] = f"冷却期内（已过 {elapsed:.0f}s / {self.cooling_period_sec}s）"
                decision["remaining_cooling_sec"] = self.cooling_period_sec - elapsed
                self.last_decision = decision
                logger.debug(decision["reason"])
                return None

        # 检查改进幅度
        if current_cache_mb is not None:
            score_by_cache = {int(m["cache_ram_mb"]): float(m["score"]) for m in scored}
            current_score = score_by_cache.get(current_cache_mb, 0.0)
            score_delta = target_score - current_score

            if score_delta < self.min_improve_score:
                decision["recommendation"] = "keep_current"
                decision["reason"] = (
                    f"改进幅度不足（{score_delta:.3f} < {self.min_improve_score}）"
                )
                decision["current_score"] = current_score
                decision["score_delta"] = score_delta
                self.last_decision = decision
                logger.info(
                    f"Cache 调优: 改进幅度不足，保持 {current_cache_mb}MB "
                    f"(当前: {current_score:.3f}, 目标: {target_score:.3f}, 差值: {score_delta:.3f})"
                )
                return None

        # 推荐切换
        decision["recommendation"] = "switch"
        decision["reason"] = f"24h 数据显示 {target_cache_mb}MB 性能更优（评分: {target_score:.3f}）"
        self.last_decision = decision

        logger.info(
            f"Cache 调优: 推荐从 {current_cache_mb}MB 切换到 {target_cache_mb}MB "
            f"（评分提升: {target_score:.3f}）"
        )

        return target_cache_mb

    def record_switch(self, new_cache_mb: int) -> None:
        """记录缓存切换时间"""
        self.last_switch_time = time.time()
        logger.info(f"Cache 调优: 已切换到 {new_cache_mb}MB，进入冷却期（{self.cooling_period_sec}s）")

    def get_last_decision(self) -> Dict[str, Any]:
        """获取最后一次决策信息"""
        return self.last_decision.copy()

    def get_stats(self) -> Dict[str, Any]:
        """获取调优器统计信息"""
        return {
            "candidates_mb": self.candidates_mb,
            "lookback_sec": self.lookback_sec,
            "min_samples": self.min_samples,
            "cooling_period_sec": self.cooling_period_sec,
            "min_improve_score": self.min_improve_score,
            "last_switch_time": self.last_switch_time,
            "last_decision": self.last_decision
        }


class BayesianCacheTuner(CacheTuner):
    """贝叶斯优化调优器（未来实现）

    使用 Gaussian Process 自动搜索最优缓存大小。
    相比启发式调优器，能更快找到全局最优解。

    TODO: Phase 2 Week 2 实现
    """

    def __init__(self):
        logger.warning("BayesianCacheTuner 尚未实现，请使用 HeuristicCacheTuner")
        raise NotImplementedError("BayesianCacheTuner 尚未实现")

    async def recommend_cache_size(
        self,
        metrics: List[Dict[str, Any]],
        current_cache_mb: Optional[int] = None
    ) -> Optional[int]:
        raise NotImplementedError("BayesianCacheTuner 尚未实现")
