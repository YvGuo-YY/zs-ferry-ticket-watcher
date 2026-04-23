#!/usr/bin/env python3
"""启动脚本"""
import subprocess, sys, os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "src.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload",
    ])
