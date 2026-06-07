"""
Dnam - Discord Quest Auto-Completer Bot
"""

import os
import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import random
import database
import aiohttp
import threading
from worker import start_worker, stop_worker, get_worker, get_running_accounts


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("henxi")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ── DM helpers (hỗ trợ Embed) ─────────────────────────────────────────────

async def _send_dm(discord_user_id: int, content):
    try:
        user = await bot.fetch_user(discord_user_id)
        dm = await user.create_dm()
        if isinstance(content, discord.Embed):
            return await dm.send(embed=content)
        return await dm.send(str(content))
    except Exception as e:
        log.warning(f"Không gửi DM: {e}")
        return None


async def _edit_dm(message: discord.Message, content):
    try:
        if isinstance(content, discord.Embed):
            await message.edit(embed=content)
        else:
            await message.edit(content=str(content))
    except Exception as e:
        log.warning(f"Không edit DM: {e}")


def send_dm_sync(discord_user_id: int, content):
    try:
        loop = bot.loop
        if not loop or not loop.is_running():
            return None
        return asyncio.run_coroutine_threadsafe(
            _send_dm(discord_user_id, content), loop
        ).result(timeout=10)
    except Exception as e:
        log.warning(f"send_dm_sync lỗi: {e}")
        return None


def edit_dm_sync(msg: discord.Message, content):
    try:
        loop = bot.loop
        if not loop or not loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            _edit_dm(msg, content), loop
        ).result(timeout=10)
    except Exception as e:
        log.warning(f"edit_dm_sync lỗi: {e}")


# ── Anti-Sleep ────────────────────────────────────────────────────────────

async def keep_alive(bot):
    url = os.environ.get("WEB_URL", "https://auto-quest.onrender.com/")
    my_discord_id = 1115243210596429834
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        log.warning(f"Ping server thất bại: {resp.status}")
                    else:
                        log.info("✅ Ping server OK")
        except Exception as e:
            log.error(f"Lỗi ping: {e}")
        await asyncio.sleep(600)


# ── Embed Builder ─────────────────────────────────────────────────────────

def build_unified_progress_msg(
    quest_overview: dict,
    username: str,
    user_id: str,
    current_quest_id: str = None,
    current_percent: int = 0,
    current_time_str: str = None,
    all_done: bool = False
) -> discord.Embed:
    
    total = len(quest_overview)
    done = sum(1 for q in quest_overview.values() if q.get("status") == "done")
    expired = sum(1 for q in quest_overview.values() if q.get("status") == "expired")
    pending = total - done - expired

    if total > 0 and pending == 0:
        embed = discord.Embed(
            title="🎉 HOÀN THÀNH TẤT CẢ QUEST!",
            description=f"✅ Đã farm xong **{done}/{total}** quest.\nVào Discord nhận thưởng đi **{username}**!",
            color=0x2ecc71
        )
        embed.set_footer(text=f"Auto by Henxi • {datetime.now().strftime('%H:%M')}")
        return embed

    embed = discord.Embed(
        title="🔥 Henxi Auto Quest",
        description=f"👤 **{username}** • `{user_id[-6:]}`",
        color=0x5865F2
    )

    embed.add_field(
        name="📊 TỔNG QUAN",
        value=f"**Tổng:** {total} | ✅ **{done}** | ⏳ **{pending}** | ⚠️ **{expired}**",
        inline=False
    )

    # === PHẦN QUEST ĐANG CÀY (real-time) ===
    pending_quests = [(qid, q) for qid, q in quest_overview.items() 
                     if q.get("status") not in ("done", "expired")]
    pending_quests.sort(key=lambda x: 0 if x[0] == current_quest_id else 1)

    if current_quest_id and current_quest_id in quest_overview:
        q = quest_overview[current_quest_id]
        name = q["name"][:50] + "..." if len(q["name"]) > 50 else q["name"]
        emoji = "🎮" if q["type"] in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY") else "🎬"
        
        bar = "█" * (current_percent // 5) + "░" * (20 - current_percent // 5)
        time_info = current_time_str or f"~{q.get('target_seconds', 0)//60} phút"
        
        embed.add_field(
            name=f"🔥 {emoji} ĐANG CÀY",
            value=f"**{name}**\n`{bar}` **{current_percent}%** • {time_info}",
            inline=False
        )

    # === HÀNG CHỜ (các quest còn lại) ===
    queue = []
    for qid, q in pending_quests:
        if qid == current_quest_id:
            continue
        name = q["name"][:45] + "..." if len(q["name"]) > 45 else q["name"]
        emoji = "🎮" if q["type"] in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY") else "🎬"
        target_min = q.get("target_seconds", 900) // 60
        queue.append(f"{emoji} {name} — ~{target_min} phút")

    if queue:
        embed.add_field(
            name="📋 Hàng chờ",
            value="\n".join(queue[:8]),
            inline=False
        )

    # Thời gian còn lại tổng
    remaining_minutes = 0
    for qid, q in pending_quests:
        if qid == current_quest_id and current_percent > 0:
            target = q.get("target_seconds", 900)
            done_sec = int(target * current_percent / 100)
            remaining_minutes += max(0, (target - done_sec) // 60)
        else:
            remaining_minutes += q.get("target_seconds", 900) // 60

    time_str = f"{remaining_minutes//60}h {remaining_minutes%60}p" if remaining_minutes >= 60 else f"{remaining_minutes} phút"
    embed.add_field(name="⏱️ Thời gian còn lại", value=f"**{time_str}**", inline=False)

    embed.set_footer(text="Auto by Henxi • Update real-time")
    return embed


# ── Events ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    await tree.sync()
    await tree.sync()
    log.info(f"✅ Bot online: {bot.user}")
    log.info("🔧 Commands synced")
    bot.loop.create_task(keep_alive(bot))


# ── Commands ──────────────────────────────────────────────────────────────

@tree.command(name="quest", description="Thêm token & bắt đầu auto quest")
@app_commands.describe(token="Token Discord của account", poll_interval="Chu kỳ kiểm tra (giây)")
async def quest_command(interaction: discord.Interaction, token: str, poll_interval: int = 60):
    await interaction.response.defer(ephemeral=True)
    try:
        database.add_account(token, discord_owner_id=str(interaction.user.id))
        accounts = database.get_all_accounts()
        account = next((a for a in accounts if a.get("token") == token), None)
        if not account:
            await interaction.followup.send("❌ Không tìm thấy tài khoản.", ephemeral=True)
            return

        user_id = account["user_id"]
        username = account.get("global_name") or account.get("username") or user_id[:12]
        discord_user_id = interaction.user.id

        from worker import WorkerAPI, fetch_latest_build_number, get_quest_name, get_task_type, get_seconds_needed, is_completed, get_expires_at

        def start_and_notify():
            try:
                build = fetch_latest_build_number()
                api = WorkerAPI(token, build)
                r = api.get("/quests/@me")

                quest_overview = {}
                if r.status_code == 200:
                    data = r.json()
                    quests = data.get("quests", []) if isinstance(data, dict) else data
                    for q in quests:
                        qid = q.get("id")
                        if not qid: continue
                        expires = get_expires_at(q)
                        is_expired = False
                        if expires:
                            try:
                                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                                if exp_dt <= datetime.now(timezone.utc):
                                    is_expired = True
                            except:
                                pass
                        quest_overview[qid] = {
                            "name": get_quest_name(q),
                            "type": get_task_type(q) or "UNKNOWN",
                            "status": "done" if is_completed(q) else ("expired" if is_expired else "pending"),
                            "target_seconds": get_seconds_needed(q),
                        }

                initial_embed = build_unified_progress_msg(quest_overview, username, user_id)
                main_message = send_dm_sync(discord_user_id, initial_embed)

                w = start_worker(
                    token, user_id, username,
                    poll_interval=poll_interval,
                    auto_accept=True,
                    dm_send=send_dm_sync,
                    dm_edit=edit_dm_sync,
                    discord_user_id=discord_user_id,
                    build_progress_msg_func=build_unified_progress_msg,
                )

                if w and main_message:
                    w._main_msg = main_message

            except Exception as e:
                log.warning(f"start_and_notify lỗi: {e}")

        threading.Thread(target=start_and_notify, daemon=True).start()

        await interaction.followup.send(f"✅ Đã khởi động worker cho **{username}**!", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi: {str(e)}", ephemeral=True)


@tree.command(name="stopquest", description="Dừng auto quest của bạn")
async def stopquest_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    account = database.get_account_by_owner(str(interaction.user.id))
    if not account:
        await interaction.followup.send("❌ Bạn chưa có tài khoản nào.", ephemeral=True)
        return
    stop_worker(account["user_id"])
    await interaction.followup.send("✅ Đã dừng worker!", ephemeral=True)


@tree.command(name="queststatus", description="Xem trạng thái worker đang chạy")
async def queststatus_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    running = get_running_accounts()
    if not running:
        await interaction.followup.send("Không có worker nào đang chạy.", ephemeral=True)
        return
    lines = [
        f"**{w['username']}** (`{w['user_id']}`)\n"
        f"  Status: {w['status']} — {w['message']}\n"
        f"  Completed: {w.get('stats', {}).get('quests_completed', 0)}"
        for w in running
    ]
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


# ── Main ─────────────────────────────────────────────────────────────────

def run_bot():
    database.init_db()
    log.info("📦 Database initialized")
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if token:
        bot.run(token)
    else:
        log.error("DISCORD_BOT_TOKEN không tìm thấy!")


if __name__ == "__main__":
    run_bot()