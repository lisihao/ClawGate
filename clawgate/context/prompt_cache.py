"""Prompt Cache - 热/温两层缓存

从 ThunderLLAMA thunder_service.py 迁移，用于 ClawGate 的请求缓存。

两层架构：
- 热缓存（Hot Cache）：内存 LRU，TTL=1h，最多 256 条，命中快
- 温缓存（Warm Cache）：磁盘 JSON，TTL=24h，容量大，持久化

提升机制：
- 温缓存命中次数达到阈值后提升到热缓存

参考: ThunderLLAMA/tools/thunder-service/thunder_service.py L934-1099
"""

import hashlib
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PromptCacheManager:
    """热/温两层 Prompt 缓存管理器

    热缓存: 内存 LRU，TTL=1h，最多 256 条
    温缓存: 磁盘 JSON，TTL=24h
    """

    def __init__(
        self,
        enabled: bool = True,
        hot_cache_size: int = 256,
        hot_ttl_sec: int = 3600,  # 1 小时
        warm_cache_dir: str = ".solar/prompt-cache/warm",
        warm_ttl_sec: int = 86400,  # 24 小时
        hot_hit_threshold: int = 3,  # 温缓存命中 3 次后提升到热缓存
        prune_interval_sec: int = 600  # 10 分钟清理一次过期条目
    ):
        """
        初始化 Prompt 缓存管理器

        Args:
            enabled: 是否启用缓存
            hot_cache_size: 热缓存最大条目数
            hot_ttl_sec: 热缓存 TTL（秒）
            warm_cache_dir: 温缓存目录
            warm_ttl_sec: 温缓存 TTL（秒）
            hot_hit_threshold: 温缓存命中多少次后提升到热缓存
            prune_interval_sec: 清理过期条目的间隔（秒）
        """
        self.enabled = enabled
        self.hot_cache_size = max(1, hot_cache_size)
        self.hot_ttl_sec = max(30, hot_ttl_sec)
        self.warm_ttl_sec = max(self.hot_ttl_sec, warm_ttl_sec)
        self.hot_hit_threshold = max(1, hot_hit_threshold)
        self.prune_interval_sec = max(30, prune_interval_sec)

        self.warm_cache_dir = Path(warm_cache_dir).expanduser()

        # 热缓存：OrderedDict 实现 LRU
        self.hot_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()

        # 统计信息
        self.stats = {
            "hit_hot": 0,
            "hit_warm": 0,
            "miss": 0,
            "store": 0,
            "evict_hot": 0,
            "evict_warm": 0
        }

        # 创建温缓存目录
        if self.enabled:
            self.warm_cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"PromptCacheManager 初始化: hot_size={self.hot_cache_size}, "
                f"hot_ttl={self.hot_ttl_sec}s, warm_ttl={self.warm_ttl_sec}s, "
                f"warm_dir={self.warm_cache_dir}"
            )

    @staticmethod
    def is_cacheable(payload: Dict[str, Any]) -> bool:
        """判断请求是否可缓存

        只有确定性请求才缓存（temperature=0, stream=False, n=1）
        """
        # 流式请求不缓存
        if payload.get("stream") is True:
            return False

        # n > 1 不缓存（生成多个结果）
        if int(payload.get("n", 1) or 1) != 1:
            return False

        # temperature != 0 不缓存（非确定性）
        temp = payload.get("temperature", 0)
        try:
            return float(temp) == 0.0
        except Exception:
            return False

    @staticmethod
    def _stable_payload_for_key(
        payload: Dict[str, Any],
        messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """提取稳定的 payload 字段用于生成缓存键"""
        return {
            "model": payload.get("model"),
            "messages": messages,
            "max_tokens": payload.get("max_tokens"),
            "temperature": payload.get("temperature", 0),
            "top_p": payload.get("top_p"),
            "presence_penalty": payload.get("presence_penalty"),
            "frequency_penalty": payload.get("frequency_penalty"),
            "stop": payload.get("stop"),
            "response_format": payload.get("response_format"),
            "tools": payload.get("tools"),
            "tool_choice": payload.get("tool_choice"),
        }

    def make_key(
        self,
        payload: Dict[str, Any],
        messages: List[Dict[str, str]]
    ) -> str:
        """生成缓存键（SHA256 hash）"""
        stable = self._stable_payload_for_key(payload, messages)
        raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _warm_path(self, key: str) -> Path:
        """获取温缓存文件路径"""
        return self.warm_cache_dir / f"{key}.json"

    @staticmethod
    def _now() -> int:
        """获取当前时间戳（秒）"""
        return int(time.time())

    def _is_expired(self, entry: Dict[str, Any], ttl_sec: int) -> bool:
        """判断缓存条目是否过期"""
        ts = int(entry.get("last_access", entry.get("created_at", 0)) or 0)
        return (self._now() - ts) > ttl_sec

    def _promote_to_hot(self, key: str, entry: Dict[str, Any]) -> None:
        """将温缓存条目提升到热缓存"""
        if int(entry.get("hit_count", 0)) < self.hot_hit_threshold:
            return

        logger.debug(f"提升到热缓存: {key[:8]}... (命中 {entry['hit_count']} 次)")

        # 添加到热缓存
        self.hot_cache[key] = dict(entry)
        # 移动到末尾（最近使用）
        self.hot_cache.move_to_end(key)

        # LRU 淘汰
        if len(self.hot_cache) > self.hot_cache_size:
            oldest_key, _ = self.hot_cache.popitem(last=False)
            self.stats["evict_hot"] += 1
            logger.debug(f"LRU 淘汰热缓存: {oldest_key[:8]}...")

    def get(self, key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        获取缓存

        Returns:
            (response, cache_type): 响应数据和缓存类型（"hot" | "warm" | None）
        """
        if not self.enabled:
            return None, None

        now = self._now()

        # 1. 检查热缓存
        hot_entry = self.hot_cache.get(key)
        if hot_entry:
            if not self._is_expired(hot_entry, self.hot_ttl_sec):
                # 命中热缓存
                hot_entry["last_access"] = now
                hot_entry["hit_count"] = int(hot_entry.get("hit_count", 0)) + 1
                self.hot_cache.move_to_end(key)  # 移动到末尾（最近使用）
                self.stats["hit_hot"] += 1

                response = hot_entry.get("response")
                logger.debug(f"热缓存命中: {key[:8]}... (命中 {hot_entry['hit_count']} 次)")
                return response, "hot"
            else:
                # 过期，删除
                del self.hot_cache[key]
                logger.debug(f"热缓存过期: {key[:8]}...")

        # 2. 检查温缓存
        warm_path = self._warm_path(key)
        if warm_path.exists():
            try:
                with warm_path.open("r", encoding="utf-8") as f:
                    warm_entry = json.load(f)

                if not self._is_expired(warm_entry, self.warm_ttl_sec):
                    # 命中温缓存
                    warm_entry["last_access"] = now
                    warm_entry["hit_count"] = int(warm_entry.get("hit_count", 0)) + 1

                    # 更新温缓存文件
                    with warm_path.open("w", encoding="utf-8") as f:
                        json.dump(warm_entry, f, ensure_ascii=False, indent=2)

                    self.stats["hit_warm"] += 1

                    # 尝试提升到热缓存
                    self._promote_to_hot(key, warm_entry)

                    response = warm_entry.get("response")
                    logger.debug(
                        f"温缓存命中: {key[:8]}... (命中 {warm_entry['hit_count']} 次)"
                    )
                    return response, "warm"
                else:
                    # 过期，删除
                    warm_path.unlink()
                    self.stats["evict_warm"] += 1
                    logger.debug(f"温缓存过期: {key[:8]}...")

            except Exception as e:
                logger.warning(f"读取温缓存失败 {key[:8]}...: {e}")

        # 3. 未命中
        self.stats["miss"] += 1
        return None, None

    def store(
        self,
        key: str,
        response: Dict[str, Any]
    ) -> None:
        """
        存储缓存

        Args:
            key: 缓存键
            response: 响应数据
        """
        if not self.enabled:
            return

        now = self._now()

        entry = {
            "created_at": now,
            "last_access": now,
            "hit_count": 0,
            "response": response
        }

        # 存储到热缓存
        self.hot_cache[key] = entry
        self.hot_cache.move_to_end(key)

        # LRU 淘汰
        if len(self.hot_cache) > self.hot_cache_size:
            oldest_key, _ = self.hot_cache.popitem(last=False)
            self.stats["evict_hot"] += 1
            logger.debug(f"LRU 淘汰热缓存: {oldest_key[:8]}...")

        # 存储到温缓存
        try:
            warm_path = self._warm_path(key)
            with warm_path.open("w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"写入温缓存失败 {key[:8]}...: {e}")

        self.stats["store"] += 1
        logger.debug(f"缓存存储: {key[:8]}... (热+温)")

    def prune_expired(self) -> int:
        """清理过期的缓存条目

        Returns:
            清理的条目数
        """
        if not self.enabled:
            return 0

        pruned = 0
        now = self._now()

        # 清理热缓存
        expired_hot = [
            key for key, entry in self.hot_cache.items()
            if self._is_expired(entry, self.hot_ttl_sec)
        ]
        for key in expired_hot:
            del self.hot_cache[key]
            pruned += 1
            self.stats["evict_hot"] += 1

        # 清理温缓存
        if self.warm_cache_dir.exists():
            for warm_file in self.warm_cache_dir.glob("*.json"):
                try:
                    with warm_file.open("r", encoding="utf-8") as f:
                        entry = json.load(f)

                    if self._is_expired(entry, self.warm_ttl_sec):
                        warm_file.unlink()
                        pruned += 1
                        self.stats["evict_warm"] += 1
                except Exception as e:
                    logger.warning(f"清理温缓存失败 {warm_file.name}: {e}")

        if pruned > 0:
            logger.info(f"清理过期缓存: {pruned} 条")

        return pruned

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_requests = self.stats["hit_hot"] + self.stats["hit_warm"] + self.stats["miss"]
        hit_rate = (
            (self.stats["hit_hot"] + self.stats["hit_warm"]) / total_requests
            if total_requests > 0 else 0.0
        )

        return {
            "enabled": self.enabled,
            "hot_cache_size": len(self.hot_cache),
            "hot_cache_max": self.hot_cache_size,
            "hit_hot": self.stats["hit_hot"],
            "hit_warm": self.stats["hit_warm"],
            "miss": self.stats["miss"],
            "total_requests": total_requests,
            "hit_rate": hit_rate,
            "store": self.stats["store"],
            "evict_hot": self.stats["evict_hot"],
            "evict_warm": self.stats["evict_warm"]
        }

    def clear(self) -> None:
        """清空所有缓存"""
        self.hot_cache.clear()

        if self.warm_cache_dir.exists():
            for warm_file in self.warm_cache_dir.glob("*.json"):
                try:
                    warm_file.unlink()
                except Exception as e:
                    logger.warning(f"删除温缓存失败 {warm_file.name}: {e}")

        logger.info("缓存已清空")
