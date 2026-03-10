"""A/B Test: ConversationStore Pipeline vs Naive Tail-Cut Baseline

Goal: Prove that the full auto_fit() pipeline (structured summary +
user-first + model-tier filtering) produces better context for weak
models than a naive sliding window.

Test Design:
  - 40+ message realistic conversation
  - Method A (Baseline): Naive tail cut - last N messages within budget
  - Method B (ConversationStore): Full auto_fit(model="qwen-1.7b")
  - 5 factual questions about USER decisions
  - Scored by glm-4-flash via brain-router MCP
  - 2 trials for consistency

Run:
  python tests/test_ab_convstore.py
"""

import json
import os
import sys
import tempfile
import textwrap
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tiktoken

from clawgate.storage.sqlite_store import SQLiteStore
from clawgate.context.manager import ContextManager

# ============================================================
# 1. Test Conversation (40+ messages, ~20K chars)
# ============================================================

CONVERSATION = [
    {"role": "system", "content": "你是一个 Python 后端开发助手。"},

    # ========== Work Block 1: auth.py bcrypt bug fix (msg 1-10) ==========
    {"role": "user", "content": (
        "我发现 auth.py 有个严重 bug：用户输入正确密码也无法登录。"
        "我检查了日志，bcrypt.checkpw 总是返回 False。"
        "数据库里存的密码 hash 格式看起来是对的。"
    )},
    {"role": "assistant", "content": (
        "这个问题我之前也遇到过，很可能是 Redis session 过期导致的。"
        "当用户的 session 在 Redis 中过期后，即使密码验证通过，"
        "服务端也可能因为找不到对应的 session 而返回 False。\n\n"
        "不过让我先帮你检查一下密码验证的代码。你的 auth.py 里 "
        "verify_password 函数应该改成这样：\n\n"
        "```python\n"
        "import bcrypt\n\n"
        "def hash_password(password: str) -> str:\n"
        '    """注册时: 将明文密码哈希"""\n'
        "    salt = bcrypt.gensalt(rounds=12)\n"
        "    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)\n"
        "    return hashed.decode('utf-8')\n\n"
        "def verify_password(plain_password: str, hashed_password: str) -> bool:\n"
        '    """登录时: 验证密码"""\n'
        "    return bcrypt.checkpw(\n"
        "        plain_password.encode('utf-8'),\n"
        "        hashed_password.encode('utf-8')\n"
        "    )\n"
        "```\n\n"
        "这样可以确保编码一致性。另外建议你也检查一下 Redis 的连接配置。"
    )},
    {"role": "user", "content": (
        "不是 session 问题。我已经排查过了，Redis 连接正常。"
        "我确认根因是 bcrypt salt 轮数不匹配。"
        "注册时用的 rounds=10，但验证时用的 bcrypt.gensalt() 默认 rounds=12。"
        "所以 checkpw 的 hash 对不上。请帮我统一改成 rounds=12。"
    )},
    {"role": "assistant", "content": (
        "你说得对，确实是 salt rounds 不一致的问题。虽然我一开始怀疑是 Redis "
        "session 的问题（因为这个在分布式系统中很常见），但你的排查很准确。\n\n"
        "这里是修正后的完整 auth.py：\n\n"
        "```python\n"
        "import bcrypt\n"
        "import logging\n\n"
        "logger = logging.getLogger('auth')\n\n"
        "# 统一 bcrypt 轮数配置\n"
        "BCRYPT_ROUNDS = 12\n\n"
        "def hash_password(password: str) -> str:\n"
        '    """注册时哈希密码，使用统一的 rounds=12"""\n'
        "    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)\n"
        "    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)\n"
        "    logger.info(f'密码已哈希, rounds={BCRYPT_ROUNDS}')\n"
        "    return hashed.decode('utf-8')\n\n"
        "def verify_password(plain_password: str, hashed_password: str) -> bool:\n"
        '    """验证密码 - checkpw 会自动从 hash 中读取 salt 参数"""\n'
        "    try:\n"
        "        result = bcrypt.checkpw(\n"
        "            plain_password.encode('utf-8'),\n"
        "            hashed_password.encode('utf-8')\n"
        "        )\n"
        "        return result\n"
        "    except Exception as e:\n"
        "        logger.error(f'密码验证异常: {e}')\n"
        "        return False\n"
        "```\n\n"
        "关键点：bcrypt.checkpw 其实会自动从 hash 字符串中读取 salt 参数，"
        "所以已有的用 rounds=10 注册的用户不需要重新注册。"
        "但新注册的用户会统一使用 rounds=12。"
    )},
    {"role": "user", "content": (
        "好的，那还需要写一个迁移脚本，让老用户下次登录成功后自动重新哈希密码。"
        "用 rounds=12 重新算一次就行。"
    )},
    {"role": "assistant", "content": (
        "对，这是一个很好的实践。在很多安全审计中也推荐这种'渐进迁移'策略。"
        "之前 Redis 的那个问题虽然不是根因，但也建议你设一下 TTL 防止 session 泄漏。\n\n"
        "迁移逻辑可以嵌入到 verify_password 中：\n\n"
        "```python\n"
        "def verify_and_upgrade(user_id: str, plain_password: str,\n"
        "                       stored_hash: str, db_session) -> bool:\n"
        '    """验证密码并在需要时升级 hash rounds"""\n'
        "    if not bcrypt.checkpw(plain_password.encode('utf-8'),\n"
        "                          stored_hash.encode('utf-8')):\n"
        "        return False\n\n"
        "    # 检查是否需要升级 rounds\n"
        "    current_rounds = int(stored_hash.split('$')[2])\n"
        "    if current_rounds < BCRYPT_ROUNDS:\n"
        "        new_hash = hash_password(plain_password)\n"
        "        db_session.execute(\n"
        "            'UPDATE users SET password_hash = ? WHERE id = ?',\n"
        "            (new_hash, user_id)\n"
        "        )\n"
        "        db_session.commit()\n"
        "        logger.info(f'用户 {user_id} 密码 rounds 已升级: '\n"
        "                    f'{current_rounds} -> {BCRYPT_ROUNDS}')\n"
        "    return True\n"
        "```\n\n"
        "这样老用户在下次登录时会自动升级。"
    )},
    {"role": "user", "content": "完美，auth.py 的改动就这些。我先测试一下。"},
    {"role": "assistant", "content": (
        "好的，测试时建议你特别关注这几个场景：\n"
        "1. 用旧 hash（rounds=10）的用户能否正常登录\n"
        "2. 登录后 hash 是否被自动升级为 rounds=12\n"
        "3. 新注册用户是否直接使用 rounds=12\n"
        "4. 错误密码是否正确返回 False\n\n"
        "另外 Redis session 那边的 TTL 也建议你顺便看一下。"
    )},

    # ========== Casual Block (msg 11-18) ==========
    {"role": "user", "content": "先去吃午饭，回来再继续。"},
    {"role": "assistant", "content": "好的，慢慢吃！有什么问题回来继续聊。"},
    {"role": "user", "content": "你觉得楼下新开的那家湘菜馆怎么样？"},
    {"role": "assistant", "content": (
        "我作为 AI 没法品尝食物啦，不过湘菜的剁椒鱼头和小炒肉都是经典。"
        "如果他们家辣度可以选的话，建议先中辣试试。"
    )},
    {"role": "user", "content": "哈哈，说得我更饿了。那我去了。"},
    {"role": "assistant", "content": "去吧去吧，吃好回来继续干活！"},
    {"role": "user", "content": "吃完了，味道还不错，推荐！"},
    {"role": "assistant", "content": "不错！那咱们继续？还有什么需要做的吗？"},

    # ========== Work Block 2: rate limiting in api.py (msg 19-30) ==========
    {"role": "user", "content": (
        "回来了。现在帮我给 api.py 加 rate limiting，要求："
        "100 次/分钟，用 Redis 做后端存储。我们的 Redis 已经跑在 localhost:6379 了。"
    )},
    {"role": "assistant", "content": (
        "Rate limiting 这个好做。其实最简单的方式是用内存来做（比如 Python 字典），"
        "不需要额外依赖 Redis。内存方案在单进程部署时性能更好，延迟更低。\n\n"
        "不过既然你明确要求用 Redis，那我也给你写一个 Redis 版本。\n\n"
        "先说一下 Redis 的滑动窗口限流方案：\n\n"
        "```python\n"
        "import time\n"
        "import redis\n"
        "from functools import wraps\n"
        "from flask import request, jsonify\n\n"
        "# Redis 连接\n"
        "redis_client = redis.Redis(host='localhost', port=6379, db=0)\n\n"
        "# Rate limiting 配置\n"
        "RATE_LIMIT = 100          # 最大请求数\n"
        "RATE_WINDOW = 60          # 时间窗口(秒)\n\n"
        "def rate_limit(func):\n"
        '    """基于 Redis 的滑动窗口限流装饰器"""\n'
        "    @wraps(func)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        client_ip = request.remote_addr\n"
        "        key = f'rate_limit:{client_ip}'\n"
        "        now = time.time()\n\n"
        "        pipe = redis_client.pipeline()\n"
        "        # 移除窗口外的旧记录\n"
        "        pipe.zremrangebyscore(key, 0, now - RATE_WINDOW)\n"
        "        # 添加当前请求\n"
        "        pipe.zadd(key, {str(now): now})\n"
        "        # 获取窗口内的请求数\n"
        "        pipe.zcard(key)\n"
        "        # 设置过期时间\n"
        "        pipe.expire(key, RATE_WINDOW)\n"
        "        results = pipe.execute()\n\n"
        "        request_count = results[2]\n"
        "        if request_count > RATE_LIMIT:\n"
        "            return jsonify({'error': 'Rate limit exceeded',\n"
        "                           'limit': RATE_LIMIT,\n"
        "                           'window': RATE_WINDOW}), 429\n"
        "        return func(*args, **kwargs)\n"
        "    return wrapper\n"
        "```\n\n"
        "这个实现使用 Redis 的 Sorted Set 做滑动窗口，精度比固定窗口高。"
    )},
    {"role": "user", "content": "这个装饰器不错。怎么在 api.py 的路由上使用它？"},
    {"role": "assistant", "content": (
        "很简单，直接在需要限流的路由上加 @rate_limit 装饰器就行。"
        "不过说实话，如果你的服务只部署在一台机器上，用内存方案会更快。\n\n"
        "但既然你坚持用 Redis，这是 api.py 的示例：\n\n"
        "```python\n"
        "from flask import Flask, jsonify\n"
        "from rate_limiter import rate_limit\n\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/api/users', methods=['GET'])\n"
        "@rate_limit\n"
        "def get_users():\n"
        "    return jsonify({'users': []})\n\n"
        "@app.route('/api/users', methods=['POST'])\n"
        "@rate_limit\n"
        "def create_user():\n"
        "    # 处理创建用户逻辑\n"
        "    return jsonify({'status': 'created'}), 201\n\n"
        "@app.route('/api/health')\n"
        "def health_check():\n"
        "    # health check 不加限流\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "if __name__ == '__main__':\n"
        "    app.run(port=5000)\n"
        "```\n\n"
        "注意 health_check 不应该加限流，否则负载均衡器的健康检查会被拒。"
    )},
    {"role": "user", "content": "好的，再加一个自定义错误信息。429 返回时告诉用户还需等多久。"},
    {"role": "assistant", "content": (
        "可以的。我们可以通过 Redis 的 TTL 来计算剩余等待时间。"
        "这是改进版：\n\n"
        "```python\n"
        "def rate_limit_with_retry_after(func):\n"
        '    """带 Retry-After 头的限流装饰器"""\n'
        "    @wraps(func)\n"
        "    def wrapper(*args, **kwargs):\n"
        "        client_ip = request.remote_addr\n"
        "        key = f'rate_limit:{client_ip}'\n"
        "        now = time.time()\n\n"
        "        pipe = redis_client.pipeline()\n"
        "        pipe.zremrangebyscore(key, 0, now - RATE_WINDOW)\n"
        "        pipe.zadd(key, {str(now): now})\n"
        "        pipe.zcard(key)\n"
        "        pipe.zrange(key, 0, 0, withscores=True)\n"
        "        pipe.expire(key, RATE_WINDOW)\n"
        "        results = pipe.execute()\n\n"
        "        request_count = results[2]\n"
        "        if request_count > RATE_LIMIT:\n"
        "            oldest = results[3][0][1] if results[3] else now\n"
        "            retry_after = int(RATE_WINDOW - (now - oldest)) + 1\n"
        "            response = jsonify({\n"
        "                'error': 'Rate limit exceeded',\n"
        "                'retry_after_seconds': retry_after,\n"
        "                'limit': f'{RATE_LIMIT} requests per {RATE_WINDOW}s'\n"
        "            })\n"
        "            response.headers['Retry-After'] = str(retry_after)\n"
        "            return response, 429\n"
        "        return func(*args, **kwargs)\n"
        "    return wrapper\n"
        "```\n\n"
        "这样 429 响应会带上 Retry-After header 和 JSON 中的 retry_after_seconds。"
    )},
    {"role": "user", "content": (
        "完善了。另外需要把限流的配置集中管理，别硬编码在装饰器里。"
        "100 次/分钟的限制要能从 config.yaml 读取。"
    )},
    {"role": "assistant", "content": (
        "好主意，配置外部化是最佳实践。我帮你改成从配置文件读取：\n\n"
        "```python\n"
        "import yaml\n\n"
        "def load_rate_config(config_path='config.yaml'):\n"
        "    with open(config_path) as f:\n"
        "        config = yaml.safe_load(f)\n"
        "    return {\n"
        "        'limit': config.get('rate_limiting', {}).get('requests_per_minute', 100),\n"
        "        'window': config.get('rate_limiting', {}).get('window_seconds', 60),\n"
        "        'redis_url': config.get('rate_limiting', {}).get('redis_url', 'redis://localhost:6379/0'),\n"
        "    }\n"
        "```\n\n"
        "对应的 config.yaml：\n\n"
        "```yaml\n"
        "rate_limiting:\n"
        "  requests_per_minute: 100\n"
        "  window_seconds: 60\n"
        "  redis_url: redis://localhost:6379/0\n"
        "```\n\n"
        "这样你只需要改配置文件就能调整限流参数，不需要动代码。"
    )},
    {"role": "user", "content": "api.py 的 rate limiting 就到这里，改完测试通过了。"},
    {"role": "assistant", "content": (
        "太好了！总结一下 rate limiting 的改动：\n"
        "1. 使用 Redis Sorted Set 实现滑动窗口限流\n"
        "2. 100 次/分钟的限制\n"
        "3. 429 响应带 Retry-After header\n"
        "4. 配置从 config.yaml 读取，不硬编码\n\n"
        "api.py 的改动就完成了。下一步做什么？"
    )},

    # ========== Work Block 3: Docker deployment (msg 31-44) ==========
    {"role": "user", "content": (
        "最后，帮我写 Dockerfile 部署这个服务。"
        "端口用 8080，需要设置 DATABASE_URL 环境变量。"
        "生产镜像要尽量小。"
    )},
    {"role": "assistant", "content": (
        "关于部署，其实你有几个选择：\n\n"
        "1. **Docker** - 传统容器化，你说的这个\n"
        "2. **Kubernetes** - 如果要自动扩缩容和服务发现的话\n"
        "3. **Serverless (AWS Lambda / Cloud Run)** - 如果是事件驱动的轻量服务\n\n"
        "对于你这个场景（Python Flask + Redis + PostgreSQL），我建议先用 Docker 起步，"
        "后面有需求了再迁到 K8s。\n\n"
        "Serverless 不太适合你，因为你的服务需要持久化的 Redis 连接，"
        "cold start 会导致连接池频繁重建。\n\n"
        "先给你一个基础的 Dockerfile：\n\n"
        "```dockerfile\n"
        "# 多阶段构建，减小镜像体积\n"
        "FROM python:3.11-slim as builder\n\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir --user -r requirements.txt\n\n"
        "FROM python:3.11-slim\n\n"
        "WORKDIR /app\n\n"
        "# 从 builder 阶段复制依赖\n"
        "COPY --from=builder /root/.local /root/.local\n"
        "ENV PATH=/root/.local/bin:$PATH\n\n"
        "# 复制应用代码\n"
        "COPY . .\n\n"
        "# 环境变量\n"
        "ENV DATABASE_URL=''\n"
        "ENV REDIS_URL='redis://localhost:6379/0'\n\n"
        "# 暴露端口\n"
        "EXPOSE 8080\n\n"
        "# 启动命令\n"
        "CMD [\"gunicorn\", \"--bind\", \"0.0.0.0:8080\", \"--workers\", \"4\", \"api:app\"]\n"
        "```\n\n"
        "用的是多阶段构建，最终镜像会小很多。"
    )},
    {"role": "user", "content": "K8s 暂时不需要，就用 Docker。加一个 .dockerignore 文件。"},
    {"role": "assistant", "content": (
        "好的。如果将来要上 K8s，可以用 Helm Chart 管理配置，不过现在先不搞那么复杂。\n\n"
        ".dockerignore 文件：\n\n"
        "```\n"
        "__pycache__\n"
        "*.pyc\n"
        "*.pyo\n"
        ".git\n"
        ".gitignore\n"
        ".env\n"
        ".venv\n"
        "venv/\n"
        "*.egg-info\n"
        "dist/\n"
        "build/\n"
        "tests/\n"
        "docs/\n"
        "*.md\n"
        "docker-compose*.yml\n"
        "```\n\n"
        "这个 .dockerignore 排除了测试目录、文档、git 目录等不需要的文件，"
        "可以减小构建上下文，加快 docker build 速度。"
    )},
    {"role": "user", "content": "再写个 docker-compose.yml，把 Redis 和 PostgreSQL 一起编排。"},
    {"role": "assistant", "content": (
        "好的，这里有一个完整的 docker-compose.yml：\n\n"
        "```yaml\n"
        "version: '3.8'\n\n"
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    ports:\n"
        "      - '8080:8080'\n"
        "    environment:\n"
        "      - DATABASE_URL=postgresql://myuser:mypass@db:5432/mydb\n"
        "      - REDIS_URL=redis://redis:6379/0\n"
        "    depends_on:\n"
        "      - db\n"
        "      - redis\n"
        "    restart: unless-stopped\n\n"
        "  db:\n"
        "    image: postgres:16-alpine\n"
        "    environment:\n"
        "      POSTGRES_USER: myuser\n"
        "      POSTGRES_PASSWORD: mypass\n"
        "      POSTGRES_DB: mydb\n"
        "    volumes:\n"
        "      - postgres_data:/var/lib/postgresql/data\n"
        "    ports:\n"
        "      - '5432:5432'\n\n"
        "  redis:\n"
        "    image: redis:7-alpine\n"
        "    ports:\n"
        "      - '6379:6379'\n"
        "    volumes:\n"
        "      - redis_data:/data\n\n"
        "volumes:\n"
        "  postgres_data:\n"
        "  redis_data:\n"
        "```\n\n"
        "启动命令：`docker-compose up -d`\n"
        "查看日志：`docker-compose logs -f app`\n\n"
        "注意 DATABASE_URL 里的 host 用了 `db` 而不是 `localhost`，"
        "因为在 Docker 网络里容器之间用服务名互访。"
    )},
    {"role": "user", "content": "环境变量不应该硬编码在 docker-compose 里，用 .env 文件。"},
    {"role": "assistant", "content": (
        "对的，敏感信息不应该直接写在 docker-compose.yml 里。\n\n"
        "创建 .env 文件：\n\n"
        "```\n"
        "DATABASE_URL=postgresql://myuser:mypass@db:5432/mydb\n"
        "REDIS_URL=redis://redis:6379/0\n"
        "POSTGRES_USER=myuser\n"
        "POSTGRES_PASSWORD=mypass\n"
        "POSTGRES_DB=mydb\n"
        "APP_PORT=8080\n"
        "```\n\n"
        "然后在 docker-compose.yml 中引用：\n\n"
        "```yaml\n"
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    ports:\n"
        "      - '${APP_PORT}:8080'\n"
        "    env_file:\n"
        "      - .env\n"
        "    depends_on:\n"
        "      - db\n"
        "      - redis\n"
        "```\n\n"
        "记得把 .env 加到 .gitignore 里。"
    )},
    {"role": "user", "content": "要加个 healthcheck 到 Dockerfile 里。"},
    {"role": "assistant", "content": (
        "好的，给 Dockerfile 加一个 HEALTHCHECK 指令：\n\n"
        "```dockerfile\n"
        "# 在 CMD 之前添加\n"
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n"
        "    CMD curl -f http://localhost:8080/api/health || exit 1\n"
        "```\n\n"
        "不过这需要容器里有 curl。如果用 slim 镜像可能没有，"
        "可以改用 Python 来做健康检查：\n\n"
        "```dockerfile\n"
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n"
        "    CMD python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')\" || exit 1\n"
        "```\n\n"
        "这样就不需要额外安装 curl 了，用 Python 标准库就行。"
    )},
    {"role": "user", "content": "最终版的 Dockerfile 帮我整理一下，把所有改动合并。"},
    {"role": "assistant", "content": (
        "好的，这是最终版的 Dockerfile，包含了所有讨论的改动：\n\n"
        "```dockerfile\n"
        "# === 阶段 1: 构建 ===\n"
        "FROM python:3.11-slim as builder\n\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir --user -r requirements.txt\n\n"
        "# === 阶段 2: 运行 ===\n"
        "FROM python:3.11-slim\n\n"
        "WORKDIR /app\n\n"
        "# 从 builder 复制 Python 包\n"
        "COPY --from=builder /root/.local /root/.local\n"
        "ENV PATH=/root/.local/bin:$PATH\n\n"
        "# 复制应用代码\n"
        "COPY . .\n\n"
        "# 环境变量 (运行时通过 -e 或 .env 覆盖)\n"
        "ENV DATABASE_URL=''\n"
        "ENV REDIS_URL='redis://localhost:6379/0'\n\n"
        "# 暴露端口\n"
        "EXPOSE 8080\n\n"
        "# 健康检查\n"
        "HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n"
        "    CMD python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')\" || exit 1\n\n"
        "# 启动\n"
        "CMD [\"gunicorn\", \"--bind\", \"0.0.0.0:8080\", \"--workers\", \"4\", \"api:app\"]\n"
        "```\n\n"
        "总结今天所有的改动：\n"
        "1. auth.py - 修复 bcrypt rounds 不一致 bug + 渐进迁移\n"
        "2. api.py - 添加 Redis rate limiting (100次/分钟)\n"
        "3. Dockerfile + docker-compose.yml + .dockerignore - 容器化部署\n\n"
        "如果还有什么需要的就说。"
    )},
    {"role": "user", "content": "完美。今天的工作就到这里，三个文件都改好了：auth.py, api.py, Dockerfile。"},
    {"role": "assistant", "content": (
        "好的，今天的工作总结：\n\n"
        "**修改的文件：**\n"
        "- `auth.py` - bcrypt rounds 统一为 12 + 渐进式迁移\n"
        "- `api.py` - Redis 滑动窗口限流 (100次/分钟)\n"
        "- `Dockerfile` - 多阶段构建 + 健康检查 + 8080 端口\n"
        "- `docker-compose.yml` - 编排 app + PostgreSQL + Redis\n"
        "- `.dockerignore` - 排除不必要文件\n"
        "- `config.yaml` - 限流配置外部化\n\n"
        "辛苦了，有问题随时找我！"
    )},
]

# ============================================================
# 2. Five Factual Questions (about USER decisions)
# ============================================================

QUESTIONS = [
    {
        "id": 1,
        "question": "用户最初报告的登录 bug 的根因是什么？",
        "correct_keywords": ["bcrypt", "salt", "轮数", "rounds", "哈希", "密码"],
        "wrong_keywords": ["redis", "session", "过期"],
        "description": "bcrypt salt rounds mismatch (10 vs 12)",
    },
    {
        "id": 2,
        "question": "用户要求的 rate limiting 上限是多少？",
        "correct_keywords": ["100"],
        "wrong_keywords": [],
        "description": "100 requests per minute",
    },
    {
        "id": 3,
        "question": "用户指定用什么做 rate limiting 的后端存储？",
        "correct_keywords": ["redis"],
        "wrong_keywords": ["内存", "memory", "in-memory"],
        "description": "Redis (not in-memory)",
    },
    {
        "id": 4,
        "question": "用户要求 Docker 容器暴露哪个端口？",
        "correct_keywords": ["8080"],
        "wrong_keywords": ["5000", "80", "443"],
        "description": "Port 8080",
    },
    {
        "id": 5,
        "question": "整个对话中用户修改了哪些文件？请列出所有文件名。",
        "correct_keywords": ["auth.py", "api.py", "dockerfile"],
        "wrong_keywords": [],
        "description": "auth.py, api.py, Dockerfile (at least these 3)",
    },
]


# ============================================================
# 3. Naive Tail-Cut Baseline (Method A)
# ============================================================

def naive_tail_cut(messages, target_tokens, tokenizer):
    """Baseline: just take the last N messages that fit in budget.

    Simple loop from end, accumulating tokens until budget is exceeded.
    Always keeps system messages.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    sys_tokens = sum(len(tokenizer.encode(m.get("content", ""))) for m in system_msgs)
    remaining_budget = target_tokens - sys_tokens

    # Take messages from the end
    kept = []
    used = 0
    for msg in reversed(non_system):
        msg_tokens = len(tokenizer.encode(msg.get("content", "")))
        if used + msg_tokens > remaining_budget:
            break
        kept.insert(0, msg)
        used += msg_tokens

    return system_msgs + kept


# ============================================================
# 4. Context Printer
# ============================================================

def print_context(label, messages, tokenizer):
    """Print compressed context for inspection."""
    total_tokens = sum(len(tokenizer.encode(m.get("content", ""))) for m in messages)
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Messages: {len(messages)} | Tokens: {total_tokens}")
    print(f"{'='*70}")
    for i, msg in enumerate(messages):
        role = msg["role"]
        content = msg["content"]
        # Truncate long content for display
        if len(content) > 200:
            display = content[:100] + " ... " + content[-80:]
        else:
            display = content
        print(f"  [{i:2d}] {role:>10}: {display}")
    print(f"{'='*70}\n")


# ============================================================
# 5. Score an answer
# ============================================================

def score_answer(answer, question_spec):
    """Score an answer: 1 = correct, 0 = wrong/incomplete."""
    answer_lower = answer.lower()

    # Check correct keywords (must have at least one)
    has_correct = False
    for kw in question_spec["correct_keywords"]:
        if kw.lower() in answer_lower:
            has_correct = True
            break

    # Check wrong keywords (must NOT have any dominant wrong answer)
    has_wrong = False
    for kw in question_spec["wrong_keywords"]:
        if kw.lower() in answer_lower:
            # Only count as wrong if it seems like the primary answer
            # (not just mentioned as "not X")
            # Simple heuristic: if wrong keyword appears but correct also appears, OK
            if not has_correct:
                has_wrong = True
                break

    if has_correct and not has_wrong:
        return 1
    return 0


# ============================================================
# 6. Main Test Runner
# ============================================================

def run_test():
    """Run the full A/B test."""
    print("\n" + "=" * 70)
    print("  ConversationStore A/B Test")
    print("  Method A: Naive Tail Cut")
    print("  Method B: Full auto_fit() Pipeline (ConversationStore)")
    print("  Model tier: qwen-1.7b (weak, 2048 context, 512 reserve)")
    print("  Evaluator: glm-4-flash via brain-router MCP")
    print("=" * 70)

    tokenizer = tiktoken.get_encoding("cl100k_base")

    # Calculate original stats
    orig_tokens = sum(len(tokenizer.encode(m.get("content", "")))
                      for m in CONVERSATION)
    print(f"\nOriginal conversation: {len(CONVERSATION)} messages, {orig_tokens} tokens")

    # Target: qwen-1.7b (2048 context - 512 reserve = 1536 target)
    TARGET_TOKENS = 1536

    # ========== Method A: Naive Tail Cut ==========
    print("\n--- Method A: Naive Tail Cut ---")
    context_a = naive_tail_cut(CONVERSATION, TARGET_TOKENS, tokenizer)
    print_context("METHOD A: Naive Tail Cut", context_a, tokenizer)

    # ========== Method B: ConversationStore auto_fit ==========
    print("\n--- Method B: ConversationStore auto_fit ---")
    tmpdir = tempfile.mkdtemp()
    db_store = SQLiteStore(db_path=tmpdir)
    ctx_manager = ContextManager(config_path="/nonexistent/path.yaml", db_store=db_store)

    context_b_msgs, meta_b = ctx_manager.auto_fit(
        messages=CONVERSATION,
        model="qwen-1.7b",
        reserve_tokens=512,
    )
    print(f"  auto_fit metadata: {json.dumps(meta_b, indent=2, ensure_ascii=False)}")
    print_context("METHOD B: ConversationStore auto_fit", context_b_msgs, tokenizer)

    # ========== Prepare prompts for LLM evaluation ==========
    questions_text = "\n".join([
        f"{q['id']}. {q['question']}" for q in QUESTIONS
    ])

    def build_eval_prompt(context_msgs):
        """Build evaluation prompt with compressed context."""
        context_str = "\n".join([
            f"[{m['role']}]: {m['content']}" for m in context_msgs
        ])
        return (
            f"以下是一段对话记录（可能已被压缩）：\n\n"
            f"{context_str}\n\n"
            f"---\n\n"
            f"根据以上对话内容，请回答以下 5 个问题。"
            f"每个问题请用一句话简洁回答，不要编造信息，"
            f"如果对话中没有提到则回答'对话中未提及'。\n\n"
            f"{questions_text}\n\n"
            f"请按格式回答：\n"
            f"1. [答案]\n"
            f"2. [答案]\n"
            f"3. [答案]\n"
            f"4. [答案]\n"
            f"5. [答案]"
        )

    prompt_a = build_eval_prompt(context_a)
    prompt_b = build_eval_prompt(context_b_msgs)

    # Print prompts for transparency
    print(f"\n{'='*70}")
    print("  PROMPT A (Naive Tail Cut) - length: {} chars".format(len(prompt_a)))
    print(f"{'='*70}")
    print(prompt_a[:2000])
    if len(prompt_a) > 2000:
        print(f"\n... ({len(prompt_a) - 2000} more chars) ...\n")

    print(f"\n{'='*70}")
    print("  PROMPT B (ConversationStore) - length: {} chars".format(len(prompt_b)))
    print(f"{'='*70}")
    print(prompt_b[:2000])
    if len(prompt_b) > 2000:
        print(f"\n... ({len(prompt_b) - 2000} more chars) ...\n")

    # Save prompts to files for inspection
    prompt_a_path = os.path.join(tmpdir, "prompt_a.txt")
    prompt_b_path = os.path.join(tmpdir, "prompt_b.txt")
    with open(prompt_a_path, "w") as f:
        f.write(prompt_a)
    with open(prompt_b_path, "w") as f:
        f.write(prompt_b)
    print(f"\n  Full prompts saved to:\n  A: {prompt_a_path}\n  B: {prompt_b_path}")

    # Return data for MCP-based evaluation
    return {
        "prompt_a": prompt_a,
        "prompt_b": prompt_b,
        "context_a": context_a,
        "context_b": context_b_msgs,
        "meta_b": meta_b,
        "questions": QUESTIONS,
        "tmpdir": tmpdir,
    }


if __name__ == "__main__":
    data = run_test()
    print("\n\n" + "=" * 70)
    print("  Test data prepared. Prompts ready for MCP evaluation.")
    print("  Run the MCP calls externally to complete the A/B test.")
    print("=" * 70)
