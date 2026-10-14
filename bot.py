import os
import json
import asyncio
import sqlite3
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import re
import gc
from datetime import datetime

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
DB_FILE = "/var/data/contacts.db"

# ==================== SQLITE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_key TEXT,
            data TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_phone_key ON contacts(phone_key)")
    conn.commit()
    conn.close()

def get_db_count():
    if not os.path.exists(DB_FILE):
        return 0
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM contacts")
        return cur.fetchone()[0]
    finally:
        conn.close()

def insert_contacts(rows):
    """rows = list of (phone_key, json_data)"""
    if not rows:
        return
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.executemany(
            "INSERT INTO contacts (phone_key, data) VALUES (?, ?)",
            rows
        )
        conn.commit()
    finally:
        conn.close()

def search_contacts(phone_key, limit=5):
    if not os.path.exists(DB_FILE):
        return []
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute(
            "SELECT data FROM contacts WHERE phone_key = ? LIMIT ?",
            (phone_key, limit)
        )
        return [json.loads(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()

def clear_db():
    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("DELETE FROM contacts")
            conn.commit()
        finally:
            conn.close()

init_db()

# ==================== PERSISTANCE ====================
def load_authorized():
    if os.path.exists(AUTHORIZED_FILE):
        with open(AUTHORIZED_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                return {str(uid): {"added": None, "last_seen": None, "username": None} for uid in data}
            return data
    return {}

def save_authorized(users):
    with open(AUTHORIZED_FILE, "w") as f:
        json.dump(users, f, indent=2)

authorized_users = load_authorized()

def touch_user(user):
    uid = str(user.id)
    if uid not in authorized_users:
        return
    authorized_users[uid]["last_seen"] = datetime.utcnow().isoformat(timespec="seconds")
    authorized_users[uid]["username"] = user.username
    authorized_users[uid]["first_name"] = user.first_name
    save_authorized(authorized_users)

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
    return str(user_id) in authorized_users or is_admin(user_id)

async def animate_message(message, steps, delay=0.35):
    for text in steps:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(delay)
        except Exception:
            pass

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if not is_authorized(user_id):
        await update.message.reply_text(
            "<b>Accès restreint</b>\n\n"
            "Ce bot est privé.\n"
            "Envoyez votre <b>clé secrète</b> pour débloquer l’accès.",
            parse_mode=ParseMode.HTML
        )
        return

    touch_user(user)
    await update.message.reply_text(
        "<b>Bienvenue</b>\n\n"
        "Envoyez un numéro de téléphone pour lancer une recherche.",
        parse_mode=ParseMode.HTML
    )

async def handle_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    if is_authorized(user_id):
        return

    key = update.message.text.strip().upper()
    if key in SECRET_KEYS:
        authorized_users[str(user_id)] = {
            "added": datetime.utcnow().isoformat(timespec="seconds"),
            "last_seen": datetime.utcnow().isoformat(timespec="seconds"),
            "username": user.username,
            "first_name": user.first_name
        }
        save_authorized(authorized_users)
        await update.message.reply_text(
            "<b>Clé acceptée</b>\n\n"
            "Accès débloqué. Vous n’aurez plus à la saisir.\n\n"
            "Envoyez un numéro de téléphone.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "<b>Clé invalide</b>\n\n"
            "Vérifiez et réessayez.",
            parse_mode=ParseMode.HTML
        )

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "<b>Accès requis</b>\n\n"
            "Entrez d’abord votre clé secrète.",
            parse_mode=ParseMode.HTML
        )
        return

    touch_user(user)

    if get_db_count() == 0:
        await update.message.reply_text(
            "<b>Base vide</b>\n\n"
            "Aucun fichier de contacts n’a été chargé.",
            parse_mode=ParseMode.HTML
        )
        return

    query = update.message.text.strip()
    phone_query = normalize_phone(query)[-9:]

    msg = await update.message.reply_text("<i>Recherche en cours…</i>", parse_mode=ParseMode.HTML)

    try:
        results = search_contacts(phone_query, limit=5)
    except Exception as e:
        await msg.edit_text(
            f"<b>Erreur</b>\n\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if not results:
        await msg.edit_text(
            "<b>Aucun résultat</b>\n\n"
            "Aucun contact ne correspond à ce numéro.",
            parse_mode=ParseMode.HTML
        )
        return

    text = ""
    for idx, row in enumerate(results, 1):
        text += f"<b>Contact {idx}</b>\n"
        text += "<pre>"
        for col, value in row.items():
            if pd.notna(value) and str(value).strip() not in ["", "nan"]:
                clean = str(value).replace(".0", "").strip()
                text += f"{str(col)[:18]:<18} {clean}\n"
        text += "</pre>\n"
    await msg.edit_text(text.strip(), parse_mode=ParseMode.HTML)

# ==================== ADMIN PANEL ====================
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    count_contacts = get_db_count()
    active = len(authorized_users)
    text = (
        "<b>Panel Admin</b>\n\n"
        f"Contacts dans la base : <b>{count_contacts}</b>\n"
        f"Utilisateurs autorisés : <b>{active}</b>\n\n"
        "Commandes :\n"
        "/users — Liste des utilisateurs\n"
        "/status — Statut rapide\n"
        "/reset — Vider la base"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not authorized_users:
        await update.message.reply_text("Aucun utilisateur autorisé.")
        return

    lines = ["<b>Utilisateurs autorisés</b>\n"]
    for uid, info in list(authorized_users.items())[:30]:
        name = info.get("first_name") or "?"
        uname = f"@{info.get('username')}" if info.get("username") else "—"
        last = info.get("last_seen") or "jamais"
        lines.append(f"<code>{uid}</code>  {name}  {uname}\n  Dernière activité : {last}")

    if len(authorized_users) > 30:
        lines.append(f"\n… et {len(authorized_users)-30} autres")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "<b>Accès refusé</b>\n\nRéservé aux administrateurs.",
            parse_mode=ParseMode.HTML
        )
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text(
            "<b>Format invalide</b>\n\nSeuls les fichiers .csv sont acceptés.",
            parse_mode=ParseMode.HTML
        )
        return

    temp_file = "temp_upload.csv"
    file = await doc.get_file()
    await file.download_to_drive(temp_file)

    try:
        # Indexation SQLite par chunks
        total_inserted = 0
        for chunk in pd.read_csv(temp_file, sep='|', engine='python', on_bad_lines='skip', chunksize=20000):
            mapping = find_columns(chunk)
            if "phone" not in mapping:
                continue
            rows_to_insert = []
            for _, row in chunk.iterrows():
                phone_norm = normalize_phone(row.get(mapping["phone"], ""))
                phone_key = phone_norm[-9:] if phone_norm else ""
                # Stocke toutes les colonnes utiles
                data = {col: (None if pd.isna(val) else str(val).replace(".0", "").strip()) for col, val in row.items()}
                rows_to_insert.append((phone_key, json.dumps(data, ensure_ascii=False)))
            insert_contacts(rows_to_insert)
            total_inserted += len(rows_to_insert)
            del chunk, rows_to_insert
            gc.collect()

        # Aussi append dans le CSV pour compatibilité
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "a", encoding="utf-8") as dest:
                with open(temp_file, "r", encoding="utf-8", errors="ignore") as src:
                    next(src, None)
                    dest.write(src.read())
        else:
            with open(temp_file, "r", encoding="utf-8", errors="ignore") as src:
                with open(DATA_FILE, "w", encoding="utf-8") as dest:
                    dest.write(src.read())

        count = get_db_count()
        await update.message.reply_text(
            f"<b>Base mise à jour</b>\n\n{count} contacts indexés",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(
            f"<b>Erreur d’import</b>\n\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        gc.collect()

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count = get_db_count()
    await update.message.reply_text(
        f"<b>Statut</b>\n\n"
        f"Contacts : <b>{count}</b>\n"
        f"Utilisateurs : <b>{len(authorized_users)}</b>",
        parse_mode=ParseMode.HTML
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    clear_db()
    await update.message.reply_text(
        "<b>Base réinitialisée</b>\n\nDonnées supprimées.",
        parse_mode=ParseMode.HTML
    )

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("users", users))
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
