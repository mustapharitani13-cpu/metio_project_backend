# backend/database.py
import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.getenv("MONGO_URL", "mongodb://127.0.0.1:27017")

client = AsyncIOMotorClient(MONGO_URL, tlsCAFile=certifi.where())

db = client["metio_chatbot_db"]

# Collections
messages_collection = db["messages"]
chats_collection = db["chats"]
users_collection = db["users"]