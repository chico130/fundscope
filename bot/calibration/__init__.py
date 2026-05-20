"""
bot/calibration — Motor de calibração offline de parâmetros do Clyde.

Varre filtros técnicos (RSI, EMA50, vol_ratio) contra 2–4 anos do S&P 500
para descobrir o sweet spot de Profit Factor / Win Rate.

Zero alterações ao caminho quente de produção (price_feed, phase0).
"""
