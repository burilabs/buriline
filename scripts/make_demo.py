"""Write the synthetic demo datasets to ./demo_data (same as ``buriline demo``)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buriline import demo  # noqa: E402

for k, v in demo.write_demo(Path("demo_data")).items():
    print(k, v)
