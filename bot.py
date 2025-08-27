import gspread
import os
from dotenv import load_dotenv
import logging
import google.generativeai as genai
from serpapi import GoogleSearch
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import uuid
import re
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode, ChatAction

load_dotenv()
# --- KONFIGURASI ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY") 
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")

# Timeouts untuk koneksi Telegram
TELEGRAM_CONNECT_TIMEOUT = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "20"))
TELEGRAM_READ_TIMEOUT = float(os.getenv("TELEGRAM_READ_TIMEOUT", "30"))

# --- KONFIGURASI SHEET ---
SHEET_NAMES = [
    "Villa, Hotel, Resort Sidemen",
    "Villa, Hotel, Resort Abang",
    "Villa, Hotel, Resort Amed"
]

# Kolom standar yang ada di semua sheet
COLUMN_HEADERS = [
    "Nama",
    "Jenis",
    "Lokasi",
    "Kecamatan",
    "Desa",
    "Tahun Terbangun",
    "Jumlah Kamar",
    "Contact Person",
    "Ulasan Review IT",
]

# --- SETUP LOGGING ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- INISIALISASI KONEKSI ---
try:
    gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
    spreadsheet = gc.open_by_key(SPREADSHEET_ID)
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Koneksi ke Google Sheets dan Gemini AI berhasil.")
except Exception as e:
    logger.error(f"Gagal saat inisialisasi: {e}")
    exit()

# ======================================================================
# BAGIAN 1: FUNGSI-FUNGSI NAVIGASI TOMBOL (TIDAK BERUBAH)
# ======================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton(f"📍 {name.split(' ').pop()}", callback_data=f"view_desas;{i}")] for i, name in enumerate(SHEET_NAMES)]
    # keyboard.append([InlineKeyboardButton("🔍 IT Review", callback_data="view_it_reviews")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Selamat datang!\n\nAnda bisa memilih area di bawah ini, mencari berdasarkan IT Review, atau langsung ajukan pertanyaan kepada saya (misal: 'cari info kontak Villa Damai').", 
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    parts = data.split(';')
    action = parts[0]
   
    if action == "view_areas":
        keyboard = [[InlineKeyboardButton(f"📍 {name.split(' ').pop()}", callback_data=f"view_desas;{i}")] for i, name in enumerate(SHEET_NAMES)]
        keyboard.append([InlineKeyboardButton("🔍 IT Review", callback_data="view_it_reviews")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Silakan pilih salah satu area atau IT Review:", reply_markup=reply_markup)
    elif action == "view_desas":
        sheet_index = int(parts[1])
        sheet = spreadsheet.worksheet(SHEET_NAMES[sheet_index])
        all_values = sheet.get_all_values()
        headers, data_rows = all_values[0], all_values[1:]
        try:
            desa_col_index = headers.index('Desa')
            unique_desas = sorted(list(set(row[desa_col_index] for row in data_rows if row[desa_col_index])))
            keyboard = [[InlineKeyboardButton(desa, callback_data=f"view_villas;{sheet_index};{desa}")] for desa in unique_desas]
            keyboard.append([InlineKeyboardButton("⬅️ Kembali", callback_data="view_areas")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(f"Silakan pilih desa di area *{SHEET_NAMES[sheet_index].split(' ').pop()}*:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await query.edit_message_text("Error: Kolom 'Desa' tidak ditemukan.")
    elif action == "view_villas":
        sheet_index, desa_name = int(parts[1]), parts[2]
        sheet = spreadsheet.worksheet(SHEET_NAMES[sheet_index])
        all_values = sheet.get_all_values()
        headers, data_rows = all_values[0], all_values[1:]
        try:
            nama_col_index, desa_col_index = headers.index('Nama'), headers.index('Desa')
            keyboard = [[InlineKeyboardButton(row[nama_col_index], callback_data=f"view_details;{sheet_index};{i}")] for i, row in enumerate(data_rows) if len(row) > desa_col_index and row[desa_col_index] == desa_name]
            keyboard.append([InlineKeyboardButton("⬅️ Kembali", callback_data=f"view_desas;{sheet_index}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(f"Properti di desa *{desa_name}*:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await query.edit_message_text("Error: Kolom 'Nama' atau 'Desa' tidak ditemukan.")
    elif action == "view_it_reviews":
        await query.edit_message_text("Silakan ketik kata kunci untuk review IT (misal: 'review IT wifi cepat'). Bot akan scan dan tampilkan hotel yang sesuai.")
    elif action == "view_details":
        sheet_index, row_index = int(parts[1]), int(parts[2])
        sheet = spreadsheet.worksheet(SHEET_NAMES[sheet_index])
        all_values = sheet.get_all_values()
        headers, data_rows = all_values[0], all_values[1:]
        try:
            row_data = data_rows[row_index]
            desa_name = row_data[headers.index('Desa')]
            response_text = "✅ *Detail Properti*\n\n"
            for header, value in zip(headers, row_data):
                if value:
                    if header.lower() == 'lokasi' and 'http' in value.lower():
                        addr, link = value[:value.lower().find('http')].strip(), value[value.lower().find('http'):].strip()
                        response_text += f"*{header}*: {addr}\n[Lihat di Google Maps]({link})\n"
                    else:
                        response_text += f"*{header}*: {value}\n"

            # Siapkan usulan pengisian untuk kolom yang kosong
            nama = row_data[headers.index('Nama')]
            desa = row_data[headers.index('Desa')]
            search_query = f"{nama} {desa} Bali"

            # Peta kolom ke strategi pencarian
            proposed_updates = {}

            def is_empty(col_name: str) -> bool:
                try:
                    return not row_data[headers.index(col_name)].strip()
                except ValueError:
                    return True

            # Contact Person (prioritas Google Maps)
            if is_empty('Contact Person'):
                contact_info = search_google_maps(search_query + " contact person")
                # Ambil nomor telepon jika ada
                m = re.search(r"Telepon:\s*([^\n]+)", contact_info or "")
                if m:
                    proposed_updates['Contact Person'] = m.group(1).strip()
                elif contact_info and contact_info != "Tidak ada informasi ditemukan di Google Maps.":
                    proposed_updates['Contact Person'] = contact_info[:300]

            # Jumlah Kamar
            if is_empty('Jumlah Kamar'):
                rooms_info = search_the_web(search_query + " jumlah kamar OR number of rooms OR room count")
                # ekstrak angka kamar
                m = re.search(r"(\d{1,3})\s*(kamar|rooms|room)", rooms_info or "", re.I)
                if m:
                    proposed_updates['Jumlah Kamar'] = m.group(1)
                elif rooms_info:
                    proposed_updates['Jumlah Kamar'] = clean_text_snippet(rooms_info)[:500]

            # Lokasi (alamat singkat)
            if is_empty('Lokasi'):
                maps_info = search_google_maps(search_query)
                m = re.search(r"Alamat:\s*([^\n]+)", maps_info or "")
                if m:
                    proposed_updates['Lokasi'] = m.group(1).strip()

            # Tahun Terbangun (cari pola tahun)
            if is_empty('Tahun Terbangun'):
                year_info = search_the_web(search_query + " tahun dibangun OR tahun terbangun OR built in year")
                m = re.search(r"(19\d{2}|20\d{2})", year_info or "")
                if m:
                    proposed_updates['Tahun Terbangun'] = m.group(1)

            # Kecamatan (coba dari alamat)
            if is_empty('Kecamatan'):
                maps_info2 = search_google_maps(search_query)
                addr_match = re.search(r"Alamat:\s*([^\n]+)", maps_info2 or "")
                if addr_match:
                    addr = addr_match.group(1)
                    # Heuristik sederhana: cari kata 'Kecamatan' atau 'Kec.'
                    kec_match = re.search(r"Kecamatan\s+([^,]+)|Kec\.?\s*([^,]+)", addr, re.I)
                    if kec_match:
                        proposed_updates['Kecamatan'] = (kec_match.group(1) or kec_match.group(2)).strip()

            # Jenis (coba dari hasil Maps type atau inferensi nama)
            if is_empty('Jenis'):
                maps_info3 = search_google_maps(search_query)
                # Coba deteksi kata kunci umum
                jenis = None
                for kw in ["Villa", "Hotel", "Resort", "Guesthouse", "Homestay", "Hostel"]:
                    if re.search(rf"\b{kw}\b", nama, re.I):
                        jenis = kw
                        break
                if not jenis:
                    if "villa" in (maps_info3 or "").lower():
                        jenis = "Villa"
                    elif "hotel" in (maps_info3 or "").lower():
                        jenis = "Hotel"
                    elif "resort" in (maps_info3 or "").lower():
                        jenis = "Resort"
                if jenis:
                    proposed_updates['Jenis'] = jenis

            # Ulasan Review IT (khusus layanan IT, multi-bahasa)
            if is_empty('Ulasan Review IT'):
                it_query = (
                    search_query
                    + " review reviews internet wifi wi-fi jaringan network connection connectivity bandwidth signal kecepatan speed lambat slow kencang fast koneksi IT remote work digital nomad streaming video call zoom latency ping Mbps mb/s fiber fibre ethernet 4G 5G LTE"
                )
                raw_reviews = search_the_web(it_query)
                filtered = filter_it_reviews(raw_reviews or "")
                if filtered:
                    refined = ai_refine_it_reviews(filtered) or filtered
                    refined = clean_text_snippet(refined)
                    sentences = re.split(r"(?<=[.!?])\s+", refined)
                    proposed_updates['Ulasan Review IT'] = " ".join(sentences[:3])

            if proposed_updates:
                response_text += "\n💡 *Usulan pengisian data kosong*:\n"
                for k, v in proposed_updates.items():
                    response_text += f"- *{k}*: {v}\n"

                # Simpan usulan di user_data dengan token kecil untuk konfirmasi
                token = f"save_{uuid.uuid4().hex[:10]}"
                context.user_data[token] = {
                    'sheet_index': sheet_index,
                    'row_index': row_index,
                    'sheet_name': SHEET_NAMES[sheet_index],
                    'nama': nama,
                    'desa': desa,
                    'updates': proposed_updates,
                }
                keyboard = [
                    [InlineKeyboardButton("💾 Simpan usulan", callback_data=f"confirm_save;{token}")],
                    [InlineKeyboardButton("❌ Abaikan", callback_data=f"cancel_save;{token}")],
                    [InlineKeyboardButton("⬅️ Kembali", callback_data=f"view_villas;{sheet_index};{desa_name}")],
                ]
            else:
                keyboard = [[InlineKeyboardButton("⬅️ Kembali", callback_data=f"view_villas;{sheet_index};{desa_name}")]]

            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(response_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        except (ValueError, IndexError) as e:
            logger.error(f"Error saat ambil detail: {e}")
            await query.edit_message_text("Error: Gagal mengambil detail data.")
    elif action == "confirm_save":
        token = parts[1] if len(parts) > 1 else None
        pending = context.user_data.get(token)
        if not pending:
            await query.edit_message_text("Tidak ada data usulan untuk disimpan atau sudah kadaluarsa.")
            return
        try:
            save_additional_data(pending['sheet_name'], pending['nama'], pending['desa'], pending['updates'])
            del context.user_data[token]
            await query.edit_message_text("✅ Data berhasil disimpan ke spreadsheet.")
        except Exception as e:
            logger.error(f"Gagal menyimpan data: {e}")
            await query.edit_message_text("❌ Gagal menyimpan data.")
    elif action == "cancel_save":
        token = parts[1] if len(parts) > 1 else None
        if token in context.user_data:
            del context.user_data[token]
        await query.edit_message_text("❎ Penyimpanan dibatalkan oleh pengguna.")


# ======================================================================
# BAGIAN 2: FUNGSI-FUNGSI AI AGENT DENGAN KEMAMPUAN PENCARIAN
# ======================================================================

def get_all_data_as_context():
    """Mengambil semua data dari semua sheet dan memformatnya untuk AI."""
    context_string = ""
    for sheet_name in SHEET_NAMES:
        try:
            sheet = spreadsheet.worksheet(sheet_name)
            records = sheet.get_all_records()
            context_string += f"Data dari area {sheet_name.split(' ').pop()}:\n{str(records)}\n\n"
        except gspread.exceptions.WorksheetNotFound:
            logger.warning(f"Sheet '{sheet_name}' tidak ditemukan.")
    return context_string

def search_the_web(query: str) -> str:
    """Fungsi yang menjalankan pencarian Google Web menggunakan SerpApi."""
    try:
        params = {"q": query, "api_key": SERPAPI_API_KEY, "engine": "google", "gl": "id", "hl": "id"}
        search = GoogleSearch(params)
        results = search.get_dict()
        snippets = [res.get("snippet", "") for res in results.get("organic_results", [])[:5] if res.get("snippet")]
        if "answer_box" in results: snippets.append(results["answer_box"].get("snippet", ""))
        return "\n".join(snippets) if snippets else "Tidak ada hasil pencarian web yang relevan."
    except Exception as e:
        logger.error(f"Error saat pencarian web: {e}")
        return "Kesalahan saat mencari di internet."

def search_google_maps(query: str) -> str:
    """Fungsi yang menjalankan pencarian Google Maps menggunakan SerpApi."""
    try:
        params = {"q": query, "api_key": SERPAPI_API_KEY, "engine": "google_maps", "gl": "id", "hl": "id"}
        search = GoogleSearch(params)
        results = search.get_dict()
        if "local_results" in results and results["local_results"]:
            place = results["local_results"][0]
            info = [
                f"Nama: {place.get('title', 'N/A')}",
                f"Alamat: {place.get('address', 'N/A')}",
                f"Telepon: {place.get('phone', 'N/A')}",
                f"Website: {place.get('website', 'N/A')}",
                f"Rating: {place.get('rating', 'N/A')} ({place.get('reviews', 0)} ulasan)",
            ]
            return "\n".join(info)
        return "Tidak ada informasi ditemukan di Google Maps."
    except Exception as e:
        logger.error(f"Error saat pencarian Google Maps: {e}")
        return "Kesalahan saat mencari di Google Maps."

def filter_it_reviews(text: str) -> str:
    """Ambil hanya kalimat yang berkaitan dengan layanan IT (internet/wifi/jaringan)."""
    if not text:
        return ""
    it_keywords = [
    # Indonesian
    r"wifi", r"wi-?fi", r"internet", r"jaringan", r"bandwidth", r"kecepatan", r"koneksi",
    r"fiber", r"sinyal", r"router", r"modem", r"IT", r"telekomunikasi", r"hotspot",
    r"lambat", r"kencang", r"stabil", r"putus", r"zoom", r"video call", r"Mbps|MB/s",
    # English
    r"network", r"connection", r"connectivity", r"signal", r"latency", r"ping",
    r"fast", r"slow", r"stable", r"unstable", r"drop(s|ped)?", r"zoom", r"stream(ing)?",
    r"video\s*call", r"work from home", r"remote work", r"digital nomad",
    r"fibre|fiber|ethernet|broadband|wi[-\s]?fi", r"4G|5G|LTE",
    ]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if any(re.search(k, s, re.I) for k in it_keywords)]
    return " ".join(kept).strip()

def clean_text_snippet(text: str) -> str:
    """Bersihkan snippet agar tidak terlihat terpotong dengan '...' dan rapikan spasi."""
    if not text:
        return ""
    cleaned = re.sub(r"\.{3,}|…", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def ai_refine_it_reviews(text: str) -> str:
    """Gunakan AI untuk mengekstrak dan merangkum poin terkait IT dari teks multi-bahasa."""
    try:
        if not text:
            return ""
        prompt = (
            "Anda adalah asisten yang mengekstrak ulasan terkait layanan IT (WiFi/internet/network/connection/bandwidth/latency/signal) dari teks multi-bahasa. "
            "Ambil hanya kalimat yang relevan IT, lalu rangkum singkat (1-5 kalimat), jelas dan faktual. Jangan sertakan link atau informasi yang tidak relevan. Teks:\n\n" + text
        )
        model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME)
        resp = model.generate_content(prompt)
        refined = (resp.text or "").strip()
        return clean_text_snippet(refined)
    except Exception as e:
        logger.warning(f"AI refine IT reviews gagal, gunakan fallback regex. Error: {e}")
        return clean_text_snippet(text)

async def handle_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_question = update.message.text.lower()
    if 'review it' in user_question:
        keyword = user_question.replace('review it', '').strip()
        await scan_it_reviews(update, keyword)
        return
    
    """Menangani pertanyaan pengguna dengan model AI yang bisa menggunakan alat pencarian."""
    user_question = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    spreadsheet_data = get_all_data_as_context()
    if not spreadsheet_data:
        await update.message.reply_text("Maaf, database tidak dapat diakses.")
        return

    # Definisikan DUA alat yang bisa digunakan oleh AI
    tools = [{"function_declarations": [
        {
            "name": "search_google_maps",
            "description": "Gunakan ini SEBAGAI PRIORITAS untuk mencari info kontak, nomor telepon, alamat, atau website resmi.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Nama properti dan lokasinya. Contoh: 'Villa Damai Sidemen'"}}, "required": ["query"]}
        },
        {
            "name": "search_the_web",
            "description": "Gunakan ini untuk mencari informasi subjektif seperti ulasan pelanggan dari Agoda, Booking.com, dll.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Kueri pencarian spesifik. Contoh: 'ulasan Villa Damai Sidemen booking.com'"}}, "required": ["query"]}
        },
        {
            "name": "search_traveloka",
            "description": "Gunakan ini untuk mencari informasi dari Traveloka seperti review, contact, jumlah kamar.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Kueri pencarian spesifik untuk Traveloka."}}, "required": ["query"]}
        },
        {
            "name": "search_agoda",
            "description": "Gunakan ini untuk mencari informasi dari Agoda seperti review, harga, dan fasilitas.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Kueri pencarian spesifik untuk Agoda."}}, "required": ["query"]}
        },
        {
            "name": "search_tiketcom",
            "description": "Gunakan ini untuk mencari informasi dari Tiket.com seperti review dan harga.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Kueri pencarian spesifik untuk Tiket.com."}}, "required": ["query"]}
        },
        {
            "name": "search_bookingcom",
            "description": "Gunakan ini untuk mencari informasi dari Booking.com seperti review dan fasilitas.",
            "parameters": {"type": "OBJECT", "properties": {"query": {"type": "STRING", "description": "Kueri pencarian spesifik untuk Booking.com."}}, "required": ["query"]}
        }
    ]}]

    model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME, tools=tools)
    chat = model.start_chat()
    prompt = f"""Anda adalah AI Agent properti di Bali. Jawab berdasarkan data spreadsheet dulu. Jika data tidak ada atau kosong, gunakan alat yang sesuai.
    - Untuk KONTAK, ALAMAT, TELEPON -> Gunakan `search_google_maps`.
    - Untuk ULASAN PELANGGAN -> Gunakan `search_the_web`.
    - Untuk data dari Traveloka (review, contact, dll) -> Gunakan `search_traveloka`.
    - Untuk data dari Agoda (review, harga, fasilitas) -> Gunakan `search_agoda`.
    - Untuk data dari Tiket.com (review, harga) -> Gunakan `search_tiketcom`.
    - Untuk data dari Booking.com (review, fasilitas) -> Gunakan `search_bookingcom`.
    
    --- DATA SPREADSHEET ---
    {spreadsheet_data}
    --- AKHIR DATA ---

    Pertanyaan Pengguna: "{user_question}"
    """

    try:
        response = await chat.send_message_async(prompt)
        response_part = response.parts[0]

        while response_part.function_call:
            function_call = response_part.function_call
            tool_name = function_call.name
            query = function_call.args['query']
            search_result = ""

            if tool_name == "search_google_maps":
                logger.info(f"AI -> Google Maps: '{query}'")
                search_result = search_google_maps(query)
            elif tool_name == "search_the_web":
                logger.info(f"AI -> Google Web: '{query}'")
                search_result = search_the_web(query)
            elif tool_name == "search_traveloka":
                logger.info(f"AI -> Traveloka Search: '{query}'")
                search_result = search_the_web(f"site:traveloka.com {query}")
            elif tool_name == "search_agoda":
                logger.info(f"AI -> Agoda Search: '{query}'")
                search_result = search_the_web(f"site:agoda.com {query}")
            elif tool_name == "search_tiketcom":
                logger.info(f"AI -> Tiket.com Search: '{query}'")
                search_result = search_the_web(f"site:tiket.com {query}")
            elif tool_name == "search_bookingcom":
                logger.info(f"AI -> Booking.com Search: '{query}'")
                search_result = search_the_web(f"site:booking.com {query}")
            
            if search_result:
                response = await chat.send_message_async(
                    {"function_response": {"name": tool_name, "response": {"result": search_result}}}
                )
                response_part = response.parts[0]
            else:
                break
        
        await update.message.reply_text(response.text)
    except Exception as e:
        logger.error(f"Error saat interaksi dengan Gemini Agent: {e}")
        await update.message.reply_text("Maaf, terjadi kesalahan pada AI Agent.")

async def scan_it_reviews(update: Update, keyword: str) -> None:
    await update.message.reply_text("Sedang scanning review IT...")
    all_hotels = []
    for sheet_name in SHEET_NAMES:
        sheet = spreadsheet.worksheet(sheet_name)
        records = sheet.get_all_records()
        for rec in records:
            nama = rec.get('Nama', '')
            desa = rec.get('Desa', '')
            if nama and desa:
                query = f"{nama} {desa} Bali review internet wifi jaringan IT {keyword}"
                review = search_the_web(query)
                filtered = filter_it_reviews(review or "")
                if filtered and (not keyword or keyword.lower() in filtered.lower()):
                    refined = ai_refine_it_reviews(filtered) or filtered
                    snippet = clean_text_snippet(refined)
                    # ambil 2-3 kalimat saja agar padat
                    sents = re.split(r"(?<=[.!?])\s+", snippet)
                    display = " ".join(sents[:3])
                    all_hotels.append(f"• {nama} ({desa}): {display}")
    if all_hotels:
        response = "Hotel dengan ulasan IT mengandung '{}':\n".format(keyword) + "\n".join(all_hotels[:10])  # Limit to 10
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("Tidak ditemukan hotel dengan review IT yang sesuai.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangkap error global agar tidak crash diam-diam dan beri log yang jelas."""
    logger.error("Unhandled exception", exc_info=context.error)
    # Opsional: beritahu user jika memungkinkan
    try:
        if hasattr(update, 'effective_chat') and update.effective_chat:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Maaf, koneksi sedang lambat. Coba lagi sebentar.")
    except Exception:
        pass

def save_additional_data(sheet_name: str, nama: str, desa: str, data: dict) -> None:
    try:
        sheet = spreadsheet.worksheet(sheet_name)
        all_values = sheet.get_all_values()
        headers = all_values[0]
        row_index = next((i+2 for i, row in enumerate(all_values[1:]) if row[headers.index('Nama')] == nama and row[headers.index('Desa')] == desa), None)
        if not row_index:
            return
        for key, value in data.items():
            if key not in headers:
                col = len(headers) + 1
                sheet.update_cell(1, col, key)
                headers.append(key)
            col_index = headers.index(key) + 1
            sheet.update_cell(row_index, col_index, value)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ======================================================================
# BAGIAN 3: FUNGSI UTAMA UNTUK MENJALANKAN BOT
# ======================================================================
def main() -> None:
    request = HTTPXRequest(connect_timeout=TELEGRAM_CONNECT_TIMEOUT, read_timeout=TELEGRAM_READ_TIMEOUT)
    application = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_query))
    application.add_error_handler(error_handler)
    logger.info("Bot dimulai...")
    application.run_polling()

if __name__ == "__main__":
    main()
