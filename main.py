import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from datetime import datetime, timezone
import requests

from database import db, create_document, get_documents
from schemas import User, Product, TrackItem, PricePoint

app = FastAPI(title="Price Tracker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Price Tracker API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = getattr(db, "name", None) or "Unknown"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:120]}"
        else:
            response["database"] = "❌ db is None"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:120]}"
    return response

# ---------- Auth Models ----------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class AuthResponse(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    token: str

# Note: For demo we return a fake token. In production, use JWT.

@app.post("/api/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest):
    # Minimal demo: just create a user doc if not exists
    existing = db["user"].find_one({"email": req.email}) if db else None
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    # Hashing skipped for simplicity in this demo environment
    user = User(email=req.email, password_hash=req.password, name=req.name)
    _id = create_document("user", user)
    return AuthResponse(email=req.email, name=req.name, token=f"demo-{_id}")

@app.post("/api/auth/login", response_model=AuthResponse)
def login(req: LoginRequest):
    doc = db["user"].find_one({"email": req.email}) if db else None
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return AuthResponse(email=req.email, name=doc.get("name"), token=f"demo-{doc.get('_id')}")

# ---------- Track Items ----------
class CreateTrackRequest(BaseModel):
    email: EmailStr
    url: str
    target_price: float

@app.post("/api/track")
def create_track(req: CreateTrackRequest):
    # limit: free tier 5 items
    count = db["trackitem"].count_documents({"user_email": req.email}) if db else 0
    if count >= 5:
        raise HTTPException(status_code=403, detail="Free tier allows up to 5 products")
    track = TrackItem(user_email=req.email, url=req.url, target_price=req.target_price)
    _id = create_document("trackitem", track)
    return {"id": _id, "status": "created"}

@app.get("/api/track")
def list_tracks(email: EmailStr):
    docs = get_documents("trackitem", {"user_email": email}, limit=100)
    for d in docs:
        d["id"] = str(d.pop("_id", ""))
    return {"items": docs}

# ---------- Telegram Setup ----------
class TelegramConfig(BaseModel):
    email: EmailStr
    token: str
    chat_id: str

@app.post("/api/telegram/save")
def save_telegram(cfg: TelegramConfig):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    db["user"].update_one({"email": cfg.email}, {"$set": {"telegram_token": cfg.token, "telegram_chat_id": cfg.chat_id}}, upsert=True)
    return {"ok": True}

@app.post("/api/telegram/test")
def test_telegram(cfg: TelegramConfig):
    # Send a test message
    try:
        url = f"https://api.telegram.org/bot{cfg.token}/sendMessage"
        resp = requests.post(url, json={"chat_id": cfg.chat_id, "text": "✅ Price Tracker connected!"})
        ok = resp.ok and resp.json().get("ok")
        return {"ok": bool(ok), "status": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------- Schema Introspection (for internal DB viewer) ----------
@app.get("/schema")
def get_schema():
    return {
        "user": User.model_json_schema(),
        "product": Product.model_json_schema(),
        "trackitem": TrackItem.model_json_schema(),
        "pricepoint": PricePoint.model_json_schema(),
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
