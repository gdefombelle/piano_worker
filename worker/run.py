# worker/run.py
import os
os.environ.setdefault("PYTUNE_IS_WORKER", "1")
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from piano_tasks import app as celery_app
    import piano_tasks as _pt
    print(">> piano_tasks chargé depuis:", _pt.__file__)
except Exception as e:
    print("!! Impossible d'importer piano_tasks:", e)
    raise

if __name__ == "__main__":
    # équiv. : celery -A piano_tasks worker -Q i2i_tasks_queue -l DEBUG -P solo
    celery_app.worker_main(["worker", "-Q", "i2i_tasks_queue", "-l", "DEBUG", "-P", "solo"])
