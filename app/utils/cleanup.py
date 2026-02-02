# app/utils/cleanup.py
import sys
import logging
from pathlib import Path
from sqlalchemy.orm import Session

from app.db.models import ProcessedMessage
from app.db.connection import get_db

# Ensure project root is on path (safe for cron / scripts)
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

logger = logging.getLogger(__name__)


def run_processed_message_cleanup():
    """
    Deletes ALL rows from the ProcessedMessage table.
    """
    db: Session = next(get_db())

    try:
        deleted = (
            db.query(ProcessedMessage)
            .delete(synchronize_session=False)
        )
        db.commit()

        logger.info(f"🗑 Deleted {deleted} processed messages")
        print(f"✅ Cleanup completed. Deleted {deleted} processed messages.")
        return deleted

    except Exception as e:
        db.rollback()
        logger.exception("❌ ProcessedMessage cleanup failed")
        raise e

    finally:
        db.close()
