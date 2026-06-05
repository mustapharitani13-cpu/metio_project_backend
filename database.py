# backend/database.py
import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.getenv("MONGO_URL", "mongodb://127.0.0.1:27017")

# Use tlsAllowInvalidCertificates for cloud deployment compatibility
if "mongodb+srv" in MONGO_URL or "mongodb.net" in MONGO_URL:
    client = AsyncIOMotorClient(
        MONGO_URL,
        tls=True,
        tlsAllowInvalidCertificates=True,
        tlsCAFile=certifi.where()
    )
else:
    client = AsyncIOMotorClient(MONGO_URL)

db = client["metio_chatbot_db"]

# Collections
messages_collection = db["messages"]
chats_collection = db["chats"]
users_collection = db["users"]