"""Cliente (sincrono, solo stdlib) para la nube de Dreame — dispositivo SF25.

Ingenieria inversa del protocolo de la app Dreamehome. Se usa desde el
coordinator mediante hass.async_add_executor_job para no bloquear el loop.

Lectura/escritura de estado via RPC MIoT (nube -> dispositivo) 'sendCommand',
porque este MCU (dreame.fwd.u2527) no cachea propiedades en la nube.
"""
from __future__ import annotations

import hashlib
import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .const import (
    ACTION_WAKE,
    BASIC_AUTH,
    DEFAULT_TENANT,
    PORT,
    PROP_CHILD_LOCK,
    PROP_STATUS,
    PWD_SALT,
    TARGET_MODEL,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Tras el code 1 el aparato puede tardar mas de un segundo en salir de
# suspension, y la RPC de despertar a menudo caduca aunque el cambio llegue.
WAKE_WAIT_SECONDS = 12.0
WAKE_POLL_INTERVAL = 2.0
_SUSPENDED_STATUS = 3

# Verificacion TLS completa: los seis endpoints regionales presentan un
# certificado publicamente valido (CN=*.iot.dreame.tech, DigiCert). Por aqui
# viajan las credenciales de la cuenta, asi que no se relaja la comprobacion.
_SSL_CTX = ssl.create_default_context()


class DreameAuthError(Exception):
    """Fallo de autenticacion (credenciales/region)."""


class DreameApiError(Exception):
    """Fallo generico de la API."""


class DreameSF25Client:
    """Cliente minimo para el SF25 en la nube de Dreame."""

    def __init__(self, email: str, password: str, region: str = "eu") -> None:
        self._email = email
        self._password = password
        self._region = region
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._tenant_id: str = DEFAULT_TENANT
        self._token_expire: float = 0.0
        self._rpc_id: int = 1
        self._uid: str | None = None          # uid de la cuenta (login)
        # rellenados tras localizar el dispositivo:
        self.did: str | None = None
        self.bind_host: str | None = None
        self.model: str | None = None
        self.master_uid: str | None = None    # masterUid del dispositivo (topic MQTT)

    # ------------------------------------------------------------ Propiedades
    @property
    def uid(self) -> str | None:
        """uid de la cuenta (usuario MQTT)."""
        return self._uid

    @property
    def region(self) -> str:
        return self._region

    def mqtt_credentials(self) -> tuple[str, str]:
        """(usuario, contrasena) validos para el broker MQTT.

        Refresca el token si hiciera falta, porque el broker lo rechaza caducado.
        """
        self._ensure_token()
        return self._uid or "", self._access_token or ""

    # ------------------------------------------------------------------ HTTP
    def _base_url(self) -> str:
        return f"https://{self._region}.iot.dreame.tech:{PORT}"

    def _post(self, path: str, headers: dict, data: bytes, timeout: int = 15) -> dict:
        url = f"{self._base_url()}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            if e.code == 401:
                raise DreameAuthError("Authentication rejected (HTTP 401)") from e
            raise DreameApiError(f"HTTP {e.code}: {raw[:200]}") from e
        except Exception as e:  # noqa: BLE001
            raise DreameApiError(str(e)) from e
        try:
            return json.loads(raw)
        except ValueError as e:
            raise DreameApiError(f"Respuesta no-JSON: {raw[:200]}") from e

    def _auth_headers(self) -> dict:
        return {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": BASIC_AUTH,
            "Tenant-Id": self._tenant_id or DEFAULT_TENANT,
            "Dreame-Auth": self._access_token or "",
        }

    def _api(self, path: str, params: Any | None = None) -> dict:
        data = json.dumps(params, separators=(",", ":")).encode("utf-8") if params is not None else b""
        try:
            return self._post(path, self._auth_headers(), data)
        except DreameAuthError:
            # Token rechazado antes de su caducidad (sesion cerrada desde la app,
            # rotacion en la nube...): _ensure_token solo mira el reloj, asi que
            # hacemos login de nuevo y reintentamos UNA vez. Si el login falla,
            # la contrasena ya no vale y el error sube como DreameAuthError.
            _LOGGER.debug("401 en %s; nuevo login y reintento", path)
            self.login()
            return self._post(path, self._auth_headers(), data)

    # ------------------------------------------------------------------ Auth
    def login(self) -> None:
        """Login por usuario/contrasena (grant password)."""
        pwd_hash = hashlib.md5((self._password + PWD_SALT).encode("utf-8")).hexdigest()
        body = urllib.parse.urlencode(
            {
                "platform": "IOS",
                "scope": "all",
                "grant_type": "password",
                "username": self._email,
                "password": pwd_hash,
                "type": "account",
            }
        )
        self._do_token(body)

    def _refresh(self) -> None:
        body = urllib.parse.urlencode(
            {
                "platform": "IOS",
                "scope": "all",
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token or "",
            }
        )
        self._do_token(body)

    def _do_token(self, body: str) -> None:
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Authorization": BASIC_AUTH,
            "Tenant-Id": DEFAULT_TENANT,
        }
        data = self._post("/dreame-auth/oauth/token", headers, body.encode("utf-8"))
        if "access_token" not in data:
            raise DreameAuthError("Dreame did not return an access token")
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        self._tenant_id = data.get("tenant_id", DEFAULT_TENANT)
        self._uid = data.get("uid", self._uid)
        self._token_expire = time.time() + int(data.get("expires_in", 3600)) - 120

    def _ensure_token(self) -> None:
        if not self._access_token:
            self.login()
        elif time.time() > self._token_expire:
            try:
                self._refresh()
            except DreameAuthError:
                self.login()

    # -------------------------------------------------------------- Devices
    def list_devices(self) -> list[dict]:
        self._ensure_token()
        resp = self._api("/dreame-user-iot/iotuserbind/device/listV2")
        if resp.get("code") == 0 and "data" in resp:
            return list(resp["data"].get("page", {}).get("records", []))
        raise DreameApiError(f"listV2: {json.dumps(resp)[:200]}")

    def resolve_device(self, did: str | None = None) -> dict:
        """Localiza el SF25 (o el did dado) y cachea did/bind_host/model."""
        for d in self.list_devices():
            model = d.get("model") or ""
            supported = model == TARGET_MODEL or model.endswith(".u2527")
            match = supported and (
                did is None or str(d.get("did")) == str(did)
            )
            if match:
                self.did = str(d.get("did"))
                self.bind_host = d.get("bindDomain")
                self.model = model
                self.master_uid = d.get("masterUid")
                return d
        raise DreameApiError("No se encontro el dispositivo SF25 en la cuenta")

    # ------------------------------------------------------------------ RPC
    def _rpc_path(self) -> str:
        shard = self.bind_host.split(".")[0] if self.bind_host else ""
        if shard and not shard.isdecimal():
            raise DreameApiError("Invalid Dreame cloud shard")
        host = f"-{shard}" if shard else ""
        return f"/dreame-iot-com{host}/device/sendCommand"

    def _send_rpc(self, method: str, params: Any, *, allow_empty_success: bool = False) -> Any:
        self._ensure_token()
        self._rpc_id += 1
        body = {
            "did": str(self.did),
            "id": self._rpc_id,
            "data": {"did": str(self.did), "id": self._rpc_id, "method": method, "params": params},
        }
        resp = self._api(self._rpc_path(), body)
        if resp and isinstance(resp.get("data"), dict) and "result" in resp["data"]:
            return resp["data"]["result"]
        if allow_empty_success and resp and resp.get("success") is True:
            return None
        # 'success' sin data -> reintento unico
        if resp and resp.get("success") is True:
            resp = self._api(self._rpc_path(), body)
            if resp and isinstance(resp.get("data"), dict):
                return resp["data"].get("result")
        raise DreameApiError(f"RPC {method} sin resultado: {json.dumps(resp)[:200]}")

    def get_properties(self, props: list[tuple[int, int]]) -> dict[tuple[int, int], Any]:
        """Lee propiedades (siid,piid) -> {(siid,piid): value} para code 0."""
        keys = [{"did": f"{s}.{p}", "siid": s, "piid": p} for s, p in props]
        out: dict[tuple[int, int], Any] = {}
        for i in range(0, len(keys), 15):
            result = self._send_rpc("get_properties", keys[i:i + 15])
            for e in result or []:
                if e.get("code") == 0 and "value" in e:
                    out[(e["siid"], e["piid"])] = e["value"]
        return out

    def _set_once(self, siid: int, piid: int, value: Any) -> tuple[Any, Any]:
        """Escribe una vez; devuelve (entry, code) para (siid,piid)."""
        params = [{"did": f"{siid}.{piid}", "siid": siid, "piid": piid, "value": value}]
        result = self._send_rpc("set_properties", params)
        for entry in result or []:
            if entry.get("siid") == siid and entry.get("piid") == piid:
                return entry, entry.get("code")
        return result, None

    def wake(self) -> None:
        """Despierta el aparato de suspension (accion MIoT 2/1)."""
        self.run_action(*ACTION_WAKE)

    def _read_status(self) -> int | None:
        """Lee 2.1. None si la nube no devuelve un entero."""
        try:
            raw = self.get_properties([PROP_STATUS]).get(PROP_STATUS)
            return int(raw)
        except (DreameApiError, TypeError, ValueError) as err:
            _LOGGER.debug("No se pudo leer el estado al despertar: %s", err)
            return None

    def _wake_and_wait(self) -> bool:
        """Despierta y sondea hasta que el estado deja de ser suspension.

        Devuelve True si se ha visto un estado distinto de suspension.
        """
        try:
            self.wake()
        except DreameApiError as err:
            # The wake RPC can time out even though the device transitions
            # to standby. The following status polls decide whether it did.
            _LOGGER.debug("Wake RPC was not acknowledged: %s", err)
        deadline = time.monotonic() + WAKE_WAIT_SECONDS
        while True:
            status = self._read_status()
            if status is not None and status != _SUSPENDED_STATUS:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(WAKE_POLL_INTERVAL, remaining))

    def set_property(self, siid: int, piid: int, value: Any) -> Any:
        """Escribe una propiedad (siid,piid)=value.

        El SF25 devuelve code 1 (y no aplica) cuando esta en suspension (2.1=3).
        En ese caso despertamos y esperamos a que el estado cambie antes de
        reintentar la escritura una vez.
        """
        entry, code = self._set_once(siid, piid, value)
        awake = True
        if code == 1:
            awake = self._wake_and_wait()
            entry, code = self._set_once(siid, piid, value)
        if code == 0:
            return entry
        if (siid, piid) == PROP_CHILD_LOCK and awake and code == 1:
            raise DreameApiError(
                "El SF25 ha rechazado el bloqueo infantil. "
                "Solo tiene efecto con un ciclo en marcha."
            )
        if not awake:
            raise DreameApiError(
                f"Escritura {siid}.{piid}={value} rechazada: "
                "el aparato sigue en suspension."
            )
        raise DreameApiError(
            f"Escritura {siid}.{piid}={value} rechazada (code {code})."
        )

    def run_action(self, siid: int, aiid: int, in_args: list | None = None) -> Any:
        """Ejecuta una accion MIoT. Tolera respuestas sin 'result'."""
        params = {"did": str(self.did), "siid": siid, "aiid": aiid, "in": in_args or []}
        return self._send_rpc("action", params, allow_empty_success=True)
