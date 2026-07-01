"""Shopping Agent Server — LLM 商品推荐 A2A Agent。

通过 A2A 协议对外提供商品搜索与推荐服务。Agent 使用 LLM 的训练数据
直接生成商品推荐列表（含名称、价格、描述、购买链接）。

用法：
    uv run python app/shop [--host HOST] [--port PORT]

环境变量（与其他 Server 完全一致）：
    model_source      模型来源：google | 其他
    GOOGLE_API_KEY    Google Gemini API Key
    API_KEY           OpenAI 兼容 API Key
    TOOL_LLM_URL      OpenAI 兼容 API 地址
    TOOL_LLM_NAME     模型名称
"""

import logging
import os
import sys

import click
import httpx
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from dotenv import load_dotenv

from app.shop.agent import ShoppingAgent
from app.shop.agent_executor import ShoppingAgentExecutor


load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10002)
def main(host: str, port: int) -> None:
    """Starts the Shopping Agent server."""
    try:
        if os.getenv("model_source", "google") == "google":
            if not os.getenv("GOOGLE_API_KEY"):
                raise MissingAPIKeyError(
                    "GOOGLE_API_KEY environment variable not set."
                )
        else:
            if not os.getenv("TOOL_LLM_URL"):
                raise MissingAPIKeyError(
                    "TOOL_LLM_URL environment variable not set."
                )
            if not os.getenv("TOOL_LLM_NAME"):
                raise MissingAPIKeyError(
                    "TOOL_LLM_NAME environment variable not set."
                )

        capabilities = AgentCapabilities(streaming=True, push_notifications=True)
        skill = AgentSkill(
            id="product_search",
            name="Product Search & Recommend",
            description=(
                "Searches and recommends products based on user requirements. "
                "Uses LLM knowledge to generate real product recommendations "
                "with names, prices, descriptions, and purchase links."
            ),
            tags=[
                "shopping",
                "product search",
                "recommendation",
                "e-commerce",
            ],
            examples=[
                "推荐 200 元以下的足球鞋，给出购买链接",
                "推荐一款适合学生的笔记本电脑",
                "有什么好的蓝牙耳机推荐？",
            ],
        )
        agent_card = AgentCard(
            name="Shopping Agent",
            description=(
                "A shopping assistant that recommends products based on user "
                "needs. Uses LLM knowledge to provide real product suggestions "
                "with current market prices and purchase links."
            ),
            url=f"http://{host}:{port}/",
            version="1.0.0",
            default_input_modes=ShoppingAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=ShoppingAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        httpx_client = httpx.AsyncClient()
        push_config_store = InMemoryPushNotificationConfigStore()
        push_sender = BasePushNotificationSender(
            httpx_client=httpx_client, config_store=push_config_store
        )
        request_handler = DefaultRequestHandler(
            agent_executor=ShoppingAgentExecutor(),
            task_store=InMemoryTaskStore(),
            push_config_store=push_config_store,
            push_sender=push_sender,
        )
        server = A2AStarletteApplication(
            agent_card=agent_card, http_handler=request_handler
        )

        uvicorn.run(server.build(), host=host, port=port)

    except MissingAPIKeyError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
