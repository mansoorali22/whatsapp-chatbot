"""
Subscription service for managing user access and logging
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, date
import json

from db.connection import db
from app.config import settings

logger = logging.getLogger(__name__)


class SubscriptionService:
    async def get_subscription(self, whatsapp_number: str) -> Optional[Dict[str, Any]]:
        """Get subscription details for a WhatsApp number"""
        try:
            query = """
                SELECT id, whatsapp_number, status, plugnpay_customer_id,
                       subscription_start, subscription_end, message_count_today,
                       last_message_date, created_at, updated_at
                FROM subscriptions
                WHERE whatsapp_number = $1
            """
            row = await db.fetchrow(query, whatsapp_number)
            
            if row:
                return dict(row)
            return None
        
        except Exception as e:
            logger.error(f"Error fetching subscription: {e}", exc_info=True)
            return None
    
    async def create_or_update_subscription(
        self,
        whatsapp_number: str,
        status: str,
        plugnpay_customer_id: Optional[str] = None,
        subscription_start: Optional[datetime] = None,
        subscription_end: Optional[datetime] = None
    ) -> bool:
        """Create or update a subscription"""
        try:
            query = """
                INSERT INTO subscriptions (
                    whatsapp_number, status, plugnpay_customer_id,
                    subscription_start, subscription_end
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (whatsapp_number)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    plugnpay_customer_id = COALESCE(EXCLUDED.plugnpay_customer_id, subscriptions.plugnpay_customer_id),
                    subscription_start = COALESCE(EXCLUDED.subscription_start, subscriptions.subscription_start),
                    subscription_end = COALESCE(EXCLUDED.subscription_end, subscriptions.subscription_end),
                    updated_at = NOW()
            """
            await db.execute(
                query,
                whatsapp_number,
                status,
                plugnpay_customer_id,
                subscription_start,
                subscription_end
            )
            logger.info(f"Subscription created/updated for {whatsapp_number}")
            return True
        
        except Exception as e:
            logger.error(f"Error creating/updating subscription: {e}", exc_info=True)
            return False
    
    async def update_subscription_status(
        self,
        whatsapp_number: str,
        status: str,
        subscription_end: Optional[datetime] = None
    ) -> bool:
        """Update subscription status"""
        try:
            if subscription_end:
                query = """
                    UPDATE subscriptions
                    SET status = $1, subscription_end = $2, updated_at = NOW()
                    WHERE whatsapp_number = $3
                """
                await db.execute(query, status, subscription_end, whatsapp_number)
            else:
                query = """
                    UPDATE subscriptions
                    SET status = $1, updated_at = NOW()
                    WHERE whatsapp_number = $2
                """
                await db.execute(query, status, whatsapp_number)
            
            logger.info(f"Subscription status updated for {whatsapp_number}: {status}")
            return True
        
        except Exception as e:
            logger.error(f"Error updating subscription status: {e}", exc_info=True)
            return False
    
    async def check_rate_limit(self, whatsapp_number: str) -> bool:
        """Check if user is within rate limit"""
        try:
            subscription = await self.get_subscription(whatsapp_number)
            if not subscription:
                return False
            
            today = date.today()
            last_message_date = subscription.get("last_message_date")
            message_count = subscription.get("message_count_today", 0)
            
            # Reset count if it's a new day
            if not last_message_date or last_message_date != today:
                return True
            
            # Check limit
            return message_count < settings.DAILY_MESSAGE_LIMIT
        
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}", exc_info=True)
            return False
    
    async def increment_message_count(self, whatsapp_number: str) -> bool:
        """Increment today's message count"""
        try:
            query = """
                UPDATE subscriptions
                SET 
                    message_count_today = CASE 
                        WHEN last_message_date = CURRENT_DATE THEN message_count_today + 1
                        ELSE 1
                    END,
                    last_message_date = CURRENT_DATE,
                    updated_at = NOW()
                WHERE whatsapp_number = $1
            """
            await db.execute(query, whatsapp_number)
            return True
        
        except Exception as e:
            logger.error(f"Error incrementing message count: {e}", exc_info=True)
            return False
    
    async def log_chat(
        self,
        whatsapp_number: str,
        user_message: str,
        bot_response: str,
        response_type: str,
        chunks_used: Optional[List[int]] = None,
        tokens_used: Optional[int] = None,
        retrieval_score: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Log a chat interaction"""
        try:
            query = """
                INSERT INTO chat_logs (
                    whatsapp_number, user_message, bot_response, response_type,
                    chunks_used, tokens_used, retrieval_score, error_message
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """
            
            chunks_json = json.dumps(chunks_used) if chunks_used else None
            
            await db.execute(
                query,
                whatsapp_number,
                user_message,
                bot_response,
                response_type,
                chunks_json,
                tokens_used,
                retrieval_score,
                error_message
            )
            return True
        
        except Exception as e:
            logger.error(f"Error logging chat: {e}", exc_info=True)
            return False
    
    async def get_usage_stats(self, whatsapp_number: Optional[str] = None) -> Dict[str, Any]:
        """Get usage statistics"""
        try:
            if whatsapp_number:
                # Stats for specific user
                query = """
                    SELECT 
                        COUNT(*) as total_messages,
                        COUNT(*) FILTER (WHERE response_type = 'answered') as answered_count,
                        COUNT(*) FILTER (WHERE response_type = 'refused') as refused_count,
                        COUNT(*) FILTER (WHERE response_type = 'error') as error_count,
                        AVG(tokens_used) as avg_tokens,
                        MAX(created_at) as last_message
                    FROM chat_logs
                    WHERE whatsapp_number = $1
                """
                row = await db.fetchrow(query, whatsapp_number)
            else:
                # Overall stats
                query = """
                    SELECT 
                        COUNT(*) as total_messages,
                        COUNT(*) FILTER (WHERE response_type = 'answered') as answered_count,
                        COUNT(*) FILTER (WHERE response_type = 'refused') as refused_count,
                        COUNT(*) FILTER (WHERE response_type = 'error') as error_count,
                        COUNT(DISTINCT whatsapp_number) as unique_users,
                        AVG(tokens_used) as avg_tokens
                    FROM chat_logs
                """
                row = await db.fetchrow(query)
            
            return dict(row) if row else {}
        
        except Exception as e:
            logger.error(f"Error getting usage stats: {e}", exc_info=True)
            return {}