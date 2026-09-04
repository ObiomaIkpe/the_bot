"""
One-off, run-by-hand script -- verifies TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
are actually working end-to-end (a real message lands in the group),
not just that they're set. send_telegram_alert() fails quietly on a
bad token/chat-id (logged, never raised -- see app/core/telegram.py's
own docstring), so a real send is the only way to know for sure.

Run via: docker compose exec -T api python -m app.scripts.test_telegram_alert
"""
from app.core.telegram import send_telegram_alert


def main():
    send_telegram_alert("Test alert from the-bot -- if you see this, alerting is working.")
    print("Sent (check the logs above for a warning if it actually failed).")


if __name__ == "__main__":
    main()
