"""kypp.potion: the stdlib embedder matches model2vec.

The synthetic-model tests always run. The golden test pins token ids and leading vector components
taken from model2vec 0.10.0 on potion-code-16M-v2; it runs only when that model is already cached
(KYPP_MODEL_CACHE or ~/.cache/kypp/models), so CI never downloads 32 MB.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest

from kypp.potion import MODELS, StaticModel, is_potion


def tiny_model(path: str, dtype: str = "F32") -> None:
    vocab = {"[PAD]": 0, "[UNK]": 1, "re": 2, "##call": 3, "rank": 4, "(": 5, ")": 6, "cafe": 7}
    tok = {"normalizer": {"type": "BertNormalizer", "lowercase": True, "strip_accents": None},
           "model": {"type": "WordPiece", "vocab": vocab, "unk_token": "[UNK]",
                     "continuing_subword_prefix": "##", "max_input_chars_per_word": 100}}
    with open(os.path.join(path, "tokenizer.json"), "w") as f:
        json.dump(tok, f)
    rows = [[float(i), 1.0] for i in range(len(vocab))]
    code, size = {"F32": ("f", 4), "F16": ("e", 2)}[dtype]
    data = b"".join(struct.pack(f"<2{code}", *r) for r in rows)
    header = json.dumps({"embeddings": {"dtype": dtype, "shape": [len(rows), 2],
                                        "data_offsets": [0, len(data)]}}).encode()
    with open(os.path.join(path, "model.safetensors"), "wb") as f:
        f.write(struct.pack("<Q", len(header)) + header + data)


class SyntheticModelTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        tiny_model(self.dir)
        self.m = StaticModel(self.dir)

    def test_wordpiece_punctuation_accents_and_unknowns(self):
        # "recall" splits to re + ##call; "(" ")" are isolated; "Café" → "cafe"; "zzz" is [UNK], dropped
        self.assertEqual(self.m.tokenize("Recall(rank) Café zzz"), [2, 3, 5, 4, 6, 7])
        self.assertEqual(self.m.tokenize("\x00\x07"), [])

    def test_embed_is_normalized_mean(self):
        v = self.m.embed("rank cafe")  # rows (4,1) and (7,1): mean (5.5,1)
        n = (5.5 ** 2 + 1) ** 0.5
        self.assertAlmostEqual(v[0], 5.5 / n, places=6)
        self.assertAlmostEqual(v[1], 1 / n, places=6)
        self.assertEqual(self.m.embed("zzz"), [])  # nothing known → unembedded, not NaN

    def test_float16_table(self):
        d = tempfile.mkdtemp()
        tiny_model(d, "F16")
        self.assertEqual(StaticModel(d).embed("rank"), self.m.embed("rank"))

    def test_is_potion(self):
        self.assertTrue(is_potion("potion-code-16M-v2"))
        self.assertTrue(is_potion(self.dir))
        self.assertFalse(is_potion("nomic-embed-text"))


def cached(name: str) -> str | None:
    root = os.environ.get("KYPP_MODEL_CACHE") or os.path.expanduser("~/.cache/kypp/models")
    path = os.path.join(root, f"{name}@{MODELS[name][1][:12]}")
    return path if os.path.exists(os.path.join(path, ".verified")) else None


@unittest.skipUnless(cached("potion-code-16M-v2"), "potion-code-16M-v2 not cached")
class GoldenTest(unittest.TestCase):
    GOLDEN = [
        ("how does recall rank claims", [1132, 1518, 8134, 3638, 3450], [0.0827, 0.0407, 0.0574, 0.0094]),
        ("D1Store.recall(project_id)", [30326, 22812, 1066, 15, 8134, 9, 1625, 38, 7912, 10],
         [0.0215, 0.0614, 0.0526, 0.0451]),
        ("Café Straße 日本", [6671, 63441, 16502, 867, 879], [0.0182, 0.064, -0.0475, 0.0755]),
        ("\x00ctrl\tsnake_case_name", [29668, 6491, 38, 1556, 38, 1174], [-0.124, 0.0414, -0.0884, 0.0378]),
    ]

    def test_matches_model2vec(self):
        m = StaticModel(cached("potion-code-16M-v2"))
        for text, ids, head in self.GOLDEN:
            self.assertEqual(m.tokenize(text), ids, text)
            for got, want in zip(m.embed(text), head):
                self.assertAlmostEqual(got, want, delta=5e-4)


if __name__ == "__main__":
    unittest.main()
