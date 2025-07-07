import json
import aiohttp
import asyncio
import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

CONFIG_FILE = "dane_uzytkownikow.json"

notified = {}

file_lock = asyncio.Lock()

async def load_data():
    """Wczytuje strukturę danych z pliku JSON. Jeśli plik nie istnieje – tworzy nową bazę."""
    async with file_lock:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
            await save_data(data)
        return data

async def save_data(data):
    """Zapisuje strukturę danych do pliku JSON."""
    async with file_lock:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def ensure_user(data, user_id):
    """
    Upewnia się, że dla danego użytkownika istnieje wpis z listą URL-i do monitorowania.
    Jeśli nie – tworzy nowy.
    Jeśli wpis istnieje, ale klucz "urls" zawiera dane w postaci słownika,
    migruje go do listy (przyjmując, że klucze słownika to dodane URL-e).
    """
    if user_id not in data:
        data[user_id] = {"urls": []}
    else:
        if "urls" not in data[user_id]:
            data[user_id]["urls"] = []
        elif isinstance(data[user_id]["urls"], dict):
            data[user_id]["urls"] = list(data[user_id]["urls"].keys())
    return data

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicjalizuje konfigurację użytkownika."""
    user_id = str(update.effective_chat.id)
    data = await load_data()
    data = ensure_user(data, user_id)
    await save_data(data)
    await update.message.reply_text("Witaj! Aby poznać komendy, wpisz /pomoc.")

async def pomoc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wyświetla listę dostępnych komend."""
    help_text = (
        "Lista komend:\n"
        "/dodaj_url <URL> - Dodaje URL do monitorowania.\n"
        "/usun_url <URL> - Usuwa URL z monitorowania.\n"
        "/lista - Wyświetla dodane URL-e.\n"
    )
    await update.message.reply_text(help_text)

async def dodaj_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dodaje URL do listy monitorowanych.
    Użycie: /dodaj_url <URL>
    """
    user_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Użycie: /dodaj_url <URL>")
        return
    url = " ".join(context.args).strip()
    data = await load_data()
    data = ensure_user(data, user_id)
    if url.lower() in (x.lower() for x in data[user_id]["urls"]):
        await update.message.reply_text("Ten URL został już dodany.")
    else:
        data[user_id]["urls"].append(url)
        await update.message.reply_text(f"Dodano URL: {url}")
    await save_data(data)

async def usun_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usuwa URL z listy monitorowanych.
    Użycie: /usun_url <URL>
    """
    user_id = str(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Użycie: /usun_url <URL>")
        return
    url = " ".join(context.args).strip()
    data = await load_data()
    if user_id not in data or not data[user_id]["urls"]:
        await update.message.reply_text("Brak dodanych URL-i.")
        return
    if url.lower() not in (x.lower() for x in data[user_id]["urls"]):
        await update.message.reply_text("Nie znaleziono takiego URL.")
    else:
        data[user_id]["urls"] = [x for x in data[user_id]["urls"] if x.lower() != url.lower()]
        await update.message.reply_text(f"Usunięto URL: {url}")
        if user_id in notified and url in notified[user_id]:
            del notified[user_id][url]
    await save_data(data)

async def lista(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.effective_chat.id)
    data = await load_data()
    if user_id not in data or not data[user_id]["urls"]:
        await update.message.reply_text("Nie dodano jeszcze żadnych URL-i.")
        return
    message = "Dodane URL-e:\n" + "\n".join(data[user_id]["urls"])
    await update.message.reply_text(message)

async def monitor_callback(context: ContextTypes.DEFAULT_TYPE):
    data = await load_data()
    index_code = ""
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index_code = f.read()
        except Exception as e:
            logging.error(f"Błąd przy odczytywaniu {index_path}: {e}")
    else:
        logging.error(f"Plik {index_path} nie został znaleziony!")
    
    timeout = aiohttp.ClientTimeout(total=10)
    danger_snippets = [
        "Niebezpieczna strona",
        "Uwaga! Ta strona stanowi zagrożenie"
    ]

    async with aiohttp.ClientSession() as session:
        for user_id, user_data in data.items():
            for url in user_data.get("urls", []):
                page_content = ""
                if url.lower().startswith("http"):
                    try:
                        async with session.get(url, timeout=timeout) as response:
                            page_content = await response.text()
                    except Exception as e:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"Błąd przy pobieraniu {url}: {e}"
                        )
                        continue
                else:
                    if os.path.exists(url):
                        try:
                            with open(url, "r", encoding="utf-8") as f:
                                page_content = f.read()
                        except Exception as e:
                            await context.bot.send_message(
                                chat_id=int(user_id),
                                text=f"Błąd przy odczytywaniu pliku {url}: {e}"
                            )
                            continue
                    else:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"Plik {url} nie został znaleziony."
                        )
                        continue

                sent_notification = notified.get(user_id, {}).get(url, False)
                if not page_content.strip():
                    if not sent_notification:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"!! CERT !!\nStrona {url} jest pusta (nie zawiera żadnej treści)!"
                        )
                        if user_id not in notified:
                            notified[user_id] = {}
                        notified[user_id][url] = True
                elif any(danger in page_content for danger in danger_snippets):
                    if not sent_notification:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"!! CERT !!\nStrona {url} zawiera ostrzeżenie:\n\n"
                                "Niebezpieczna strona\n"
                                "Osoby przeprowadzające atak na stronie, którą próbujesz otworzyć, mogą podstępem "
                                "nakłonić Cię do zainstalowania oprogramowania lub ujawnienia takich danych jak Twoje "
                                "hasła, numery telefonów czy numery kart kredytowych. Brave zdecydowanie zaleca "
                                "powrót do bezpieczeństwa. Więcej informacji o tym ostrzeżeniu.\n\n"
                                "Uwaga! Ta strona stanowi zagrożenie\n"
                                "Może ona wyłudzać dane osobowe, dane uwierzytelniające do kont bankowych lub serwisów "
                                "społecznościowych. W trosce o Twoje bezpieczeństwo dostawca internetu powstrzymał próbę "
                                "ataku poprzez stronę."
                            )
                        )
                        if user_id not in notified:
                            notified[user_id] = {}
                        notified[user_id][url] = True
                elif index_code and index_code in page_content:
                    if not sent_notification:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=f"!! CERT !!\nStrona {url} zawiera cert."
                        )
                        if user_id not in notified:
                            notified[user_id] = {}
                        notified[user_id][url] = True
                else:
                    if user_id in notified and url in notified[user_id]:
                        notified[user_id][url] = False

def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )
    app = ApplicationBuilder().token("INSERT HERE YOUR TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pomoc", pomoc))
    app.add_handler(CommandHandler("dodaj_url", dodaj_url))
    app.add_handler(CommandHandler("usun_url", usun_url))
    app.add_handler(CommandHandler("lista", lista))

    app.job_queue.run_repeating(monitor_callback, interval=60, first=0)
    app.run_polling()

if __name__ == "__main__":
    main()
