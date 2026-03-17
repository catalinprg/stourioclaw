from __future__ import annotations
import hashlib
import logging
import os
from pathlib import Path

from sqlalchemy import select, delete

from src.config import settings
from src.models.schemas import new_id
from src.persistence.database import DocumentChunk, async_session
from src.rag.chunker import chunk_markdown
from src.rag.embeddings.base import BaseEmbedder

logger = logging.getLogger("stourio.rag.ingestion")


def _hash_file(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _should_reingest(file_hash: str, existing_metadata: dict) -> bool:
    """Return True if the file has changed since last ingestion."""
    return existing_metadata.get("file_hash") != file_hash


async def ingest_runbooks(embedder: BaseEmbedder, directory: str | None = None) -> int:
    """Scan directory for .md files, chunk, embed, and store. Returns chunk count."""
    runbooks_dir = directory or settings.runbooks_dir
    base_path = Path(runbooks_dir)

    if not base_path.exists():
        logger.warning(f"Runbooks directory does not exist: {runbooks_dir}")
        return 0

    md_files = list(base_path.rglob("*.md"))
    if not md_files:
        logger.info(f"No markdown files found in {runbooks_dir}")
        return 0

    total_chunks = 0

    async with async_session() as session:
        for md_path in md_files:
            source_path = str(md_path)
            content = md_path.read_text(encoding="utf-8")
            file_hash = _hash_file(content)

            # Check existing chunks for this file
            result = await session.execute(
                select(DocumentChunk).where(
                    DocumentChunk.source_path == source_path,
                    DocumentChunk.source_type == "runbook",
                )
            )
            existing = result.scalars().all()

            if existing:
                existing_meta = existing[0].metadata_ or {}
                if not _should_reingest(file_hash, existing_meta):
                    logger.debug(f"Skipping unchanged file: {source_path}")
                    continue
                # Delete stale chunks before re-ingesting
                await session.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.source_path == source_path,
                        DocumentChunk.source_type == "runbook",
                    )
                )
                await session.flush()

            chunks = chunk_markdown(content, source_path)
            if not chunks:
                continue

            texts = [c["content"] for c in chunks]
            embeddings = await embedder.embed(texts)

            for chunk, embedding in zip(chunks, embeddings):
                meta = chunk["metadata"]
                meta["file_hash"] = file_hash
                record = DocumentChunk(
                    id=new_id(),
                    source_type="runbook",
                    source_path=source_path,
                    title=md_path.stem,
                    section_header=chunk["section_header"],
                    content=chunk["content"],
                    metadata_=meta,
                    embedding=embedding,
                )
                session.add(record)

            total_chunks += len(chunks)
            logger.info(f"Ingested {len(chunks)} chunks from {md_path.name}")

        await session.commit()

    return total_chunks


async def ingest_text(
    embedder: BaseEmbedder,
    content: str,
    source_type: str,
    source_path: str,
    title: str,
    extra_metadata: dict | None = None,
) -> int:
    """Ingest arbitrary text (e.g. agent memory). Returns chunk count."""
    from src.rag.chunker import chunk_markdown

    chunks = chunk_markdown(content, source_path)
    if not chunks:
        return 0

    texts = [c["content"] for c in chunks]
    embeddings = await embedder.embed(texts)

    async with async_session() as session:
        for chunk, embedding in zip(chunks, embeddings):
            meta = chunk["metadata"]
            if extra_metadata:
                meta.update(extra_metadata)
            record = DocumentChunk(
                id=new_id(),
                source_type=source_type,
                source_path=source_path,
                title=title,
                section_header=chunk["section_header"],
                content=chunk["content"],
                metadata_=meta,
                embedding=embedding,
            )
            session.add(record)
        await session.commit()

    return len(chunks)
