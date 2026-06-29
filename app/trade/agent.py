"""TradeAgent — 使用 LangGraph 构建的模拟交易智能体。"""

import json
import os
from collections.abc import AsyncIterable
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.trade import tools as trade_tools


memory = MemorySaver()

SYSTEM_INSTRUCTION = (
    "You are a simulated trading assistant. You help users with mock stock trading.\n\n"
    "Available operations:\n"
    "1. Check market prices for stocks\n"
    "2. View portfolio and account balance\n"
    "3. Place buy/sell orders\n"
    "4. View order history\n"
    "5. Deposit simulated funds\n\n"
    "All data is simulated — no real money is involved.\n\n"
    "When responding, you MUST output a JSON object with exactly two fields:\n"
    '- "status": one of "completed" (task done), '
    '"input_required" (need more info), or "error" (something went wrong)\n'
    '- "message": your response text\n\n'
    'Example: {"status": "completed", "message": "AAPL current price: $182.30."}'
)


def _create_llm():
    """与 app/__main__.py 相同的环境变量逻辑。"""
    model_source = os.getenv("model_source", "google")
    if model_source == "google":
        return ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    else:
        return ChatOpenAI(
            model=os.getenv("TOOL_LLM_NAME"),
            openai_api_key=os.getenv("API_KEY", "EMPTY"),
            openai_api_base=os.getenv("TOOL_LLM_URL"),
            temperature=0,
        )


class TradeAgent:
    """TradeAgent — 模拟交易助手。"""

    def __init__(self):
        # 将 tools.py 中的函数注册为 LangChain tool
        self.tools = [
            tool(trade_tools.get_market_price),
            tool(trade_tools.view_portfolio),
            tool(trade_tools.place_order),
            tool(trade_tools.view_order_history),
            tool(trade_tools.deposit),
            tool(trade_tools.get_supported_symbols),
        ]
        self.model = _create_llm()
        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=SYSTEM_INSTRUCTION,
        )

    async def stream(self, query: str, context_id: str) -> AsyncIterable[dict[str, Any]]:
        """流式处理用户查询，产出状态更新与最终结果。"""
        inputs = {"messages": [("user", query)]}
        config = {"configurable": {"thread_id": context_id}}

        for item in self.graph.stream(inputs, config, stream_mode="values"):
            message = item["messages"][-1]
            if (
                isinstance(message, AIMessage)
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Processing your trading request...",
                }
            elif isinstance(message, ToolMessage):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "Executing order...",
                }

        yield self._get_final_response(config)

    def _get_final_response(self, config) -> dict[str, Any]:
        """从 graph state 中提取最终回复。"""
        current_state = self.graph.get_state(config)
        messages = current_state.values.get("messages", [])
        if messages:
            content = messages[-1].content
            if content:
                try:
                    parsed = json.loads(content)
                    status = parsed.get("status", "error")
                    message = parsed.get("message", "")
                    if status == "input_required":
                        return {
                            "is_task_complete": False,
                            "require_user_input": True,
                            "content": message,
                        }
                    if status == "error":
                        return {
                            "is_task_complete": False,
                            "require_user_input": True,
                            "content": message,
                        }
                    if status == "completed":
                        return {
                            "is_task_complete": True,
                            "require_user_input": False,
                            "content": message,
                        }
                except json.JSONDecodeError:
                    pass

        return {
            "is_task_complete": True,
            "require_user_input": False,
            "content": "Unable to process your request at the moment.",
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]
