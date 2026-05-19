"""
Instala o git hook 'pre-push' no repositorio local.
Executar uma vez por clone (Windows / Linux / macOS):

    python scripts/install_hooks.py

A partir desse momento qualquer `git push` corre primeiro
validate_pipeline.py e aborta o envio se houver erros.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".git" / "hooks"
HOOK_PATH = HOOKS_DIR / "pre-push"

HOOK_BODY = """#!/bin/sh
# FundScope :: pre-push validator (auto-instalado por scripts/install_hooks.py)
echo ""
echo ">>> Pre-push: a correr validate_pipeline.py ..."
python validate_pipeline.py
status=$?
if [ $status -ne 0 ]; then
    echo ""
    echo ">>> Push ABORTADO pelo validator (exit $status)."
    exit $status
fi
exit 0
"""


def main() -> int:
    if not HOOKS_DIR.is_dir():
        print(f"[FAIL] {HOOKS_DIR} nao existe. Estas dentro de um repositorio git?")
        return 1

    HOOK_PATH.write_text(HOOK_BODY, encoding="utf-8", newline="\n")

    # tornar executavel (no-op no Windows, mas necessario em WSL/Linux)
    try:
        mode = HOOK_PATH.stat().st_mode
        HOOK_PATH.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass

    print(f"[OK] Hook instalado em: {HOOK_PATH}")
    print("     O proximo 'git push' executa validate_pipeline.py automaticamente.")
    print("     Para testar sem fazer push real:  python validate_pipeline.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
