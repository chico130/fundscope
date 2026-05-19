"""
inject_frontmatter.py — Prepende frontmatter YAML canonico nos .md do vault.
Idempotente: nao toca ficheiros que ja tenham frontmatter.
Uso: python scripts/inject_frontmatter.py [--apply]
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
from datetime import date

TODAY = date.today().isoformat()
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# Mapeamento: path relativo -> frontmatter a injectar
SPECS: dict[str, dict] = {
    "vault/specs/CRO_SPEC.md": {
        "id": "spec-cro",
        "title": "CRO — Especificação Completa",
        "type": "spec",
        "domain": "cro",
        "regime": "n/a",
        "tags": ["spec", "cro", "kill-switch", "risco-sistemico"],
        "parent_moc": "[[MOC_CRO]]",
        "vizinhos": "[[MOC_Bonnie]] [[MOC_Clyde]] [[MOC_Infraestrutura]]",
        "status": "stable",
    },
    "vault/specs/FASE-1.md": {
        "id": "spec-fase1",
        "title": "FASE-1 — Roadmap e Diagnóstico do Bot",
        "type": "spec",
        "domain": "geral",
        "regime": "n/a",
        "tags": ["spec", "roadmap", "bot", "diagnostico"],
        "parent_moc": "[[MOC_FundScope]]",
        "vizinhos": "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]",
        "status": "stable",
    },
    "vault/specs/FUNDSCOPE_CLAUDE_CODE_SPEC.md": {
        "id": "spec-global",
        "title": "FundScope — Especificação Técnica Global",
        "type": "spec",
        "domain": "geral",
        "regime": "n/a",
        "tags": ["spec", "global", "arquitetura"],
        "parent_moc": "[[MOC_FundScope]]",
        "vizinhos": "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]",
        "status": "stable",
    },
    "vault/specs/SPEC_HANDOFF_SONNET.md": {
        "id": "spec-handoff",
        "title": "Spec Handoff — Auth, Routing e serve.py",
        "type": "spec",
        "domain": "infra",
        "regime": "n/a",
        "tags": ["spec", "auth", "serve", "routing"],
        "parent_moc": "[[MOC_Infraestrutura]]",
        "vizinhos": "[[MOC_Frontend]] [[MOC_CRO]]",
        "status": "stable",
    },
    "vault/specs/VPS_MIGRATION_SPEC.md": {
        "id": "spec-vps",
        "title": "VPS Migration — Oracle Cloud",
        "type": "spec",
        "domain": "infra",
        "regime": "n/a",
        "tags": ["spec", "vps", "oracle", "systemd", "caddy"],
        "parent_moc": "[[MOC_Infraestrutura]]",
        "vizinhos": "[[MOC_Frontend]] [[MOC_CRO]]",
        "status": "draft",
    },
    "vault/specs/ROADMAP_FRONTEND.md": {
        "id": "spec-roadmap-frontend",
        "title": "ROADMAP — Frontend Dashboard",
        "type": "spec",
        "domain": "frontend",
        "regime": "n/a",
        "tags": ["spec", "frontend", "roadmap", "ux"],
        "parent_moc": "[[MOC_Frontend]]",
        "vizinhos": "[[MOC_Infraestrutura]] [[MOC_CRO]]",
        "status": "stable",
    },
    "vault/specs/EARNINGS_TAB.md": {
        "id": "spec-earnings",
        "title": "Earnings Tab — Especificação",
        "type": "spec",
        "domain": "frontend",
        "regime": "n/a",
        "tags": ["spec", "earnings", "frontend", "calendario"],
        "parent_moc": "[[MOC_Frontend]]",
        "vizinhos": "[[MOC_Clyde]] [[MOC_Bonnie]]",
        "status": "stable",
    },
    "vault/atoms/master_prompts.md": {
        "id": "atom-master-prompts",
        "title": "Master Prompts — Gates do Sistema",
        "type": "atom",
        "domain": "geral",
        "regime": "n/a",
        "tags": ["atom", "prompts", "clyde", "bonnie", "gates"],
        "parent_moc": "[[MOC_FundScope]]",
        "vizinhos": "[[MOC_Clyde]] [[MOC_Bonnie]] [[MOC_CRO]]",
        "status": "stable",
    },
    "vault/templates/template_learner.md": {
        "id": "template-learner",
        "title": "Template — Learner Pattern",
        "type": "template",
        "domain": "bonnie",
        "regime": "n/a",
        "tags": ["template", "learner", "bonnie", "ml"],
        "parent_moc": "[[MOC_Bonnie]]",
        "vizinhos": "[[MOC_Clyde]] [[MOC_Infraestrutura]]",
        "status": "draft",
    },
}


def build_frontmatter(meta: dict) -> str:
    lines = ["---"]
    lines.append(f"id: {meta['id']}")
    lines.append(f"title: \"{meta['title']}\"")
    lines.append(f"type: {meta['type']}")
    lines.append(f"domain: {meta['domain']}")
    lines.append(f"regime: {meta['regime']}")
    tags_str = "[" + ", ".join(meta["tags"]) + "]"
    lines.append(f"tags: {tags_str}")
    lines.append("links_obrigatorios:")
    lines.append(f"  parent_moc: \"{meta['parent_moc']}\"")
    lines.append(f"  vizinhos: \"{meta['vizinhos']}\"")
    lines.append(f"status: {meta['status']}")
    lines.append(f"ultima_revisao: {TODAY}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Escreve as mudancas")
    args = ap.parse_args()

    root = Path(".")
    changed = 0

    for rel_path, meta in SPECS.items():
        p = root / rel_path
        if not p.exists():
            print(f"SKIP (nao existe): {rel_path}")
            continue

        content = p.read_text(encoding="utf-8")
        if FRONTMATTER_RE.match(content):
            print(f"SKIP (ja tem frontmatter): {rel_path}")
            continue

        fm = build_frontmatter(meta)
        new_content = fm + content
        changed += 1
        print(f"[{'APPLY' if args.apply else 'DRY  '}] {rel_path}")
        if args.apply:
            p.write_text(new_content, encoding="utf-8")

    print(f"\nFicheiros a modificar: {changed}")
    if not args.apply:
        print("Dry-run. Re-corre com --apply para escrever.")


if __name__ == "__main__":
    sys.exit(main())
