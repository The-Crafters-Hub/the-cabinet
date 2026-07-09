#!/usr/bin/env python3
"""
Cabinet REST API
================
FastAPI wrapper for The Cabinet Memory System.
Provides REST endpoints for semantic search and memory management.

Usage:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

Endpoints:
    GET  /health              - Health check (public)
    POST /memories            - Add a new memory (requires auth)
    POST /search              - Search memories (requires auth)
    GET  /memories/{table}    - List memories in a table (requires auth)
    DELETE /memories/{table}  - Clear all memories in a table (requires auth)

Authentication:
    All endpoints except /health and / require Bearer token.
    Set CABINET_API_KEY in .env file.
"""

import os
import sys
import json
import logging
import secrets
from typing import Optional, List
from datetime import datetime
from collections import defaultdict
from time import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from scripts.cabinet_memory import CabinetMemory

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load API key from environment
API_KEY = os.getenv("CABINET_API_KEY", "")

# ──────────────────────────────────────────────────────────────────────────────
# PRODUCTION SAFETY CHECK (P0-02)
# Halt startup if running in production without an API key.
# Set ENVIRONMENT=production in .env when deploying live.
# ──────────────────────────────────────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

if ENVIRONMENT == "production" and not API_KEY:
    # Use print here because logging may not be fully initialised yet
    print("FATAL: CABINET_API_KEY is not set but ENVIRONMENT=production.")
    print("Set CABINET_API_KEY in .env before starting in production mode.")
    sys.exit(1)

# Rate limiting configuration
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_REQUESTS = 100  # requests per window
rate_limit_storage: dict = defaultdict(list)

# Initialize FastAPI app
app = FastAPI(
    title="The Cabinet API",
    description="REST API for The Cabinet AI-powered memory system",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Security scheme
security = HTTPBearer(auto_error=False)

# CORS middleware - restrict in production
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://localhost:8888").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# M-03: Prometheus metrics instrumentation
# Exposes GET /metrics — scraped by prometheus.yml job: cabinet-api
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app)

# Initialize Cabinet Memory
memory = None


# ──────────────────────────────────────────────────────────────────────────────
# Authentication & Rate Limiting
# ──────────────────────────────────────────────────────────────────────────────

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify API key from Authorization header."""
    if not API_KEY:
        # No API key configured - allow all requests (development mode)
        logger.warning("No CABINET_API_KEY set - running in open mode. Set API key for production!")
        return True
    
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use: Authorization: Bearer <your-api-key>"
        )
    
    if not secrets.compare_digest(credentials.credentials, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )
    
    return True


def check_rate_limit(request: Request):
    """Simple in-memory rate limiting."""
    client_ip = request.client.host if request.client else "unknown"
    now = time()
    
    # Clean old entries
    rate_limit_storage[client_ip] = [
        t for t in rate_limit_storage[client_ip]
        if now - t < RATE_LIMIT_WINDOW
    ]
    
    # Check limit
    if len(rate_limit_storage[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Max {RATE_LIMIT_MAX_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds."
        )
    
    # Add current request
    rate_limit_storage[client_ip].append(now)
    return True


@app.on_event("startup")
async def startup_event():
    """Initialize Cabinet Memory on startup."""
    global memory
    try:
        memory = CabinetMemory()
        memory.create_tables()
        logger.info("Cabinet Memory initialized successfully.")
        if not API_KEY:
            logger.warning("CABINET_API_KEY not set. API is running in open mode.")
    except Exception as e:
        logger.error(f"Failed to initialize Cabinet Memory: {e}")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connection on shutdown."""
    global memory
    if memory:
        memory.close_connection()
        logger.info("Cabinet Memory connection closed.")


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ──────────────────────────────────────────────────────────────────────────────

class MemoryCreate(BaseModel):
    """Request model for creating a new memory."""
    content: str = Field(..., min_length=1, description="The text content to store")
    metadata: Optional[dict] = Field(default={}, description="Associated metadata")
    table_name: str = Field(default="ai_memory_sandbox", description="Target table name")


class SearchRequest(BaseModel):
    """Request model for searching memories."""
    query: str = Field(..., min_length=1, description="Search query text")
    n_results: int = Field(default=5, ge=1, le=50, description="Number of results to return")
    table_name: str = Field(default="ai_memory_sandbox", description="Table to search")


class MemoryResponse(BaseModel):
    """Response model for a single memory."""
    id: int
    content: str
    metadata: dict
    score: Optional[float] = None  # pgvector may return None for some rows


class SearchResponse(BaseModel):
    """Response model for search results."""
    query: str
    table: str
    results: List[MemoryResponse]
    count: int


class HealthResponse(BaseModel):
    """Response model for health check."""
    status: str
    timestamp: str
    database: str
    tables: List[str]


# ──────────────────────────────────────────────────────────────────────────────
# Public Endpoints (No Auth Required)
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Check API health and database connectivity. No authentication required."""
    try:
        conn = memory._connect()
        return HealthResponse(
            status="healthy",
            timestamp=datetime.now().isoformat(),
            database="connected",
            tables=["ai_memory_sandbox", "ai_memory_production", "ai_cinematic_library"]
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")


@app.get("/", tags=["System"])
async def root():
    """API root endpoint with available endpoints. No authentication required."""
    return {
        "name": "The Cabinet API",
        "version": "1.0.0",
        "status": "running",
        "auth_required": bool(API_KEY),
        "docs": "/docs",
        "endpoints": {
            "health": "/health (public)",
            "memories": "/memories (requires auth)",
            "search": "/search (requires auth)"
        }
    }


# ──────────────────────────────────────────────────────────────────────────────
# Protected Endpoints (Auth Required)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/memories", response_model=dict, tags=["Memories"])
async def create_memory(
    memory_data: MemoryCreate,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Add a new memory to the specified table. Requires authentication."""
    try:
        success = memory.add_memory(
            content=memory_data.content,
            metadata=memory_data.metadata,
            table_name=memory_data.table_name
        )
        
        if success:
            return {
                "success": True,
                "message": f"Memory added to {memory_data.table_name}",
                "table": memory_data.table_name
            }
        else:
            raise HTTPException(status_code=400, detail="Failed to add memory")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/search", response_model=SearchResponse, tags=["Search"])
async def search_memories(
    search: SearchRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Search for semantically similar memories. Requires authentication."""
    try:
        results = memory.search(
            query_text=search.query,
            n_results=search.n_results,
            table_name=search.table_name
        )
        
        formatted_results = [
            MemoryResponse(
                id=r['id'],
                content=r['content'],
                metadata=r['metadata'],
                score=r['score']
            )
            for r in results
        ]
        
        return SearchResponse(
            query=search.query,
            table=search.table_name,
            results=formatted_results,
            count=len(formatted_results)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories/{table_name}", response_model=dict, tags=["Memories"])
async def list_memories(
    table_name: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=100, description="Number of memories to return"),
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """List recent memories from a table. Requires authentication."""
    # Security: Use dictionary lookup to prevent SQL injection — never interpolate user input
    SAFE_TABLES = {
        "ai_memory_sandbox": "ai_memory_sandbox",
        "ai_memory_production": "ai_memory_production",
        "ai_cinematic_library": "ai_cinematic_library",
    }
    
    safe_name = SAFE_TABLES.get(table_name)
    if not safe_name:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid table name. Must be one of: {list(SAFE_TABLES.keys())}"
        )
    
    try:
        conn = memory._connect()
        with conn.cursor() as cursor:
            # safe_name is from our hardcoded dict, never from user input
            cursor.execute(f"""
                SELECT id, content, metadata, created_at
                FROM {safe_name}
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            
            results = cursor.fetchall()
            
            memories = [
                {
                    "id": row[0],
                    "content": row[1],
                    "metadata": row[2],
                    "created_at": row[3].isoformat() if row[3] else None
                }
                for row in results
            ]
            
            return {
                "table": table_name,
                "count": len(memories),
                "memories": memories
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories/{table_name}", response_model=dict, tags=["Memories"])
async def clear_memories(
    table_name: str,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Clear all memories from a table. USE WITH CAUTION. Requires authentication."""
    # Security: Use dictionary lookup to prevent SQL injection
    SAFE_TABLES = {
        "ai_memory_sandbox": "ai_memory_sandbox",
        "ai_memory_production": "ai_memory_production",
        "ai_cinematic_library": "ai_cinematic_library",
    }
    
    safe_name = SAFE_TABLES.get(table_name)
    if not safe_name:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid table name. Must be one of: {list(SAFE_TABLES.keys())}"
        )
    
    try:
        success = memory.delete_all_memories(safe_name)
        
        if success:
            return {
                "success": True,
                "message": f"All memories cleared from {safe_name}",
                "table": safe_name
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to clear memories")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Concierge Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/concierge/conversations", tags=["Concierge"])
async def get_concierge_conversations(
    request: Request,
    channel: Optional[str] = Query(default=None, description="Filter by channel"),
    limit: int = Query(default=50, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """List recent concierge conversations with optional channel filter."""
    try:
        conn = _pg_connect()  # uses Docker secret — fixed from broken os.getenv pattern
        with conn.cursor() as cur:
            if channel:
                cur.execute("""
                    SELECT id, session_id, channel, sender_id, original_message,
                           rewritten_query, ai_response, model_used, intent_detected,
                           escalated, response_time_ms, created_at
                    FROM concierge_conversations
                    WHERE channel = %s
                    ORDER BY created_at DESC LIMIT %s OFFSET %s
                """, (channel, limit, offset))
            else:
                cur.execute("""
                    SELECT id, session_id, channel, sender_id, original_message,
                           rewritten_query, ai_response, model_used, intent_detected,
                           escalated, response_time_ms, created_at
                    FROM concierge_conversations
                    ORDER BY created_at DESC LIMIT %s OFFSET %s
                """, (limit, offset))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            conversations = [dict(zip(cols, row)) for row in rows]
            for c in conversations:
                if c.get('created_at'):
                    c['created_at'] = c['created_at'].isoformat()
                if c.get('session_id'):
                    c['session_id'] = str(c['session_id'])
        conn.close()
        return {"conversations": conversations, "count": len(conversations), "offset": offset}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/concierge/stats", tags=["Concierge"])
async def get_concierge_stats(
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Get aggregate concierge statistics."""
    try:
        conn = _pg_connect()  # uses Docker secret — fixed from broken os.getenv pattern
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE escalated = true) as escalated,
                    COALESCE(AVG(response_time_ms), 0)::int as avg_response_ms,
                    COUNT(*) FILTER (WHERE channel = 'whatsapp') as whatsapp,
                    COUNT(*) FILTER (WHERE channel = 'messenger') as messenger,
                    COUNT(*) FILTER (WHERE channel = 'instagram') as instagram,
                    COUNT(*) FILTER (WHERE channel = 'web') as web
                FROM concierge_conversations
            """)
            row = cur.fetchone()
        conn.close()
        return {
            "total": row[0], "escalated": row[1], "avgResponse": row[2],
            "channels": {"whatsapp": row[3], "messenger": row[4], "instagram": row[5], "web": row[6]}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Concierge Chat Endpoint
# ──────────────────────────────────────────────────────────────────────────────

class ConciergeChat(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)

@app.post("/concierge/chat", tags=["Concierge"])
async def concierge_chat(
    body: ConciergeChat,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key),
):
    """AI chat endpoint for the Control Panel Concierge. Queries live DB context and calls Gemini."""
    import urllib.request
    import urllib.error

    # Read from Docker secret first (cabinet-mcp-server uses secrets, not plain env vars)
    _secret_path = "/run/secrets/gemini_api_key"
    if os.path.exists(_secret_path):
        with open(_secret_path) as _f:
            GEMINI_KEY = _f.read().strip()
    else:
        GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
    if not GEMINI_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    # ── Pull live DB context ──────────────────────────────────────────────────
    try:
        conn = _pg_connect()
        ctx_lines = []
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM students")
            ctx_lines.append(f"Total students: {cur.fetchone()[0]}")

            cur.execute("""
                SELECT
                    COALESCE(SUM(income_receivable + income_cash + income_bank), 0),
                    COALESCE(SUM(expense_payable + expense_cash + expense_bank), 0)
                FROM finance_transactions
            """)
            row = cur.fetchone()
            net = row[0] - row[1]
            ctx_lines.append(f"Total revenue: EGP {row[0]:,.0f}, Total expenses: EGP {row[1]:,.0f}, Net profit: EGP {net:,.0f}")

            cur.execute("""
                SELECT name, current_stock, minimum_stock
                FROM inventory_items
                WHERE current_stock <= minimum_stock
                ORDER BY current_stock ASC
                LIMIT 5
            """)
            low = cur.fetchall()
            if low:
                ctx_lines.append("Low stock items: " + ", ".join(f"{r[0]} ({r[1]} left, min {r[2]})" for r in low))

            cur.execute("""
                SELECT COUNT(*) FROM students
                WHERE created_at >= date_trunc('month', NOW())
            """)
            ctx_lines.append(f"New students this month: {cur.fetchone()[0]}")

            cur.execute("""
                SELECT category,
                    SUM(income_receivable + income_cash + income_bank) as total
                FROM finance_transactions
                WHERE (income_receivable + income_cash + income_bank) > 0
                GROUP BY category ORDER BY total DESC LIMIT 5
            """)
            cats = cur.fetchall()
            if cats:
                ctx_lines.append("Revenue by category: " + ", ".join(f"{r[0]}: EGP {r[1]:,.0f}" for r in cats))

        conn.close()
        db_context = "\n".join(ctx_lines)
    except Exception as e:
        logger.warning(f"Concierge DB context fetch failed: {e}")
        db_context = "Live database context unavailable."

    # ── Call Gemini ───────────────────────────────────────────────────────────
    system_prompt = (
        "You are HAMADA, the AI business intelligence assistant for The Crafters Hub — "
        "a woodworking and woodturning school in Egypt. "
        "Answer questions about the business using the live data context provided. "
        "Be concise, direct, and professional. Use EGP for currency. "
        "Never invent data not in the context. If data is missing, say so.\n\n"
        f"LIVE BUSINESS DATA:\n{db_context}"
    )
    payload = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": body.message}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"maxOutputTokens": 512, "temperature": 0.3}
    }).encode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_KEY}"
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        answer = result["candidates"][0]["content"]["parts"][0]["text"]
        return {"response": answer}
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        logger.error(f"Gemini API error: {e.code} — {err}")
        raise HTTPException(status_code=502, detail=f"Gemini error: {e.code}")
    except Exception as e:
        logger.error(f"Concierge chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Heritage Archive Endpoint
# Receives artisan technique submissions via WhatsApp (or any channel).
# Gemini extracts structured data → pgvector embeds → artisan_techniques table.
# Added: July 2026 — Phase A of The Cabinet Heritage Archive
# ──────────────────────────────────────────────────────────────────────────────

class ArchiveSubmission(BaseModel):
    """Request model for artisan technique submission."""
    raw_text: str = Field(..., min_length=5, max_length=3000,
                          description="Full raw message from artisan")
    artisan_name: Optional[str] = Field(default=None, description="Artisan's name if known")
    whatsapp_media_url: Optional[str] = Field(default=None,
                          description="Direct URL to WhatsApp photo (Meta CDN)")
    source_url: Optional[str] = Field(default=None,
                          description="Google Drive link artisan shared for full video")
    submission_channel: str = Field(default="whatsapp",
                          description="whatsapp / web / direct")


@app.post("/archive/submit", tags=["Heritage Archive"])
async def submit_technique(
    submission: ArchiveSubmission,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key),
):
    """
    Archive a traditional artisan technique.
    Gemini 3.5 Flash extracts structured data from the raw submission text.
    If a WhatsApp photo URL is provided, Gemini performs multimodal analysis.
    Falls back gracefully — raw text is always stored even if Gemini fails.
    """
    import urllib.request
    import urllib.error
    import base64

    # ── Read Gemini key (Docker secret first, env fallback) ───────────────────
    _secret_path = "/run/secrets/gemini_api_key"
    if os.path.exists(_secret_path):
        with open(_secret_path) as _f:
            GEMINI_KEY = _f.read().strip()
    else:
        GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
    if not GEMINI_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    # ── Build Gemini extraction prompt ────────────────────────────────────────
    extraction_prompt = (
        "You are an expert in traditional woodworking, woodturning, and artisanal crafts.\n"
        "An artisan has shared a technique submission. Extract structured information from it.\n\n"
        f"Submission:\n{submission.raw_text}\n\n"
        "Return a JSON object with EXACTLY these fields (no markdown, no explanation):\n"
        "{\n"
        '  "technique_name": "name in original language + English if possible",\n'
        '  "artisan_name": "name if mentioned, else null",\n'
        '  "region": "city, region, or country where they learned it, else null",\n'
        '  "category": "one of: carving / woodturning / joinery / inlay / marquetry / finishing / other",\n'
        '  "materials": ["list of woods or materials mentioned"],\n'
        '  "tools": ["list of tools mentioned"],\n'
        '  "description": "2-3 sentence English description of the technique",\n'
        '  "risk_level": "critical (likely disappearing) / rare (few practitioners) / common"\n'
        "}"
    )

    # ── Call Gemini ───────────────────────────────────────────────────────────
    extracted = None
    try:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-3.5-flash:generateContent?key={GEMINI_KEY}"
        )

        parts = [{"text": extraction_prompt}]

        # Add image part if WhatsApp media URL provided
        if submission.whatsapp_media_url:
            try:
                img_req = urllib.request.Request(
                    submission.whatsapp_media_url,
                    headers={"User-Agent": "CabinetArchive/1.0"}
                )
                with urllib.request.urlopen(img_req, timeout=10) as img_resp:
                    img_bytes = img_resp.read()
                parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": base64.b64encode(img_bytes).decode("utf-8")
                    }
                })
                logger.info("WhatsApp image fetched and attached to Gemini request")
            except Exception as img_err:
                logger.warning(f"Could not fetch WhatsApp media, proceeding text-only: {img_err}")

        payload = json.dumps({
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": 1500,
                "temperature": 0.1
            }
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
        # Use regex to find JSON object — handles code fences, Arabic text, extra commentary
        import re as _re
        json_match = _re.search(r'\{[\s\S]*\}', raw_text)
        if not json_match:
            raise ValueError(f"No JSON object found in Gemini response: {raw_text[:200]}")
        extracted = json.loads(json_match.group())
        logger.info(f"Gemini extracted technique: {extracted.get('technique_name')}")

    except Exception as e:
        logger.warning(f"Gemini extraction failed ({e}) — storing raw submission as fallback")
        extracted = {
            "technique_name": "Unknown (extraction pending)",
            "artisan_name": submission.artisan_name,
            "region": None,
            "category": "other",
            "materials": [],
            "tools": [],
            "description": submission.raw_text[:500],
            "risk_level": "unknown"
        }

    # ── Generate embedding via gemini-embedding-2 (768-dim truncated) ──────────────
    embedding = None
    try:
        embed_text = (
            f"{extracted.get('technique_name', '')} "
            f"{extracted.get('description', '')} "
            f"{extracted.get('category', '')} "
            f"{extracted.get('region', '')}"
        ).strip()

        embed_url = (
            f"https://generativelanguage.googleapis.com/v1/models/"
            f"gemini-embedding-2:embedContent?key={GEMINI_KEY}"
        )
        embed_payload = json.dumps({
            "content": {"parts": [{"text": embed_text}]},
            "taskType": "RETRIEVAL_DOCUMENT",
            "outputDimensionality": 768
        }).encode("utf-8")

        embed_req = urllib.request.Request(
            embed_url, data=embed_payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(embed_req, timeout=20) as embed_resp:
            embed_result = json.loads(embed_resp.read())
        embedding = embed_result["embedding"]["values"]
        logger.info(f"Embedding generated: {len(embedding)} dimensions")

    except Exception as e:
        logger.warning(f"Embedding failed ({e}) — storing without vector (searchable later)")

    # ── Normalize values to fit DB column constraints ────────────────────────
    _risk_raw = str(extracted.get("risk_level", "unknown")).lower().split()[0] if extracted.get("risk_level") else "unknown"
    _risk = _risk_raw if _risk_raw in {"critical", "rare", "common"} else "unknown"
    _cat_raw = str(extracted.get("category", "other")).lower().strip()
    _cat = _cat_raw if _cat_raw in {"carving","woodturning","joinery","inlay","marquetry","finishing","other"} else "other"

    # ── Insert into PostgreSQL ────────────────────────────────────────────────
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO artisan_techniques (
                    technique_name, artisan_name, region, category,
                    materials, tools, description, raw_submission_text,
                    source_url, whatsapp_media_id, submission_channel,
                    risk_level, embedding_vector
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, technique_name, category, risk_level, created_at
            """, (
                extracted.get("technique_name", "Unknown"),
                extracted.get("artisan_name") or submission.artisan_name,
                extracted.get("region"),
                _cat,
                extracted.get("materials", []),
                extracted.get("tools", []),
                extracted.get("description"),
                submission.raw_text,
                submission.source_url,
                submission.whatsapp_media_url,
                submission.submission_channel,
                _risk,
                embedding
            ))
            row = cur.fetchone()
            conn.commit()
        conn.close()

        logger.info(f"Technique archived: #{row[0]} — {row[1]} [{row[2]}] risk={row[3]}")
        return {
            "success": True,
            "technique_id": row[0],
            "technique_name": row[1],
            "category": row[2],
            "risk_level": row[3],
            "archived_at": row[4].isoformat(),
            "embedded": embedding is not None,
            "message": f"Technique '{row[1]}' archived successfully."
        }

    except Exception as e:
        logger.error(f"DB insert failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to store technique: {str(e)}")


# ──────────────────────────────────────────────────────────────────────────────
# Wix Cache Sync Endpoint
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/sync/wix-cache", tags=["Sync"])
async def sync_wix_cache(
    payload: dict,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Receive Wix cache data from n8n workflow and upsert into PostgreSQL."""
    try:
        conn = _pg_connect()  # uses Docker secret — fixed from broken os.getenv pattern
        
        services = payload.get("services", [])
        bookings = payload.get("bookings", [])
        
        with conn.cursor() as cur:
            # Upsert services
            services_synced = 0
            for svc in services:
                cur.execute("""
                    INSERT INTO wix_services_cache
                    (wix_service_id, service_type, title, price_egp, duration_minutes, category, status, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (wix_service_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        price_egp = EXCLUDED.price_egp,
                        duration_minutes = EXCLUDED.duration_minutes,
                        category = EXCLUDED.category,
                        synced_at = CURRENT_TIMESTAMP
                """, (
                    svc.get("wix_id"),
                    svc.get("service_type", "BOOKING_SERVICE"),
                    svc.get("title", "Unknown"),
                    svc.get("price", 0.0),
                    svc.get("duration"),
                    svc.get("category"),
                    svc.get("status", "ACTIVE")
                ))
                services_synced += 1
            
            # Upsert bookings (simplified to customer_interactions)
            bookings_synced = 0
            for booking in bookings:
                cur.execute("""
                    INSERT INTO customer_interactions
                    (wix_contact_id, message_text, outcome, metadata, created_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT DO NOTHING
                """, (
                    booking.get("wix_contact_id"),
                    f"Booking: {booking.get('full_name')} - {booking.get('wix_service_id')}",
                    "booked",
                    json.dumps({"email": booking.get("email"), "phone": booking.get("phone")})
                ))
                bookings_synced += 1
        
        conn.commit()
        conn.close()
        
        logger.info(f"Wix Cache Sync: {services_synced} services, {bookings_synced} bookings synced")
        
        return {
            "status": "ok",
            "services_synced": services_synced,
            "bookings_synced": bookings_synced,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Wix Cache Sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ──────────────────────────────────────────────────────────────────────────────
# Dashboard Stats Endpoint
# ──────────────────────────────────────────────────────────────────────────────

def _pg_connect():
    """Helper: connect to PostgreSQL using env vars."""
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        user=os.getenv("POSTGRES_USER", "crafter_admin"),
        password=get_pg_password(),
        dbname=os.getenv("POSTGRES_DB", "thecraftershub")
    )


def get_pg_password() -> str:
    """Read PostgreSQL password from Docker secret file or environment variable."""
    secret_path = "/run/secrets/postgres_password"
    if os.path.exists(secret_path):
        with open(secret_path, "r") as f:
            return f.read().strip()
    return os.getenv("POSTGRES_PASSWORD", "")


@app.get("/dashboard/stats", tags=["Dashboard"])
async def get_dashboard_stats(request: Request, _: bool = Depends(check_rate_limit), __: bool = Depends(verify_api_key)):
    """Get live business metrics for the Dashboard page."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM students")
            total_students = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM concierge_conversations
                WHERE created_at >= NOW() - INTERVAL '24 hours'
            """)
            ai_convos_24h = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM finance_transactions
                WHERE income_receivable > 0
                AND (income_cash = 0 AND income_bank = 0)
                AND transaction_date >= NOW() - INTERVAL '90 days'
            """)
            unpaid_invoices = cur.fetchone()[0]

            cur.execute("""
                SELECT COALESCE(SUM(income_cash + income_bank), 0)
                FROM finance_transactions
                WHERE transaction_date >= DATE_TRUNC('month', NOW())
            """)
            revenue_this_month = float(cur.fetchone()[0])

        conn.close()
        return {
            "total_students": total_students,
            "ai_convos_24h": ai_convos_24h,
            "unpaid_invoices": unpaid_invoices,
            "revenue_this_month": revenue_this_month,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Students Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/students", tags=["Students"])
async def get_students(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    search: Optional[str] = Query(default=None),
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key),
):
    """List students from PostgreSQL with optional search."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            # Stats
            cur.execute("SELECT COUNT(*) FROM students")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(DISTINCT student_id) FROM registrations
                WHERE registration_date >= DATE_TRUNC('month', NOW())
            """)
            active_month = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM registrations")
            total_registrations = cur.fetchone()[0]

            # Student list
            if search:
                cur.execute("""
                    SELECT id, full_name, email, phone, created_at
                    FROM students
                    WHERE full_name ILIKE %s OR email ILIKE %s
                    ORDER BY created_at DESC LIMIT %s
                """, (f"%{search}%", f"%{search}%", limit))
            else:
                cur.execute("""
                    SELECT id, full_name, email, phone, created_at
                    FROM students ORDER BY created_at DESC LIMIT %s
                """, (limit,))

            rows = cur.fetchall()
            students = [
                {
                    "id": r[0],
                    "name": r[1] or "Unknown",
                    "email": r[2] or "",
                    "phone": r[3] or "",
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
        conn.close()
        return {
            "students": students,
            "total": total,
            "active_this_month": active_month,
            "total_registrations": total_registrations,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Finance Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/finance/summary", tags=["Finance"])
async def get_finance_summary(request: Request, _: bool = Depends(check_rate_limit), __: bool = Depends(verify_api_key)):
    """Get aggregate finance KPIs."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(SUM(income_receivable + income_cash + income_bank), 0) as total_income,
                    COALESCE(SUM(expense_payable + expense_cash + expense_bank), 0) as total_expense
                FROM finance_transactions
            """)
            row = cur.fetchone()
            total_income = float(row[0])
            total_expense = float(row[1])

            # Monthly trend (last 6 months)
            cur.execute("""
                SELECT
                    TO_CHAR(DATE_TRUNC('month', transaction_date), 'Mon YY') as month,
                    COALESCE(SUM(income_receivable + income_cash + income_bank), 0)::float as revenue,
                    COALESCE(SUM(expense_payable + expense_cash + expense_bank), 0)::float as expenses
                FROM finance_transactions
                WHERE transaction_date >= NOW() - INTERVAL '6 months'
                GROUP BY DATE_TRUNC('month', transaction_date)
                ORDER BY DATE_TRUNC('month', transaction_date)
            """)
            monthly = [
                {"month": r[0], "revenue": float(r[1]), "expenses": float(r[2])}
                for r in cur.fetchall()
            ]

            # Category breakdown
            cur.execute("""
                SELECT category, COALESCE(SUM(income_receivable + income_cash + income_bank), 0)::float as total
                FROM finance_transactions
                WHERE (income_receivable + income_cash + income_bank) > 0 AND category IS NOT NULL AND category != ''
                GROUP BY category ORDER BY total DESC LIMIT 5
            """)
            categories = [{"name": r[0], "value": float(r[1])} for r in cur.fetchall()]

        conn.close()
        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "net_profit": total_income - total_expense,
            "monthly_trend": monthly,
            "categories": categories,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/finance/transactions", tags=["Finance"])
async def get_finance_transactions(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key),
):
    """Get recent finance transactions."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, transaction_date, description, category,
                       income_receivable + income_cash + income_bank as income,
                       expense_payable + expense_cash + expense_bank as expense
                FROM finance_transactions
                ORDER BY transaction_date DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            transactions = [
                {
                    "id": r[0],
                    "date": r[1].strftime("%Y-%m-%d") if r[1] else "",
                    "description": r[2] or "",
                    "category": r[3] or "Uncategorized",
                    "amount": float(r[4]) if float(r[4]) > 0 else -float(r[5]),
                    "type": "income" if float(r[4]) > 0 else "expense",
                }
                for r in rows
            ]
        conn.close()
        return {"transactions": transactions, "count": len(transactions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Inventory Endpoints — real stock levels from inventory_items + stock_movements
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/inventory/summary", tags=["Inventory"])
async def get_inventory_summary(request: Request, _: bool = Depends(check_rate_limit), __: bool = Depends(verify_api_key)):
    """Get inventory items with live stock levels and recent purchase history."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            # Live stock levels per item
            cur.execute("""
                SELECT id, name, category, unit, current_stock, minimum_stock,
                       reorder_point, cost_per_unit, supplier, updated_at
                FROM inventory_items ORDER BY category, name
            """)
            rows = cur.fetchall()
            items = [
                {
                    "id": r[0], "name": r[1], "category": r[2], "unit": r[3],
                    "current_stock": float(r[4]), "minimum_stock": float(r[5]),
                    "reorder_point": float(r[6]),
                    "cost_per_unit": float(r[7]) if r[7] else None,
                    "supplier": r[8] or "",
                    "low_stock": float(r[4]) <= float(r[5]),
                    "last_updated": r[9].strftime("%Y-%m-%d") if r[9] else None,
                }
                for r in rows
            ]

            # Category totals derived from finance_transactions
            cur.execute("SELECT inventory_category, purchase_count, total_invested, last_purchase FROM inventory_summary")
            cat_rows = cur.fetchall()
            categories = [
                {
                    "name": r[0], "purchase_count": r[1],
                    "total_invested": float(r[2]),
                    "last_purchase": r[3].strftime("%Y-%m-%d") if r[3] else None,
                }
                for r in cat_rows
            ]

            # Recent stock movements
            cur.execute("""
                SELECT sm.id, ii.name, sm.movement_type, sm.quantity, ii.unit,
                       sm.notes, sm.created_at, sm.created_by
                FROM stock_movements sm
                JOIN inventory_items ii ON ii.id = sm.item_id
                ORDER BY sm.created_at DESC LIMIT 25
            """)
            rows = cur.fetchall()
            movements = [
                {
                    "id": r[0], "item_name": r[1], "type": r[2],
                    "quantity": float(r[3]), "unit": r[4],
                    "notes": r[5] or "", "date": r[6].strftime("%Y-%m-%d") if r[6] else "",
                    "by": r[7] or "system",
                }
                for r in rows
            ]

            # Summary stats
            low_stock_count = sum(1 for i in items if i["low_stock"])
            cur.execute("""
                SELECT COUNT(*), COALESCE(SUM(expense_cash+expense_bank+expense_payable),0)::numeric(10,0)
                FROM finance_transactions
                WHERE category IN ('Materials','Equipment','Sales (Out)','Sales Out')
                  AND (expense_cash+expense_bank+expense_payable) > 0
            """)
            row = cur.fetchone()
            totals = {"total_purchases": row[0], "total_invested": float(row[1]),
                      "low_stock_alerts": low_stock_count}

        conn.close()
        return {"items": items, "categories": categories, "movements": movements, "totals": totals}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UsageLog(BaseModel):
    item_id: int
    quantity: float = Field(..., gt=0)
    notes: Optional[str] = ""


@app.post("/inventory/log-usage", tags=["Inventory"])
async def log_inventory_usage(
    usage: UsageLog,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Log material consumption — decrements current_stock via trigger."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            # Verify item exists
            cur.execute("SELECT name, current_stock, unit FROM inventory_items WHERE id = %s", (usage.item_id,))
            item = cur.fetchone()
            if not item:
                raise HTTPException(status_code=404, detail="Item not found")
            if float(item[1]) < usage.quantity:
                raise HTTPException(status_code=400,
                    detail=f"Insufficient stock. Have {item[1]} {item[2]}, requested {usage.quantity}")
            cur.execute("""
                INSERT INTO stock_movements (item_id, movement_type, quantity, notes, created_by)
                VALUES (%s, 'consumption', %s, %s, 'dashboard')
                RETURNING id
            """, (usage.item_id, usage.quantity, usage.notes or "Manual usage log"))
            movement_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return {"success": True, "movement_id": movement_id,
                "message": f"Logged {usage.quantity} {item[2]} of {item[0]}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Knowledge Base Search (used by WhatsApp agent) ──────────────────────────

class KBSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=3, ge=1, le=10)


@app.post("/search/kb", tags=["Search"])
async def search_knowledge_base(
    body: KBSearchRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Semantic search over knowledge_embeddings table using pgvector.
    Used by the WhatsApp AI agent to retrieve grounded KB context before answering.
    Returns top N results with question, answer, category, and similarity score.
    """
    try:
        import urllib.request
        import urllib.error
        # Load Gemini key (Docker secret first, then env var)
        _secret_path = "/run/secrets/gemini_api_key"
        if os.path.exists(_secret_path):
            with open(_secret_path) as _f:
                GEMINI_KEY = _f.read().strip()
        else:
            GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
        if not GEMINI_KEY:
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

        # 1. Embed the query
        embed_url = (
            f"https://generativelanguage.googleapis.com/v1/models/"
            f"gemini-embedding-2:embedContent?key={GEMINI_KEY}"
        )
        embed_payload = json.dumps({
            "content": {"parts": [{"text": body.query}]},
            "taskType": "RETRIEVAL_QUERY",
            "outputDimensionality": 768
        }).encode("utf-8")
        embed_req = urllib.request.Request(
            embed_url, data=embed_payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(embed_req, timeout=15) as resp:
            embed_result = json.loads(resp.read())
        query_vector = embed_result["embedding"]["values"]

        # 2. pgvector cosine similarity search on knowledge_embeddings
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, question, answer, category, confidence,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM knowledge_embeddings
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (query_vector, query_vector, body.limit))
            rows = cur.fetchall()
        conn.close()

        # Filter by similarity threshold — only pass genuinely relevant results to Gemini
        # KB scores are in 0.05–0.5 range; 0.12 cuts noise while keeping relevant matches
        MIN_SIMILARITY = 0.12
        results = [
            {
                "id": row[0],
                "question": row[1],
                "answer": row[2],
                "category": row[3],
                "confidence": float(row[4]) if row[4] else None,
                "similarity": round(float(row[5]), 4) if row[5] else None
            }
            for row in rows
            if row[5] is not None and float(row[5]) >= MIN_SIMILARITY
        ]

        logger.info(f"KB search: '{body.query[:50]}' -> {len(results)} relevant results (threshold={MIN_SIMILARITY})")
        return {"query": body.query, "results": results, "count": len(results)}

    except Exception as e:
        logger.error(f"KB search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/settings/ai-mode", tags=["Settings"])
async def get_ai_mode(
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Return current AI Concierge mode: 'active' or 'paused'."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM setting WHERE key = 'ai_concierge_mode'")
            row = cur.fetchone()
        conn.close()
        mode = row[0] if row else "active"
        return {"mode": mode, "is_active": mode == "active"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class AIModeUpdate(BaseModel):
    mode: str = Field(..., pattern="^(active|paused)$")


@app.post("/settings/ai-mode", tags=["Settings"])
async def set_ai_mode(
    body: AIModeUpdate,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Set AI Concierge mode to 'active' or 'paused'."""
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO setting (key, value) VALUES ('ai_concierge_mode', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (body.mode,))
        conn.commit()
        conn.close()
        logger.info(f"AI Concierge mode set to: {body.mode}")
        return {"success": True, "mode": body.mode, "is_active": body.mode == "active"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Shadow Conversations — Tone Learning & AI Quality Capture
# Captures AI draft responses + Mostafa's actual replies for training data
# Tampermonkey script on web.whatsapp.com POSTs to /shadow/log-reply
# n8n workflow POSTs AI draft to /shadow/log-ai-response
# ──────────────────────────────────────────────────────────────────────────────

class ShadowAILogRequest(BaseModel):
    """Posted by n8n after AI generates a response."""
    customer_wa_id: str = Field(..., description="Customer WhatsApp ID (phone number)")
    customer_name: Optional[str] = Field(None, description="Customer display name")
    customer_message: str = Field(..., description="Customer's original message")
    customer_message_wamid: Optional[str] = Field(None, description="WhatsApp message ID")
    ai_proposed_response: str = Field(..., description="AI's generated response text")


class ShadowReplyLogRequest(BaseModel):
    """Posted by Tampermonkey when Mostafa sends a message on WhatsApp Web."""
    customer_wa_id: str = Field(..., description="Customer phone number from WhatsApp Web URL/DOM")
    mostafa_actual_response: str = Field(..., description="Text Mostafa actually sent")
    capture_source: str = Field(default="tampermonkey", description="tampermonkey | manual")


class ShadowRatingRequest(BaseModel):
    """Rate a training pair from the Control Panel."""
    shadow_id: int
    quality_rating: str = Field(..., description="better | similar | worse")
    notes: Optional[str] = None


@app.post("/shadow/log-ai-response", tags=["Shadow Mode"])
async def log_ai_response(
    body: ShadowAILogRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """
    Called by n8n after AI generates a response.
    Stores the customer message + AI draft for later comparison with Mostafa's reply.
    """
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO shadow_conversations
                    (customer_wa_id, customer_name, customer_message,
                     customer_message_wamid, ai_proposed_response, ai_responded_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (customer_message_wamid) DO UPDATE SET
                    ai_proposed_response = EXCLUDED.ai_proposed_response,
                    ai_responded_at = NOW()
                RETURNING id
            """, (
                body.customer_wa_id,
                body.customer_name,
                body.customer_message,
                body.customer_message_wamid,
                body.ai_proposed_response
            ))
            row = cur.fetchone()
            shadow_id = row[0] if row else None
        conn.commit()
        conn.close()
        logger.info(f"Shadow AI response logged for wa_id={body.customer_wa_id}, id={shadow_id}")
        return {"success": True, "shadow_id": shadow_id}
    except Exception as e:
        logger.error(f"Shadow AI log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shadow/log-reply", tags=["Shadow Mode"])
async def log_mostafa_reply(
    body: ShadowReplyLogRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """
    Called by Tampermonkey userscript when Mostafa sends a message on WhatsApp Web.
    Matches to the most recent unmatched shadow_conversation for that customer phone.
    This creates a complete training pair: (customer_msg, ai_draft, mostafa_actual).
    """
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            # Find the most recent unmatched shadow record for this customer
            # within the last 24 hours
            cur.execute("""
                UPDATE shadow_conversations
                SET mostafa_actual_response = %s,
                    mostafa_responded_at = NOW(),
                    capture_source = %s
                WHERE id = (
                    SELECT id FROM shadow_conversations
                    WHERE customer_wa_id = %s
                      AND mostafa_actual_response IS NULL
                      AND created_at > NOW() - INTERVAL '24 hours'
                    ORDER BY created_at DESC
                    LIMIT 1
                )
                RETURNING id, customer_message, ai_proposed_response
            """, (body.mostafa_actual_response, body.capture_source, body.customer_wa_id))

            row = cur.fetchone()
            if row:
                shadow_id, customer_msg, ai_draft = row
                conn.commit()
                conn.close()
                logger.info(f"Training pair COMPLETE: shadow_id={shadow_id}, wa_id={body.customer_wa_id}")
                return {
                    "success": True,
                    "matched": True,
                    "shadow_id": shadow_id,
                    "training_pair_complete": True,
                    "customer_message_preview": (customer_msg or "")[:80],
                    "ai_draft_preview": (ai_draft or "")[:80],
                    "mostafa_reply_preview": body.mostafa_actual_response[:80]
                }
            else:
                # No matching shadow record — store as standalone reply
                cur.execute("""
                    INSERT INTO shadow_conversations
                        (customer_wa_id, customer_message, mostafa_actual_response,
                         mostafa_responded_at, capture_source)
                    VALUES (%s, '[no AI draft captured]', %s, NOW(), %s)
                    RETURNING id
                """, (body.customer_wa_id, body.mostafa_actual_response, body.capture_source))
                row = cur.fetchone()
                conn.commit()
                conn.close()
                logger.info(f"Mostafa reply stored without AI match: wa_id={body.customer_wa_id}")
                return {
                    "success": True,
                    "matched": False,
                    "shadow_id": row[0] if row else None,
                    "note": "No recent AI draft found for this customer. Stored as standalone reply."
                }
    except Exception as e:
        logger.error(f"Shadow reply log error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shadow/rate", tags=["Shadow Mode"])
async def rate_shadow_pair(
    body: ShadowRatingRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """Rate a training pair (better/similar/worse) from the Control Panel."""
    if body.quality_rating not in ("better", "similar", "worse"):
        raise HTTPException(status_code=400, detail="quality_rating must be: better, similar, or worse")
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE shadow_conversations
                SET quality_rating = %s, notes = %s
                WHERE id = %s
                RETURNING id
            """, (body.quality_rating, body.notes, body.shadow_id))
            row = cur.fetchone()
        conn.commit()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Shadow record {body.shadow_id} not found")
        return {"success": True, "shadow_id": body.shadow_id, "quality_rating": body.quality_rating}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/shadow/training-pairs", tags=["Shadow Mode"])
async def get_training_pairs(
    limit: int = Query(default=50, ge=1, le=200),
    only_complete: bool = Query(default=True, description="Only return pairs with both AI draft and Mostafa reply"),
    request: Request = None,
    _: bool = Depends(check_rate_limit),
    __: bool = Depends(verify_api_key)
):
    """
    Retrieve training pairs for the Control Panel review tab and tone extraction.
    Returns conversations where both AI draft and Mostafa's actual reply are captured.
    """
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            if only_complete:
                cur.execute("""
                    SELECT id, customer_wa_id, customer_name, customer_message,
                           ai_proposed_response, mostafa_actual_response,
                           quality_rating, capture_source, created_at,
                           mostafa_responded_at
                    FROM shadow_conversations
                    WHERE ai_proposed_response IS NOT NULL
                      AND mostafa_actual_response IS NOT NULL
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute("""
                    SELECT id, customer_wa_id, customer_name, customer_message,
                           ai_proposed_response, mostafa_actual_response,
                           quality_rating, capture_source, created_at,
                           mostafa_responded_at
                    FROM shadow_conversations
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
            rows = cur.fetchall()

            # Stats
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE ai_proposed_response IS NOT NULL AND mostafa_actual_response IS NOT NULL) AS complete_pairs,
                    COUNT(*) FILTER (WHERE quality_rating = 'better') AS ai_better,
                    COUNT(*) FILTER (WHERE quality_rating = 'similar') AS ai_similar,
                    COUNT(*) FILTER (WHERE quality_rating = 'worse') AS ai_worse,
                    COUNT(*) AS total
                FROM shadow_conversations
            """)
            stats = cur.fetchone()
        conn.close()

        pairs = [
            {
                "id": r[0],
                "customer_wa_id": r[1],
                "customer_name": r[2],
                "customer_message": r[3],
                "ai_proposed_response": r[4],
                "mostafa_actual_response": r[5],
                "quality_rating": r[6],
                "capture_source": r[7],
                "created_at": r[8].isoformat() if r[8] else None,
                "mostafa_responded_at": r[9].isoformat() if r[9] else None,
            }
            for r in rows
        ]
        return {
            "pairs": pairs,
            "count": len(pairs),
            "stats": {
                "complete_pairs": stats[0],
                "ai_better": stats[1],
                "ai_similar": stats[2],
                "ai_worse": stats[3],
                "total": stats[4]
            } if stats else {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ConversationPair(BaseModel):
    customer_wa_id: str
    customer_name: Optional[str] = None
    customer_message: str
    customer_message_ts: Optional[str] = None
    tch_reply: Optional[str] = None
    source: Optional[str] = "tampermonkey_extract_v3"

class ConversationBatchRequest(BaseModel):
    phone: str
    name: Optional[str] = None
    pairs: List[ConversationPair]

@app.post("/shadow/log-conversation-batch", tags=["Shadow Mode"])
async def log_conversation_batch(
    body: ConversationBatchRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Batch ingest of historical conversation pairs extracted by Tampermonkey
    from WhatsApp Web DOM. Each pair is a customer message + optional TCH reply.
    """
    inserted = 0
    skipped = 0
    try:
        conn = _pg_connect()
        with conn.cursor() as cur:
            for pair in body.pairs:
                if not pair.customer_message.strip():
                    skipped += 1
                    continue
                cur.execute("""
                    INSERT INTO shadow_conversations
                        (customer_wa_id, customer_name, customer_message,
                         ai_proposed_response, mostafa_actual_response,
                         capture_source, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT DO NOTHING
                """, (
                    pair.customer_wa_id,
                    pair.customer_name or body.name,
                    pair.customer_message,
                    "[historical export - no AI draft]",
                    pair.tch_reply,
                    pair.source or "tampermonkey_extract_v3"
                ))
                inserted += 1
        conn.commit()
        conn.close()
        logger.info(f"Batch insert: {inserted} pairs for wa_id={body.phone}")
        return {"success": True, "inserted": inserted, "skipped": skipped}
    except Exception as e:
        logger.error(f"Batch insert error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)