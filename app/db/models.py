from sqlalchemy import Column, String, Integer, Float, DateTime, Text, JSON, Index, Boolean, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .connection import Base

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(Integer, primary_key=True)
    whatsapp_number = Column(String(20), unique=True, index=True, nullable=False)
    
    # --- Status & Identification ---
    status = Column(String(20), default="inactive") # active, expired, blocked
    plan_name = Column(String(50), nullable=True)   # Buddy Start, Buddy Pro, etc.
    is_recurring = Column(Boolean, default=False)   # True for (3,4,5), False for (1,2)
    plugnpay_customer_id = Column(String(100), nullable=True)
    
    # --- The Credit Economy ---
    credits = Column(Integer, default=15)           # Current balance (Trial = 15)
    total_purchased = Column(Integer, default=0)    # Lifetime credits bought
    message_count = Column(Integer, default=0)      # Total questions asked
    
    # --- Trial & Subscription Logic ---
    is_trial = Column(Boolean, default=True)
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True) # Expiry date
    
    # --- Timestamps ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship to profile
    profile = relationship("UserProfile", back_populates="subscription", uselist=False)


class UserProfile(Base):
    """D/E1: Lightweight user profile for personalized responses."""
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True)
    whatsapp_number = Column(
        String(20),
        ForeignKey("subscriptions.whatsapp_number"),
        unique=True,
        nullable=False,
    )
    weight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)
    age = Column(Integer, nullable=True)
    goals = Column(Text, nullable=True)               # e.g. "muscle gain", "weight loss"
    sport = Column(Text, nullable=True)                # e.g. "running", "cycling", "gym"
    dietary_preferences = Column(Text, nullable=True)  # e.g. "vegetarian", "no lactose"
    training_frequency = Column(Text, nullable=True)   # e.g. "4x per week"
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship back to subscription
    subscription = relationship("Subscription", back_populates="profile")


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    # WhatsApp Message ID (wamid) as Primary Key prevents duplicates
    message_id = Column(String(255), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ChatLog(Base):
    __tablename__ = "chat_logs"
    
    id = Column(Integer, primary_key=True)
    
    # --- Identification & Session ---
    # WhatsApp number is our primary session ID for LangChain
    whatsapp_number = Column(String(20), index=True, nullable=False)
    
    # --- Interaction Data ---
    user_message = Column(Text, nullable=False)
    bot_response = Column(Text, nullable=False)
    
    # --- LangChain Metadata (Optional but helpful) ---
    # We store the role-based history in a JSON format LangChain understands
    history_snapshot = Column(JSON, nullable=True) 

    # --- Your Strict Mode A Audit Fields ---
    response_type = Column(String(50)) # 'answered', 'refused', 'error'
    chunks_used = Column(JSON, nullable=True) # IDs and metadata of the PDF chunks

    # --- B4: Refusal Analytics ---
    refusal_category = Column(String(50), nullable=True)  # off_topic, medical_advice, inappropriate, no_context, unknown

    # --- B3: Token & Cost Tracking ---
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    model = Column(String(128), nullable=True)

    # --- Timestamps ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())

# High-performance index for finding a specific user's history
Index('idx_user_history', ChatLog.whatsapp_number, ChatLog.created_at)


# ===========================
# Admin Dashboard Models
# ===========================

class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(100), nullable=True)
    role = Column(String(20), nullable=False, default="support")  # 'admin' | 'support'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    actor_email = Column(String(255), nullable=False)
    action = Column(String(50), nullable=False)       # PLAN_CHANGE, STATUS_CHANGE, BLOCK, UNBLOCK, SEND_MESSAGE, LOGIN, etc.
    target_type = Column(String(20), nullable=True)    # 'user', 'alert', 'config'
    target_id = Column(String(100), nullable=True)     # whatsapp_number or alert ID
    details = Column(JSON, nullable=True)              # {from: 'monthly', to: 'quarterly', reason: '...'}
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ===========================
# B6: Alerts
# ===========================

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    alert_type = Column(String(50), nullable=False)    # cost_spike, usage_spike, expired_subs, high_refusal_rate
    severity = Column(String(20), nullable=False, default="warning")  # info, warning, critical
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active, acknowledged, resolved
    acknowledged_by = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    details = Column(JSON, nullable=True)              # {threshold, actual_value, period, etc.}
    created_at = Column(DateTime(timezone=True), server_default=func.now())

