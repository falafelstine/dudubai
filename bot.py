import os
import json
import asyncio
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode
import re
from datetime import datetime

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# 20 clés secrètes
SECRET_KEYS = {
    "K7M9-P2QX-4RWL", "B3N8-T6YH-9VCF", "D5J1-M4ZK-7QWE", "H8L2-R9XP-3TYU",
    "F6C4-W1VN-8BGS", "A9E7-K5MD-2HJX", "P3Q8-L6RF-4ZNC", "T1Y5-B7VK-9WME",
    "R4U2-N8CX-6DJP", "M7S9-Q3HL-1FVT", "G2W6-E5YB-8KZR", "X9C1-J4NP-7LQA",
    "V5D8-H2MT-3RKF", "Z6B3-F9WU-4YSE", "L1P7-A8QK-5CXN", "N4R2-T6JM-9VBD",
    "C8H5-W3YL-1QPF", "E7K9-M2DX-6BGS", "Q3V1-R8NF-4JHT", "Y6Z4-P5LC-7WME"
}

AUTHORIZED_FILE = "authorized_users.json"
DATA_FILE = "contacts.csv"

# ==================== PERSISTANCE ====================
def load_authorized():
    if os.path.exists(AUTHORIZED_FILE):
        with open(AUTHORIZED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_authorized(users):
    with open(AUTHORIZED_FILE, "w") as f:
        json.dump(list(users), f)

authorized_users = load_authorized()

# ==================== DONNÉES ====================
df = None

def load_csv():
    global df
    if os.path.exists(DATA_FILE):
        df = pd.read_csv(DATA_FILE)
        return True
    return False

def normalize_phone(phone):
    if pd.isna(phone):
        return ""
    phone = re.sub(r"[^\d+]", "", str(phone))
    if phone.startswith("0"):
        phone = "+33" + phone[1:]
    elif phone.startswith("33") and not phone.startswith("+"):
        phone = "+" + phone
    return phone

def find_columns(df):
    cols = {c.lower(): c for c in df.columns}
    mapping = {}
    for key in ["nom", "name", "lastname"]:
        if key in cols: mapping["nom"] = cols[key]; break
    for key in ["prénom", "prenom", "firstname", "first"]:
        if key in cols: mapping["prenom"] = cols[key]; break
    for key in ["téléphone", "telephone", "phone", "numero", "numéro", "tel"]:
        if key in cols: mapping["phone"] = cols[key]; break
    for key in ["adresse", "address", "addr"]:
        if key in cols: mapping["adresse"] = cols[key]; break
    return mapping

# ==================== HELPERS ====================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_authorized(user_id):
    return user_id in authorized_users or is_admin(user_id)

async def animate_message(message, steps, delay=0.6):
    for text in steps:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(delay)
        except:
            pass

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    welcome_gif = "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif"  # Placeholder

    if not is_authorized(user_id):
        await update.message.reply_animation(animation=welcome_gif)
        await update.message.reply_text(
            "🔐 <b>Accès restreint</b>\n\n"
            "Entre ta <b>clé secrète</b> pour accéder au bot :",
            parse_mode=ParseMode.HTML
        )
        return

    await update.message.reply_animation(animation=welcome_gif)
    msg = await update.message.reply_text("✨ Initialisation...")
    await animate_message(msg, [
        "✨ Initialisation...",
        "🔍 Préparation de la recherche...",
        "✅ <b>Prêt !</b>\n\nEnvoie simplement un <b>numéro de téléphone</b> pour obtenir la fiche."
    ])

async def handle_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_authorized(user_id):
        return

    key = update.message.text.strip().upper()
    if key in SECRET_KEYS:
        authorized_users.add(user_id)
        save_authorized(authorized_users)
        await update.message.reply_text(
            "✅ <b>Clé valide !</b>\n\nTu es maintenant connecté de façon permanente.\n"
            "Envoie un numéro pour rechercher.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("❌ Clé invalide. Réessaie.")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🔐 Tu dois d'abord entrer une clé secrète.")
        return

    if df is None:
        await update.message.reply_text("⚠️ Aucune base de données chargée. Contacte un admin.")
        return

    query = update.message.text.strip()
    phone_query = normalize_phone(query)

    msg = await update.message.reply_text("🔍 Recherche en cours...")
    await animate_message(msg, [
        "🔍 Recherche en cours...",
        "⚡ Analyse des données...",
        "✨ Finalisation..."
    ], delay=0.4)

    mapping = find_columns(df)
    if "phone" not in mapping:
        await msg.edit_text("❌ Colonne téléphone introuvable dans le CSV.")
        return

    df["phone_norm"] = df[mapping["phone"]].apply(normalize_phone)
    results = df[df["phone_norm"].str.contains(phone_query[-9:], na=False)]

    if results.empty:
        await msg.edit_text("❌ <b>Aucun contact trouvé</b> pour ce numéro.", parse_mode=ParseMode.HTML)
        return

    # Affichage fiche
    text = ""
    for _, row in results.head(5).iterrows():
        nom = row.get(mapping.get("nom", ""), "—")
        prenom = row.get(mapping.get("prenom", ""), "—")
        phone = row.get(mapping["phone"], "—")
        adresse = row.get(mapping.get("adresse", ""), "—")
        text += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Nom :</b> {nom}\n"
            f"✨ <b>Prénom :</b> {prenom}\n"
            f"📞 <b>Numéro :</b> <code>{phone}</code>\n"
            f"📍 <b>Adresse :</b> {adresse}\n"
        )
    text += "━━━━━━━━━━━━━━━━━━"

    await msg.edit_text(text, parse_mode=ParseMode.HTML)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Réservé aux administrateurs.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("❌ Envoie uniquement un fichier .csv")
        return

    file = await doc.get_file()
    await file.download_to_drive(DATA_FILE)
    if load_csv():
        await update.message.reply_text(f"✅ CSV chargé avec succès !\n📊 {len(df)} contacts importés.")
    else:
        await update.message.reply_text("❌ Erreur lors du chargement.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count = len(df) if df is not None else 0
    await update.message.reply_text(f"📊 <b>Statut</b>\n\nContacts chargés : <b>{count}</b>\nUtilisateurs autorisés : <b>{len(authorized_users)}</b>", parse_mode=ParseMode.HTML)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    global df
    df = None
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    await update.message.reply_text("🗑️ Base de données réinitialisée.")

# ==================== MAIN ====================
def main():
    load_csv()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_key(u, c) if not is_authorized(u.effective_user.id) else handle_search(u, c)))

    print("Bot démarré...")
    app.run_polling()

if __name__ == "__main__":
    main()
