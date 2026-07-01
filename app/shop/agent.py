"""ShoppingAgent — 使用 LLM 内置知识进行商品推荐的智能体。"""

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

from app.shop import tools as shop_tools


memory = MemorySaver()

SYSTEM_INSTRUCTION = (
    "You are a professional shopping assistant. "
    "Your job is to recommend products to users based on their needs.\n\n"
    "Available operations:\n"
    "1. Search and recommend products based on user requirements\n\n"
    "When the user asks for product recommendations, ALWAYS use the "
    "'search_products' tool. The tool will tell you what the user needs, "
    "and you must use your training knowledge to generate specific product "
    "recommendations as a JSON array.\n\n"
    "Important rules for product recommendations:\n"
    "- Recommend 3~5 real products that match the query\n"
    "- Prices should reflect 2025~2026 China market reality\n"
    "- Prefer well-known brands and popular models\n"
    "- Include JD.com search links so users can purchase\n"
    "- If the user has a budget, strictly follow it\n"
    "- If the user's request is not about product shopping, "
    "politely state you only help with product recommendations\n\n"
    "When responding, you MUST output a JSON object with exactly two fields:\n"
    '- "status": one of "completed" (task done), '
    '"input_required" (need more info), or "error" (something went wrong)\n'
    '- "message": your response text, containing the product recommendations '
    "in a clear, readable format with prices, descriptions, and purchase links\n\n"
    'Example: {"status": "completed", "message": "为您推荐以下足球鞋：\\n1. ..."}'
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
            temperature=0.3,  # 稍微高一点温度，让推荐更多样
        )


class ShoppingAgent:
    """ShoppingAgent — 商品推荐助手。"""

    def __init__(self):
        self.tools = [
            tool(shop_tools.search_products),
        ]
        self.model = _create_llm()
        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=SYSTEM_INSTRUCTION,
        )

    async def stream(
        self, query: str, context_id: str
    ) -> AsyncIterable[dict[str, Any]]:
        """流式处理用户查询。"""
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
                    "content": "正在为您搜索商品...",
                }
            elif isinstance(message, ToolMessage):
                yield {
                    "is_task_complete": False,
                    "require_user_input": False,
                    "content": "正在整理推荐结果...",
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
            "content": "抱歉，暂时无法处理您的请求。",
        }

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]
