import os
import json
import asyncio
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import re
import gc

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

SECRET_KEYS = {
    "K7M9-P2QX-4RWL", "B3N8-T6YH-9VCF", "D5J1-M4ZK-7QWE", "H8L2-R9XP-3TYU",
    "F6C4-W1VN-8BGS", "A9E7-K5MD-2HJX", "P3Q8-L6RF-4ZNC", "T1Y5-B7VK-9WME",
    "R4U2-N8CX-6DJP", "M7S9-Q3HL-1FVT", "G2W6-E5YB-8KZR", "X9C1-J4NP-7LQA",
    "V5D8-H2MT-3RKF", "Z6B3-F9WU-4YSE", "L1P7-A8QK-5CXN", "N4R2-T6JM-9VBD",
    "C8H5-W3YL-1QPF", "E7K9-M2DX-6BGS", "Q3V1-R8NF-4JHT", "Y6Z4-P5LC-7WME"
}

AUTHORIZED_FILE = "/var/data/authorized_users.json"
DATA_FILE = "/var/data/contacts.csv"

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
def normalize_phone(phone):
    if pd.isna(phone):
        return ""
    phone = re.sub(r"[^\d+]", "", str(phone))
    if phone.startswith("0"):
        phone = "+33" + phone[1:]
    elif phone.startswith("33") and not phone.startswith("+"):
        phone = "+" + phone
    return phone

def find_columns(dataframe):
    cols_lower = {str(c).lower().strip(): c for c in dataframe.columns}
    mapping = {}
    for k in ["nom", "name", "lastname"]:
        if k in cols_lower: mapping["nom"] = cols_lower[k]; break
    for k in ["prénom", "prenom", "firstname"]:
        if k in cols_lower: mapping["prenom"] = cols_lower[k]; break
    for k in ["téléphone", "telephone", "phone", "tel", "mobile"]:
        if k in cols_lower: mapping["phone"] = cols_lower[k]; break
    for k in ["adresse", "address"]:
        if k in cols_lower: mapping["adresse"] = cols_lower[k]; break
    return mapping

# ==================== HELPERS ====================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_authorized(user_id):
    return user_id in authorized_users or is_admin(user_id)

async def animate_message(message, steps, delay=0.4):
    for text in steps:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(delay)
        except Exception:
            pass

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🔐 <b>Accès privé</b>\n\nEntre ta clé secrète.", parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text("✅ <b>Bienvenue</b>\n\nEnvoie un numéro de téléphone.", parse_mode=ParseMode.HTML)

async def handle_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_authorized(user_id):
        return
    key = update.message.text.strip().upper()
    if key in SECRET_KEYS:
        authorized_users.add(user_id)
        save_authorized(authorized_users)
        await update.message.reply_text("✅ Accès autorisé. Tu peux chercher.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Clé invalide.")

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text("🔐 Entre d'abord une clé.")
        return

    if not os.path.exists(DATA_FILE):
        await update.message.reply_text("⚠️ Aucune base chargée.")
        return

    query = update.message.text.strip()
    phone_query = normalize_phone(query)[-9:]

    msg = await update.message.reply_text("🔍 Recherche...")
    await animate_message(msg, ["🔍 Recherche...", "✨ Résultat"], delay=0.25)

    # Charge par chunks pour économiser la mémoire
    results = []
    try:
        for chunk in pd.read_csv(DATA_FILE, sep='|', engine='python', on_bad_lines='skip', chunksize=50000):
            mapping = find_columns(chunk)
            if "phone" not in mapping:
                continue
            chunk["phone_norm"] = chunk[mapping["phone"]].apply(normalize_phone)
            match = chunk[chunk["phone_norm"].str.contains(phone_query, na=False)]
            if not match.empty:
                results.append(match)
            del chunk
            gc.collect()
    except Exception as e:
        await msg.edit_text(f"❌ Erreur : {str(e)}")
        return

    if not results:
        await msg.edit_text("❌ Aucun résultat.")
        return

    final = pd.concat(results, ignore_index=True).head(5)
    text = ""
    for _, row in final.iterrows():
        text += "📋 <b>Fiche contact</b>\n──────────────────\n"
        for col in final.columns:
            if col == "phone_norm":
                continue
            value = row.get(col, "")
            if pd.notna(value) and str(value).strip() not in ["", "nan"]:
                clean = str(value).replace(".0", "").strip()
                emoji = "📞" if "tel" in col.lower() or "phone" in col.lower() else "•"
                text += f"{emoji} <b>{col}</b>  {clean}\n"
        text += "──────────────────\n\n"
    await msg.edit_text(text, parse_mode=ParseMode.HTML)
    del final, results
    gc.collect()

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin seulement.")
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("❌ Fichier .csv uniquement.")
        return

    temp_file = "temp_upload.csv"
    file = await doc.get_file()
    await file.download_to_drive(temp_file)

    try:
        # Append simple sans tout charger en mémoire
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "a", encoding="utf-8") as dest:
                with open(temp_file, "r", encoding="utf-8", errors="ignore") as src:
                    next(src, None)  # skip header
                    dest.write(src.read())
        else:
            # Copie au lieu de rename (différents systèmes de fichiers)
            with open(temp_file, "r", encoding="utf-8", errors="ignore") as src:
                with open(DATA_FILE, "w", encoding="utf-8") as dest:
                    dest.write(src.read())

        # Compte approximatif
        count = sum(1 for _ in open(DATA_FILE, encoding="utf-8", errors="ignore")) - 1
        await update.message.reply_text(f"✅ Base mise à jour\n\n📊 ~{count} lignes.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur : {str(e)}")
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        gc.collect()

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count = 0
    if os.path.exists(DATA_FILE):
        count = sum(1 for _ in open(DATA_FILE, encoding="utf-8", errors="ignore")) - 1
    await update.message.reply_text(f"📊 Contacts : <b>{count}</b>", parse_mode=ParseMode.HTML)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    await update.message.reply_text("🗑️ Base réinitialisée.")

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_key(u, c) if not is_authorized(u.effective_user.id) else handle_search(u, c)))

    print("Bot démarré...")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.run_polling()

if __name__ == "__main__":
    main()
