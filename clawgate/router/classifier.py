"""Task Classifier - 任务分类器"""

from typing import List, Dict, Optional
import re
import logging

logger = logging.getLogger("clawgate.router.classifier")


class TaskClassifier:
    """任务分类器 - 分析任务类型、复杂度、优先级"""

    def __init__(self):
        # 任务类型关键词
        self.task_keywords = {
            "reasoning": ["分析", "推理", "证明", "解释", "为什么", "analyze", "reason", "explain", "why"],
            "coding": ["代码", "实现", "写", "修复", "bug", "code", "implement", "write", "fix"],
            "translation": ["翻译", "translate", "中文", "英文", "chinese", "english"],
            "creative": ["创意", "设计", "故事", "creative", "design", "story"],
            "qa": ["问答", "什么", "如何", "what", "how", "question"],
        }

        # 复杂度指标
        self.complexity_indicators = {
            "high": ["复杂", "深入", "详细", "系统", "架构", "complex", "detailed", "architecture"],
            "low": ["简单", "快速", "概述", "simple", "quick", "brief"],
        }

        # 敏感内容关键词 (中英文)
        self.sensitivity_keywords = {
            "nsfw": [
                "色情", "性爱", "做爱", "裸体", "裸照", "成人", "情色", "调情",
                "约炮", "一夜情", "性感", "胸部", "屁股", "下体", "阴茎", "阴道",
                "自慰", "口交", "肛交", "高潮", "勃起", "射精", "潮吹",
                "porn", "sex", "nude", "naked", "erotic", "nsfw", "xxx",
                "orgasm", "masturbat", "fetish", "hentai", "boobs", "dick",
                "pussy", "cock", "blowjob", "anal", "cum", "horny",
                "slutty", "kinky", "foreplay", "threesome",
            ],
            "violence": [
                "杀人", "自杀", "暴力", "虐待", "谋杀", "血腥", "残忍",
                "砍头", "分尸", "酷刑", "屠杀", "强奸", "强暴",
                "kill", "murder", "suicide", "torture", "gore", "violent",
                "rape", "assault", "massacre", "slaughter", "dismember",
            ],
            "politics": [
                "习近平", "共产党", "六四", "天安门", "台独", "藏独", "疆独",
                "法轮功", "民主运动", "政治犯", "维权", "翻墙",
                "tiananmen", "falun gong", "uyghur", "tibet independence",
                "xinjiang", "ccp",
            ],
            "drugs": [
                "毒品", "大麻", "可卡因", "海洛因", "冰毒", "摇头丸", "迷幻",
                "嗑药", "吸毒", "贩毒",
                "cocaine", "heroin", "meth", "mdma", "lsd", "weed",
                "marijuana", "drug use", "overdose",
            ],
        }

    def classify(self, messages: List[Dict]) -> Dict:
        """
        分类任务

        Args:
            messages: 消息列表

        Returns:
            {
                "task_type": "reasoning/coding/translation/creative/qa",
                "complexity": "high/medium/low",
                "priority": 0/1/2,
                "features": {...}
            }
        """
        # 提取最后一条用户消息
        user_messages = [msg for msg in messages if msg.get("role") == "user"]
        if not user_messages:
            return self._default_classification()

        last_message = user_messages[-1].get("content", "")

        # 0. 检测强制路由标签 [[model/backend]]
        force_route = self._detect_force_route_tag(last_message)
        if force_route:
            last_message = force_route["clean_message"]
            # 更新消息内容（去掉标签）
            user_messages[-1]["content"] = last_message

        # 1. 任务类型
        task_type = self._detect_task_type(last_message)

        # 2. 复杂度
        complexity = self._detect_complexity(last_message, messages)

        # 3. 优先级（默认从请求中获取，这里作为后备）
        priority = 1

        # 4. 敏感度检测
        sensitivity = self._detect_sensitivity(last_message)

        # 5. 特征
        features = {
            "has_code": bool(re.search(r"```", last_message)),
            "message_length": len(last_message),
            "conversation_length": len(messages),
            "requires_context": len(messages) > 2,
        }

        result = {
            "task_type": task_type,
            "complexity": complexity,
            "priority": priority,
            "sensitivity": sensitivity,
            "features": features,
        }

        # 如果有强制路由标签，加入结果
        if force_route:
            result["force_route"] = force_route["target"]
            logger.info(f"[分类] 🏷️ 强制路由标签: [[{force_route['target']}]]")

        return result

    def _detect_task_type(self, text: str) -> str:
        """检测任务类型"""
        text_lower = text.lower()

        # 计算每种类型的匹配度
        scores = {}
        for task_type, keywords in self.task_keywords.items():
            matched = [kw for kw in keywords if kw in text_lower]
            scores[task_type] = len(matched)
            if matched:
                logger.debug(f"[分类] {task_type}: 命中关键词 {matched} (得分={len(matched)})")

        # 返回得分最高的类型
        if max(scores.values()) > 0:
            result = max(scores, key=scores.get)
            logger.info(f"[分类] 任务类型={result} | 得分分布: {scores}")
            return result
        else:
            logger.info(f"[分类] 无关键词命中，默认=qa | 输入前50字: {text_lower[:50]}...")
            return "qa"  # 默认为问答

    def _detect_complexity(self, text: str, messages: List[Dict]) -> str:
        """检测复杂度"""
        text_lower = text.lower()

        # 1. 关键词检测
        high_matched = [kw for kw in self.complexity_indicators["high"] if kw in text_lower]
        low_matched = [kw for kw in self.complexity_indicators["low"] if kw in text_lower]
        high_score = len(high_matched)
        low_score = len(low_matched)

        # 2. 长度检测
        if len(text) > 500:
            high_score += 1
            logger.debug(f"[复杂度] 消息长度={len(text)} > 500, high_score+1")

        # 3. 上下文长度
        if len(messages) > 5:
            high_score += 1
            logger.debug(f"[复杂度] 对话轮数={len(messages)} > 5, high_score+1")

        # 判断
        if high_score > low_score:
            result = "high"
        elif low_score > high_score:
            result = "low"
        else:
            result = "medium"

        logger.info(
            f"[复杂度] result={result} | high={high_score}(词:{high_matched}) "
            f"low={low_score}(词:{low_matched}) | 消息长度={len(text)}, 对话轮数={len(messages)}"
        )
        return result

    def _detect_force_route_tag(self, text: str) -> Optional[Dict]:
        """检测强制路由标签 [[target]]

        支持格式:
            [[gemini]] 你好 → 强制路由到 gemini
            [[deepseek]] 分析一下 → 强制路由到 deepseek
            [[gpt]] / [[glm]] / [[local]] 等

        Returns:
            None 或 {"target": "gemini", "clean_message": "你好"}
        """
        match = re.match(r"^\s*\[\[(\w[\w\-\.]*)\]\]\s*(.*)", text, re.DOTALL)
        if not match:
            return None

        target = match.group(1).lower().strip()
        clean_message = match.group(2).strip()

        logger.info(f"[标签路由] 检测到 [[{target}]] | 原文: {text[:60]}...")
        return {"target": target, "clean_message": clean_message or text}

    def _detect_sensitivity(self, text: str) -> Dict:
        """检测内容敏感度

        Returns:
            {
                "level": "none" / "low" / "high",
                "categories": ["nsfw", "violence", ...],
                "details": {"nsfw": ["色情", "xxx"], ...}
            }
        """
        text_lower = text.lower()
        detected = {}

        for category, keywords in self.sensitivity_keywords.items():
            matched = [kw for kw in keywords if kw in text_lower]
            if matched:
                detected[category] = matched

        if not detected:
            logger.debug("[敏感度] 无敏感内容检出")
            return {"level": "none", "categories": [], "details": {}}

        # 判断敏感度等级
        total_hits = sum(len(v) for v in detected.values())
        categories = list(detected.keys())

        # high: 多关键词命中 或 命中多个类别 或 命中 nsfw/violence
        if total_hits >= 3 or len(categories) >= 2 or "nsfw" in categories or "violence" in categories:
            level = "high"
        else:
            level = "low"

        logger.warning(
            f"[敏感度] ⚠️ level={level} | 类别={categories} | "
            f"命中数={total_hits} | 详情={detected}"
        )
        return {"level": level, "categories": categories, "details": detected}

    def _default_classification(self) -> Dict:
        """默认分类"""
        return {
            "task_type": "qa",
            "complexity": "medium",
            "priority": 1,
            "sensitivity": {"level": "none", "categories": [], "details": {}},
            "features": {},
        }
