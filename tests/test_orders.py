import unittest
from unittest.mock import Mock, patch

from cli import orders


class OrderTests(unittest.TestCase):
    def setUp(self):
        keys = {
            f"ALPACA_{mode}_{field}": "test"
            for mode in ("PAPER", "LIVE")
            for field in ("KEY_ID", "SECRET_KEY")
        }
        self.enter_patch(patch.dict(orders.os.environ, keys))
        self.post = self.enter_patch(patch.object(orders.requests, "post"))
        self.post.return_value = Mock(status_code=200, json=lambda: {"status": "accepted"})
        self.args = ["submit", "AAPL", "sell", "1", "--limit", "100", "--order-id", "example"]

    def enter_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_default_paper_order_closes_position(self):
        orders.main(self.args)
        self.assertEqual(self.post.call_args.args, ("https://paper-api.alpaca.markets/v2/orders",))
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["position_intent"], "sell_to_close")
        self.assertEqual(payload["client_order_id"], "example")
        self.assertEqual(payload["limit_price"], "100")

    def test_live_requires_matching_confirmation(self):
        with patch("builtins.input", return_value="wrong"), self.assertRaises(SystemExit):
            orders.main([*self.args, "--live"])
        self.post.assert_not_called()
        with patch("builtins.input", return_value="example"):
            orders.main([*self.args, "--live"])
        self.assertEqual(self.post.call_args.args, ("https://api.alpaca.markets/v2/orders",))

    def test_research_must_match_side(self):
        for rating in ("Hold", "Underweight", "Buy", None):
            with patch.object(orders, "research_rating", return_value=rating):
                orders.main([*self.args, "--require-research"])
        self.post.assert_not_called()
        with patch.object(orders, "research_rating", return_value="Sell"):
            orders.main([*self.args, "--require-research"])
        self.post.assert_called_once()

    def test_unknown_submission_can_be_checked_without_resubmission(self):
        self.post.side_effect = orders.requests.Timeout()
        with self.assertRaises(SystemExit):
            orders.main(self.args)
        with patch.object(
            orders.requests, "get", return_value=Mock(status_code=200, json=dict)
        ) as get:
            orders.main(["status", "--order-id", "example"])
        self.post.assert_called_once()
        self.assertEqual(get.call_args.kwargs["params"], {"client_order_id": "example"})


def test_research_order_runs_graph_and_saves_report(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from tradingagents.graph import trading_graph

    model = RunnableLambda(lambda _: AIMessage(content="**Rating**: Buy\n\nFixture analysis."))
    client = Mock()
    client.get_llm.return_value = Mock(
        invoke=model.invoke,
        bind_tools=Mock(return_value=model),
        with_structured_output=Mock(side_effect=NotImplementedError),
    )
    monkeypatch.setattr(trading_graph, "create_llm_client", lambda **_: client)
    monkeypatch.setattr(trading_graph, "resolve_instrument_identity", lambda _: None)

    config = {
        **trading_graph.DEFAULT_CONFIG,
        "data_cache_dir": str(tmp_path / "cache"),
        "results_dir": str(tmp_path / "results"),
        "memory_log_path": None,
        "checkpoint_enabled": False,
    }
    graph_class = trading_graph.TradingAgentsGraph
    monkeypatch.setattr(
        trading_graph,
        "TradingAgentsGraph",
        lambda: graph_class(selected_analysts=("market",), config=config),
    )
    monkeypatch.setenv("ALPACA_PAPER_KEY_ID", "test")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "test")
    with patch.object(
        orders.requests, "post", return_value=Mock(status_code=200, json=dict)
    ) as post:
        orders.main(
            [
                "submit",
                "AAPL",
                "buy",
                "1",
                "--limit",
                "100",
                "--order-id",
                "smoke",
                "--require-research",
            ]
        )
    post.assert_called_once()
    assert post.call_args.kwargs["json"]["side"] == "buy"
    assert any((tmp_path / "results" / "reports").rglob("*.md"))
