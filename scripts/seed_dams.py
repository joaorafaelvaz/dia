"""Seed initial 15 dams (Gerdau + Kinross) into the DIA database.

Idempotent: uses (owner_group, name) as the natural key. Re-running updates fields.

Run:
    docker compose exec api python -m scripts.seed_dams
or locally:
    python -m scripts.seed_dams
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import SessionLocal
from app.models.dam import Dam
from app.utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)


# 15 barragens mapeadas — 6 Gerdau (Miguel Burnier, Ouro Preto/MG)
# + 9 Kinross (Paracatu/MG + Caçu/GO).
# Coordenadas aproximadas do município quando a localização exata não é pública;
# revisar com o cliente antes de usar os valores de risco em produção.
DAMS: list[dict] = [
    # --- Gerdau ---
    {
        "name": "Barragem dos Alemães",
        "owner_group": "Gerdau",
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4400,
        "longitude": -43.6900,
        "anm_classification": "A",
        "cri": "Alta",
        "dpa": "Alto",
        "status": "active",
        "notes": "Miguel Burnier. Barragem de rejeitos.",
    },
    {
        "name": "Baias da UTM II",
        "owner_group": "Gerdau",
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4420,
        "longitude": -43.6920,
        "anm_classification": "B",
        "cri": "Média",
        "dpa": "Médio",
        "status": "active",
    },
    {
        "name": "Barragem Solo Mole",
        "owner_group": "Gerdau",
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4350,
        "longitude": -43.6850,
        "anm_classification": "B",
        "cri": "Alta",
        "dpa": "Médio",
        "status": "decharacterizing",
        "notes": "Em descaracterização.",
    },
    {
        "name": "Dique Norte da PDE 1",
        "owner_group": "Gerdau",
        "dam_type": "sediment",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4380,
        "longitude": -43.6880,
        "anm_classification": "C",
        "cri": "Baixa",
        "dpa": "Baixo",
        "status": "active",
    },
    {
        "name": "Barragem Olhos D'Água",
        "owner_group": "Gerdau",
        "dam_type": "tailings",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4410,
        "longitude": -43.6860,
        "anm_classification": "B",
        "cri": "Média",
        "dpa": "Médio",
        "status": "active",
    },
    {
        "name": "Dique de Contenção PDE Sul",
        "owner_group": "Gerdau",
        "dam_type": "sediment",
        "municipality": "Ouro Preto",
        "state": "MG",
        "latitude": -20.4390,
        "longitude": -43.6830,
        "anm_classification": "C",
        "cri": "Baixa",
        "dpa": "Baixo",
        "status": "active",
    },
    # --- Kinross (Paracatu, MG — Morro do Ouro) ---
    {
        "name": "Barragem Santo Antônio",
        "owner_group": "Kinross",
        "dam_type": "tailings",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2220,
        "longitude": -46.8740,
        "anm_classification": "A",
        "cri": "Alta",
        "dpa": "Alto",
        "status": "active",
        "notes": "Complexo Morro do Ouro.",
    },
    {
        "name": "Barragem Eustáquio",
        "owner_group": "Kinross",
        "dam_type": "tailings",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2250,
        "longitude": -46.8770,
        "anm_classification": "A",
        "cri": "Alta",
        "dpa": "Alto",
        "status": "active",
    },
    {
        "name": "Tanque Específico XII",
        "owner_group": "Kinross",
        "dam_type": "tailings",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2200,
        "longitude": -46.8700,
        "anm_classification": "B",
        "cri": "Média",
        "dpa": "Médio",
        "status": "active",
    },
    {
        "name": "Barragem Água Clara",
        "owner_group": "Kinross",
        "dam_type": "tailings",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2180,
        "longitude": -46.8680,
        "anm_classification": "B",
        "cri": "Média",
        "dpa": "Médio",
        "status": "active",
    },
    {
        "name": "Dique de Rejeitos B4",
        "owner_group": "Kinross",
        "dam_type": "tailings",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2230,
        "longitude": -46.8750,
        "anm_classification": "C",
        "cri": "Baixa",
        "dpa": "Baixo",
        "status": "active",
    },
    {
        "name": "Barragem de Sedimentos L2",
        "owner_group": "Kinross",
        "dam_type": "sediment",
        "municipality": "Paracatu",
        "state": "MG",
        "latitude": -17.2210,
        "longitude": -46.8720,
        "anm_classification": "C",
        "cri": "Baixa",
        "dpa": "Baixo",
        "status": "active",
    },
    # --- Kinross (Goiás — UHEs Paranaíba) ---
    {
        "name": "UHE Caçu",
        "owner_group": "Kinross",
        "dam_type": "hydropower",
        "municipality": "Caçu",
        "state": "GO",
        "latitude": -18.5570,
        "longitude": -51.1180,
        "anm_classification": None,
        "cri": "Média",
        "dpa": "Alto",
        "status": "active",
        "notes": "Usina hidrelétrica — bacia do Paranaíba.",
    },
    {
        "name": "UHE Barra dos Coqueiros",
        "owner_group": "Kinross",
        "dam_type": "hydropower",
        "municipality": "Cachoeira Alta",
        "state": "GO",
        "latitude": -18.7710,
        "longitude": -50.9890,
        "anm_classification": None,
        "cri": "Média",
        "dpa": "Alto",
        "status": "active",
    },
    {
        "name": "Reservatório de Regularização Paranaíba",
        "owner_group": "Kinross",
        "dam_type": "flood_control",
        "municipality": "Itumbiara",
        "state": "GO",
        "latitude": -18.4190,
        "longitude": -49.2150,
        "anm_classification": None,
        "cri": "Baixa",
        "dpa": "Médio",
        "status": "active",
    },
]


async def seed() -> None:
    async with SessionLocal() as session:
        created = 0
        updated = 0
        for payload in DAMS:
            stmt = select(Dam).where(
                Dam.owner_group == payload["owner_group"],
                Dam.name == payload["name"],
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing:
                for key, value in payload.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                session.add(Dam(**payload, is_active=True))
                created += 1
        await session.commit()
        log.info("seed_complete", created=created, updated=updated, total=len(DAMS))
        print(f"Seed done — created={created} updated={updated} total={len(DAMS)}")


if __name__ == "__main__":
    asyncio.run(seed())
