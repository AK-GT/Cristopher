"""Hosting temporal de archivos locales tras una URL pública (Google Cloud
Storage), para la fase de publicación de redes sociales.

Instagram Content Publishing exige un `video_url` que Meta pueda descargar por
HTTP — no admite subida directa de bytes locales como sí hacen YouTube
(resumable upload) y TikTok (FILE_UPLOAD). Este módulo es el único punto del
árbol de redes que resuelve ese problema, deliberadamente separado de
`redes_auth.py`: no es autenticación de una red social concreta, es un helper
genérico de infraestructura ("necesito una URL pública temporal de un archivo
local") que podría reutilizar cualquier otra cosa en el futuro.

Decisiones (ver plan de la fase de publicación):
- Google Drive descartado: sus enlaces de descarga están pensados para
  navegadores, no para *fetchers* automatizados de terceros (interpone páginas
  de confirmación/aviso de virus de forma no determinista).
- Cloud Storage del MISMO proyecto GCP que ya usa Calendar/Gmail/YouTube, con
  una service account propia (JSON key, NO el OAuth interactivo de usuario) —
  es el patrón natural para GCS: sin consentimiento interactivo, permisos
  acotados por IAM solo a este bucket.
- Signed URL (v4, GET) en vez de objeto público: el bucket nunca queda expuesto
  con `allUsers`; si el borrado explícito post-publicación fallara, el objeto
  sigue siendo inaccesible pasada la expiración en vez de público indefinidamente.
"""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path
from typing import Optional

from cristopher.config import (
    GCS_BUCKET_TEMPORAL,
    GCS_SERVICE_ACCOUNT_JSON,
    GCS_SIGNED_URL_EXPIRACION_SEGUNDOS,
)

_client: Optional[object] = None


class HostingTemporalError(RuntimeError):
    """Falta configuración del hosting temporal, o falla la subida/firma/borrado.
    Mensaje siempre accionable (§1 CLAUDE.md: fallos explícitos)."""


def _cliente_gcs():
    global _client
    if _client is not None:
        return _client

    if not GCS_BUCKET_TEMPORAL:
        raise HostingTemporalError(
            "Falta GCS_BUCKET_TEMPORAL en tu .env. Crea un bucket de Cloud Storage "
            "en el mismo proyecto de Google Cloud que usas para Calendar/Gmail/"
            "YouTube y pon su nombre en esa variable (ver .env.example)."
        )
    if not GCS_SERVICE_ACCOUNT_JSON.exists():
        raise HostingTemporalError(
            "Falta el archivo de credenciales de la service account de Cloud "
            f"Storage.\n  Colócalo en: {GCS_SERVICE_ACCOUNT_JSON}\n"
            "  Créala en Google Cloud Console → IAM → Cuentas de servicio, con el "
            "rol 'Storage Object Admin' acotado SOLO a tu bucket temporal, y "
            "descarga su clave JSON a esa ruta."
        )

    from google.cloud import storage

    _client = storage.Client.from_service_account_json(str(GCS_SERVICE_ACCOUNT_JSON))
    return _client


def subir_temporal(ruta_archivo: str, prefijo: str = "tmp") -> tuple[str, str]:
    """Sube `ruta_archivo` al bucket temporal y devuelve (url_firmada, blob_name).

    La URL firmada (v4, GET) expira en GCS_SIGNED_URL_EXPIRACION_SEGUNDOS —
    tiempo de sobra para que Instagram la descargue al crear el contenedor de
    media (ocurre casi al momento de la llamada, no durante el polling
    posterior). `blob_name` se necesita después para borrar_temporal().

    Args:
        ruta_archivo: ruta local al archivo a exponer temporalmente.
        prefijo: subcarpeta lógica dentro del bucket (por defecto 'tmp').
    """
    ruta = Path(ruta_archivo)
    if not ruta.exists() or not ruta.is_file():
        raise HostingTemporalError(f"No encuentro el archivo '{ruta_archivo}'.")

    try:
        client = _cliente_gcs()
        bucket = client.bucket(GCS_BUCKET_TEMPORAL)
        blob_name = f"{prefijo}/{uuid.uuid4().hex}{ruta.suffix}"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(ruta))
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(seconds=GCS_SIGNED_URL_EXPIRACION_SEGUNDOS),
            method="GET",
        )
    except HostingTemporalError:
        raise
    except Exception as exc:
        raise HostingTemporalError(f"No se pudo subir el archivo al hosting temporal: {exc}") from exc

    return url, blob_name


def borrar_temporal(blob_name: str) -> None:
    """Borra el objeto subido por subir_temporal. Best-effort: nunca lanza — se
    llama desde un `finally` en el flujo de publicación, y un fallo de limpieza
    no debe tumbar ni ocultar el resultado real de esa publicación."""
    try:
        client = _cliente_gcs()
        client.bucket(GCS_BUCKET_TEMPORAL).blob(blob_name).delete()
    except Exception:
        pass
