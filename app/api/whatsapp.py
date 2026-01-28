import logging
from fastapi import APIRouter, Request, Response, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.connection import get_db
from app.core.config import settings

router = APIRouter()

# --- 1. THE VERIFICATION (GET) ---
@router.get("/get-messages")
async def verify_whatsapp(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    # Change 'my_secret_token' to what you type in Meta Dashboard
    VERIFY_TOKEN = "AtleetBuddy_2024" 
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook Verified Successfully!")
        return Response(content=challenge, media_type="text/plain")
    
    return Response(content="Verification failed", status_code=403)

# --- 2. THE MESSAGE RECEIVER (POST) ---
@router.post("/get-messages")
async def receive_message(request: Request):
    body = await request.json()
    
    # Check if this is a message event
    try:
        if body.get("object") == "whatsapp_business_account":
            entry = body["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            
            # WhatsApp also sends 'status' updates (sent, delivered, read). 
            # We only care about actual 'messages'.
            if "messages" in value:
                message = value["messages"][0]
                sender = message["from"]
                text = message.get("text", {}).get("body", "(No text)")
                
                # THIS IS THE PRINT YOU ASKED FOR
                print("\n" + "="*30)
                print(f"📩 NEW MESSAGE FROM: {sender}")
                print(f"💬 MESSAGE CONTENT: {text}")
                print("="*30 + "\n")
                
            return {"status": "success"}
    except Exception as e:
        print(f"⚠️ Error parsing payload: {e}")
        
    return {"status": "ignored"}