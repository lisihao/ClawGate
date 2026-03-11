# ClawGate 关键依赖

## 核心依赖

### ContextPilot（必需）

**作用**：KV Cache 感知的上下文优化

**重要性**：⭐⭐⭐⭐⭐（关键性能优化）

**功能**：
- **Level 1 — Reorder**: 重排上下文块以最大化 KV cache 前缀共享，实现高达 3× prefill 加速
- **Level 2 — Dedup**: 多轮对话中去除重复文档块，节省约 25-30% prompt tokens

**安装**：

```bash
# 方式 1：通过 setup.sh 自动安装（推荐）
./scripts/setup.sh

# 方式 2：手动安装
cd /path/to/ClawGate
git clone https://github.com/EfficientContext/ContextPilot.git vendor/contextpilot
pip install ujson numpy scipy tqdm elasticsearch==8.18.1
```

**验证**：

```bash
python3 -c "import sys; sys.path.insert(0, 'vendor/contextpilot'); from contextpilot.server.live_index import ContextPilot; print('✅ ContextPilot OK')"
```

**影响**：
- ✅ 启用：KV Cache 优化 + 多轮去重，显著提升性能
- ❌ 禁用：ClawGate 仍可运行，但失去上下文优化能力

---

## 可选依赖

### Tantivy

**作用**：高性能全文搜索引擎（Rust）

**重要性**：⭐⭐⭐（可选）

**安装**：
```bash
pip install tantivy
```

### MLX（Apple Silicon）

**作用**：Apple Silicon 优化的推理框架

**重要性**：⭐⭐⭐⭐（Apple Silicon 用户必需）

**安装**：
```bash
pip install mlx mlx-lm
```

---

## 依赖检查清单

启动 ClawGate 前，确保以下依赖已安装：

- [ ] **ContextPilot** — `vendor/contextpilot/` 目录非空
- [ ] **FastAPI** — `pip list | grep fastapi`
- [ ] **httpx** — `pip list | grep httpx`
- [ ] **tiktoken** — `pip list | grep tiktoken`
- [ ] **aiosqlite** — `pip list | grep aiosqlite`

---

## 故障排查

### ContextPilot 不可用

**症状**：
```
[ContextPilot] 库不可用: No module named 'contextpilot'
ℹ️  ContextPilot 不可用 (跳过上下文重排优化)
```

**解决**：
1. 检查 `vendor/contextpilot/` 目录是否存在且非空
2. 重新克隆：`git clone https://github.com/EfficientContext/ContextPilot.git vendor/contextpilot`
3. 安装依赖：`pip install ujson numpy scipy tqdm elasticsearch`

### ImportError: No module named 'ujson'

**解决**：
```bash
pip install ujson numpy
```

### elasticsearch 安装失败

**影响**：不影响 ContextPilot 核心功能（仅影响 BM25 检索器）

**解决**：
```bash
# 可忽略，或手动安装
pip install elasticsearch==8.18.1
```

---

## 依赖版本要求

| 依赖 | 最低版本 | 推荐版本 |
|------|---------|---------|
| Python | 3.10 | 3.11+ |
| FastAPI | 0.115.0 | latest |
| httpx | 0.28.0 | latest |
| ContextPilot | — | latest (from git) |
| ujson | — | latest |
| numpy | — | latest |
| scipy | — | latest |
| elasticsearch | 8.18.0 | 8.18.1 |

---

*最后更新：2026-03-11*
