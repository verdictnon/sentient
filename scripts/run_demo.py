"""Start Sentient on any laptop with the simulated store: python scripts/run_demo.py

No footage, models or GPU are needed. The first run builds the demo store once.
Point --data-dir at an existing workspace to add the demo store next to real
recordings; real recordings and their results are left alone.
"""

import argparse
import os
from pathlib import Path
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".local/demo-workspace")
    parser.add_argument("--rebuild", action="store_true", help="simulate the store again, ending yesterday")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    from app.demo_store import seed_store
    from app.p1.server import create_app

    os.environ["SENTIENT_PORTABLE_DEMO"] = "1"
    app = create_app(args.data_dir, include_generated=True)
    operations = app.state.operations
    if args.rebuild or operations.demo_state()["status"] == "absent":
        print("Building the simulated store. This happens once and takes a minute or two.")
        last = [None]

        def progress(stage, fraction):
            label = {"days": "Simulating two weeks of shoppers", "video": "Rendering the camera view"}.get(stage, "Saving")
            percent = int(fraction * 100)
            if (stage, percent // 10) != last[0]:
                last[0] = (stage, percent // 10)
                print(f"  {percent:3d}%  {label}", flush=True)

        seed_store(operations, progress=progress)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Sentient is running at {url}. Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, [url]).start()
    from app.showcase import import_footage

    import_footage(operations)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
