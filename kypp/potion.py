"""potion.py — a local static-embedding `embed` for the store's vector seam, stdlib only.

A model2vec "potion" model is a lookup table: one learned vector per WordPiece token. A text's
embedding is the mean of its tokens' rows, L2-normalized. No transformer runs, so there is no model
server, no API key and no per-query cost; a query embeds in well under a millisecond in plain
Python. `KYPP_EMBED_MODEL=potion-code-16M-v2` selects it (any MODELS name or a model directory); other
values still go to ollama, which needs a running server.

WHY stdlib (no numpy, no `tokenizers`): kypp's runtime deps are pyturso + mcp. The tokenizer every
potion model ships is the BERT WordPiece (lowercase, accent-strip, punctuation split), which is
~60 lines here, and the safetensors table is mmapped and decoded one row per token on demand, so
loading is an mmap and a JSON parse, not a 30 MB float decode. tests/test_potion.py pins parity
with the reference model2vec implementation on golden vectors.

Model files are fetched from Hugging Face at a pinned revision, checked against pinned SHA-256s,
and cached under ~/.cache/kypp/models (KYPP_MODEL_CACHE overrides). A directory path also works,
for offline installs. All potion models here are MIT (Minish Lab).
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import struct
import unicodedata
from typing import Callable

# name → (hf repo, revision, {file: sha256}). Pinned: a moved revision must not silently change
# every stored embedding (vectors from two models are not comparable; see store.ollama_embed NOTE).
MODELS: dict[str, tuple[str, str, dict[str, str]]] = {
    "potion-base-8M": ("minishlab/potion-base-8M", "bf8b056651a2c21b8d2565580b8569da283cab23", {
        "tokenizer.json": "e67e803f624fb4d67dea1c730d06e1067e1b14d830e2c2202569e3ef0f70bb50",
        "model.safetensors": "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2",
        "config.json": "2a6ac0e9aaa356a68a5688070db78fc3a464fefe85d2f06a1905ce3718687553"}),
    "potion-retrieval-32M": ("minishlab/potion-retrieval-32M", "6fc8051fab2a1e0ee76689cf08c853792ac285e7", {
        "tokenizer.json": "7d75cbc54318138807c401b0f0c9721117c628b39de8e8e0edb6cb17e0ee7d18",
        "model.safetensors": "07609e5bd33aad37900b3fd62f4ec96f6daec88ca4d46b9d8b928bfababf6ea0",
        "config.json": "63c00d90824c832c04ec1d02b6a983fb90489bf049f29fbff15ba481b8a432ee"}),
    "potion-code-16M-v2": ("minishlab/potion-code-16M-v2", "e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b", {
        "tokenizer.json": "107bbdcbad4bff1d299b7a4c3a2fb17c52890688b7dd0e4c9deab79d3c4f3d45",
        "model.safetensors": "75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c",
        "config.json": "148e5691a6fcc553437156859701fba017a1ba5d340b170f17e0f3668fb861a7"}),
}
MAX_TOKENS = 512  # model2vec's default encode truncation; matched so vectors agree with it
_DTYPES = {"F32": ("f", 4), "F16": ("e", 2)}


class StaticModel:
    """A loaded potion model: `embed(text)` → unit-length list[float] ([] when no token is known)."""

    def __init__(self, path: str):
        with open(os.path.join(path, "tokenizer.json"), encoding="utf-8") as f:
            tok = json.load(f)
        model = tok["model"]
        if model.get("type") != "WordPiece" or (tok.get("normalizer") or {}).get("type") != "BertNormalizer":
            raise ValueError(f"{path}: only BERT WordPiece potion models are supported")
        self.vocab: dict[str, int] = model["vocab"]
        self.unk_id = self.vocab.get(model.get("unk_token", "[UNK]"))
        self.prefix = model.get("continuing_subword_prefix", "##")
        self.max_word = model.get("max_input_chars_per_word", 100)
        self.lowercase = tok["normalizer"].get("lowercase", True)
        strip = tok["normalizer"].get("strip_accents")
        self.strip_accents = self.lowercase if strip is None else strip
        lens = sorted(len(t) for t in self.vocab)
        self.max_chars = MAX_TOKENS * lens[len(lens) // 2]  # model2vec clips chars before tokenizing

        with open(os.path.join(path, "model.safetensors"), "rb") as f:
            self._buf = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        (hlen,) = struct.unpack_from("<Q", self._buf, 0)
        header = json.loads(self._buf[8:8 + hlen])
        meta = header["embeddings"]
        code, size = _DTYPES[meta["dtype"]]
        rows, self.dim = meta["shape"]
        self._base = 8 + hlen + meta["data_offsets"][0]
        self._row = struct.Struct(f"<{self.dim}{code}")
        self._stride = self.dim * size
        self._cache: dict[int, tuple[float, ...]] = {}
        if rows < len(self.vocab):
            raise ValueError(f"{path}: {rows} embedding rows for {len(self.vocab)} tokens")

    def _vector(self, token_id: int) -> tuple[float, ...]:
        v = self._cache.get(token_id)
        if v is None:
            v = self._cache[token_id] = self._row.unpack_from(self._buf, self._base + token_id * self._stride)
        return v

    def tokenize(self, text: str) -> list[int]:
        ids: list[int] = []
        for word in _bert_words(_normalize(text[:self.max_chars], self.lowercase, self.strip_accents)):
            ids.extend(self._wordpiece(word))
            if len(ids) >= MAX_TOKENS:
                break
        ids = ids[:MAX_TOKENS]
        # model2vec drops [UNK] after truncation, so a text that is mostly unknown stays short
        return [i for i in ids if i != self.unk_id]

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > self.max_word:
            return [self.unk_id]
        out, start = [], 0
        while start < len(word):
            end = len(word)
            while end > start:
                piece = word[start:end] if start == 0 else self.prefix + word[start:end]
                if piece in self.vocab:
                    out.append(self.vocab[piece])
                    break
                end -= 1
            if end == start:
                return [self.unk_id]
            start = end
        return out

    def embed(self, text: str) -> list[float]:
        ids = self.tokenize(text)
        if not ids:
            return []
        acc = [0.0] * self.dim
        for i in ids:
            for d, x in enumerate(self._vector(i)):
                acc[d] += x
        norm = sum(x * x for x in acc) ** 0.5
        # mean then normalize == sum then normalize; a zero sum stays unembedded rather than NaN
        return [x / norm for x in acc] if norm > 0 else []


def _is_control(c: str) -> bool:
    return c not in "\t\n\r" and unicodedata.category(c).startswith("C")


def _is_cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2A6DF
            or 0x2A700 <= cp <= 0x2B73F or 0x2B740 <= cp <= 0x2B81F or 0x2B820 <= cp <= 0x2CEAF
            or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F)


def _normalize(text: str, lowercase: bool, strip_accents: bool) -> str:
    """HF BertNormalizer: clean text, pad CJK, strip accents, lowercase (that order)."""
    out = []
    for c in text:
        cp = ord(c)
        if cp == 0 or cp == 0xFFFD or _is_control(c):
            continue
        if c.isspace():
            out.append(" ")
        elif _is_cjk(cp):
            out.append(f" {c} ")
        else:
            out.append(c)
    s = "".join(out)
    if strip_accents:
        s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.lower() if lowercase else s


def _is_punct(c: str) -> bool:
    cp = ord(c)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(c).startswith("P")


def _bert_words(text: str) -> list[str]:
    """HF BertPreTokenizer: split on whitespace, then isolate every punctuation character."""
    words = []
    for chunk in text.split():
        cur = []
        for c in chunk:
            if _is_punct(c):
                if cur:
                    words.append("".join(cur))
                    cur = []
                words.append(c)
            else:
                cur.append(c)
        if cur:
            words.append("".join(cur))
    return words


def fetch(name: str, cache_dir: str | None = None) -> str:
    """Return a local directory holding `name`'s pinned files, downloading any that are missing.
    A file whose SHA-256 differs from the pin is rejected (and not kept), never loaded."""
    import urllib.request

    repo, rev, files = MODELS[name]
    root = cache_dir or os.environ.get("KYPP_MODEL_CACHE") or os.path.expanduser("~/.cache/kypp/models")
    path = os.path.join(root, f"{name}@{rev[:12]}")
    os.makedirs(path, exist_ok=True)
    marker = os.path.join(path, ".verified")
    if os.path.exists(marker):  # hashed once at download; re-hashing 32 MB per CLI process costs ~60 ms
        return path
    for fname, sha in files.items():
        dest = os.path.join(path, fname)
        if os.path.exists(dest) and _sha256(dest) == sha:
            continue
        tmp = dest + ".part"
        with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/{rev}/{fname}", timeout=120) as r, \
                open(tmp, "wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
        if _sha256(tmp) != sha:
            os.unlink(tmp)
            raise ValueError(f"{name}/{fname}: SHA-256 does not match the pinned revision {rev}")
        os.replace(tmp, dest)
    open(marker, "w").close()
    return path


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def is_potion(model: str) -> bool:
    """True for a pinned potion name or a local directory holding a model2vec tokenizer + table."""
    return model in MODELS or os.path.isfile(os.path.join(model, "model.safetensors"))


def potion_embed(model: str = "potion-code-16M-v2", cache_dir: str | None = None) -> Callable[[str], list[float]]:
    """A BYO `embed` for MemoryStore: MemoryStore(db, embed=potion_embed()). The model loads on
    first use, so wiring it costs nothing for a process that never embeds (e.g. browse-mode recall)."""
    loaded: list[StaticModel] = []

    def embed(text: str) -> list[float]:
        if not loaded:
            loaded.append(StaticModel(model if model not in MODELS else fetch(model, cache_dir)))
        return loaded[0].embed(text)

    return embed
