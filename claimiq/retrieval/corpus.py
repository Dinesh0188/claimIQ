"""Parse the markdown corpus into citable, attributable chunks.

A rule that cannot name its authority is not a rule. Everything in this module exists
to make that structurally true rather than aspirational: the loader **fails closed**,
so a chunk missing a citation, a severity or a resolvable source stops the process
instead of quietly becoming a determination on somebody's claim.

Two levels of metadata, and the split is deliberate. A file-level block carries what is
constant for the whole list -- the annexure it comes from, the bearer, the severity --
because repeating nine keys across 104 chunks is how they drift apart. A chunk header
carries its ID and any override:

    <!-- source:
    jurisdiction: IN
    list: LIST_II_ROOM
    ...
    -->

    <!-- chunk_id: L2-001 -->
    <!-- chunk_id: L2-014 | citation_precision: item | citation: Annexure II item 14 -->

The second form is what an item looks like after a human has checked it against the
official annexure and can say something more precise than "it is somewhere in this
list". That upgrade path is the point: `citation_precision` is how the corpus admits
what it has not yet verified.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from claimiq.config import ROOT
from claimiq.state import Severity

CORPUS_DIR = ROOT / "corpus"
SOURCES_PATH = CORPUS_DIR / "SOURCES.toml"

# A source nobody has re-checked in this long is served with a staleness warning even
# if its status says in_force. Regulation moves; a tick from three years ago is not
# evidence about today.
STALE_AFTER_DAYS = 365

_FILE_META = re.compile(r"<!--\s*source:\s*(?P<body>.*?)-->", re.DOTALL)
_HEADER = re.compile(r"<!--\s*chunk_id:\s*(?P<id>[\w\-.]+)\s*(?P<meta>(\|[^|>]+)*)-->")
_ALIASES = re.compile(r"^Aliases:\s*(?P<aliases>.+)$", re.MULTILINE)
_TITLE = re.compile(r"^#{1,6}\s*(?P<title>.+)$", re.MULTILINE)

CitationPrecision = Literal["item", "list"]
SourceStatus = Literal["in_force", "superseded", "unverified"]


class CorpusError(RuntimeError):
    """The corpus could not be loaded as valid, attributable rules.

    Raised rather than degraded. An engine running on a partially-parsed rule set
    produces confident numbers from an unknown subset of the rules, which is worse
    than not answering.
    """


class RuleSource(BaseModel):
    """A document rules are derived from. One entry per authority in SOURCES.toml."""

    source_id: str
    jurisdiction: str
    title: str
    reference: str = ""
    publisher: str = ""
    url: str = ""
    published: date
    retrieved: date
    verified_on: date | None = None
    superseded_by: str | None = None
    status: SourceStatus = "unverified"
    note: str = ""

    def staleness(self, today: date | None = None) -> str | None:
        """Why this source should not be trusted silently, or None if it is fine.

        Returns a sentence rather than a boolean because it is rendered to the user
        verbatim. A staleness indicator the user cannot read the reason for is just
        a yellow dot they learn to ignore.
        """
        today = today or date.today()
        if self.status == "superseded":
            replacement = self.superseded_by or "a later document"
            return f"Superseded by {replacement}."
        if self.status == "unverified" or self.verified_on is None:
            return "Never verified against the issuing body's current publication."
        age = (today - self.verified_on).days
        if age > STALE_AFTER_DAYS:
            return f"Last verified {age} days ago, on {self.verified_on.isoformat()}."
        return None


class Chunk(BaseModel):
    """One citable rule."""

    chunk_id: str
    title: str
    body: str
    aliases: list[str] = []
    source: str

    # Attribution. Every one of these is required -- see the module docstring.
    jurisdiction: str
    severity: Severity
    source_id: str
    citation: str
    citation_precision: CitationPrecision

    # Classification metadata. `list_name` is absent on policy-wording chunks, which
    # describe how a deduction is computed rather than naming a non-payable item.
    list_name: str | None = None
    bearer: str | None = None
    topic: str | None = None

    @property
    def search_text(self) -> str:
        """What gets embedded and indexed.

        Aliases are repeated deliberately: hospital bills print the alias, not the
        canonical name, so the alias strings carry most of the retrieval signal.
        """
        alias_text = " ".join(self.aliases)
        return f"{self.title}. {alias_text}. {self.body}"

    @property
    def attribution(self) -> str:
        """One-line human citation, e.g. 'IRDA/HLT/REG/CIR/146/07/2016, Annexure II'.

        Resolved against the source registry so the reference number travels with the
        rule instead of living in a file the reader has to go and find.
        """
        source = sources().get(self.source_id)
        reference = (source.reference or source.title) if source else self.source_id
        return f"{reference}, {self.citation}"


def _parse_block(raw: str) -> dict[str, str]:
    """Parse `key: value` pairs from either metadata form.

    Handles the file-level block (newline-separated) and the chunk header
    (pipe-separated) with one splitter, since the only difference is the delimiter.
    """
    meta: dict[str, str] = {}
    for part in re.split(r"[|\n]", raw):
        key, sep, value = part.partition(":")
        if sep and key.strip():
            meta[key.strip()] = value.strip()
    return meta


REQUIRED_KEYS = ("jurisdiction", "severity", "source_id", "citation", "citation_precision")


def _parse_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    # Repo-relative where possible; bare filename otherwise. `relative_to` raises on a
    # path outside ROOT, which made the loader untestable against a fixture directory.
    try:
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        relative = path.name

    file_match = _FILE_META.search(text)
    if not file_match:
        raise CorpusError(
            f"{relative} has no <!-- source: ... --> block. Every corpus file must "
            f"declare the document its rules come from."
        )
    defaults = _parse_block(file_match.group("body"))

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

        chunk_id = match.group("id")
        meta = defaults | _parse_block(match.group("meta") or "")

        missing = [key for key in REQUIRED_KEYS if not meta.get(key)]
        if missing:
            raise CorpusError(
                f"chunk {chunk_id!r} in {relative} is missing required attribution: "
                f"{', '.join(missing)}. A rule without a citation cannot be served."
            )

        try:
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    title=title,
                    body=" ".join(body.split()),
                    aliases=aliases,
                    source=relative,
                    jurisdiction=meta["jurisdiction"],
                    severity=meta["severity"],  # type: ignore[arg-type]
                    source_id=meta["source_id"],
                    citation=meta["citation"],
                    citation_precision=meta["citation_precision"],  # type: ignore[arg-type]
                    list_name=meta.get("list"),
                    bearer=meta.get("bearer"),
                    topic=meta.get("topic"),
                )
            )
        except ValidationError as exc:
            raise CorpusError(f"chunk {chunk_id!r} in {relative}: {exc}") from exc

    return chunks


@lru_cache(maxsize=1)
def sources() -> dict[str, RuleSource]:
    """The source registry, keyed by source_id."""
    if not SOURCES_PATH.is_file():
        raise CorpusError(
            f"{SOURCES_PATH} is missing. Rules cannot be served without the registry "
            f"of documents they derive from."
        )

    raw = tomllib.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    registry: dict[str, RuleSource] = {}
    for entry in raw.get("source", []):
        try:
            source = RuleSource.model_validate(entry)
        except ValidationError as exc:
            raise CorpusError(f"invalid source entry in SOURCES.toml: {exc}") from exc
        registry[source.source_id] = source
    return registry


@lru_cache(maxsize=1)
def load_corpus() -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(CORPUS_DIR.rglob("*.md")):
        chunks.extend(_parse_file(path))

    if not chunks:
        raise CorpusError(f"no rules found under {CORPUS_DIR}. Refusing to audit without them.")

    registry = sources()
    seen: dict[str, str] = {}
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise CorpusError(
                f"duplicate chunk_id {chunk.chunk_id!r} in {chunk.source} "
                f"and {seen[chunk.chunk_id]}"
            )
        seen[chunk.chunk_id] = chunk.source

        if chunk.source_id not in registry:
            raise CorpusError(
                f"chunk {chunk.chunk_id!r} cites source_id {chunk.source_id!r}, which is "
                f"not in SOURCES.toml. Known: {', '.join(sorted(registry))}"
            )

    return chunks


def stale_sources(today: date | None = None) -> dict[str, str]:
    """Sources currently in use that should not be trusted silently, and why.

    Only sources some chunk actually cites are reported -- warning about a registry
    entry nothing depends on trains people to dismiss the warning.
    """
    in_use = {chunk.source_id for chunk in load_corpus()}
    registry = sources()
    return {
        source_id: reason
        for source_id in sorted(in_use)
        if (reason := registry[source_id].staleness(today)) is not None
    }


@lru_cache(maxsize=1)
def corpus_version() -> str:
    """Content hash. Stamped onto every audit so a result can be reproduced.

    Covers the attribution and classification metadata, not just the prose. It used to
    hash only `chunk_id + search_text`, which meant flipping `bearer: HOSPITAL` to
    `PATIENT` -- moving money from the hospital's write-off to the patient's bill --
    left the stamp unchanged. Two audits could carry the same version and disagree.
    """
    digest = hashlib.sha256()
    for chunk in load_corpus():
        digest.update(chunk.chunk_id.encode())
        digest.update(chunk.search_text.encode())
        for field in (
            chunk.jurisdiction,
            chunk.severity,
            chunk.source_id,
            chunk.citation,
            chunk.citation_precision,
            chunk.list_name or "",
            chunk.bearer or "",
            chunk.topic or "",
        ):
            digest.update(b"\x00")
            digest.update(field.encode())

    # The prefix is not decoration: it is asserted by the test suite and it degrades to
    # "unverified" whenever any source in use is unverified, superseded or stale, so a
    # version string cannot claim more confidence than its weakest source.
    prefix = "unverified" if stale_sources() else "verified"
    return f"{prefix}-{digest.hexdigest()[:10]}"
