"""Smoke test offline do ANA Hidrowebservice client.

Valida a lógica de parsing e geolocalização em `app/services/climate/ana.py`
contra fixtures capturados da API real (commitados em `scripts/fixtures/ana/`).

Não precisa de Redis, Docker, credenciais nem httpx — roda com Python stdlib.

Fixtures:
  - ana_mg_sample.json             — 111 estações MG (primeiras 30 + todas
                                     num raio de 20 km de Ouro Preto).
                                     Amostra de HidroInventarioEstacoes?UF=MG.
  - chuva_station_1942008_2024.json — 12 itens mensais com breakdown diário
                                     Chuva_01..Chuva_31 pra 2024 completo.

Para re-capturar:
  curl -H "Authorization: Bearer <JWT>" \\
    "https://www.ana.gov.br/hidrowebservice/EstacoesTelemetricas/HidroInventarioEstacoes/v1?Unidade%20Federativa=MG"

Uso: `python scripts/smoke_ana.py` — exit 0 se todos os testes passam.
"""
from __future__ import annotations

import calendar
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "ana"


# ---------------------------------------------------------------------------
# Cópias leves do parser em app/services/climate/ana.py.
# Duplicação intencional: o smoke valida a forma dos dados sem carregar
# toda a stack (httpx, redis, pydantic-settings, geopy).
# ---------------------------------------------------------------------------


@dataclass
class DailyForecast:
    date: date
    precipitation_mm: float = 0.0


def parse_rainfall_month(raw: dict) -> list[DailyForecast]:
    dth = raw.get("Data_Hora_Dado")
    if not dth:
        return []
    try:
        month_start = date.fromisoformat(str(dth)[:10])
    except ValueError:
        return []
    year, month = month_start.year, month_start.month
    days_in_month = calendar.monthrange(year, month)[1]
    out: list[DailyForecast] = []
    for day in range(1, days_in_month + 1):
        key = f"Chuva_{day:02d}"
        v = raw.get(key)
        if v is None or v == "":
            continue
        try:
            mm = float(str(v).replace(",", "."))
        except ValueError:
            continue
        out.append(DailyForecast(date=date(year, month, day), precipitation_mm=mm))
    return out


def parse_station(raw: dict) -> dict | None:
    code_raw = raw.get("codigoestacao")
    try:
        lat = float(raw["Latitude"])
        lon = float(raw["Longitude"])
        code = int(str(code_raw).strip())
    except (TypeError, ValueError, KeyError):
        return None
    return {
        "code": code,
        "name": str(raw.get("Estacao_Nome") or "").strip(),
        "state": str(raw.get("UF_Estacao") or "").strip().upper(),
        "municipality": str(raw.get("Municipio_Nome") or "").strip(),
        "lat": lat,
        "lon": lon,
        "is_pluvio": str(raw.get("Tipo_Estacao_Pluviometro") or "0") == "1",
        "is_operating": str(raw.get("Operando") or "0") == "1",
    }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    la1, lo1, la2, lo2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * R * asin(sqrt(h))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_inventario() -> None:
    print("=" * 60)
    print("TEST 1: parse_station on ana_mg_sample.json")
    print("=" * 60)
    payload = json.loads((FIXTURES / "ana_mg_sample.json").read_text(encoding="utf-8"))
    items = payload["items"]
    total = 0
    pluvio_active = 0
    bad = 0
    for it in items:
        s = parse_station(it)
        if s is None:
            bad += 1
            continue
        total += 1
        if s["is_pluvio"] and s["is_operating"]:
            pluvio_active += 1
    print(f"  raw items:         {len(items)}")
    print(f"  parsed OK:         {total}")
    print(f"  parse failures:    {bad}")
    print(f"  pluvio + operando: {pluvio_active}")
    assert total == len(items), f"expected zero parse failures on curated fixture, got {bad}"
    assert pluvio_active > 10, f"expected >10 active pluvio in 20 km radius sample, got {pluvio_active}"
    print("  PASS")


def test_rainfall() -> None:
    print()
    print("=" * 60)
    print("TEST 2: parse_rainfall_month on chuva_station_1942008_2024.json")
    print("=" * 60)
    payload = json.loads(
        (FIXTURES / "chuva_station_1942008_2024.json").read_text(encoding="utf-8")
    )
    items = payload["items"]
    print(f"  monthly items: {len(items)}")

    by_date: dict[date, tuple[int, DailyForecast]] = {}
    for it in items:
        cons = int(str(it.get("Nivel_Consistencia") or "0") or 0)
        for d in parse_rainfall_month(it):
            prev = by_date.get(d.date)
            if prev is None or cons > prev[0]:
                by_date[d.date] = (cons, d)
    daily = sorted(by_date.values(), key=lambda p: p[1].date)
    print(f"  daily rows (deduped): {len(daily)}")
    first_date = daily[0][1].date
    last_date = daily[-1][1].date
    print(f"  first day: {first_date}  last day: {last_date}")

    by_month: dict[tuple[int, int], float] = defaultdict(float)
    for _, d in daily:
        by_month[(d.date.year, d.date.month)] += d.precipitation_mm
    months = sorted(by_month.keys())
    print(f"  months covered: {months[:3]}...{months[-3:]}")

    jan_sum = by_month[(2024, 1)]
    expected_jan = float(str(items[0]["Total"]).replace(",", "."))
    print(f"  Jan 2024 our sum = {jan_sum:.1f}  vs API Total = {expected_jan:.1f}")
    assert abs(jan_sum - expected_jan) < 0.5, (
        f"Jan 2024 sum mismatch: {jan_sum} vs {expected_jan}"
    )

    jan_days = [d for _, d in daily if d.date.year == 2024 and d.date.month == 1]
    jan_max_day = max(jan_days, key=lambda d: d.precipitation_mm)
    print(f"  Jan 2024 max = {jan_max_day.precipitation_mm} mm on day {jan_max_day.date.day}")
    assert jan_max_day.precipitation_mm == 98.2, f"got {jan_max_day.precipitation_mm}"
    assert jan_max_day.date.day == 24, f"got day {jan_max_day.date.day}"
    print("  PASS")


def test_nearest() -> None:
    print()
    print("=" * 60)
    print("TEST 3: nearest pluvio to Ouro Preto")
    print("=" * 60)
    payload = json.loads((FIXTURES / "ana_mg_sample.json").read_text(encoding="utf-8"))
    candidates = []
    for it in payload["items"]:
        s = parse_station(it)
        if s and s["is_pluvio"] and s["is_operating"]:
            candidates.append(s)
    print(f"  candidates: {len(candidates)}")
    tlat, tlon = -20.3855, -43.5036
    best = min(candidates, key=lambda s: haversine_km(tlat, tlon, s["lat"], s["lon"]))
    best_km = haversine_km(tlat, tlon, best["lat"], best["lon"])
    print(f"  nearest: code={best['code']}  name={best['name']!r}  muni={best['municipality']!r}")
    print(f"           lat={best['lat']} lon={best['lon']}  dist={best_km:.2f} km")
    assert best_km < 5, f"expected <5 km, got {best_km}"
    assert best["code"] == 2043049, f"expected Ouro Preto station 2043049, got {best['code']}"
    print("  PASS")


if __name__ == "__main__":
    test_inventario()
    test_rainfall()
    test_nearest()
    print()
    print("ALL SMOKE TESTS PASSED")
