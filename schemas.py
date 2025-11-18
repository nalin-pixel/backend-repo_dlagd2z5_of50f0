"""
Database Schemas for Price Tracker

Each Pydantic model represents a collection in MongoDB. The collection name
is the lowercase class name (e.g., User -> "user").
"""
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime

class User(BaseModel):
    """
    Users collection schema
    Collection: "user"
    """
    email: EmailStr = Field(..., description="User email (unique)")
    password_hash: Optional[str] = Field(None, description="Hashed password (bcrypt)")
    name: Optional[str] = Field(None, description="Display name")
    telegram_token: Optional[str] = Field(None, description="Telegram Bot Token for this user")
    telegram_chat_id: Optional[str] = Field(None, description="Telegram Chat ID for notifications")
    is_pro: bool = Field(False, description="Pro plan status")

class Product(BaseModel):
    """
    Products collection schema
    Collection: "product"
    """
    retailer: str = Field(..., description="Retailer name (e.g., Webhallen, Inet, Power, PriceRunner)")
    product_id: Optional[str] = Field(None, description="Retailer-specific product id")
    title: str = Field(..., description="Product title")
    url: str = Field(..., description="Product page URL")
    image: Optional[str] = Field(None, description="Product image URL")
    current_price: Optional[float] = Field(None, description="Latest known price in SEK")

class TrackItem(BaseModel):
    """
    Track items created by users
    Collection: "trackitem"
    """
    user_email: EmailStr = Field(..., description="Owner (user email)")
    product_id: Optional[str] = Field(None, description="Linked product id (stringified ObjectId)")
    url: str = Field(..., description="Product URL to track")
    target_price: float = Field(..., ge=0, description="Target price in SEK")
    status: str = Field("tracking", description="tracking|deal|pending|error")

class PricePoint(BaseModel):
    """
    Historical price points for a track item
    Collection: "pricepoint"
    """
    trackitem_id: str = Field(..., description="TrackItem id (stringified ObjectId)")
    price: float = Field(..., ge=0, description="Price in SEK")
    recorded_at: Optional[datetime] = Field(default=None, description="When the price was recorded (UTC)")

# The Flames database viewer will automatically:
# 1. Read these schemas from GET /schema endpoint
# 2. Use them for document validation
# 3. Handle operations in the viewer
