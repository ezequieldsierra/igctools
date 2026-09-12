from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import certifi
import frappe
import requests

SETTINGS_DOCTYPE = "Configuracion Tasa Banco Popular"

PRODUCTION_HOST = "apipublico.bpd.com.do"

_CERTIFICATE_FILE = Path(__file__).resolve().parent.parent / "certificates" / "DigiCertEVRSACAG2.pem"


def _validate_bpd_url(url: str) -> str:
	url = str(url or "").strip()

	parsed = urlparse(url)

	if parsed.scheme.lower() != "https":
		raise ValueError("BPD requiere HTTPS")

	if parsed.hostname != PRODUCTION_HOST:
		raise ValueError("Host BPD no autorizado para CA personalizada: " + str(parsed.hostname))

	return url


@contextmanager
def bpd_ca_bundle():
	"""
	CA bundle normal + DigiCert EV RSA CA G2.

	La validacion SSL permanece ACTIVADA.
	No utiliza verify=False.
	"""

	if not _CERTIFICATE_FILE.exists():
		raise RuntimeError("No existe certificado DigiCert requerido: " + str(_CERTIFICATE_FILE))

	temp_path = None

	try:
		with open(certifi.where(), "rb") as base_file:
			base_bundle = base_file.read()

		with open(_CERTIFICATE_FILE, "rb") as intermediate_file:
			intermediate = intermediate_file.read()

		with tempfile.NamedTemporaryFile(
			mode="wb",
			prefix="igc_bpd_ca_",
			suffix=".pem",
			delete=False,
		) as temp_file:
			temp_file.write(base_bundle)

			if not base_bundle.endswith(b"\n"):
				temp_file.write(b"\n")

			temp_file.write(intermediate)

			if not intermediate.endswith(b"\n"):
				temp_file.write(b"\n")

			temp_path = temp_file.name

		yield temp_path

	finally:
		if temp_path:
			try:
				os.unlink(temp_path)
			except FileNotFoundError:
				pass


def _get_settings():
	cfg = frappe.get_single(SETTINGS_DOCTYPE)

	client_id = str(cfg.client_id or "").strip()
	client_secret = cfg.get_password("client_secret")
	scope = str(cfg.scope or "scope_1").strip()

	token_url = _validate_bpd_url(cfg.token_url)
	rate_url = _validate_bpd_url(cfg.rate_url)

	if not client_id:
		raise ValueError("Client ID Banco Popular no configurado")

	if not client_secret:
		raise ValueError("Client Secret Banco Popular no configurado")

	return {
		"client_id": client_id,
		"client_secret": client_secret,
		"scope": scope,
		"token_url": token_url,
		"rate_url": rate_url,
	}


def _rate_limit_headers(response):
	return {
		"rate_limit_limit": response.headers.get("X-RateLimit-Limit"),
		"rate_limit_remaining": response.headers.get("X-RateLimit-Remaining"),
		"burst_limit": response.headers.get("X-BurstLimit-Limit"),
		"burst_remaining": response.headers.get("X-BurstLimit-Remaining"),
		"retry_after": response.headers.get("Retry-After"),
		"rate_limit_reset": response.headers.get("X-RateLimit-Reset"),
	}


@frappe.whitelist()
def tls_probe():
	"""
	Prueba TLS sin utilizar las credenciales BPD.

	HTTP 401/403 significa que TLS fue establecido correctamente.
	"""

	cfg = frappe.get_single(SETTINGS_DOCTYPE)
	rate_url = _validate_bpd_url(cfg.rate_url)

	with bpd_ca_bundle() as verify_bundle:
		response = requests.get(
			rate_url,
			headers={
				"Accept": "application/json",
			},
			timeout=20,
			verify=verify_bundle,
			allow_redirects=False,
		)

	return {
		"ok": True,
		"tls": True,
		"http_code": response.status_code,
		"host": PRODUCTION_HOST,
		"ssl_verification": "ENABLED",
		"custom_intermediate": "DigiCert EV RSA CA G2",
	}


@frappe.whitelist()
def fetch_usd_rates():
	"""
	Consulta BPD Produccion utilizando las credenciales almacenadas
	en Configuracion Tasa Banco Popular.

	No devuelve Client Secret ni access_token.
	"""

	cfg = _get_settings()

	client_id = cfg["client_id"]
	client_secret = cfg["client_secret"]
	scope = cfg["scope"]

	token_url = cfg["token_url"]
	rate_url = cfg["rate_url"]

	token_refreshed = False

	with bpd_ca_bundle() as verify_bundle:
		# ------------------------------------------------------------------
		# TOKEN
		# ------------------------------------------------------------------

		token_response = requests.post(
			token_url,
			headers={
				"Accept": "application/json",
				"Content-Type": "application/x-www-form-urlencoded",
			},
			data={
				"grant_type": "client_credentials",
				"client_id": client_id,
				"client_secret": client_secret,
				"scope": scope,
			},
			timeout=20,
			verify=verify_bundle,
		)

		token_http_code = token_response.status_code

		if token_http_code < 200 or token_http_code >= 300:
			return {
				"ok": False,
				"stage": "TOKEN",
				"http_code": token_http_code,
				"error": "BPD OAuth respondio HTTP " + str(token_http_code),
				"ssl_verification": "ENABLED",
			}

		token_data = token_response.json()

		access_token = token_data.get("access_token")

		if not access_token:
			return {
				"ok": False,
				"stage": "TOKEN",
				"http_code": token_http_code,
				"error": "BPD OAuth respondio sin access_token",
				"ssl_verification": "ENABLED",
			}

		# BPD publica burst limit de 1 TPS.
		time.sleep(1.2)

		# ------------------------------------------------------------------
		# TASA
		# ------------------------------------------------------------------

		rate_response = requests.get(
			rate_url,
			headers={
				"Accept": "application/json",
				"Authorization": "Bearer " + access_token,
				"X-IBM-Client-Id": client_id,
			},
			timeout=20,
			verify=verify_bundle,
		)

		# Si el token fuera rechazado, renovar una sola vez.
		if rate_response.status_code == 401:
			token_refreshed = True

			time.sleep(1.2)

			token_response_2 = requests.post(
				token_url,
				headers={
					"Accept": "application/json",
					"Content-Type": "application/x-www-form-urlencoded",
				},
				data={
					"grant_type": "client_credentials",
					"client_id": client_id,
					"client_secret": client_secret,
					"scope": scope,
				},
				timeout=20,
				verify=verify_bundle,
			)

			if token_response_2.status_code < 200 or token_response_2.status_code >= 300:
				return {
					"ok": False,
					"stage": "TOKEN_REFRESH",
					"http_code": token_response_2.status_code,
					"error": "BPD renovacion OAuth respondio HTTP " + str(token_response_2.status_code),
					"ssl_verification": "ENABLED",
				}

			token_data_2 = token_response_2.json()
			access_token_2 = token_data_2.get("access_token")

			if not access_token_2:
				return {
					"ok": False,
					"stage": "TOKEN_REFRESH",
					"http_code": token_response_2.status_code,
					"error": "BPD renovacion OAuth sin access_token",
					"ssl_verification": "ENABLED",
				}

			time.sleep(1.2)

			rate_response = requests.get(
				rate_url,
				headers={
					"Accept": "application/json",
					"Authorization": "Bearer " + access_token_2,
					"X-IBM-Client-Id": client_id,
				},
				timeout=20,
				verify=verify_bundle,
			)

		rate_http_code = rate_response.status_code
		limit_data = _rate_limit_headers(rate_response)

		if rate_http_code < 200 or rate_http_code >= 300:
			result = {
				"ok": False,
				"stage": "RATE",
				"http_code": rate_http_code,
				"error": "BPD consultaTasa respondio HTTP " + str(rate_http_code),
				"token_refreshed": token_refreshed,
				"ssl_verification": "ENABLED",
			}

			result.update(limit_data)

			return result

		rate_data = rate_response.json()

	monedas_root = rate_data.get("monedas") or {}
	monedas = monedas_root.get("moneda") or []

	usd = None

	for moneda in monedas:
		if str(moneda.get("descripcion") or "").upper() == "USD":
			usd = moneda
			break

	if not usd:
		return {
			"ok": False,
			"stage": "PARSE",
			"http_code": rate_http_code,
			"error": "BPD consultaTasa no devolvio USD",
			"ssl_verification": "ENABLED",
		}

	result = {
		"ok": True,
		"stage": "OK",
		"token_http_code": token_http_code,
		"rate_http_code": rate_http_code,
		"descripcion": "USD",
		"compra": usd.get("compra"),
		"venta": usd.get("venta"),
		"fecha_consulta": monedas_root.get("fechaConsulta"),
		"token_refreshed": token_refreshed,
		"ssl_verification": "ENABLED",
		"custom_intermediate": "DigiCert EV RSA CA G2",
	}

	result.update(limit_data)

	return result
