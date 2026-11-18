from pathlib import Path
import pandas as pd

DATA_DIR = Path("data")

for csv in sorted(DATA_DIR.glob("*.csv")):
    df = pd.read_csv(csv)
    print(f"\n=== {csv.name} ===")
    print(f"rows: {len(df)}, columns: {len(df.columns)}")
    nulls = df.isna().sum()
    nulls = nulls[nulls > 0]
    print("nulls per column:")
    print(nulls.to_string() if len(nulls) else "none")
    print(df.head(2).to_string())