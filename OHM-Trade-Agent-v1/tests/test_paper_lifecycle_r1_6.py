from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPER_COMPOSE = ROOT / "docker-compose.paper.yml"


def test_fresh_paper_install_bootstraps_default_disabled_control():
    text = PAPER_COMPOSE.read_text(encoding="utf-8")

    bootstrap = text.index("SYSTEM_BOOTSTRAP")
    validation = text.index("for REQUIRED in")
    assert bootstrap < validation
    assert '"enabled":false' in text
    assert '"paper_only":true' in text
    assert '"kraken_execution_authority":false' in text
    assert "[ ! -s /freqtrade/control/control.json ]" in text


def test_existing_nonempty_control_is_not_overwritten():
    text = PAPER_COMPOSE.read_text(encoding="utf-8")
    assert "if [ ! -s /freqtrade/control/control.json ]; then" in text
