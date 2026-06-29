"""模拟交易工具集。

所有数据均为内存模拟，不涉及真实资金和市场。
"""

import random
import time
from typing import Any


# ---------------------------------------------------------------------------
# 模拟数据存储（进程级内存）
# ---------------------------------------------------------------------------

_BASE_PRICES: dict[str, float] = {
    "AAPL": 180.50,
    "GOOGL": 175.20,
    "MSFT": 420.30,
    "AMZN": 178.90,
    "TSLA": 240.60,
    "NVDA": 880.10,
    "META": 510.40,
    "BTC": 67500.00,
    "ETH": 3500.00,
}

_account = {
    "cash_balance": 100000.00,  # 初始模拟资金
    "positions": {},             # symbol -> shares
}

_orders: list[dict[str, Any]] = []       # 订单历史
_order_id_counter: int = 0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _next_order_id() -> str:
    global _order_id_counter
    _order_id_counter += 1
    return f"ORD{_order_id_counter:06d}"


def _simulate_price(symbol: str) -> float:
    """在基础价格上加随机波动，模拟实时价格。"""
    base = _BASE_PRICES.get(symbol)
    if base is None:
        raise ValueError(f"Unknown symbol: {symbol}")
    change = base * random.uniform(-0.03, 0.03)  # ±3% 波动
    return round(base + change, 2)


# ---------------------------------------------------------------------------
# Tools（每个 tool 都是普通同步函数，agent.py 中用 @tool 包装）
# ---------------------------------------------------------------------------

def get_market_price(symbol: str) -> str:
    """获取指定股票的模拟实时价格。

    Args:
        symbol: 股票代码，如 AAPL、GOOGL、MSFT、TSLA 等。

    Returns:
        包含价格信息的字符串。
    """
    symbol = symbol.upper().strip()
    if symbol not in _BASE_PRICES:
        available = ", ".join(sorted(_BASE_PRICES.keys()))
        return f"❌ 未知股票代码 {symbol}。支持的代码：{available}"

    price = _simulate_price(symbol)
    return f"📈 {symbol} 当前模拟价格：${price:.2f}"


def view_portfolio() -> str:
    """查看当前模拟投资组合：现金余额与各持仓情况。"""
    lines = ["📊 **模拟投资组合**\n"]
    lines.append(f"  可用现金：${_account['cash_balance']:,.2f}\n")

    if not _account["positions"]:
        lines.append("  持仓：无（空仓）")
    else:
        lines.append("  持仓明细：")
        total_market_value = 0.0
        for symbol, shares in sorted(_account["positions"].items()):
            price = _simulate_price(symbol)
            value = price * shares
            total_market_value += value
            lines.append(
                f"    • {symbol}: {shares} 股 @ ${price:.2f} = ${value:,.2f}"
            )
        lines.append("")
        lines.append(f"  总市值：${total_market_value:,.2f}")
        lines.append(f"  总资产（现金+市值）：${_account['cash_balance'] + total_market_value:,.2f}")

    return "\n".join(lines)


def place_order(symbol: str, quantity: int, side: str = "buy") -> str:
    """下模拟买卖单。

    Args:
        symbol: 股票代码。
        quantity: 数量（正数）。
        side: "buy" 买入 或 "sell" 卖出。

    Returns:
        订单执行结果。
    """
    symbol = symbol.upper().strip()
    side = side.lower().strip()

    if symbol not in _BASE_PRICES:
        available = ", ".join(sorted(_BASE_PRICES.keys()))
        return f"❌ 未知股票代码 {symbol}。支持的代码：{available}"

    if quantity <= 0:
        return "❌ 数量必须为正数。"

    if side not in ("buy", "sell"):
        return "❌ side 必须为 'buy' 或 'sell'。"

    price = _simulate_price(symbol)
    total_cost = round(price * quantity, 2)

    order_id = _next_order_id()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    if side == "buy":
        if total_cost > _account["cash_balance"]:
            return (
                f"❌ 资金不足。需要 ${total_cost:,.2f}，"
                f"可用现金 ${_account['cash_balance']:,.2f}"
            )
        _account["cash_balance"] = round(_account["cash_balance"] - total_cost, 2)
        _account["positions"][symbol] = _account["positions"].get(symbol, 0) + quantity
    else:  # sell
        current_shares = _account["positions"].get(symbol, 0)
        if quantity > current_shares:
            return f"❌ 持仓不足。{symbol} 持仓 {current_shares} 股，无法卖出 {quantity} 股。"
        _account["cash_balance"] = round(_account["cash_balance"] + total_cost, 2)
        if quantity == current_shares:
            del _account["positions"][symbol]
        else:
            _account["positions"][symbol] = current_shares - quantity

    order = {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "total": total_cost,
        "timestamp": timestamp,
    }
    _orders.append(order)

    action = "买入" if side == "buy" else "卖出"
    return (
        f"✅ 订单已执行：{action} {quantity} 股 {symbol}\n"
        f"   成交价：${price:.2f}　合计：${total_cost:,.2f}\n"
        f"   订单号：{order_id}　时间：{timestamp}"
    )


def view_order_history() -> str:
    """查看历史订单记录。"""
    if not _orders:
        return "📋 暂无历史订单。"

    lines = ["📋 **历史订单**\n"]
    for o in reversed(_orders[-20:]):  # 最多显示最近 20 条
        action = "买入" if o["side"] == "buy" else "卖出"
        lines.append(
            f"  {o['order_id']} | {action} {o['quantity']} 股 {o['symbol']} "
            f"@ ${o['price']:.2f} | {o['total']:,.2f} | {o['timestamp']}"
        )
    return "\n".join(lines)


def deposit(amount: float) -> str:
    """向模拟账户存入资金（仅用于测试）。

    Args:
        amount: 存入金额（正数）。

    Returns:
        操作结果。
    """
    if amount <= 0:
        return "❌ 存入金额必须为正数。"
    _account["cash_balance"] = round(_account["cash_balance"] + amount, 2)
    return f"💰 已存入 ${amount:,.2f}，当前现金余额：${_account['cash_balance']:,.2f}"


def get_supported_symbols() -> str:
    """获取所有支持的股票代码列表。"""
    symbols = sorted(_BASE_PRICES.keys())
    return f"📋 支持交易的标的：{'、'.join(symbols)}"
