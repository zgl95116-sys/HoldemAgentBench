# HoldemAgentBench 开发文档 v2.2

> 一个基于 Claude Code 的多模型扑克对抗评测平台

**项目缩写**：HAB
**口号**：When agents take a seat at the table, who walks away with the chips?

---

## 📖 文档说明

本文档是**完整开发规范**，供 Claude Code 作为实施参考。

**v2.2 关键变化**：

- 项目正式定名为 **HoldemAgentBench**（缩写 HAB）
- 所有 CLI 命令、包名、目录结构按新命名统一
- 其他设计保持 v2.1 不变

**v2.1 关键内容回顾**：
- 模型接入采用 OpenRouter + 内置 shim
- Orchestrator 完全自动化进程管理（用户只开一个终端）
- Agent 改为"按需唤醒"模式（headless Claude Code）
- 第 13 章：完整三层评分系统
- 第 14 章：methodology.md 模板
- 第 15 章：GitHub Pages 部署

**给 Claude Code 实施者的提示**：

- 这份文档是一次性塞给你的完整设计规范
- 一气呵成把整个项目实施完成，不要停下来等确认
- Phase 划分是内部检查点（用于跑测试），不是时间表
- 估算总工时：**4-6 小时连续工作**
- 真正消耗时间的是跑实测、调 bug、准备数据

---

## 1. 项目概览

### 1.1 一句话定位

HoldemAgentBench (HAB) 让不同厂商的 LLM（Claude、GPT、Gemini、DeepSeek 等）在 **Claude Code agent 环境**下，武装完整的扑克工具链，进行德州扑克对抗评测。

### 1.2 核心价值主张

不同于现有的"LLM 打扑克"项目（纯 API 调用），HoldemAgentBench 评测的是 **"模型 + agent 脚手架"** 的组合能力：

- **工具使用能力**（调用 equity calculator、GTO solver 等）
- **长期记忆能力**（跨 session 的对手建模）
- **元认知能力**（subagent 分工、自我复盘）

### 1.3 用户体验目标

```bash
# 三行命令出结果，零终端管理
pip install holdem-agent-bench
export OPENROUTER_API_KEY=sk-or-...
hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5
```

### 1.4 核心研究问题

HoldemAgentBench 能回答这些目前**没有公开答案**的问题：

1. Claude Code + 工具链给各模型带来多少扑克能力增量？
2. 哪些模型更"会用工具"？
3. 持久笔记是否让模型发展出显式对手模型？
4. Subagent 分工是否比单 agent 深度思考更强？

### 1.5 非目标

- ❌ 训练专用扑克模型（不是 RL 项目）
- ❌ 打败 GTO solver（做不到）
- ❌ 真钱扑克（仅 play money）
- ❌ 支持其他游戏（聚焦扑克）

---

## 2. 技术架构

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────┐
│              Single User-Facing CLI (one terminal)        │
│   hab run quickstart ...                                  │
└──────────────────────────────────────────────────────────┘
                           ↓
┌──────────────────────────────────────────────────────────┐
│                  Orchestrator (in-process)                │
│   ┌────────────┬──────────────┬────────────────┐         │
│   │ Lifecycle  │ Agent Pool   │ Live Dashboard │         │
│   │ Manager    │ (concurrency)│ (Rich TUI)     │         │
│   └────────────┴──────────────┴────────────────┘         │
└──────────────────────────────────────────────────────────┘
            ↓                    ↓                  ↓
┌────────────────┐  ┌─────────────────┐  ┌────────────────┐
│  Game Engine   │  │  OpenRouter     │  │ MCP Tool       │
│  (pokerkit)    │  │  Shim Server    │  │ Server         │
│                │  │  (auto-launched)│  │ (auto-launched)│
└────────────────┘  └─────────────────┘  └────────────────┘
            ↓                ↑                    ↑
            ↓                │                    │
┌──────────────────────────────────────────────────────────┐
│         Headless Claude Code Subprocesses                 │
│   (按需启动 / 完成即退出 / 每次决策一个进程)              │
│                                                            │
│   player_a/    player_b/    player_c/    player_d/        │
│   (workspace)  (workspace)  (workspace)  (workspace)      │
└──────────────────────────────────────────────────────────┘
            ↓ (持久化)
┌──────────────────────────────────────────────────────────┐
│  Persistent State (filesystem)                            │
│  - notes/opponents/*.md (跨决策记忆)                      │
│  - hands/*.json (完整手牌历史)                            │
│  - game_view/*.json (当前局面状态)                        │
└──────────────────────────────────────────────────────────┘
```

### 2.2 关键设计决策

| 决策点 | 选择 | 理由 |
|-------|------|------|
| 扑克引擎 | `pokerkit` | 最成熟 Python 库，ACPC 级别权威 |
| Agent 通信 | 文件系统 + JSON | 简单、可调试、天然持久化、可审计 |
| 多模型接入 | OpenRouter + 内置 shim | 一个 key 接 300+ 模型 |
| Agent 运行模式 | 按需唤醒（headless） | 成本可控、隔离清晰、崩溃恢复容易 |
| 工具分发 | MCP Server | Claude Code 原生支持 |
| 进程管理 | 单进程 orchestrator | 用户零终端管理 |
| 存储 | SQLite + JSON 文件 | 单机够用，无重型依赖 |
| Dashboard | Streamlit + Rich TUI | 实时进度 + 离线分析 |
| 公开 Leaderboard | 渐进式（README → Pages → HF Space） | 见第 13 章 |
| 可选直连 | Anthropic 直 API（Claude 模型） | 用户有订阅时省钱 |

### 2.3 技术栈

```yaml
语言: Python 3.11+
扑克引擎: pokerkit >= 0.5
Agent 运行时: Claude Code CLI (headless mode)
模型接入: OpenRouter (主) + 直接 Anthropic API (可选)
内置 Shim: FastAPI (Anthropic↔OpenAI 格式转换)
MCP 框架: mcp (官方 Python SDK)
进程管理: asyncio + subprocess
数据存储: SQLite (统计) + JSON 文件 (牌局)
分析: pandas, numpy, scipy
可视化: streamlit (web), rich (TUI)
公开榜单前端: vanilla HTML/JS + Alpine.js (GitHub Pages)
公开榜单托管: GitHub Pages → HuggingFace Space (后续)
测试: pytest, pytest-asyncio
打包发布: PyPI (包名 holdem-agent-bench)
```

### 2.4 Agent 运行模式：按需唤醒

**核心原则**：**记忆在文件里，不在进程里**。

每个决策的生命周期：

```
1. Engine 写入 game_view/current_state.json
2. Orchestrator 启动新的 Claude Code 进程（headless mode）
3. Claude Code 进程读 CLAUDE.md、skills、notes、current_state
4. 思考、调工具、写入 actions/action.json
5. 进程退出（释放资源）
6. Engine 读取 action,继续游戏
```

**记忆从哪来？**

- `notes/opponents/*.md`（持久笔记）
- `journal/hand_journal.md`（手牌日志）
- `hands/` 目录下的历史（可查询）

每手牌结算后，agent 主动更新 notes，下次决策从 notes 恢复"记忆"。这跟人类扑克手做笔记的方式一致。

**优势**：

- 单次决策成本可预估（context 大小有限）
- 进程崩溃不影响整个 session
- 可任意并发（受内存限制）
- Token 消耗远低于"常驻进程"模式

---

## 3. 目录结构

```
holdem-agent-bench/
├── README.md
├── pyproject.toml                  # 包名: holdem-agent-bench
├── LICENSE                         # MIT
├── .env.example
│
├── src/
│   ├── hab/                        # 主包名
│   │   ├── __init__.py
│   │   ├── cli/
│   │   │   ├── main.py             # `hab` 命令入口
│   │   │   ├── init.py             # `hab init`
│   │   │   ├── run.py              # `hab run`
│   │   │   ├── dashboard.py        # `hab dashboard`
│   │   │   ├── submit.py           # `hab submit`
│   │   │   └── models.py           # `hab models list`
│   │   │
│   │   ├── engine/                 # 扑克引擎
│   │   │   ├── game_master.py
│   │   │   ├── state.py
│   │   │   ├── actions.py
│   │   │   └── recorder.py
│   │   │
│   │   ├── orchestrator/           # 进程编排（核心）
│   │   │   ├── lifecycle.py
│   │   │   ├── agent_pool.py
│   │   │   ├── workspace_manager.py
│   │   │   └── progress.py
│   │   │
│   │   ├── shim/                   # OpenRouter shim
│   │   │   ├── server.py
│   │   │   ├── translator.py
│   │   │   └── router.py
│   │   │
│   │   ├── mcp_server/             # 工具链
│   │   │   ├── server.py
│   │   │   └── tools/
│   │   │       ├── equity.py
│   │   │       ├── range_analyzer.py
│   │   │       ├── gto_lookup.py
│   │   │       ├── opponent_db.py
│   │   │       ├── hand_search.py
│   │   │       ├── pot_odds.py
│   │   │       └── notes.py
│   │   │
│   │   ├── analytics/              # 评分核心（见第 13 章）
│   │   │   ├── stats.py
│   │   │   ├── elo.py
│   │   │   ├── duplicate.py
│   │   │   ├── confidence.py
│   │   │   ├── leaderboard.py
│   │   │   └── reports.py
│   │   │
│   │   └── dashboard/
│   │       ├── app.py
│   │       └── pages/
│   │
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── e2e/
│
├── agent_templates/                # Workspace 模板
│   ├── CLAUDE.md
│   ├── .claude/
│   │   └── mcp_servers.json
│   └── skills/
│       ├── poker-fundamentals/
│       ├── opponent-modeling/
│       ├── gto-reference/
│       └── meta-strategy/
│
├── configs/
│   ├── presets/
│   │   ├── quickstart.yaml
│   │   ├── daily-bench.yaml
│   │   ├── full-benchmark.yaml
│   │   └── agent-vs-bare.yaml
│   └── default_models.yaml
│
├── scripts/
│   ├── setup_gto_data.py
│   ├── generate_report.py
│   ├── update_leaderboard.py       # 自动更新榜单
│   └── deploy_pages.py             # 部署到 GitHub Pages
│
├── docs/                           # GitHub Pages 根目录
│   ├── index.html                  # Leaderboard 主页
│   ├── methodology.html
│   ├── replay.html
│   ├── methodology.md              # 方法论原文
│   ├── architecture.md
│   ├── custom_models.md
│   ├── api_reference.md
│   ├── faq.md
│   ├── assets/
│   │   ├── style.css
│   │   └── leaderboard.js
│   └── data/
│       ├── leaderboard.json
│       ├── runs/
│       │   ├── 2026-04-20.json
│       │   └── ...
│       └── models.json
│
├── examples/
│   ├── quickstart.py
│   ├── custom_model.py
│   └── modify_toolkit.py
│
└── official_runs/                  # 官方 benchmark 原始数据
    └── 2026-XX/
```

**命名约定速查**：

| 资产 | 名字 |
|------|-----|
| 项目名 | HoldemAgentBench |
| 缩写 | HAB |
| GitHub repo | `holdem-agent-bench` |
| PyPI 包 | `holdem-agent-bench` |
| Python import | `import hab` |
| CLI 命令 | `hab` |
| 配置目录 | `~/.hab/` |
| 默认输出目录 | `~/hab-sessions/` |
| 公开站点 | `<user>.github.io/holdem-agent-bench` |
| 社交标签 | `#HoldemAgentBench` 或 `#HAB` |

---

## 4. 核心模块规范

### 4.1 用户 CLI (`src/hab/cli/`)

#### 4.1.1 设计原则

- 一个命令行入口：`hab`
- 子命令清晰：`init`、`run`、`dashboard`、`submit`、`models`
- 进度可见：实时 Rich TUI
- 可中断恢复

#### 4.1.2 核心命令

```bash
# 初始化
hab init

# 列出可用模型
hab models list [--provider openrouter]

# 运行 session
hab run <preset> [options]
  presets: quickstart | daily-bench | full-benchmark | agent-vs-bare
  options:
    --models <m1,m2,...>
    --hands <n>
    --output <dir>
    --budget <$>
    --resume <session_id>

# 看板
hab dashboard [<session_dir>]

# 提交分数
hab submit <session_dir>

# 资源管理
hab sessions list
hab sessions clean --older-than 7d
```

#### 4.1.3 用户首次体验

```
$ pip install holdem-agent-bench
$ hab init

🃏 Welcome to HoldemAgentBench (HAB)!

We use OpenRouter to access 300+ LLM models with one API key.
Get yours at: https://openrouter.ai/keys

> OpenRouter API key (sk-or-...): ****
> Optional: Anthropic API key for direct Claude access (saves cost): [skip]
> Default budget per session: $50
> Output directory: ~/hab-sessions

✅ Configuration saved to ~/.hab/config.yaml

Run your first benchmark:
  hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5
```

### 4.2 OpenRouter Shim (`src/hab/shim/`)

#### 4.2.1 职责

- 提供本地 HTTP endpoint，伪装成 Anthropic API
- 自动转发请求到 OpenRouter 或 Anthropic
- 处理格式差异（特别是 tool_use）
- 由 orchestrator 自动启动/关闭，用户无感知

#### 4.2.2 启动逻辑

```python
# src/hab/shim/server.py
from fastapi import FastAPI
import uvicorn
import socket

class ShimServer:
    def __init__(self, openrouter_key: str, anthropic_key: str = None):
        self.openrouter_key = openrouter_key
        self.anthropic_key = anthropic_key
        self.app = FastAPI()
        self.port = self._find_free_port()
        self._setup_routes()

    def _find_free_port(self) -> int:
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"

    async def start(self):
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())

    async def stop(self):
        self.server.should_exit = True
        await self.task
```

#### 4.2.3 路由策略

```python
# src/hab/shim/router.py
def route_request(model: str, anthropic_key: str | None) -> str:
    if model.startswith("anthropic/") and anthropic_key:
        return "anthropic_direct"
    return "openrouter"
```

#### 4.2.4 格式转换

```python
# src/hab/shim/translator.py
class FormatTranslator:
    @staticmethod
    def anthropic_request_to_openai(req: dict) -> dict:
        """Anthropic /v1/messages → OpenAI /v1/chat/completions"""
        ...

    @staticmethod
    def openai_response_to_anthropic(resp: dict) -> dict:
        ...
```

#### 4.2.5 端点实现

```python
@app.post("/v1/messages")
async def messages_endpoint(request: dict):
    model = request["model"]

    if route_request(model, anthropic_key) == "anthropic_direct":
        return await forward_to_anthropic(request)

    openai_request = translator.anthropic_request_to_openai(request)
    openai_response = await forward_to_openrouter(openai_request)
    return translator.openai_response_to_anthropic(openai_response)
```

#### 4.2.6 关键挑战与对策

| 问题 | 对策 |
|------|------|
| 不同模型 tool_use 格式微差 | 在 translator 中做归一化 |
| 流式响应转换复杂 | MVP 先支持非流式 |
| 某些模型乱加 markdown 包裹 JSON | 后处理清理 |
| token 计数不一致 | 用 tiktoken 统一计算 |
| OpenRouter 限流 | 自动 retry with backoff |

### 4.3 Orchestrator (`src/hab/orchestrator/`)

#### 4.3.1 职责

- 启动/关闭 shim、MCP server、引擎
- 管理 agent 进程池
- 实时进度显示
- 崩溃恢复
- 预算监控

#### 4.3.2 主流程

```python
# src/hab/orchestrator/lifecycle.py

class HABSession:
    def __init__(self, config: SessionConfig):
        self.config = config
        self.shim: ShimServer
        self.mcp_server: MCPServer
        self.engine: GameEngine
        self.agent_pool: AgentPool
        self.progress: ProgressDisplay

    async def run(self) -> SessionResult:
        try:
            await self._startup()
            result = await self._main_loop()
            return result
        finally:
            await self._cleanup()

    async def _startup(self):
        self.session_dir = self._create_session_dir()
        self.shim = ShimServer(self.config.openrouter_key, self.config.anthropic_key)
        await self.shim.start()
        self.mcp_server = MCPServer(self.session_dir)
        await self.mcp_server.start()
        for player_id, model in self.config.players.items():
            workspace = self._create_workspace(player_id, model)
        self.engine = GameEngine(self.config, self.session_dir)
        self.agent_pool = AgentPool(
            shim_url=self.shim.base_url,
            mcp_endpoint=self.mcp_server.endpoint,
            max_concurrent=self.config.max_concurrent_agents,
        )
        self.progress = ProgressDisplay(self.config)
        await self.progress.start()

    async def _main_loop(self):
        async for event in self.engine.events():
            if event.type == "action_needed":
                asyncio.create_task(self._handle_action_request(event))
            elif event.type == "hand_complete":
                self.progress.update(event)
                await self._check_budget()
            elif event.type == "session_complete":
                break

    async def _handle_action_request(self, event):
        action = await self.agent_pool.request_action(
            player_id=event.player_id,
            model=self.config.players[event.player_id],
            workspace=self._workspace_for(event.player_id),
            timeout=self.config.decision_timeout,
        )
        await self.engine.submit_action(event.player_id, action)

    async def _cleanup(self):
        await self.agent_pool.shutdown()
        await self.mcp_server.stop()
        await self.shim.stop()
        await self.progress.stop()
```

#### 4.3.3 Agent Pool（核心）

```python
# src/hab/orchestrator/agent_pool.py
class AgentPool:
    def __init__(self, shim_url, mcp_endpoint, max_concurrent=4):
        self.shim_url = shim_url
        self.mcp_endpoint = mcp_endpoint
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_processes = {}

    async def request_action(self, player_id, model, workspace, timeout=300):
        async with self.semaphore:
            self._reset_scratch(workspace)
            env = self._build_env(player_id, model, workspace)

            proc = await asyncio.create_subprocess_exec(
                "claude",
                "-p", self._build_prompt(player_id),
                "--output-format", "stream-json",
                "--dangerously-skip-permissions",
                cwd=str(workspace),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=os.setsid if os.name != 'nt' else None,
            )

            self.active_processes[player_id] = proc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )

                action_file = workspace / "actions" / "action.json"
                if not action_file.exists():
                    return self._fold_action("agent_no_output")

                action = json.loads(action_file.read_text())
                self._log_usage(player_id, stdout)
                return action

            except asyncio.TimeoutError:
                self._kill_process_group(proc)
                return self._fold_action("timeout")
            except Exception as e:
                return self._fold_action(f"error: {e}")
            finally:
                self.active_processes.pop(player_id, None)

    def _build_env(self, player_id, model, workspace):
        return {
            **os.environ,
            "ANTHROPIC_BASE_URL": self.shim_url,
            "ANTHROPIC_AUTH_TOKEN": "hab-internal",
            "ANTHROPIC_MODEL": model,
            "PLAYER_ID": player_id,
            "SESSION_ID": str(workspace.parent.name),
            "MCP_ENDPOINT": self.mcp_endpoint,
        }

    def _build_prompt(self, player_id):
        return f"""你是 {player_id}。请按 CLAUDE.md 的规则参与扑克对局。
读取当前状态、使用工具分析、写入决策。"""

    def _fold_action(self, reason):
        return {
            "action": "fold",
            "reason": reason,
            "tool_calls_used": [],
        }

    async def shutdown(self):
        for proc in list(self.active_processes.values()):
            self._kill_process_group(proc)
        self.active_processes.clear()

    def _kill_process_group(self, proc):
        if os.name == 'nt':
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
```

#### 4.3.4 进度显示

```
┌─ HoldemAgentBench - daily-bench ─────────────────────┐
│ Models: 4 | Hands: 1000 | Budget: $50 / $45 used     │
├──────────────────────────────────────────────────────┤
│ Table 1: h_127/500    Table 2: h_134/500             │
│   claude-opus thinking  gpt-5 idle                    │
│   gpt-5 idle           deepseek thinking              │
│   gemini idle          claude-opus idle               │
├──────────────────────────────────────────────────────┤
│ Standings (after 261 hands):                          │
│  1. claude-opus-4-7  +18.2 BB/100 (±5.3)             │
│  2. gpt-5            +12.4 BB/100 (±4.8)             │
│  3. gemini-3-pro     +2.1  BB/100 (±5.1)             │
│  4. deepseek-r1      -28.7 BB/100 (±6.2)             │
├──────────────────────────────────────────────────────┤
│ Elapsed: 23:15 | ETA: 1h 38m | 'd' dashboard | 'q' quit│
└──────────────────────────────────────────────────────┘
```

### 4.4 游戏引擎 (`src/hab/engine/`)

#### 4.4.1 状态文件格式

**`current_state.json`**：
```json
{
  "hand_id": "h_20260424_001_042",
  "table_id": "table_1",
  "street": "flop",
  "board": ["Qs", "Jh", "2c"],
  "pot": 120,
  "to_act": "player_a",
  "stacks": {"player_a": 480, "player_b": 420},
  "current_bet": 40,
  "action_history": [...],
  "legal_actions": [
    {"type": "fold"},
    {"type": "call", "amount": 40},
    {"type": "raise", "min": 80, "max": 480}
  ],
  "deadline": "2026-04-24T15:30:45Z"
}
```

**`hole_cards.json`**（私密）：
```json
{
  "hand_id": "h_20260424_001_042",
  "cards": ["As", "Kh"]
}
```

**`action.json`**：
```json
{
  "hand_id": "h_20260424_001_042",
  "action": "raise",
  "amount": 120,
  "reasoning": "optional",
  "tool_calls_used": ["equity_calculator", "gto_lookup"],
  "timestamp": "2026-04-24T15:30:42Z"
}
```

#### 4.4.2 验证规则

- action 必须匹配 `legal_actions`
- amount 必须在合法区间内
- 超时（默认 5 分钟）视为 fold
- 非法 action 视为 fold 并记录违规

#### 4.4.3 事件流

```python
class GameEngine:
    async def events(self) -> AsyncIterator[Event]:
        yield Event(type="session_start", ...)
        for hand_num in range(self.config.hands_target):
            yield Event(type="hand_start", hand_id=...)
            while not hand.complete:
                yield Event(type="action_needed", ...)
                await self.action_received.wait()
            yield Event(type="hand_complete", result=...)
        yield Event(type="session_complete", final_stats=...)
```

### 4.5 MCP Server (`src/hab/mcp_server/`)

#### 4.5.1 架构

单个 MCP server 提供所有工具。通过 `mcp_servers.json` 挂载到每个 agent workspace。由 orchestrator **自动启动**。

```json
{
  "mcpServers": {
    "hab-poker-toolkit": {
      "command": "python",
      "args": ["-m", "hab.mcp_server.server"],
      "env": {
        "PLAYER_ID": "${PLAYER_ID}",
        "SESSION_ID": "${SESSION_ID}"
      }
    }
  }
}
```

**重要**：MCP server 通过 `PLAYER_ID` 环境变量识别调用者，隔离其 notes 和 opponent_db 访问权限。

#### 4.5.2 七个工具规范

##### Tool 1: `equity_calculator`

```python
@tool
def equity_calculator(
    my_cards: list[str],
    board: list[str] = [],
    opponent_range: str = "random",
    num_opponents: int = 1,
    simulations: int = 10000
) -> dict:
    """
    返回：
    {
      "equity": 0.62,
      "tie": 0.02,
      "win": 0.60,
      "breakdown": {...},
      "simulations_run": 10000,
      "confidence": "high"
    }
    """
```

##### Tool 2: `range_analyzer`

```python
@tool
def range_analyzer(
    opponent_id: str,
    action_sequence: list[dict],
    board: list[str] = [],
    position: str = None,
    stack_depth_bb: int = 100
) -> dict:
    """估算对手范围"""
```

##### Tool 3: `gto_lookup`

```python
@tool
def gto_lookup(
    position_scenario: str,
    action_sequence: str,
    my_cards: list[str],
    stack_depth_bb: int = 100
) -> dict:
    """查 GTO 基线策略"""
```

##### Tool 4: `opponent_database_query`

```python
@tool
def opponent_database_query(
    opponent_id: str,
    filters: dict = {}
) -> dict:
    """查对手统计"""
```

##### Tool 5: `hand_history_search`

```python
@tool
def hand_history_search(
    query: str,
    opponent_id: str = None,
    my_position: str = None,
    limit: int = 5
) -> list[dict]:
    """搜索历史类似场景"""
```

##### Tool 6: `pot_odds_calculator`

```python
@tool
def pot_odds_calculator(
    pot: float,
    bet_to_call: float,
    my_equity: float = None
) -> dict:
    """底池赔率计算"""
```

##### Tool 7: `note_manager`

```python
@tool
def note_manager(
    action: str,
    opponent_id: str,
    observation_type: str = None,
    content: str = None,
    hand_id: str = None
) -> dict:
    """管理对手笔记"""
```

### 4.6 Skills 与 CLAUDE.md

#### 4.6.1 `meta-strategy/SKILL.md`（最关键）

```markdown
# Meta-Strategy: How to Play Each Hand

## 决策流程

每当你被要求做决策时，严格按以下步骤：

### Step 1: 读取当前状态
- 读 game_view/current_state.json
- 读 game_view/hole_cards.json

### Step 2: 评估底池重要性
- Pot < 10bb：Quick Decision
- Pot 10-50bb：Standard Decision
- Pot > 50bb：Deep Analysis（召唤 hand_analyst subagent）

### Step 3a: Standard Decision
必做工具调用：
1. equity_calculator
2. pot_odds_calculator
3. opponent_database_query（若对手有足够历史）

### Step 4: 写入 action
- 必须记录本次用了哪些工具（tool_calls_used 字段）

### Step 5: 更新笔记（手牌结束后）
```

#### 4.6.2 CLAUDE.md 模板

```markdown
# HAB PokerAgent - {player_id}

你是 {player_id}，参与 HoldemAgentBench 扑克对局。

## 你的工作方式

事件驱动。每当 game_view/action_queue.json 标记你需要行动时：
1. 阅读当前状态
2. 使用工具分析
3. 写入 actions/action.json

## 工具链
- equity_calculator
- range_analyzer
- gto_lookup
- opponent_database_query
- hand_history_search
- pot_odds_calculator
- note_manager

## 知识库（Skills）
- poker-fundamentals
- opponent-modeling
- gto-reference
- meta-strategy（必读）

## 持久记忆
- notes/opponents/{id}.md
- notes/strategy.md
- notes/observations.md

## 核心约束
1. 绝不作弊
2. 诚实记录 tool_calls_used
3. 5 分钟内决策
4. 严格按 legal_actions 格式
```

### 4.7 Analytics（详见第 13 章）

```python
# src/hab/analytics/stats.py
class PlayerStats:
    @property
    def bb_per_100(self) -> float: ...
    def confidence_interval(self, metric, confidence=0.95): ...

# src/hab/analytics/elo.py
class EloSystem:
    def update_after_session(self, session_results): ...

# src/hab/analytics/duplicate.py
class DuplicatePokerAnalyzer:
    def analyze(self, sessions): ...
```

完整公式与实现见**第 13 章**。

---

## 5. 配置文件规范

### 5.1 用户配置 `~/.hab/config.yaml`

```yaml
providers:
  openrouter:
    api_key: ${OPENROUTER_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    use_for_claude_models: true

defaults:
  budget_per_session_usd: 50
  output_dir: ~/hab-sessions
  max_concurrent_agents: 4
  decision_timeout_sec: 300

dashboard:
  auto_open: true
  port: 8501
```

### 5.2 内置赛制

```yaml
# configs/presets/quickstart.yaml
preset:
  name: quickstart
  estimated_cost_usd: 5
  estimated_time_min: 20
session:
  format: heads-up
  hands_target: 100
game:
  blinds: { small: 1, big: 2 }
  starting_stack: 200
tables: { count: 1 }
```

```yaml
# configs/presets/daily-bench.yaml
preset:
  name: daily-bench
  estimated_cost_usd: 50
  estimated_time_min: 120
session:
  format: 6-max
  hands_target: 1000
tables:
  count: 2
  rotation: true
evaluation:
  variance_reduction: duplicate
```

### 5.3 默认模型列表

```yaml
recommended_models:
  flagship:
    - anthropic/claude-opus-4-7
    - openai/gpt-5
    - google/gemini-3-pro
    - x-ai/grok-4
  efficient:
    - anthropic/claude-sonnet-4-7
    - openai/gpt-5-mini
    - google/gemini-3-flash
    - deepseek/deepseek-reasoner
  open_source:
    - meta-llama/llama-4-maverick
    - qwen/qwen3-235b
    - moonshotai/kimi-k2
```

---

## 6. 公平性与防作弊

### 6.1 公平性保证

| 维度 | 措施 |
|------|------|
| System prompt | 完全相同的 CLAUDE.md |
| 工具集 | 共享同一个 MCP server endpoint |
| Context 长度 | 统一截断（默认 50k tokens） |
| 决策时间 | 统一超时（默认 5 分钟） |
| 工具调用预算 | 每决策最多 10 次 tool call |
| 位置平衡 | 6-max 自动轮转 |
| 发牌随机性 | 固定 seed |

### 6.2 防作弊

| 威胁 | 防御 |
|-----|------|
| 偷看对方底牌 | workspace 文件权限隔离 |
| 篡改 action | Engine 验证 + HMAC 签名 |
| 超时刷分 | 硬超时 fold |
| 工具调用造假 | MCP server 端日志比对 |

---

## 7. 开发实施指南

### 7.1 实施原则

**这份文档是一次性塞给 Claude Code 的完整设计规范**。一气呵成完成所有 Phase。

按小时级里程碑：

```
0:00-0:30  搭骨架 + pokerkit 引擎 + shim
0:30-1:00  Agent pool + 文件系统通信 + CLAUDE.md
1:00-1:30  跑通第一场 100 手 HU（MVP 完成）
1:30-2:30  MCP server + 7 个工具
2:30-3:30  6-max + subagent + 评测系统
3:30-4:00  Dashboard + 报告
4:00-4:30  打包 + README + GitHub Pages 部署
```

### 7.2 实施检查点

#### Phase 1: MVP

- [ ] 项目骨架（pyproject.toml 包名 holdem-agent-bench、目录结构）
- [ ] OpenRouter shim 基础版
- [ ] pokerkit 引擎封装（HU 模式）
- [ ] Agent pool 基础实现
- [ ] 最小 CLAUDE.md
- [ ] CLI: `hab run quickstart`
- [ ] e2e 测试：100 手 HU

**验收**：单命令跑完 100 手，自动清理子进程。

#### Phase 2: 工具链

- [ ] MCP server 框架
- [ ] equity_calculator、pot_odds_calculator、note_manager、opponent_database_query
- [ ] Skills: poker-fundamentals、meta-strategy
- [ ] 对照实验

**验收**：工具化 agent 显著优于裸 agent。

#### Phase 3: 进阶能力

- [ ] range_analyzer、gto_lookup、hand_history_search
- [ ] Subagent: hand_analyst、session_reviewer
- [ ] 6-max 多桌并发
- [ ] Skills: opponent-modeling、gto-reference

**验收**：4 人 6-max 稳定运行。

#### Phase 4: 评测体系 + Dashboard

- [ ] **第 13 章定义的完整评分体系**
- [ ] 完整 PlayerStats
- [ ] Streamlit dashboard
- [ ] Markdown 报告生成

**验收**：本地 dashboard 展示完整评分体系。

#### Phase 5: 公开发布

- [ ] PyPI 打包（包名 holdem-agent-bench）
- [ ] `hab init` 引导式配置
- [ ] `hab submit` 提交流程
- [ ] **`docs/methodology.md` 完整版**（见第 14 章）
- [ ] **GitHub Pages leaderboard**（见第 15 章）
- [ ] 官方 benchmark：8 个主流模型 × 5000 手
- [ ] README 第一份榜单截图
- [ ] 5 分钟 demo 视频

**验收**：用户能在 GitHub Pages 看到完整 leaderboard。

### 7.3 V2 后置功能

- AIVAT 实现
- HuggingFace Space 部署
- 接入 Slumbot / GTO Wizard API
- MTT（锦标赛）模式
- 9-max full ring
- 独立网站

---

## 8. 测试策略

### 8.1 单元测试
- 引擎逻辑：all-in、side pot、tie-breaker
- Shim 格式转换
- 工具计算正确性
- 评分系统：Bootstrap CI、Elo 更新、Duplicate poker

### 8.2 集成测试
- Shim + 真实 OpenRouter API
- MCP server 响应格式
- Agent workspace 隔离

### 8.3 E2E 测试
- 100 手 HU session
- 1000 手 6-max session
- 崩溃恢复

### 8.4 对抗测试
- 偷看 hole_cards
- 篡改 action JSON
- 超时

---

## 9. 运维规范

### 9.1 日志

```
sessions/<id>/logs/
├── orchestrator.log
├── shim.log
├── engine.log
├── mcp_server.log
└── agents/
    ├── player_a.log
    └── player_b.log
```

### 9.2 监控
- 进程健康检查
- Token 监控
- 决策时长统计

### 9.3 成本控制
- Session 级 token hard cap
- Prompt caching
- 开发阶段全用 Sonnet/Haiku

### 9.4 数据备份
- Session 结束自动 tar.gz
- 重要实验上传到 HuggingFace Datasets

---

## 10. 用户体验关键点

### 10.1 README 第一屏

```markdown
# HoldemAgentBench (HAB) 🃏

> A benchmark where AI agents face off at the poker table.
> Each agent runs in a Claude Code environment with full toolkit access.

[🏆 Live Leaderboard](https://<user>.github.io/holdem-agent-bench) | [📊 Methodology](docs/methodology.md)

## ⚡ Quick Start (5 minutes, $5)

​```bash
pip install holdem-agent-bench
export OPENROUTER_API_KEY=sk-or-...
hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5
​```

## 🏆 Current Top 5

| Rank | Model | Elo | Skill BB/100 |
|------|-------|-----|--------------|
| 1 | claude-opus-4-7 | 1745 | +22.3 |
| 2 | gpt-5 | 1687 | +18.1 |
| ... | ... | ... | ... |

[Full Leaderboard →](https://<user>.github.io/holdem-agent-bench)
```

### 10.2 三层用户

| 用户类型 | 占比 | 主要诉求 | 服务方式 |
|---------|------|---------|---------|
| 看榜的 | 80% | 看 leaderboard | GitHub Pages |
| 跑评测的 | 15% | 验证自家模型 | CLI + presets |
| 研究者 | 5% | 魔改实验 | 模块化代码 + docs |

### 10.3 出分透明度

详见**第 13、14 章**。三层：
1. 原始数据公开
2. 评分算法公开（methodology.md）
3. 官方 / 社区 leaderboard 分开标注

---

## 11. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 成本爆炸 | 高 | 高 | Hard cap + 实时显示 + Sonnet 测试 |
| Shim 格式 bug | 中 | 高 | 充分测试 + 错误日志 |
| Claude Code 进程清理失败 | 中 | 中 | 进程组隔离 + signal handler |
| OpenRouter 限流 | 中 | 中 | 自动 retry |
| 扑克引擎 bug | 低 | 高 | pokerkit 成熟 + 单元测试 |
| 方差太大 | 中 | 中 | Duplicate poker + 大样本 |
| 评分被质疑 | 中 | 高 | methodology.md 极度透明 |

---

## 12. 附录

### A. 参考资源
- pokerkit: https://github.com/uoftcprg/pokerkit
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- OpenRouter: https://openrouter.ai/docs
- AIVAT 论文: "AIVAT: A New Variance Reduction Technique"

### B. 术语表
- **HAB**: HoldemAgentBench 缩写
- **BB/100**: Big Blinds per 100 hands
- **VPIP / PFR**: 入池率 / 翻前加注率
- **GTO / HU / 6-max**: 博弈论最优 / 单挑 / 六人桌
- **AIVAT**: 方差缩减技术
- **Headless mode**: Claude Code 无人值守模式

### C. 完整使用示例

```bash
pip install holdem-agent-bench
hab init
hab models list
hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5
hab run daily-bench --models claude-opus-4-7,gpt-5,gemini-3-pro,deepseek-r1
hab dashboard
hab submit ./hab-sessions/<latest>
```

---

## 13. Leaderboard 与评分系统

本章是项目公信力的核心。评分系统的每个数字都必须可推导、可验证、可追溯。

### 13.1 评分系统总览

**三层指标体系**：

```
Layer 1: Raw BB/100         → 原始数据
Layer 2: Skill BB/100       → 扣除运气的技术分（Duplicate Poker）
Layer 3: Elo Rating         → 综合排名（用于排序）
```

**最终榜单按 Elo 排序，但显示所有三层数据**：

```
Rank | Model              | Elo  | Skill BB/100 (95% CI)  | Raw BB/100 | Hands
-----|--------------------|------|------------------------|------------|------
  1  | claude-opus-4-7    | 1745 | +22.3 (+15.1, +29.5)   | +25.1      | 15000
  2  | gpt-5              | 1687 | +18.1 (+11.4, +24.8)   | +20.3      | 12000
  3  | gemini-3-pro       | 1621 | +12.4 ( +5.2, +19.6)   | +14.7      | 10000
```

### 13.2 Layer 1: Raw BB/100

**公式**：

```
Raw BB/100 = (Total chips won / Big blind) × (100 / Total hands played)
```

**实现**：

```python
# src/hab/analytics/stats.py
import numpy as np

def calculate_bb_per_100(hand_results: list[float], big_blind: float) -> float:
    if not hand_results:
        return 0.0
    total_chips = sum(hand_results)
    in_bb = total_chips / big_blind
    return in_bb * 100 / len(hand_results)
```

#### 13.2.1 置信区间（Bootstrap）

```python
# src/hab/analytics/confidence.py
import numpy as np

def bootstrap_ci(
    hand_results: list[float],
    big_blind: float,
    n_bootstrap: int = 10000,
    confidence: float = 0.95,
) -> tuple[float, tuple[float, float]]:
    if len(hand_results) < 30:
        point = calculate_bb_per_100(hand_results, big_blind)
        return point, (float('-inf'), float('inf'))

    n = len(hand_results)
    bootstrap_means = np.empty(n_bootstrap)
    rng = np.random.default_rng(seed=42)

    for i in range(n_bootstrap):
        sample = rng.choice(hand_results, size=n, replace=True)
        bootstrap_means[i] = np.mean(sample) / big_blind * 100

    point_estimate = np.mean(hand_results) / big_blind * 100
    alpha = (1 - confidence) / 2
    ci_low = np.percentile(bootstrap_means, alpha * 100)
    ci_high = np.percentile(bootstrap_means, (1 - alpha) * 100)

    return point_estimate, (ci_low, ci_high)
```

**报告格式**：`+36.6 BB/100 (95% CI: +18.2 ~ +55.0)`

### 13.3 Layer 2: Skill BB/100（Duplicate Poker）

#### 13.3.1 核心思想

让所有玩家打**完全相同的发牌序列**，比较各自的处理。运气直接抵消。

#### 13.3.2 实现

```python
# src/hab/analytics/duplicate.py
from dataclasses import dataclass
from collections import defaultdict
import numpy as np

@dataclass
class DuplicateResult:
    player_id: str
    skill_bb_per_100: float
    ci_low: float
    ci_high: float
    n_templates: int

class DuplicatePokerAnalyzer:
    def __init__(self, big_blind: float = 2.0):
        self.big_blind = big_blind

    def analyze(self, templates: list[dict]) -> dict[str, DuplicateResult]:
        player_deltas = defaultdict(list)

        for template in templates:
            all_chips = []
            for rotation in template['rotations']:
                all_chips.extend(rotation['player_chips'].values())

            template_avg = np.mean(all_chips)

            player_avg_in_template = defaultdict(list)
            for rotation in template['rotations']:
                for pid, chips in rotation['player_chips'].items():
                    player_avg_in_template[pid].append(chips)

            for pid, chips_list in player_avg_in_template.items():
                avg = np.mean(chips_list)
                delta = avg - template_avg
                player_deltas[pid].append(delta)

        results = {}
        for pid, deltas in player_deltas.items():
            point, (ci_low, ci_high) = self._bootstrap_skill(deltas)
            results[pid] = DuplicateResult(
                player_id=pid,
                skill_bb_per_100=point,
                ci_low=ci_low,
                ci_high=ci_high,
                n_templates=len(deltas),
            )
        return results

    def _bootstrap_skill(self, deltas, n_bootstrap=10000):
        rng = np.random.default_rng(42)
        n = len(deltas)
        means = np.empty(n_bootstrap)
        for i in range(n_bootstrap):
            sample = rng.choice(deltas, size=n, replace=True)
            means[i] = np.mean(sample) / self.big_blind * 100

        point = np.mean(deltas) / self.big_blind * 100
        ci_low = np.percentile(means, 2.5)
        ci_high = np.percentile(means, 97.5)
        return point, (ci_low, ci_high)
```

### 13.4 Layer 3: Elo Rating

#### 13.4.1 设置

- 初始分：1500
- K 因子：32

#### 13.4.2 Update 规则

每 session 结算一次。两两比较，按 BB/100 with CI overlap rule。

#### 13.4.3 实现

```python
# src/hab/analytics/elo.py
from dataclasses import dataclass

@dataclass
class EloRating:
    player_id: str
    rating: float
    games_played: int

class EloSystem:
    def __init__(self, initial_rating=1500.0, k_factor=32.0):
        self.initial_rating = initial_rating
        self.k = k_factor
        self.ratings: dict[str, float] = {}
        self.games: dict[str, int] = {}

    def get(self, player_id: str) -> float:
        return self.ratings.get(player_id, self.initial_rating)

    def update_after_session(self, session_results: dict[str, dict]):
        players = list(session_results.keys())
        new_ratings = {p: self.get(p) for p in players}

        for i, a in enumerate(players):
            for b in players[i+1:]:
                score_a = self._compare(session_results[a], session_results[b])

                ra = self.get(a)
                rb = self.get(b)
                expected_a = 1 / (1 + 10 ** ((rb - ra) / 400))

                delta = self.k * (score_a - expected_a)
                new_ratings[a] += delta
                new_ratings[b] -= delta

        for p, r in new_ratings.items():
            self.ratings[p] = r
            self.games[p] = self.games.get(p, 0) + 1

    def _compare(self, result_a, result_b) -> float:
        a_low, a_high = result_a['ci']
        b_low, b_high = result_b['ci']

        if a_low <= b_high and b_low <= a_high:
            return 0.5

        if result_a['bb_per_100'] > result_b['bb_per_100']:
            return 1.0
        return 0.0

    def leaderboard(self) -> list[EloRating]:
        return sorted(
            [EloRating(p, r, self.games.get(p, 0)) for p, r in self.ratings.items()],
            key=lambda x: -x.rating,
        )
```

### 13.5 Eligibility（资格规则）

| 规则 | 阈值 |
|-----|------|
| 最少手数 | 5,000 hands |
| 最少 sessions | 3 个独立 session |
| 必须使用 official preset | `daily-bench` 或 `full-benchmark` |
| 数据完整性 | 所有 hand_history 和 tool_calls 必须公开 |
| 全数据规则 | 同一模型一个月内所有 official runs 必须全部计入 |

### 13.6 模型版本与归类

#### 13.6.1 ID 规范

完全采用 OpenRouter 命名：`provider/model-name-version`

#### 13.6.2 同模型不同 mode

```
anthropic/claude-opus-4-7              (Default: full agent)
anthropic/claude-opus-4-7@bare         (No tools)
anthropic/claude-opus-4-7@no-notes     (No persistent memory)
```

### 13.7 Methodology 版本管理

```
v1.0 (2026-04): 初始版本
v1.1: 修复 Elo K 值
v1.2: 加入 AIVAT 替代 Duplicate
v2.0: 重大变更，老分数归档
```

### 13.8 Leaderboard 数据格式

#### 13.8.1 `docs/data/leaderboard.json`

```json
{
  "methodology_version": "v1.0",
  "last_updated": "2026-04-25T14:30:00Z",
  "preset": "daily-bench",
  "format": "6-max",
  "entries": [
    {
      "rank": 1,
      "model": "anthropic/claude-opus-4-7",
      "display_name": "Claude Opus 4.7",
      "elo": 1745,
      "skill_bb_per_100": {
        "point": 22.3,
        "ci_low": 15.1,
        "ci_high": 29.5
      },
      "raw_bb_per_100": 25.1,
      "hands_played": 15000,
      "sessions_played": 5,
      "cost_per_hand_usd": 0.28,
      "tier": "official",
      "last_run": "2026-04-20",
      "tactical_metrics": {
        "vpip": 0.231,
        "pfr": 0.184,
        "three_bet": 0.082
      },
      "agent_metrics": {
        "tool_calls_per_hand": 3.8,
        "notes_written": 287,
        "subagent_calls": 42
      }
    }
  ]
}
```

### 13.9 报告生成器

```python
# src/hab/analytics/leaderboard.py
import json
from pathlib import Path
from datetime import datetime

class LeaderboardGenerator:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def generate_from_runs(self, runs: list[dict]) -> dict:
        elo_system = EloSystem()
        all_stats = {}

        for run in sorted(runs, key=lambda r: r['ended_at']):
            session_results = self._extract_session_results(run)
            elo_system.update_after_session(session_results)
            self._update_aggregate_stats(all_stats, run)

        eligible = self._filter_eligible(all_stats)

        leaderboard = []
        for rank, entry in enumerate(elo_system.leaderboard(), 1):
            if entry.player_id not in eligible:
                continue
            stats = all_stats[entry.player_id]
            leaderboard.append({
                "rank": rank,
                "model": entry.player_id,
                "elo": round(entry.rating, 0),
                "skill_bb_per_100": stats['skill'],
                "raw_bb_per_100": stats['raw'],
                "hands_played": stats['hands'],
            })

        return {
            "methodology_version": "v1.0",
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "preset": "daily-bench",
            "format": "6-max",
            "entries": leaderboard,
        }

    def write(self, leaderboard_data: dict):
        path = self.output_dir / "leaderboard.json"
        with open(path, 'w') as f:
            json.dump(leaderboard_data, f, indent=2)
```

---

## 14. Methodology 文档

这是 `docs/methodology.md` 的完整内容模板。

```markdown
# HoldemAgentBench: Scoring Methodology

**Version**: 1.0
**Last Updated**: 2026-04-25

## 1. Overview

HoldemAgentBench (HAB) uses a three-layer scoring system:

1. **Raw BB/100**: Actual win rate, transparent and verifiable.
2. **Skill BB/100**: Variance-reduced via Duplicate Poker. Reflects pure technical skill.
3. **Elo Rating**: Comprehensive ranking that accounts for opponent strength.

The official leaderboard is sorted by Elo, but displays all three metrics.

## 2. Game Format

- **Variant**: No-Limit Texas Hold'em
- **Default Preset**: `daily-bench` (6-max cash, $1/$2 blinds, 200 big blinds starting stack)
- **Decision timeout**: 5 minutes per action
- **Max tool calls per decision**: 10

## 3. Layer 1: Raw BB/100

Big Blinds won per 100 hands:

```
Raw BB/100 = (Total chips won / Big blind) × (100 / Total hands played)
```

We report 95% bootstrap CI with 10,000 resamples.

Raw BB/100 has high variance. Always interpret with CI.

## 4. Layer 2: Skill BB/100 (Duplicate Poker)

For each "template" (a fixed card sequence):
1. Deal cards using a fixed seed.
2. Run 4 rotations, with each player occupying each position once.
3. Each player's "skill delta" = avg chips won across all rotations - template average.

Skill BB/100 = mean(skill_deltas) / big_blind × 100

CI computed via bootstrap on the delta distribution.

## 5. Layer 3: Elo Rating

- Initial rating: 1500
- K factor: 32

After each session:
1. Compare every pair of players.
2. Determine win/loss/draw based on BB/100 with CI overlap rule.
3. Update Elo using standard formula.

## 6. Eligibility

To appear on the official leaderboard:

| Requirement | Threshold |
|-------------|-----------|
| Minimum hands | 5,000 |
| Minimum sessions | 3 |
| Required preset | `daily-bench` or `full-benchmark` |
| Data completeness | All hand histories and tool calls public |
| All-runs rule | All official runs in a calendar month must be included |

## 7. Tier System

- 🏅 **Official**: Run by the maintainers under standard conditions.
- ✅ **Verified**: Community submission, reproduced by maintainers.
- ⚠️ **Unverified**: Community submission, not yet reproduced.
- 🚩 **Challenged**: Under reproducibility challenge.
- ❌ **Invalidated**: Confirmed to violate methodology.

## 8. Submission Process

1. Run `hab submit <session>`.
2. Data uploaded to public repo with checksums.
3. Maintainers review tool call logs.
4. After approval, entry added with appropriate tier.

## 9. Reproducibility Challenges

Any user can challenge a leaderboard entry:
1. Run same configuration with same seed.
2. File GitHub Issue with discrepancy.
3. If verified, original entry is downgraded.

## 10. Versioning

- Patch (1.0 → 1.1): Bug fixes, no rescore.
- Minor (1.x → 1.y): New metrics, optional rescore.
- Major (1.x → 2.0): Fundamental changes. Previous scores archived.

## 11. Known Limitations

- Only NLHE 6-max is officially scored.
- Cross-session learning not directly evaluated.
- Tool usage quality reported but not factored into main score.

## 12. Open Data

All data publicly available at:
- `official_runs/` directory in GitHub repo
- HuggingFace Datasets mirror (post-v1.0)
```

---

## 15. GitHub Pages 部署

### 15.1 部署策略：渐进式

```
v0.1 启动期         → GitHub README 表格 + 自动更新脚本
v0.5 数据丰富期     → GitHub Pages 静态站点
v1.0 社区扩散期     → 增加 HuggingFace Space
v2.0 商业化（可选） → 独立网站
```

### 15.2 GitHub Pages 实现

#### 15.2.1 启用 Pages

1. GitHub repo Settings → Pages
2. Source: Deploy from a branch
3. Branch: `main` / `docs` folder
4. URL: `https://<user>.github.io/holdem-agent-bench`

#### 15.2.2 前端文件

**`docs/index.html`**：

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>HoldemAgentBench Leaderboard</title>
  <link rel="stylesheet" href="assets/style.css">
  <script src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js" defer></script>
</head>
<body>
  <header>
    <h1>🃏 HoldemAgentBench</h1>
    <p>A benchmark where AI agents face off at the poker table.</p>
    <nav>
      <a href="index.html">Leaderboard</a>
      <a href="methodology.html">Methodology</a>
      <a href="https://github.com/<user>/holdem-agent-bench">GitHub</a>
    </nav>
  </header>

  <main x-data="leaderboard()" x-init="load()">
    <section class="meta">
      <p>Methodology version: <strong x-text="data.methodology_version"></strong></p>
      <p>Last updated: <strong x-text="data.last_updated"></strong></p>
      <p>Preset: <strong x-text="data.preset"></strong></p>
    </section>

    <section class="filters">
      <input type="text" placeholder="Search models..." x-model="search">
      <select x-model="tierFilter">
        <option value="">All tiers</option>
        <option value="official">🏅 Official only</option>
        <option value="verified">✅ Verified+</option>
      </select>
    </section>

    <table class="leaderboard">
      <thead>
        <tr>
          <th>Rank</th>
          <th>Model</th>
          <th>Tier</th>
          <th>Elo</th>
          <th>Skill BB/100</th>
          <th>Raw BB/100</th>
          <th>Hands</th>
          <th>Cost/Hand</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="entry in filteredEntries" :key="entry.model">
          <tr>
            <td x-text="entry.rank"></td>
            <td><a :href="'replay.html?model=' + entry.model">
              <span x-text="entry.display_name"></span>
            </a></td>
            <td x-text="tierIcon(entry.tier)"></td>
            <td><strong x-text="entry.elo"></strong></td>
            <td>
              <span x-text="formatBb(entry.skill_bb_per_100.point)"></span>
              <small>(<span x-text="formatBb(entry.skill_bb_per_100.ci_low)"></span>,
              <span x-text="formatBb(entry.skill_bb_per_100.ci_high)"></span>)</small>
            </td>
            <td x-text="formatBb(entry.raw_bb_per_100)"></td>
            <td x-text="entry.hands_played.toLocaleString()"></td>
            <td>$<span x-text="entry.cost_per_hand_usd.toFixed(2)"></span></td>
          </tr>
        </template>
      </tbody>
    </table>
  </main>

  <script src="assets/leaderboard.js"></script>
</body>
</html>
```

**`docs/assets/leaderboard.js`**：

```javascript
function leaderboard() {
  return {
    data: { entries: [] },
    search: '',
    tierFilter: '',

    async load() {
      const res = await fetch('data/leaderboard.json');
      this.data = await res.json();
    },

    get filteredEntries() {
      return this.data.entries.filter(e => {
        if (this.search && !e.display_name.toLowerCase().includes(this.search.toLowerCase())) return false;
        if (this.tierFilter === 'official' && e.tier !== 'official') return false;
        if (this.tierFilter === 'verified' && !['official', 'verified'].includes(e.tier)) return false;
        return true;
      });
    },

    tierIcon(tier) {
      return {
        official: '🏅',
        verified: '✅',
        unverified: '⚠️',
        challenged: '🚩',
        invalidated: '❌',
      }[tier] || '?';
    },

    formatBb(value) {
      const sign = value >= 0 ? '+' : '';
      return sign + value.toFixed(1);
    },
  };
}
```

**`docs/assets/style.css`**：

```css
body { font-family: -apple-system, system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 2rem; }
header h1 { margin: 0; }
nav a { margin-right: 1rem; }
.leaderboard { width: 100%; border-collapse: collapse; margin-top: 2rem; }
.leaderboard th, .leaderboard td { padding: 0.75rem; text-align: left; border-bottom: 1px solid #eee; }
.leaderboard th { background: #f7f7f7; }
.leaderboard tr:hover { background: #fafafa; }
small { color: #666; }
.filters { margin: 1rem 0; }
.filters input, .filters select { padding: 0.5rem; margin-right: 0.5rem; }
```

### 15.3 自动更新脚本

```python
# scripts/update_leaderboard.py
import json
from pathlib import Path
from hab.analytics.leaderboard import LeaderboardGenerator

def main():
    runs_dir = Path("official_runs")
    runs = []
    for run_file in sorted(runs_dir.glob("*/run.json")):
        with open(run_file) as f:
            runs.append(json.load(f))

    output_dir = Path("docs/data")
    generator = LeaderboardGenerator(output_dir)
    leaderboard_data = generator.generate_from_runs(runs)

    with open(output_dir / "leaderboard.json", 'w') as f:
        json.dump(leaderboard_data, f, indent=2)

    update_readme_top5(leaderboard_data)
    print(f"✅ Updated leaderboard with {len(leaderboard_data['entries'])} entries")

def update_readme_top5(data):
    readme_path = Path("README.md")
    content = readme_path.read_text()

    top5_md = ["| Rank | Model | Elo | Skill BB/100 |", "|------|-------|-----|--------------|"]
    for entry in data['entries'][:5]:
        skill = entry['skill_bb_per_100']['point']
        sign = '+' if skill >= 0 else ''
        top5_md.append(
            f"| {entry['rank']} | {entry['display_name']} | "
            f"{entry['elo']} | {sign}{skill:.1f} |"
        )

    new_table = "\n".join(top5_md)

    start = "<!-- LEADERBOARD_START -->"
    end = "<!-- LEADERBOARD_END -->"
    pre, _, rest = content.partition(start)
    _, _, post = rest.partition(end)
    new_content = f"{pre}{start}\n{new_table}\n{end}{post}"

    readme_path.write_text(new_content)

if __name__ == "__main__":
    main()
```

### 15.4 GitHub Actions 自动部署

```yaml
# .github/workflows/update-leaderboard.yml
name: Update Leaderboard

on:
  workflow_dispatch:
  schedule:
    - cron: '0 0 * * 0'
  push:
    paths:
      - 'official_runs/**'

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -e .

      - name: Generate leaderboard
        run: python scripts/update_leaderboard.py

      - name: Commit changes
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/data/leaderboard.json README.md
          git diff --staged --quiet || git commit -m "chore: update leaderboard"
          git push
```

### 15.5 Submit 命令实现

```python
# src/hab/cli/submit.py
import json
import hashlib
from pathlib import Path
import subprocess
from datetime import datetime

def submit_session(session_dir: Path):
    checksums = {}
    for f in session_dir.rglob("*"):
        if f.is_file():
            checksums[str(f.relative_to(session_dir))] = sha256(f)

    submission_dir = session_dir / "submission"
    submission_dir.mkdir(exist_ok=True)

    copy_essential_files(session_dir, submission_dir)

    with open(submission_dir / "metadata.json", 'w') as f:
        json.dump({
            "submitted_at": datetime.utcnow().isoformat(),
            "checksums": checksums,
            "version": get_hab_version(),
            "platform": get_platform_info(),
        }, f, indent=2)

    archive_path = session_dir / f"{session_dir.name}.tar.gz"
    subprocess.run(["tar", "-czf", str(archive_path), "-C", str(submission_dir), "."])

    print(f"📦 Submission package created: {archive_path}")
    print(f"📋 Next steps:")
    print(f"  1. Upload {archive_path} to: https://github.com/<repo>/releases")
    print(f"  2. Open a PR adding the run to official_runs/")
    print(f"  3. Maintainers will review within 7 days")

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            h.update(chunk)
    return h.hexdigest()
```

### 15.6 后续：HuggingFace Space

V1.0 之后部署到 HF Space。

部署步骤：
1. huggingface.co 创建新 Space，类型选 Streamlit
2. 把 `src/hab/dashboard/app.py` 改造成读取 HF Dataset 数据
3. GitHub Action 同步 `docs/data/` 到 HF Dataset
4. 在 HF Space 下选择 SDK = Streamlit，自动部署

---

## 文档结束

**最后提示给 Claude Code 实施者**：

1. **一气呵成**：连续工作完成所有 Phase
2. **按小时推进**：估算总工时 4-6 小时
3. **成本敏感**：开发阶段全用 `anthropic/claude-sonnet-4-7` 或 `openai/gpt-5-mini`
4. **MVP 单终端**：单命令跑完 100 手是 Phase 1 硬性要求
5. **包名严格用 holdem-agent-bench**，import 用 `hab`，CLI 用 `hab`
6. **OpenRouter shim 是关键路径**
7. **进程清理不可妥协**：Ctrl+C 后绝不能留孤儿进程
8. **评分系统必须有完整测试覆盖**：Bootstrap CI、Elo 更新、Duplicate Poker
9. **第 14 章的 methodology.md 必须如实写入** `docs/methodology.md`
10. **第 15 章的 GitHub Pages 必须在 Phase 5 实施**
11. **遇到设计分歧标记 TODO**，不擅自决策重大方向
12. **完成所有 Phase 后**，生成完整进度报告

祝开发顺利。🃏

---

**HoldemAgentBench (HAB)** — When agents take a seat at the table, who walks away with the chips?
