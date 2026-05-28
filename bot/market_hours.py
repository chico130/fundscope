"""Horário NYSE em UTC com ajuste automático ao DST americano.

Partilhado entre main.py (loop de mercado) e notifier.py (mensagens Telegram)
para garantir que a hora de fecho anunciada bate certo com a hora real em que
o bot pára. Tudo é UTC-aware — não depende do timezone do sistema.

NYSE: 09:30–16:00 ET, todos os dias úteis.
DST US: 2.º domingo de Março → 1.º domingo de Novembro.
  • EDT (UTC-4): 09:30 ET = 13:30 UTC, 16:00 ET = 20:00 UTC
  • EST (UTC-5): 09:30 ET = 14:30 UTC, 16:00 ET = 21:00 UTC

Damos 5 min de margem na abertura (evitar leilão de abertura) e fecho.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# (hora, minuto) UTC — margem de 5 min relativa ao sino real
_MARKET_OPEN_UTC_SUMMER  = (13, 35)   # 09:35 EDT
_MARKET_CLOSE_UTC_SUMMER = (19, 55)   # 15:55 EDT (fecho real 20:00 UTC)
_MARKET_OPEN_UTC_WINTER  = (14, 35)   # 09:35 EST
_MARKET_CLOSE_UTC_WINTER = (20, 55)   # 15:55 EST (fecho real 21:00 UTC)


def is_dst_us(now: datetime | None = None) -> bool:
    """True se os EUA estão em horário de verão (DST) no instante dado.

    Aproximação: 2.º domingo de Março às 07:00 UTC → 1.º domingo de Novembro
    às 06:00 UTC. Suficiente para o uso do bot (não somos uma exchange).
    """
    now = now or datetime.now(timezone.utc)
    year = now.year
    mar = datetime(year, 3, 8, 7, 0, tzinfo=timezone.utc)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(year, 11, 1, 6, 0, tzinfo=timezone.utc)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return dst_start <= now < dst_end


def market_hours_utc(now: datetime | None = None) -> tuple[tuple[int, int], tuple[int, int]]:
    """Devolve ((open_h, open_m), (close_h, close_m)) em UTC para o instante dado."""
    if is_dst_us(now):
        return _MARKET_OPEN_UTC_SUMMER, _MARKET_CLOSE_UTC_SUMMER
    return _MARKET_OPEN_UTC_WINTER, _MARKET_CLOSE_UTC_WINTER


def market_close_label_utc(now: datetime | None = None) -> str:
    """String 'HH:MM UTC' do fecho REAL do NYSE (sem a margem de 5 min do loop).

    Usado em mensagens Telegram — o utilizador espera o sino oficial, não a
    margem interna do bot.
    """
    _, (close_h, close_m) = market_hours_utc(now)
    real_close_h = close_h + 1 if close_m == 55 else close_h
    real_close_m = 0 if close_m == 55 else close_m
    return f"{real_close_h:02d}:{real_close_m:02d} UTC"


def market_open_label_utc(now: datetime | None = None) -> str:
    """String 'HH:MM UTC' da abertura REAL do NYSE."""
    (open_h, open_m), _ = market_hours_utc(now)
    real_open_h = open_h
    real_open_m = open_m - 5 if open_m >= 5 else open_m
    return f"{real_open_h:02d}:{real_open_m:02d} UTC"


def is_market_open(now: datetime | None = None) -> bool:
    """True se o mercado NYSE está aberto agora (sem feriados)."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False
    (open_h, open_m), (close_h, close_m) = market_hours_utc(now)
    open_time  = now.replace(hour=open_h,  minute=open_m,  second=0, microsecond=0)
    close_time = now.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
    return open_time <= now <= close_time


def seconds_until_next_open(now: datetime | None = None) -> int:
    """Segundos até à próxima abertura do mercado NYSE."""
    now = now or datetime.now(timezone.utc)
    (open_h, open_m), _ = market_hours_utc(now)
    candidate = now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return max(0, math.floor((candidate - now).total_seconds()))
