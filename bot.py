"""JARVIS Discord shop and server management bot for JARVIS AI.

SellAuth Checkout API is intentionally not used. Purchases open the configured
storefront and Customer is assigned only by an explicit admin verification.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import random
import shutil
import sqlite3
import string
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from discord.ext import commands, tasks
from dotenv import load_dotenv
try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

ROOT = Path(__file__).resolve().parent
WELCOME_BACKGROUND = ROOT / "jarvis.jpg"
STOCK_BACKGROUND = ROOT / "jarvis.jpg"
load_dotenv(dotenv_path=ROOT / ".env", override=True)
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).expanduser()
DATA.mkdir(exist_ok=True)
DB = DATA / "jarvis.sqlite3"
PREFIX = "$"
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.FileHandler(DATA / "jarvis.log", encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("jarvis")


def make_welcome_card(avatar_bytes: bytes, username: str, member_count: int) -> bytes:
    """Create a personalized gold/cyan JARVIS welcome card from the bundled art."""
    width, height = 1200, 600
    with Image.open(WELCOME_BACKGROUND).convert("RGB") as source:
        background = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(2))
    tint = Image.new("RGBA", (width, height), (5, 8, 18, 128))
    canvas = Image.alpha_composite(background.convert("RGBA"), tint)
    draw = ImageDraw.Draw(canvas, "RGBA")
    # Deep navy panel and restrained gold/cyan accents echo the supplied helmet art.
    draw.rounded_rectangle((40, 38, width-40, height-38), radius=34,
                           fill=(8, 13, 24, 170), outline=(221, 165, 67, 225), width=3)
    draw.rounded_rectangle((40, 38, 57, height-38), radius=8, fill=(15, 220, 255, 230))

    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = ImageOps.fit(avatar, (244, 244), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", avatar.size, 0)
    ImageDraw.Draw(mask).ellipse((2, 2, 241, 241), fill=255)
    avatar.putalpha(mask)
    ax, ay = 100, 178
    draw.ellipse((ax-9, ay-9, ax+253, ay+253), fill=(0, 225, 255, 50), outline=(13, 220, 255, 245), width=5)
    canvas.alpha_composite(avatar, (ax, ay))
    draw = ImageDraw.Draw(canvas)
    try:
        title_font = ImageFont.truetype("arial.ttf", 36)
        name_font = ImageFont.truetype("arial.ttf", 58)
        body_font = ImageFont.truetype("arial.ttf", 27)
        small_font = ImageFont.truetype("arial.ttf", 21)
    except OSError:
        title_font, name_font, body_font, small_font = [ImageFont.load_default() for _ in range(4)]
    x = 410
    draw.text((x, 118), "JARVIS AI  /  JARVIS", font=small_font, fill=(67, 226, 255, 255))
    draw.text((x, 177), "WELCOME TO THE SHOP", font=title_font, fill=(241, 190, 92, 255))
    shown_name = username if len(username) <= 24 else username[:21] + "..."
    draw.text((x, 242), shown_name, font=name_font, fill=(255, 255, 255, 255))
    draw.text((x, 332), "Your place for trusted digital goods.", font=body_font, fill=(221, 229, 242, 255))
    draw.line((x, 407, width-105, 407), fill=(15, 220, 255, 180), width=2)
    draw.text((x, 430), f"You are member #{member_count:,}", font=small_font, fill=(220, 220, 230, 255))
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _card_font(size: int):
    for font_path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_stock_card(item_name: str, stock: str, status: str) -> bytes:
    """Render a product-specific restock card using the bundled JARVIS artwork."""
    # Use the user's black JARVIS artwork on a 960x540 wide canvas without cropping it.
    width, height = 960, 540
    art_path = STOCK_BACKGROUND if STOCK_BACKGROUND.exists() else WELCOME_BACKGROUND
    with Image.open(art_path).convert("RGB") as source:
        background = ImageOps.fit(source, (width, height), method=Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(12))
        full_art = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS).convert("RGBA")
    canvas = Image.alpha_composite(background.convert("RGBA"), Image.new("RGBA", (width, height), (3, 6, 14, 72)))
    canvas.alpha_composite(full_art, ((width - full_art.width) // 2, (height - full_art.height) // 2))
    draw = ImageDraw.Draw(canvas, "RGBA")
    # Cover the rightmost end of the source artwork's old gold underline as well as its text.
    draw.rectangle((374, 326, 480, 341), fill=(0, 0, 0, 255))
    draw.rounded_rectangle((18, 55, 380, 485), radius=24, fill=(4, 5, 9, 245), outline=(220, 169, 74, 240), width=3)
    draw.rounded_rectangle((18, 55, 32, 485), radius=8, fill=(19, 213, 245, 245))
    draw.text((46, 84), "JARVIS  |  STOCK UPDATE", font=_card_font(17), fill=(55, 222, 248, 255))
    draw.text((46, 132), status.upper(), font=_card_font(29), fill=(244, 190, 91, 255))

    font_size = 25
    name_font = _card_font(font_size)
    safe_name = str(item_name).replace("\n", " ").strip() or "Store item"
    # Wrap the detected product title so even long names fit in the compact card.
    words = safe_name.split()
    lines = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=name_font)[2] <= 315:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    if not lines:
        lines = ["Store item"]
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1].rstrip(" .") + "..."
    while draw.textbbox((0, 0), lines[-1], font=name_font)[2] > 315 and font_size > 21:
        font_size -= 2
        name_font = _card_font(font_size)
    while draw.textbbox((0, 0), lines[-1] + "...", font=name_font)[2] > 315 and len(lines[-1]) > 1:
        lines[-1] = lines[-1][:-1]
    if len(lines) > 1 and not lines[-1].endswith("...") and " " not in safe_name and lines[-1] != safe_name:
        lines[-1] = lines[-1].rstrip() + "..."
    name_y = 200
    for line_index, name_line in enumerate(lines):
        draw.text((46, name_y + line_index * 34), name_line, font=name_font, fill=(255, 255, 255, 255))
    quantity = f"{stock} unit" if str(stock).strip() == "1" else f"{stock} units"
    quantity_y = 292 if len(lines) == 1 else 310
    draw.text((48, quantity_y), quantity[:100], font=_card_font(22), fill=(226, 232, 240, 255))
    draw.line((46, 380, 332, 380), fill=(220, 169, 74, 180), width=2)
    draw.text((48, 405), "From website | .gg/jarvismart", font=_card_font(14), fill=(190, 203, 218, 255))
    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def stock_change_status(previous_stock: Optional[str], current_stock: str) -> str:
    if previous_stock is None:
        return "NEW ARRIVAL"
    try:
        if float(current_stock) > float(previous_stock):
            return "RESTOCKED"
    except (TypeError, ValueError):
        pass
    return "STOCK UPDATED"


async def send_stock_notification(channel, item_name: str, stock: str, status: str):
    safe_name = discord.utils.escape_mentions(str(item_name))[:256]
    embed = discord.Embed(
        title=f"📦 {status.title()} • {safe_name}",
        description=f"**{safe_name}**\nAvailable stock: **{stock} {'unit' if str(stock).strip() == '1' else 'units'}**",
        color=0xD4AF37,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="From the website  •  .gg/jarvismart")
    try:
        card = make_stock_card(safe_name, str(stock), status)
        image = discord.File(io.BytesIO(card), filename="jarvis-stock-update.png")
        embed.set_image(url="attachment://jarvis-stock-update.png")
        await channel.send(embed=embed, file=image, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.exception("Could not render or send the stock card; sending text embed instead")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS config(guild INTEGER, key TEXT, value TEXT, PRIMARY KEY(guild,key));
        CREATE TABLE IF NOT EXISTS products(guild INTEGER, id TEXT, name TEXT, price TEXT, stock TEXT, url TEXT, updated TEXT, PRIMARY KEY(guild,id));
        CREATE TABLE IF NOT EXISTS tickets(guild INTEGER, channel INTEGER PRIMARY KEY, user INTEGER, kind TEXT, product TEXT, claimed INTEGER, opened TEXT, status TEXT, order_ref TEXT DEFAULT '', details TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, guild INTEGER, user INTEGER, product TEXT, qty INTEGER, amount REAL, seller INTEGER, status TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS profiles(guild INTEGER, user INTEGER, points INTEGER DEFAULT 0, xp INTEGER DEFAULT 0, PRIMARY KEY(guild,user));
        CREATE TABLE IF NOT EXISTS vouches(guild INTEGER, id INTEGER PRIMARY KEY AUTOINCREMENT, author INTEGER, seller INTEGER, text TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS warnings(guild INTEGER, user INTEGER, mod INTEGER, reason TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS reaction_roles(guild INTEGER, channel INTEGER, message INTEGER, emoji TEXT, role INTEGER, PRIMARY KEY(guild,message,emoji));
        CREATE TABLE IF NOT EXISTS custom(guild INTEGER, name TEXT, response TEXT, PRIMARY KEY(guild,name));
        CREATE TABLE IF NOT EXISTS giveaways(guild INTEGER, channel INTEGER, message INTEGER PRIMARY KEY, prize TEXT, ends TEXT, host INTEGER, ended INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS giveaway_entries(message INTEGER, user INTEGER, PRIMARY KEY(message,user));
        CREATE TABLE IF NOT EXISTS claims(guild INTEGER, seller INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(guild,seller));
        CREATE TABLE IF NOT EXISTS invite_snapshots(guild INTEGER, code TEXT, inviter INTEGER, uses INTEGER DEFAULT 0, PRIMARY KEY(guild,code));
        CREATE TABLE IF NOT EXISTS invite_joins(id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER, user INTEGER, inviter INTEGER, invite_code TEXT, joined TEXT);
        CREATE TABLE IF NOT EXISTS verification_overwrites(guild INTEGER, channel INTEGER, target_type TEXT, target INTEGER, view_value INTEGER, PRIMARY KEY(guild,channel,target_type,target));
        """)
        ticket_columns = {row[1] for row in db.execute("PRAGMA table_info(tickets)").fetchall()}
        if "order_ref" not in ticket_columns:
            db.execute("ALTER TABLE tickets ADD COLUMN order_ref TEXT DEFAULT ''")
        if "details" not in ticket_columns:
            db.execute("ALTER TABLE tickets ADD COLUMN details TEXT DEFAULT ''")


def create_database_snapshot(target: Path):
    """Create a consistent SQLite snapshot, including any committed WAL data."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as source, sqlite3.connect(target) as destination:
        source.backup(destination)


def validate_database_file(path: Path):
    """Check database integrity and expected project tables without modifying it."""
    if not path.is_file():
        raise ValueError("That backup file does not exist.")
    try:
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        raise ValueError(f"The backup could not be read: {exc}") from exc
    required = {"config", "products", "tickets", "orders", "profiles"}
    if not integrity or integrity[0] != "ok":
        raise ValueError("SQLite integrity check failed; this backup cannot be restored.")
    missing = sorted(required - tables)
    if missing:
        raise ValueError("This file is missing JARVIS database tables: " + ", ".join(missing))


def restore_database_snapshot(filename: str):
    """Validate an in-folder backup, snapshot the current DB, then atomically restore."""
    backup_dir = (DATA / "backups").resolve()
    source = (backup_dir / filename).resolve()
    if Path(filename).name != filename or source.parent != backup_dir or source.suffix.lower() != ".sqlite3":
        raise ValueError("Choose a .sqlite3 filename from `$backuplist`; paths are not accepted.")
    validate_database_file(source)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safety_copy = backup_dir / f"pre-restore-{stamp}.sqlite3"
    candidate = DATA / f"restore-candidate-{stamp}.sqlite3"
    create_database_snapshot(safety_copy)
    try:
        with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as backup_db, sqlite3.connect(candidate) as candidate_db:
            backup_db.backup(candidate_db)
        validate_database_file(candidate)
        with sqlite3.connect(DB) as current_db:
            current_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.replace(candidate, DB)
        init_db()
    finally:
        if candidate.exists():
            candidate.unlink()
    return safety_copy.name


def cfg(guild: int, key: str, default: str = "") -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM config WHERE guild=? AND key=?", (guild, key)).fetchone()
    return row["value"] if row else default


def setcfg(guild: int, key: str, value: str):
    with connect() as db:
        db.execute("INSERT INTO config VALUES(?,?,?) ON CONFLICT(guild,key) DO UPDATE SET value=excluded.value", (guild,key,value))


async def translate_text(text: str, language: str = "english") -> str:
    if GoogleTranslator is None:
        return "Translation is not installed yet. The owner needs to run `pip install -r requirements.txt` after updating Jarvis."
    try:
        return await asyncio.to_thread(GoogleTranslator(source="auto", target=language).translate, text[:4500])
    except Exception:
        log.exception("Translation failed")
        return "I could not translate that text. Try a language name or code such as `english`, `spanish`, `hindi`, `fr`, or `de`."


def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role_id = cfg(member.guild.id, "admin_role")
    return any(r.name.lower() == "admin" or (role_id.isdigit() and r.id == int(role_id)) for r in member.roles)


def has_role(member: discord.Member, name: str) -> bool:
    if is_admin(member):
        return True
    role_id = cfg(member.guild.id, f"{name.lower()}_role")
    return any(r.name.lower() == name.lower() or (role_id.isdigit() and r.id == int(role_id)) for r in member.roles)


async def require_admin(ctx):
    if not is_admin(ctx.author):
        await ctx.reply("This command is Admin-only.", mention_author=False)
        return False
    return True


async def require_staff(ctx):
    if not has_role(ctx.author, "Staff"):
        await ctx.reply("This command requires Staff or Admin.", mention_author=False)
        return False
    return True


async def log_to(guild: discord.Guild, message: str):
    channel_id = cfg(guild.id, "log_channel")
    channel = guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel:
        try:
            await channel.send(message[:1900])
        except discord.HTTPException:
            log.exception("Could not send audit message")


async def ensure_roles(guild):
    names = ["Admin", "Staff", "Seller", "Customer", "Member", "Verified"]
    existing = {r.name.lower(): r for r in guild.roles}
    created = []
    for name in reversed(names):
        if name.lower() not in existing:
            try:
                existing[name.lower()] = await guild.create_role(name=name, reason="JARVIS standard role setup")
                created.append(name)
            except discord.Forbidden:
                log.warning("Missing Manage Roles permission in guild %s", guild.id)
                return created
    # Put the standard roles in the requested order where Discord's role hierarchy permits it.
    roles = [existing[n.lower()] for n in names if n.lower() in existing]
    try:
        for index, role in enumerate(reversed(roles), start=1):
            await role.edit(position=index)
    except discord.HTTPException:
        pass
    return created


async def cache_guild_invites(guild):
    """Seed invite-use counts so later joins can be attributed without erasing saved baselines."""
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        log.warning("Invite tracking needs Manage Server permission in guild %s", guild.id)
        return
    except discord.HTTPException:
        log.exception("Could not fetch invites for guild %s", guild.id)
        return
    with connect() as db:
        for invite in invites:
            db.execute(
                "INSERT INTO invite_snapshots(guild,code,inviter,uses) VALUES(?,?,?,?) "
                "ON CONFLICT(guild,code) DO UPDATE SET inviter=excluded.inviter,uses=excluded.uses",
                (guild.id, invite.code, invite.inviter.id if invite.inviter else None, invite.uses or 0),
            )


async def track_member_invite(member):
    """Attribute a new human join only when exactly one invite's use count increased."""
    if member.bot:
        return None, None
    guild = member.guild
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Invite attribution unavailable in guild %s", guild.id)
        invites = []

    candidates = []
    with connect() as db:
        for invite in invites:
            current_uses = invite.uses or 0
            previous = db.execute(
                "SELECT uses,inviter FROM invite_snapshots WHERE guild=? AND code=?",
                (guild.id, invite.code),
            ).fetchone()
            if previous and current_uses > previous["uses"]:
                candidates.append((invite.inviter.id if invite.inviter else previous["inviter"], invite.code))
            db.execute(
                "INSERT INTO invite_snapshots(guild,code,inviter,uses) VALUES(?,?,?,?) "
                "ON CONFLICT(guild,code) DO UPDATE SET inviter=excluded.inviter,uses=excluded.uses",
                (guild.id, invite.code, invite.inviter.id if invite.inviter else None, current_uses),
            )
        inviter_id, invite_code = candidates[0] if len(candidates) == 1 else (None, None)
        db.execute(
            "INSERT INTO invite_joins(guild,user,inviter,invite_code,joined) VALUES(?,?,?,?,?)",
            (guild.id, member.id, inviter_id, invite_code, datetime.now(timezone.utc).isoformat()),
        )
    return inviter_id, invite_code


async def close_ticket_channel(channel, guild, actor, row):
    """Save an open ticket transcript, mark it closed, then remove its channel."""
    lines = []
    async for msg in channel.history(limit=None, oldest_first=True):
        lines.append(f"[{msg.created_at.isoformat()}] {msg.author}: {msg.clean_content}")
    transcript = "\n".join(lines) or "(empty ticket)"
    log_channel_id = cfg(guild.id, "log_channel")
    log_channel = guild.get_channel(int(log_channel_id)) if log_channel_id.isdigit() else None
    if log_channel:
        file = discord.File(fp=io.BytesIO(transcript.encode("utf-8")), filename=f"transcript-{channel.id}.txt")
        await log_channel.send(
            f"🧾 Closed ticket {channel.mention}; opener <@{row['user']}>; closer {actor.mention}.",
            file=file,
        )
    with connect() as db:
        db.execute("UPDATE tickets SET status='closed' WHERE channel=?", (channel.id,))
    await asyncio.sleep(2)
    await channel.delete(reason=f"Ticket closed by {actor}")


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def ticket_row(self, interaction):
        with connect() as db:
            return db.execute("SELECT * FROM tickets WHERE channel=? AND status='open'", (interaction.channel_id,)).fetchone()

    def can_manage(self, member):
        return is_admin(member) or has_role(member, "Seller")

    @discord.ui.button(label="Add Details", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="jarvis:ticket:details")
    async def add_details(self, interaction, button):
        row = await self.ticket_row(interaction)
        if not row:
            return await interaction.response.send_message("This ticket is closed or unavailable.", ephemeral=True)
        if interaction.user.id != row["user"]:
            return await interaction.response.send_message("Only the ticket opener can submit these details.", ephemeral=True)
        await interaction.response.send_modal(TicketDetailsModal())

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.primary, custom_id="jarvis:ticket:claim")
    async def claim(self, interaction, button):
        row = await self.ticket_row(interaction)
        if not row:
            return await interaction.response.send_message("This ticket is closed or unavailable.", ephemeral=True)
        if not self.can_manage(interaction.user):
            return await interaction.response.send_message("Only Sellers or Admins can claim tickets.", ephemeral=True)
        if row["claimed"] is not None and row["claimed"] != interaction.user.id and not is_admin(interaction.user):
            return await interaction.response.send_message(f"This ticket is already claimed by <@{row['claimed']}>.", ephemeral=True)
        if row["claimed"] == interaction.user.id:
            return await interaction.response.send_message("You already claimed this ticket.", ephemeral=True)
        with connect() as db:
            db.execute("UPDATE tickets SET claimed=? WHERE channel=?", (interaction.user.id, interaction.channel_id))
            db.execute("INSERT INTO claims VALUES(?,?,1) ON CONFLICT(guild,seller) DO UPDATE SET count=count+1", (interaction.guild_id,interaction.user.id))
        await interaction.response.send_message(f"Claimed by {interaction.user.mention}.")
        await log_to(interaction.guild, f"🎫 Ticket claimed by {interaction.user.mention}: {interaction.channel.mention}")

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="jarvis:ticket:unclaim")
    async def unclaim(self, interaction, button):
        row = await self.ticket_row(interaction)
        if not row:
            return await interaction.response.send_message("This ticket is closed or unavailable.", ephemeral=True)
        if not self.can_manage(interaction.user):
            return await interaction.response.send_message("Only Sellers or Admins can unclaim tickets.", ephemeral=True)
        if row["claimed"] is not None and row["claimed"] != interaction.user.id and not is_admin(interaction.user):
            return await interaction.response.send_message("Only the claiming Seller or an Admin can unclaim this ticket.", ephemeral=True)
        with connect() as db:
            db.execute("UPDATE tickets SET claimed=NULL WHERE channel=?", (interaction.channel_id,))
        await interaction.response.send_message(f"Unclaimed by {interaction.user.mention}.")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="jarvis:ticket:close")
    async def close(self, interaction, button):
        row = await self.ticket_row(interaction)
        if not row:
            return await interaction.response.send_message("This ticket is already closed.", ephemeral=True)
        if not self.can_manage(interaction.user):
            return await interaction.response.send_message("Only Sellers or Admins can close tickets.", ephemeral=True)
        await interaction.response.send_message("Closing ticket and saving transcript…")
        await close_ticket_channel(interaction.channel, interaction.guild, interaction.user, row)


class TicketDetailsModal(discord.ui.Modal, title="Ticket details"):
    product = discord.ui.TextInput(label="Product name (optional)", max_length=100, required=False)
    order_ref = discord.ui.TextInput(label="Order ID (optional)", max_length=80, required=False)
    details = discord.ui.TextInput(label="How can we help?", style=discord.TextStyle.paragraph, max_length=1000, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        with connect() as db:
            row = db.execute("SELECT * FROM tickets WHERE channel=? AND status='open'", (interaction.channel_id,)).fetchone()
            if not row or interaction.user.id != row["user"]:
                return await interaction.response.send_message("Only the opener of an open ticket can submit details.", ephemeral=True)
            product = str(self.product.value).strip() or row["product"] or ""
            order_ref = str(self.order_ref.value).strip()
            details = str(self.details.value).strip()
            db.execute("UPDATE tickets SET product=?,order_ref=?,details=? WHERE channel=?", (product, order_ref, details, interaction.channel_id))
        embed = discord.Embed(title="📝 Customer details", color=0xD4AF37)
        embed.add_field(name="Product", value=discord.utils.escape_mentions(product)[:200] if product else "Not specified", inline=True)
        embed.add_field(name="Order ID", value=discord.utils.escape_mentions(order_ref)[:100] if order_ref else "Not specified", inline=True)
        embed.add_field(name="Request", value=discord.utils.escape_mentions(details)[:1000], inline=False)
        await interaction.response.send_message("Thanks! Your details were added to this private ticket.", ephemeral=True)
        await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def create_ticket(interaction, kind: str, product: str = ""):
    guild, user = interaction.guild, interaction.user
    if guild is None:
        return await interaction.response.send_message("Tickets can only be opened in the server.", ephemeral=True)
    if cfg(guild.id, "maintenance") == "on" and not (isinstance(user, discord.Member) and is_admin(user)):
        return await interaction.response.send_message("Jarvis is currently under maintenance. Please try again later.", ephemeral=True)
    with connect() as db:
        duplicate = db.execute("SELECT channel FROM tickets WHERE guild=? AND user=? AND status='open'", (guild.id,user.id)).fetchone()
    if duplicate:
        ch = guild.get_channel(duplicate["channel"])
        return await interaction.response.send_message(f"You already have an open ticket: {ch.mention if ch else 'existing ticket'}", ephemeral=True)
    category_id = cfg(guild.id, "ticket_category")
    category = guild.get_channel(int(category_id)) if category_id.isdigit() else None
    overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                  user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)}
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
    for role_name in ("Seller", "Admin", "Staff"):
        role = discord.utils.get(guild.roles, name=role_name)
        if role: overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    try:
        channel = await guild.create_text_channel(f"{kind}-{user.name}"[:90], category=category, overwrites=overwrites, reason="JARVIS support ticket")
    except discord.Forbidden:
        return await interaction.response.send_message("JARVIS needs Manage Channels permission to create tickets.", ephemeral=True)
    with connect() as db:
        db.execute(
            "INSERT INTO tickets(guild,channel,user,kind,product,claimed,opened,status,order_ref,details) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (guild.id, channel.id, user.id, kind, product, None, datetime.now(timezone.utc).isoformat(), "open", "", ""),
        )
    embed = discord.Embed(title=f"JARVIS • {kind.title()} ticket", color=0xD4AF37)
    embed.description = f"Customer: {user.mention}\nRequest: {product or kind.title()}\n\nA Seller will assist you. Use **Add Details** to include a product name, order ID, and what you need help with."
    if product:
        embed.add_field(name="Purchase", value=f"Browse or purchase through the configured storefront: {cfg(guild.id,'store_url','Not configured; use $config')}", inline=False)
    seller_id = cfg(guild.id,"seller_role")
    content = f"{user.mention} <@&{seller_id}>" if seller_id.isdigit() else user.mention
    await channel.send(content=content, embed=embed, view=TicketControls())
    await interaction.response.send_message(f"Your ticket is ready: {channel.mention}", ephemeral=True)
    await log_to(guild, f"🎫 {kind.title()} ticket opened by {user.mention}: {channel.mention} {product}")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Help", emoji="🆘", style=discord.ButtonStyle.danger, custom_id="jarvis:panel:help")
    async def help_ticket(self, interaction, button):
        await create_ticket(interaction, "help")

    @discord.ui.button(label="Purchase", emoji="🛒", style=discord.ButtonStyle.success, custom_id="jarvis:panel:purchase")
    async def purchase_ticket(self, interaction, button):
        await create_ticket(interaction, "purchase")

    @discord.ui.button(label="Questions", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="jarvis:panel:questions")
    async def questions_ticket(self, interaction, button):
        await create_ticket(interaction, "questions")


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="jarvis:verify")
    async def verify(self, interaction, button):
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message("Verification is only available inside the server.", ephemeral=True)
        role_id = cfg(guild.id, "verified_role")
        role = guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(guild.roles, name="Verified")
        if role is None:
            return await interaction.response.send_message("The Verified role has not been configured. Please contact an Admin.", ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message("You are already verified. Welcome back!", ephemeral=True)
        roles = [role]
        member_role_id = cfg(guild.id, "member_role")
        member_role = guild.get_role(int(member_role_id)) if member_role_id.isdigit() else discord.utils.get(guild.roles, name="Member")
        if member_role and member_role not in member.roles:
            roles.append(member_role)
        try:
            await member.add_roles(*roles, reason="Jarvis member verification")
        except discord.Forbidden:
            return await interaction.response.send_message("Jarvis cannot assign the verification role. An Admin must move the bot role above it.", ephemeral=True)
        await interaction.response.send_message("✅ You are verified. Server access has been unlocked.", ephemeral=True)
        await log_to(guild, f"✅ {member.mention} completed server verification.")


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Enter giveaway", emoji="🎉", style=discord.ButtonStyle.success, custom_id="jarvis:giveaway:enter")
    async def enter(self, interaction, button):
        with connect() as db:
            giveaway = db.execute("SELECT ended,ends FROM giveaways WHERE message=?",(interaction.message.id,)).fetchone()
            if not giveaway or giveaway["ended"] or giveaway["ends"] <= datetime.now(timezone.utc).isoformat():
                return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            db.execute("INSERT OR IGNORE INTO giveaway_entries VALUES(?,?)",(interaction.message.id,interaction.user.id))
        await interaction.response.send_message("You're entered! The host will draw a winner when it ends.", ephemeral=True)


class SetupRoleView(discord.ui.View):
    role_keys = ("admin_role", "staff_role", "seller_role", "customer_role", "member_role", "verified_role", "autorole_role")

    def __init__(self):
        super().__init__(timeout=180)
        self.selected_key = "admin_role"

    async def interaction_check(self, interaction):
        if interaction.guild and isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            return True
        await interaction.response.send_message("Only an Admin can use this setup panel.", ephemeral=True)
        return False

    @discord.ui.select(placeholder="Choose which role setting to edit", options=[
        discord.SelectOption(label="Admin role", value="admin_role"),
        discord.SelectOption(label="Staff role", value="staff_role"),
        discord.SelectOption(label="Seller role", value="seller_role"),
        discord.SelectOption(label="Customer role", value="customer_role"),
        discord.SelectOption(label="Member role", value="member_role"),
        discord.SelectOption(label="Verified role", value="verified_role"),
        discord.SelectOption(label="Extra autorole", value="autorole_role"),
    ])
    async def choose_setting(self, interaction, select):
        self.selected_key = select.values[0]
        await interaction.response.edit_message(content=f"Now choose the Discord role for **{self.selected_key.replace('_role', '').title()}**.", view=self)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="Choose a server role", min_values=1, max_values=1)
    async def choose_role(self, interaction, select):
        role = select.values[0]
        if role.is_default() or role.managed:
            return await interaction.response.send_message("Choose a regular role that JARVIS can manage.", ephemeral=True)
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("Move the JARVIS bot role above this role before assigning it.", ephemeral=True)
        setcfg(interaction.guild_id, self.selected_key, str(role.id))
        await interaction.response.edit_message(content=f"✅ **{self.selected_key.replace('_role', '').title()}** is now {role.mention}. Select another setting or close this message.", view=self)


class SetupChannelView(discord.ui.View):
    channel_keys = ("log_channel", "welcome_channel", "restock_channel", "ticket_category")

    def __init__(self):
        super().__init__(timeout=180)
        self.selected_key = "log_channel"

    async def interaction_check(self, interaction):
        if interaction.guild and isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            return True
        await interaction.response.send_message("Only an Admin can use this setup panel.", ephemeral=True)
        return False

    @discord.ui.select(placeholder="Choose which channel setting to edit", options=[
        discord.SelectOption(label="Audit and transcript logs", value="log_channel"),
        discord.SelectOption(label="Welcome messages", value="welcome_channel"),
        discord.SelectOption(label="Restock notices", value="restock_channel"),
        discord.SelectOption(label="Ticket category", value="ticket_category"),
    ])
    async def choose_setting(self, interaction, select):
        self.selected_key = select.values[0]
        await interaction.response.edit_message(content=f"Now choose a channel for **{self.selected_key.replace('_', ' ').title()}**.", view=self)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="Choose a channel or category", min_values=1, max_values=1,
                               channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.category])
    async def choose_channel(self, interaction, select):
        channel = select.values[0]
        wants_category = self.selected_key == "ticket_category"
        if wants_category != isinstance(channel, discord.CategoryChannel):
            expected = "a category" if wants_category else "a text channel"
            return await interaction.response.send_message(f"Choose {expected} for this setting.", ephemeral=True)
        setcfg(interaction.guild_id, self.selected_key, str(channel.id))
        await interaction.response.edit_message(content=f"✅ **{self.selected_key.replace('_', ' ').title()}** is now **{channel.name}**.", view=self)


class SetupWizardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    async def interaction_check(self, interaction):
        if interaction.guild and isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            return True
        await interaction.response.send_message("Only an Admin can use this setup panel.", ephemeral=True)
        return False

    @discord.ui.button(label="Configure Roles", emoji="👥", style=discord.ButtonStyle.primary)
    async def roles(self, interaction, button):
        await interaction.response.send_message("Select a role setting, then choose the role to save it.", view=SetupRoleView(), ephemeral=True)

    @discord.ui.button(label="Configure Channels", emoji="📍", style=discord.ButtonStyle.secondary)
    async def channels(self, interaction, button):
        await interaction.response.send_message("Select a channel setting, then choose the matching channel type.", view=SetupChannelView(), ephemeral=True)

    @discord.ui.button(label="Auto-set standard roles", emoji="✨", style=discord.ButtonStyle.success)
    async def auto_roles(self, interaction, button):
        created = await ensure_roles(interaction.guild)
        role_names = ("Admin", "Staff", "Seller", "Customer", "Member", "Verified")
        configured = []
        for name in role_names:
            role = discord.utils.get(interaction.guild.roles, name=name)
            if role:
                setcfg(interaction.guild_id, f"{name.lower()}_role", str(role.id))
                configured.append(name)
        summary = f"✅ Set up these roles: {', '.join(configured)}."
        if created:
            summary += f" Created: {', '.join(created)}."
        summary += " New members receive Member automatically. Configure channels with the other button. Check `$checkup` for hierarchy or permission issues."
        await interaction.response.send_message(summary, ephemeral=True)


class RestoreBackupView(discord.ui.View):
    def __init__(self, requester_id: int, filename: str):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.filename = filename

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the Admin who started this restore can confirm it.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("Admin permission is required to restore a backup.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Restore backup", emoji="♻️", style=discord.ButtonStyle.danger)
    async def confirm_restore(self, interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            safety_file = await asyncio.to_thread(restore_database_snapshot, self.filename)
        except (ValueError, OSError, sqlite3.Error) as exc:
            await interaction.followup.send(f"Restore stopped safely: {exc}", ephemeral=True)
        else:
            await log_to(interaction.guild, f"♻️ {interaction.user.mention} restored database backup `{self.filename}`. Safety copy: `{safety_file}`.")
            await interaction.followup.send(f"✅ Backup restored. A pre-restore safety copy was saved as `{safety_file}`.", ephemeral=True)
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_restore(self, interaction, button):
        await interaction.response.edit_message(content="Restore cancelled. The database was not changed.", embed=None, view=None)
        self.stop()


class Jarvis(commands.Bot):
    async def setup_hook(self):
        self.add_view(TicketControls())
        self.add_view(TicketPanelView())
        self.add_view(VerifyView())
        self.add_view(GiveawayView())
        self.backup_loop.start()
        self.giveaway_loop.start()
        self.presence_loop.start()
        self.product_sync_loop.start()

    @tasks.loop(hours=24)
    async def backup_loop(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = DATA / "backups" / f"jarvis-{stamp}.sqlite3"
        target.parent.mkdir(exist_ok=True)
        try:
            create_database_snapshot(target)
            files = sorted(target.parent.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in files[14:]: old.unlink()
        except (OSError, sqlite3.Error):
            log.exception("Database backup failed")
    @backup_loop.before_loop
    async def before_backup(self): await self.wait_until_ready()

    @tasks.loop(seconds=30)
    async def giveaway_loop(self):
        now = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            rows = db.execute("SELECT * FROM giveaways WHERE ended=0 AND ends<=?",(now,)).fetchall()
            for row in rows: db.execute("UPDATE giveaways SET ended=1 WHERE message=?",(row["message"],))
        for row in rows:
            channel = self.get_channel(row["channel"])
            if channel:
                with connect() as db:
                    entrants=db.execute("SELECT user FROM giveaway_entries WHERE message=?",(row["message"],)).fetchall()
                winner=random.choice(entrants)["user"] if entrants else None
                try: await channel.send(f"🎉 Giveaway **{row['prize']}** ended. Winner: {f'<@{winner}>' if winner else 'No eligible entrants.'}")
                except discord.HTTPException: pass
    @giveaway_loop.before_loop
    async def before_giveaways(self): await self.wait_until_ready()

    @tasks.loop(hours=12)
    async def presence_loop(self):
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.watching, name="you !!"),
        )

    @presence_loop.before_loop
    async def before_presence(self): await self.wait_until_ready()

    @tasks.loop(hours=6)
    async def product_sync_loop(self):
        for guild in self.guilds:
            if not cfg(guild.id, "sellauth_url") or not cfg(guild.id, "sellauth_key"):
                continue
            try:
                result = await sync_products_for_guild(guild)
                log.info("Automatic SellAuth sync in %s: %s", guild.id, result)
            except Exception:
                log.exception("Automatic SellAuth product sync failed for guild %s", guild.id)

    @product_sync_loop.before_loop
    async def before_product_sync(self):
        await self.wait_until_ready()
        await asyncio.sleep(300)


class JarvisContext(commands.Context):
    """Give command replies a uniform, readable embed presentation."""
    async def send(self, *args, **kwargs):
        content = kwargs.get("content")
        if content is None and args:
            content = args[0]

        embed = kwargs.get("embed")
        if isinstance(content, str) and content.strip() and embed is None:
            command_name = self.command.name if self.command else "help"
            styles = {
                "help": ("🤖", "JARVIS Command Guide", 0x5865F2),
                "shop": ("🛍️", "JARVIS AI", 0xD4AF37),
                "store": ("🔗", "Storefront", 0xD4AF37),
                "ticketpanel": ("🎫", "Ticket Panel", 0x5865F2),
                "config": ("⚙️", "Configuration", 0x5865F2),
                "setrole": ("👥", "Role Settings", 0x5865F2),
                "fill": ("🧩", "Standard Roles", 0xD4AF37),
                "setlog": ("🧾", "Log Settings", 0x5865F2),
                "setwelcome": ("👋", "Welcome Settings", 0x5865F2),
                "autorole": ("🪪", "Automatic Role", 0x2ECC71),
                "security": ("🛡️", "Security Settings", 0xE67E22),
                "addrole": ("✅", "Role Updated", 0x2ECC71),
                "removerole": ("✅", "Role Updated", 0x2ECC71),
                "syncproducts": ("🔄", "Product Sync", 0x5865F2),
                "profile": ("👤", "Customer Profile", 0x5865F2),
                "ordercreate": ("📦", "Order Recorded", 0x2ECC71),
                "verifycustomer": ("💎", "Customer Verified", 0x2ECC71),
                "myorders": ("📦", "Order History", 0x5865F2),
                "order": ("🔎", "Order Details", 0x5865F2),
                "vouch": ("⭐", "Vouch", 0xD4AF37),
                "vouches": ("⭐", "Vouches", 0xD4AF37),
                "dashboard": ("📊", "Shop Dashboard", 0x5865F2),
                "sellerstats": ("🧑‍💼", "Seller Statistics", 0x5865F2),
                "timeout": ("⏱️", "Moderation Action", 0xE67E22),
                "warn": ("⚠️", "Moderation Action", 0xE67E22),
                "warnings": ("📋", "Warning History", 0xE67E22),
                "purge": ("🧹", "Messages Cleared", 0xE67E22),
                "ban": ("🔨", "Moderation Action", 0xE74C3C),
                "kick": ("👢", "Moderation Action", 0xE67E22),
                "reactionrole": ("🎭", "Reaction Roles", 0x5865F2),
                "customadd": ("✨", "Custom Response", 0x2ECC71),
                "customremove": ("🗑️", "Custom Response", 0xE67E22),
                "setrestock": ("📦", "Stock Alerts", 0x5865F2),
                "lowstock": ("⚠️", "Stock Alerts", 0xE67E22),
                "restock": ("📦", "Stock Updated", 0x2ECC71),
                "giveaway": ("🎉", "Giveaway", 0xD4AF37),
                "giveawayend": ("🏆", "Giveaway Results", 0xD4AF37),
                "backup": ("💾", "Database Backup", 0x5865F2),
                "maintenance": ("🛠️", "Maintenance Mode", 0xE67E22),
                "setticketcategory": ("🎫", "Ticket Settings", 0x5865F2),
                "sales": ("💰", "Sales Report", 0x2ECC71),
                "ticketclaim": ("🙋", "Ticket Claim", 0x5865F2),
                "verifypanel": ("✅", "Verification Panel", 0x2ECC71),
                "setverifyrole": ("🛡️", "Verification Settings", 0x5865F2),
                "verificationlock": ("🔐", "Verification Lock", 0xE67E22),
                "verificationunlock": ("🔓", "Verification Lock", 0x2ECC71),
                "say": ("📣", "Announcement Sent", 0x2ECC71),
                "broadcast": ("📨", "Verified Members Update", 0x5865F2),
                "autotranslate": ("🌐", "Auto Translation", 0x5865F2),
                "find": ("🔎", "Product Finder", 0xD4AF37),
                "orderstatus": ("📦", "Order Status", 0x5865F2),
                "ticketadd": ("➕", "Ticket Access", 0x2ECC71),
                "ticketremove": ("➖", "Ticket Access", 0xE67E22),
                "suggest": ("💡", "Suggestion Posted", 0xD4AF37),
            }
            emoji, title, success_color = styles.get(command_name, ("✨", "JARVIS Update", 0x5865F2))
            if command_name == "config":
                if "saved" in content.lower():
                    title = "Setting Saved"
                else:
                    title = "Configuration Guide"
            elif command_name == "reactionrole":
                action = self.message.content.split()[1].lower() if len(self.message.content.split()) > 1 else ""
                lowered_content = content.lower()
                title = ("Reaction Role Added" if "react with" in lowered_content else "Reaction Role Setup") if action == "set" else ("Reaction Role Removed" if "removed" in lowered_content else "Reaction Role Setup")
            lowered = content.lower()
            failure_words = ("failed", "could not", "not found", "missing", "already ", "only ",
                             "cannot", "no eligible", "not configured", "unknown ")
            color = 0xE74C3C if any(word in lowered for word in failure_words) else success_color
            embed = discord.Embed(title=f"{emoji} {title}", description=content[:4096], color=color,
                                  timestamp=datetime.now(timezone.utc))
            embed.set_footer(text="JARVIS • JARVIS AI")
            kwargs["embed"] = embed
            if args:
                args = (None, *args[1:])
            else:
                kwargs["content"] = None
        elif isinstance(embed, discord.Embed):
            if embed.timestamp is None:
                embed.timestamp = datetime.now(timezone.utc)
            if not embed.footer.text:
                embed.set_footer(text="JARVIS • JARVIS AI")
            kwargs["embed"] = embed

        return await super().send(*args, **kwargs)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.invites = True
bot = Jarvis(command_prefix=PREFIX, intents=intents, help_command=None, context_class=JarvisContext)
invite_locks = {}


@bot.event
async def on_ready():
    log.info("JARVIS connected as %s", bot.user)
    for guild in bot.guilds:
        await cache_guild_invites(guild)
        await ensure_roles(guild)


@bot.event
async def on_invite_create(invite):
    if not invite.guild:
        return
    with connect() as db:
        db.execute(
            "INSERT INTO invite_snapshots(guild,code,inviter,uses) VALUES(?,?,?,?) "
            "ON CONFLICT(guild,code) DO UPDATE SET inviter=excluded.inviter,uses=excluded.uses",
            (invite.guild.id, invite.code, invite.inviter.id if invite.inviter else None, invite.uses or 0),
        )


@bot.event
async def on_member_join(member):
    invite_lock = invite_locks.setdefault(member.guild.id, asyncio.Lock())
    async with invite_lock:
        inviter_id, invite_code = await track_member_invite(member)
    if inviter_id:
        await log_to(member.guild, f"🔗 {member.mention} joined using invite `{invite_code}` created by <@{inviter_id}>.")
    elif not member.bot:
        await log_to(member.guild, f"🔗 {member.mention} joined, but JARVIS could not confidently identify the invite used.")
    role_id = cfg(member.guild.id, "member_role")
    role = member.guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(member.guild.roles,name="Member")
    if role:
        try: await member.add_roles(role, reason="Automatic JARVIS member role")
        except discord.HTTPException: log.exception("Could not add Member role")
    autorole_id = cfg(member.guild.id, "autorole_role")
    autorole = member.guild.get_role(int(autorole_id)) if autorole_id.isdigit() else None
    if autorole and autorole != role:
        try: await member.add_roles(autorole, reason="Configured JARVIS autorole")
        except discord.HTTPException: log.exception("Could not add configured autorole")
    channel_id = cfg(member.guild.id,"welcome_channel")
    channel = member.guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    if channel:
        welcome_text = cfg(member.guild.id,"welcome_text",f"Welcome {{user}} to **{{server}}**!").replace("{user}",member.mention).replace("{server}",member.guild.name)
        try:
            avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
            card_bytes = make_welcome_card(avatar_bytes, member.display_name, member.guild.member_count or 0)
            card_file = discord.File(io.BytesIO(card_bytes), filename="jarvis-welcome.png")
            embed = discord.Embed(description=welcome_text[:4096], color=0xD4AF37, timestamp=datetime.now(timezone.utc))
            embed.set_image(url="attachment://jarvis-welcome.png")
            embed.set_footer(text="JARVIS • JARVIS AI")
            await channel.send(content=member.mention, embed=embed, file=card_file)
        except Exception:
            log.exception("Personalized welcome card failed; sending text welcome")
            await channel.send(f"{member.mention} {welcome_text}")


@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id: return
    with connect() as db:
        row = db.execute("SELECT role FROM reaction_roles WHERE guild=? AND channel=? AND message=? AND emoji=?",(payload.guild_id,payload.channel_id,payload.message_id,str(payload.emoji))).fetchone()
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(row["role"]) if guild and row else None
    if member and role:
        try: await member.add_roles(role, reason="JARVIS reaction role")
        except discord.HTTPException: log.exception("Reaction role assignment failed")


@bot.event
async def on_raw_reaction_remove(payload):
    with connect() as db:
        row = db.execute("SELECT role FROM reaction_roles WHERE guild=? AND channel=? AND message=? AND emoji=?",(payload.guild_id,payload.channel_id,payload.message_id,str(payload.emoji))).fetchone()
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = guild.get_role(row["role"]) if guild and row else None
    if member and role:
        try: await member.remove_roles(role, reason="JARVIS reaction role removed")
        except discord.HTTPException: log.exception("Reaction role removal failed")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return await bot.process_commands(message)
    with connect() as db:
        db.execute("INSERT INTO profiles(guild,user,xp) VALUES(?,?,1) ON CONFLICT(guild,user) DO UPDATE SET xp=xp+1",(message.guild.id,message.author.id))
        row = db.execute("SELECT response FROM custom WHERE guild=? AND name=?",(message.guild.id,message.content.split()[0].lower() if message.content else "")).fetchone()
    if cfg(message.guild.id, "security_enabled", "on") == "on" and not is_admin(message.author):
        key = (message.guild.id, message.author.id)
        now = datetime.now(timezone.utc).timestamp()
        buckets = getattr(bot, "_security_buckets", {})
        bucket = buckets.setdefault(key, deque())
        bucket.append(now)
        while bucket and now - bucket[0] > 7:
            bucket.popleft()
        bot._security_buckets = buckets
        last_messages = getattr(bot, "_security_last_messages", {})
        previous = last_messages.get(key, ("", 0))
        repeated = bool(message.content and previous[0] == message.content and now - previous[1] < 5)
        last_messages[key] = (message.content, now)
        bot._security_last_messages = last_messages
        suspicious_invite = bool(re.search(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)", message.content, re.I))
        mass_ping = message.mention_everyone or len(message.mentions) >= 6
        message_flood = len(bucket) >= 7
        if suspicious_invite or mass_ping or message_flood or repeated:
            try:
                await message.delete()
            except discord.HTTPException:
                log.exception("Security filter could not delete a message")
            why = "invite link" if suspicious_invite else "mass mention" if mass_ping else "message flood" if message_flood else "duplicate message"
            await log_to(message.guild, f"🛡️ Security filter removed a {why} from {message.author.mention} in {message.channel.mention}.")
            return
    if row:
        await message.channel.send(row["response"][:1900])
    if bot.user and bot.user.mentioned_in(message) and not message.content.startswith(PREFIX):
        request = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if message.reference and request.lower().startswith("translate it"):
            language = request[len("translate it"):].strip() or "english"
            source = message.reference.resolved
            if not isinstance(source, discord.Message) and message.reference.message_id:
                try:
                    source = await message.channel.fetch_message(message.reference.message_id)
                except discord.HTTPException:
                    source = None
            if not isinstance(source, discord.Message) or not source.clean_content:
                await message.reply("Reply to the text, mention Jarvis, then write `translate it` for English or `translate it hindi` (or another target language).", mention_author=False)
                return
            async with message.channel.typing():
                translated = await translate_text(source.clean_content, language)
            embed = discord.Embed(title=f"🌐 Translation • {language.title()}", color=0x5865F2, timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Original", value=source.clean_content[:1024], inline=False)
            embed.add_field(name="Translation", value=translated[:1024], inline=False)
            embed.set_footer(text="Jarvis • Reply, mention me, then write: translate it [language]")
            await message.reply(embed=embed, mention_author=False)
        else:
            await message.reply("Reply to the message, mention Jarvis, then write `translate it` for English by default, or `translate it hindi` / another target language.", mention_author=False)
        return
    auto_language = cfg(message.guild.id, f"auto_translate:{message.channel.id}")
    if auto_language and not message.content.startswith(PREFIX):
        async with message.channel.typing():
            translated = await translate_text(message.clean_content, auto_language)
        if translated != message.clean_content:
            embed = discord.Embed(title=f"🌐 Auto Translation • {auto_language.title()}", description=translated[:4096], color=0x5865F2)
            embed.set_footer(text=f"Original message from {message.author.display_name}")
            await message.reply(embed=embed, mention_author=False)
    await bot.process_commands(message)


@bot.command(name="help")
async def help_cmd(ctx, category: str = ""):
    staff_view = has_role(ctx.author, "Staff")
    seller_view = has_role(ctx.author, "Seller")
    page = category.lower().strip()
    page = {"tickets": "ticket", "store": "shop", "orders": "order", "account": "member",
            "translate": "translation", "moderation": "staff", "seller": "ticket", "community": "social"}.get(page, page)

    if not page:
        embed = discord.Embed(
            title="JARVIS • Help Categories",
            description="Choose a category with `$help <category>`. Pages are filtered to your role. Admin-only setup is in `$helps`.",
            color=0xD4AF37,
        )
        embed.add_field(name="👥 Member pages", value="`member` • `shop` • `order` • `ticket` • `social` • `translation` • `verification`", inline=False)
        if seller_view:
            embed.add_field(name="🎫 Seller page", value="`ticket` includes your claim, unclaim, and close tools.", inline=False)
        if staff_view:
            embed.add_field(name="🛡️ Staff page", value="`staff` shows Staff tools. Use `$helps` for expanded role-filtered help.", inline=False)
        embed.set_footer(text="Admin-only commands are in $helps and visible to Admins only.")
        return await ctx.reply(embed=embed, mention_author=False)

    pages = {
        "member": ("👥 Member & Customer", "• `$help` — show this category menu\n• `$profile` — view your profile\n• `$myorders` — view your order history\n• `$order <id>` — view your own order\n• `$invites` — see joins credited to your invites\n• `$shop` / `$products` — browse the catalog\n• `$store` — get the storefront link\n• Use ticket, verification, and giveaway buttons posted in the server."),
        "shop": ("🛍️ Shop", "• `$shop` / `$products` — browse synced products and stock\n• `$store` — get the checkout storefront link\n• Purchases are completed on the storefront; a ticket contacts the shop team.\n• Admin catalog and stock controls are in `$helps shop`."),
        "order": ("📦 Orders & Profiles", "• `$myorders` — view your recorded orders\n• `$order <id>` — view your order; Staff/Admin can look up others\n• `$profile` — view your profile, spend, vouches, loyalty points, and level\n• Staff/Admin can use `$profile @member` to look up another profile."),
        "ticket": ("🎫 Tickets", "• Use the posted panel buttons: **Help**, **Purchase**, or **Questions**\n• One open ticket per member; tickets are private to you and the shop team\n• Use **Add Details** inside your ticket to send the product, order ID, and issue\n• Seller/Admin tools: `$ticketclaim`, `$ticketunclaim`, `$ticketclose` (buttons are also available)\n• Closing saves a transcript to the configured log channel, then deletes the ticket\n• Admin panel setup is in `$helps ticket`."),
        "social": ("✨ Community", "• `$vouch @seller <text>` — leave seller feedback\n• `$vouches [@seller]` — read recent vouches\n• `$invites` — see your invite-attributed joins\n• Giveaway entry uses the **Enter giveaway** button."),
        "translation": ("🌐 Translation", "• Reply to the message you want translated, mention Jarvis, then write `translate it` for English by default.\n• Example: reply to `hello bhai kesa he` and write `translate it` to request English.\n• Choose another target with `translate it hindi`, `translate it spanish`, or another language.\n• Admin channel-wide translation settings are in `$helps community`."),
        "verification": ("✅ Verification", "• Click **Verify** on the posted panel to receive the Verified role and unlock configured channels\n• This verifies you in the current server; it does not add you to another server or connect external apps\n• Admin panel and access settings are in `$helps verification`."),
    }
    if page == "staff":
        if not staff_view:
            return await ctx.reply("That help page is for Staff/Admin. Use `$help` for your available categories.", mention_author=False)
        title = "🛡️ Staff tools"
        text = "• `$timeout @member 10m [reason]` / `$to` — timeout up to 28 days\n• `$warn @member <reason>` — record a warning\n• `$warnings @member` — view warning history\n• `$purge <count>` — delete 1–100 recent messages\n• `$dashboard` — view shop and ticket stats\n• `$sales` — view recorded sales totals\n• `$sellerstats [@seller]` — view seller stats\n• `$invitetop` — view invite leaderboard\n• `$helps staff` — expanded Staff help; Admin commands are hidden from Staff."
    elif page in pages:
        title, text = pages[page]
        if page == "ticket" and not seller_view:
            text = text.replace("Seller/Admin tools: `$ticketclaim`, `$ticketunclaim`, `$ticketclose` (buttons are also available)", "Ticket management is reserved for Sellers/Admins (buttons are also available).")
        if page == "social" and staff_view:
            text += "\n• `$invites @member` — look up another member\n• `$invitetop` — leaderboard"
    else:
        return await ctx.reply("Unknown category. Try `$help member`, `$help shop`, `$help order`, `$help ticket`, `$help social`, `$help translation`, `$help verification`, or `$help staff`.", mention_author=False)
    embed = discord.Embed(title=f"JARVIS • {title}", description=text, color=0xD4AF37)
    embed.set_footer(text="Use $helps for Admin-only setup commands.")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="helps")
async def helps_cmd(ctx, category: str = ""):
    if not await require_staff(ctx): return
    admin_view = is_admin(ctx.author)
    seller_view = has_role(ctx.author, "Seller")
    staff_text = (
        "• `$timeout @member 10m [reason]` / `$to` — timeout up to 28 days\n"
        "• `$warn @member <reason>` — record a warning\n"
        "• `$warnings @member` — view warning history\n"
        "• `$purge <count>` — delete 1–100 recent messages\n"
        "• `$dashboard` — view shop and ticket stats\n"
        "• `$sales` — view recorded sales totals\n"
        "• `$sellerstats [@seller]` — view seller stats\n"
        "• `$invitetop` — view invite leaderboard\n"
        "• `$order <id>` / `$profile @member` — look up other members' order/profile info"
    )
    admin_pages = {
        "settings": ("⚙️ Settings & operations", "• `$setup` — open the guided role/channel setup panel\n• `$checkup` — check bot permissions, role mappings, and configured channels\n• `$config` — show configuration guide; `$config <key> <value>` saves store settings\n• `$setlog #channel` — set audit/transcript logs\n• `$maintenance on|off` — pause/resume ticket opening\n• `$backup` — create and send a consistent database snapshot\n• `$backuplist` — list saved database backups\n• `$restorebackup <filename>` — restore after a confirmation; a safety copy is made first"),
        "shop": ("🛍️ Shop & orders", "• `$config store_url <url>` — set storefront\n• `$config sellauth_url <url>` / `$config sellauth_key <key>` — catalog credentials\n• `$syncproducts` — sync now; after credentials are saved, JARVIS checks the catalog every 6 hours\n• `$restock <product-id> <stock>` — change stock and post card\n• `$setrestock #channel` — set stock-alert channel\n• `$lowstock <threshold>` — set low-stock level\n• `$ordercreate @member <product-id> <qty> <amount>` — record verified sale\n• `$verifycustomer @member` — assign Customer role after manual check"),
        "ticket": ("🎫 Ticket setup", "• `$ticketpanel` — post Help/Purchase/Questions buttons\n• `$setticketcategory <category>` — choose new-ticket category\n• New tickets include an opener-only product/order details form\n• Sellers/Admins can also use `$ticketclaim`, `$ticketunclaim`, `$ticketclose` inside an open ticket\n• Closing saves a transcript in the configured log channel, then deletes the ticket"),
        "roles": ("👥 Roles & reaction roles", "• `$fill` — create missing standard roles\n• `$setrole <admin/staff/seller/customer/member> @role` — map a role\n• `$addrole @member @role` / `$removerole @member @role` — manage roles\n• `$reactionrole set #channel <message-id> <emoji> @role`\n• `$reactionrole remove #channel <message-id> <emoji>`"),
        "verification": ("✅ Verification & access", "• `$setverifyrole @role` — select Verified role\n• `$verifypanel` — post Verify button\n• `$verificationlock` — save and restrict channel visibility\n• `$verificationunlock` — restore saved visibility"),
        "welcome": ("👋 Welcome & autorole", "• `$setwelcome #channel <message>` — choose channel/text; supports `{user}` and `{server}`\n• `$autorole @role` — add an extra role on join\n• `$autorole off` — disable extra role; Member role still auto-assigns"),
        "moderation": ("🔨 Admin moderation", "• `$ban @member [reason]` — ban\n• `$kick @member [reason]` — kick\nStaff moderation commands are listed under `$helps staff`."),
        "security": ("🛡️ Security", "• `$security` — view filter status\n• `$security on|off` — toggle invite-link, mass-mention, duplicate-message, and flood filtering"),
        "community": ("🎉 Community & custom replies", "• `$giveaway <minutes> <prize>` — start giveaway\n• `$giveawayend <message-id>` — end early and draw\n• `$customadd <word> <response>` — add single-word auto-reply trigger\n• `$customremove <word>` — remove trigger\n• `$autotranslate on <language>` / `$autotranslate off` — channel translation\nMembers trigger a custom reply by starting a message with its saved word."),
        "announcements": ("📣 Announcements", "• `$say <text>` — delete command message if possible, then post plain text\n• `$broadcast <text>` — DM the update to Verified members"),
    }

    if category:
        page = {"tickets": "ticket", "orders": "shop", "announce": "announcements", "verify": "verification"}.get(category.lower(), category.lower())
        if page not in admin_pages:
            if page == "staff":
                page = "staff"
            else:
                return await ctx.reply("Try `$helps staff` or an Admin category: `settings`, `shop`, `ticket`, `roles`, `verification`, `welcome`, `moderation`, `security`, `community`, `announcements`.", mention_author=False)
        if page == "staff":
            title, value = "🛡️ Staff tools", staff_text
        else:
            if not admin_view:
                return await ctx.reply("That category is Admin-only. Use `$helps staff` for your available commands.", mention_author=False)
            title, value = admin_pages[page]
        embed = discord.Embed(title=f"JARVIS • {title}", description=value, color=0xD4AF37)
        embed.set_footer(text="Command access follows your Discord role and bot permissions.")
        return await ctx.reply(embed=embed, mention_author=False)

    embed = discord.Embed(
        title="JARVIS • Staff Commands" if not admin_view else "JARVIS • Staff & Admin Commands",
        description="Help is grouped by category. `$helps <category>` opens one page; Admin pages are hidden from Staff.",
        color=0xD4AF37,
    )
    embed.add_field(name="🛡️ Staff", value=staff_text, inline=False)
    if seller_view:
        embed.add_field(name="🎫 Seller ticket controls", value="• `$ticketclaim` — claim ticket\n• `$ticketunclaim` — release your claim\n• `$ticketclose` — save transcript and close", inline=False)
    if admin_view:
        for key in ("settings", "shop", "ticket", "roles", "verification", "welcome", "moderation", "security", "community", "announcements"):
            title, value = admin_pages[key]
            embed.add_field(name=title, value=value, inline=False)
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="invites")
async def invites_cmd(ctx, member: Optional[discord.Member] = None):
    target = member or ctx.author
    if target.id != ctx.author.id and not has_role(ctx.author, "Staff"):
        return await ctx.reply("You can check your own invite joins. Staff/Admin can check another member.")
    with connect() as db:
        credited = db.execute(
            "SELECT COUNT(*) AS n FROM invite_joins WHERE guild=? AND inviter=?",
            (ctx.guild.id, target.id),
        ).fetchone()["n"]
    embed = discord.Embed(
        title=f"🔗 Invite Stats • {target.display_name}",
        description=f"**{credited}** join(s) have been credited to {target.mention} since JARVIS invite tracking was enabled.",
        color=0xD4AF37,
    )
    embed.set_footer(text="Invite tracking requires Manage Server permission for JARVIS.")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="invitetop")
async def invitetop(ctx):
    if not await require_staff(ctx): return
    with connect() as db:
        rows = db.execute(
            "SELECT inviter,COUNT(*) AS joins FROM invite_joins "
            "WHERE guild=? AND inviter IS NOT NULL GROUP BY inviter ORDER BY joins DESC,inviter LIMIT 10",
            (ctx.guild.id,),
        ).fetchall()
    lines = [f"**{index}.** <@{row['inviter']}> — **{row['joins']}** join(s)" for index, row in enumerate(rows, 1)]
    embed = discord.Embed(
        title="🏆 Invite Leaderboard",
        description="\n".join(lines) if lines else "No invite-attributed joins have been recorded yet.",
        color=0xD4AF37,
    )
    embed.set_footer(text="Only confidently attributed joins appear here.")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="say")
async def say(ctx, *, text: str):
    if not await require_admin(ctx): return
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    allowed_mentions = discord.AllowedMentions(everyone=True, users=True, roles=True, replied_user=False)
    await ctx.channel.send(text[:2000], allowed_mentions=allowed_mentions)


@bot.command(name="broadcast")
async def broadcast(ctx, *, text: str):
    if not await require_admin(ctx): return
    verified_id = cfg(ctx.guild.id, "verified_role")
    verified_role = ctx.guild.get_role(int(verified_id)) if verified_id.isdigit() else discord.utils.get(ctx.guild.roles, name="Verified")
    if verified_role is None:
        return await ctx.reply("The Verified role is not configured. Set it with `$setverifyrole @Verified` first.")
    try:
        members = [member async for member in ctx.guild.fetch_members(limit=None)
                   if not member.bot and verified_role in member.roles]
    except discord.Forbidden:
        return await ctx.reply("I can't read the server member list. Enable Server Members Intent for the bot in the Discord Developer Portal, then restart it.")
    if not members:
        return await ctx.reply("No verified members were found to message.")
    embed = discord.Embed(title="📣 Server Update", description=text[:4096], color=0xD4AF37, timestamp=datetime.now(timezone.utc))
    embed.set_footer(text="JARVIS • Verified member update")
    sent = failed = 0
    status = await ctx.reply(f"📨 Sending this update to {len(members)} verified member(s)…")
    for member in members:
        try:
            await member.send(embed=embed)
            sent += 1
        except discord.HTTPException:
            failed += 1
    await status.edit(content=f"✅ Update finished. Delivered to {sent} verified member(s); {failed} couldn't receive DMs (privacy settings or unavailable account).")


@bot.command(name="autotranslate")
async def autotranslate(ctx, state: str, *, language: str = "english"):
    if not await require_admin(ctx): return
    key = f"auto_translate:{ctx.channel.id}"
    if state.lower() == "on":
        setcfg(ctx.guild.id, key, language.strip() or "english")
        await ctx.reply(f"Auto translation is on in {ctx.channel.mention}. New messages will be translated to {language.title()}.")
    elif state.lower() == "off":
        setcfg(ctx.guild.id, key, "")
        await ctx.reply(f"Auto translation is off in {ctx.channel.mention}.")
    else:
        await ctx.reply("Use `$autotranslate on [language]` or `$autotranslate off`.")


@bot.command(name="store")
async def store(ctx): await ctx.reply(cfg(ctx.guild.id,"store_url","Store URL not configured. Admin: $config"),mention_author=False)

@bot.command(name="config")
async def config_cmd(ctx, key: str=""):
    if not await require_admin(ctx): return
    if not key:
        embed = discord.Embed(
            title="⚙️ JARVIS Configuration",
            description="Set server options with `$config <key> <value>`. Use the dedicated role and channel commands where listed.",
            color=0x5865F2,
        )
        embed.add_field(name="🛍️ Store", value="`store_url` — public JARVIS AI link\n`sellauth_url` — SellAuth catalog API base URL\n`sellauth_key` — SellAuth API key (enter only in a private Admin channel)", inline=False)
        embed.add_field(name="👥 Roles", value="Use `$setrole admin @role`, `$setrole staff @role`, `$setrole seller @role`, `$setrole customer @role`, or `$setrole member @role`.", inline=False)
        embed.add_field(name="📣 Channels & messages", value="Use `$setlog #channel`, `$setwelcome #channel <message>`, `$autorole @role` (or `$autorole off`), `$setrestock #channel`, and `$setticketcategory <category>`.", inline=False)
        embed.add_field(name="📦 Stock & operation", value="`low_stock_threshold` — numeric low-stock alert level\n`maintenance` — set with `$maintenance on|off`\nWelcome text supports `{user}` and `{server}`.", inline=False)
        await ctx.reply(embed=embed,mention_author=False)
        return
    parts=ctx.message.content.split(maxsplit=2)
    if len(parts)<3: return await ctx.reply("Provide a value: `$config store_url https://…`",mention_author=False)
    if key.lower() in {"sellauth_key", "discord_bot_token"}:
        default_role=ctx.guild.default_role
        bot_member=ctx.guild.me
        if ctx.channel.permissions_for(default_role).view_channel is not False:
            return await ctx.reply("For safety, enter secret settings only in a private Admin-only channel.",mention_author=False)
        if not bot_member or not ctx.channel.permissions_for(bot_member).manage_messages:
            return await ctx.reply("JARVIS needs Manage Messages here so it can remove the secret command after saving.",mention_author=False)
        setcfg(ctx.guild.id,key,parts[2])
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            return await ctx.reply("The setting was saved, but I could not remove the secret message. Delete it manually now.",mention_author=False)
        return await ctx.send(f"Saved `{key}` and removed the command message.")
    setcfg(ctx.guild.id,key,parts[2]); await ctx.reply(f"Saved `{key}`.",mention_author=False)

@bot.command(name="setrole")
async def setrole(ctx, key: str, role: discord.Role):
    if not await require_admin(ctx): return
    if key.lower() not in {"admin","staff","seller","customer","member"}: return await ctx.reply("Role key must be admin/staff/seller/customer/member.")
    setcfg(ctx.guild.id,key.lower()+"_role",str(role.id)); await ctx.reply(f"{key.title()} role set to {role.mention}.")


@bot.command(name="fill")
async def fill_roles(ctx):
    if not await require_admin(ctx): return
    before = {role.name.lower() for role in ctx.guild.roles}
    await ensure_roles(ctx.guild)
    after = {role.name.lower() for role in ctx.guild.roles}
    added = [name.title() for name in ("admin", "staff", "seller", "customer", "member", "verified") if name not in before and name in after]
    missing = [name.title() for name in ("admin", "staff", "seller", "customer", "member", "verified") if name not in after]
    if missing:
        return await ctx.reply(
            f"I couldn't create these roles: {', '.join(missing)}. Give JARVIS **Manage Roles** and move its bot role high enough in Server Settings > Roles, then run `$fill` again."
        )
    if added:
        await ctx.reply(f"✅ Created the missing standard roles: {', '.join(added)}. Set each role's members and permissions in Discord as needed.")
    else:
        await ctx.reply("✅ All standard roles already exist: Admin, Staff, Seller, Customer, Member, and Verified.")

@bot.command(name="setverifyrole")
async def setverifyrole(ctx, role: discord.Role):
    if not await require_admin(ctx): return
    setcfg(ctx.guild.id, "verified_role", str(role.id))
    await ctx.reply(f"Members will receive {role.mention} when they use the Verify button.")


@bot.command(name="verificationlock")
async def verificationlock(ctx):
    """Hide channels from unverified members while retaining a recoverable permission snapshot."""
    if not await require_admin(ctx): return
    role_id = cfg(ctx.guild.id, "verified_role")
    verified_role = ctx.guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(ctx.guild.roles, name="Verified")
    if verified_role is None:
        return await ctx.reply("Set the Verified role first with `$setverifyrole @Verified`.")
    changed = failed = 0
    for channel in ctx.guild.channels:
        current = channel.overwrites
        stored = {}
        for target, overwrite in current.items():
            target_type = "role" if isinstance(target, discord.Role) else "member"
            if isinstance(target, discord.Role) and target.is_default():
                target_type = "role"
            stored[(target_type, target.id)] = overwrite.view_channel
        stored[("role", ctx.guild.default_role.id)] = current.get(ctx.guild.default_role, discord.PermissionOverwrite()).view_channel
        stored[("role", verified_role.id)] = current.get(verified_role, discord.PermissionOverwrite()).view_channel
        with connect() as db:
            already = db.execute("SELECT 1 FROM verification_overwrites WHERE guild=? AND channel=? LIMIT 1", (ctx.guild.id,channel.id)).fetchone()
            if not already:
                for (target_type, target_id), view_value in stored.items():
                    db.execute("INSERT OR REPLACE INTO verification_overwrites VALUES(?,?,?,?,?)",(ctx.guild.id,channel.id,target_type,target_id,None if view_value is None else int(view_value)))
        overwrites = dict(current)
        for target in list(overwrites):
            overwrite = overwrites[target]
            if isinstance(target, discord.Member) and target.id == bot.user.id:
                overwrite.view_channel = True
            elif isinstance(target, discord.Role) and target.id == verified_role.id:
                overwrite.view_channel = True
            else:
                overwrite.view_channel = False
            overwrites[target] = overwrite
        for target, allowed in ((ctx.guild.default_role, False), (verified_role, True)):
            overwrite = overwrites.get(target, discord.PermissionOverwrite())
            overwrite.view_channel = allowed
            overwrites[target] = overwrite
        if ctx.guild.me:
            overwrite = overwrites.get(ctx.guild.me, discord.PermissionOverwrite())
            overwrite.view_channel = True
            overwrites[ctx.guild.me] = overwrite
        try:
            await channel.edit(overwrites=overwrites, reason=f"Verification gate enabled by {ctx.author}")
            changed += 1
        except discord.Forbidden:
            failed += 1
        except discord.HTTPException:
            failed += 1
    await ctx.reply(f"🔐 Verification lock applied to {changed} channel(s). {failed} could not be updated. Use `$verificationunlock` to restore saved view permissions.")


@bot.command(name="verificationunlock")
async def verificationunlock(ctx):
    if not await require_admin(ctx): return
    with connect() as db:
        snapshots = db.execute("SELECT * FROM verification_overwrites WHERE guild=?", (ctx.guild.id,)).fetchall()
    if not snapshots:
        return await ctx.reply("No saved verification-lock permissions were found for this server.")
    grouped = {}
    for row in snapshots:
        grouped.setdefault(row["channel"], []).append(row)
    restored = failed = 0
    for channel_id, rows in grouped.items():
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            continue
        overwrites = dict(channel.overwrites)
        for row in rows:
            target = ctx.guild.get_role(row["target"]) if row["target_type"] == "role" else ctx.guild.get_member(row["target"])
            if target is None:
                continue
            overwrite = overwrites.get(target, discord.PermissionOverwrite())
            overwrite.view_channel = None if row["view_value"] is None else bool(row["view_value"])
            if overwrite.is_empty():
                overwrites.pop(target, None)
            else:
                overwrites[target] = overwrite
        try:
            await channel.edit(overwrites=overwrites, reason=f"Verification lock removed by {ctx.author}")
            restored += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1
    if failed == 0:
        with connect() as db:
            db.execute("DELETE FROM verification_overwrites WHERE guild=?", (ctx.guild.id,))
    await ctx.reply(f"🔓 Restored saved permissions for {restored} channel(s). {failed} could not be restored." + (" The snapshot is kept so you can retry." if failed else ""))

@bot.command(name="setlog")
async def setlog(ctx, channel: discord.TextChannel):
    if await require_admin(ctx): setcfg(ctx.guild.id,"log_channel",str(channel.id)); await ctx.reply(f"Audit and ticket logs go to {channel.mention}.")

@bot.command(name="setwelcome")
async def setwelcome(ctx, channel: discord.TextChannel, *, message="Welcome {user} to {server}!"):
    if not await require_admin(ctx): return
    setcfg(ctx.guild.id,"welcome_channel",str(channel.id)); setcfg(ctx.guild.id,"welcome_text",message)
    await ctx.reply("Welcome message saved. Use `{user}` and `{server}` placeholders.")

@bot.command(name="autorole")
async def autorole(ctx, *, role_input: str = ""):
    if not await require_admin(ctx): return
    if role_input.strip().lower() == "off":
        setcfg(ctx.guild.id, "autorole_role", "")
        return await ctx.reply("🪪 Extra join role disabled. The configured Member role is still assigned automatically.")
    if not role_input.strip():
        current_id = cfg(ctx.guild.id, "autorole_role")
        current = ctx.guild.get_role(int(current_id)) if current_id.isdigit() else None
        if current:
            return await ctx.reply(f"🪪 New members receive {current.mention} as an extra autorole, plus the Member role.")
        return await ctx.reply("🪪 No extra autorole is set. Use `$autorole @role` or `$autorole off`. The Member role is still assigned automatically.")
    try:
        role = await commands.RoleConverter().convert(ctx, role_input)
    except commands.BadArgument:
        return await ctx.reply("I couldn't find that role. Mention a role: `$autorole @role`, or use `$autorole off`.")
    if role.is_default() or role.managed:
        return await ctx.reply("Choose a regular server role that JARVIS can manage.")
    setcfg(ctx.guild.id, "autorole_role", str(role.id))
    await ctx.reply(f"🪪 Autorole enabled: new members will receive {role.mention} in addition to the Member role.")

@bot.command(name="addrole")
async def addrole(ctx, member: discord.Member, role: discord.Role):
    if not await require_admin(ctx): return
    await member.add_roles(role,reason=f"By {ctx.author}"); await ctx.reply(f"Added {role.mention} to {member.mention}.")

@bot.command(name="removerole")
async def removerole(ctx, member: discord.Member, role: discord.Role):
    if not await require_admin(ctx): return
    await member.remove_roles(role,reason=f"By {ctx.author}"); await ctx.reply(f"Removed {role.mention} from {member.mention}.")

@bot.command(name="ticketpanel")
async def ticketpanel(ctx):
    if not await require_admin(ctx): return
    embed = discord.Embed(
        title="🎟️ JARVIS AI • Ticket Panel",
        description=(
            "Choose an option below to open a ticket.\n\n"
            "🆘 **Help** — Get help with an issue or an existing order.\n"
            "🛒 **Purchase** — Open a purchase ticket to discuss an item with the shop team.\n"
            "❓ **Questions** — Ask general questions before purchasing.\n\n"
            "**Purchase Policy**\n"
            "• Payments are only via PayPal or LTC.\n"
            "• If you send money to the wrong PayPal address, no refund will be provided.\n"
            "• Payments must be sent in $ (USD) or € (EUR).\n"
            "• If a Nitro or any other item gets revoked, no refund or replacement will be given.\n"
            "• Buying SellAuth and getting Nitro claimed without screen recording = no replacement/refund.\n"
            "• Advertising in slots/channels = instant ban.\n"
            "• Accusing us of scamming = instant ban.\n"
            "• No refund is possible for any purchase."
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="JARVIS AI • Please read the policy before purchasing")
    await ctx.send(embed=embed, view=TicketPanelView())

@bot.command(name="verifypanel")
async def verifypanel(ctx):
    if not await require_admin(ctx): return
    role_id = cfg(ctx.guild.id, "verified_role")
    role = ctx.guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(ctx.guild.roles, name="Verified")
    if role is None:
        return await ctx.reply("Create a Verified role, then set it with `$setverifyrole @Verified` before posting the panel.")
    embed = discord.Embed(
        title="✅ Jarvis • Server Verification",
        description=(
            "Click **Verify** below to unlock the server.\n\n"
            "You will receive the verified-member role and access to the server channels. "
            "Jarvis will never ask for your Discord password or token."
        ),
        color=0x2ECC71,
    )
    embed.set_footer(text="Jarvis • Secure server access")
    await ctx.send(embed=embed, view=VerifyView())

@bot.command(name="shop", aliases=["products"])
async def shop(ctx):
    with connect() as db: rows=db.execute("SELECT * FROM products WHERE guild=? ORDER BY name LIMIT 25",(ctx.guild.id,)).fetchall()
    e=discord.Embed(title="JARVIS AI • Products",color=0x2ECC71)
    if not rows: e.description="No products are synced. Admin: configure SellAuth and use `$syncproducts`."
    for p in rows: e.add_field(name=p["name"],value=f"Price: {p['price']}\nStock: {p['stock']}\nID: `{p['id']}`",inline=True)
    e.add_field(name="Checkout",value=f"Purchase through [JARVIS AI]({cfg(ctx.guild.id,'store_url','https://example.com')})",inline=False)
    await ctx.reply(embed=e,mention_author=False)

@bot.command(name="syncproducts")
async def syncproducts(ctx):
    if not await require_admin(ctx): return
    if not cfg(ctx.guild.id,"sellauth_url") or not cfg(ctx.guild.id,"sellauth_key"):
        return await ctx.reply("Configure `sellauth_url` and `sellauth_key` via `$config` first.")
    try:
        result = await sync_products_for_guild(ctx.guild, fallback_channel=ctx.channel)
        await ctx.reply(f"🔄 {result} Checkout links are handled by the storefront; JARVIS does not call SellAuth Checkout API.")
    except Exception as exc:
        log.exception("SellAuth sync failed"); await ctx.reply(f"Product sync failed: {str(exc)[:300]}")


async def sync_products_for_guild(guild, fallback_channel=None):
    """Fetch catalog data and publish stock changes; used by manual and scheduled syncs."""
    import aiohttp
    base, key = cfg(guild.id, "sellauth_url"), cfg(guild.id, "sellauth_key")
    if not base or not key:
        raise ValueError("SellAuth URL or API key is not configured")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(base.rstrip("/") + "/v1/products", timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"SellAuth product request failed (HTTP {resp.status}); check URL, API key, and product-read access")
            data = await resp.json()
    products = data.get("data", data if isinstance(data, list) else [])
    count, stock_events, low_alerts = 0, [], []
    threshold = cfg(guild.id, "low_stock_threshold", "3")
    with connect() as db:
        for item in products:
            pid = str(item.get("id") or item.get("product_id") or "")
            if not pid:
                continue
            name = str(item.get("name") or item.get("title") or pid)
            price = str(item.get("price", item.get("amount", "Contact seller")))
            stock = str(item.get("stock", item.get("quantity", "Unknown")))
            previous = db.execute("SELECT stock FROM products WHERE guild=? AND id=?", (guild.id, pid)).fetchone()
            db.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild,id) DO UPDATE SET name=excluded.name,price=excluded.price,stock=excluded.stock,updated=excluded.updated",
                       (guild.id, pid, name, price, stock, str(item.get("url", "")), datetime.now(timezone.utc).isoformat()))
            count += 1
            if not previous or previous["stock"] != stock:
                stock_events.append((name, stock, stock_change_status(previous["stock"] if previous else None, stock)))
            try:
                if int(stock) <= int(threshold) and (not previous or previous["stock"] != stock):
                    low_alerts.append((name, stock))
            except (TypeError, ValueError):
                pass
    alert_channel_id = cfg(guild.id, "restock_channel")
    alert_channel = guild.get_channel(int(alert_channel_id)) if alert_channel_id.isdigit() else None
    alert_channel = alert_channel or fallback_channel
    if alert_channel:
        for name, stock, status in stock_events:
            await send_stock_notification(alert_channel, name, stock, status)
        for name, stock in low_alerts:
            low_embed = discord.Embed(title="⚠️ Low stock", description=f"**{discord.utils.escape_mentions(name)}** has **{stock} units** left (threshold: {threshold}).", color=0xE67E22)
            await alert_channel.send(embed=low_embed, allowed_mentions=discord.AllowedMentions.none())
    return f"Synced **{count}** products. " + (f"Published {len(stock_events)} stock update(s) and {len(low_alerts)} low-stock alert(s)." if stock_events or low_alerts else "No stock changes were detected.")

@bot.command(name="profile")
async def profile(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    if member.id != ctx.author.id and not has_role(ctx.author, "Staff"):
        return await ctx.reply("You can view your own profile. Staff/Admin can view another member's profile.", mention_author=False)
    with connect() as db:
        p=db.execute("SELECT * FROM profiles WHERE guild=? AND user=?",(ctx.guild.id,member.id)).fetchone()
        orders=db.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) total FROM orders WHERE guild=? AND user=? AND status='complete'",(ctx.guild.id,member.id)).fetchone()
        vc=db.execute("SELECT COUNT(*) n FROM vouches WHERE guild=? AND seller=?",(ctx.guild.id,member.id)).fetchone()["n"]
    e=discord.Embed(title=f"{member.display_name} • Customer profile",color=member.color)
    e.add_field(name="Orders",value=orders["n"]); e.add_field(name="Verified spend",value=f"{orders['total']:.2f}"); e.add_field(name="Vouches",value=str(vc))
    e.add_field(name="Loyalty",value=f"{p['points'] if p else 0} points • Level {(p['xp']//100+1) if p else 1}")
    await ctx.reply(embed=e,mention_author=False)

@bot.command(name="ordercreate")
async def ordercreate(ctx, member: discord.Member, product_id: str, quantity: int, amount: float):
    if not await require_admin(ctx): return
    if quantity<1 or amount<0: return await ctx.reply("Quantity must be positive and amount nonnegative.")
    with connect() as db:
        product=db.execute("SELECT name FROM products WHERE guild=? AND id=?",(ctx.guild.id,product_id)).fetchone()
        if not product: return await ctx.reply("Unknown product ID. Sync products first.")
        oid="JARVIS-"+"".join(random.choices(string.digits,k=6))
        db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",(oid,ctx.guild.id,member.id,product["name"],quantity,amount,ctx.author.id,"complete",datetime.now(timezone.utc).isoformat()))
        db.execute("INSERT INTO profiles(guild,user,points) VALUES(?,?,?) ON CONFLICT(guild,user) DO UPDATE SET points=points+excluded.points",(ctx.guild.id,member.id,max(1,int(amount))))
    await ctx.reply(f"Recorded verified order `{oid}` for {member.mention}. Customer role is assigned only after this admin-confirmed record.")
    role_id=cfg(ctx.guild.id,"customer_role"); role=ctx.guild.get_role(int(role_id)) if role_id.isdigit() else None
    if role:
        try: await member.add_roles(role,reason=f"Admin-verified purchase {oid}")
        except discord.HTTPException: pass
    await log_to(ctx.guild,f"📦 Verified order {oid}: {member.mention} • {product['name']} ×{quantity} • {amount:.2f}")

@bot.command(name="verifycustomer")
async def verifycustomer(ctx, member: discord.Member):
    if not await require_admin(ctx): return
    role_id=cfg(ctx.guild.id,"customer_role"); role=ctx.guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(ctx.guild.roles,name="Customer")
    if not role: return await ctx.reply("Set the Customer role first with `$setrole customer @role`.")
    await member.add_roles(role,reason=f"Purchase manually verified by {ctx.author}"); await log_to(ctx.guild,f"✅ Customer role manually verified for {member.mention} by {ctx.author.mention}"); await ctx.reply(f"Verified {member.mention} as a customer.")

@bot.command(name="myorders")
async def myorders(ctx):
    with connect() as db: rows=db.execute("SELECT * FROM orders WHERE guild=? AND user=? ORDER BY created DESC LIMIT 10",(ctx.guild.id,ctx.author.id)).fetchall()
    if not rows: return await ctx.reply("No verified orders found.",mention_author=False)
    await ctx.reply("\n".join(f"`{r['id']}` • {r['product']} ×{r['qty']} • {r['amount']:.2f} • {r['status']}" for r in rows),mention_author=False)

@bot.command(name="order")
async def order(ctx, order_id: str):
    with connect() as db: r=db.execute("SELECT * FROM orders WHERE id=? AND guild=?",(order_id,ctx.guild.id)).fetchone()
    if not r: return await ctx.reply("Order not found.")
    if r["user"]!=ctx.author.id and not has_role(ctx.author,"Staff"): return await ctx.reply("You can only view your own order. Staff/Admin can look up other orders.")
    await ctx.reply(f"**{r['id']}** • <@{r['user']}> • {r['product']} ×{r['qty']} • {r['amount']:.2f} • {r['status']} • Seller <@{r['seller']}>")

@bot.command(name="vouch")
async def vouch(ctx, seller: discord.Member, *, text):
    if seller.id==ctx.author.id or seller.bot: return await ctx.reply("You cannot vouch for yourself or a bot.")
    with connect() as db: db.execute("INSERT INTO vouches(guild,author,seller,text,created) VALUES(?,?,?,?,?)",(ctx.guild.id,ctx.author.id,seller.id,text[:1000],datetime.now(timezone.utc).isoformat()))
    await ctx.reply("Vouch recorded. Thank you!"); await log_to(ctx.guild,f"⭐ Vouch by {ctx.author.mention} for {seller.mention}: {text[:500]}")

@bot.command(name="vouches")
async def vouches(ctx, seller: Optional[discord.Member]=None):
    seller=seller or ctx.author
    with connect() as db: rows=db.execute("SELECT * FROM vouches WHERE guild=? AND seller=? ORDER BY id DESC LIMIT 10",(ctx.guild.id,seller.id)).fetchall()
    if not rows: return await ctx.reply("No vouches yet.")
    await ctx.reply("\n".join(f"⭐ <@{r['author']}>: {r['text'][:250]}" for r in rows))

@bot.command(name="dashboard")
async def dashboard(ctx):
    if not await require_staff(ctx): return
    with connect() as db:
        orders=db.execute("SELECT COUNT(*) n,COALESCE(SUM(amount),0) s FROM orders WHERE guild=? AND status='complete'",(ctx.guild.id,)).fetchone()
        tickets=db.execute("SELECT COUNT(*) n FROM tickets WHERE guild=? AND status='open'",(ctx.guild.id,)).fetchone()["n"]
        customers=db.execute("SELECT COUNT(DISTINCT user) n FROM orders WHERE guild=?",(ctx.guild.id,)).fetchone()["n"]
        stock=db.execute("SELECT COUNT(*) n FROM products WHERE guild=?",(ctx.guild.id,)).fetchone()["n"]
        claims=db.execute("SELECT seller,count FROM claims WHERE guild=? ORDER BY count DESC LIMIT 5",(ctx.guild.id,)).fetchall()
    e=discord.Embed(title="JARVIS • JARVIS AI dashboard",color=0x5865F2)
    e.add_field(name="Verified orders",value=str(orders["n"])); e.add_field(name="Recorded revenue",value=f"{orders['s']:.2f}"); e.add_field(name="Open tickets",value=str(tickets)); e.add_field(name="Customers",value=str(customers)); e.add_field(name="Products synced",value=str(stock))
    e.add_field(name="Seller ticket claims",value="\n".join(f"<@{r['seller']}> — {r['count']}" for r in claims) or "No claims yet",inline=False)
    await ctx.reply(embed=e,mention_author=False)

@bot.command(name="sellerstats")
async def sellerstats(ctx, member: Optional[discord.Member]=None):
    member=member or ctx.author
    if member.id!=ctx.author.id and not await require_staff(ctx): return
    with connect() as db:
        c=db.execute("SELECT count FROM claims WHERE guild=? AND seller=?",(ctx.guild.id,member.id)).fetchone()
        o=db.execute("SELECT COUNT(*) n FROM orders WHERE guild=? AND seller=?",(ctx.guild.id,member.id)).fetchone()["n"]
    await ctx.reply(f"**{member.display_name}** • Ticket claims: {c['count'] if c else 0} • Orders handled: {o}")

@bot.command(name="timeout",aliases=["to"])
async def timeout(ctx, member: discord.Member, duration: str, *, reason="No reason provided"):
    if not has_role(ctx.author,"Staff"): return await ctx.reply("Staff/Admin-only moderation.")
    try:
        unit=duration[-1].lower(); amount=int(duration[:-1]); delta=timedelta(minutes=amount) if unit=="m" else timedelta(hours=amount) if unit=="h" else timedelta(days=amount) if unit=="d" else None
        if delta is None or delta>timedelta(days=28): raise ValueError
    except ValueError: return await ctx.reply("Use a duration such as `10m`, `2h`, or `1d` (maximum 28 days).")
    await member.timeout(datetime.now(timezone.utc)+delta,reason=reason); await ctx.reply(f"Timed out {member.mention} for {duration}."); await log_to(ctx.guild,f"⏱️ {member.mention} timed out by {ctx.author.mention}: {reason}")

@bot.command(name="warn")
async def warn(ctx, member: discord.Member, *, reason):
    if not has_role(ctx.author,"Staff"): return await ctx.reply("Staff/Admin-only moderation.")
    with connect() as db: db.execute("INSERT INTO warnings VALUES(?,?,?,?,?)",(ctx.guild.id,member.id,ctx.author.id,reason,datetime.now(timezone.utc).isoformat()))
    await ctx.reply(f"Warned {member.mention}."); await log_to(ctx.guild,f"⚠️ {member.mention} warned by {ctx.author.mention}: {reason}")

@bot.command(name="warnings")
async def warnings(ctx, member: discord.Member):
    if not has_role(ctx.author,"Staff"): return await ctx.reply("Staff/Admin-only moderation.")
    with connect() as db: rows=db.execute("SELECT * FROM warnings WHERE guild=? AND user=? ORDER BY rowid DESC LIMIT 15",(ctx.guild.id,member.id)).fetchall()
    await ctx.reply("\n".join(f"• {r['reason']} — <@{r['mod']}> ({r['created'][:10]})" for r in rows) or "No warnings.")

@bot.command(name="purge")
async def purge(ctx, count: int):
    if not has_role(ctx.author,"Staff"): return await ctx.reply("Staff/Admin-only moderation.")
    if not 1<=count<=100: return await ctx.reply("Choose a number from 1 to 100.")
    deleted=await ctx.channel.purge(limit=count+1); await ctx.send(f"Deleted {len(deleted)-1} messages.",delete_after=5); await log_to(ctx.guild,f"🧹 {ctx.author.mention} purged {len(deleted)-1} messages in {ctx.channel.mention}")

@bot.command(name="ban")
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if not await require_admin(ctx): return
    await member.ban(reason=f"{ctx.author}: {reason}"); await ctx.reply(f"Banned {member}."); await log_to(ctx.guild,f"🔨 {member} banned by {ctx.author.mention}: {reason}")

@bot.command(name="kick")
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if not await require_admin(ctx): return
    await member.kick(reason=f"{ctx.author}: {reason}"); await ctx.reply(f"Kicked {member}."); await log_to(ctx.guild,f"👢 {member} kicked by {ctx.author.mention}: {reason}")

@bot.command(name="reactionrole")
async def reactionrole(ctx, action: str, channel: discord.TextChannel, message_id: int, emoji: str, role: Optional[discord.Role]=None):
    if not await require_admin(ctx): return
    if action.lower()=="set":
        if role is None: return await ctx.reply("Provide a role: `$reactionrole set #channel MESSAGE_ID emoji @role`.")
        try:
            msg=await channel.fetch_message(message_id); await msg.add_reaction(emoji)
        except discord.HTTPException: return await ctx.reply("Could not find message or add that reaction.")
        with connect() as db: db.execute("INSERT OR REPLACE INTO reaction_roles VALUES(?,?,?,?,?)",(ctx.guild.id,channel.id,message_id,emoji,role.id))
        await ctx.reply(f"React with {emoji} on {channel.mention} to receive {role.mention}.")
    elif action.lower()=="remove":
        with connect() as db: db.execute("DELETE FROM reaction_roles WHERE guild=? AND message=? AND emoji=?",(ctx.guild.id,message_id,emoji))
        await ctx.reply("Reaction-role mapping removed.")
    else: await ctx.reply("Use `$reactionrole set #channel MESSAGE_ID emoji @role` or `remove`.")

@bot.command(name="customadd")
async def customadd(ctx, name: str, *, response: str):
    if not await require_admin(ctx): return
    with connect() as db: db.execute("INSERT OR REPLACE INTO custom VALUES(?,?,?)",(ctx.guild.id,name.lower(),response))
    await ctx.reply(f"Custom response `{name}` saved.")
@bot.command(name="customremove")
async def customremove(ctx, name: str):
    if not await require_admin(ctx): return
    with connect() as db: db.execute("DELETE FROM custom WHERE guild=? AND name=?",(ctx.guild.id,name.lower()))
    await ctx.reply("Custom response removed.")

@bot.command(name="setrestock")
async def setrestock(ctx, channel: discord.TextChannel):
    if await require_admin(ctx): setcfg(ctx.guild.id,"restock_channel",str(channel.id)); await ctx.reply(f"Restock alerts go to {channel.mention}.")
@bot.command(name="lowstock")
async def lowstock(ctx, threshold: int):
    if await require_admin(ctx): setcfg(ctx.guild.id,"low_stock_threshold",str(max(0,threshold))); await ctx.reply(f"Low-stock threshold set to {max(0,threshold)}.")
@bot.command(name="restock")
async def restock(ctx, product_id: str, stock: str):
    if not await require_admin(ctx): return
    with connect() as db:
        row=db.execute("SELECT name,stock FROM products WHERE guild=? AND id=?",(ctx.guild.id,product_id)).fetchone()
        if row: db.execute("UPDATE products SET stock=?,updated=? WHERE guild=? AND id=?",(stock,datetime.now(timezone.utc).isoformat(),ctx.guild.id,product_id))
    if not row: return await ctx.reply("Product not found. Sync products first.")
    channel_id=cfg(ctx.guild.id,"restock_channel"); channel=ctx.guild.get_channel(int(channel_id)) if channel_id.isdigit() else None
    channel = channel or ctx.channel
    status=stock_change_status(row["stock"],stock)
    await send_stock_notification(channel,row["name"],stock,status)
    threshold=cfg(ctx.guild.id,"low_stock_threshold","3")
    try: low=int(stock)<=int(threshold)
    except (TypeError,ValueError): low=False
    if low:
        low_embed=discord.Embed(title="⚠️ Low stock",description=f"**{discord.utils.escape_mentions(row['name'])}** has **{stock} units** left (threshold: {threshold}).",color=0xE67E22)
        await channel.send(embed=low_embed,allowed_mentions=discord.AllowedMentions.none())
    await ctx.reply(f"Stock updated for **{row['name']}**; the {status.lower()} card was posted in {channel.mention}.")

@bot.command(name="giveaway")
async def giveaway(ctx, minutes: int, *, prize: str):
    if not await require_admin(ctx): return
    if not 1<=minutes<=10080: return await ctx.reply("Duration must be between 1 minute and 7 days.")
    ends=datetime.now(timezone.utc)+timedelta(minutes=minutes)
    msg=await ctx.send(embed=discord.Embed(title="🎉 JARVIS AI Giveaway",description=f"Prize: **{prize}**\nEnds <t:{int(ends.timestamp())}:R>",color=0xF1C40F),view=GiveawayView())
    with connect() as db: db.execute("INSERT INTO giveaways VALUES(?,?,?,?,?,?,0)",(ctx.guild.id,ctx.channel.id,msg.id,prize,ends.isoformat(),ctx.author.id))
    await ctx.reply("Giveaway started.")
@bot.command(name="giveawayend")
async def giveawayend(ctx, message_id: int):
    if not await require_admin(ctx): return
    with connect() as db:
        row=db.execute("SELECT * FROM giveaways WHERE message=? AND guild=?",(message_id,ctx.guild.id)).fetchone()
        if row and not row["ended"]: db.execute("UPDATE giveaways SET ended=1 WHERE message=?",(message_id,))
    if not row: return await ctx.reply("Giveaway not found.")
    if row["ended"]: return await ctx.reply("That giveaway has already ended.")
    with connect() as db: entrants=db.execute("SELECT user FROM giveaway_entries WHERE message=?",(message_id,)).fetchall()
    winner=random.choice(entrants)["user"] if entrants else None
    await ctx.send(f"🎉 Giveaway **{row['prize']}** winner: {f'<@{winner}>' if winner else 'No eligible entrants.'}")

@bot.command(name="backup")
async def backup(ctx):
    if not await require_admin(ctx): return
    dest=DATA/"backups"; dest.mkdir(exist_ok=True); path=dest/f"manual-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
    await asyncio.to_thread(create_database_snapshot, path)
    embed=discord.Embed(title="💾 Database backup ready", description="The backup file is attached below.", color=0x5865F2)
    await ctx.reply(file=discord.File(path,filename=path.name),embed=embed,mention_author=False)


@bot.command(name="backuplist")
async def backuplist(ctx):
    if not await require_admin(ctx): return
    backup_dir = DATA / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(backup_dir.glob("*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)[:15]
    if not files:
        return await ctx.reply("📂 No backups yet. Run `$backup` to create one.", mention_author=False)
    lines = [f"• `{path.name}` — {datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}" for path in files]
    embed = discord.Embed(title="💾 Available database backups", description="\n".join(lines), color=0xD4AF37)
    embed.set_footer(text="Restore with $restorebackup <filename>; a safety copy is created first.")
    await ctx.reply(embed=embed, mention_author=False)


@bot.command(name="restorebackup")
async def restorebackup(ctx, *, filename: str):
    if not await require_admin(ctx): return
    try:
        validate_database_file(DATA / "backups" / filename)
    except ValueError as exc:
        return await ctx.reply(f"❌ {exc} Use `$backuplist` to choose a valid backup filename.", mention_author=False)
    embed = discord.Embed(
        title="⚠️ Confirm database restore",
        description=f"Restore `{discord.utils.escape_markdown(filename)}`? This replaces the current bot database. JARVIS first checks the backup and saves a safety copy of the current database.",
        color=0xD4AF37,
    )
    await ctx.reply(embed=embed, view=RestoreBackupView(ctx.author.id, filename), mention_author=False)


@bot.command(name="setup")
async def setup_wizard(ctx):
    if not await require_admin(ctx): return
    embed = discord.Embed(
        title="🧭 JARVIS guided setup",
        description="Choose roles and channels from Discord menus. You do not need to edit configuration values by hand. The bot still needs its Discord token in the host's secret settings.",
        color=0xD4AF37,
    )
    await ctx.reply(embed=embed, view=SetupWizardView(), mention_author=False)


@bot.command(name="checkup")
async def checkup(ctx):
    if not await require_admin(ctx): return
    guild, me = ctx.guild, ctx.guild.me
    if me is None:
        return await ctx.reply("I couldn't inspect my server member permissions. Re-invite or reconnect the bot and try again.", mention_author=False)
    permissions = me.guild_permissions
    checks = {
        "View Channels": permissions.view_channel,
        "Send Messages": permissions.send_messages,
        "Embed Links": permissions.embed_links,
        "Attach Files (welcome/stock art)": permissions.attach_files,
        "Read Message History (tickets)": permissions.read_message_history,
        "Manage Channels (tickets/verification)": permissions.manage_channels,
        "Manage Roles (join/verify/reaction roles)": permissions.manage_roles,
        "Manage Messages (security/purge)": permissions.manage_messages,
        "Timeout Members": permissions.moderate_members,
        "Manage Server (invite tracking)": permissions.manage_guild,
    }
    lines = [f"{'✅' if enabled else '⚠️'} **{label}** — {'available' if enabled else 'missing'}" for label, enabled in checks.items()]
    role_labels = (("admin_role", "Admin"), ("staff_role", "Staff"), ("seller_role", "Seller"),
                   ("customer_role", "Customer"), ("member_role", "Member"), ("verified_role", "Verified"), ("autorole_role", "Autorole"))
    role_lines = []
    for key, label in role_labels:
        role_id = cfg(guild.id, key)
        role = guild.get_role(int(role_id)) if role_id.isdigit() else discord.utils.get(guild.roles, name=label)
        if role is None:
            role_lines.append(f"⚠️ **{label}:** not configured")
        elif role >= me.top_role and not role.is_default():
            role_lines.append(f"⚠️ **{label}:** {role.mention} is at/above JARVIS's top role")
        else:
            role_lines.append(f"✅ **{label}:** {role.mention}")
    channel_lines = []
    for key, label, category_only in (("log_channel", "Logs", False), ("welcome_channel", "Welcome", False),
                                      ("restock_channel", "Restock notices", False), ("ticket_category", "Ticket category", True)):
        raw_id = cfg(guild.id, key)
        channel = guild.get_channel(int(raw_id)) if raw_id.isdigit() else None
        good = isinstance(channel, discord.CategoryChannel) if category_only else isinstance(channel, (discord.TextChannel, discord.NewsChannel))
        channel_lines.append(f"{'✅' if good else '⚠️'} **{label}:** {channel.mention if good and not category_only else (channel.name if good else 'not configured or wrong type')}")
    embed = discord.Embed(title="🩺 JARVIS setup check", description="\n".join(lines), color=0xD4AF37)
    embed.add_field(name="Role settings", value="\n".join(role_lines), inline=False)
    embed.add_field(name="Channel settings", value="\n".join(channel_lines), inline=False)
    embed.add_field(name="Developer Portal", value="The bot cannot read portal toggles. Confirm **Server Members Intent** and **Message Content Intent** are enabled in Discord Developer Portal → Bot.", inline=False)
    embed.set_footer(text="Use $setup to configure roles/channels. No token or secret is shown here.")
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="maintenance")
async def maintenance(ctx, state: str):
    if not await require_admin(ctx): return
    if state.lower() not in {"on","off"}: return await ctx.reply("Use `$maintenance on` or `$maintenance off`.")
    setcfg(ctx.guild.id,"maintenance",state.lower()); await ctx.reply(f"Maintenance mode {state.lower()}.")

@bot.command(name="security")
async def security(ctx, state: str = ""):
    if not await require_admin(ctx): return
    current = cfg(ctx.guild.id, "security_enabled", "on")
    if not state:
        status = "enabled" if current == "on" else "disabled"
        return await ctx.reply(f"🛡️ JARVIS security filter is **{status}**. It removes Discord invite links, mass mentions, and rapid message floods, and logs incidents to the configured `$setlog` channel. Use `$security on|off` to change it.")
    if state.lower() not in {"on", "off"}:
        return await ctx.reply("Use `$security on` or `$security off`.")
    setcfg(ctx.guild.id, "security_enabled", state.lower())
    await ctx.reply(f"🛡️ Security filter {('enabled' if state.lower() == 'on' else 'disabled')}. Invite links, mass mentions, and message floods {'will' if state.lower() == 'on' else 'will no longer'} be filtered.")

@bot.command(name="setticketcategory")
async def setticketcategory(ctx, category: discord.CategoryChannel):
    if await require_admin(ctx): setcfg(ctx.guild.id,"ticket_category",str(category.id)); await ctx.reply(f"Ticket category set to {category.name}.")

@bot.command(name="sales")
async def sales(ctx):
    if not await require_staff(ctx): return
    with connect() as db:
        rows=db.execute("SELECT substr(created,1,10) day,COUNT(*) orders,SUM(amount) total FROM orders WHERE guild=? AND status='complete' GROUP BY day ORDER BY day DESC LIMIT 7",(ctx.guild.id,)).fetchall()
    await ctx.reply("\n".join(f"{r['day']}: {r['orders']} orders • {r['total']:.2f}" for r in rows) or "No verified sales recorded.")

@bot.command(name="ticketclaim")
async def ticketclaim(ctx):
    if not (has_role(ctx.author, "Seller") or is_admin(ctx.author)):
        return await ctx.reply("Only Sellers or Admins can claim tickets.")
    with connect() as db:
        row = db.execute("SELECT * FROM tickets WHERE channel=? AND status='open'", (ctx.channel.id,)).fetchone()
        if not row:
            return await ctx.reply("This channel is not an open JARVIS ticket.")
        if row["claimed"] == ctx.author.id:
            return await ctx.reply("You already claimed this ticket.")
        if row["claimed"] is not None and not is_admin(ctx.author):
            return await ctx.reply(f"This ticket is already claimed by <@{row['claimed']}>.")
        db.execute("UPDATE tickets SET claimed=? WHERE channel=?", (ctx.author.id, ctx.channel.id))
        db.execute(
            "INSERT INTO claims VALUES(?,?,1) ON CONFLICT(guild,seller) DO UPDATE SET count=count+1",
            (ctx.guild.id, ctx.author.id),
        )
    await ctx.reply(f"🙋 Ticket claimed by {ctx.author.mention}.")
    await log_to(ctx.guild, f"🎫 Ticket claimed by {ctx.author.mention}: {ctx.channel.mention}")


@bot.command(name="ticketunclaim")
async def ticketunclaim(ctx):
    if not (has_role(ctx.author, "Seller") or is_admin(ctx.author)):
        return await ctx.reply("Only Sellers or Admins can unclaim tickets.")
    with connect() as db:
        row = db.execute("SELECT * FROM tickets WHERE channel=? AND status='open'", (ctx.channel.id,)).fetchone()
        if not row:
            return await ctx.reply("This channel is not an open JARVIS ticket.")
        if row["claimed"] is None:
            return await ctx.reply("This ticket is not currently claimed.")
        if row["claimed"] != ctx.author.id and not is_admin(ctx.author):
            return await ctx.reply("Only the claiming Seller or an Admin can unclaim this ticket.")
        db.execute("UPDATE tickets SET claimed=NULL WHERE channel=?", (ctx.channel.id,))
    await ctx.reply(f"↩️ Ticket unclaimed by {ctx.author.mention}.")
    await log_to(ctx.guild, f"🎫 Ticket unclaimed by {ctx.author.mention}: {ctx.channel.mention}")


@bot.command(name="ticketclose")
async def ticketclose(ctx):
    if not (has_role(ctx.author, "Seller") or is_admin(ctx.author)):
        return await ctx.reply("Only Sellers or Admins can close tickets.")
    with connect() as db:
        row = db.execute("SELECT * FROM tickets WHERE channel=? AND status='open'", (ctx.channel.id,)).fetchone()
    if not row:
        return await ctx.reply("This channel is not an open JARVIS ticket.")
    await ctx.reply("🔒 Closing this ticket and saving its transcript…")
    await close_ticket_channel(ctx.channel, ctx.guild, ctx.author, row)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error,commands.CommandNotFound): return
    if isinstance(error,commands.MissingRequiredArgument): return await ctx.reply(f"Missing argument: `{error.param.name}`. Use `$help`.")
    if isinstance(error,commands.BadArgument): return await ctx.reply("I couldn't parse that. Check mentions, channel, role, and argument order; use `$help`.")
    if isinstance(error,commands.CommandOnCooldown): return await ctx.reply(f"Try again in {error.retry_after:.1f}s.")
    log.error("Command failure", exc_info=(type(error),error,error.__traceback__))
    try: await ctx.reply("That action failed. The error was recorded in `data/jarvis.log`.")
    except discord.HTTPException: pass


def main():
    init_db()
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token.")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()


