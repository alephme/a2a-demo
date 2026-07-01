"""交易工具集 — 使用 Yahoo Finance (yfinance) 获取真实行情。

账户资金、持仓、订单记录为进程级内存模拟。
"""

import time
from typing import Any

import yfinance as yf


# ---------------------------------------------------------------------------
# 模拟账户（进程级内存）
# ---------------------------------------------------------------------------

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


def _get_realtime_price(symbol: str) -> float:
    """从 Yahoo Finance 获取实时价格。"""
    symbol = symbol.upper().strip()
    ticker = yf.Ticker(symbol)
    info = ticker.info
    # 优先用 regularMarketPrice，回退到 previousClose
    price = info.get("regularMarketPrice") or info.get("previousClose")
    if price is None:
        # 最后尝试从 history 获取
        hist = ticker.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
    if price is None:
        raise ValueError(f"Unable to fetch price for {symbol}")
    return round(price, 2)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def get_market_price(symbol: str) -> str:
    """获取指定股票的实时市场价格（来自 Yahoo Finance）。

    Args:
        symbol: 股票代码，如 AAPL、GOOGL、MSFT、TSLA、NVDA 等美股代码。

    Returns:
        包含实时价格信息的字符串。
    """
    symbol = symbol.upper().strip()
    try:
        price = _get_realtime_price(symbol)
        ticker = yf.Ticker(symbol)
        name = ticker.info.get("shortName") or ticker.info.get("longName") or symbol
        return f"📈 {symbol} ({name}) 实时价格：${price:.2f}"
    except Exception:
        return f"❌ 无法获取 {symbol} 的价格。请确认代码是否正确（支持美股代码如 AAPL、TSLA 等）。"


def view_portfolio() -> str:
    """查看当前投资组合：现金余额与各持仓实时市值。"""
    lines = ["📊 **投资组合**\n"]
    lines.append(f"  可用现金：${_account['cash_balance']:,.2f}\n")

    if not _account["positions"]:
        lines.append("  持仓：无（空仓）")
    else:
        lines.append("  持仓明细：")
        total_market_value = 0.0
        for symbol, shares in sorted(_account["positions"].items()):
            try:
                price = _get_realtime_price(symbol)
            except Exception:
                price = 0.0
            value = price * shares
            total_market_value += value
            lines.append(
                f"    • {symbol}: {shares} 股 @ ${price:.2f} = ${value:,.2f}"
            )
        lines.append("")
        lines.append(f"  持仓总市值：${total_market_value:,.2f}")
        lines.append(f"  总资产（现金+市值）：${_account['cash_balance'] + total_market_value:,.2f}")

    return "\n".join(lines)


def place_order(symbol: str, quantity: int, side: str = "buy") -> str:
    """下买卖单（模拟，按实时价格成交）。

    Args:
        symbol: 美股代码。
        quantity: 数量（正数）。
        side: "buy" 买入 或 "sell" 卖出。

    Returns:
        订单执行结果。
    """
    symbol = symbol.upper().strip()
    side = side.lower().strip()

    if quantity <= 0:
        return "❌ 数量必须为正数。"

    if side not in ("buy", "sell"):
        return "❌ side 必须为 'buy' 或 'sell'。"

    try:
        price = _get_realtime_price(symbol)
    except Exception:
        return f"❌ 无法获取 {symbol} 当前价格，请稍后重试。"

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
            f"@ ${o['price']:.2f} | 总 ${o['total']:,.2f} | {o['timestamp']}"
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


def search_stocks(query: str) -> str:
    """搜索股票，返回匹配的股票代码与名称列表。

    当用户不知道准确代码、或想查看某一行业/主题的股票时，使用此工具根据关键词搜索。

    Args:
        query: 搜索关键词，如 "AI"、"半导体"、"新能源汽车"、"银行" 等。

    Returns:
        匹配的股票列表，含代码和公司名称。
    """
    try:
        ticker = yf.Ticker(query.upper().strip())
        info = ticker.info
        if info.get("symbol"):
            name = info.get("shortName") or info.get("longName") or query
            return f"🔍 搜索 '{query}' 的结果：\n  • {info['symbol']}: {name}"
    except Exception:
        pass

    # 如果直接搜索失败，返回提示
    return (
        f"🔍 未找到与 '{query}' 精确匹配的股票。\n"
        f"提示：请使用美股代码（如 AAPL、TSLA、MSFT）或公司英文名。"
    )
