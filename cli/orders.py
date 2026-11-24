"""Submit and inspect user-sized Alpaca equity limit orders."""

import argparse
import json
import os
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests

import tradingagents  # noqa: F401  (loads project environment settings)


def price(value):
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,4})?", value) or Decimal(value) <= 0:
        raise argparse.ArgumentTypeError("Enter a positive dollar price with up to four decimals.")
    return value


def research_rating(symbol):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = TradingAgentsGraph()
    day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    state, rating = graph.propagate(symbol, day)
    print(f"Research report: {graph.save_reports(state, symbol)}")
    headers = re.findall(r"^\*\*Rating\*\*: (\w+)\s*$", state["final_trade_decision"], re.M)
    return rating if headers == [rating] else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = parser.add_subparsers(dest="action", required=True)
    submit = actions.add_parser("submit", allow_abbrev=False)
    status = actions.add_parser("status", allow_abbrev=False)
    for command in (submit, status):
        command.add_argument(
            "--order-id", required=True, help="Unique ID for this intent; reuse on retry."
        )
        command.add_argument("--live", action="store_true", help="Use the real-money account.")
    submit.add_argument("symbol")
    submit.add_argument("side", choices=("buy", "sell"))
    submit.add_argument("shares", type=int)
    submit.add_argument("--limit", type=price, required=True)
    submit.add_argument(
        "--require-research",
        action="store_true",
        help="Only submit if today's research gives an explicit matching Buy or Sell.",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.order_id):
        parser.error("Use 1–48 letters, digits, underscores or hyphens for the order ID.")
    mode = "LIVE" if args.live else "PAPER"
    endpoint = "https://api.alpaca.markets" if args.live else "https://paper-api.alpaca.markets"
    key = os.getenv(f"ALPACA_{mode}_KEY_ID")
    secret = os.getenv(f"ALPACA_{mode}_SECRET_KEY")
    if not key or not secret:
        parser.error(f"Set ALPACA_{mode}_KEY_ID and ALPACA_{mode}_SECRET_KEY.")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = endpoint + "/v2/orders"
    if args.action == "submit":
        symbol = args.symbol.upper()
        if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", symbol) or args.shares < 1:
            parser.error("Use a US equity ticker and a positive whole number of shares.")
        payload = {
            "symbol": symbol,
            "side": args.side,
            "qty": str(args.shares),
            "type": "limit",
            "limit_price": args.limit,
            "time_in_force": "day",
            "client_order_id": args.order_id,
            "position_intent": "buy_to_open" if args.side == "buy" else "sell_to_close",
        }
        if args.require_research:
            rating = research_rating(symbol)
            if rating != args.side.capitalize():
                print(f"Skipped order. Research rating: {rating or 'unavailable'}.")
                return
        if args.live:
            print(f"LIVE {args.side} {args.shares} {symbol} at limit {args.limit}, day order.")
            if input(f"Type {args.order_id} to submit: ") != args.order_id:
                parser.exit(1, "Order canceled locally.\n")
    try:
        if args.action == "status":
            response = requests.get(
                url + ":by_client_order_id",
                params={"client_order_id": args.order_id},
                headers=headers,
                timeout=20,
                allow_redirects=False,
            )
        else:
            response = requests.post(
                url, json=payload, headers=headers, timeout=20, allow_redirects=False
            )
        if not 200 <= response.status_code < 300:
            parser.exit(
                1,
                f"Alpaca HTTP {response.status_code}: {response.text}\n"
                f"Check status using order ID {args.order_id} before retrying.\n",
            )
        print(json.dumps(response.json(), indent=2))
    except (requests.RequestException, ValueError) as exc:
        parser.exit(
            1,
            f"Broker response unavailable ({type(exc).__name__}). "
            f"Check status using order ID {args.order_id} before retrying.\n",
        )


if __name__ == "__main__":
    main()
