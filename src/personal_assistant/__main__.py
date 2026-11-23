"""支持 `python -m personal_assistant` 直接调用 CLI。"""

from personal_assistant.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
