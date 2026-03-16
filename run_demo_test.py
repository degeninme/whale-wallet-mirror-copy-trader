#!/usr/bin/env python3
"""Run demo and capture output for debugging."""
import sys
from io import StringIO

# Force demo mode
sys.argv = ["main.py", "--demo", "--mode", "paper"]

# Capture stdout
old_stdout = sys.stdout
sys.stdout = StringIO()

try:
    import main
    main.main()
except Exception as e:
    import traceback
    with open("/root/whale-wallet-mirror-copy-trader/test_output.txt", "w") as f:
        f.write(f"ERROR: {e}\n")
        f.write(traceback.format_exc())
    raise

out = sys.stdout.getvalue()
sys.stdout = old_stdout

with open("/root/whale-wallet-mirror-copy-trader/test_output.txt", "w") as f:
    f.write(out)
print(out)
