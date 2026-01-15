from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# ==================== MODELS ====================

class Category(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    image_url: str
    order: int = 0

class CategoryCreate(BaseModel):
    name: str
    image_url: str
    order: int = 0

class Dish(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    price: float
    category: str
    image_url: str
    is_popular: bool = False

class DishCreate(BaseModel):
    name: str
    description: str
    price: float
    category: str
    image_url: str
    is_popular: bool = False

class CartItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    dish_id: str
    dish_name: str
    dish_price: float
    dish_image: str
    quantity: int = 1

class CartItemCreate(BaseModel):
    session_id: str
    dish_id: str

class CartItemUpdate(BaseModel):
    session_id: str
    dish_id: str
    quantity: int

class OrderItem(BaseModel):
    dish_id: str
    dish_name: str
    dish_price: float
    quantity: int

class Order(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_number: str
    session_id: str
    table_number: int
    items: List[OrderItem]
    total: float
    status: str = "pending"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class OrderCreate(BaseModel):
    session_id: str
    table_number: int
    items: List[OrderItem]
    total: float

class Notification(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_id: str
    order_number: str
    table_number: int
    message: str
    read: bool = False
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class NotificationCreate(BaseModel):
    order_id: str
    order_number: str
    table_number: int
    message: str


# ==================== CATEGORY ROUTES ====================

@api_router.get("/categories", response_model=List[Category])
async def get_categories():
    categories = await db.categories.find({}, {"_id": 0}).sort("order", 1).to_list(100)
    return categories

@api_router.post("/categories", response_model=Category)
async def create_category(category: CategoryCreate):
    cat_obj = Category(**category.model_dump())
    doc = cat_obj.model_dump()
    await db.categories.insert_one(doc)
    return cat_obj


# ==================== DISH ROUTES ====================

@api_router.get("/dishes", response_model=List[Dish])
async def get_dishes(category: Optional[str] = None):
    query = {}
    if category:
        query["category"] = category
    dishes = await db.dishes.find(query, {"_id": 0}).to_list(1000)
    return dishes

@api_router.get("/dishes/popular", response_model=List[Dish])
async def get_popular_dishes():
    dishes = await db.dishes.find({"is_popular": True}, {"_id": 0}).to_list(100)
    return dishes

@api_router.post("/dishes", response_model=Dish)
async def create_dish(dish: DishCreate):
    dish_obj = Dish(**dish.model_dump())
    doc = dish_obj.model_dump()
    await db.dishes.insert_one(doc)
    return dish_obj


# ==================== CART ROUTES ====================

@api_router.get("/cart/{session_id}", response_model=List[CartItem])
async def get_cart(session_id: str):
    cart_items = await db.cart.find({"session_id": session_id}, {"_id": 0}).to_list(1000)
    return cart_items

@api_router.post("/cart/add", response_model=CartItem)
async def add_to_cart(item: CartItemCreate):
    # Check if item already exists in cart
    existing = await db.cart.find_one({"session_id": item.session_id, "dish_id": item.dish_id}, {"_id": 0})
    
    if existing:
        # Increment quantity
        await db.cart.update_one(
            {"session_id": item.session_id, "dish_id": item.dish_id},
            {"$inc": {"quantity": 1}}
        )
        existing["quantity"] += 1
        return CartItem(**existing)
    
    # Get dish details
    dish = await db.dishes.find_one({"id": item.dish_id}, {"_id": 0})
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")
    
    cart_item = CartItem(
        session_id=item.session_id,
        dish_id=item.dish_id,
        dish_name=dish["name"],
        dish_price=dish["price"],
        dish_image=dish["image_url"],
        quantity=1
    )
    
    await db.cart.insert_one(cart_item.model_dump())
    return cart_item

@api_router.put("/cart/update")
async def update_cart_item(item: CartItemUpdate):
    if item.quantity <= 0:
        await db.cart.delete_one({"session_id": item.session_id, "dish_id": item.dish_id})
        return {"message": "Item removed from cart"}
    
    result = await db.cart.update_one(
        {"session_id": item.session_id, "dish_id": item.dish_id},
        {"$set": {"quantity": item.quantity}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cart item not found")
    
    return {"message": "Cart updated successfully"}

@api_router.delete("/cart/remove/{session_id}/{dish_id}")
async def remove_from_cart(session_id: str, dish_id: str):
    result = await db.cart.delete_one({"session_id": session_id, "dish_id": dish_id})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Cart item not found")
    
    return {"message": "Item removed from cart"}

@api_router.delete("/cart/clear/{session_id}")
async def clear_cart(session_id: str):
    await db.cart.delete_many({"session_id": session_id})
    return {"message": "Cart cleared"}


# ==================== ORDER ROUTES ====================

@api_router.post("/orders", response_model=Order)
async def create_order(order: OrderCreate):
    # Generate order number
    order_count = await db.orders.count_documents({})
    order_number = f"ORD{order_count + 1:05d}"
    
    order_obj = Order(
        order_number=order_number,
        session_id=order.session_id,
        table_number=order.table_number,
        items=order.items,
        total=order.total
    )
    
    doc = order_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    await db.orders.insert_one(doc)
    
    # Create notification
    notification = Notification(
        order_id=order_obj.id,
        order_number=order_number,
        table_number=order.table_number,
        message=f"An order is placed from table {order.table_number}."
    )
    
    notif_doc = notification.model_dump()
    notif_doc['timestamp'] = notif_doc['timestamp'].isoformat()
    await db.notifications.insert_one(notif_doc)
    
    # Clear cart
    await db.cart.delete_many({"session_id": order.session_id})
    
    return order_obj

@api_router.get("/orders/history/{session_id}", response_model=List[Order])
async def get_order_history(session_id: str):
    orders = await db.orders.find({"session_id": session_id}, {"_id": 0}).sort("timestamp", -1).to_list(1000)
    
    for order in orders:
        if isinstance(order['timestamp'], str):
            order['timestamp'] = datetime.fromisoformat(order['timestamp'])
    
    return orders

@api_router.get("/orders/{order_id}", response_model=Order)
async def get_order(order_id: str):
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if isinstance(order['timestamp'], str):
        order['timestamp'] = datetime.fromisoformat(order['timestamp'])
    
    return Order(**order)


# ==================== NOTIFICATION ROUTES ====================

@api_router.get("/notifications", response_model=List[Notification])
async def get_notifications():
    notifications = await db.notifications.find({}, {"_id": 0}).sort("timestamp", -1).to_list(1000)
    
    for notif in notifications:
        if isinstance(notif['timestamp'], str):
            notif['timestamp'] = datetime.fromisoformat(notif['timestamp'])
    
    return notifications

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str):
    result = await db.notifications.update_one(
        {"id": notification_id},
        {"$set": {"read": True}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification marked as read"}

@api_router.get("/notifications/unread/count")
async def get_unread_count():
    count = await db.notifications.count_documents({"read": False})
    return {"count": count}


# ==================== ROOT ROUTE ====================

@api_router.get("/")
async def root():
    return {"message": "Cafetaria API is running"}


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
