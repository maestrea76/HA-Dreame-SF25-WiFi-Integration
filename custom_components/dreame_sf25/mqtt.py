"""Cliente MQTT para recibir cambios de propiedad en tiempo real (push).

El SF25 publica sus cambios en el broker de Dreame (bindDomain del dispositivo),
que es el mismo canal que usa la app. Esto evita esperar al sondeo: la tapa, el
estado o el programa se reflejan al instante.

Formato del mensaje:
    {"data": {"method": "properties_changed",
              "params": [{"siid": 6, "piid": 26, "value": 1}, ...]}}

El cliente corre en su propio hilo (paho); los cambios se entregan mediante un
callback que el coordinator marshalea al loop de Home Assistant.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import ssl
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from .api import DreameSF25Client
from .const import MQTT_CERT_SHA256

_LOGGER = logging.getLogger(__name__)

# rc=5 -> no autorizado (token caducado): reconectamos con credenciales frescas
_RC_NOT_AUTHORIZED = 5


def _verify_pin(ssock: ssl.SSLSocket, fingerprint: str) -> None:
    """Comprueba la huella una vez el handshake ha terminado."""
    der = ssock.getpeercert(binary_form=True) or b""
    got = hashlib.sha256(der).hexdigest()
    if got != fingerprint:
        try:
            ssock.close()
        except Exception:  # noqa: BLE001
            pass
        raise ssl.SSLCertVerificationError(
            "El certificado del broker MQTT no coincide con el esperado "
            f"(huella {got}). No se envian credenciales."
        )


class _PinnedSSLContext(ssl.SSLContext):
    """Contexto TLS que exige una huella concreta del certificado del servidor.

    El broker de Dreame usa un CA privado que OpenSSL no puede validar (ver
    MQTT_CERT_SHA256 en const.py). En lugar de aceptar cualquier certificado,
    se comprueba la huella justo despues del handshake y ANTES de que paho
    envie el CONNECT con el usuario y el token.

    paho 1.6+ llama wrap_socket(..., do_handshake_on_connect=False) y hace el
    handshake despues: si se anclara la huella dentro de wrap_socket, el
    certificado aun no existe, el pin falla siempre y el push MQTT nunca
    arranca (la tapa y el resto caen al sondeo de 30 s).
    """

    fingerprint: str = ""

    def wrap_socket(self, sock, *args, **kwargs):  # type: ignore[override]
        do_handshake = kwargs.get("do_handshake_on_connect", True)
        ssock = super().wrap_socket(sock, *args, **kwargs)
        if do_handshake:
            _verify_pin(ssock, self.fingerprint)
            return ssock

        orig = ssock.do_handshake

        def do_handshake_and_pin(*a, **k):
            orig(*a, **k)
            _verify_pin(ssock, self.fingerprint)

        ssock.do_handshake = do_handshake_and_pin  # type: ignore[method-assign]
        return ssock


def _pinned_context() -> _PinnedSSLContext:
    ctx = _PinnedSSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # La cadena no es verificable (CA privado sin AKI); la garantia la da el pin.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.fingerprint = MQTT_CERT_SHA256
    return ctx


def _random_agent_id() -> str:
    return "".join(random.choice("ABCDEF") for _ in range(13))


class DreameSF25Mqtt:
    """Suscripcion al topic de estado del dispositivo."""

    def __init__(
        self,
        client: DreameSF25Client,
        on_properties: Callable[[dict[tuple[int, int], Any]], None],
    ) -> None:
        self._api = client
        self._on_properties = on_properties
        self._client: mqtt.Client | None = None
        self.connected = False

    @property
    def _topic(self) -> str:
        return (
            f"/status/{self._api.did}/{self._api.master_uid}/"
            f"{self._api.model}/{self._api.region}/"
        )

    def start(self) -> None:
        """Conecta y arranca el bucle en segundo plano (llamar en executor)."""
        host, _, port = (self._api.bind_host or "").partition(":")
        if (
            not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.iot\.dreame\.tech", host)
            or not port.isdecimal()
            or not 1 <= int(port) <= 65535
        ):
            raise ValueError("Invalid Dreame MQTT endpoint")

        username, password = self._api.mqtt_credentials()
        client_id = f"p_{self._api.master_uid}_{_random_agent_id()}_{host}"

        # VERSION1: el rc de CONNACK/disconnect es el entero MQTT 3.1.1
        # (5 = no autorizado). VERSION2 lo traduce a reason codes MQTT 5
        # (135) y el refresco de token deja de dispararse.
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1, client_id, clean_session=True
            )
        else:  # paho 1.x
            client = mqtt.Client(client_id, clean_session=True)

        client.username_pw_set(username, password)
        client.tls_set_context(_pinned_context())
        client.reconnect_delay_set(1, 60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # connect_async + loop_start: si la nube no responde al arrancar, el
        # hilo de paho reintenta solo (con connect() la excepcion mataba el push
        # hasta recargar la integracion)
        client.connect_async(host, int(port), keepalive=60)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        """Cierra la conexion."""
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
        self.connected = False

    # --------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        code = int(rc) if not isinstance(rc, int) else rc
        if code == 0:
            self.connected = True
            client.subscribe(self._topic)
            _LOGGER.info("MQTT conectado; suscrito a %s", self._topic)
        else:
            self.connected = False
            _LOGGER.warning("MQTT rechazo la conexion (rc=%s)", code)

    def _on_disconnect(self, client, userdata, *args) -> None:
        # paho 1.x: (rc,) · paho 2.x: (disconnect_flags, reason_code, properties)
        self.connected = False
        rc = args[0] if len(args) == 1 else (args[1] if len(args) > 1 else 0)
        code = int(rc) if not isinstance(rc, int) else rc
        _LOGGER.info("MQTT desconectado (rc=%s); paho reintentara", code)
        if code == _RC_NOT_AUTHORIZED:
            # token caducado: renovamos credenciales para el proximo intento
            try:
                username, password = self._api.mqtt_credentials()
                client.username_pw_set(username, password)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("No se pudieron renovar credenciales MQTT: %s", err)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return

        data = payload.get("data", payload)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return
        if not isinstance(data, dict) or data.get("method") != "properties_changed":
            return

        updates: dict[tuple[int, int], Any] = {}
        for param in data.get("params") or []:
            if not isinstance(param, dict):
                continue
            siid, piid = param.get("siid"), param.get("piid")
            if siid is None or piid is None or "value" not in param:
                continue
            try:
                key = (int(siid), int(piid))
            except (TypeError, ValueError):
                continue
            if param.get("code", 0) == 0:
                updates[key] = param["value"]

        if updates:
            self._on_properties(updates)
