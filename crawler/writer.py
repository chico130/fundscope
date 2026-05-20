"""Escrita atómica do agregado de sentimento.

Escreve para um ficheiro temporário e faz ``os.replace`` (atómico no mesmo
filesystem) para que a Bonnie nunca leia um JSON meio-escrito.
"""

from __future__ import annotations

import json
import os
import pathlib

# crawler/writer.py -> parents[1] == raiz do repo (fundscope/)
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "data" / "beta" / "social_sentiment.json"


def write_sentiment(payload: dict, path: pathlib.Path = OUTPUT_PATH) -> pathlib.Path:
    """Serializa ``payload`` e escreve atomicamente. Devolve o caminho final."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)  # rename atómico
    return path
