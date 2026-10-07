"""Stand-in child processes for the MCP module tests. Each mode mimics one real-world behaviour.

    python mcp_fake.py print-exit 3          print two lines, exit with code 3 (crash)
    python mcp_fake.py sleep                 print one line, run until killed
    python mcp_fake.py tree <file>           start a grandchild, write its PID to <file>, run until killed
    python mcp_fake.py listen <port>         open a TCP port (like a server), run until killed
"""
import socket
import subprocess
import sys
import time

mode = sys.argv[1]

if mode == "print-exit":
    print("hello from fake", flush=True)
    print("ERROR: boom - fake crash", flush=True)
    sys.exit(int(sys.argv[2]))

if mode == "sleep":
    print("INFO: fake sleeping", flush=True)
    while True:
        time.sleep(1)

if mode == "tree":
    # like the venv launcher on Windows: the real work happens in a child process
    child = subprocess.Popen([sys.executable, __file__, "sleep"])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(str(child.pid))
    while True:
        time.sleep(1)

if mode == "listen":
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", int(sys.argv[2])))
    srv.listen()
    print(f"INFO: listening on {sys.argv[2]}", flush=True)
    while True:
        time.sleep(1)

sys.exit(f"unknown mode {mode}")
