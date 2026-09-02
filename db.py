# -*- coding: utf-8 -*-
"""Работа с базой данных SQLite (файл ads.db)."""

import json
from datetime import datetime, timezone

import aiosqlite

DB_PATH = "ads.db"


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                author_id INTEGER NOT NULL,
                author_username TEXT,
                animal_type TEXT,
                breed_color TEXT,
                sex TEXT,
                age TEXT,
                district TEXT,
                location_detail TEXT,
                event_date TEXT,
                contact TEXT,
                photos TEXT,
                created_at TEXT,
                channel_message_id INTEGER,
                reject_reason TEXT
            )
            """
        )
        await db.commit()


async def create_ad(data: dict) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO ads (
                kind, author_id, author_username, animal_type, breed_color,
                sex, age, district, location_detail, event_date, contact,
                photos, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("kind"),
                data.get("author_id"),
                data.get("author_username"),
                data.get("animal_type"),
                data.get("breed_color"),
                data.get("sex"),
                data.get("age"),
                data.get("district"),
                data.get("location_detail"),
                data.get("event_date"),
                data.get("contact"),
                json.dumps(data.get("photos", [])),
                now,
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_ad(ad_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM ads WHERE id = ?", (ad_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        ad = {key: row[key] for key in row.keys()}
        ad["photos"] = json.loads(ad["photos"] or "[]")
        return ad


async def update_status(ad_id: int, status: str, channel_message_id=None, reject_reason=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE ads SET status = ?, channel_message_id = ?, reject_reason = ? WHERE id = ?",
            (status, channel_message_id, reject_reason, ad_id),
        )
        await db.commit()


async def search_ads(kind=None, animal_type=None, district=None, query=None, limit=10):
    """Поиск по опубликованным объявлениям.

    kind        — 'lost' или 'found' (или None = все)
    animal_type — 'cat' / 'dog' / 'other' (или None = все)
    district    — название района (или None = все)
    query       — слово для поиска по описанию и месту (или None)
    """
    sql = "SELECT * FROM ads WHERE status = 'published'"
    params = []

    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if animal_type:
        sql += " AND animal_type = ?"
        params.append(animal_type)
    if district:
        sql += " AND district = ?"
        params.append(district)
    if query:
        sql += " AND (breed_color LIKE ? OR location_detail LIKE ? OR event_date LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        result = []
        for row in rows:
            ad = {key: row[key] for key in row.keys()}
            ad["photos"] = json.loads(ad["photos"] or "[]")
            result.append(ad)
        return result
