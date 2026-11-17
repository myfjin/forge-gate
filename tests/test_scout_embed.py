"""Build step 8b slice 1: the local embedder client and its vector cache (DECISIONS-8b H1, H4).

A fake Ollama on a loopback port serves /api/tags, /api/show and /api/embed over real HTTP, and
records every request, so "refused" means "no text was sent", not just "an error was raised"."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from forge_gate import config
from forge_gate.scout import embed


def vec(text: str, dim: int = 8) -> list:
    h = hashlib.sha256(text.encode()).digest()
    return [(b - 127.5) / 127.5 for b in h[:dim]]


class Fake:
    def __init__(self):
        self.tags = {"models": [{"name": embed.MODEL, "digest": embed.DIGEST}]}
        self.show = {"details": {"family": "nomic-bert"}}
        self.embed_fn = lambda texts: {"embeddings": [vec(t) for t in texts]}
        self.calls = []                       # (path, body)

    def embedded(self) -> list:
        return [t for p, b in self.calls if p == "/api/embed" for t in b["input"]]


def _serve(fake: Fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            fake.calls.append((self.path, None))
            self._reply(fake.tags if self.path == "/api/tags" else {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            fake.calls.append((self.path, body))
            if self.path == "/api/show":
                self._reply(fake.show)
            elif self.path == "/api/embed":
                self._reply(fake.embed_fn(body["input"]))
            else:
                self._reply({})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def fake():
    f = Fake()
    srv = _serve(f)
    f.url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield f
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for k in (config.EMBED_URL_KEY, config.EMBED_CACHE_KEY):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- the client

def test_the_pinned_local_model_embeds_in_order_across_batches(fake):
    texts = [f"text {i}" for i in range(embed.BATCH * 2 + 3)]
    out = embed.Embedder(fake.url).embed(texts)
    assert out == [vec(t) for t in texts]
    assert fake.embedded() == texts
    assert [p for p, _ in fake.calls][:2] == ["/api/tags", "/api/show"]      # checked BEFORE any text
    assert all(b["model"] == embed.MODEL for p, b in fake.calls if p == "/api/embed")


@pytest.mark.parametrize("change, reason", [
    (lambda f: f.tags["models"][0].update(remote_host="https://ollama.com"), "embedder-remote"),
    (lambda f: f.tags["models"][0].update(remote_model="nomic-embed-text"), "embedder-remote"),
    (lambda f: f.show.update(remote_host="https://ollama.com"), "embedder-remote"),
    (lambda f: f.show.update(remote_model="x"), "embedder-remote"),
    (lambda f: f.tags["models"][0].update(digest="0" * 64), "embedder-digest"),
    (lambda f: f.tags["models"][0].update(name="nomic-embed-text:v2"), "embedder-no-model"),
    (lambda f: f.tags.update(models="nope"), "embedder-unexpected"),
])
def test_a_model_that_is_not_the_pinned_local_one_never_receives_text(fake, change, reason):
    change(fake)
    with pytest.raises(embed.EmbedError, match=reason):
        embed.Embedder(fake.url).embed(["private corpus body"])
    assert fake.embedded() == []


def test_a_cloud_model_of_the_same_name_family_is_not_picked(fake):
    fake.tags["models"].insert(0, {"name": "nomic-embed-text:cloud", "digest": embed.DIGEST,
                                   "remote_host": "https://ollama.com"})
    assert embed.Embedder(fake.url).embed(["a"]) == [vec("a")]          # exact name only


@pytest.mark.parametrize("reply", [
    {"embeddings": []},
    {"embeddings": [[0.1, 0.2]]},                            # one vector for two texts
    {"embeddings": [[0.1, float("nan")], [0.1, 0.2]]},
    {"embeddings": [[0.0, 0.0], [0.1, 0.2]]},                # an all-zero vector scores nothing
    {"embeddings": [[True, 0.2], [0.1, 0.2]]},
    {"embeddings": [[0.1, 0.2], [0.1]]},                     # ragged
    {"embeddings": [[0.1, "0.2"], [0.1, 0.2]]},
    {"error": "model failed"},
])
def test_a_malformed_embed_reply_is_refused(fake, reply):
    fake.embed_fn = lambda texts: reply
    with pytest.raises(embed.EmbedError, match="embedder-unexpected"):
        embed.Embedder(fake.url).embed(["a", "b"])


def test_an_unreachable_embedder_is_a_reason_not_a_crash():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(embed.EmbedError, match="embedder-unreachable"):
        embed.Embedder(f"http://127.0.0.1:{port}", timeout=2).embed(["a"])


def test_a_proxy_in_the_environment_never_sees_a_body(fake, monkeypatch):
    proxy = Fake()
    psrv = _serve(proxy)
    try:
        purl = f"http://127.0.0.1:{psrv.server_address[1]}"
        for k in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(k, purl)
        for k in ("no_proxy", "NO_PROXY"):
            monkeypatch.setenv(k, "")
        assert embed.Embedder(fake.url).embed(["secret"]) == [vec("secret")]
        assert proxy.calls == [] and fake.embedded() == ["secret"]
    finally:
        psrv.shutdown()
        psrv.server_close()


def test_get_without_the_key_is_not_configured():
    with pytest.raises(embed.EmbedError, match="not-configured: FORGE_GATE_EMBED_URL"):
        embed.get()


def test_get_with_the_key_uses_it(fake, monkeypatch):
    monkeypatch.setenv(config.EMBED_URL_KEY, fake.url + "/")
    e = embed.get()
    assert e.url == fake.url and e.embed(["x"]) == [vec("x")]


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434", "http://127.0.0.1:11434/", "http://[::1]:11434"])
def test_loopback_urls_are_accepted(monkeypatch, url):
    monkeypatch.setenv(config.EMBED_URL_KEY, url)
    assert config.embed_url() == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:11434", "http://10.0.0.5:11434", "http://ollama.com:80", "http://127.0.0.1",
    "http://u:p@127.0.0.1:11434", "http://127.0.0.1:11434/api", "http://127.0.0.1:11434?x=1",
    "http://127.0.0.1:11434#f", "http://127.0.0.1.evil.com:11434", "file:///etc/passwd",
    "http://127.0.0.1:99999", "127.0.0.1:11434", "http://localhost:11434"])
def test_anything_but_plain_loopback_http_is_refused(monkeypatch, url):
    monkeypatch.setenv(config.EMBED_URL_KEY, url)
    with pytest.raises(config.NotConfigured, match=config.EMBED_URL_KEY):
        config.embed_url()


def test_cosine():
    assert embed.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert embed.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert embed.cosine([1, 2], [-1, -2]) == pytest.approx(-1.0)
    assert embed.cosine([0, 0], [1, 0]) == 0.0
    assert embed.cosine([1, 0], [1, 0, 0]) == 0.0


# ---------------------------------------------------------------- the cache

def test_cache_without_the_key_is_not_configured():
    with pytest.raises(embed.EmbedError, match="not-configured: FORGE_GATE_EMBED_CACHE_DIR"):
        embed.read_cache()


def test_a_cache_dir_that_does_not_exist_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(config.EMBED_CACHE_KEY, str(tmp_path / "nope"))
    with pytest.raises(embed.EmbedError, match="not an existing directory"):
        embed.read_cache()


def test_fill_then_read_and_a_second_fill_sends_nothing(fake, tmp_path, monkeypatch):
    monkeypatch.setenv(config.EMBED_CACHE_KEY, str(tmp_path))
    e = embed.Embedder(fake.url)
    texts = ["a", "b", "a", "c"]
    assert embed.fill(texts, e) == (3, 1)
    got = embed.read_cache()
    assert got == {embed.key(t): vec(t) for t in "abc"}
    n = len(fake.embedded())
    assert embed.fill(["a", "b", "d"], e) == (1, 2)
    assert fake.embedded()[n:] == ["d"]
    assert len(embed.read_cache()) == 4


def test_the_key_binds_the_digest_and_the_text():
    assert embed.key("a") != embed.key("b")
    assert embed.key("a") != embed.key("a", "f" * 64)
    assert embed.key("a") == hashlib.sha256(embed.DIGEST.encode() + b"\0a").hexdigest()


def test_another_digest_reads_another_file(fake, tmp_path):
    embed.fill(["a"], embed.Embedder(fake.url), d=tmp_path)
    assert embed.read_cache(tmp_path, "f" * 64) == {}


def test_bad_cache_lines_are_skipped_never_trusted(tmp_path):
    p = tmp_path / f"vectors-{embed.DIGEST[:12]}.jsonl"
    good = {"k": "a" * 64, "v": [0.5, 0.5]}
    lines = [json.dumps(good), "not json", json.dumps({"k": "short", "v": [1.0]}),
             json.dumps({"k": "b" * 64, "v": [0.0, 0.0]}), json.dumps({"k": "c" * 64, "v": "x"}),
             json.dumps([1, 2]), json.dumps({"k": "d" * 64, "v": [1.0] * (embed.MAX_ROW // 4)}),
             json.dumps({"k": "e" * 64, "v": [0.1, 0.2]})]          # torn: no newline at the end
    p.write_text("\n".join(lines))
    assert embed.read_cache(tmp_path) == {"a" * 64: [0.5, 0.5]}


def test_a_second_fill_while_one_runs_is_refused(fake, tmp_path):
    import fcntl
    with open(tmp_path / "embed.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        with pytest.raises(embed.EmbedError, match="cache-busy"):
            embed.fill(["a"], embed.Embedder(fake.url), d=tmp_path)
    assert fake.embedded() == []


def test_a_failed_fill_keeps_what_it_wrote_and_nothing_torn(fake, tmp_path):
    seen = []

    def flaky(texts):
        seen.append(texts)
        if len(seen) == 2:
            return {"error": "boom"}
        return {"embeddings": [vec(t) for t in texts]}
    fake.embed_fn = flaky
    texts = [f"t{i}" for i in range(embed.BATCH * 2)]
    with pytest.raises(embed.EmbedError):
        embed.fill(texts, embed.Embedder(fake.url), d=tmp_path)
    got = embed.read_cache(tmp_path)
    assert got == {embed.key(t): vec(t) for t in texts[:embed.BATCH]}
    assert (tmp_path / f"vectors-{embed.DIGEST[:12]}.jsonl").read_bytes().endswith(b"\n")


# ---------------------------------------------------------------- s1 review fixes

def test_the_embedder_refuses_a_non_loopback_url_whoever_builds_it():          # F2
    for url in ("http://10.0.0.5:11434", "http://localhost:11434", "https://127.0.0.1:1"):
        with pytest.raises(embed.EmbedError, match="embedder-url"):
            embed.Embedder(url)


def test_a_tag_moved_mid_fill_stops_the_fill_before_writing(fake, tmp_path):     # F1
    n = []

    def moving(texts):
        n.append(1)
        if len(n) == 2:
            fake.tags["models"][0]["digest"] = "1" * 64        # someone pulled a new :latest
        return {"embeddings": [vec(t) for t in texts]}
    fake.embed_fn = moving
    texts = [f"t{i}" for i in range(embed.BATCH * 3)]
    with pytest.raises(embed.EmbedError, match="embedder-digest"):
        embed.fill(texts, embed.Embedder(fake.url), d=tmp_path)
    assert set(embed.read_cache(tmp_path)) == {embed.key(t) for t in texts[:embed.BATCH]}


def test_a_symlinked_cache_file_is_refused_for_read_and_fill(fake, tmp_path):     # F4
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(json.dumps({"k": embed.key("a"), "v": [1.0, 0.0]}) + "\n")
    d = tmp_path / "cache"
    d.mkdir(mode=0o700)
    (d / f"vectors-{embed.DIGEST[:12]}.jsonl").symlink_to(elsewhere)
    with pytest.raises(embed.EmbedError, match="cache-unreadable"):
        embed.read_cache(d)
    with pytest.raises(embed.EmbedError):
        embed.fill(["b"], embed.Embedder(fake.url), d=d)
    assert elsewhere.read_text().count("\n") == 1 and fake.embedded() == []


def test_a_symlinked_lock_is_refused(fake, tmp_path):                             # F4
    d = tmp_path / "cache"
    d.mkdir(mode=0o700)
    (d / "embed.lock").symlink_to(tmp_path / "victim")
    with pytest.raises(embed.EmbedError, match="cache-unsafe"):
        embed.fill(["b"], embed.Embedder(fake.url), d=d)
    assert not (tmp_path / "victim").exists()


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o722])
def test_a_cache_dir_others_can_write_is_refused(tmp_path, mode):                 # F4
    d = tmp_path / "cache"
    d.mkdir()
    os.chmod(d, mode)
    with pytest.raises(embed.EmbedError, match="cache-unsafe"):
        embed.read_cache(d)


def test_a_lone_surrogate_has_a_key_not_a_crash():                                 # F5
    assert len(embed.key("\ud800")) == 64 and embed.key("\ud800") != embed.key("\udc86")


def test_a_symlink_planted_after_the_read_is_not_written_through(fake, tmp_path, monkeypatch):   # F4 race
    d = tmp_path / "cache"
    d.mkdir(mode=0o700)
    victim = tmp_path / "victim.jsonl"
    victim.write_text("")
    real_read = embed.read_cache

    def read_then_plant(dd, digest=embed.DIGEST):
        got = real_read(dd, digest)
        (Path(dd) / f"vectors-{digest[:12]}.jsonl").symlink_to(victim)      # the race window
        return got
    monkeypatch.setattr(embed, "read_cache", read_then_plant)
    with pytest.raises(embed.EmbedError, match="cache-unsafe"):
        embed.fill(["b"], embed.Embedder(fake.url), d=d)
    assert victim.read_text() == ""


def test_a_truncated_reply_is_embedder_unreachable_not_a_crash(monkeypatch):
    import http.client

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            raise http.client.IncompleteRead(b"{")

    class O:
        def open(self, req, timeout=None):
            return R()
    monkeypatch.setattr(embed, "_opener", lambda: O())
    with pytest.raises(embed.EmbedError, match="embedder-unreachable"):
        embed.Embedder("http://127.0.0.1:9").check()
