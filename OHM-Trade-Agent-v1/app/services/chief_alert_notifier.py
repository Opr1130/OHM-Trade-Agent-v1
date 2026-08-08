from typing import Any

from app.services.telegram_notifier import send_telegram_message


def format_chief_alert(candidate: dict[str, Any], summary: str) -> str:
    return (
        "🚨 OHM AI CHIEF ANALYST ALERT\n\n"
        f"Symbol: {candidate.get('symbol', 'UNKNOWN')}\n"
        f"Confidence: {candidate.get('confidence', 0)}%\n"
        f"Risk: {str(candidate.get('risk_level', 'unknown')).upper()}\n"
        f"Decision: {str(candidate.get('decision', 'watch')).upper()}\n\n"
        f"Reason:\n{candidate.get('reason', '')}\n\n"
        f"Chief Analyst Summary:\n{summary}"
    )


def send_chief_alerts(
    alerts: list[dict[str, Any]],
    summary: str,
    bot_token: str,
    chat_id: str,
) -> int:
    sent = 0

    for candidate in alerts:
        message = format_chief_alert(candidate, summary)

        if send_telegram_message(bot_token, chat_id, message):
            sent += 1

    return sent
