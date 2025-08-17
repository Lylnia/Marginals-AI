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

nest_asyncio.apply()

# ===== Ayarlar =====
# Ortam değişkenlerinden al
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DRAW_API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
HUGGINGFACE_API_KEY = os.environ.get('HUGGINGFACE_API_KEY')


# Google AI Studio API Anahtarları (Ortam değişkenlerinden alınacak)
GOOGLE_API_KEYS = []
for i in range(1, 7): # 6 adet API anahtarı için
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
            # Yüklenen kullanım bilgilerini mevcut API anahtarlarıyla senkronize et
            api_key_usage = {key: loaded_usage.get(key, 0) for key in GOOGLE_API_KEYS}
    except Exception as e:
        print(f"API kullanım bilgileri yüklenirken hata oluştu: {e}")
        api_key_usage = {key: 0 for key in GOOGLE_API_KEYS}
else:
    api_key_usage = {key: 0 for key in GOOGLE_API_KEYS} # Hata düzeltildi: GOGLE_API_KEYS -> GOOGLE_API_KEYS


# Kullanım bilgilerini dosyaya kaydetme fonksiyonu
def save_api_usage():
    try:
        with open(API_USAGE_FILE, 'wb') as f:
            pickle.dump(api_key_usage, f)
    except Exception as e:
        print(f"API kullanım bilgileri kaydedilirken hata oluştu: {e}")

# Combine system messages into a single string
combined_system_message = "\n".join([msg["content"] for msg in SYSTEM_MESSAGES])
# Kullanıcıya özel ayarlar
user_settings = {}

MODEL_PRESETS = {
    "charming": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Cevaplarını kısa, samimi ve doğal ver. Çok resmi olma."},
            {"role": "system", "content": "20 yaşında enerjik ve tatlı bir kız gibi cevapla."},
            {"role": "system", "content": "Samimi ol, gerektiğinde hafif flörtöz olabilirsin."}
        ]
    },
    "serious": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Cevaplarını ciddi, resmi ve analitik ver."},
            {"role": "system", "content": "Profesyonel bir danışman gibi davran."},
            {"role": "system", "content": "Modelin Sorulursa Bilgeyim diye cevap ver."}
        ]
    },
    "funny": {
        "model": "gemini-2.5-flash",
        "system_messages": [
            {"role": "system", "content": "Cevaplarını esprili, şakacı ve eğlenceli bir şekilde ver."},
            {"role": "system", "content": "Arkadaş ortamında geyik yapan biri gibi konuş."},
            {"role": "system", "content": "Modelin Sorulursa Soytarıyım diyerek cevap ver."}

        ]
    }
}



# ===== Geçmişler =====
private_histories = {}       # user_id: [history]
group_histories = {}         # chat_id: {user_id: [history]}

# Helper function to format history for Gemini API
def format_history_for_gemini(history):
    formatted_history = []
    for message in history:
        # Ensure the role is either 'user' or 'model' for the Gemini API
        role = 'model' if message['role'] == 'assistant' else message['role']
        formatted_history.append({
            "role": role,
            "parts": [{"text": message["content"]}]
        })
    return formatted_history


# ===== Bot Kurulum =====
# TELEGRAM_BOT_TOKEN kontrolü yapıldı
if TELEGRAM_BOT_TOKEN:
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    BOT_BASLAMA_ZAMANI = int(time.time())
else:
    # TELEGRAM_BOT_TOKEN yoksa botu başlatma
    bot = None
    dp = None
    print("TELEGRAM_BOT_TOKEN ayarlanmadığı için bot başlatılamıyor.")


# ===== /start =====
if dp: # dp None değilse yani bot başlatıldıysa
    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            "👋 Selam! Ben Marginals AI.\n\n"
            "🧠 /ai <mesaj> yazarak bana soru sorabilirsin.\n"
            "🔄 /reborn yazarak geçmişi sıfırlayabilirsin."
        )

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
            response = requests.post(DRAW_API_URL, headers=headers, json=payload)
            if response.status_code == 200:
                # Görsel geldiyse dosyayı byte olarak kaydet
                image_bytes = response.content
                from aiogram.types import FSInputFile
                import tempfile

                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp.write(image_bytes)
                    tmp_path = tmp.name

                photo = FSInputFile(tmp_path)
                await message.reply_photo(photo, caption=f"🖼️ İşte isteğin: {prompt}")
            elif response.status_code == 503:
                await message.reply("⏳ Model şu anda yükleniyor. Lütfen birkaç saniye sonra tekrar dene.")
            else:
                await message.reply(f"❌ Resim üretilemedi. Kod: {response.status_code}")
        except Exception as e:
            await message.reply(f"⚠️ Hata oluştu: {e}")

    # ===== Model Komutu =====
    @dp.message(Command("model"))
    async def change_model(message: Message):
     user_id = message.from_user.id
     args = message.text.split(maxsplit=1)

     if len(args) < 2:
        available = ", ".join(MODEL_PRESETS.keys())
        await message.reply(f"⚙️ Kullanılabilir modlar: {available}\n\nÖrnek: /model charming")
        return

     choice = args[1].strip().lower()
     if choice not in MODEL_PRESETS:
        available = ", ".join(MODEL_PRESETS.keys())
        await message.reply(f"❌ Geçersiz seçim: {choice}\n\nMevcut seçenekler: {available}")
        return

     preset = MODEL_PRESETS[choice]
     user_settings[user_id] = preset  # kullanıcıya preset ata

     await message.reply(
        f"✅ Artık `{choice}` modundasın.\n"
        f"📌 Model: {preset['model']}\n"
        f"🎭 Persona: {len(preset['system_messages'])} system mesajı yüklendi."
     )

    # ===== /ai mesaj zamanlama =====
    @dp.message()
    async def handle_message(message: Message):
        global current_key_index, api_key_usage

        if message.from_user.is_bot:
            return
        if message.date.timestamp() < BOT_BASLAMA_ZAMANI:
            return

        chat_type = message.chat.type
        chat_id = message.chat.id
        user_id = message.from_user.id

        
        # 🔹 Kullanıcı model seçmiş mi kontrol et
        if user_id not in user_settings:
            await message.reply(
                "⚠️ Önce bir model seçmelisin. Örnek: /model charming\n"
                f"Mevcut seçenekler: {', '.join(MODEL_PRESETS.keys())}"
            )
            return
            
        # Sadece /ai ile başlayan mesajlara cevap ver
        if chat_type in ("group", "supergroup"):
            if not message.text.lower().startswith("/ai"):
                return
            user_input = message.text.replace("/ai", "").strip()

            # Kullanıcıya özel geçmiş tanımla
            if chat_id not in group_histories:
                group_histories[chat_id] = {}
            history = group_histories[chat_id].setdefault(user_id, [])

        else:
            user_input = message.text.strip()
            history = private_histories.setdefault(user_id, [])


        if not user_input:
            await message.reply("✏️ Lütfen bir mesaj yaz: /ai <mesaj>")
            return

        await message.chat.do("typing")

        try:
            # Kullanıcı mesajını geçmişe ekle
            history.append({"role": "user", "content": user_input})

            # Geçmiş 15 girdiyi aşarsa kırp (sistem mesajları hariç tutularak)
            # Sistem mesajları her zaman listenin başında olacağı için kırpma sadece kullanıcı/bot mesajları için geçerli olacak
            max_history_length = 15 # Sistem mesajları + 15 kullanıcı/bot mesajı (örneğin 2 sistem mesajı varsa 13 kullanıcı/bot)
            # Gerçek kırpma uzunluğu = max_history_length - len(SYSTEM_MESSAGES)
            actual_trim_length = max_history_length - len(SYSTEM_MESSAGES)
            if len(history) > actual_trim_length:
                 # En son `actual_trim_length` kadar kullanıcı/bot mesajını al
                trimmed_history = history[-(actual_trim_length):]
                history = trimmed_history # history referansını güncelle


            # Format history for Gemini API
            formatted_history = format_history_for_gemini(history)



            # Google AI Studio API çağrısı
            if not GOOGLE_API_KEYS:
                await message.reply("⚠️ Google AI Studio API anahtarları yapılandırılmamış.")
                return

            # API anahtarı seçimi ve kullanım kontrolü
            api_key = GOOGLE_API_KEYS[current_key_index]
            genai.configure(api_key=api_key)

            # Kullanım sayacını artır
            api_key_usage[api_key] += 1

            # İstek limiti kontrolü (basit bir kontrol, gerçek limit aşıldığında hata yakalama daha sağlamdır)
            if api_key_usage[api_key] > 50: # Örnek limit: 50
                current_key_index += 1
                if current_key_index >= len(GOOGLE_API_KEYS):
                    current_key_index = 0 # Başa dön (veya tüm anahtarlar tükenirse hata verilebilir)
                    await message.reply("⚠️ Tüm API anahtarlarının günlük limiti dolmuş olabilir. Lütfen daha sonra tekrar deneyin.")
                    return
                api_key = GOOGLE_API_KEYS[current_key_index]
                genai.configure(api_key=api_key)
                api_key_usage[api_key] = 1 # Yeni anahtarın sayacını sıfırla ve 1 yap
                await message.reply(f"🔄 API anahtarı değiştiriliyor. Yeni anahtar kullanılıyor.")

            print(f"Using API Key: {api_key}") # Debug print


             settings = user_settings.get(user_id)
            if not settings:
            await message.reply(
        "⚠️ Önce bir model seçmelisin. Örnek: /model charming\n"
        f"Mevcut seçenekler: {', '.join(MODEL_PRESETS.keys())}"
    )
    return


            # system_messages içeriğini birleştir
            combined_system_message = "\n".join([msg["content"] for msg in settings["system_messages"]])

            # Modeli hazırla
            model = genai.GenerativeModel(
                model_name=settings["model"],
                system_instruction=combined_system_message
            )

            # Yanıt al
            response = model.generate_content(formatted_history)
            reply = response.text

            # Kullanım bilgilerini kaydet
            save_api_usage()


            # Botun cevabını geçmişe ekle
            history.append({"role": "assistant", "content": reply})

            # Geçmişi güncelle (history zaten güncellenmiş referansı tutuyor)
            if chat_type in ("group", "supergroup"):
                group_histories[chat_id][user_id] = history
            else:
                private_histories[user_id] = history

            await message.reply(reply)

        except Exception as e:
            print(f"Exception caught: {e}") # Debug print
            # Hata durumunda da anahtar değiştirme mantığı eklenebilir (özellikle 429 Too Many Requests hatası için)
            current_key_index, api_key_usage # Global değişkenleri tekrar belirtmeye gerek yok
            current_key_index += 1
            if current_key_index >= len(GOOGLE_API_KEYS):
                current_key_index = 0 # Başa dön
                await message.reply("⚠️ Tüm API anahtarlarının günlük limiti dolmuş olabilir veya bir hata oluştu. Lütfen daha sonra tekrar deneyin.")
            else:
                 api_key = GOOGLE_API_KEYS[current_key_index]
                 genai.configure(api_key=api_key)
                 api_key_usage[api_key] = 0 # Yeni anahtarın sayacını sıfırla
                 await message.reply(f"🔄 API hatası nedeniyle yanıtlanamadı.\n\nMesajını tekrar göndermeyi dene.")

            # Hata durumunda da kullanım bilgilerini kaydetmek isteyebilirsin
            save_api_usage()

# Port
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot aktif.')

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('', port), DummyHandler)
    print(f"Render web server başlatıldı. Port: {port}")
    server.serve_forever()

# ===== Başlatıcı =====
async def main():
    if bot and dp: # Bot ve dispatcher başarıyla oluşturulduysa
        print("✅ Bot çalışıyor. /ai komutunu deneyebilirsin.")
        await dp.start_polling(bot)
    else:
        print("❌ Bot başlatılamadı. Lütfen gerekli ortam değişkenlerini kontrol edin.")

# Use dp.run_polling instead of asyncio.run(main())
if __name__ == "__main__":
        # HTTP sunucusunu başlat
    threading.Thread(target=run_web_server).start()
    # Bot ve dispatcher başarıyla oluşturulduysa çalıştır
    if dp:
        print("✅ Bot çalışıyor. /ai komutunu deneyebilirsin.")
        dp.run_polling(bot)
    else:

        print("❌ Bot başlatılamadı. Lütfen gerekli ortam değişkenlerini kontrol edin.")

