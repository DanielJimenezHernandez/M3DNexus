"""Cobro con terminal Mercado Pago Point (API de integraciones presenciales).

Flujo: se crea una "intención de pago" asignada a la terminal (el monto
aparece en su pantalla), el front hace poll del estado y la venta solo se
registra cuando el pago queda aprobado. La terminal tiene que estar en modo
PDV (en modo STANDALONE ignora las intenciones y solo cobra lo que se teclee
en ella); el modo se cambia por API y se controla desde Ajustes.

Contrato (docs "Integrar vía API para Punto de Venta" de MP, nov 2026):
  - El monto va en centavos SIN decimales: $150.00 → 15000.
  - Estados de la intención: OPEN → ON_TERMINAL → PROCESSING → FINISHED,
    o CANCELED / ABANDONED / ERROR. FINISHED no implica aprobado: hay que
    leer el pago (/v1/payments/{id}) y exigir status == "approved".
  - DELETE de la intención solo mientras siga OPEN (si ya está en la
    terminal, se cancela en la terminal misma).
"""

from __future__ import annotations

import httpx

API = "https://api.mercadopago.com"
TIMEOUT = 15.0


class MPointError(Exception):
    """Error de la API de MP con mensaje ya apto para mostrar en la UI."""


def _request(token: str, method: str, path: str, json: dict | None = None) -> dict:
    try:
        r = httpx.request(
            method,
            API + path,
            json=json,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise MPointError(f"No se pudo contactar a Mercado Pago: {exc}") from exc
    if r.status_code == 401:
        raise MPointError("Mercado Pago rechazó el token (401): revísalo en Ajustes")
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            body = {}
        detail = body.get("message") or body.get("error") or r.text[:200]
        raise MPointError(f"Mercado Pago respondió {r.status_code}: {detail}")
    try:
        return r.json() if r.text else {}
    except ValueError:
        return {}


def list_devices(token: str) -> list[dict]:
    """Terminales Point de la cuenta: id, modelo y modo de operación."""
    data = _request(token, "GET", "/point/integration-api/devices?limit=50")
    return [
        {
            "id": d.get("id"),
            "pos_id": d.get("pos_id"),
            "operating_mode": d.get("operating_mode"),
        }
        for d in data.get("devices") or []
    ]


def set_operating_mode(token: str, device_id: str, mode: str) -> dict:
    """PDV = acepta cobros mandados por API; STANDALONE = solo teclado propio."""
    return _request(
        token, "PATCH", f"/point/integration-api/devices/{device_id}",
        {"operating_mode": mode},
    )


def create_intent(token: str, device_id: str, amount_cents: int,
                  reference: str, print_receipt: bool) -> dict:
    return _request(
        token, "POST", f"/point/integration-api/devices/{device_id}/payment-intents",
        {
            "amount": amount_cents,
            "additional_info": {
                "external_reference": reference,
                "print_on_terminal": print_receipt,
            },
        },
    )


def get_intent(token: str, intent_id: str) -> dict:
    return _request(token, "GET", f"/point/integration-api/payment-intents/{intent_id}")


def cancel_intent(token: str, device_id: str, intent_id: str) -> dict:
    return _request(
        token, "DELETE",
        f"/point/integration-api/devices/{device_id}/payment-intents/{intent_id}",
    )


def get_payment(token: str, payment_id: str) -> dict:
    return _request(token, "GET", f"/v1/payments/{payment_id}")
