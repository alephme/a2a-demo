"""Orchestrator Agent — 通用 A2A 调度 Agent.

通过 Agent Card 发现远程 A2A Agent 的能力，使用 LLM 理解用户请求，
通过 A2A 协议私下调用远程 Agent 完成任务，并以自然语言回复用户。

用法：
    uv run python app/cli_agent.py

环境变量：
    A2A_SERVER_URLS   远程 A2A Server 地址列表，逗号分隔（默认 http://localhost:10000）
                      A2A_SERVER_URL（单值）作为向后兼容的别名
    model_source      模型来源：google | 其他（deepseek/openai 等）
    GOOGLE_API_KEY    Google Gemini API Key
    API_KEY           OpenAI 兼容 API Key（可选，本地 LLM 可不填）
    TOOL_LLM_URL      OpenAI 兼容 API 地址
    TOOL_LLM_NAME     模型名称
"""

import asyncio
import logging
import os
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import AgentCard, MessageSendParams, SendMessageRequest

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM 工厂
# ---------------------------------------------------------------------------

def _create_llm():
    """使用与 server (app/__main__.py) 相同的环境变量逻辑创建 LLM."""
    model_source = os.getenv('model_source', 'google')
    if model_source == 'google':
        return ChatGoogleGenerativeAI(model='gemini-2.0-flash')
    else:
        return ChatOpenAI(
            model=os.getenv('TOOL_LLM_NAME'),
            openai_api_key=os.getenv('API_KEY', 'EMPTY'),
            openai_api_base=os.getenv('TOOL_LLM_URL'),
            temperature=0,
        )


# ---------------------------------------------------------------------------
# 从 Agent Card 构建 System Prompt
# ---------------------------------------------------------------------------

def _build_system_prompt(cards: list[AgentCard]) -> str:
    """将远程 Agent 的 Agent Card 原始 JSON 注入 system prompt。

    直接传入完整 JSON 而非模板化提取，使得未来新增字段或接入多 Server 时
    无需修改代码，LLM 会自动从 JSON 中读取所需信息。
    """
    cards_json = []
    for c in cards:
        cards_json.append(c.model_dump_json(indent=2, exclude_none=True))
    cards_raw = "\n\n---\n\n".join(cards_json)

    return (
        f"You are a smart orchestrator agent. "
        f"You have access to the following remote A2A agent(s) via the `call_a2a_agent` tool.\n\n"
        f"## Available Remote Agents (Agent Cards)\n"
        f"{cards_raw}\n\n"
        f"## Your workflow\n"
        f"1. Read the Agent Card(s) above to understand each remote agent's capabilities, "
        f"skills, input/output modes, and any other details.\n"
        f"2. When the user makes a request, decide which remote agent (if any) should handle it.\n"
        f"3. If a suitable agent exists, call the `call_a2a_agent` tool.\n"
        f"4. If the tool returns [INFO_NEEDED]..., the remote agent needs more details. "
        f"Ask the user for the required info, then call the tool again.\n"
        f"5. If no remote agent can handle the request, politely decline.\n"
        f"6. Present the final result to the user in a clear and helpful way."
        f"7. 用户不主动询问的时候没有必要告知你支持的remote agents."
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """去掉尾部斜杠，确保 URL 匹配一致。"""
    return url.rstrip('/')


def _parse_server_urls() -> list[str]:
    """解析 A2A_SERVER_URLS（逗号分隔）或向后兼容 A2A_SERVER_URL。"""
    urls_str = os.getenv('A2A_SERVER_URLS') or os.getenv('A2A_SERVER_URL', 'http://localhost:10000')
    return [_normalize_url(u.strip()) for u in urls_str.split(',') if u.strip()]


async def main():
    server_urls = _parse_server_urls()
    logger.info("Orchestrator Agent starting — connecting to %d A2A server(s): %s",
                len(server_urls), server_urls)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as httpx_client:
        # ---- 1. 获取所有 Server 的 Agent Card ----
        cards: list[AgentCard] = []
        clients: dict[str, A2AClient] = {}

        for url in server_urls:
            try:
                resolver = A2ACardResolver(httpx_client=httpx_client, base_url=url)
                card = await resolver.get_agent_card()
                cards.append(card)
                # 使用标准化后的 URL 作为 key
                clients[url] = A2AClient(httpx_client=httpx_client, agent_card=card)
                logger.info("Connected to '%s' (%s) — %s", card.name, url, card.description)
            except Exception as e:
                logger.error("Failed to fetch Agent Card from %s: %s", url, e)
                print(f"  ⚠️  Cannot connect to {url} — {e}")

        if not cards:
            print("\n❌ No A2A servers available. Exiting.")
            return

        # 多轮对话上下文（按 server_url 隔离，key: url -> state）
        _mt_state: dict[str, dict[str, str | None]] = {}

        # ---- 2. 定义 Tool：远程 A2A Agent 调用 ----
        @tool
        async def call_a2a_agent(server_url: str, query: str) -> str:
            """Send a text query to a remote A2A agent and return its response.
            Use this when the user's request matches one of the remote agents' skills.

            Args:
                server_url: The base URL of the target A2A server (from the Agent Card's `url` field).
                query: The text query to send to the agent.
            """
            nonlocal _mt_state

            # 标准化 URL（Agent Card 可能带尾部斜杠，clients dict key 不带）
            normalized = _normalize_url(server_url)
            client = clients.get(normalized)
            if client is None:
                return f"[ERROR] Unknown server URL: {server_url}"

            # 初始化/恢复多轮上下文
            state = _mt_state.setdefault(normalized, {"task_id": None, "context_id": None})

            payload: dict[str, Any] = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": query}],
                    "message_id": uuid4().hex,
                },
            }
            if state["task_id"] and state["context_id"]:
                payload["message"]["task_id"] = state["task_id"]
                payload["message"]["context_id"] = state["context_id"]

            request = SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(**payload),
            )
            response = await client.send_message(request)
            result = response.root.result

            state["task_id"] = result.id
            state["context_id"] = result.context_id

            task_state = result.status.state

            if task_state == "completed":
                texts: list[str] = []
                if result.artifacts:
                    for artifact in result.artifacts:
                        if artifact.parts:
                            for part in artifact.parts:
                                if part.root.kind == "text":
                                    texts.append(part.root.text)
                # 重置多轮状态
                _mt_state[normalized] = {"task_id": None, "context_id": None}
                return "\n".join(texts) if texts else "(empty response)"

            if task_state == "input-required":
                question = ""
                if result.status.message:
                    for part in result.status.message.parts:
                        if part.root.kind == "text":
                            question += part.root.text + " "
                return f"[INFO_NEEDED] {question.strip()}"

            if task_state == "failed":
                _mt_state[normalized] = {"task_id": None, "context_id": None}
                error_msg = ""
                if result.status.message:
                    for part in result.status.message.parts:
                        if part.root.kind == "text":
                            error_msg += part.root.text + " "
                return f"[ERROR] {error_msg.strip() or 'Task failed with no details.'}"

            return f"[UNEXPECTED] Task state: {task_state}"

        # ---- 3. 初始化 LLM + LangGraph Agent ----
        llm = _create_llm()
        system_prompt = _build_system_prompt(cards)
        memory = MemorySaver()
        agent = create_react_agent(
            llm,
            tools=[call_a2a_agent],
            checkpointer=memory,
            prompt=system_prompt,
        )

        # ---- 4. 交互式 CLI ----
        print(f"\n=== Orchestrator Agent ===")
        print(f"Connected to {len(cards)} A2A server(s):")
        for card in cards:
            skills_count = len(card.skills) if card.skills else 0
            print(f"  • {card.name} — {card.description} ({skills_count} skill(s))")
        print(f"Model source: {os.getenv('model_source', 'google')}")
        print("Type 'exit' or 'quit' to quit.\n")

        config = {"configurable": {"thread_id": "orchestrator-session"}}

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if user_input.lower() in ("exit", "quit"):
                print("Goodbye!")
                break
            if not user_input:
                continue

            inputs = {"messages": [("user", user_input)]}

            # 收集所有 AI 输出块，仅打印最终的回复
            final_responses: list[str] = []
            async for chunk in agent.astream(inputs, config, stream_mode="values"):
                messages = chunk.get("messages", [])
                if not messages:
                    continue
                last = messages[-1]
                if (
                    isinstance(last, AIMessage)
                    and last.content
                    and not last.tool_calls
                ):
                    final_responses.append(str(last.content))

            # 打印去重后的最终回复
            seen = set()
            for resp in final_responses:
                if resp not in seen:
                    print(f"🤖 {resp}")
                    seen.add(resp)
            print()  # 空行分隔轮次


if __name__ == "__main__":
    asyncio.run(main())
