#!/usr/bin/env python3
"""Escucha sismos y avisa a n8n en cuanto un catálogo los publica.

EMSC empuja cada evento por websocket. USGS se consulta cada minuto por si
EMSC no lo publicó. No es alerta temprana: el mensaje sale cuando la red ya
calculó el sismo, no cuando empieza a temblar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets

EMSC_WS = "wss://www.seismicportal.eu/standing_order/websocket"
USGS_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_hour.geojson"
BOGOTA = ZoneInfo("America/Bogota")
S_WAVE_KM_S = 3.5

# Caja aproximada de Colombia continental.
COLOMBIA = (-4.3, 13.5, -79.1, -66.8)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("sismos")


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


HOME_LAT = env_float("HOME_LAT", 6.2442)
HOME_LON = env_float("HOME_LON", -75.5812)
RADIUS_KM = env_float("RADIUS_KM", 300)
MIN_MAG = env_float("MIN_MAG", 4.5)
MAG_DELTA = env_float("MAG_UPDATE_DELTA", 0.3)
WEBHOOK_URL = os.environ.get(
    "N8N_WEBHOOK_URL",
    "https://n8n.capitalimpulso.com/webhook/sismo-alerta",
)
ALERT_TOKEN = os.environ.get("ALERT_TOKEN", "CAMBIA-ESTE-TOKEN")
STATE_PATH = Path(os.environ.get("STATE_PATH", "/data/seen.json"))
USGS_EVERY_SECONDS = int(os.environ.get("USGS_EVERY_SECONDS", "60"))
WHATSAPP_NUMBERS = [
    number.strip().lstrip("+")
    for number in os.environ.get("WHATSAPP_NUMBERS", "573192754132").split(",")
    if number.strip()
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def in_colombia(lat: float, lon: float) -> bool:
    south, north, west, east = COLOMBIA
    return south <= lat <= north and west <= lon <= east


def decide(lat: float, lon: float, mag: float) -> tuple[bool, float]:
    distance = haversine_km(lat, lon, HOME_LAT, HOME_LON)
    if mag < MIN_MAG:
        return False, distance
    if distance <= RADIUS_KM or in_colombia(lat, lon):
        return True, distance
    return False, distance


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"by_id": {}, "recent": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def already_sent(state: dict, event_id: str, lat: float, lon: float, when: datetime, mag: float) -> str | None:
    """Devuelve None si hay que avisar, o el motivo para no hacerlo."""
    previous = state["by_id"].get(event_id)
    if previous is not None:
        if mag >= float(previous["mag"]) + MAG_DELTA:
            return None
        return "mismo evento sin cambio de magnitud"

    when_epoch = when.timestamp()
    for item in state["recent"]:
        age = abs(when_epoch - float(item["time"]))
        near = haversine_km(lat, lon, float(item["lat"]), float(item["lon"])) <= 40
        similar = abs(mag - float(item["mag"])) < 0.6
        if age <= 180 and near and similar:
            return "ya avisado por la otra fuente"
    return None


def remember(state: dict, event_id: str, lat: float, lon: float, when: datetime, mag: float) -> None:
    state["by_id"][event_id] = {"mag": mag}
    state["recent"].append(
        {"lat": lat, "lon": lon, "time": when.timestamp(), "mag": mag}
    )
    cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
    state["recent"] = [item for item in state["recent"] if float(item["time"]) >= cutoff]
    if len(state["by_id"]) > 500:
        state["by_id"] = dict(list(state["by_id"].items())[-500:])


def message_for(event: dict, distance: float, correction: bool) -> str:
    when = event["time"].astimezone(BOGOTA)
    age = max(0, (datetime.now(timezone.utc) - event["time"]).total_seconds())
    travel = distance / S_WAVE_KM_S
    remaining = travel - age
    if remaining > 20:
        wave = (
            f"La onda fuerte puede tardar cerca de {int(remaining)} s en llegar a Medellín. "
            "Este aviso sale cuando la red publica el sismo, no es la alerta del celular."
        )
    else:
        wave = (
            "Por la distancia, en Medellín ya debió sentirse o será leve. "
            "Este aviso confirma el registro, no adelanta la onda."
        )
    title = "Corrección de sismo" if correction else "Sismo"
    depth = event.get("depth_km")
    depth_text = f"{depth:.0f} km" if isinstance(depth, (int, float)) else "sin dato"
    return (
        f"{title} {event['mag']:.1f} en {event['region']}\n"
        f"A {distance:.0f} km de Medellín. Profundidad: {depth_text}\n"
        f"{when.strftime('%d/%m/%Y %H:%M')} hora de Bogotá\n"
        f"{wave}\n"
        f"Fuente: {event['source']}"
    )


def post_alert(text: str, number: str) -> None:
    payload = json.dumps({"text": text, "number": number}).encode()
    request = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Alert-Token": ALERT_TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def consider(state: dict, event: dict) -> None:
    mag = event.get("mag")
    lat = event.get("lat")
    lon = event.get("lon")
    when = event.get("time")
    if not isinstance(mag, (int, float)) or lat is None or lon is None or when is None:
        return
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
        event["time"] = when

    allowed, distance = decide(float(lat), float(lon), float(mag))
    if not allowed:
        return

    event_id = f"{event['source']}:{event['id']}"
    reason = already_sent(state, event_id, float(lat), float(lon), when, float(mag))
    if reason:
        log.info("omitido %s (%s)", event_id, reason)
        return

    previous = state["by_id"].get(event_id)
    correction = previous is not None
    if not WHATSAPP_NUMBERS:
        log.error("WHATSAPP_NUMBERS está vacío. No hay a quién avisar.")
        return

    text = message_for(event, distance, correction)
    try:
        for number in WHATSAPP_NUMBERS:
            post_alert(text, number)
    except urllib.error.URLError as exc:
        log.error("n8n no aceptó el aviso de %s: %s", event_id, exc)
        return

    remember(state, event_id, float(lat), float(lon), when, float(mag))
    save_state(state)
    log.info(
        "aviso enviado %s mag %.1f a %.0f km, %s números",
        event_id,
        mag,
        distance,
        len(WHATSAPP_NUMBERS),
    )


def parse_emsc(raw: str) -> dict | None:
    data = json.loads(raw)
    feature = data.get("data") or {}
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    lon = coords[0] if len(coords) > 0 else props.get("lon")
    lat = coords[1] if len(coords) > 1 else props.get("lat")
    depth = coords[2] if len(coords) > 2 else props.get("depth")
    when_text = props.get("time")
    if not when_text:
        return None
    when = datetime.fromisoformat(when_text.replace("Z", "+00:00"))
    return {
        "source": "EMSC",
        "id": str(props.get("unid") or props.get("source_id") or when_text),
        "mag": props.get("mag"),
        "lat": lat,
        "lon": lon,
        "depth_km": depth,
        "region": props.get("flynn_region") or "región sin nombre",
        "time": when,
    }


def parse_usgs_feature(feature: dict) -> dict | None:
    props = feature.get("properties") or {}
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coords) < 2 or props.get("time") is None:
        return None
    when = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
    return {
        "source": "USGS",
        "id": str(feature.get("id")),
        "mag": props.get("mag"),
        "lat": coords[1],
        "lon": coords[0],
        "depth_km": coords[2] if len(coords) > 2 else None,
        "region": props.get("place") or "región sin nombre",
        "time": when,
    }


async def listen_emsc(state: dict) -> None:
    delay = 2
    while True:
        try:
            log.info("conectando al websocket de EMSC")
            async with websockets.connect(EMSC_WS, ping_interval=15, open_timeout=20) as socket:
                delay = 2
                log.info("EMSC conectado")
                async for raw in socket:
                    try:
                        event = parse_emsc(raw)
                    except (json.JSONDecodeError, KeyError, ValueError) as exc:
                        log.warning("mensaje EMSC ilegible: %s", exc)
                        continue
                    if event:
                        consider(state, event)
        except Exception as exc:
            log.warning("websocket EMSC cerrado (%s). Reintento en %ss", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def poll_usgs(state: dict) -> None:
    while True:
        try:
            request = urllib.request.Request(USGS_FEED, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode())
            for feature in payload.get("features") or []:
                event = parse_usgs_feature(feature)
                if event:
                    consider(state, event)
        except Exception as exc:
            log.warning("feed USGS falló: %s", exc)
        await asyncio.sleep(USGS_EVERY_SECONDS)


async def main() -> None:
    if ALERT_TOKEN == "CAMBIA-ESTE-TOKEN":
        log.warning("ALERT_TOKEN sigue en el valor de ejemplo. Cámbialo junto con el workflow.")
    state = load_state()
    await asyncio.gather(listen_emsc(state), poll_usgs(state))


if __name__ == "__main__":
    asyncio.run(main())
