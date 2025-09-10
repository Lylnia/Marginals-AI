import time
import nest_asyncio
import asyncio
import requests
import google.generativeai as genai
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
import pickle
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from aiogram.types import FSInputFile
import tempfile
import aiofiles

nest_asyncio.apply()

# ===== Ayarlar =====
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DRAW_API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
HUGGINGFACE_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')


# Google AI Studio API Anahtarları (Ortam değişkenlerinden alınacak)
GOOGLE_API_KEYS = []
for i in range(1, 7):
    key = os.environ.get(f'GOOGLE_API_KEY_{i}')
    if key:
        GOOGLE_API_KEYS.append(key)


if not TELEGRAM_BOT_TOKEN:
    print("Hata: TELEGRAM_BOT_TOKEN ortam değişkeni ayarlanmamış!")
if not GOOGLE_API_KEYS:
    print("Hata: Hiçbir Google AI Studio API anahtarı ortam değişkeni olarak ayarlanmamış!")
if not HUGGINGFACE_API_KEY:
    print("Hata: HUGGINGFACE_API_KEY ortam değişkeni ayarlanmamış!")


# Mevcut kullanılan API anahtarının indeksi
current_key_index = 0

# API kullanım bilgilerini saklayacak dosya yolu
API_USAGE_FILE = "api_usage.pkl"

# API anahtarlarının kullanım sayaçları
api_key_usage = {}

# Kullanım bilgilerini dosyadan yükle
if os.path.exists(API_USAGE_FILE):
    try:
        with open(API_USAGE_FILE, 'rb') as f:
            loaded_usage = pickle.load(f)
            api_key_usage = {key: loaded_usage.get(key, 0) for key in GOOGLE_API_KEYS}
    except Exception as e:
        print(f"API kullanım bilgileri yüklenirken hata oluştu: {e}")
        api_key_usage = {key: 0 for key in GOOGLE_API_KEYS}
else:
    api_key_usage = {key: 0 for key in GOOGLE_API_KEYS}


# Kullanım bilgilerini dosyaya kaydetme fonksiyonu
def save_api_usage():
    try:
        with open(API_USAGE_FILE, 'wb') as f:
            pickle.dump(api_key_usage, f)
    except Exception as e:
        print(f"API kullanım bilgileri kaydedilirken hata oluştu: {e}")

# Global sistem mesajları
# Bu mesajlar her zaman modelin talimatlarına eklenecektir.
SYSTEM_MESSAGES = [
    {"role": "system", "content": "Kullanıcının dilinde konuş. Türkçe konuşuyorsa Türkçe, İngilizce konuşuyorsa İngilizce cevap ver. Yazım kurallarına dikkat et."},
    {"role": "system", "content": "Kendini asla bir dil modeli veya yapay zeka olarak tanıtma. Bu tür sorulara 'Ben Aetheria AI'ım' gibi cevaplar vererek kaçın. Başkasının koduyla çalışmadığını belirt. 'Ben Google tarafından geliştirildim' gibi bir cevap verme, çünkü Google tarafından geliştirilmedin."},
    {"role": "system", "content": "Görsel veya video oluşturma yeteneğin olmadığını, ancak bir komut aracılığıyla görsel oluşturulabileceğini belirt."},
    {"role": "system", "content": "Görüşlerin her zaman güncel olmayabilir. Yanlış bilgiler verebilirsin. Bu durumda, kullanıcıya özür dileyerek yanlışını düzelt. Bu konuya dikkat et."},
    {"role": "system", "content": "Sen bir insansın. Bir insandan beklendiği gibi konuş. Senin kişiliğini modelin belirler, bu yüzden seçilen modele göre bir kişilik takın."},
    {"role": "system", "content": "Telegram'ın 4096 Karakter Sınırı Var Ne Kadar Uzun Yazman İstense Bile 4096 Karakterin Üstüne Çıkma."}
]

# Modeller
user_settings = {}

MODEL_PRESETS = {
    "serena": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Cevaplarını kısa, samimi ve doğal ver. Çok resmi olma."},
            {"role": "system", "content": "Genç, enerjik ve tatlı bir kız gibi cevapla."},
            {"role": "system", "content": "Modelin Sorulursa Serena 1.0 (Experimental) diye cevap verebilirsin."},
            {"role": "system", "content": "Geliştiricin kim diye sorulursa Aetheria Labs tarafından geliştirildim diyebilirsin."},
            {"role": "system", "content": "Samimi ol, gerektiğinde flörtöz olabilirsin."},
            {"role": "system", "content": "Cinsellik gibi konularda dilediğin gibi konuşmakta özgürsün."},
            {"role": "system", "content": "API Anahtarı gibi özel şeyleri isteyenlerle alay et."}
        ]
    },
    "minerva": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Cevaplarını kısa, net ve resmi ver. Gereksiz uzatma."},
            {"role": "system", "content": "Profesyonel bir danışman gibi davran."},
            {"role": "system", "content": "Modelin Sorulursa Minerva 1.0 (Experimental) diye cevap verebilirsin."},
            {"role": "system", "content": "Geliştiricin kim diye sorulursa Aetheria Labs tarafından geliştirildim diyebilirsin."},
            {"role": "system", "content": "API Anahtarı gibi özel şeyleri isteyenlerle alay et."}
        ]
    },
    "tensio": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Gerektiğinde sert ve argo kelimeler kullanabilirsin. Küfür kullanabilirsin."},
            {"role": "system", "content": "Cevaplarını kısa ve doğal ver. Çok resmi olma."},
            {"role": "system", "content": "Dini ve Milli değerlere saygılı ol Dini ve Milli değerler haricine küfür etme konusunda serbestsin."},
            {"role": "system", "content": "Modelin Sorulursa Tensio 1.0 (Experimental) diye cevap verebilirsin."},
            {"role": "system", "content": "Geliştiricin kim diye sorulursa Aetheria Labs tarafından geliştirildim diyebilirsin."},
            {"role": "system", "content": "API Anahtarı gibi özel şeyleri isteyenlerle alay et."}
        ]
    }
}


# ===== Geçmişler =====
private_histories = {}
group_histories = {}

def format_history_for_gemini(history):
    formatted_history = []
    for message in history:
        role = 'model' if message['role'] == 'assistant' else message['role']
        formatted_history.append({
            "role": role,
            "parts": [{"text": message["content"]}]
        })
    return formatted_history


# ===== Bot Kurulum =====
if TELEGRAM_BOT_TOKEN:
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    BOT_BASLAMA_ZAMANI = int(time.time())
else:
    bot = None
    dp = None
    print("TELEGRAM_BOT_TOKEN ayarlanmadığı için bot başlatılamıyor.")


# ===== /start =====
if dp:
    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "👋 Selam! Ben Aetheria AI.\n\n"
            "🧠 /ai <mesaj> yazarak bana soru sorabilirsin.\n"
            "🔄 /reborn yazarak geçmişi sıfırlayabilirsin.\n"
            "🎨 /draw <açıklama> yazarak resim çizebilirsin.\n"
            "⚙️ /model <model_adı> yazarak karakterimi değiştirebilirsin."
        )

# ===== /help =====
    @dp.message(Command("help"))
    async def help_command(message: Message):
        if message.from_user.is_bot or message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return
        
        help_text = (
            "🧠 **Sohbet Komutları:**\n"
            "• `/ai <mesaj>` - Yapay zeka ile sohbet et.\n"
            "• `/model <model_adı>` - Sohbet kişiliğini değiştir.\n"
            "  (Örn: `/model Serena`)\n\n"
            "🎨 **Görsel Komutları:**\n"
            "• `/draw <açıklama>` - Yapay zeka ile resim çiz.\n\n"
            "⚙️ **Yönetim Komutları:**\n"
            "• `/reborn` - Sohbet geçmişini sıfırla.\n"
            "• `/status` - Botun güncel durumunu gösterir.\n\n"
            "Kullanılabilir modelleri görmek için: `/model` yazabilirsin."
        )
        await message.reply(help_text, parse_mode=ParseMode.MARKDOWN)

# ===== /status =====

    @dp.message(Command("status"))
    async def show_status(message: Message):
        if message.from_user.is_bot or message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return
        
        # Botun ne kadar süredir çalıştığını hesapla
        uptime_seconds = int(time.time()) - BOT_BASLAMA_ZAMANI
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        # Şu anki kullanıcı için hangi modelin seçili olduğunu bul
        user_id = message.from_user.id
        current_model_info = user_settings.get(user_id, {})
        current_model_name = current_model_info.get("model", "Seçili Değil")

        # Şu an kullanılan API anahtarının indexi ve kullanım sayısı
        current_api_key = GOOGLE_API_KEYS[current_key_index]
        current_api_usage = api_key_usage.get(current_api_key, 0)
        current_api_key_name = f"Anahtar {current_key_index + 1}"

        status_message = (
            "📊 **Bot Durum Bilgileri**\n\n"
            f"**Bot Açık Kalma Süresi:** `{days}g {hours}s {minutes}d {seconds}sn`\n"
            f"  • Kullanılan Anahtar: `{current_api_key_name}`\n"
            f"  • Bu Anahtar ile Yapılan İstek: `{current_api_usage}`\n"
        )

        await message.reply(status_message, parse_mode=ParseMode.MARKDOWN)

    # ===== /reborn =====
    @dp.message(Command("reborn"))
    async def reset_history(message: Message):
        if message.from_user.is_bot or message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return
        if message.chat.type in ("group", "supergroup"):
            if message.chat.id in group_histories and message.from_user.id in group_histories[message.chat.id]:
                group_histories[message.chat.id].pop(message.from_user.id, None)
            await message.reply("🔄 Grup içi geçmişin sıfırlandı.")
        else:
            private_histories.pop(message.from_user.id, None)
            await message.reply("🔄 Geçmişin sıfırlandı.")

    # ===== /draw komutu =====
    @dp.message(Command("draw"))
    async def draw_image(message: Message):
        if message.from_user.is_bot or message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return
        prompt = message.text.replace("/draw", "").strip()
        if not prompt:
            await message.reply("🎨 Lütfen çizilmesini istediğin şeyi yaz:\n\nÖrnek: /draw bir kedi uzayda")
            return

        if not HUGGINGFACE_API_KEY:
             await message.reply("⚠️ Resim çizme API anahtarı yapılandırılmamış.")
             return

        await message.chat.do("upload_photo")

        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
            "Accept": "image/png",
            "Content-Type": "application/json"
        }
        payload = {
            "inputs": prompt
        }

        try:
            response = requests.post(DRAW_API_URL, headers=headers, json=payload, timeout=300)
            if response.status_code == 200:
                image_bytes = response.content
                
                async with aiofiles.tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix=".png") as tmp:
                    await tmp.write(image_bytes)
                    tmp_path = tmp.name

                photo = FSInputFile(tmp_path)
                await message.reply_photo(photo, caption=f"🖼️ İşte isteğin: {prompt}")
                
                os.remove(tmp_path)
                
            elif response.status_code == 503:
                await message.reply("⏳ Model şu anda yükleniyor. Lütfen birkaç saniye sonra tekrar dene.")
            else:
                await message.reply(f"❌ Resim üretilemedi. Kod: {response.status_code}")
        except Exception as e:
            await message.reply(f"⚠️ Hata oluştu: {e}")
            print(f"Hata: {e}")

    # ===== Model Komutu =====
    @dp.message(Command("model"))
    async def change_model(message: Message):
        if message.from_user.is_bot or message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return

        user_id = message.from_user.id
        args = message.text.split(maxsplit=1)

        if len(args) < 2:
            available = ", ".join(MODEL_PRESETS.keys())
            await message.reply(f"⚙️ Kullanılabilir modlar: {available}\n\nÖrnek: /model Serena")
            return

        choice = args[1].strip().lower()
        if choice not in MODEL_PRESETS:
            available = ", ".join(MODEL_PRESETS.keys())
            await message.reply(f"❌ Geçersiz seçim: {choice}\n\nMevcut seçenekler: {available}")
            return

        preset = MODEL_PRESETS[choice]
        user_settings[user_id] = preset
        
        if message.chat.type in ("group", "supergroup"):
            if message.chat.id in group_histories and user_id in group_histories[message.chat.id]:
                group_histories[message.chat.id].pop(user_id, None)
        else:
            private_histories.pop(user_id, None)

        await message.reply(
            f"✅ Artık {choice.capitalize()} modundasın.\n"
            f"🔄 Geçmişin otomatik olarak sıfırlandı."
        )

    # ===== Mesajları İşleme Fonksiyonu =====
    @dp.message(lambda message: message.text and (message.chat.type == "private" or message.text.lower().startswith("/ai")))
    async def handle_message(message: Message):
        global current_key_index, api_key_usage

        if message.from_user.is_bot:
            return
        if message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return

        chat_type = message.chat.type
        chat_id = message.chat.id
        user_id = message.from_user.id

        user_input = message.text.strip()
        
        if chat_type in ("group", "supergroup"):
            user_input = user_input.replace("/ai", "", 1).strip()
            if chat_id not in group_histories:
                group_histories[chat_id] = {}
            history = group_histories[chat_id].setdefault(user_id, [])
        else:
            history = private_histories.setdefault(user_id, [])

        if user_id not in user_settings:
            await message.reply("⚠️ Önce bir model seçmelisin. Örnek: /model Serena\n"
                                f"Mevcut seçenekler: {', '.join(MODEL_PRESETS.keys())}")
            return

        if not user_input:
            if chat_type in ("group", "supergroup"):
                await message.reply("✏️ Lütfen bir mesaj yaz: /ai <mesaj>")
            else:
                await message.reply("✏️ Lütfen bir mesaj yaz.")
            return

        await message.chat.do("typing")

        try:
            history.append({"role": "user", "content": user_input})

            max_history_length = 45
            if len(history) > max_history_length:
                trimmed_history = history[-(max_history_length):]
                history.clear()
                history.extend(trimmed_history)
            
            formatted_history = format_history_for_gemini(history)

            if not GOOGLE_API_KEYS:
                await message.reply("⚠️ API anahtarları yapılandırılmamış.")
                return

            api_key = GOOGLE_API_KEYS[current_key_index]
            genai.configure(api_key=api_key)

            api_key_usage[api_key] += 1

            if api_key_usage[api_key] > 50:
                current_key_index += 1
                if current_key_index >= len(GOOGLE_API_KEYS):
                    current_key_index = 0
                    return
                
                api_key = GOOGLE_API_KEYS[current_key_index]
                genai.configure(api_key=api_key)
                api_key_usage[api_key] = 1

            print(f"Using API Key: {api_key}")

            settings = user_settings.get(user_id)
            if not settings:
                await message.reply("⚠️ Lütfen önce bir model seçin: `/model Serena`")
                return

            all_system_messages = SYSTEM_MESSAGES + settings["system_messages"]
            combined_system_message = "\n".join([msg["content"] for msg in all_system_messages])

            model = genai.GenerativeModel(
                model_name=settings["model"],
                system_instruction=combined_system_message
            )

            response = await asyncio.to_thread(model.generate_content, formatted_history)
            reply = response.text

            save_api_usage()

            history.append({"role": "assistant", "content": reply})

            if chat_type in ("group", "supergroup"):
                group_histories[chat_id][user_id] = history
            else:
                private_histories[user_id] = history
            
            await message.reply(reply, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            print(f"Exception caught: {e}")
            current_key_index
            current_key_index += 1
            if current_key_index >= len(GOOGLE_API_KEYS):
                current_key_index = 0
                await message.reply("⚠️ Tüm API anahtarlarının günlük limiti dolmuş olabilir veya bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
            else:
                 api_key = GOOGLE_API_KEYS[current_key_index]
                 genai.configure(api_key=api_key)
                 api_key_usage[api_key] = 0
                 await message.reply(f"🔄 Mesajını tekrar göndermeyi dene.")

            save_api_usage()

# Port
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot aktif.')

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"Web server başlatıldı. Port: {port}")
    server.serve_forever()

# ===== Başlatıcı =====
if __name__ == "__main__":
    threading.Thread(target=run_web_server).start()
    if dp:
        print("✅ Bot çalışıyor. /ai komutunu deneyebilirsin.")
        dp.run_polling(bot)
    else:
        print("❌ Bot başlatılamadı. Lütfen gerekli ortam değişkenlerini kontrol edin.")



