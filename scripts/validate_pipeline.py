"""
FundScope - Pre-Push Pipeline Validator
========================================
Corre localmente antes de cada `git push` (via hook pre-push) ou
manualmente. Aborta com sys.exit(1) se detetar:

  1. SyntaxError em qualquer ficheiro .py dentro de bot/
  2. Imports a modulos bot.* que nao existem
  3. Simbolos referenciados em `from bot.X import Y` mas Y nao
     existe no modulo X (apanha "declarei a constante mas esqueci
     de a expor em config.py")

Usa apenas a stdlib (ast, pathlib, sys) - sem dependencias externas.
Funciona em Windows / Linux / macOS sem alteracao.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent
BOT_DIR = PROJECT_ROOT / "bot"
PACKAGE_ROOTS = ("bot",)  # extender aqui se nascer um segundo package


# ---------------------------------------------------------------------------
# Helpers AST
# ---------------------------------------------------------------------------

def iter_python_files(directory: Path) -> Iterable[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def parse_file(path: Path) -> ast.AST | None:
    """Devolve a AST ou None (e regista erro) em caso de SyntaxError."""
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def extract_internal_imports(tree: ast.AST, source_file: Path):
    """
    Devolve uma lista de (module, names, lineno) referentes apenas aos
    packages internos (PACKAGE_ROOTS). Suporta imports absolutos e relativos.
      - `import bot.x`              -> ("bot.x", [], lineno)
      - `from bot.x import a, b`    -> ("bot.x", ["a", "b"], lineno)
      - `from .x import a` (em bot) -> ("bot.x", ["a"], lineno)
      - `from . import x` (em bot)  -> ("bot", ["x"], lineno)
    """
    file_package = _file_package(source_file)  # ex: "bot" para bot/phase0.py
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            level = node.level or 0
            if level > 0:
                # import relativo - resolve a partir do package do ficheiro
                resolved = _resolve_relative(file_package, node.module, level)
                if resolved is None or not _is_internal(resolved):
                    continue
                names = [alias.name for alias in node.names]
                found.append((resolved, names, node.lineno))
            else:
                module = node.module or ""
                if _is_internal(module):
                    names = [alias.name for alias in node.names]
                    found.append((module, names, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_internal(alias.name):
                    found.append((alias.name, [], node.lineno))
    return found


def _file_package(source_file: Path) -> str:
    """Devolve o nome do package onde reside o ficheiro (ex: 'bot')."""
    try:
        rel = source_file.relative_to(PROJECT_ROOT)
    except ValueError:
        return ""
    parts = rel.parts[:-1]  # tira o nome do ficheiro
    return ".".join(parts)


def _resolve_relative(file_package: str, module: str | None, level: int) -> str | None:
    """Resolve `from .x import ...` para o nome absoluto do modulo alvo."""
    if not file_package:
        return None
    parts = file_package.split(".")
    # level=1 fica no package; level=2 sobe um; etc.
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


def _is_internal(module: str) -> bool:
    if not module:
        return False
    root = module.split(".", 1)[0]
    return root in PACKAGE_ROOTS


def module_to_path(module: str) -> Path | None:
    parts = module.split(".")
    file_candidate = PROJECT_ROOT.joinpath(*parts).with_suffix(".py")
    pkg_candidate = PROJECT_ROOT.joinpath(*parts) / "__init__.py"
    if file_candidate.is_file():
        return file_candidate
    if pkg_candidate.is_file():
        return pkg_candidate
    return None


def top_level_names(tree: ast.AST) -> set[str]:
    """Nomes definidos ao nivel top-level de um modulo (funcoes, classes,
    atribuicoes simples/anotadas, imports). Suficiente para detetar
    'constante referenciada mas nao definida'."""
    names: set[str] = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                _collect_assign_targets(target, names)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = (alias.asname or alias.name).split(".")[0]
                names.add(top)
        elif isinstance(node, ast.If):
            # apanhar `if TYPE_CHECKING: ...` ou `if sys.version_info >= ...`
            for inner in node.body + node.orelse:
                if isinstance(inner, ast.Assign):
                    for target in inner.targets:
                        _collect_assign_targets(target, names)
    return names


def _collect_assign_targets(target: ast.AST, out: set[str]) -> None:
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            _collect_assign_targets(el, out)


# ---------------------------------------------------------------------------
# Pipeline de validacao
# ---------------------------------------------------------------------------

def main() -> int:
    if not BOT_DIR.is_dir():
        _hdr("ABORTADO")
        print(f"  Pasta '{BOT_DIR}' nao encontrada.")
        return 1

    _hdr("FundScope - Pre-Push Pipeline Validator")
    files = list(iter_python_files(BOT_DIR))
    errors: list[tuple[str, str, str]] = []

    # ----- Fase 1: sintaxe -----
    print(f"\n[1/3] Sintaxe ({len(files)} ficheiros)")
    trees: dict[Path, ast.AST] = {}
    for f in files:
        rel = f.relative_to(PROJECT_ROOT).as_posix()
        try:
            trees[f] = parse_file(f)
            print(f"  OK   {rel}")
        except SyntaxError as e:
            print(f"  FAIL {rel}  -> linha {e.lineno}: {e.msg}")
            errors.append(("SYNTAX", rel, f"linha {e.lineno}: {e.msg}"))
        except UnicodeDecodeError as e:
            print(f"  FAIL {rel}  -> encoding: {e}")
            errors.append(("ENCODING", rel, str(e)))

    if errors:
        return _finalize(errors)

    # ----- Fase 2: resolucao de modulos internos -----
    print("\n[2/3] Resolucao de imports internos (bot.*)")
    imports_index: list[tuple[Path, str, list[str], int]] = []
    for f, tree in trees.items():
        for module, names, lineno in extract_internal_imports(tree, f):
            imports_index.append((f, module, names, lineno))

    unresolved = 0
    for f, module, _names, lineno in imports_index:
        if module_to_path(module) is None:
            rel = f.relative_to(PROJECT_ROOT).as_posix()
            print(f"  FAIL {rel}:{lineno}  -> modulo '{module}' nao existe")
            errors.append(("IMPORT-MISSING", rel, f"linha {lineno}: {module}"))
            unresolved += 1
    if unresolved == 0:
        print(f"  OK   {len(imports_index)} import(s) interno(s) resolvidos")

    # ----- Fase 3: simbolos referenciados em 'from X import Y' -----
    print("\n[3/3] Simbolos referenciados em 'from bot.X import ...'")
    defined_cache: dict[Path, set[str]] = {}
    missing_symbols = 0
    checked_symbols = 0

    for f, module, names, lineno in imports_index:
        if not names:
            continue
        target_path = module_to_path(module)
        if target_path is None:
            continue  # ja reportado na fase 2

        if target_path not in defined_cache:
            try:
                defined_cache[target_path] = top_level_names(parse_file(target_path))
            except SyntaxError:
                defined_cache[target_path] = set()
        defined = defined_cache[target_path]

        for name in names:
            checked_symbols += 1
            if name == "*":
                continue
            if name in defined:
                continue
            # talvez seja um sub-modulo: from bot import config -> bot/config.py
            if module_to_path(f"{module}.{name}") is not None:
                continue
            rel = f.relative_to(PROJECT_ROOT).as_posix()
            print(f"  FAIL {rel}:{lineno}  -> '{name}' nao definido em '{module}'")
            errors.append(("SYMBOL-MISSING", rel, f"linha {lineno}: {name} <- {module}"))
            missing_symbols += 1

    if missing_symbols == 0:
        print(f"  OK   {checked_symbols} simbolo(s) verificado(s)")

    return _finalize(errors)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _hdr(title: str) -> None:
    bar = "=" * 64
    print(bar)
    print(f"  {title}")
    print(bar)


def _finalize(errors: list[tuple[str, str, str]]) -> int:
    print()
    if errors:
        _hdr(f"BLOQUEADO - {len(errors)} problema(s)")
        for kind, where, msg in errors:
            print(f"  [{kind:<15}] {where}  ::  {msg}")
        print()
        print("  >> Push abortado. Corrige os erros acima e tenta de novo.")
        sys.stdout.write("\a")  # beep
        sys.stdout.flush()
        return 1
    _hdr("OK - Pipeline validado. Push autorizado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
