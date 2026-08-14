from pathlib import Path


def test_wave_checkpoints_do_not_authorize_deployment():
    text = Path("WAVE_CHECKPOINTS.md").read_text().lower()
    assert "no checkpoint implies merge, deployment, or live-capital authorization" in text
