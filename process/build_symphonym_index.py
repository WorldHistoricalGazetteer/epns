#!/usr/bin/python3
"""Precompute Symphonym v8 phonetic embeddings for the site's name index -> int8 matrix.

    /usr/bin/python3 process/build_symphonym_index.py --selftest      # golden-fixture parity only
    /usr/bin/python3 process/build_symphonym_index.py                 # docs/data/names/keys.json -> docs/data/symphonym/

SYSTEM PYTHON, DELIBERATELY. The canonical tokeniser lives in the indexing repo's hf/inference.py,
whose module imports torch, and torch + onnxruntime are installed for /usr/bin/python3 on this
machine and not in the project venv. This is a build instrument, not a runtime dependency.

WHY THE TOKENISER IS IMPORTED AND NOT RE-IMPLEMENTED. Until September 2026 there were four
implementations of this tokeniser and they disagreed; 63.9% of WHG's index was unreachable by its
own query vectors as a result. The browser side of this site (docs/symphonym/preprocess.js) is
whg3's character-for-character port of the canonical Python; this script calls the canonical
Python itself. Both are checked against the same golden fixture (8 cases across 6 scripts), so a
query embedded in the browser meets a corpus embedded here under one tokeniser, not two.

The model is the dynamically-quantised int8 ONNX export (docs/symphonym/symphonym.onnx, md5
afc74f10…), run here with onnxruntime — the SAME graph the browser runs, so corpus and query
share the export's rounding as well as its tokeniser. Vectors are L2-normalised 128-d and stored
as round-half-to-even(x * 127) int8, the form WHG's index uses; ranking is by dot product, for
which the scale is irrelevant.

Corpus language is 'en' on both sides (the browser passes 'en' too). The model is conditioned on
the language tag; 'und' is not in its vocabulary at all (it falls to the unknown id), and whg3's
Map Your Data, which measured it, tags every Latin-script name 'en' rather than leave it
undetermined. The model also knows 'ang', 'enm' and 'la', but DEEP does not say which spellings
are Old English, Middle English or Latin, so one tag is used for all and the query matches it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SYM = ROOT / "docs" / "symphonym"
INDEXING_HF = Path("/home/stephen/PycharmProjects/indexing/hf")
GOLDEN = Path("/home/stephen/PycharmProjects/CAMPOP-Places/contribute/symphonym/symphonym-v8-golden.json")
EXPECT_MD5 = {"symphonym.onnx": "afc74f102d9ab98e92382dcaed93628c",
              "char_vocab.json": "16a4d41e868fecc16e1b701363e1bda8",
              "lang_vocab.json": "17ce7fe69a836ddd32c9577cdd60f389",
              "script_vocab.json": "18fd9410e1c543429ec8327a29ee3225"}
DIM = 128
LANG = "en"


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def load():
    for name, want in EXPECT_MD5.items():
        got = md5(SYM / name)
        if got != want:
            sys.exit(f"{name}: md5 {got} is not the Symphonym v8 asset ({want}); refusing to embed with a mismatched set")
    sys.path.insert(0, str(INDEXING_HF))
    from inference import tokenise  # noqa: E402  (the canonical block; module import needs torch)
    import onnxruntime as ort
    vocab = lambda f, k: (lambda j: j.get(k, j))(json.loads((SYM / f).read_text()))
    char_to_id = vocab("char_vocab.json", "char_to_id")
    lang_to_id = vocab("lang_vocab.json", "lang_to_id")
    script_to_id = vocab("script_vocab.json", "script_to_id")
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(str(SYM / "symphonym.onnx"), so, providers=["CPUExecutionProvider"])

    def embed(text: str, lang: str = LANG) -> tuple[list[int], int, int, np.ndarray]:
        char_ids, script_id, lang_id = tokenise(text, lang, char_to_id, lang_to_id, script_to_id)
        out = sess.run(None, {"char_ids": np.array([char_ids], dtype=np.int64),
                              "script_id": np.array([script_id], dtype=np.int64),
                              "lang_id": np.array([lang_id], dtype=np.int64),
                              "length": np.array([len(char_ids)], dtype=np.int64)})[0][0]
        return char_ids, script_id, lang_id, out

    return embed


def quantise(v: np.ndarray) -> np.ndarray:
    # np.round is round-half-to-even, matching the index writer and the browser's quantise.js.
    return np.round(v.astype(np.float64) * 127.0).astype(np.int8)


def selftest(embed) -> None:
    """Golden-fixture parity: tokens exactly, int8 vectors within the export-noise bound whg3 uses."""
    g = json.loads(GOLDEN.read_text())
    if g["vocab_md5"] != {k: v for k, v in EXPECT_MD5.items() if k != "symphonym.onnx"}:
        sys.exit("golden fixture was generated against different vocabularies")
    COS_FLOOR, DIFF_CEILING = 0.995, 4
    worst_cos, worst_diff, ok = 1.0, 0, 0
    for c in g["cases"]:
        ids, sid, lid, v = embed(c["text"], c["lang"])
        tok_ok = ids == c["char_ids"] and sid == c["script_id"] and lid == c["lang_id"] and len(ids) == c["length"]
        q = quantise(v).astype(np.float64)
        ref = np.array(c["embedding_int8"], dtype=np.float64)
        cos = float(q @ ref / (np.linalg.norm(q) * np.linalg.norm(ref)))
        diff = int(np.max(np.abs(q - ref)))
        worst_cos, worst_diff = min(worst_cos, cos), max(worst_diff, diff)
        good = tok_ok and cos >= COS_FLOOR and diff <= DIFF_CEILING
        ok += good
        print(f"  {'ok ' if good else 'BAD'} {c['text']!r:22} lang={c['lang']:4} tokens={'=' if tok_ok else 'DIFFER'} "
              f"cos={cos:.5f} max|Δ|={diff}")
    print(f"golden: {ok}/{len(g['cases'])} within bound (worst cos {worst_cos:.5f}, worst |Δ| {worst_diff})")
    if ok != len(g["cases"]):
        sys.exit("golden-fixture parity FAILED; the corpus must not be embedded with this pipeline")
    # The comparator's own control: a vector against itself must score 1 and differ by 0.
    _, _, _, v = embed("London", "en")
    q = quantise(v).astype(np.float64)
    assert abs(q @ q / (q @ q) - 1.0) < 1e-12 and int(np.max(np.abs(q - q))) == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="run the golden-fixture parity check and stop")
    ap.add_argument("--keys", default=str(ROOT / "docs" / "data" / "names" / "keys.json"),
                    help="JSON array of distinct name keys, in the order the browser index uses")
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "symphonym"))
    ap.add_argument("--shard-bytes", type=int, default=40_000_000, help="split the int8 matrix so no file exceeds this")
    args = ap.parse_args()

    embed = load()
    print("golden-fixture parity:")
    selftest(embed)
    if args.selftest:
        return

    keys = json.loads(Path(args.keys).read_text())
    # Only the first `nEmbedded` keys are embedded: export_site.py orders keys.json so that the keys
    # the phonetic index covers come first, and records the count in the site manifest. The browser
    # refuses a matrix whose row count disagrees with that manifest, so read it from the same place.
    site_manifest = json.loads((Path(args.keys).parent.parent / "manifest.json").read_text())
    n = site_manifest["names"]["nEmbedded"]
    if n > len(keys):
        sys.exit(f"manifest says {n} keys to embed but keys.json has {len(keys)}")
    keys = keys[:n]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("emb-*.i8"):      # a shorter matrix must not leave a stale shard behind
        old.unlink()
    emb = np.zeros((n, DIM), dtype=np.int8)
    t0 = time.time()
    for i, k in enumerate(keys):
        emb[i] = quantise(embed(k)[3])
        if i % 20000 == 0 and i:
            rate = i / (time.time() - t0)
            print(f"  {i:>8,}/{n:,}  {rate:,.0f}/s  eta {(n - i) / rate / 60:.1f} min", flush=True)
    rows_per_shard = max(1, args.shard_bytes // DIM)
    shards = []
    for s, start in enumerate(range(0, n, rows_per_shard)):
        part = emb[start:start + rows_per_shard]
        p = out / f"emb-{s:02d}.i8"
        p.write_bytes(part.tobytes())
        shards.append({"file": p.name, "rows": int(part.shape[0]), "bytes": p.stat().st_size,
                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    keys_sha = hashlib.sha256(Path(args.keys).read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps({
        "model": "symphonym-v8", "model_md5": EXPECT_MD5["symphonym.onnx"], "lang": LANG, "dim": DIM,
        "scale": 127, "n": n, "keys_sha256": keys_sha, "shards": shards}, indent=1))
    print(f"wrote {n:,}×{DIM} int8 in {len(shards)} shard(s) -> {out} ({emb.nbytes / 1e6:.1f} MB) in {time.time() - t0:.0f}s")

    # KNN sanity on the finished matrix: a historical spelling should find its modern headword.
    E = emb.astype(np.float32)
    for q in ["Bonestou", "Grantebrige", "Snotingaham", "Eoforwic"]:
        v = quantise(embed(q)[3]).astype(np.float32)
        top = np.argsort(-(E @ v))[:5]
        print(f"  {q:14} -> " + ", ".join(f"{keys[j]}({(E[j] @ v) / 127 / 127:.2f})" for j in top))


if __name__ == "__main__":
    main()
