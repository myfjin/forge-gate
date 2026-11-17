"""The embedder behind the language-meaning and intent lenses (design §4a.3 lenses 4-5, build step 8b s1).

One engine: a LOCAL Ollama serving ``nomic-embed-text`` at the pinned digest (DECISIONS-8b H1 a),
reached at ``FORGE_GATE_EMBED_URL`` (env > config.toml, no default; unset = the lenses are BLIND).
Corpus bodies are private: before any text is sent, ``check()`` refuses

- a URL that is not plain http on a loopback address (config.embed_url),
- a model that Ollama marks remote (``remote_host`` / ``remote_model``: the same Ollama on the deploy host also
  serves ``:cloud`` models whose input leaves the host, DECISIONS-8b F9),
- a digest other than the pin (another model scores differently: its Rule-2 run would mean nothing),

and every request goes through an opener with NO proxy handler, so an ``http_proxy`` in the
environment cannot route a body off the host.

Vectors are cached (H4 b) in one append-only file per model digest under
``FORGE_GATE_EMBED_CACHE_DIR``. A scout run only READS the cache; ``fill()`` (a separate, explicit
command) is the only writer. A row is keyed by ``sha256(digest NUL text)`` and checked when read.
"""
from __future__ import annotations

import fcntl
import http.client
import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

MODEL = "nomic-embed-text:latest"
# /api/tags digest of the model on the deploy host, Ollama 0.31.2, read 2026-11-16 (DECISIONS-8b F9).
DIGEST = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
TIMEOUT = 600.0          # a batch of corpus bodies costs ~4 s each on the deploy host's CPU (F14)
BATCH = 16
MAX_ROW = 64 * 1024      # a cache line longer than this is refused, not parsed


class EmbedError(RuntimeError):
    """The embedder cannot be used for this run; the message is the BLIND reason."""


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Embedder:
    def __init__(self, url: str, *, model: str = MODEL, digest: str = DIGEST, timeout: float = TIMEOUT):
        try:                                     # the loopback rule holds for every caller (s1 review F2)
            url = config.loopback_url(url)
        except config.NotConfigured as e:
            raise EmbedError(f"embedder-url: {e}") from None
        self.url, self.model, self.digest, self.timeout = url, model, digest, timeout
        self._checked = False

    def _call(self, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data, {"Content-Type": "application/json"})
        try:
            with _opener().open(req, timeout=self.timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
            raise EmbedError(f"embedder-unreachable: {path}: {e}") from None

    def check(self) -> None:
        """Raise EmbedError unless the pinned LOCAL model is what this URL serves."""
        tags = self._call("/api/tags")
        models = tags.get("models") if isinstance(tags, dict) else None
        if not isinstance(models, list):
            raise EmbedError("embedder-unexpected: /api/tags has no model list")
        m = next((m for m in models if isinstance(m, dict) and m.get("name") == self.model), None)
        if m is None:
            raise EmbedError(f"embedder-no-model: {self.model} is not served at {self.url}")
        if m.get("remote_host") or m.get("remote_model"):
            raise EmbedError(f"embedder-remote: {self.model} is served from {m.get('remote_host')!r}; "
                             "private text is never sent off the host")
        if m.get("digest") != self.digest:
            raise EmbedError(f"embedder-digest: {self.model} is {str(m.get('digest'))[:12]}, pinned "
                             f"{self.digest[:12]}")
        show = self._call("/api/show", {"model": self.model})
        if not isinstance(show, dict) or show.get("remote_host") or show.get("remote_model"):
            raise EmbedError(f"embedder-remote: /api/show for {self.model} names a remote host")
        self._checked = True

    def embed(self, texts: list) -> list:
        """Vectors for ``texts``, in order. Checks the model first (once per Embedder)."""
        if not self._checked:
            self.check()
        out = []
        for i in range(0, len(texts), BATCH):
            chunk = texts[i:i + BATCH]
            r = self._call("/api/embed", {"model": self.model, "input": chunk})
            vecs = r.get("embeddings") if isinstance(r, dict) else None
            if not _vectors_ok(vecs, len(chunk)):
                raise EmbedError(f"embedder-unexpected: /api/embed gave no {len(chunk)} finite vectors")
            out += vecs
        return out


def _vectors_ok(vecs, n: int) -> bool:
    if not isinstance(vecs, list) or len(vecs) != n or not vecs:
        return False
    dim = len(vecs[0]) if isinstance(vecs[0], list) else 0
    return dim > 0 and all(
        isinstance(v, list) and len(v) == dim and
        all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v)
        and any(x != 0 for x in v)
        for v in vecs)


def get() -> Embedder:
    """The configured embedder. Raises EmbedError (BLIND reason) when it cannot be used."""
    try:
        return Embedder(config.embed_url())
    except config.NotConfigured as e:
        raise EmbedError(f"not-configured: {config.EMBED_URL_KEY}: {e}") from None


def cosine(a: list, b: list) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0 or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# ---------------------------------------------------------------- the vector cache (H4 b)

def key(text: str, digest: str = DIGEST) -> str:
    return hashlib.sha256(digest.encode() + b"\0" + text.encode("utf-8", "surrogatepass")).hexdigest()  # F5


def _cache_file(d: Path, digest: str) -> Path:
    return Path(d) / f"vectors-{digest[:12]}.jsonl"


def _safe_dir(d) -> Path:
    """The cache folder must be the caller's own and not writable by others (s1 review F4): anyone
    who can write into it could plant a symlink or rows."""
    d = Path(d)
    try:
        st = os.stat(d)
    except OSError as e:
        raise EmbedError(f"cache-unreadable: {d}: {e}") from None
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise EmbedError(f"cache-unsafe: {d} must be owned by uid {os.getuid()} and not group/world "
                         f"writable (uid {st.st_uid}, mode {oct(st.st_mode & 0o777)})")
    return d


def read_cache(d=None, digest: str = DIGEST) -> dict:
    """{key: vector} from the cache file. Unset dir -> EmbedError; a missing file -> {} (nothing
    embedded yet). A malformed or oversized line is skipped (a torn tail from a killed fill)."""
    if d is None:
        try:
            d = config.embed_cache_dir()
        except config.NotConfigured as e:
            raise EmbedError(f"not-configured: {config.EMBED_CACHE_KEY}: {e}") from None
    p = _cache_file(_safe_dir(d), digest)
    out = {}
    try:
        fh = os.fdopen(os.open(p, os.O_RDONLY | os.O_NOFOLLOW), "rb")     # F4: never through a symlink
    except FileNotFoundError:
        return out
    except OSError as e:
        raise EmbedError(f"cache-unreadable: {p}: {e}") from None
    with fh:
        for line in fh:
            if len(line) > MAX_ROW or not line.endswith(b"\n"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            k, v = (row.get("k"), row.get("v")) if isinstance(row, dict) else (None, None)
            if isinstance(k, str) and len(k) == 64 and _vectors_ok([v], 1):
                out[k] = v
    return out


def fill(texts: list, emb: Embedder, d=None) -> tuple[int, int]:
    """Embed the texts the cache does not hold yet and append them. The ONLY cache writer.
    Returns (added, already). One lock for the whole fill, so two fills never interleave."""
    d = _safe_dir(d if d is not None else config.embed_cache_dir())
    p = _cache_file(d, emb.digest)
    try:
        lfd = os.open(d / "embed.lock", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise EmbedError(f"cache-unsafe: {d}/embed.lock: {e}") from None
    with os.fdopen(lfd, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EmbedError(f"cache-busy: another fill holds {d}/embed.lock") from None
        have = read_cache(d, emb.digest)
        todo, seen = [], set()
        for t in texts:
            k = key(t, emb.digest)
            if k not in have and k not in seen:
                seen.add(k)
                todo.append((k, t))
        try:
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            raise EmbedError(f"cache-unsafe: {p}: {e}") from None
        try:
            for i in range(0, len(todo), BATCH):
                part = todo[i:i + BATCH]
                vecs = emb.embed([t for _, t in part])
                emb.check()       # the tag may have moved mid-fill: re-pin BEFORE writing (s1 review F1)
                os.write(fd, b"".join(json.dumps({"k": k, "v": v}, separators=(",", ":")).encode() + b"\n"
                                      for (k, _), v in zip(part, vecs)))
            os.fsync(fd)
        finally:
            os.close(fd)
    return len(todo), len(texts) - len(todo)
