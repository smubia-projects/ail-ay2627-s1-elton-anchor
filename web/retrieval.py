"""Hybrid search over one Library in Neon: keyword + vector, merged by RRF.

The same first two stages as scripts/hybrid.py (Phase 6), on pgvector at 768
dims through the gateway's embedding model:

  1. Candidates   up to CANDIDATES chunks from Postgres full-text search and
                  from pgvector cosine distance, each filtered on library_id
                  in the same statement.
  2. RRF          Reciprocal Rank Fusion (RRF_K = 60), then duplicate
                  passages dropped (hybrid.distinct, pentest.md B6).

The self-hosted bge-reranker-v2-m3 step is left out of the demo: it needs
about 3.9 GB of memory and 21-34 s of CPU per question. The top k by RRF go
straight to the answering model, so retrieval differs from the student's
measured pipeline (README, "This demo").
"""
import os

import db

CANDIDATES = 150
RRF_K = 60
EMBEDDING_MODEL = (os.environ.get("EMBEDDING_MODEL_NAME", "").strip() or "text-embedding-3-small")


def chunk_input(ch):
    """What gets embedded for a chunk: a one-line header plus its text (embed.py)."""
    where = ch.get("loc") or ch.get("heading") or ""
    return f"{ch['file']} | {where}\n{ch['text']}"


def embed(texts):
    """768-dim vectors for texts, as pgvector literals, and the tokens used."""
    from openai_client import client
    resp = client().embeddings.create(model=EMBEDDING_MODEL, input=texts, dimensions=db.DIMS)
    vectors = [d.embedding for d in resp.data]
    if any(len(v) != db.DIMS for v in vectors):
        raise ValueError(f"{EMBEDDING_MODEL} did not return {db.DIMS}-dim vectors")
    used = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
    return [literal(v) for v in vectors], used


def literal(vector):
    """pgvector's text form, bound as %s::vector."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def _rows(rows):
    return [{"chunk_id": r["chunk_id"], "file": r["file"], "loc": r["loc"], "heading": r["heading"],
             "text": r["text"], "score": float(r["score"])} for r in rows]


def keyword(q, n, library):
    return _rows(db.all_(
        """
        WITH query AS (
          SELECT to_tsquery('english', array_to_string(
                   tsvector_to_array(to_tsvector('english', %(q)s)), ' | ')) AS tq)
        SELECT chunk_id, file, loc, heading, text, ts_rank_cd(tsv, query.tq) AS score
        FROM chunks, query
        WHERE library_id = %(lib)s AND query.tq <> ''::tsquery AND tsv @@ query.tq
        ORDER BY score DESC, chunk_id
        LIMIT %(n)s
        """, {"q": q, "lib": library, "n": n}))


def vector(qv, n, library):
    return _rows(db.all_(
        """
        SELECT chunk_id, file, loc, heading, text, 1 - (embedding <=> %(v)s::vector) AS score
        FROM chunks WHERE library_id = %(lib)s
        ORDER BY embedding <=> %(v)s::vector, chunk_id
        LIMIT %(n)s
        """, {"v": qv, "lib": library, "n": n}))


def rrf(*ranked_lists):
    scores, by_id = {}, {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits, 1):
            scores[h["chunk_id"]] = scores.get(h["chunk_id"], 0.0) + 1.0 / (RRF_K + rank)
            by_id[h["chunk_id"]] = h
    return [by_id[cid] | {"rrf": s} for cid, s in sorted(scores.items(), key=lambda kv: -kv[1])]


def distinct(ranked):
    """Drop a chunk whose text is the same as a better-ranked one's (hybrid.distinct)."""
    seen, out = set(), []
    for c in ranked:
        key = " ".join(c["text"].split()).lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def search(question, k, library):
    """Top k chunks of one Library for a question, best first."""
    (qv,), _ = embed([question])
    return distinct(rrf(keyword(question, CANDIDATES, library), vector(qv, CANDIDATES, library)))[:k]


def as_evidence(chunk):
    loc = chunk.get("loc") or chunk.get("heading") or ""
    return {"id": chunk["chunk_id"], "file": chunk["file"], "loc": loc, "text": chunk["text"]}
