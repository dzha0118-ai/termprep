"""Multi-process contention test for the checkpoint (HITL) session store.

Regression guard for a live-production bug: the Space ran ``gunicorn -w 2``, and
``/api/pipeline/start`` on worker A followed by ``/api/pipeline/{id}/resume`` on
worker B returned "session not found or expired" for a perfectly healthy session —
the same session id alternated between 404 and 200 across identical requests.

Root cause (two defects):
  1. sessions lived only in process memory;
  2. after moving them to SQLite, ``get()`` still performed a ``DELETE`` (cleanup)
     on the read path, and every connection ran ``PRAGMA journal_mode=WAL``
     (needs a brief exclusive lock) -> intermittent ``database is locked`` which
     the caller swallowed and reported as 404.

This suite spawns several real OS processes against the same database file (the
gunicorn-equivalent condition) and asserts zero mis-reads and zero errors.

Uses subprocess + files for IPC because the sandbox blocks named pipes
(``multiprocessing.Queue`` is unavailable here).
"""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "_hitl_storage_tmp")
PY = sys.executable
READS = 60          # reads per worker process
WORKERS = 4


def child(idx: int) -> int:
    """One 'gunicorn worker': hammer the shared session while writing too."""
    from termprep import hitl_store as S

    misses, errors = 0, []
    for i in range(READS):
        try:
            if S.get("shared") is None:
                misses += 1
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
        if i % 4 == 0:
            try:
                S.save("w%d-%d" % (idx, i), {"session_id": "w%d-%d" % (idx, i)}, ttl=600)
            except Exception as e:
                errors.append(f"save {type(e).__name__}: {e}")
    with open(os.path.join(DATA, "out_%d.json" % idx), "w", encoding="utf-8") as fh:
        json.dump({"misses": misses, "errors": errors}, fh)
    return 0


def parent() -> int:
    shutil.rmtree(DATA, ignore_errors=True)
    os.makedirs(DATA, exist_ok=True)
    os.environ["TERMPREP_DATA_DIR"] = DATA
    sys.path.insert(0, HERE)

    from termprep import hitl_store as S

    S.save("shared", {"session_id": "shared", "terms": list(range(5))}, ttl=600)

    t0 = time.time()
    procs = [
        subprocess.Popen([PY, os.path.abspath(__file__), "--worker", str(i)],
                         cwd=HERE, env=dict(os.environ),
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for i in range(WORKERS)
    ]
    stderrs = [p.communicate()[1] for p in procs]
    dt = time.time() - t0

    results = []
    for i in range(WORKERS):
        path = os.path.join(DATA, "out_%d.json" % i)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                results.append(json.load(fh))
        else:
            results.append({"misses": READS, "errors": ["child produced no result"]})

    total_miss = sum(r["misses"] for r in results)
    total_err = sum(len(r["errors"]) for r in results)
    st = S.self_test()
    last_err = S.last_error()
    count = S.count()

    print(f"{WORKERS} processes x {READS} reads (with concurrent writes)  {dt:.2f}s")
    print(f"  mis-reads (would become 'session not found'): {total_miss}")
    print(f"  exceptions                                  : {total_err}")
    for r in results:
        for e in r["errors"][:2]:
            print("    -", e)
    for se in stderrs:
        if se:
            print("    stderr:", se.decode("utf-8", "replace").strip()[:200])
    print(f"  self-test write->read->drop: {st}")
    print(f"  last_error: {last_err or '(none)'}")
    print(f"  sessions row count: {count}")

    ok = total_miss == 0 and total_err == 0 and bool(st.get("ok"))
    print("\nRESULT:", "PASS - store is stable across processes"
          if ok else "FAIL - concurrent access still broken")
    shutil.rmtree(DATA, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.exit(child(int(sys.argv[sys.argv.index("--worker") + 1])))
    sys.exit(parent())
