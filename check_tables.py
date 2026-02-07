import pandas as pd

df = pd.read_pickle("preprocess/processed_output/MinerU/bylw-zx/data.pkl")
tables = df[df["style"] == "Table"]

print(f"Total tables: {len(tables)}")
print("\nChecking alt_text for each table:")
print("=" * 50)

no_alt_count = 0
for idx, row in tables.iterrows():
    pt = row["para_text"]
    tid = row["table_id"]
    alt = pt.get("alt_text") if isinstance(pt, dict) else None
    has_content = bool(pt.get("content")) if isinstance(pt, dict) else False
    has_image = bool(pt.get("image_path")) if isinstance(pt, dict) else False

    if not alt:
        no_alt_count += 1
        print(f"Table {tid}: NO alt_text (content={has_content}, image={has_image})")
        if not has_content and not has_image:
            print(f"  -> EMPTY TABLE (no content, no image, no caption)")

print(f"\nSummary: {no_alt_count} out of {len(tables)} tables have NO alt_text")
