"""
storage.py — SQLite 저장 계층 (FEAT-02)

설계서 3장 DDL 기준: articles + meta 테이블 + 인덱스.
표준 sqlite3만 사용(ORM 없음).
"""

import json
import os
import sqlite3
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    published_at  TEXT,
    source        TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    raw_excerpt   TEXT,
    summary       TEXT,
    tags          TEXT,
    importance    INTEGER DEFAULT 0,
    relevance     INTEGER DEFAULT 0,
    analyzed      INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_collected ON articles(collected_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """DB 파일을 초기화하고 커넥션을 반환한다.

    디렉토리가 없으면 생성한다. 스키마는 CREATE IF NOT EXISTS 방식이라
    기존 DB에 재실행해도 안전하다.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_article(conn: sqlite3.Connection, a: dict) -> bool:
    """기사를 삽입한다. URL 중복 시 무시하고 False를 반환한다.

    반환값:
        True  — 신규 삽입 성공
        False — URL 이미 존재(중복 무시)

    DB 쓰기 오류(디스크/락 등)는 예외를 그대로 올려
    pipeline이 종료코드 1로 처리하게 한다.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, title, published_at, source, collected_at, raw_excerpt)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            a["url"],
            a["title"],
            a.get("published_at"),
            a["source"],
            a["collected_at"],
            a.get("raw_excerpt", ""),
        ),
    )
    conn.commit()
    return cur.rowcount == 1


def update_analysis(
    conn: sqlite3.Connection,
    url: str,
    summary: str,
    tags: list,
    importance: int,
    relevance: int,
    analyzed: int,
) -> None:
    """분석 결과를 해당 URL 행에 갱신한다.

    tags는 list → JSON 문자열로 직렬화해 저장한다.
    DB 쓰기 오류는 예외를 그대로 올린다.
    """
    conn.execute(
        """UPDATE articles
           SET summary=?, tags=?, importance=?, relevance=?, analyzed=?
           WHERE url=?""",
        (
            summary,
            json.dumps(tags, ensure_ascii=False),
            int(importance),
            int(relevance),
            int(analyzed),
            url,
        ),
    )
    conn.commit()


def get_articles_by_range(
    conn: sqlite3.Connection, start: str, end: str
) -> list:
    """collected_at 기준으로 [start, end] 범위의 기사를 조회한다.

    반환값:
        list[dict] — tags는 JSON 역직렬화된 list.
                     tags 컬럼이 None/빈 문자열인 경우 빈 리스트로 정규화.
    """
    rows = conn.execute(
        """SELECT * FROM articles
           WHERE collected_at BETWEEN ? AND ?
           ORDER BY importance DESC, published_at DESC""",
        (start, end),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        raw_tags = d.get("tags")
        if raw_tags:
            try:
                d["tags"] = json.loads(raw_tags)
            except (json.JSONDecodeError, ValueError):
                d["tags"] = []
        else:
            d["tags"] = []
        result.append(d)
    return result


def get_meta(conn: sqlite3.Connection, key: str, default=None) -> Optional[str]:
    """meta 테이블에서 key에 해당하는 값을 반환한다.

    없으면 default(기본 None)를 반환한다.
    """
    row = conn.execute(
        "SELECT value FROM meta WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """meta 테이블에 key-value를 upsert한다.

    키가 없으면 삽입, 있으면 값을 갱신한다.
    DB 쓰기 오류는 예외를 그대로 올린다.
    """
    conn.execute(
        """INSERT INTO meta(key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, str(value)),
    )
    conn.commit()
