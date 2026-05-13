#!/usr/bin/env python3
"""
Telegram Bot – AI PDF Generator with OpenRouter
Uses config.py for all settings.
"""

import os
import asyncio
import logging
from io import BytesIO
from typing import List, Dict

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import gray
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
)

import config  # <--- IMPORT CONFIG FILE

# ---------- CONFIG (from config.py) ----------
BOT_TOKEN = config.BOT_TOKEN
OPENROUTER_API_KEY = config.OPENROUTER_API_KEY
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OWNER_USERNAME = config.OWNER_USERNAME
DEFAULT_MODEL = config.DEFAULT_MODEL

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Per‑user booklet storage
user_booklets: Dict[int, dict] = {}

# ========== OPENROUTER ==========
def _sync_query(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"]["content"]
        return "⚠️ AI returned no content."
    except Exception as e:
        logger.error("OpenRouter error: %s", e)
        return f"❌ Error: {e}"

async def query_openrouter(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_query, prompt)

# ========== PDF HELPERS ==========
def _build_progress_bar(percent: int) -> str:
    total = 20
    filled = int(total * percent / 100)
    bar = "[" + "=" * filled + "-" * (total - filled) + f"] {percent}%"
    return bar

def _add_watermark(canvas_obj, doc):
    canvas_obj.saveState()
    canvas_obj.setFillAlpha(0.15)
    canvas_obj.setFillColor(gray)
    canvas_obj.setFont("Helvetica", 65)
    canvas_obj.translate(A4[0] / 2, A4[1] / 2)
    canvas_obj.rotate(45)
    canvas_obj.drawCentredString(0, 0, "aipdfbot")
    canvas_obj.restoreState()

def generate_booklet_pdf_sync(topics: List[dict]) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = styles["Heading2"]
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["BodyText"],
        fontSize=12,
        leading=16,
        spaceAfter=10,
    )
    story = []

    for idx, item in enumerate(topics):
        story.append(Paragraph(f"📌 Topic {idx + 1}: {item['topic']}", title_style))
        story.append(Spacer(1, 0.15 * inch))
        for para in item["answer"].split("\n\n"):
            para = para.strip()
            if para:
                para = para.replace("\n", "<br/>")
                story.append(Paragraph(para, body_style))
        if idx < len(topics) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=_add_watermark, onLaterPages=_add_watermark)
    buffer.seek(0)
    return buffer

def generate_single_pdf_sync(topic: str, answer: str) -> BytesIO:
    return generate_booklet_pdf_sync([{"topic": topic, "answer": answer}])

async def animate_and_generate(
    progress_msg,
    context,
    pdf_func,
    *args,
) -> BytesIO:
    loop = asyncio.get_running_loop()
    pdf_future = loop.run_in_executor(None, pdf_func, *args)

    for i in range(21):  # 0,5,10,...,100
        percent = i * 5
        if pdf_future.done():
            break
        bar = _build_progress_bar(percent)
        text = f"> ⏳ Generating PDF...\n> {bar}"
        try:
            await progress_msg.edit_text(text)
        except Exception:
            pass
        await asyncio.sleep(0.4)

    pdf_buffer = await pdf_future
    bar100 = _build_progress_bar(100)
    text100 = f"> ✅ PDF generated!\n> {bar100}"
    await progress_msg.edit_text(text100)
    return pdf_buffer

# ========== TELEGRAM HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome = (
        f"👋 Hello {user.mention_html()}!\n"
        f"Welcome to AI PDF Bot – creating research PDFs with OpenRouter.\n"
        f"Owner: {OWNER_USERNAME}\n\n"
        f"Commands:\n"
        f"/research &lt;prompt&gt; – get AI answer + add to booklet\n"
        f"/pdf &lt;topic&gt; – direct PDF for a single topic"
    )
    await update.message.reply_html(welcome)

async def research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "ℹ️ Please provide a prompt.\nExample: /research explain quantum computing"
        )
        return

    prompt = " ".join(context.args)
    msg = await update.message.reply_text("🔍 Researching...")
    ai_response = await query_openrouter(prompt)
    await msg.delete()

    context.user_data["last_research"] = {"topic": prompt, "answer": ai_response}

    keyboard = [[InlineKeyboardButton("📥 Add to PDF Booklet", callback_data="add_booklet")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    preview = ai_response[:3500] + ("..." if len(ai_response) > 3500 else "")
    await update.message.reply_text(
        f"📄 *Research on:* {prompt[:100]}\n\n{preview}",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

async def add_booklet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    research = context.user_data.pop("last_research", None)
    if not research:
        await query.edit_message_text("❌ No recent research found. Use /research first.")
        return

    if user_id not in user_booklets:
        user_booklets[user_id] = {"topics": [], "last_msg_id": None}
    booklet = user_booklets[user_id]
    booklet["topics"].append(research)

    pages = len(booklet["topics"])

    await query.edit_message_reply_markup(reply_markup=None)

    buttons = []
    buttons.append([InlineKeyboardButton(f"📄 {pages} page(s)", callback_data="noop")])
    if pages >= 2:
        buttons.append([InlineKeyboardButton("📑 Make PDF", callback_data="make_pdf")])

    follow_text = (
        f"✅ Topic added! (Total pages: {pages})\n"
        f"Send /research &lt;topic&gt; to add more."
    )
    follow_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=follow_text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    booklet["last_msg_id"] = follow_msg.message_id

async def make_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    booklet = user_booklets.get(user_id)
    if not booklet or not booklet["topics"]:
        await query.edit_message_text("❌ No pages to make PDF. Add topics first.")
        return

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="> ⏳ Starting PDF generation...",
    )

    pdf_buffer = await animate_and_generate(
        progress_msg, context, generate_booklet_pdf_sync, booklet["topics"]
    )

    await progress_msg.delete()
    caption = f"📑 Your PDF booklet ({len(booklet['topics'])} topics)"
    await context.bot.send_document(
        chat_id=chat_id,
        document=pdf_buffer,
        filename="booklet.pdf",
        caption=caption,
    )
    del user_booklets[user_id]

async def pdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("ℹ️ Please provide a topic.\nExample: /pdf quantum computing")
        return

    prompt = " ".join(context.args)
    msg = await update.message.reply_text("🔍 Researching for PDF...")
    ai_response = await query_openrouter(prompt)
    await msg.delete()

    progress_msg = await update.message.reply_text("> ⏳ Creating your PDF...")
    pdf_buffer = await animate_and_generate(
        progress_msg, context, generate_single_pdf_sync, prompt, ai_response
    )
    await progress_msg.delete()
    await update.message.reply_document(
        document=pdf_buffer,
        filename="research.pdf",
        caption=f"📄 Research on: {prompt[:100]}",
    )

async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# ========== MAIN ==========
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("Please set your BOT_TOKEN in config.py")
        return
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "YOUR_OPENROUTER_API_KEY":
        logger.error("Please set your OPENROUTER_API_KEY in config.py")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("research", research))
    app.add_handler(CommandHandler("pdf", pdf_command))

    app.add_handler(CallbackQueryHandler(add_booklet_callback, pattern="^add_booklet$"))
    app.add_handler(CallbackQueryHandler(make_pdf_callback, pattern="^make_pdf$"))
    app.add_handler(CallbackQueryHandler(noop_callback, pattern="^noop$"))

    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
