"""SQLite 存储层"""

import sqlite3
import json
import uuid
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime


class SQLiteStore:
    """SQLite 存储层（轻量级关系型数据库）"""

    def __init__(self, db_path: str = "data/sqlite"):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self._init_databases()

    def _init_databases(self):
        """初始化所有数据库"""

        # models.db
        models_db = self.db_path / "models.db"
        conn = sqlite3.connect(models_db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                backend TEXT NOT NULL,
                engine TEXT,
                provider TEXT,
                model_path TEXT,
                cost_per_1k REAL NOT NULL,
                quality_score REAL DEFAULT 0.8,
                max_context INTEGER DEFAULT 32768,
                use_cases TEXT,
                config TEXT,
                enabled BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS model_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                avg_ttft REAL,
                p50_ttft REAL,
                p99_ttft REAL,
                avg_throughput REAL,
                total_input_tokens INTEGER DEFAULT 0,
                total_output_tokens INTEGER DEFAULT 0,
                total_cost REAL DEFAULT 0,
                success_rate REAL,
                avg_quality_score REAL,
                FOREIGN KEY (model_id) REFERENCES models(id)
            );

            CREATE INDEX IF NOT EXISTS idx_model_metrics_timestamp
                ON model_metrics(timestamp);
        """
        )
        conn.close()

        # requests.db
        requests_db = self.db_path / "requests.db"
        conn = sqlite3.connect(requests_db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model TEXT NOT NULL,
                messages TEXT NOT NULL,
                priority INTEGER DEFAULT 1,
                agent_type TEXT,
                agent_id TEXT,
                task_id TEXT,
                response TEXT,
                status TEXT,
                error TEXT,
                ttft REAL,
                total_time REAL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cost REAL,
                quality_score REAL,
                compressed BOOLEAN DEFAULT 0,
                compression_ratio REAL,
                metadata TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_requests_timestamp
                ON requests(timestamp);
            CREATE INDEX IF NOT EXISTS idx_requests_model
                ON requests(model);
            CREATE INDEX IF NOT EXISTS idx_requests_status
                ON requests(status);
        """
        )
        conn.close()

        # context.db
        context_db = self.db_path / "context.db"
        conn = sqlite3.connect(context_db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT UNIQUE NOT NULL,
                messages TEXT NOT NULL,
                compressed_messages TEXT,
                summary TEXT,
                token_count INTEGER,
                compressed_token_count INTEGER,
                compression_strategy TEXT,
                hit_count INTEGER DEFAULT 0,
                last_hit TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_cache_key
                ON context_cache(cache_key);

            CREATE TABLE IF NOT EXISTS context_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                summary_level TEXT,
                summary_text TEXT NOT NULL,
                key_decisions TEXT,
                key_code_blocks TEXT,
                key_tasks TEXT,
                original_message_count INTEGER,
                original_token_count INTEGER,
                summary_token_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_summaries_session
                ON context_summaries(session_id);

            CREATE TABLE IF NOT EXISTS conversation_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                segment_index INTEGER NOT NULL,
                topic_type TEXT NOT NULL,
                summary TEXT,
                messages TEXT NOT NULL,
                message_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                UNIQUE(conversation_id, segment_index)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_segments_conv_id
                ON conversation_segments(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_conv_segments_type
                ON conversation_segments(topic_type);
            CREATE INDEX IF NOT EXISTS idx_conv_segments_expires
                ON conversation_segments(expires_at);

            CREATE TABLE IF NOT EXISTS long_term_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_key TEXT UNIQUE NOT NULL,
                key_files TEXT NOT NULL,
                summary TEXT NOT NULL,
                conversation_id TEXT,
                access_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ltm_key
                ON long_term_memories(memory_key);
            CREATE INDEX IF NOT EXISTS idx_ltm_expires
                ON long_term_memories(expires_at);
            CREATE INDEX IF NOT EXISTS idx_ltm_files
                ON long_term_memories(key_files);

            CREATE TABLE IF NOT EXISTS prompt_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_key TEXT UNIQUE NOT NULL,
                system_messages TEXT NOT NULL,
                compressed_system TEXT NOT NULL,
                token_count INTEGER,
                compressed_token_count INTEGER,
                hit_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_prompt_cache_key
                ON prompt_cache(prompt_key);

            CREATE TABLE IF NOT EXISTS semantic_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE NOT NULL,
                query_text TEXT NOT NULL,
                keywords TEXT NOT NULL,
                response TEXT NOT NULL,
                model TEXT,
                hit_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_semantic_cache_hash
                ON semantic_cache(query_hash);
            CREATE INDEX IF NOT EXISTS idx_semantic_cache_model
                ON semantic_cache(model);
        """
        )
        conn.close()

        print(f"✅ SQLite 数据库初始化完成: {self.db_path}")

    # ========== 模型管理 ==========

    def add_model(self, model_config: Dict) -> int:
        """添加模型"""
        conn = sqlite3.connect(self.db_path / "models.db")
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO models (
                name, backend, engine, provider, model_path,
                cost_per_1k, quality_score, max_context, use_cases, config
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                model_config["name"],
                model_config["backend"],
                model_config.get("engine"),
                model_config.get("provider"),
                model_config.get("model_path"),
                model_config["cost_per_1k"],
                model_config.get("quality_score", 0.8),
                model_config.get("max_context", 32768),
                json.dumps(model_config.get("use_cases", [])),
                json.dumps(model_config.get("config", {})),
            ),
        )

        model_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return model_id

    def get_models(self, backend: str = None) -> List[Dict]:
        """获取模型列表"""
        conn = sqlite3.connect(self.db_path / "models.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if backend:
            cursor.execute(
                "SELECT * FROM models WHERE backend = ? AND enabled = 1",
                (backend,),
            )
        else:
            cursor.execute("SELECT * FROM models WHERE enabled = 1")

        models = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # 解析 JSON 字段
        for model in models:
            if model["use_cases"]:
                model["use_cases"] = json.loads(model["use_cases"])
            if model["config"]:
                model["config"] = json.loads(model["config"])

        return models

    # ========== 请求记录 ==========

    def log_request(self, request_data: Dict) -> str:
        """记录请求"""
        conn = sqlite3.connect(self.db_path / "requests.db")
        cursor = conn.cursor()

        request_id = request_data.get("id", str(uuid.uuid4()))

        cursor.execute(
            """
            INSERT INTO requests (
                id, model, messages, priority, agent_type, agent_id, task_id,
                response, status, error, ttft, total_time,
                input_tokens, output_tokens, cost, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                request_id,
                request_data["model"],
                json.dumps(request_data["messages"]),
                request_data.get("priority", 1),
                request_data.get("agent_type"),
                request_data.get("agent_id"),
                request_data.get("task_id"),
                json.dumps(request_data.get("response", {})),
                request_data.get("status", "success"),
                request_data.get("error"),
                request_data.get("ttft"),
                request_data.get("total_time"),
                request_data.get("input_tokens"),
                request_data.get("output_tokens"),
                request_data.get("cost"),
                json.dumps(request_data.get("metadata", {})),
            ),
        )

        conn.commit()
        conn.close()

        return request_id

    def get_request_history(
        self, limit: int = 100, model: str = None
    ) -> List[Dict]:
        """获取请求历史"""
        conn = sqlite3.connect(self.db_path / "requests.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if model:
            cursor.execute(
                "SELECT * FROM requests WHERE model = ? ORDER BY timestamp DESC LIMIT ?",
                (model, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM requests ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )

        requests = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return requests

    # ========== 上下文缓存 ==========

    def cache_context(
        self,
        cache_key: str,
        messages: List[Dict],
        compressed_messages: List[Dict] = None,
        summary: str = None,
        **kwargs,
    ):
        """缓存上下文"""
        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO context_cache (
                cache_key, messages, compressed_messages, summary,
                token_count, compressed_token_count, compression_strategy
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                cache_key,
                json.dumps(messages),
                json.dumps(compressed_messages) if compressed_messages else None,
                summary,
                kwargs.get("token_count"),
                kwargs.get("compressed_token_count"),
                kwargs.get("compression_strategy"),
            ),
        )

        conn.commit()
        conn.close()

    def get_cached_context(self, cache_key: str) -> Optional[Dict]:
        """获取缓存的上下文"""
        conn = sqlite3.connect(self.db_path / "context.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM context_cache WHERE cache_key = ?", (cache_key,)
        )

        row = cursor.fetchone()

        if row:
            # 更新命中次数
            cursor.execute(
                """
                UPDATE context_cache
                SET hit_count = hit_count + 1, last_hit = CURRENT_TIMESTAMP
                WHERE cache_key = ?
            """,
                (cache_key,),
            )
            conn.commit()

            result = dict(row)
            result["messages"] = json.loads(result["messages"])
            if result["compressed_messages"]:
                result["compressed_messages"] = json.loads(
                    result["compressed_messages"]
                )
        else:
            result = None

        conn.close()

        return result

    # ========== Dashboard 聚合查询 (F4) ==========

    def get_model_stats(self, hours: int = 24) -> List[Dict]:
        """Per-model aggregated statistics over the last N hours"""
        conn = sqlite3.connect(self.db_path / "requests.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                model,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                AVG(total_time) as avg_latency,
                AVG(ttft) as avg_ttft,
                SUM(CASE WHEN status = 'success' THEN 1.0 ELSE 0.0 END) / COUNT(*) as success_rate,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(cost), 0) as total_cost
            FROM requests
            WHERE timestamp >= datetime('now', ?)
            GROUP BY model
            ORDER BY count DESC
            """,
            (f"-{hours} hours",),
        )
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_percentile_ttft(self, model: str, hours: int = 24) -> Dict:
        """Get p50 and p99 TTFT for a specific model"""
        conn = sqlite3.connect(self.db_path / "requests.db")
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT ttft FROM requests
            WHERE model = ? AND ttft IS NOT NULL
                AND timestamp >= datetime('now', ?)
            ORDER BY ttft ASC
            """,
            (model, f"-{hours} hours"),
        )
        values = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not values:
            return {"p50": 0, "p99": 0}

        n = len(values)
        return {
            "p50": values[n // 2] if n > 0 else 0,
            "p99": values[min(int(n * 0.99), n - 1)] if n > 0 else 0,
        }

    def get_requests_per_minute(self, minutes: int = 60) -> List[Dict]:
        """Requests per minute over the last N minutes"""
        conn = sqlite3.connect(self.db_path / "requests.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                strftime('%H:%M', timestamp) as minute,
                COUNT(*) as count,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count
            FROM requests
            WHERE timestamp >= datetime('now', ?)
            GROUP BY strftime('%H:%M', timestamp)
            ORDER BY minute ASC
            """,
            (f"-{minutes} minutes",),
        )
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results

    def get_context_stats(self) -> Dict:
        """Context engine statistics"""
        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        stats = {}

        # Cache stats
        cursor.execute("SELECT COUNT(*), SUM(hit_count) FROM context_cache")
        row = cursor.fetchone()
        stats["cache_entries"] = row[0] or 0
        stats["cache_total_hits"] = row[1] or 0

        # Segment stats
        cursor.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN topic_type='work' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN topic_type='casual' THEN 1 ELSE 0 END) "
            "FROM conversation_segments WHERE expires_at > datetime('now')"
        )
        row = cursor.fetchone()
        stats["active_segments"] = row[0] or 0
        stats["work_segments"] = row[1] or 0
        stats["casual_segments"] = row[2] or 0

        # LTM stats
        cursor.execute(
            "SELECT COUNT(*), SUM(access_count) FROM long_term_memories "
            "WHERE expires_at > datetime('now')"
        )
        row = cursor.fetchone()
        stats["ltm_count"] = row[0] or 0
        stats["ltm_total_recalls"] = row[1] or 0

        # Prompt cache stats (if table exists)
        try:
            cursor.execute(
                "SELECT COUNT(*), SUM(hit_count) FROM prompt_cache "
                "WHERE expires_at > datetime('now')"
            )
            row = cursor.fetchone()
            stats["prompt_cache_entries"] = row[0] or 0
            stats["prompt_cache_hits"] = row[1] or 0
        except sqlite3.OperationalError:
            stats["prompt_cache_entries"] = 0
            stats["prompt_cache_hits"] = 0

        # Semantic cache stats (if table exists)
        try:
            cursor.execute(
                "SELECT COUNT(*), SUM(hit_count) FROM semantic_cache "
                "WHERE expires_at > datetime('now')"
            )
            row = cursor.fetchone()
            stats["semantic_cache_entries"] = row[0] or 0
            stats["semantic_cache_hits"] = row[1] or 0
        except sqlite3.OperationalError:
            stats["semantic_cache_entries"] = 0
            stats["semantic_cache_hits"] = 0

        conn.close()
        return stats

    # ========== Prompt Cache (F5) ==========

    def get_prompt_cache(self, prompt_key: str) -> Optional[Dict]:
        """Get cached compressed system prompt"""
        conn = sqlite3.connect(self.db_path / "context.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM prompt_cache WHERE prompt_key = ? AND expires_at > datetime('now')",
            (prompt_key,),
        )
        row = cursor.fetchone()

        if row:
            cursor.execute(
                "UPDATE prompt_cache SET hit_count = hit_count + 1 WHERE prompt_key = ?",
                (prompt_key,),
            )
            conn.commit()
            result = dict(row)
            result["system_messages"] = json.loads(result["system_messages"])
            result["compressed_system"] = json.loads(result["compressed_system"])
        else:
            result = None

        conn.close()
        return result

    def set_prompt_cache(
        self,
        prompt_key: str,
        system_messages: List[Dict],
        compressed_system: List[Dict],
        token_count: int = 0,
        compressed_token_count: int = 0,
        ttl_hours: int = 24,
    ):
        """Cache compressed system prompt"""
        from datetime import datetime, timedelta

        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        expires_at = (
            datetime.utcnow() + timedelta(hours=ttl_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT OR REPLACE INTO prompt_cache (
                prompt_key, system_messages, compressed_system,
                token_count, compressed_token_count, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_key,
                json.dumps(system_messages, ensure_ascii=False),
                json.dumps(compressed_system, ensure_ascii=False),
                token_count,
                compressed_token_count,
                expires_at,
            ),
        )
        conn.commit()
        conn.close()

    # ========== Semantic Cache (F6) ==========

    def get_all_semantic_cache(self, model: Optional[str] = None) -> List[Dict]:
        """Get all non-expired semantic cache entries for similarity search"""
        conn = sqlite3.connect(self.db_path / "context.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if model:
            cursor.execute(
                "SELECT * FROM semantic_cache WHERE model = ? AND expires_at > datetime('now') "
                "ORDER BY hit_count DESC",
                (model,),
            )
        else:
            cursor.execute(
                "SELECT * FROM semantic_cache WHERE expires_at > datetime('now') "
                "ORDER BY hit_count DESC"
            )

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        for row in rows:
            row["keywords"] = json.loads(row["keywords"])
            row["response"] = json.loads(row["response"])

        return rows

    def set_semantic_cache(
        self,
        query_hash: str,
        query_text: str,
        keywords: List[str],
        response: Dict,
        model: str,
        ttl_hours: int = 4,
    ):
        """Store a semantic cache entry"""
        from datetime import datetime, timedelta

        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        expires_at = (
            datetime.utcnow() + timedelta(hours=ttl_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute(
            """
            INSERT OR REPLACE INTO semantic_cache (
                query_hash, query_text, keywords, response, model, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                query_hash,
                query_text,
                json.dumps(keywords, ensure_ascii=False),
                json.dumps(response, ensure_ascii=False),
                model,
                expires_at,
            ),
        )
        conn.commit()
        conn.close()

    def bump_semantic_cache_hit(self, query_hash: str):
        """Increment hit count for a semantic cache entry"""
        conn = sqlite3.connect(self.db_path / "context.db")
        conn.execute(
            "UPDATE semantic_cache SET hit_count = hit_count + 1 WHERE query_hash = ?",
            (query_hash,),
        )
        conn.commit()
        conn.close()

    def cleanup_semantic_cache(self, max_size: int = 500) -> int:
        """Remove expired + LRU eviction beyond max_size"""
        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        # Remove expired
        cursor.execute("DELETE FROM semantic_cache WHERE expires_at <= datetime('now')")
        expired_count = cursor.rowcount

        # LRU eviction: keep top max_size by (hit_count DESC, created_at DESC)
        cursor.execute(
            """
            DELETE FROM semantic_cache WHERE id NOT IN (
                SELECT id FROM semantic_cache
                ORDER BY hit_count DESC, created_at DESC
                LIMIT ?
            )
            """,
            (max_size,),
        )
        evicted_count = cursor.rowcount

        conn.commit()
        conn.close()
        return expired_count + evicted_count

    def save_summary(self, session_id: str, summary_data: Dict):
        """保存摘要"""
        conn = sqlite3.connect(self.db_path / "context.db")
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO context_summaries (
                session_id, summary_level, summary_text,
                key_decisions, key_code_blocks, key_tasks,
                original_message_count, original_token_count, summary_token_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                session_id,
                summary_data.get("summary_level"),
                summary_data["summary_text"],
                json.dumps(summary_data.get("key_decisions", [])),
                json.dumps(summary_data.get("key_code_blocks", [])),
                json.dumps(summary_data.get("key_tasks", [])),
                summary_data.get("original_message_count"),
                summary_data.get("original_token_count"),
                summary_data.get("summary_token_count"),
            ),
        )

        conn.commit()
        conn.close()
