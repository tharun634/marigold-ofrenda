"""Marigold: a local ofrenda.

Stories are embedded with Gemma on this machine and stored in Tiger Cloud.
The séance answers only from those memories.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "gemma4:e4b")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "embeddinggemma")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "768"))
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
FRONTEND = ROOT / "frontend"

SAMPLE = [
    {
        "display_name": "Abuela Rosa",
        "relationship": "grandmother (fictional sample)",
        "years_lived": "1942–2019",
        "favorite_food": "tamales",
        "favorite_song": "La Llorona",
        "offering": "marigolds in a coffee tin",
        "memories": [
            "Rosa taught me to fold tamales on Christmas Eve. She hid a coin in one tamal, and whoever found it was promised a year of good luck.",
            "Her favorite song was La Llorona. She sang it softly in the kitchen and said it was about love that refuses to leave, not about a ghost.",
            "She kept marigolds in a coffee tin on the kitchen window. On the first of November she set out pan dulce and a glass of water.",
        ],
    },
    {
        "display_name": "June Park",
        "relationship": "kept an old library alive (fictional sample)",
        "years_lived": "1984–2023",
        "favorite_food": "cold noodles",
        "favorite_song": "Clair de Lune",
        "offering": "a printed man page",
        "memories": [
            "June Park's last commit message on the py-marigold library was: the tests know more than I do tonight.",
            "She named the date helpers after constellations. The function orion_offset still has her comment: if the year is missing, assume they meant to stay.",
        ],
    },
]


class BelovedIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(default="", max_length=120)
    years_lived: str = Field(default="", max_length=40)
    favorite_food: str = Field(default="", max_length=80)
    favorite_song: str = Field(default="", max_length=80)
    offering: str = Field(default="", max_length=120)


class MemoryIn(BaseModel):
    beloved_id: int
    body: str = Field(min_length=1, max_length=4000)


class CandleIn(BaseModel):
    beloved_id: int
    whisper: str = Field(default="", max_length=280)


class SeanceIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    beloved_id: int | None = None


def connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise HTTPException(
            status_code=500,
            detail="DATABASE_URL is not set. Copy .env.example to .env and add a Tiger connection URI.",
        )
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def apply_schema(conn: psycopg.Connection) -> None:
    # The policy call is not idempotent. Schema runs only when the altar is new.
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    statement: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        statement.append(line)
        if line.strip().endswith(";"):
            conn.execute("\n".join(statement))
            statement = []


def document_prompt(text: str) -> str:
    # EmbeddingGemma's document prompt. Queries use a different task prefix.
    return f"title: none | text: {text}"


def query_prompt(text: str) -> str:
    return f"task: search result | query: {text}"


def embed(text: str, *, kind: str) -> list[float]:
    prompt = document_prompt(text) if kind == "document" else query_prompt(text)
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": prompt},
            timeout=120,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama embeddings are unavailable ({EMBED_MODEL}). {exc}",
        ) from exc
    payload = response.json()
    vectors = payload.get("embeddings") or []
    if not vectors:
        raise HTTPException(status_code=503, detail="Ollama returned no embedding.")
    vector = vectors[0]
    if len(vector) != EMBED_DIM:
        raise HTTPException(
            status_code=500,
            detail=f"{EMBED_MODEL} returned {len(vector)} dimensions, expected {EMBED_DIM}.",
        )
    return vector


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def rrf(keyword_rows: list[dict], semantic_rows: list[dict], k: int = 60) -> list[dict]:
    scores: dict[int, float] = {}
    merged: dict[int, dict] = {}
    for rank, row in enumerate(keyword_rows, start=1):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (k + rank)
        merged[row["id"]] = {**row, "keyword_rank": rank}
    for rank, row in enumerate(semantic_rows, start=1):
        scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (k + rank)
        current = merged.get(row["id"], {**row})
        current["semantic_rank"] = rank
        merged[row["id"]] = current
    ordered = sorted(scores, key=scores.get, reverse=True)
    results = []
    for memory_id in ordered[:5]:
        item = merged[memory_id]
        item["score"] = round(scores[memory_id], 5)
        item.setdefault("keyword_rank", None)
        item.setdefault("semantic_rank", None)
        results.append(item)
    return results


def remember(conn: psycopg.Connection, beloved_id: int, body: str) -> dict:
    vector = embed(body, kind="document")
    row = conn.execute(
        """
        INSERT INTO memories (beloved_id, body, embedding)
        VALUES (%s, %s, %s::halfvec)
        RETURNING id, beloved_id, told_at, body
        """,
        (beloved_id, body, vector_literal(vector)),
    ).fetchone()
    return row


def seed(conn: psycopg.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS n FROM beloved").fetchone()["n"]
    if count:
        return
    for person in SAMPLE:
        created = conn.execute(
            """
            INSERT INTO beloved (
                display_name, relationship, years_lived,
                favorite_food, favorite_song, offering
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                person["display_name"],
                person["relationship"],
                person["years_lived"],
                person["favorite_food"],
                person["favorite_song"],
                person["offering"],
            ),
        ).fetchone()
        for memory in person["memories"]:
            remember(conn, created["id"], memory)
        conn.execute(
            """
            INSERT INTO candle_lights (beloved_id, whisper)
            VALUES (%s, %s)
            """,
            (created["id"], "A first candle, so the altar is not dark."),
        )


def answer_with_gemma(question: str, sources: list[dict]) -> str:
    excerpts = []
    for index, source in enumerate(sources, start=1):
        excerpts.append(
            f"{index}. {source['display_name']}: {source['body']}"
        )
    system = (
        "You are the voice of a household ofrenda named Marigold. "
        "Answer only from the memories listed below. "
        "If they do not contain the answer, say the altar is quiet on that question. "
        "Do not invent biography, dates, or feelings that are not written down. "
        "Speak with warmth, in plain sentences, under 90 words. "
        "Name the person whose memory you used."
    )
    user = "Memories:\n" + "\n".join(excerpts) + f"\n\nQuestion: {question}"
    body: dict[str, Any] = {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 180},
    }
    try:
        response = httpx.post(f"{OLLAMA_BASE_URL}/api/chat", json=body, timeout=180)
        if response.status_code >= 400 and "think" in body:
            body.pop("think")
            response = httpx.post(f"{OLLAMA_BASE_URL}/api/chat", json=body, timeout=180)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama chat is unavailable ({CHAT_MODEL}). {exc}",
        ) from exc
    message = response.json().get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        text = "The altar is quiet on that question."
    return text


@asynccontextmanager
async def lifespan(app: FastAPI):
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('public.beloved') AS name").fetchone()
        if exists["name"] is None:
            apply_schema(conn)
        seed(conn)
        conn.commit()
    yield


app = FastAPI(title="Marigold", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    db_ok = False
    db_error = None
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
            db_ok = True
    except Exception as exc:  # noqa: BLE001 — surface a short status to the page
        db_error = str(exc).splitlines()[0][:180]
    ollama_ok = False
    try:
        probe = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        ollama_ok = probe.status_code == 200
    except httpx.HTTPError:
        ollama_ok = False
    return {
        "ok": db_ok and ollama_ok,
        "database": db_ok,
        "database_error": db_error,
        "ollama": ollama_ok,
        "chat_model": CHAT_MODEL,
        "embed_model": EMBED_MODEL,
    }


@app.get("/api/altar")
def altar() -> dict:
    with connect() as conn:
        people = conn.execute(
            """
            SELECT
                b.id,
                b.display_name,
                b.relationship,
                b.years_lived,
                b.favorite_food,
                b.favorite_song,
                b.offering,
                (SELECT COUNT(*) FROM memories m WHERE m.beloved_id = b.id) AS memory_count,
                (SELECT COUNT(*) FROM candle_lights c WHERE c.beloved_id = b.id) AS candle_count
            FROM beloved b
            ORDER BY b.id
            """
        ).fetchall()
        recent = conn.execute(
            """
            SELECT bucket, beloved_id, lights
            FROM candle_lights_daily
            WHERE bucket >= now() - INTERVAL '14 days'
            ORDER BY bucket DESC
            LIMIT 30
            """
        ).fetchall()
    for row in people:
        row["memory_count"] = int(row["memory_count"])
        row["candle_count"] = int(row["candle_count"])
    for row in recent:
        row["bucket"] = row["bucket"].date().isoformat()
        row["lights"] = int(row["lights"])
    return {"people": people, "recent_lights": recent}


@app.post("/api/beloved")
def add_beloved(payload: BelovedIn) -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO beloved (
                display_name, relationship, years_lived,
                favorite_food, favorite_song, offering
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, display_name
            """,
            (
                payload.display_name.strip(),
                payload.relationship.strip(),
                payload.years_lived.strip(),
                payload.favorite_food.strip(),
                payload.favorite_song.strip(),
                payload.offering.strip(),
            ),
        ).fetchone()
        conn.commit()
    return row


@app.post("/api/memories")
def add_memory(payload: MemoryIn) -> dict:
    with connect() as conn:
        person = conn.execute(
            "SELECT id FROM beloved WHERE id = %s",
            (payload.beloved_id,),
        ).fetchone()
        if person is None:
            raise HTTPException(status_code=404, detail="That person is not on the altar.")
        row = remember(conn, payload.beloved_id, payload.body.strip())
        conn.commit()
    row["told_at"] = row["told_at"].isoformat()
    return row


@app.post("/api/candles")
def light_candle(payload: CandleIn) -> dict:
    with connect() as conn:
        person = conn.execute(
            "SELECT id, display_name FROM beloved WHERE id = %s",
            (payload.beloved_id,),
        ).fetchone()
        if person is None:
            raise HTTPException(status_code=404, detail="That person is not on the altar.")
        row = conn.execute(
            """
            INSERT INTO candle_lights (beloved_id, whisper)
            VALUES (%s, %s)
            RETURNING id, lit_at, whisper
            """,
            (payload.beloved_id, payload.whisper.strip()),
        ).fetchone()
        conn.commit()
    return {
        "id": row["id"],
        "lit_at": row["lit_at"].isoformat(),
        "whisper": row["whisper"],
        "display_name": person["display_name"],
    }


@app.post("/api/seance")
def seance(payload: SeanceIn) -> dict:
    question = payload.question.strip()
    vector = embed(question, kind="query")
    literal = vector_literal(vector)
    with connect() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL hnsw.ef_search = 100")
            conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            semantic = conn.execute(
                """
                SELECT m.id, m.body, b.display_name
                FROM memories m
                JOIN beloved b ON b.id = m.beloved_id
                WHERE m.embedding IS NOT NULL
                  AND (%s::bigint IS NULL OR m.beloved_id = %s)
                ORDER BY m.embedding <=> %s::halfvec
                LIMIT 20
                """,
                (payload.beloved_id, payload.beloved_id, literal),
            ).fetchall()
            keyword = conn.execute(
                """
                SELECT m.id, m.body, b.display_name
                FROM memories m
                JOIN beloved b ON b.id = m.beloved_id
                WHERE %s::bigint IS NULL OR m.beloved_id = %s
                ORDER BY m.body OPERATOR(sloth_sort.<@>)
                         sloth_sort.to_bm25query(%s, 'memories_body_bm25')
                LIMIT 20
                """,
                (payload.beloved_id, payload.beloved_id, question),
            ).fetchall()
    sources = rrf(keyword, semantic)
    if not sources:
        return {
            "answer": "The altar is quiet. Nothing placed here matches that question.",
            "sources": [],
            "chat_model": CHAT_MODEL,
            "embed_model": EMBED_MODEL,
        }
    answer = answer_with_gemma(question, sources)
    public_sources = [
        {
            "id": source["id"],
            "display_name": source["display_name"],
            "body": source["body"],
            "keyword_rank": source["keyword_rank"],
            "semantic_rank": source["semantic_rank"],
            "score": source["score"],
        }
        for source in sources
    ]
    return {
        "answer": answer,
        "sources": public_sources,
        "chat_model": CHAT_MODEL,
        "embed_model": EMBED_MODEL,
    }


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
