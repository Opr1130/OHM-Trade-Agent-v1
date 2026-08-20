from app.jobs import drain_tradingview_inbox


def test_tradingview_drain_processes_multiple_small_batches(monkeypatch, capsys):
    calls = []
    results = [
        {"checked": 2, "processed": 2, "qualified": 2},
        {"checked": 2, "processed": 1, "rejected": 1},
        {"checked": 0},
    ]

    def fake_process():
        calls.append(1)
        return results.pop(0)

    monkeypatch.setattr(drain_tradingview_inbox, "process_queued_events", fake_process)
    drain_tradingview_inbox.main()
    output = capsys.readouterr().out
    assert len(calls) == 3
    assert "checked: 4" in output
    assert "processed: 3" in output
    assert "qualified: 2" in output
    assert "TradingView promotion authority: False" in output
