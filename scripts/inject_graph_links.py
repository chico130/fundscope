"""
inject_graph_links.py — Injeta links [[...]] do Obsidian em ficheiros .md
via regex, sem reescrever conteudo. Idempotente.

Regras:
- NUNCA edita dentro de blocos ``` ``` ou de inline `code`.
- NUNCA edita dentro de frontmatter YAML.
- NUNCA duplica links ja existentes ([[X]] ou [[alvo|X]]).
- Por defeito, liga apenas a 1a ocorrencia de cada termo por ficheiro
  (configuravel via --all-occurrences).
- Match em word-boundary Unicode.
- Faz dry-run por defeito; so escreve com --apply.

Uso:
  python scripts/inject_graph_links.py                 # dry-run
  python scripts/inject_graph_links.py --apply         # escreve
  python scripts/inject_graph_links.py --all-occurrences --apply
"""
from __future__ import annotations
import argparse, re, sys, yaml
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
FENCED_RE      = re.compile(r"```.*?```", re.DOTALL)
INLINE_RE      = re.compile(r"`[^`\n]+`")
LINKED_RE      = re.compile(r"\[\[[^\]]+\]\]")


def split_protected(text: str):
    """Devolve lista de (segmento, editavel?) preservando ordem."""
    out, idx = [], 0
    m = FRONTMATTER_RE.match(text)
    if m:
        out.append((text[:m.end()], False))
        idx = m.end()
    pattern = re.compile(
        rf"(?P<fenced>{FENCED_RE.pattern})|"
        rf"(?P<inline>{INLINE_RE.pattern})|"
        rf"(?P<linked>{LINKED_RE.pattern})",
        re.DOTALL,
    )
    for m in pattern.finditer(text, idx):
        if m.start() > idx:
            out.append((text[idx:m.start()], True))
        out.append((m.group(0), False))
        idx = m.end()
    if idx < len(text):
        out.append((text[idx:], True))
    return out


def compile_terms(glossary: dict):
    # Ordenar por comprimento desc (termos mais longos têm prioridade)
    items = sorted(glossary.items(), key=lambda kv: -len(kv[0]))
    compiled = []
    for term, target in items:
        # Ignorar termos que sao paths (contem /)
        if "/" in str(term):
            continue
        esc = re.escape(str(term))
        # Bordas: excluir letras/digitos/ponto/hifen/barra E caracteres de links Obsidian ([, ], |)
        # Isto evita que um termo seja injetado dentro de um [[link]] ja existente
        rx = re.compile(rf"(?<![\w.\-/\[\]|]){esc}(?![\w.\-/\[\]|])")
        compiled.append((rx, str(term), str(target)))
    return compiled


def inject(text: str, compiled, first_only: bool = True):
    segments = split_protected(text)
    seen_in_file: set[str] = set()
    changed = 0
    out = []
    for seg, editable in segments:
        if not editable:
            out.append(seg)
            continue
        new = seg
        for rx, term, target in compiled:
            if first_only and term in seen_in_file:
                continue

            def _sub(m, _term=term, _target=target):
                nonlocal changed
                changed += 1
                if first_only:
                    seen_in_file.add(_term)
                matched = m.group(0)
                # Se o target e identico ao termo, usa [[target]]
                if _target == matched:
                    return f"[[{_target}]]"
                # Caso contrario usa [[target|termo]] para preservar o texto original
                return f"[[{_target}|{matched}]]"

            new = rx.sub(_sub, new, count=1 if first_only else 0)
        out.append(new)
    return "".join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault", default="vault", help="Pasta a varrer (default: vault)")
    ap.add_argument("--glossary", default="vault/_meta/glossary.yml")
    ap.add_argument("--apply", action="store_true", help="Escreve mudancas")
    ap.add_argument("--all-occurrences", action="store_true",
                    help="Liga todas as ocorrencias (nao so a 1a por ficheiro)")
    args = ap.parse_args()

    glossary_path = Path(args.glossary)
    if not glossary_path.exists():
        print(f"ERRO: glossario nao encontrado em {args.glossary}")
        return 1

    glossary = yaml.safe_load(glossary_path.read_text(encoding="utf-8"))
    if not isinstance(glossary, dict):
        print("ERRO: glossario.yml invalido (deve ser um mapeamento chave: valor)")
        return 1

    compiled = compile_terms(glossary)
    print(f"Glossario: {len(compiled)} termos compilados")

    vault_path = Path(args.vault)
    if not vault_path.exists():
        print(f"ERRO: pasta vault nao encontrada: {args.vault}")
        return 1

    md_files = sorted(vault_path.rglob("*.md"))
    print(f"Ficheiros .md encontrados: {len(md_files)}")
    print()

    total_files_changed = 0
    total_links_injected = 0

    for md in md_files:
        original = md.read_text(encoding="utf-8")
        new_text, n = inject(original, compiled, first_only=not args.all_occurrences)
        if n > 0:
            total_files_changed += 1
            total_links_injected += n
            rel = md.relative_to(".")
            status = "APPLY" if args.apply else "DRY  "
            print(f"[{status}] {rel}  (+{n} links)")
            if args.apply:
                md.write_text(new_text, encoding="utf-8")

    print()
    print(f"Ficheiros modificados : {total_files_changed}")
    print(f"Links injetados       : {total_links_injected}")
    if not args.apply:
        print("\nDry-run. Re-corre com --apply para escrever.")
    else:
        print("\nConcluido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
