import os
from typing import Optional, List
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from apscheduler.schedulers.background import BackgroundScheduler
import jwt
from passlib.context import CryptContext
import requests
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import User, Product, TrackItem, PricePoint

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Price Tracker API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------- Utility -------------------------

def hash_password(p: str) -> str:
    return pwd_context.hash(p)

def verify_password(p: str, h: str) -> bool:
    return pwd_context.verify(p, h)

def create_token(email: str) -> str:
    payload = {"sub": email, "iat": int(datetime.now(timezone.utc).timestamp())}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

# ------------------------- Models -------------------------

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

class GoogleAuthRequest(BaseModel):
    token: str  # Google ID token (demo: pass email)

class CreateTrackRequest(BaseModel):
    url: str
    target_price: float

class TelegramConfig(BaseModel):
    token: str
    chat_id: str

class ScrapeRequest(BaseModel):
    url: str

# ------------------------- Routes -------------------------

@app.get("/")
def read_root():
    return {"message": "Price Tracker API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": getattr(db, "name", None) or "Unknown",
        "collections": []
    }
    try:
        if db is not None:
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["database"] = "✅ Connected"
        else:
            response["database"] = "❌ db is None"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:120]}"
    return response

# ------------------------- Auth -------------------------

@app.post("/api/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    if db["user"].find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=req.email, password_hash=hash_password(req.password), name=req.name)
    create_document("user", user)
    token = create_token(req.email)
    return AuthResponse(email=req.email, name=req.name, token=token)

@app.post("/api/auth/login", response_model=AuthResponse)
def login(req: LoginRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    doc = db["user"].find_one({"email": req.email})
    if not doc or not doc.get("password_hash") or not verify_password(req.password, doc["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.email)
    return AuthResponse(email=req.email, name=doc.get("name"), token=token)

@app.post("/api/auth/google", response_model=AuthResponse)
def google_auth(req: GoogleAuthRequest):
    # Demo: accept email directly
    if "@" not in req.token:
        raise HTTPException(status_code=400, detail="Invalid Google token (demo expects an email)")
    email = req.token
    doc = db["user"].find_one({"email": email}) if db else None
    if not doc:
        user = User(email=email, name=email.split("@")[0])
        create_document("user", user)
    token = create_token(email)
    return AuthResponse(email=email, name=(doc or {}).get("name"), token=token)

# ------------------------- Tracking -------------------------

@app.post("/api/track")
def create_track(req: CreateTrackRequest, user_email: str = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    count = db["trackitem"].count_documents({"user_email": user_email})
    if count >= 5:
        raise HTTPException(status_code=403, detail="Free tier allows up to 5 products")
    track = TrackItem(user_email=user_email, url=req.url, target_price=req.target_price)
    _id = create_document("trackitem", track)
    return {"id": _id, "status": "created"}

@app.get("/api/track")
def list_tracks(user_email: str = Depends(get_current_user)):
    docs = get_documents("trackitem", {"user_email": user_email}, limit=100)
    for d in docs:
        d["id"] = str(d.pop("_id", ""))
    return {"items": docs}

@app.get("/api/pricepoints")
def get_pricepoints(trackitem_id: str, user_email: str = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    # Validate ownership
    try:
        ti = db["trackitem"].find_one({"_id": ObjectId(trackitem_id)})
    except Exception:
        ti = None
    if not ti or ti.get("user_email") != user_email:
        raise HTTPException(status_code=404, detail="Track item not found")
    points = db["pricepoint"].find({"trackitem_id": trackitem_id}).sort("recorded_at", -1).limit(50)
    data = [{"price": p.get("price"), "recorded_at": p.get("recorded_at") } for p in points]
    return {"items": list(reversed(data))}

# ------------------------- Telegram -------------------------

@app.post("/api/telegram/save")
def save_telegram(cfg: TelegramConfig, user_email: str = Depends(get_current_user)):
    if not db:
        raise HTTPException(status_code=500, detail="Database not configured")
    db["user"].update_one({"email": user_email}, {"$set": {"telegram_token": cfg.token, "telegram_chat_id": cfg.chat_id}}, upsert=True)
    return {"ok": True}

@app.post("/api/telegram/test")
def test_telegram(cfg: TelegramConfig, user_email: str = Depends(get_current_user)):
    try:
        url = f"https://api.telegram.org/bot{cfg.token}/sendMessage"
        resp = requests.post(url, json={"chat_id": cfg.chat_id, "text": "✅ Price Tracker connected!"})
        ok = resp.ok and resp.json().get("ok")
        return {"ok": bool(ok), "status": resp.json()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ------------------------- Scraping Stubs -------------------------

@app.post("/api/scrape")
def scrape_price(req: ScrapeRequest):
    # Placeholder: integrate BeautifulSoup/retailer APIs here
    # Return a mocked price for now
    return {"url": req.url, "price": 4999.0, "currency": "SEK", "retailer": "Mock"}

# ------------------------- Background Jobs -------------------------

def check_prices_job():
    if not db:
        return
    items = db["trackitem"].find({})
    for it in items:
        # Here you'd scrape the price. We'll mock a price that sometimes drops.
        current_price = 4000.0 if hash(it["url"]) % 3 == 0 else it.get("target_price", 5000) + 500
        # Save price point
        pp = PricePoint(trackitem_id=str(it.get("_id")), price=current_price, recorded_at=datetime.now(timezone.utc))
        try:
            create_document("pricepoint", pp)
        except Exception:
            pass
        # If deal, notify
        if current_price <= it.get("target_price", 0):
            user = db["user"].find_one({"email": it.get("user_email")})
            token = (user or {}).get("telegram_token")
            chat_id = (user or {}).get("telegram_chat_id")
            if token and chat_id:
                try:
                    url = f"https://api.telegram.org/bot{token}/sendMessage"
                    text = f"🔥 Deal found! {it['url']} now {current_price} SEK (target {it['target_price']} SEK)"
                    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=5)
                except Exception:
                    pass
            db["trackitem"].update_one({"_id": it["_id"]}, {"$set": {"status": "deal", "current_price": current_price}})
        else:
            db["trackitem"].update_one({"_id": it["_id"]}, {"$set": {"status": "tracking", "current_price": current_price}})

scheduler = BackgroundScheduler()
scheduler.add_job(check_prices_job, 'interval', minutes=30, id='price-check')
scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    try:
        scheduler.shutdown()
    except Exception:
        pass

# ------------------------- Schema Introspection -------------------------

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
