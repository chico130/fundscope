"""Social Crawler do FundScope.

Pipeline determinístico (zero tokens) que extrai sentimento de mercado de
fontes leves (analistas Finnhub, Reddit) e escreve um agregado numérico em
``data/beta/social_sentiment.json`` para a Bonnie consumir passivamente.

Entrypoint: ``python -m crawler.runner``
"""

__version__ = "0.1.0"
