"""Trade Agent Server — 模拟交易 A2A Agent。

通过 A2A 协议对外提供模拟股票交易服务。

用法：
    uv run python app/trade [--host HOST] [--port PORT]

环境变量（与 app/__main__.py 完全一致）：
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

from app.trade.agent import TradeAgent
from app.trade.agent_executor import TradeAgentExecutor


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""


@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10001)
def main(host: str, port: int) -> None:
    """Starts the Trade Agent server."""
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
            id="simulated_trading",
            name="Simulated Stock Trading",
            description=(
                "Simulated stock trading service. Supports checking market "
                "prices, viewing portfolio, placing buy/sell orders, viewing "
                "order history, and depositing simulated funds."
            ),
            tags=[
                "stock trading",
                "simulated trading",
                "portfolio management",
                "market prices",
            ],
            examples=[
                "What is the price of AAPL?",
                "Buy 10 shares of TSLA",
                "Show my portfolio",
                "Sell 5 shares of MSFT",
            ],
        )
        agent_card = AgentCard(
            name="Trade Agent",
            description=(
                "A simulated trading assistant. Manages mock stock portfolios, "
                "provides simulated market prices, and executes paper trades."
            ),
            url=f"http://{host}:{port}/",
            version="1.0.0",
            default_input_modes=TradeAgent.SUPPORTED_CONTENT_TYPES,
            default_output_modes=TradeAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        httpx_client = httpx.AsyncClient()
        push_config_store = InMemoryPushNotificationConfigStore()
        push_sender = BasePushNotificationSender(
            httpx_client=httpx_client, config_store=push_config_store
        )
        request_handler = DefaultRequestHandler(
            agent_executor=TradeAgentExecutor(),
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
