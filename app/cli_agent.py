"""Orchestrator Agent — 通用 A2A 调度 Agent.

通过 Agent Card 发现远程 A2A Agent 的能力，使用 LLM 理解用户请求，
通过 A2A 协议私下调用远程 Agent 完成任务，并以自然语言回复用户。

用法：
    uv run python app/cli_agent.py

环境变量：
    A2A_SERVER_URL    远程 A2A Server 地址（默认 http://localhost:10000）
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

load_dotenv()

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

async def main():
    base_url = os.getenv('A2A_SERVER_URL', 'http://localhost:10000')
    logger.info("Orchestrator Agent starting — connecting to A2A server at %s", base_url)

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as httpx_client:
        # ---- 1. 获取 Agent Card ----
        try:
            resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base_url)
            agent_card = await resolver.get_agent_card()
        except Exception as e:
            logger.error("Failed to fetch Agent Card from %s: %s", base_url, e)
            print(f"\n❌ Cannot connect to A2A server at {base_url}")
            print(f"   Make sure the server is running and A2A_SERVER_URL is correct.")
            return

        logger.info("Connected to '%s' — %s", agent_card.name, agent_card.description)

        # ---- 2. 创建 A2A Client ----
        a2a_client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)

        # 多轮对话上下文（闭包变量，跨 tool 调用保持状态）
        _mt_task_id: str | None = None
        _mt_context_id: str | None = None

        # ---- 3. 定义 Tool：远程 A2A Agent 调用 ----
        @tool
        async def call_a2a_agent(query: str) -> str:
            """Send a text query to the remote A2A agent and return its response. Use this when the user's request aligns with the remote agent's skills."""
            nonlocal _mt_task_id, _mt_context_id

            payload: dict[str, Any] = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": query}],
                    "message_id": uuid4().hex,
                },
            }
            # 如果是继续之前的对话，带上 task_id / context_id
            if _mt_task_id and _mt_context_id:
                payload["message"]["task_id"] = _mt_task_id
                payload["message"]["context_id"] = _mt_context_id

            request = SendMessageRequest(
                id=str(uuid4()),
                params=MessageSendParams(**payload),
            )
            response = await a2a_client.send_message(request)
            result = response.root.result

            # 保存上下文（后续可能还需要继续对话）
            _mt_task_id = result.id
            _mt_context_id = result.context_id

            state = result.status.state

            if state == "completed":
                texts: list[str] = []
                if result.artifacts:
                    for artifact in result.artifacts:
                        if artifact.parts:
                            for part in artifact.parts:
                                if part.root.kind == "text":
                                    texts.append(part.root.text)
                _mt_task_id = None
                _mt_context_id = None
                return "\n".join(texts) if texts else "(empty response)"

            if state == "input-required":
                # 远程 Agent 需要更多信息，提取它的问题
                question = ""
                if result.status.message:
                    for part in result.status.message.parts:
                        if part.root.kind == "text":
                            question += part.root.text + " "
                return f"[INFO_NEEDED] {question.strip()}"

            if state == "failed":
                _mt_task_id = None
                _mt_context_id = None
                error_msg = ""
                if result.status.message:
                    for part in result.status.message.parts:
                        if part.root.kind == "text":
                            error_msg += part.root.text + " "
                return f"[ERROR] {error_msg.strip() or 'Task failed with no details.'}"

            return f"[UNEXPECTED] Task state: {state}"

        # ---- 4. 初始化 LLM + LangGraph Agent ----
        llm = _create_llm()
        system_prompt = _build_system_prompt([agent_card])
        memory = MemorySaver()
        agent = create_react_agent(
            llm,
            tools=[call_a2a_agent],
            checkpointer=memory,
            prompt=system_prompt,
        )

        # ---- 5. 交互式 CLI ----
        skills_count = len(agent_card.skills) if agent_card.skills else 0
        print(f"\n=== Orchestrator Agent ===")
        print(f"Connected to: {agent_card.name}")
        print(f"Description:  {agent_card.description}")
        print(f"Skills:       {skills_count} skill(s) available")
        print(f"Server:       {base_url}")
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
