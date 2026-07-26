"""Parse the markdown corpus into citable chunks.

Chunks are delimited by an HTML comment carrying their ID and metadata:

    <!-- chunk_id: L2-001 | list: LIST_II_ROOM | bearer: HOSPITAL -->

The ID is what every LLM determination has to cite, so it has to be stable and
human-checkable -- which is why the corpus is hand-authored markdown rather than
something generated.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from claimiq.config import ROOT

CORPUS_DIR = ROOT / "corpus"

_HEADER = re.compile(r"<!--\s*chunk_id:\s*(?P<id>[\w\-.]+)\s*(?P<meta>(\|[^|>]+)*)-->")
_ALIASES = re.compile(r"^Aliases:\s*(?P<aliases>.+)$", re.MULTILINE)
_TITLE = re.compile(r"^#{1,6}\s*(?P<title>.+)$", re.MULTILINE)


class Chunk(BaseModel):
    chunk_id: str
    title: str
    body: str
    aliases: list[str] = []
    source: str
    meta: dict[str, str] = {}

    @property
    def list_name(self) -> str | None:
        return self.meta.get("list")

    @property
    def bearer(self) -> str | None:
        return self.meta.get("bearer")

    @property
    def search_text(self) -> str:
        """What gets embedded and indexed.

        Aliases are repeated deliberately: hospital bills print the alias, not the
        canonical name, so the alias strings carry most of the retrieval signal.
        """
        alias_text = " ".join(self.aliases)
        return f"{self.title}. {alias_text}. {self.body}"


def _parse_meta(raw: str) -> dict[str, str]:
    meta = {}
    for part in raw.split("|"):
        if ":" in part:
            key, _, value = part.partition(":")
            meta[key.strip()] = value.strip()
    return meta


def _parse_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    matches = list(_HEADER.finditer(text))
    chunks = []

    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()

        title_match = _TITLE.search(block)
        title = title_match.group("title").strip() if title_match else match.group("id")

        alias_match = _ALIASES.search(block)
        aliases = (
            [a.strip() for a in alias_match.group("aliases").split(",") if a.strip()]
            if alias_match
            else []
        )

        body = block
        if title_match:
            body = body.replace(title_match.group(0), "", 1)
        if alias_match:
            body = body.replace(alias_match.group(0), "", 1)

        chunks.append(
            Chunk(
                chunk_id=match.group("id"),
                title=title,
                body=" ".join(body.split()),
                aliases=aliases,
                source=str(path.relative_to(ROOT)).replace("\\", "/"),
                meta=_parse_meta(match.group("meta") or ""),
            )
        )
    return chunks


@lru_cache(maxsize=1)
def load_corpus() -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(CORPUS_DIR.rglob("*.md")):
        chunks.extend(_parse_file(path))

    seen: dict[str, str] = {}
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise ValueError(
                f"duplicate chunk_id {chunk.chunk_id!r} in {chunk.source} "
                f"and {seen[chunk.chunk_id]}"
            )
        seen[chunk.chunk_id] = chunk.source
    return chunks


@lru_cache(maxsize=1)
def corpus_version() -> str:
    """Content hash. Stamped onto every audit so a result can be reproduced."""
    digest = hashlib.sha256()
    for chunk in load_corpus():
        digest.update(chunk.chunk_id.encode())
        digest.update(chunk.search_text.encode())
    return f"unverified-{digest.hexdigest()[:10]}"
