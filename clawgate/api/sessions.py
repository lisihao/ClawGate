"""Session Management API - CRUD endpoints for conversation sessions.

Provides:
  GET    /v1/sessions                    - List active sessions
  GET    /v1/sessions/{session_id}       - Get session details (segments, messages)
  DELETE /v1/sessions/{session_id}       - Delete session and associated LTM
  POST   /v1/sessions/{session_id}/clear - Clear segment history, preserve LTM

All data is backed by ConversationStore's SQLite tables:
  - conversation_segments (24h TTL segments)
  - long_term_memories (7d TTL cross-session memories)
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import verify_api_key

logger = logging.getLogger("clawgate.api.sessions")

router = APIRouter(prefix="/v1/sessions", tags=["sessions"], dependencies=[Depends(verify_api_key)])

# Module-level references (set by init_sessions)
_context_manager = None
_db_path: Optional[Path] = None


def init_sessions(context_manager):
    """Initialize sessions module with context_manager dependency.

    Called from startup_event in main_v2.py.
    """
    global _context_manager, _db_path
    _context_manager = context_manager
    if context_manager and hasattr(context_manager, "db_store") and context_manager.db_store:
        _db_path = context_manager.db_store.db_path
    logger.info(f"[Sessions] Initialized (db_path={_db_path})")


def _get_context_db() -> sqlite3.Connection:
    """Open a connection to context.db with Row factory."""
    if not _db_path:
        raise HTTPException(status_code=503, detail="Session store not initialized")
    db_file = _db_path / "context.db"
    if not db_file.exists():
        raise HTTPException(status_code=503, detail="Context database not found")
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


# ========== Response Models ==========


class SessionSummary(BaseModel):
    session_id: str
    segment_count: int
    message_count: int
    last_activity: Optional[str] = None
    topic_types: List[str] = []


class SessionListResponse(BaseModel):
    sessions: List[SessionSummary]
    total: int


class SegmentDetail(BaseModel):
    segment_index: int
    topic_type: str
    summary: Optional[str] = None
    message_count: int
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    session_id: str
    segments: List[SegmentDetail]
    total_messages: int
    created_at: Optional[str] = None
    last_activity: Optional[str] = None


class DeleteResponse(BaseModel):
    success: bool
    deleted_segments: int
    deleted_memories: int


class ClearResponse(BaseModel):
    success: bool
    cleared_segments: int


# ========== Endpoints ==========


@router.get("", response_model=SessionListResponse)
async def list_sessions(limit: int = 50, offset: int = 0):
    """List active sessions with aggregated statistics.

    Returns sessions ordered by most recent activity, with segment counts
    and topic type breakdown.
    """
    conn = _get_context_db()
    try:
        cursor = conn.cursor()

        # Aggregate per conversation_id
        cursor.execute(
            """
            SELECT
                conversation_id,
                COUNT(*) AS segment_count,
                COALESCE(SUM(message_count), 0) AS message_count,
                MAX(created_at) AS last_activity
            FROM conversation_segments
            WHERE expires_at > datetime('now')
            GROUP BY conversation_id
            ORDER BY last_activity DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()

        sessions: List[SessionSummary] = []
        for row in rows:
            sid = row["conversation_id"]

            # Distinct topic types for this session
            cursor.execute(
                "SELECT DISTINCT topic_type FROM conversation_segments "
                "WHERE conversation_id = ? AND expires_at > datetime('now')",
                (sid,),
            )
            topic_types = [r["topic_type"] for r in cursor.fetchall() if r["topic_type"]]

            sessions.append(
                SessionSummary(
                    session_id=sid,
                    segment_count=row["segment_count"],
                    message_count=row["message_count"],
                    last_activity=row["last_activity"],
                    topic_types=topic_types,
                )
            )

        # Total distinct sessions (not just this page)
        cursor.execute(
            "SELECT COUNT(DISTINCT conversation_id) AS total "
            "FROM conversation_segments WHERE expires_at > datetime('now')"
        )
        total = cursor.fetchone()["total"]

        return SessionListResponse(sessions=sessions, total=total)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Sessions] list_sessions failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str):
    """Get detailed information about a specific session.

    Returns all segments with their summaries, message counts, and
    full message payloads.
    """
    conn = _get_context_db()
    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT segment_index, topic_type, summary, messages,
                   message_count, created_at, expires_at
            FROM conversation_segments
            WHERE conversation_id = ?
            ORDER BY segment_index ASC
            """,
            (session_id,),
        )
        rows = cursor.fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        segments: List[SegmentDetail] = []
        total_messages = 0
        created_at = None
        last_activity = None

        for row in rows:
            # Parse stored messages JSON
            try:
                messages_data = json.loads(row["messages"]) if row["messages"] else []
            except (json.JSONDecodeError, TypeError):
                messages_data = []

            segments.append(
                SegmentDetail(
                    segment_index=row["segment_index"],
                    topic_type=row["topic_type"],
                    summary=row["summary"],
                    message_count=row["message_count"] or 0,
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    messages=messages_data,
                )
            )
            total_messages += row["message_count"] or 0

            ts = row["created_at"]
            if ts:
                if created_at is None or ts < created_at:
                    created_at = ts
                if last_activity is None or ts > last_activity:
                    last_activity = ts

        return SessionDetailResponse(
            session_id=session_id,
            segments=segments,
            total_messages=total_messages,
            created_at=created_at,
            last_activity=last_activity,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Sessions] get_session({session_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.delete("/{session_id}", response_model=DeleteResponse)
async def delete_session(session_id: str):
    """Delete a session completely.

    Removes all conversation segments AND associated long-term memories
    for this session.
    """
    conn = _get_context_db()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM conversation_segments WHERE conversation_id = ?",
            (session_id,),
        )
        deleted_segments = cursor.rowcount

        cursor.execute(
            "DELETE FROM long_term_memories WHERE conversation_id = ?",
            (session_id,),
        )
        deleted_memories = cursor.rowcount

        conn.commit()

        logger.info(
            f"[Sessions] Deleted session={session_id}: "
            f"{deleted_segments} segments, {deleted_memories} memories"
        )
        return DeleteResponse(
            success=True,
            deleted_segments=deleted_segments,
            deleted_memories=deleted_memories,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Sessions] delete_session({session_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.post("/{session_id}/clear", response_model=ClearResponse)
async def clear_session(session_id: str):
    """Clear session conversation history while preserving long-term memories.

    This removes all conversation segments but keeps any LTM entries
    that were promoted from this session, allowing future context
    reconstruction to still benefit from cross-session recall.
    """
    conn = _get_context_db()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM conversation_segments WHERE conversation_id = ?",
            (session_id,),
        )
        cleared_segments = cursor.rowcount
        conn.commit()

        logger.info(
            f"[Sessions] Cleared session={session_id}: {cleared_segments} segments (LTM preserved)"
        )
        return ClearResponse(success=True, cleared_segments=cleared_segments)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Sessions] clear_session({session_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
