"""查看本地 Chroma 向量库状态（总量 + 按文件分布 + 抽样 metadata）。"""

from collections import Counter

try:
    from scripts.chroma_embed import get_chroma_collection
except ModuleNotFoundError:
    from chroma_embed import get_chroma_collection


def main() -> None:
    collection = get_chroma_collection()
    total = collection.count()
    print(f"collection={collection.name}")
    print(f"total_vectors={total}")

    if total == 0:
        return

    data = collection.get(include=["metadatas"])
    metadatas = data.get("metadatas", []) or []

    by_file = Counter((m or {}).get("source_filename", "unknown") for m in metadatas)
    print("vectors_by_file:")
    for filename, count in sorted(by_file.items(), key=lambda x: x[0]):
        print(f"  {filename}: {count}")

    sample = collection.peek(limit=min(5, total))
    print("sample_metadatas=")
    print(sample.get("metadatas", []))


if __name__ == "__main__":
    main()
