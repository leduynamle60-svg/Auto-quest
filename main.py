#!/usr/bin/env python3
"""
Henxi - Discord Quest Auto-Completer
Khởi động cả Discord Bot và Web Dashboard.
"""

import os
import sys
import logging
import threading

from dotenv import load_dotenv
load_dotenv()

# Setup logging
LOG_FILE = "quest_bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("henxi")


def print_banner():
    banner = r"""
   ██████╗ ███████╗███████╗██╗   ██╗███╗   ███╗███████╗
  ██╔═══██╗██╔════╝██╔════╝██║   ██║████╗ ████║██╔════╝
  ██║   ██║███████╗███████╗██║   ██║██╔████╔██║█████╗
  ██║   ██║╚════██║╚════██║██║   ██║██║╚██╔╝██║██╔══╝
  ╚██████╔╝███████║███████║╚██████╔╝██║ ╚═╝ ██║███████╗
   ╚═════╝ ╚══════╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝╚══════╝
    Discord Quest Auto-Completer — Bot + Dashboard
    """
    print(banner)


def main():
    print_banner()

    # Init database
    import database
    database.init_db()
    log.info("Database initialized: bot_data.db")

    # Check bot token
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not bot_token:
        log.error("❌ DISCORD_BOT_TOKEN not set in .env!")
        print("\n⚠️  Vui lòng thêm DISCORD_BOT_TOKEN vào file .env\n")
        return

    # Chạy bot Discord trong thread riêng
    from bot import run_bot
    threading.Thread(target=run_bot, daemon=True).start()
    log.info("🚀 Discord bot started")

    # Chạy web dashboard (FastAPI)
    from dashboard import run_dashboard
    log.info("🌐 Starting web dashboard at http://localhost:8000")
    run_dashboard(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()