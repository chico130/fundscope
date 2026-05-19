"""
graph_lint.py — Valida regras de conectividade R-CN1..R-CN6 do vault Obsidian.
Saida: lista de violacoes por ficheiro. Exit code 1 se houver violacoes criticas.

Regras:
  R-CN1: Toda nota tem parent_moc no frontmatter
  R-CN2: Toda nota tem >= 3 links [[...]] no corpo
  R-CN3: Toda nota tem >= 1 link de retorno para o MOC pai no corpo
  R-CN4: MOCs tem >= 5 outbound links no corpo
  R-CN5: Notas atomicas (type: atom) tem < 250 palavras no corpo
  R-CN6: Notas referenciam apenas alvos que existem no vault

Uso:
  python scripts/graph_lint.py [--vault vault]
"""
from __future__ import annotations
import re, sys, yaml
from pathlib import Path

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
LINK_RE        = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
FENCED_RE      = re.compile(r"```.*?```", re.DOTALL)


def strip_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    return meta, text[m.end():]


def strip_code_blocks(text: str) -> str:
    return FENCED_RE.sub("", text)


def count_words(text: str) -> int:
    return len(text.split())


def extract_links(body: str) -> list[str]:
    clean = strip_code_blocks(body)
    return LINK_RE.findall(clean)


def run_lint(vault_path: Path) -> list[dict]:
    violations = []
    md_files = sorted(vault_path.rglob("*.md"))

    # Construir indice de nomes de ficheiros existentes no vault
    existing = {f.stem for f in md_files}
    # Adicionar nomes sem extensao de todos os .md (resolucao Obsidian)
    existing.update(f.name for f in md_files)

    for md in md_files:
        rel = str(md.relative_to(vault_path.parent))
        text = md.read_text(encoding="utf-8")
        meta, body = strip_frontmatter(text)
        body_clean = strip_code_blocks(body)
        links = extract_links(body)
        note_type = meta.get("type", "unknown")
        lo = meta.get("links_obrigatorios", {}) or {}
        parent_moc = lo.get("parent_moc", "")

        def add(rule, msg, severity="WARN"):
            violations.append({"file": rel, "rule": rule, "msg": msg, "sev": severity})

        # R-CN1: parent_moc no frontmatter
        if not parent_moc or parent_moc in ("", "self"):
            if note_type not in ("template",) and "self" not in str(parent_moc):
                add("R-CN1", f"parent_moc ausente ou vazio (type={note_type})")

        # R-CN2: >= 3 links no corpo
        if len(links) < 3:
            severity = "ERROR" if note_type == "moc" else "WARN"
            add("R-CN2", f"Apenas {len(links)} link(s) no corpo (minimo: 3)", severity)

        # R-CN3: link de retorno para o MOC pai
        if parent_moc and "self" not in str(parent_moc):
            moc_name = re.sub(r"^\[\[|\]\]$", "", str(parent_moc)).split("|")[0].strip()
            if moc_name and not any(moc_name in lk for lk in links):
                add("R-CN3", f"Sem link de retorno para {parent_moc} no corpo")

        # R-CN4: MOCs tem >= 5 links
        if note_type == "moc" and len(links) < 5:
            add("R-CN4", f"MOC tem apenas {len(links)} link(s) (minimo: 5)", "ERROR")

        # R-CN5: atoms < 250 palavras
        if note_type == "atom":
            wc = count_words(body_clean)
            if wc >= 250:
                add("R-CN5", f"Nota atomica com {wc} palavras (maximo: 250)")

        # R-CN6: alvos de links existem no vault
        for lk in links:
            target = lk.split("|")[0].strip()
            # Ignorar paths externos, ancora (#), e paths com /
            if target.startswith("#") or target.startswith("http"):
                continue
            base = Path(target).stem
            if base not in existing and target not in existing:
                add("R-CN6", f"Link alvo inexistente: [[{target}]]")

    return violations


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vault", default="vault")
    ap.add_argument("--errors-only", action="store_true", help="Mostra apenas ERRORs")
    args = ap.parse_args()

    vault_path = Path(args.vault)
    if not vault_path.exists():
        print(f"ERRO: vault nao encontrado: {args.vault}")
        return 1

    violations = run_lint(vault_path)

    if args.errors_only:
        violations = [v for v in violations if v["sev"] == "ERROR"]

    if not violations:
        print("OK — Sem violacoes de conectividade.")
        return 0

    # Agrupar por ficheiro
    from collections import defaultdict
    by_file: dict[str, list] = defaultdict(list)
    for v in violations:
        by_file[v["file"]].append(v)

    errors = sum(1 for v in violations if v["sev"] == "ERROR")
    warns  = sum(1 for v in violations if v["sev"] == "WARN")

    print(f"Resultado: {errors} ERROR(s), {warns} WARN(s) em {len(by_file)} ficheiro(s)\n")
    for fpath, vs in sorted(by_file.items()):
        print(f"  {fpath}")
        for v in vs:
            prefix = "  [ERROR]" if v["sev"] == "ERROR" else "  [ WARN]"
            print(f"    {prefix} {v['rule']}: {v['msg']}")

    print()
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
