"""Control remoto de CRISTOPHER por WhatsApp (chat "Note to self").

Hilo daemon dedicado e independiente del demonio proactivo general
(`proactivo.py`, que revisa cada `POLL_SEGUNDOS` = 60s por defecto): aquí interesa
sentir que se está "chateando en vivo" desde el móvil, así que se vigila el chat
propio con un poll mucho más corto (`POLL_WHATSAPP_COMANDOS`).

Solo cuenta como orden un mensaje que el propio usuario se manda a sí mismo (chat
"Note to self", resuelto vía `whatsapp_client.self_chat_id()`) que empiece por
`WHATSAPP_COMANDO_PREFIJO`. El resto de ese chat sigue siendo notas sueltas: se
ignora, nunca dispara nada. Esto es una excepción acotada y explícita a "WhatsApp
nunca es un bot autónomo" (ver CLAUDE.md): el disparo sigue siendo una instrucción
literal del usuario, solo que llega por otro canal.
"""

from __future__ import annotations

import time
from typing import Callable

from cristopher.config import POLL_WHATSAPP_COMANDOS, WHATSAPP_COMANDO_PREFIJO
from cristopher.recordatorios import get_recordatorios


def _extraer_instruccion(texto: str) -> "str | None":
    plano = texto.strip()
    if not plano.lower().startswith(WHATSAPP_COMANDO_PREFIJO):
        return None
    resto = plano[len(WHATSAPP_COMANDO_PREFIJO):].strip()
    return resto or None


def vigilar(
    encolar_fn: Callable[[str, str, "list[tuple[bytes, str]] | None"], None],
) -> None:
    """Bucle infinito (pensado para un hilo daemon propio, ver hud/__main__.py):
    cada `POLL_WHATSAPP_COMANDOS` segundos revisa el chat "Note to self" y, si hay
    un mensaje nuevo con el prefijo de comando (o una nota de voz, ver abajo), llama
    a `encolar_fn(instruccion, chat_id, media)`. Nunca debe morir por un fallo
    puntual (WhatsApp caído, red, etc.).

    Desde que `whatsapp/wa.js` empezó a bajar audio/imagen (Fase B de la entrada
    multimodal), `chat` puede traer `tipo`/`media_path`/`mimetype` además de
    `ultimo_texto`. Dos casos especiales sobre el texto plano de siempre:
    - `tipo == "audio"`: una nota de voz no tiene caption, así que no puede llevar
      el prefijo — mandarla a este chat privado ya es, por sí sola, una orden
      deliberada (igual de explícita que escribir el prefijo). Se transcribe con
      `voz.transcribir_archivo` y dispara siempre, sin exigir prefijo.
    - `tipo in ("imagen", "documento")`: sigue exigiendo el prefijo en el caption
      como cualquier texto; si lo trae, además se leen los bytes del adjunto para
      pasarlos como `media` a `encolar_fn` (mismo patrón que
      `Cristopher.send(..., imagenes=...)` — el nombre del parámetro es historia,
      acepta cualquier mimetype que Gemini entienda como dato inline, no solo fotos).
    """
    from cristopher import voz, whatsapp_client

    rec = get_recordatorios()
    while True:
        try:
            self_id = whatsapp_client.self_chat_id()
            if self_id:
                data = whatsapp_client.check_new()
                chat = next(
                    (c for c in data.get("chats", []) if c["chat_id"] == self_id), None
                )
                if chat:
                    clave = f"wa_cmd:{chat['ultimo_id']}"
                    if not rec.ya_visto(clave):
                        tipo = chat.get("tipo") or "texto"
                        if tipo == "audio" and chat.get("media_path"):
                            try:
                                instruccion = voz.transcribir_archivo(chat["media_path"]) or None
                            except Exception:
                                instruccion = None
                            # Se marca visto se entienda o no la nota: una nota de voz
                            # corrupta o inaudible no debe reintentarse en cada poll
                            # (a diferencia del texto plano, donde re-chequear el
                            # prefijo es gratis).
                            try:
                                if instruccion:
                                    whatsapp_client.read(self_id, 1)
                                    encolar_fn(instruccion, self_id, None)
                            finally:
                                rec.marcar_visto(clave)
                        else:
                            instruccion = _extraer_instruccion(chat["ultimo_texto"])
                            if instruccion:
                                media = None
                                if tipo in ("imagen", "documento") and chat.get("media_path"):
                                    try:
                                        with open(chat["media_path"], "rb") as f:
                                            media = [(f.read(), chat.get("mimetype") or "application/octet-stream")]
                                        # Los bytes solos bastan para que el modelo "vea" la
                                        # foto/documento (Part multimodal), pero tools como
                                        # huella_buscar_cara necesitan un archivo real — sin
                                        # decirle la ruta aquí no tendría cómo invocarlas.
                                        etiqueta = "Imagen" if tipo == "imagen" else "Documento"
                                        instruccion += (
                                            f"\n\n[{etiqueta} adjunto guardado en disco en: "
                                            f"{chat['media_path']}]"
                                        )
                                    except OSError:
                                        media = None
                                # Igual patrón que proactivo.py::_revisar_preguntas_sesiones:
                                # se marca visto en el finally para nunca reintentar un
                                # comando, aunque falle a mitad (evita doble ejecución).
                                try:
                                    whatsapp_client.read(self_id, 1)  # limpia el contador de "nuevos"
                                    encolar_fn(instruccion, self_id, media)
                                finally:
                                    rec.marcar_visto(clave)
        except Exception:
            pass
        time.sleep(POLL_WHATSAPP_COMANDOS)
