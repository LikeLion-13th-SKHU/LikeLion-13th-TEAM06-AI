#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
헬퍼: 두 단계처럼 보이는 실행 래퍼
실제로는 pipeline.py 하나로 충분하지만, 로그 분리를 원해 유지
"""

import sys
import subprocess
from pathlib import Path

def main():
    if len(sys.argv) != 3:
        print("사용법: python run_all.py <input.json|.jsonl> <output.json>")
        sys.exit(1)

    in_path = Path(sys.argv[1]).resolve()
    out_path = Path(sys.argv[2]).resolve()

    # pipeline 직접 호출
    print(f"[1/1] 파이프라인 실행: {in_path.name} → {out_path.name}")
    code = subprocess.call([sys.executable, "pipeline.py", str(in_path), str(out_path)])
    if code != 0:
        print("[ERROR] pipeline 실행 실패")
        sys.exit(code)
    print("[DONE]")

if __name__ == "__main__":
    main()
