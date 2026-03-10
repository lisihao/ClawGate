"""Tantivy Full-Text Search Indexer"""

from pathlib import Path
from typing import List, Dict, Optional

try:
    import tantivy
    TANTIVY_AVAILABLE = True
except ImportError:
    TANTIVY_AVAILABLE = False


class TantivyIndexer:
    """Tantivy 全文搜索索引器

    功能:
    - 请求历史全文搜索
    - 上下文搜索
    - 模型性能搜索
    """

    def __init__(self, index_path: str = "data/tantivy"):
        if not TANTIVY_AVAILABLE:
            raise ImportError("Tantivy not installed. Install with: pip install tantivy")

        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)

        # 初始化索引
        self._init_index()

    def _init_index(self):
        """初始化 Tantivy 索引"""
        # Schema 定义
        schema_builder = tantivy.SchemaBuilder()

        # 请求字段
        schema_builder.add_text_field("request_id", stored=True)
        schema_builder.add_text_field("model", stored=True)
        schema_builder.add_text_field("messages", stored=True)  # 全文索引
        schema_builder.add_text_field("agent_type", stored=True)
        schema_builder.add_text_field("task_id", stored=True)
        schema_builder.add_text_field("status", stored=True)
        schema_builder.add_date_field("timestamp", stored=True)

        # 性能指标
        schema_builder.add_f64_field("ttft", stored=True)
        schema_builder.add_f64_field("total_time", stored=True)
        schema_builder.add_i64_field("input_tokens", stored=True)
        schema_builder.add_i64_field("output_tokens", stored=True)

        self.schema = schema_builder.build()

        # 创建或打开索引
        self.index = tantivy.Index(self.schema, path=str(self.index_path))
        self.writer = self.index.writer()

    def index_request(self, request_data: Dict):
        """
        索引请求

        Args:
            request_data: 请求数据
        """
        doc = tantivy.Document()

        # 添加字段
        doc.add_text("request_id", request_data.get("id", ""))
        doc.add_text("model", request_data.get("model", ""))

        # 合并消息内容用于全文搜索
        messages = request_data.get("messages", [])
        messages_text = " ".join([msg.get("content", "") for msg in messages])
        doc.add_text("messages", messages_text)

        doc.add_text("agent_type", request_data.get("agent_type", ""))
        doc.add_text("task_id", request_data.get("task_id", ""))
        doc.add_text("status", request_data.get("status", ""))

        # 性能指标
        if request_data.get("ttft"):
            doc.add_f64("ttft", request_data["ttft"])
        if request_data.get("total_time"):
            doc.add_f64("total_time", request_data["total_time"])
        if request_data.get("input_tokens"):
            doc.add_i64("input_tokens", request_data["input_tokens"])
        if request_data.get("output_tokens"):
            doc.add_i64("output_tokens", request_data["output_tokens"])

        # 写入索引
        self.writer.add_document(doc)
        self.writer.commit()

    def search(
        self,
        query: str,
        limit: int = 10,
        model: Optional[str] = None,
        agent_type: Optional[str] = None,
    ) -> List[Dict]:
        """
        全文搜索

        Args:
            query: 搜索查询
            limit: 返回数量
            model: 过滤模型
            agent_type: 过滤 agent 类型

        Returns:
            搜索结果列表
        """
        searcher = self.index.searcher()

        # 构建查询
        query_parser = tantivy.QueryParser.for_index(
            self.index, ["messages", "model", "agent_type"]
        )

        # 添加过滤条件
        if model:
            query = f"{query} AND model:{model}"
        if agent_type:
            query = f"{query} AND agent_type:{agent_type}"

        parsed_query = query_parser.parse_query(query)

        # 执行搜索
        top_docs = searcher.search(parsed_query, limit).hits

        # 解析结果
        results = []
        for score, doc_address in top_docs:
            doc = searcher.doc(doc_address)
            results.append(
                {
                    "score": score,
                    "request_id": doc.get_first("request_id"),
                    "model": doc.get_first("model"),
                    "messages": doc.get_first("messages"),
                    "agent_type": doc.get_first("agent_type"),
                    "status": doc.get_first("status"),
                    "ttft": doc.get_first("ttft"),
                }
            )

        return results

    def close(self):
        """关闭索引写入器"""
        if hasattr(self, "writer"):
            self.writer.commit()
