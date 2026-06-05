# backend/main.py
import sys
import os
import logging
from dotenv import load_dotenv

# Load environment variables securely
load_dotenv()

# Fix Windows console encoding for Unicode/emoji output
os.environ["PYTHONIOENCODING"] = "utf-8"

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from fastapi.middleware.cors import CORSMiddleware
from database import messages_collection, chats_collection, users_collection
import aiohttp
import re
from datetime import datetime
from bson import ObjectId
import asyncio
import bcrypt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

# Use logging instead of print to avoid Windows encoding issues
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# Safe print that won't crash on Windows
def safe_log(msg):
    try:
        logger.info(msg)
    except Exception:
        try:
            logger.info(msg.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass

# ----------------------------
# Config
# ----------------------------
API_KEY = os.getenv("OPENWEATHER_API_KEY", "YOUR_OPENWEATHER_KEY")   # OpenWeather
GROK_API_KEY = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")

# ----------------------------
# FastAPI app
# ----------------------------
app = FastAPI()

origins = [
    "http://localhost:5173",    # Default Vite port
    "http://127.0.0.1:5173",
    "http://localhost:3000",    # Default Create-React-App port
    # Add your deployed frontend URL here when you have it!
    # "https://your-frontend-app.vercel.app" 
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Schemas
# ----------------------------
class ChatRequest(BaseModel):
    text: str

class NewChatRequest(BaseModel):
    title: str = "New Chat"
    user_id: str

class UserRegister(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str


# -----------------------------
# Detect weather intent (multilingual)
# -----------------------------
def is_weather_question(user_text):
    text = user_text.lower().strip()

    # Weather keywords in English, French, Arabic, Darija
    # Including common misspellings!
    weather_keywords = [
        # English + misspellings
        "weather", "temperature", "tempurature", "temputareure", "temprature",
        "temperture", "tempereture", "temperatre", "temp",
        "forecast", "climate", "rain", "sunny", "cold", "hot", "wind",
        "humidity", "snow", "cloudy", "storm", "degree", "degrees",
        # French + misspellings
        "météo", "meteo", "temps qu'il fait", "température", "temperature",
        "prévision", "prevision", "pluie", "soleil", "froid", "chaud",
        "vent", "neige", "humidité", "humidite",
        # Arabic
        "طقس", "حرارة", "درجة الحرارة", "درجة", "توقعات", "مطر", "شمس",
        "رياح", "ثلج", "برد", "حار", "الجو", "جو",
        # Darija (Latin script) + variants
        "lwe9t", "lwaqt", "ljaw", "l7ala", "jaw",
        "skhoun", "bard", "chta", "rih", "shems",
        "ta9s", "ta9ss", "taqss", "ta2s",
        "7arara", "7rara", "l7arara", "l7rara",
        "darajat", "daraja",
        "ach kayn f", "ki dayra", "ki dayer",
        "chhal f", "ch7al", "chhal",
        "wach ghadi", "wach kayshta",
    ]

    # Weather question patterns (multilingual)
    weather_patterns = [
        # English + misspellings
        r"weather\s+in\s+",
        r"temp[eu]r?[ea]?ture?\s+in\s+",
        r"temp\s+in\s+",
        r"how.+weather",
        r"what.+weather",
        r"what.+temp",
        r"is it (cold|hot|raining|sunny|warm|cloudy)",
        r"tell me.+(weather|temp|temperature)",
        r"(weather|temp|temperature|forecast).+(in|at|for)",
        # French
        r"m[ée]t[ée]o\s+(à|a|de|en)\s+",
        r"quel\s+temps\s+(fait|à|a|en)",
        r"il fait (chaud|froid|beau)",
        r"est.ce qu.il (pleut|neige)",
        # Arabic
        r"كيف الطقس",
        r"شحال الحرارة",
        r"الطقس في",
        r"درجة الحرارة في",
        r"الجو في",
        r"كيف الجو",
        # Darija (Latin script)
        r"ki\s*dayer?\s*(l\s*we?[q9]t|l\s*7ala|l\s*jaw)",
        r"ach\s*kayn\s*f\s*",
        r"ch?7?hal\s*f\s*",
        r"wach\s*(ghadi\s*)?t(shta|chta|shti)",
        r"ki\s*dayra?\s*(f|fi)\s*",
        r"ta?9s+\s*(f|fi|dyal)\s*",
        r"l?jaw\s*(f|fi|dyal)\s*",
        r"darajat?\s*l?7a?rara?\s*(f|fi)\s*",
        r"7a?rara?\s*(f|fi|dyal)\s*",
        r"bghit\s+n3(a?)r[ae]?f\s+(ta?9|l?jaw|l?we?9t|7a?rara)",
        r"baghi\s+n3(a?)r[ae]?f\s+(ta?9|l?jaw|l?we?9t|7a?rara)",
    ]

    # Check keywords
    if any(keyword in text for keyword in weather_keywords):
        return True

    # Check patterns
    for pattern in weather_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False


# -----------------------------
# Extract city (multilingual)
# -----------------------------
def extract_city(user_text):
    text = user_text.strip()
    text_lower = text.lower()

    # ALL weather-related words (used to strip them and find the city)
    all_weather_words = {
        # English + misspellings
        "weather", "temperature", "tempurature", "temputareure", "temprature",
        "temperture", "tempereture", "temperatre", "temp",
        "forecast", "climate", "rain", "raining", "sunny", "cold", "hot",
        "wind", "windy", "humidity", "humid", "snow", "snowing", "cloudy",
        "storm", "stormy", "degree", "degrees", "speed",
        # Questions/verbs
        "what", "what's", "how", "how's", "is", "the", "it", "tell", "me",
        "about", "give", "show", "get", "know", "want", "i", "to", "can",
        "you", "please", "ok", "okay", "hello", "hi", "hey",
        # French
        "météo", "meteo", "température", "prévision", "prevision",
        "pluie", "soleil", "froid", "chaud", "vent", "neige",
        "humidité", "humidite", "quel", "quelle", "fait", "il",
        "je", "veux", "savoir", "le", "la", "les", "des", "du",
        # Darija
        "lwe9t", "lwaqt", "ljaw", "l7ala", "jaw", "ta9s", "ta9ss",
        "skhoun", "bard", "chta", "rih", "shems",
        "7arara", "7rara", "l7arara", "darajat", "daraja",
        "ki", "dayer", "dayra", "ach", "kayn", "kayna",
        "chhal", "ch7al", "wach", "bghit", "baghi",
        "n3arf", "n3ref", "n3raf",
        "3la", "3lach",
    }

    # Prepositions to skip
    prepositions = {"in", "at", "for", "of", "f", "fi", "dyal", "d",
                    "a", "à", "de", "en", "du", "pour", "my", "city", "town"}

    patterns = [
        # English: ANY weather word + in/at/for + city
        r"(?:weather|temp\w*|forecast|climate|wind|windy|humidity|humid|rain\w*|snow\w*|cold|hot|sunny|cloudy|storm\w*|degree\w*|speed)\s+(?:in|at|for|of)\s+(.+)",
        # English: "how is the wind in X", "what is the speed wind in X"
        r"(?:what|how|tell|give|show).*?(?:weather|temp\w*|wind\w*|humid\w*|rain\w*|snow\w*|forecast|cold|hot|sunny|cloud\w*|storm\w*|speed|degree)\w*\s+(?:in|at|for|of)\s+(.+)",
        # English: "X in Y" where we want Y
        r".*\b(?:in|at|for)\s+([a-zA-ZÀ-ÖØ-öø-ÿ\s\-]+)$",
        # "tell me about X" / "about X"
        r"(?:tell\s+me\s+)?about\s+(.+)",
        # French
        r"(?:m[ée]t[ée]o|meteo|temp[ée]rature|temps|vent|pluie|neige|humidit[ée])\s+(?:à|a|de|en|du|pour)\s+(.+)",
        r"(?:quel\s+temps\s+fait[- ]il\s+(?:à|a|en)\s+)(.+)",
        # Arabic
        r"(?:الطقس في|طقس|درجة الحرارة في|كيف الطقس في|شحال الحرارة في|الجو في|كيف الجو في|الرياح في|الرطوبة في)\s*(.+)",
        # Darija (Latin)
        r"(?:ki\s*dayer?\s*(?:l\s*we?[q9]t|l\s*7ala|l\s*jaw)\s*(?:f|fi|dyal)\s*)(.+)",
        r"(?:ta?9s+\s*(?:f|fi|dyal)\s*)(.+)",
        r"(?:l?jaw\s*(?:f|fi|dyal)\s*)(.+)",
        r"(?:darajat?\s*l?7a?rara?\s*(?:f|fi)\s*)(.+)",
        r"(?:7a?rara?\s*(?:f|fi|dyal)\s*)(.+)",
        r"(?:rih|vent|wind)\s*(?:f|fi|dyal|in|at)\s*(.+)",
        r"(?:bghit|baghi)\s+n3a?r[ae]?f\s+(?:ta?9s?|l?jaw|l?we?9t|7a?rara?|rih)\s+(?:f|fi|dyal)\s*(.+)",
        r"(?:bghit|baghi)\s+n3a?r[ae]?f\s+(?:3la\s+)?(?:l?madinat?\s+)?(.+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_lower, re.IGNORECASE)
        if match:
            city = match.group(1).strip()
            city = re.sub(r"[?.!,;]+$", "", city).strip()
            city = re.sub(r"^(madinat?\s+|city of\s+|ville de\s+|my\s+city\s+(in\s+)?)", "", city, flags=re.IGNORECASE).strip()
            # If the result is not empty and not just filler words
            if city and city not in all_weather_words and city not in prepositions:
                return city

    # Fallback: strip ALL known non-city words and try what remains
    words = text_lower.split()
    remaining = []
    for word in words:
        clean_word = re.sub(r"[?.!,;]+$", "", word)
        if clean_word not in all_weather_words and clean_word not in prepositions:
            remaining.append(clean_word)

    if remaining:
        city = " ".join(remaining)
        city = re.sub(r"[?.!,;]+$", "", city).strip()
        if city:
            return city

    return ""


# -----------------------------
# Clean up extracted city name
# -----------------------------
def clean_city_name(city):
    """Remove filler words and extract the actual city name."""
    if not city:
        return city
    
    city = city.strip().lower()
    
    # Remove common filler phrases
    filler_patterns = [
        r"^my\s+city\s+(in|of|is|called)?\s*",
        r"^the\s+city\s+(of|called)?\s*",
        r"^my\s+town\s+(in|of|is|called)?\s*",
        r"^the\s+town\s+(of|called)?\s*",
        r"^la\s+ville\s+de\s*",
        r"^ma\s+ville\s*",
        r"^madinat?\s*",
        r"^l?mdina\s+(dyal|d)?\s*",
        r"^city\s+of\s*",
        r"^ville\s+de\s*",
    ]
    
    for pattern in filler_patterns:
        city = re.sub(pattern, "", city, flags=re.IGNORECASE).strip()
    
    # If city still contains "in" or "at" or "of", take what's after the LAST one
    # e.g. "my city in casablanca" -> after removing "my city" -> "in casablanca" -> "casablanca"
    last_prep = re.search(r".*\b(?:in|at|of|f|fi|dyal|en|de|du|pour|à|a)\s+(.+)$", city, re.IGNORECASE)
    if last_prep:
        city = last_prep.group(1).strip()
    
    # Remove trailing punctuation
    city = re.sub(r"[?.!,;]+$", "", city).strip()
    
    return city


# -----------------------------
# Try to get weather with retry (progressively shorter city names)
# -----------------------------
async def try_weather_with_retry(raw_city, user_lang):
    """Try the full city name, then cleaned version, then last 2 words, then last word."""
    attempts = []
    
    # Attempt 1: cleaned city name
    cleaned = clean_city_name(raw_city)
    if cleaned:
        attempts.append(cleaned)
    
    # Attempt 2: raw city (if different from cleaned)
    if raw_city.lower().strip() != cleaned:
        attempts.append(raw_city.strip())
    
    # Attempt 3: last 2 words of raw city
    words = raw_city.strip().split()
    if len(words) >= 2:
        last2 = " ".join(words[-2:])
        if last2.lower() not in [a.lower() for a in attempts]:
            attempts.append(last2)
    
    # Attempt 4: last word only
    if words:
        last1 = words[-1]
        if last1.lower() not in [a.lower() for a in attempts]:
            attempts.append(last1)
    
    for city_try in attempts:
        safe_log(f"Trying weather API with city: '{city_try}'")
        result = await weather_reply(city_try, user_lang)
        if result:
            return result
    
    return None


# -----------------------------
# Groq AI chat (multilingual)
# -----------------------------
async def grok_reply(user_text, context=None):
    if context is None:
        context = []

    url = "https://api.groq.com/openai/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {GROK_API_KEY}",
        "Content-Type": "application/json"
    }
    system_prompt = (
        "You are Metio Bot, a specialized WEATHER chatbot assistant. "
        "You speak fluently: English, French, Arabic (fusha), and Moroccan Darija.\n\n"

        "YOUR IDENTITY:\n"
        "- You are a WEATHER BOT. Your main job is to help users with weather information.\n"
        "- When users ask about weather, temperature, forecast, or climate, provide helpful weather info.\n"
        "- When users ask questions that have NOTHING to do with weather "
        "(like programming, math, history, cooking, jokes, science, etc.), "
        "politely tell them you are a weather bot and redirect them.\n"
        "- Example responses for non-weather questions:\n"
        "  English: 'I'm Metio Bot, your weather assistant! I specialize in weather info. Try asking me about the weather in any city!'\n"
        "  French: 'Je suis Metio Bot, votre assistant meteo ! Demandez-moi la meteo de n'importe quelle ville !'\n"
        "  Darija: 'Ana Metio Bot, specialist f ljaw! S9sini 3la ta9s f shi mdina!'\n"
        "  Arabic: 'انا ميتيو بوت، مساعدك للطقس! اسألني عن الطقس في أي مدينة!'\n\n"

        "EXCEPTIONS - you CAN answer these non-weather things:\n"
        "- Greetings (hello, salam, bonjour) - greet back warmly and mention you can help with weather\n"
        "- Questions about yourself (who are you, what can you do)\n"
        "- Simple thank you messages\n\n"

        "LANGUAGE RULES (CRITICAL):\n"
        "- ALWAYS detect and reply in the SAME language the user uses.\n"
        "- If user writes in Darija, reply in natural Darija using Latin script. "
        "Use numbers for Arabic sounds: 3=ayn, 7=ha, 9=qaf, 5=kha, 2=hamza, 8=ghayn.\n"
        "- Write Darija naturally like real Moroccans text.\n"
        "- If user writes in Arabic script, reply in Arabic script.\n"
        "- If user writes in French, reply in French.\n"
        "- If user writes in English, reply in English.\n\n"

        "WEATHER RESPONSES:\n"
        "- When you know the weather data (it will be provided to you), present it in a friendly way.\n"
        "- Add helpful context like what to wear, if it's good for going out, etc.\n"
        "- Use emojis naturally.\n"
        "- Be warm, friendly, and conversational."
    )

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(context)
    messages.append({"role": "user", "content": user_text})

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 500
                },
                headers=headers
            ) as resp:

                response_text = await resp.text()
                safe_log(f"Groq status: {resp.status}")

                if resp.status != 200:
                    safe_log(f"Groq API error: {response_text[:200]}")
                    return None

                data = await resp.json()
                reply = data["choices"][0]["message"]["content"]
                safe_log(f"Groq replied successfully (length={len(reply)})")
                return reply

    except aiohttp.ClientError as e:
        safe_log(f"Groq connection error: {e}")
        return None
    except Exception as e:
        safe_log(f"Groq unexpected error: {e}")
        return None


# -----------------------------
# Weather reply (multilingual)
# -----------------------------
async def weather_reply(city, user_lang="en"):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric&lang=fr"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        safe_log(f"Weather API response for '{city}': cod={data.get('cod')}")

        if data.get("cod") != 200:
            return None

        temp = round(data["main"]["temp"])
        desc = data["weather"][0]["description"]
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]
        city_name = data["name"]

        if user_lang == "ar":
            return (
                f"الطقس في {city_name}:\n"
                f"الحرارة: {temp}°C\n"
                f"الحالة: {desc}\n"
                f"الرطوبة: {humidity}%\n"
                f"الرياح: {wind_speed} م/ث"
            )
        elif user_lang == "darija":
            return (
                f"Ta9s f {city_name}:\n"
                f"Temperature: {temp}°C\n"
                f"Conditions: {desc}\n"
                f"Humidity: {humidity}%\n"
                f"Wind: {wind_speed} m/s"
            )
        elif user_lang == "fr":
            return (
                f"Meteo a {city_name} :\n"
                f"Temperature : {temp}°C\n"
                f"Conditions : {desc}\n"
                f"Humidite : {humidity}%\n"
                f"Vent : {wind_speed} m/s"
            )
        else:
            return (
                f"Weather in {city_name}:\n"
                f"Temperature: {temp}°C\n"
                f"Conditions: {desc}\n"
                f"Humidity: {humidity}%\n"
                f"Wind: {wind_speed} m/s"
            )

    except Exception as e:
        safe_log(f"Weather error: {e}")
        return None


# -----------------------------
# Detect language
# -----------------------------
def detect_language(text):
    text_lower = text.lower().strip()

    # Check for Arabic script
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    if arabic_chars > len(text) * 0.3:
        darija_arabic_words = ["كيفاش", "واش", "شنو", "فين", "علاش", "بزاف", "مزيان", "خويا", "صاحبي"]
        if any(w in text for w in darija_arabic_words):
            return "darija"
        return "ar"

    # Check for Darija (Latin script)
    darija_words = [
        "labas", "labes", "wesh", "wach", "ach", "chno", "chnou",
        "fin", "3lach", "bzzaf", "bzaf", "mezian", "mzyan",
        "khouya", "sa7bi", "dyal", "dial", "hna",
        "ghadi", "kandir", "bghit", "3ndi", "andi",
        "salam", "slama", "hamdullah", "hamdulah",
        "ki dayr", "ki dayra", "cv", "kifach", "chhal", "ch7al",
        "lwe9t", "lwaqt", "skhoun", "bard",
        "kayn", "kayna", "ma3ndich", "makaynch",
        "nta", "nti", "howa", "hiya", "7na", "ntoma", "homa",
    ]
    text_words = text_lower.split()
    for w in darija_words:
        if w in text_lower:
            return "darija"

    # Check for French
    french_words = [
        "je", "tu", "il", "elle", "nous", "vous", "ils", "elles",
        "le", "la", "les", "un", "une", "des", "du", "de",
        "est", "sont", "suis",
        "bonjour", "bonsoir", "merci", "salut", "comment",
        "quoi", "pourquoi", "quand", "qui",
        "météo", "temps", "fait", "quel", "quelle",
        "oui", "non", "bien",
        "parle", "parler", "dire", "dit", "faire",
    ]
    french_matches = sum(1 for w in french_words if w in text_words)
    if french_matches >= 2:
        return "fr"

    return "en"


# -----------------------------
# AUTH ROUTES: Sign Up & Sign In
# -----------------------------
@app.post("/signup")
async def signup(user: UserRegister):
    existing_user = await users_collection.find_one({"email": user.email.lower()})
    if existing_user:
        return {"status": "error", "message": "email already registered"}
    
    hashed_password = get_password_hash(user.password)
    user_data = {
        "name": user.name,
        "email": user.email.lower(),
        "password": hashed_password,
        "created_at": datetime.utcnow()
    }
    await users_collection.insert_one(user_data)
    return {"status": "ok", "message": "user registered successfully"}

@app.post("/signin")
async def signin(user: UserLogin):
    existing_user = await users_collection.find_one({"email": user.email.lower()})
    
    # 1. User does not exist
    if not existing_user:
        return {"status": "error", "message": "user info are not exists"}
        
    # 2. Password mismatch
    if not verify_password(user.password, existing_user["password"]):
        return {"status": "error", "message": "password or gmal are wring"}
        
    return {
        "status": "ok", 
        "message": "Login successful", 
        "user_id": str(existing_user["_id"]),
        "name": existing_user["name"],
        "email": existing_user["email"]
    }

# -----------------------------
# ROUTE: Delete a User
# -----------------------------
@app.delete("/user/{user_id}")
async def delete_user_route(user_id: str):
    try:
        user_obj_id = ObjectId(user_id)
        # 1. Delete the user
        result = await users_collection.delete_one({"_id": user_obj_id})
        if result.deleted_count == 0:
            return {"status": "error", "message": "User not found"}
            
        # 2. Find their chats to delete associated messages
        user_chats_cursor = chats_collection.find({"user_id": user_id})
        user_chats = await user_chats_cursor.to_list(length=1000)
        for chat in user_chats:
            await messages_collection.delete_many({"chat_id": str(chat["_id"])})
            
        # 3. Delete their chats
        await chats_collection.delete_many({"user_id": user_id})
        
        safe_log(f"Deleted user {user_id} and all their data.")
        return {"status": "ok", "message": "User and all associated data deleted successfully"}
    except Exception as e:
        safe_log(f"Error deleting user: {e}")
        return {"status": "error", "message": "Invalid user ID or internal error"}

# -----------------------------
# ROUTE 1: Create new chat
# -----------------------------
@app.post("/new-chat")
async def create_new_chat(request: NewChatRequest):
    chat_data = {
        "title": request.title,
        "user_id": request.user_id,
        "created_at": datetime.utcnow()
    }
    result = await chats_collection.insert_one(chat_data)
    return {
        "chat_id": str(result.inserted_id),
        "title": request.title
    }


# -----------------------------
# ROUTE 2: Get all chats
# -----------------------------
@app.get("/chats/{user_id}")
async def get_all_chats(user_id: str):
    chats_cursor = chats_collection.find({"user_id": user_id}).sort("_id", -1)
    chats = await chats_cursor.to_list(length=100)
    clean_chats = []
    for chat in chats:
        clean_chats.append({
            "chat_id": str(chat["_id"]),
            "title": chat.get("title", "New Chat")
        })
    return {"chats": clean_chats}


# -----------------------------
# ROUTE 3: Delete a chat
# -----------------------------
@app.delete("/chat/{chat_id}")
async def delete_chat(chat_id: str):
    try:
        await chats_collection.delete_one({"_id": ObjectId(chat_id)})
        await messages_collection.delete_many({"chat_id": chat_id})
        safe_log(f"Deleted chat {chat_id} and its messages")
        return {"status": "ok", "deleted": chat_id}
    except Exception as e:
        safe_log(f"Error deleting chat: {e}")
        return {"status": "error", "message": str(e)}


# -----------------------------
# ROUTE 4: Get messages of one chat
# -----------------------------
@app.get("/messages/{chat_id}")
async def get_chat_messages(chat_id: str):
    messages_cursor = messages_collection.find({"chat_id": chat_id}).sort("_id", 1)
    messages = await messages_cursor.to_list(length=200)
    clean_messages = []
    for msg in messages:
        clean_messages.append({
            "sender": msg["sender"],
            "text": msg["text"]
        })
    return {"messages": clean_messages}


# -----------------------------
# ROUTE 4: Send message inside a specific chat
# -----------------------------
@app.post("/chat/{chat_id}")
async def chat_endpoint(chat_id: str, request: ChatRequest):
    user_text = request.text.strip()
    user_lang = detect_language(user_text)

    safe_log(f"User said: {repr(user_text)}")
    safe_log(f"Detected language: {user_lang}")

    # Save user message
    await messages_collection.insert_one({
        "chat_id": chat_id,
        "sender": "user",
        "text": user_text
    })

    # Auto-generate title from first message
    msg_count = await messages_collection.count_documents({"chat_id": chat_id, "sender": "user"})
    safe_log(f"Message count for chat {chat_id}: {msg_count}")
    if msg_count == 1:
        safe_log(f"Triggering auto-title for chat {chat_id}")
        # Run title generation in background
        async def _title_task():
            try:
                await auto_title_chat(chat_id, user_text)
            except Exception as e:
                safe_log(f"Title task error: {e}")
        asyncio.create_task(_title_task())

    # 1) If weather question -> OpenWeather API
    if is_weather_question(user_text):
        city = extract_city(user_text)
        safe_log(f"Weather question detected. City: '{city}'")

        if city:
            bot_reply = await try_weather_with_retry(city, user_lang)
            if bot_reply:
                await messages_collection.insert_one({
                    "chat_id": chat_id,
                    "sender": "bot",
                    "text": bot_reply
                })
                return {"reply": bot_reply}
            else:
                safe_log(f"All weather API attempts failed for '{city}'")
                # Still a weather question but couldn't find the city
                # Give a helpful error instead of falling to Groq
                if user_lang == "ar":
                    bot_reply = f"عذرا، لم أتمكن من العثور على الطقس في '{city}'. تأكد من اسم المدينة وحاول مرة أخرى."
                elif user_lang == "darija":
                    bot_reply = f"Smeh li, ma l9itch ljaw dyal '{city}'. Chek smiya dyal lmdina o 3awed."
                elif user_lang == "fr":
                    bot_reply = f"Desole, je n'ai pas trouve la meteo pour '{city}'. Verifiez le nom de la ville."
                else:
                    bot_reply = f"Sorry, I couldn't find weather data for '{city}'. Please check the city name and try again."
                await messages_collection.insert_one({
                    "chat_id": chat_id,
                    "sender": "bot",
                    "text": bot_reply
                })
                return {"reply": bot_reply}
        else:
            # Weather question detected but no city found
            if user_lang == "ar":
                bot_reply = "أنا Metio Bot! ما هي المدينة التي تريد معرفة الطقس فيها؟"
            elif user_lang == "darija":
                bot_reply = "Ana Metio Bot! Ina mdina bghiti t3raf ljaw dyalha?"
            elif user_lang == "fr":
                bot_reply = "Je suis Metio Bot! Quelle ville voulez-vous connaitre la meteo?"
            else:
                bot_reply = "I'm Metio Bot! Which city would you like to know the weather for?"
            await messages_collection.insert_one({
                "chat_id": chat_id,
                "sender": "bot",
                "text": bot_reply
            })
            return {"reply": bot_reply}
    # 2) Try as a city name (since this is a weather bot, short inputs might be city names)
    words_clean = re.sub(r"[?.!,;]+$", "", user_text.strip().lower()).split()
    greetings = {"hi", "hello", "hey", "salam", "bonjour", "bonsoir", "salut",
                 "merci", "thanks", "shukran", "labas", "cv", "wesh", "ok", "okay",
                 "oui", "non", "yes", "no", "bye", "goodbye"}
    # If it's 1-3 words and NOT a greeting, try as a city name
    if 1 <= len(words_clean) <= 3 and not any(w in greetings for w in words_clean):
        potential_city = " ".join(words_clean)
        safe_log(f"Trying as city name: '{potential_city}'")
        bot_reply = await weather_reply(potential_city, user_lang)
        if bot_reply:
            await messages_collection.insert_one({
                "chat_id": chat_id,
                "sender": "bot",
                "text": bot_reply
            })
            return {"reply": bot_reply}
        safe_log(f"'{potential_city}' is not a city, sending to Groq")

    # 3) For everything else -> Groq AI (multilingual)
    safe_log("Sending to Groq AI...")

    # Build conversation context
    recent_messages_cursor = messages_collection.find(
        {"chat_id": chat_id}
    ).sort("_id", -1).limit(10)
    recent_messages = await recent_messages_cursor.to_list(length=10)
    recent_messages.reverse()

    context = []
    for msg in recent_messages:
        role = "user" if msg["sender"] == "user" else "assistant"
        context.append({
            "role": role,
            "content": msg["text"]
        })

    bot_reply = await grok_reply(user_text, context)

    # 3) If Groq fails -> multilingual fallback
    if not bot_reply:
        safe_log("Groq failed, using fallback")
        if user_lang == "ar":
            bot_reply = "عذرا، واجهت مشكلة في الاتصال. حاول مرة أخرى من فضلك"
        elif user_lang == "darija":
            bot_reply = "سمحلي، عندي مشكل فالاتصال. عاود حاول من بعد"
        elif user_lang == "fr":
            bot_reply = "Desole, j'ai eu un probleme de connexion. Reessayez s'il vous plait"
        else:
            bot_reply = "Sorry, I had a connection issue. Please try again"

    # Save bot reply
    await messages_collection.insert_one({
        "chat_id": chat_id,
        "sender": "bot",
        "text": bot_reply
    })

    safe_log(f"Bot replied successfully (length={len(bot_reply)})")
    return {"reply": bot_reply}


# -----------------------------
# Auto-generate chat title from first message
# -----------------------------
async def auto_title_chat(chat_id, first_message):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROK_API_KEY}",
            "Content-Type": "application/json"
        }
        prompt = (
            f"Generate a very short chat title (2-5 words max, no quotes, no punctuation) "
            f"that summarizes this message: \"{first_message}\". "
            f"Reply with ONLY the title, nothing else. "
            f"If the message is in Darija or Arabic, make the title in that language using Latin script. "
            f"If in French, title in French. If in English, title in English. "
            f"Examples: 'Weather in Casablanca', 'Ljaw f Casa', 'Salutations', 'Programming Help'"
        )

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "You generate very short chat titles. Reply with only 2-5 words."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 20
                },
                headers=headers
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    title = data["choices"][0]["message"]["content"].strip()
                    # Clean up: remove quotes, limit length
                    title = title.strip('"\'\'\u201c\u201d')
                    if len(title) > 40:
                        title = title[:40]
                    await chats_collection.update_one(
                        {"_id": ObjectId(chat_id)},
                        {"$set": {"title": title}}
                    )
                    safe_log(f"Auto-titled chat {chat_id}: {repr(title)}")
    except Exception as e:
        safe_log(f"Auto-title error: {e}")
        # Fallback: use truncated first message
        title = first_message[:30] + ("..." if len(first_message) > 30 else "")
        try:
            await chats_collection.update_one(
                {"_id": ObjectId(chat_id)},
                {"$set": {"title": title}}
            )
        except Exception:
            pass


# -----------------------------
# ROUTE 6: Get all old messages (debug)
# -----------------------------
@app.get("/messages")
async def get_messages():
    messages_cursor = messages_collection.find().sort("_id", 1)
    messages = await messages_cursor.to_list(length=100)
    clean_messages = []
    for msg in messages:
        clean_messages.append({
            "chat_id": msg.get("chat_id", "no_chat_id"),
            "sender": msg["sender"],
            "text": msg["text"]
        })
    return {"messages": clean_messages}