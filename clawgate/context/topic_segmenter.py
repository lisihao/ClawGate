"""Topic Segmenter - 话题分段 + 工作/闲聊分类

将长对话按话题分段，标记每段为 work/casual，
为差异化上下文压缩提供依据。

架构预留:
  - 当前: 基于规则的快速分类 (<1ms)
  - 未来: 对接 Intent Engine (SmartHandler) 做更精准的意图识别
"""

import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger("clawgate.context.topic")


class TopicSegment:
    """一个话题段"""

    def __init__(self, start: int, end: int, topic_type: str, confidence: float):
        self.start = start          # 起始消息索引 (inclusive)
        self.end = end              # 结束消息索引 (exclusive)
        self.topic_type = topic_type  # "work" | "casual"
        self.confidence = confidence
        self.messages: List[Dict] = []

    @property
    def length(self) -> int:
        return self.end - self.start

    def __repr__(self):
        return f"<Segment [{self.start}:{self.end}] {self.topic_type} conf={self.confidence:.2f} msgs={self.length}>"


class TopicSegmenter:
    """话题分段器 - 将对话分成工作段和闲聊段"""

    def __init__(self):
        # 工作信号关键词 (中英文)
        self.work_signals = {
            "code": re.compile(
                r"```|def |class |function |import |require\(|"
                r"代码|实现|函数|变量|接口|API|模块|组件|调试|debug|bug|"
                r"编译|运行|部署|测试|test|build|deploy|config|配置",
                re.IGNORECASE,
            ),
            "technical": re.compile(
                r"架构|设计|方案|优化|性能|数据库|服务器|网络|协议|"
                r"算法|模型|训练|推理|pipeline|framework|backend|frontend|"
                r"docker|k8s|nginx|redis|sql|git|commit|branch|merge|"
                r"error|exception|traceback|stack",
                re.IGNORECASE,
            ),
            "analysis": re.compile(
                r"分析|研究|调研|对比|评估|审查|review|investigate|"
                r"根因|原因|问题|解决|方案|策略|trade-?off",
                re.IGNORECASE,
            ),
            "file_path": re.compile(
                r"[/\\][\w\-\.]+[/\\][\w\-\.]+|"       # 路径 /foo/bar
                r"\w+\.(py|ts|js|go|rs|java|cpp|h|yaml|json|md|sh|sql|toml|cfg)\b",  # 文件扩展名
                re.IGNORECASE,
            ),
            "tool_use": re.compile(
                r"tool_use|tool_result|<tool|<function|"
                r"\[Tool:|Read\(|Write\(|Bash\(|Grep\(",
                re.IGNORECASE,
            ),
        }

        # 闲聊信号
        self.casual_signals = {
            "greeting": re.compile(
                r"^(你好|嗨|hi|hello|hey|早|晚安|good morning|good night)\s*$",
                re.IGNORECASE,
            ),
            "short_confirm": re.compile(
                r"^(好|可以|OK|对|是|行|嗯|哈哈|666|👍|不错|谢谢|thanks|thx|lol|哈|嘻嘻|呵呵)\s*$",
                re.IGNORECASE,
            ),
            "chitchat": re.compile(
                r"天气|吃饭|午饭|晚饭|咖啡|休息|睡觉|起床|"
                r"电影|音乐|游戏|周末|假期|旅游|"
                r"weather|lunch|dinner|coffee|movie|game|weekend",
                re.IGNORECASE,
            ),
            "emotion": re.compile(
                r"^[😀-😿🤣🥲🤔🫡👋🎉💪🙏❤️]+\s*$|"
                r"^(哭了|笑死|无语|绝了|服了|牛|太强了|厉害)\s*$",
                re.IGNORECASE,
            ),
        }

        # 模型上下文窗口限制 (tokens)
        self.model_context_limits = {
            # 本地模型
            "qwen-1.7b": 2048,
            "qwen-7b": 8192,
            "qwen-14b": 8192,
            "qwen-32b": 32768,
            "llama-8b": 8192,
            "llama-70b": 32768,
            # 云端模型 (F7: cloud auto-fit)
            "deepseek-v3": 65536,
            "deepseek-r1": 65536,
            "deepseek-chat": 65536,
            "deepseek-reasoner": 65536,
            "gpt-5.2": 200000,
            "gpt-5.1": 200000,
            "gpt-4o": 131072,
            "gpt-4o-mini": 131072,
            "glm-4-flash": 131072,
            "glm-4-plus": 131072,
            "glm-5": 131072,
            "gemini-2.5-flash": 1048576,
            "gemini-2.5-pro": 1048576,
            "gemini-2-flash": 1048576,
            "gemini-2-pro": 1048576,
        }

    def get_context_limit(self, model: str) -> int:
        """获取模型的上下文窗口限制"""
        # 精确匹配
        if model in self.model_context_limits:
            return self.model_context_limits[model]

        # 模糊匹配 (如 qwen-1.7b-mlx → qwen-1.7b)
        for known_model, limit in self.model_context_limits.items():
            if model.startswith(known_model):
                return limit

        # 默认: 保守值 4096
        return 4096

    def get_model_tier(self, model: str) -> str:
        """根据模型上下文窗口推导能力分级

        基于 MIT 论文发现：弱模型更易受 context pollution 影响。
        分级决定 assistant 消息的过滤激进度。

        Returns:
            "weak"   — 上下文 ≤ 8K,  激进过滤 assistant
            "medium" — 上下文 8K-64K, 平衡过滤
            "strong" — 上下文 ≥ 64K,  保守过滤
        """
        limit = self.get_context_limit(model)
        if limit <= 8192:
            return "weak"
        elif limit <= 65536:
            return "medium"
        else:
            return "strong"

    def classify_message(self, msg: Dict) -> Tuple[str, float]:
        """分类单条消息: work 或 casual

        Returns:
            (topic_type, confidence)
        """
        role = msg.get("role", "user")
        content = str(msg.get("content", ""))

        # system 消息始终是 work
        if role == "system":
            return "work", 1.0

        # tool 相关消息始终是 work
        if role in ("tool", "function"):
            return "work", 1.0

        # 空消息或极短消息
        if len(content.strip()) < 3:
            return "casual", 0.7

        # 计算工作信号得分
        work_score = 0
        for signal_name, pattern in self.work_signals.items():
            matches = pattern.findall(content)
            if matches:
                # 代码块和文件路径权重更高
                weight = 2 if signal_name in ("code", "file_path", "tool_use") else 1
                work_score += len(matches) * weight

        # 计算闲聊信号得分
        casual_score = 0
        for signal_name, pattern in self.casual_signals.items():
            if pattern.search(content):
                # 短确认和表情权重更高
                weight = 2 if signal_name in ("short_confirm", "emotion") else 1
                casual_score += weight

        # 长消息 (>200字) 且 assistant 角色 → 倾向 work
        if len(content) > 200 and role == "assistant":
            work_score += 2

        # 消息很长 (>500字) → 大概率是 work
        if len(content) > 500:
            work_score += 3

        # 判断
        total = work_score + casual_score
        if total == 0:
            # 无明显信号：中等长度文字默认 work，短文字默认 casual
            if len(content) > 50:
                return "work", 0.5
            else:
                return "casual", 0.5

        work_ratio = work_score / total
        if work_ratio >= 0.6:
            return "work", min(0.95, 0.5 + work_ratio * 0.5)
        elif work_ratio <= 0.3:
            return "casual", min(0.95, 0.5 + (1 - work_ratio) * 0.5)
        else:
            # 模糊地带，倾向 work（保守策略，宁可多保留）
            return "work", 0.5

    def segment(self, messages: List[Dict]) -> List[TopicSegment]:
        """将消息列表分段

        相邻的同类型消息合并为一个段。
        工作→闲聊 或 闲聊→工作 的切换点作为段边界。

        Returns:
            TopicSegment 列表
        """
        if not messages:
            return []

        segments: List[TopicSegment] = []
        current_type = None
        current_start = 0
        current_confidences: List[float] = []

        for i, msg in enumerate(messages):
            msg_type, confidence = self.classify_message(msg)

            if current_type is None:
                # 第一条消息
                current_type = msg_type
                current_start = i
                current_confidences = [confidence]
            elif msg_type != current_type:
                # 话题切换 → 结束当前段，开始新段
                seg = TopicSegment(
                    start=current_start,
                    end=i,
                    topic_type=current_type,
                    confidence=sum(current_confidences) / len(current_confidences),
                )
                seg.messages = messages[current_start:i]
                segments.append(seg)

                current_type = msg_type
                current_start = i
                current_confidences = [confidence]
            else:
                # 同类型，继续累积
                current_confidences.append(confidence)

        # 最后一段
        if current_type is not None:
            seg = TopicSegment(
                start=current_start,
                end=len(messages),
                topic_type=current_type,
                confidence=sum(current_confidences) / len(current_confidences),
            )
            seg.messages = messages[current_start:]
            segments.append(seg)

        # 合并过短的段（<3条消息的段合并到相邻段）
        segments = self._merge_short_segments(segments, messages)

        logger.debug(
            f"[分段] {len(messages)} 条消息 → {len(segments)} 段 | "
            f"work={sum(1 for s in segments if s.topic_type == 'work')} "
            f"casual={sum(1 for s in segments if s.topic_type == 'casual')}"
        )

        return segments

    def _merge_short_segments(
        self, segments: List[TopicSegment], messages: List[Dict], min_length: int = 3
    ) -> List[TopicSegment]:
        """合并过短的段到相邻段"""
        if len(segments) <= 1:
            return segments

        merged: List[TopicSegment] = []
        for seg in segments:
            if seg.length < min_length and merged:
                # 合并到前一个段
                prev = merged[-1]
                new_seg = TopicSegment(
                    start=prev.start,
                    end=seg.end,
                    topic_type=prev.topic_type,  # 继承前一段的类型
                    confidence=prev.confidence,
                )
                new_seg.messages = messages[prev.start:seg.end]
                merged[-1] = new_seg
            else:
                merged.append(seg)

        return merged

    def get_compression_plan(
        self, segments: List[TopicSegment], total_messages: int
    ) -> List[Dict]:
        """为每个段生成压缩计划

        策略:
        - 最后一个工作段 → 完整保留
        - 其他工作段 → 保留关键消息 (selective)
        - 闲聊段 → 大幅压缩或丢弃

        Returns:
            [{segment, action, keep_ratio}]
        """
        if not segments:
            return []

        plan = []
        last_work_idx = None

        # 找到最后一个工作段
        for i in range(len(segments) - 1, -1, -1):
            if segments[i].topic_type == "work":
                last_work_idx = i
                break

        for i, seg in enumerate(segments):
            if seg.topic_type == "casual":
                # 闲聊段: 最近的保留1条概括，较早的直接丢弃
                if i >= len(segments) - 3:
                    plan.append({
                        "segment": seg,
                        "action": "summarize_one_line",
                        "keep_ratio": 0.0,
                    })
                else:
                    plan.append({
                        "segment": seg,
                        "action": "drop",
                        "keep_ratio": 0.0,
                    })
            elif i == last_work_idx:
                # 最后一个工作段: 完整保留
                plan.append({
                    "segment": seg,
                    "action": "keep_full",
                    "keep_ratio": 1.0,
                })
            elif last_work_idx is not None and i >= last_work_idx - 1:
                # 倒数第二个工作段: 保留大部分
                plan.append({
                    "segment": seg,
                    "action": "keep_selective",
                    "keep_ratio": 0.7,
                })
            else:
                # 较早的工作段: 压缩为摘要
                plan.append({
                    "segment": seg,
                    "action": "summarize",
                    "keep_ratio": 0.1,
                })

        return plan
