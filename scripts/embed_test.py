"""M0 gate: real-call verification of the Ark embedding model (T005 / AC-001).

Verifies: ARK_API_KEY works, EMBED_MODEL answers, embedding dimension is
read from the response, and the Qdrant collection is created with that
dimension. Run AFTER src/ark_client.py (user-handwritten, T004) exists.

Usage: make embed-test   (requires make up first)
"""

import sys
from pathlib import Path

# 直接运行脚本时 sys.path[0] 是 scripts/ 而非仓库根，补上项目根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src import ark_client, config


def main() -> int:
    config.load_env()

    print(f"[1/3] Embedding one probe text with model from EMBED_MODEL ...")
    vectors = ark_client.embed(["first-rag setup probe"])
    dim = len(vectors[0])
    print(f"      OK: got {len(vectors)} vector(s), dimension = {dim}")

    print(f"[2/3] Creating collection '{config.COLLECTION}' (cosine, dim={dim}) ...")
    client = QdrantClient(url=config.QDRANT_URL)
    if client.collection_exists(config.COLLECTION):
        existing = client.get_collection(config.COLLECTION)
        existing_dim = existing.config.params.vectors.size
        if existing_dim != dim:
            print(f"      ERROR: collection exists with dim {existing_dim}, "
                  f"model returns {dim}. Embedding model changed — see plan.md "
                  f"risk F1 (new collection + full re-embed), do not reuse.")
            return 1
        print(f"      Collection already exists with matching dim {dim}, skipping")
    else:
        client.create_collection(
            collection_name=config.COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        print("      Created")

    print("[3/3] Done. Next: make sync  (AC-001 verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
