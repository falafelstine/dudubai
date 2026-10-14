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
from datetime import datetime, date

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
ADMINS_FILE = "/var/data/extra_admins.json"
SETTINGS_FILE = "/var/data/settings.json"
DATA_FILE = "/var/data/contacts.csv"
DB_FILE = "/var/data/contacts.db"

os.makedirs(os.path.dirname(AUTHORIZED_FILE), exist_ok=True)

# ==================== SQLITE ====================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_key TEXT,
            name_key TEXT,
            data TEXT
        )
    """)
    # Migration douce si ancienne base
    try:
        conn.execute("ALTER TABLE contacts ADD COLUMN name_key TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_phone_key ON contacts(phone_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_name_key ON contacts(name_key)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            query TEXT,
            search_type TEXT,
            results INTEGER,
            ts TEXT
        )
    """)
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
    """rows = list of (phone_key, name_key, json_data)
    Ignore les doublons de phone_key déjà présents."""
    if not rows:
        return 0
    conn = sqlite3.connect(DB_FILE)
    try:
        # Récupère les phone_key déjà existants dans ce lot
        keys = [r[0] for r in rows if r[0]]
        existing = set()
        if keys:
            # Requête par paquets pour éviter les limites SQLite
            for i in range(0, len(keys), 500):
                batch = keys[i:i+500]
                placeholders = ",".join("?" * len(batch))
                cur = conn.execute(
                    f"SELECT phone_key FROM contacts WHERE phone_key IN ({placeholders})",
                    batch
                )
                existing.update(row[0] for row in cur.fetchall())

        to_insert = [r for r in rows if not r[0] or r[0] not in existing]
        if to_insert:
            conn.executemany(
                "INSERT INTO contacts (phone_key, name_key, data) VALUES (?, ?, ?)",
                to_insert
            )
            conn.commit()
        return len(to_insert)
    finally:
        conn.close()

def search_by_phone(phone_key, limit=5):
    if not os.path.exists(DB_FILE) or not phone_key:
        return []
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute(
            "SELECT data FROM contacts WHERE phone_key = ? LIMIT ?",
            (phone_key, limit)
        )
        results = [json.loads(row[0]) for row in cur.fetchall()]
        if results:
            return results
        cur = conn.execute(
            "SELECT data FROM contacts WHERE data LIKE ? LIMIT ?",
            (f"%{phone_key}%", limit)
        )
        return [json.loads(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()

def search_by_name(name_query, limit=5):
    if not os.path.exists(DB_FILE) or not name_query:
        return []
    conn = sqlite3.connect(DB_FILE)
    try:
        pattern = f"%{name_query.lower()}%"
        cur = conn.execute(
            "SELECT data FROM contacts WHERE name_key LIKE ? LIMIT ?",
            (pattern, limit)
        )
        return [json.loads(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()

def log_search(user_id, query, search_type, results_count):
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            "INSERT INTO search_logs (user_id, query, search_type, results, ts) VALUES (?, ?, ?, ?, ?)",
            (str(user_id), query[:100], search_type, results_count, datetime.utcnow().isoformat(timespec="seconds"))
        )
        conn.commit()
    finally:
        conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    try:
        total_searches = conn.execute("SELECT COUNT(*) FROM search_logs").fetchone()[0]
        today = date.today().isoformat()
        today_searches = conn.execute(
            "SELECT COUNT(*) FROM search_logs WHERE ts LIKE ?", (f"{today}%",)
        ).fetchone()[0]
        return total_searches, today_searches
    finally:
        conn.close()

def get_recent_logs(limit=15):
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute(
            "SELECT user_id, query, search_type, results, ts FROM search_logs ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return cur.fetchall()
    finally:
        conn.close()

def clear_db():
    if os.path.exists(DB_FILE):
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("DELETE FROM contacts")
            conn.execute("DELETE FROM search_logs")
            conn.commit()
        finally:
            conn.close()

init_db()

# ==================== PERSISTANCE ====================
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_authorized():
    data = load_json(AUTHORIZED_FILE, {})
    if isinstance(data, list):
        return {str(uid): {"added": None, "last_seen": None, "username": None} for uid in data}
    return data

def save_authorized(users):
    save_json(AUTHORIZED_FILE, users)

authorized_users = load_authorized()
extra_admins = set(load_json(ADMINS_FILE, []))
settings = load_json(SETTINGS_FILE, {
    "welcome": "🔍 <b>Bienvenue</b>\n\nEnvoyez un <b>numéro de téléphone</b> ou un <b>nom</b>\npour lancer une recherche."
})

def touch_user(user):
    uid = str(user.id)
    if uid not in authorized_users:
        return
    authorized_users[uid]["last_seen"] = datetime.utcnow().isoformat(timespec="seconds")
    authorized_users[uid]["username"] = user.username
    authorized_users[uid]["first_name"] = user.first_name
    save_authorized(authorized_users)

def save_extra_admins():
    save_json(ADMINS_FILE, list(extra_admins))

def save_settings():
    save_json(SETTINGS_FILE, settings)

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

def normalize_name(*parts):
    text = " ".join(str(p) for p in parts if p and not pd.isna(p))
    text = re.sub(r"[^a-zA-Z0-9àâäéèêëïîôùûüçÀÂÄÉÈÊËÏÎÔÙÛÜÇ\s]", " ", text)
    return " ".join(text.lower().split())

def detect_separator(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        first = f.readline()
    if not first:
        return ","
    counts = {"|": first.count("|"), ";": first.count(";"), ",": first.count(","), "\t": first.count("\t")}
    return max(counts, key=counts.get) if max(counts.values()) > 0 else ","

def find_columns(dataframe):
    cols_lower = {str(c).lower().strip(): c for c in dataframe.columns}
    mapping = {}
    phone_keywords = ["téléphone", "telephone", "phone", "tel", "mobile", "numero", "numéro", "gsm", "portable"]
    for col_lower, orig in cols_lower.items():
        if any(k in col_lower for k in phone_keywords):
            mapping["phone"] = orig
            break
    for k in ["nom", "name", "lastname"]:
        if k in cols_lower:
            mapping["nom"] = cols_lower[k]
            break
    for k in ["prénom", "prenom", "firstname"]:
        if k in cols_lower:
            mapping["prenom"] = cols_lower[k]
            break
    for k in ["adresse", "address"]:
        if k in cols_lower:
            mapping["adresse"] = cols_lower[k]
            break
    return mapping

# ==================== HELPERS ====================
def is_admin(user_id):
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return False
    return uid in ADMIN_IDS or str(uid) in extra_admins or str(user_id) in extra_admins

def is_authorized(user_id):
    return str(user_id) in authorized_users or is_admin(user_id)

async def animate_message(message, steps, delay=0.3):
    for text in steps:
        try:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(delay)
        except Exception:
            pass

def looks_like_phone(text):
    digits = re.sub(r"\D", "", text)
    return len(digits) >= 6 and len(digits) >= len(text) * 0.6

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if not is_authorized(user_id):
        await update.message.reply_text(
            "🔒 <b>Accès restreint</b>\n"
            "Ce bot est privé.\n\n"
            "Envoyez votre clé secrète pour continuer.",
            parse_mode=ParseMode.HTML
        )
        return

    touch_user(user)
    await update.message.reply_text(settings.get("welcome", "✅ Bienvenue"), parse_mode=ParseMode.HTML)

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
            "✅ <b>Clé acceptée</b>\n\n"
            "Accès débloqué. Vous n’aurez plus à la ressaisir.\n\n"
            + settings.get("welcome", "Envoyez un numéro ou un nom."),
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            "❌ <b>Clé invalide</b>\n\nVérifiez et réessayez.",
            parse_mode=ParseMode.HTML
        )

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "🔐 <b>Accès requis</b>\n\nEntrez d’abord votre clé secrète.",
            parse_mode=ParseMode.HTML
        )
        return

    touch_user(user)

    if get_db_count() == 0:
        await update.message.reply_text(
            "⚠️ <b>Base vide</b>\n\nAucun fichier de contacts n’a été chargé.",
            parse_mode=ParseMode.HTML
        )
        return

    query = update.message.text.strip()
    if not query:
        return

    msg = await update.message.reply_text("🔍 <i>Recherche en cours…</i>", parse_mode=ParseMode.HTML)
    await animate_message(
        msg,
        ["🔍 <i>Recherche en cours…</i>", "📂 <i>Consultation…</i>", "✨ <i>Finalisation…</i>"],
        delay=0.25
    )

    try:
        if looks_like_phone(query):
            phone_norm = normalize_phone(query)
            digits = re.sub(r"\D", "", phone_norm)
            phone_query = digits[-9:] if len(digits) >= 9 else digits
            if len(phone_query) < 6:
                await msg.edit_text("❌ <b>Numéro trop court</b>\n\nMinimum 6 chiffres.", parse_mode=ParseMode.HTML)
                return
            results = search_by_phone(phone_query, limit=5)
            search_type = "phone"
        else:
            name_q = normalize_name(query)
            if len(name_q) < 2:
                await msg.edit_text("❌ <b>Nom trop court</b>", parse_mode=ParseMode.HTML)
                return
            results = search_by_name(name_q, limit=5)
            search_type = "name"

        log_search(user_id, query, search_type, len(results))
    except Exception as e:
        await msg.edit_text(f"❌ <b>Erreur</b>\n\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)
        return

    if not results:
        await msg.edit_text(
            "❌ <b>Aucun résultat</b>\n\nAucun contact trouvé.",
            parse_mode=ParseMode.HTML
        )
        return

    text = ""
    for row in results:
        text += "📋 <b>Fiche contact</b>\n"
        text += "──────────────────\n"
        for col, value in row.items():
            if value is None or str(value).strip() in ("", "nan"):
                continue
            clean = str(value).replace(".0", "").strip()
            col_lower = str(col).lower().strip()
            if any(x in col_lower for x in ["nom", "name", "lastname"]):
                emoji = "👤"
            elif any(x in col_lower for x in ["prénom", "prenom", "firstname"]):
                emoji = "✨"
            elif any(x in col_lower for x in ["adresse", "address"]):
                emoji = "📍"
            elif any(x in col_lower for x in ["code_postal", "code postal", "cp", "postal"]):
                emoji = "📮"
            elif any(x in col_lower for x in ["ville", "city"]):
                emoji = "🏙️"
            elif any(x in col_lower for x in ["tel", "phone", "mobile", "numero", "numéro", "téléphone"]):
                emoji = "📞"
            elif any(x in col_lower for x in ["email", "mail"]):
                emoji = "✉️"
            else:
                emoji = "•"
            text += f"{emoji} <b>{col}</b>  {clean}\n"
        text += "──────────────────\n\n"
    await msg.edit_text(text.strip(), parse_mode=ParseMode.HTML)

# ==================== ADMIN ====================
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            f"⛔ Accès refusé.\nTon ID : <code>{user_id}</code>\n"
            f"Admins configurés : <code>{ADMIN_IDS}</code>",
            parse_mode=ParseMode.HTML
        )
        return
    count = get_db_count()
    users_count = len(authorized_users)
    total_s, today_s = get_stats()
    text = (
        "📊 <b>Panel Admin</b>\n\n"
        f"📇 Contacts : <b>{count}</b>\n"
        f"👥 Utilisateurs : <b>{users_count}</b>\n"
        f"🔍 Recherches totales : <b>{total_s}</b>\n"
        f"📅 Aujourd’hui : <b>{today_s}</b>\n"
        f"🛡️ Admins supplémentaires : <b>{len(extra_admins)}</b>\n\n"
        "<b>Commandes</b>\n"
        "/users — Liste utilisateurs\n"
        "/remove ID — Retirer un utilisateur\n"
        "/addadmin ID — Ajouter un admin\n"
        "/logs — Dernières recherches\n"
        "/setwelcome — Changer le message d’accueil\n"
        "/status — Statut rapide\n"
        "/reset — Vider la base"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not authorized_users:
        await update.message.reply_text("Aucun utilisateur autorisé.")
        return
    lines = ["👥 <b>Utilisateurs autorisés</b>\n"]
    for uid, info in list(authorized_users.items())[:25]:
        name = info.get("first_name") or "—"
        uname = f"@{info['username']}" if info.get("username") else ""
        last = info.get("last_seen") or "jamais"
        lines.append(f"<code>{uid}</code>  {name} {uname}\n  Dernière : {last}")
    if len(authorized_users) > 25:
        lines.append(f"\n… et {len(authorized_users)-25} autres")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage : /remove ID", parse_mode=ParseMode.HTML)
        return
    uid = context.args[0]
    if uid in authorized_users:
        del authorized_users[uid]
        save_authorized(authorized_users)
        await update.message.reply_text(f"✅ Utilisateur <code>{uid}</code> retiré.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Utilisateur non trouvé.")

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:  # seulement les admins d’origine
        await update.message.reply_text("⛔ Réservé aux administrateurs principaux.")
        return
    if not context.args:
        await update.message.reply_text("Usage : /addadmin ID", parse_mode=ParseMode.HTML)
        return
    uid = context.args[0]
    extra_admins.add(uid)
    save_extra_admins()
    await update.message.reply_text(f"✅ Admin ajouté : <code>{uid}</code>", parse_mode=ParseMode.HTML)

async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = get_recent_logs(12)
    if not rows:
        await update.message.reply_text("Aucun log pour le moment.")
        return
    lines = ["📜 <b>Dernières recherches</b>\n"]
    for uid, query, stype, res, ts in rows:
        lines.append(f"<code>{ts[11:16]}</code> {stype} — {query[:30]} ({res}) — {uid}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Usage : /setwelcome Votre nouveau message\n\n"
            "Le HTML est supporté (<b>, <i>…)."
        )
        return
    settings["welcome"] = text
    save_settings()
    await update.message.reply_text("✅ Message de bienvenue mis à jour.", parse_mode=ParseMode.HTML)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    count = get_db_count()
    total_s, today_s = get_stats()
    await update.message.reply_text(
        f"📊 <b>Statut</b>\n\n"
        f"Contacts : <b>{count}</b>\n"
        f"Utilisateurs : <b>{len(authorized_users)}</b>\n"
        f"Recherches : <b>{total_s}</b> (aujourd’hui {today_s})",
        parse_mode=ParseMode.HTML
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    clear_db()
    await update.message.reply_text("🗑️ <b>Base réinitialisée</b>\n\nDonnées et logs supprimés.", parse_mode=ParseMode.HTML)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ <b>Accès refusé</b>\n\nRéservé aux administrateurs.", parse_mode=ParseMode.HTML)
        return

    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("❌ <b>Format invalide</b>\n\nSeuls les .csv sont acceptés.", parse_mode=ParseMode.HTML)
        return

    temp_file = "temp_upload.csv"
    file = await doc.get_file()
    await file.download_to_drive(temp_file)

    try:
        sep = detect_separator(temp_file)
        valid_phones = 0
        inserted_total = 0
        phone_col_name = "?"
        for chunk in pd.read_csv(temp_file, sep=sep, engine='python', on_bad_lines='skip', chunksize=20000):
            mapping = find_columns(chunk)
            if "phone" not in mapping:
                continue
            phone_col_name = mapping["phone"]
            rows_to_insert = []
            for _, row in chunk.iterrows():
                raw_phone = row.get(mapping["phone"], "")
                phone_norm = normalize_phone(raw_phone)
                digits = re.sub(r"\D", "", phone_norm)
                phone_key = digits[-9:] if len(digits) >= 9 else ""

                nom = row.get(mapping.get("nom", ""), "")
                prenom = row.get(mapping.get("prenom", ""), "")
                name_key = normalize_name(nom, prenom)

                data = {col: (None if pd.isna(val) else str(val).replace(".0", "").strip()) for col, val in row.items()}
                rows_to_insert.append((phone_key, name_key, json.dumps(data, ensure_ascii=False)))
                if phone_key:
                    valid_phones += 1
            inserted = insert_contacts(rows_to_insert)
            inserted_total += inserted
            del chunk, rows_to_insert
            gc.collect()

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
        skipped = max(0, valid_phones - inserted_total)
        settings["last_upload"] = datetime.utcnow().isoformat(timespec="seconds")
        save_settings()
        await update.message.reply_text(
            f"✅ <b>Base mise à jour</b>\n\n"
            f"📊 {count} contacts au total\n"
            f"➕ {inserted_total} nouveaux ajoutés\n"
            f"📞 {valid_phones} numéros valides traités\n"
            f"🔄 {skipped} doublons ignorés\n"
            f"Colonne : <code>{phone_col_name}</code>\n"
            f"Séparateur : <code>{sep}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ <b>Erreur d’import</b>\n\n<code>{str(e)}</code>", parse_mode=ParseMode.HTML)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        gc.collect()

# ==================== MAIN ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("remove", remove_user))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("setwelcome", set_welcome))
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
