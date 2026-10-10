"""pytest 없이도 돌릴 수 있는 간단 러너: python run_tests.py"""
import sys, tempfile, pathlib, inspect
sys.path[:0] = ["src", "tests"]
import test_intraday as a, test_pipeline as b
for m in (a, b):
    for n, f in inspect.getmembers(m, inspect.isfunction):
        if n.startswith("test_"):
            f(pathlib.Path(tempfile.mkdtemp())) if "tmp_path" in inspect.signature(f).parameters else f()
            print("PASS", n)
