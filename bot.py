"""
Henxi - Discord Quest Auto-Completer Bot
"""

import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import database
from worker import start_worker, stop_worker, get_worker, get_running_accounts


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("henxi")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ── DM helpers ────────────────────────────────────────────────────────────────

async def _send_dm_text(discord_user_id: int, content: str) -> discord.Message | None:
    try:
        user = await bot.fetch_user(discord_user_id)
        dm = await user.create_dm()
        return await dm.send(content)
    except Exception as e:
        log.warning(f"Không gửi được DM: {e}")
        return None


async def _edit_dm_text(message: discord.Message, content: str):
    try:
        await message.edit(content=content)
    except Exception as e:
        log.warning(f"Không edit được DM: {e}")


def send_dm_sync(discord_user_id: int, content: str):
    try:
        loop = bot.loop
        if not loop or not loop.is_running():
            return None
        return asyncio.run_coroutine_threadsafe(
            _send_dm_text(discord_user_id, content), loop
        ).result(timeout=10)
    except Exception as e:
        log.warning(f"send_dm_sync lỗi: {e}")
        return None


def edit_dm_sync(msg: discord.Message, content: str):
    try:
        loop = bot.loop
        if not loop or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            _edit_dm_text(msg, content), loop
        ).result(timeout=10)
    except Exception as e:
        log.warning(f"edit_dm_sync lỗi: {e}")


# ── Message builders ──────────────────────────────────────────────────────────

def build_overview_msg(quests: list, username: str, user_id: str) -> str:
    """Tin nhắn 1: Tổng quan danh sách quest (giống ảnh 1)"""
    now = datetime.now(timezone.utc)

    completed = []
    pending = []
    expired = []

    for q in quests:
        from worker import (get_quest_name, get_task_type, get_seconds_needed,
                            is_completed, get_expires_at)
        name = get_quest_name(q)
        task_type = get_task_type(q) or "UNKNOWN"
        seconds = get_seconds_needed(q)
        expires = get_expires_at(q)

        # Format thời gian
        if seconds >= 60:
            time_str = f"{seconds // 60}m"
        else:
            time_str = f"{seconds}s"

        # Kiểm tra hết hạn
        is_exp = False
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if exp_dt <= now:
                    is_exp = True
            except Exception:
                pass

        entry = {"name": name, "task_type": task_type, "time_str": time_str}

        if is_exp:
            expired.append(entry)
        elif is_completed(q):
            completed.append(entry)
        else:
            pending.append(entry)

    total = len(quests)
    lines = [
        f"📋 **Danh sách Quest**",
        f"Tìm thấy **{total} quest**: ✅ Hoàn thành: **{len(completed)}** • ⌛ Cần làm: **{len(pending)}** • 🔴 Hết hạn: **{len(expired)}**",
        "",
    ]

    for q in completed:
        lines.append(f"✅ **{q['name']}**")
        lines.append(f"┗ `{q['task_type']}` • {q['time_str']} • Hoàn thành")

    for q in pending:
        lines.append(f"⌛ **{q['name']}**")
        lines.append(f"┗ `{q['task_type']}` • {q['time_str']} • Đã nhận")

    # Chỉ hiện 3 quest hết hạn đầu
    shown_expired = expired[:3]
    for q in shown_expired:
        lines.append(f"🔴 **{q['name']}**")
        lines.append(f"┗ `{q['task_type']}` • {q['time_str']} • Hết hạn")
    if len(expired) > 3:
        lines.append(f"... và {len(expired) - 3} quest khác")

    # Ước tính thời gian (đã fix lỗi ép kiểu)
    try:
        total_sec = sum(
            int(q["time_str"].replace("m", "")) * 60 if "m" in q["time_str"]
            else int(q["time_str"].replace("s", ""))
            for q in pending
        )
        minutes = total_sec // 60
        seconds = total_sec % 60
        lines.append("")
        lines.append(f"⏱️ **Ước tính hoàn thành**")
        lines.append(f"~{minutes}p {seconds}s")
    except Exception as e:
        lines.append("")
        lines.append(f"(Lỗi tính thời gian: {e})")

    lines.append("")
    lines.append(f"User ID: {user_id} • Quest Auto-Complete • {datetime.now().strftime('%H:%M %d/%m/%Y')}")

    return "\n".join(lines)


def build_progress_msg(quest_overview: dict, username: str, user_id: str,
                       current_quest_id: str = None, current_percent: int = 0,
                       current_time_str: str = None, all_done: bool = False) -> str:
    """Tin nhắn 2: Tiến độ real-time (giống ảnh 2)"""
    if all_done:
        done_count = sum(1 for q in quest_overview.values() if q["status"] == "done")
        total = len(quest_overview)
        return (
            f"🎉 **Hoàn thành tất cả Quest!**\n\n"
            f"✅ Đã hoàn thành **{done_count}/{total}** quest\n"
            f"Vào Discord nhận thưởng nhé!\n\n"
            f"Đã xong: {done_count}/{total} quest • {datetime.now().strftime('%H:%M %d/%m/%Y')}"
        )

    done_count = sum(1 for q in quest_overview.values() if q["status"] == "done")
    total = len(quest_overview)

    lines = [
        f"❓ **Đang hoàn thành Quest... [{user_id[:6]}]**",
        "",
        f"📊 Tiến độ tất cả Quest",
    ]

    # Sắp xếp: đang cày lên đầu
    sorted_quests = sorted(
        quest_overview.items(),
        key=lambda x: (
            0 if x[0] == current_quest_id else
            2 if x[1]["status"] == "done" else
            1
        )
    )

    for qid, q in sorted_quests:
        name = q["name"]
        status = q["status"]
        target_sec = q.get("target_seconds", 0)
        time_str = f"{target_sec // 60}m" if target_sec >= 60 else f"{target_sec}s"

        if status == "expired":
            continue
        elif status == "done":
            bar = "█" * 20
            lines.append(f"✅ **{name}**")
            lines.append(f"[{bar}] 100% • 0s")
            lines.append("")
        elif qid == current_quest_id:
            filled = int(current_percent / 100 * 20)
            bar = "█" * filled + "░" * (20 - filled)
            time_display = current_time_str or time_str
            lines.append(f"❓ **{name}**")
            lines.append(f"[{bar}] {current_percent}% • {time_display}")
            lines.append("")
        else:
            bar = "░" * 20
            lines.append(f"⌛ **{name}**")
            lines.append(f"[{bar}] 0% • {time_str}")
            lines.append("")

    lines.append(f"Đã xong: {done_count}/{total} quest • {datetime.now().strftime('%H:%M %d/%m/%Y')}")
    return "\n".join(lines)


# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    log.info(f"✅ Bot online: {bot.user}")
    log.info("🔧 Commands synced")


# ── Commands ──────────────────────────────────────────────────────────────────

@tree.command(name="quest", description="Thêm token & bắt đầu auto quest")
@app_commands.describe(token="Token Discord của account", poll_interval="Chu kỳ kiểm tra (giây)")
async def quest_command(interaction: discord.Interaction, token: str, poll_interval: int = 60):
    await interaction.response.defer(ephemeral=True)
    try:
        database.add_account(token, discord_owner_id=str(interaction.user.id))
        accounts = database.get_all_accounts()
        account = next((a for a in accounts if a.get("token") == token), None)
        if not account:
            await interaction.followup.send("❌ Không tìm thấy tài khoản sau khi thêm.", ephemeral=True)
            return

        user_id = account["user_id"]
        username = account.get("global_name") or account.get("username") or user_id[:12]
        discord_user_id = interaction.user.id

        # Gửi tin nhắn 1 ngay sau khi fetch quest
        from worker import WorkerAPI, fetch_latest_build_number, is_completed, is_completable
        import threading

        def start_and_notify():
            try:
                # Fetch quests để gửi tin nhắn 1
                build = fetch_latest_build_number()
                api = WorkerAPI(token, build)
                r = api.get("/quests/@me")
                if r.status_code == 200:
                    data = r.json()
                    quests = data.get("quests", []) if isinstance(data, dict) else data
                    overview_msg = build_overview_msg(quests, username, user_id)
                    send_dm_sync(discord_user_id, overview_msg)

                # Start worker với DM callback
                w = start_worker(
                    token, user_id, username,
                    poll_interval=poll_interval,
                    auto_accept=True,
                    dm_send=send_dm_sync,
                    dm_edit=edit_dm_sync,
                    discord_user_id=discord_user_id,
                    build_progress_msg_func=build_progress_msg,
                )
            except Exception as e:
                log.warning(f"start_and_notify lỗi: {e}")

        threading.Thread(target=start_and_notify, daemon=True).start()

        await interaction.followup.send(
            f"✅ Đã khởi động worker cho **{username}**!\nTôi sẽ gửi DM tiến độ quest cho bạn.",
            ephemeral=True
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi: {str(e)}", ephemeral=True)


@tree.command(name="stopquest", description="Dừng auto quest của bạn")
async def stopquest_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    account = database.get_account_by_owner(str(interaction.user.id))
    if not account:
        await interaction.followup.send("❌ Bạn chưa có tài khoản nào.", ephemeral=True)
        return
    user_id = account["user_id"]
    stop_worker(user_id)
    await interaction.followup.send("✅ Đã dừng worker của bạn!", ephemeral=True)


@tree.command(name="queststatus", description="Xem trạng thái worker đang chạy")
async def queststatus_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    running = get_running_accounts()
    if not running:
        await interaction.followup.send("Không có worker nào đang chạy.", ephemeral=True)
        return
    lines = []
    for w in running:
        stats = w.get("stats", {})
        lines.append(
            f"**{w['username']}** (`{w['user_id']}`)\n"
            f"  Status: {w['status']} — {w['message']}\n"
            f"  Completed: {stats.get('quests_completed', 0)} | Cycles: {stats.get('cycles_run', 0)}"
        )
    await interaction.followup.send("\n\n".join(lines), ephemeral=True)


@tree.command(name="questlist", description="Xem danh sách tài khoản đã thêm")
async def questlist_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    accounts = database.get_all_accounts()
    if not accounts:
        await interaction.followup.send("Chưa có tài khoản nào.", ephemeral=True)
        return
    running_ids = {w["user_id"] for w in get_running_accounts()}
    lines = []
    for a in accounts:
        uid = a.get("user_id", "?")
        name = a.get("global_name") or a.get("username") or uid[:12]
        status = "🟢 Running" if uid in running_ids else "⚫ Offline"
        lines.append(f"{status} **{name}** (`{uid}`)")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_bot():
    database.init_db()
    log.info("📦 Database initialized")
    log.info("🚀 Starting quest bot...")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        bot.run(token)
    else:
        log.error("DISCORD_BOT_TOKEN không tìm thấy trong file .env!")


if __name__ == "__main__":
    run_bot()