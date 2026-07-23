"""
webhook_whatsapp.py - Integração real com a WhatsApp Cloud API (Meta).

Duas responsabilidades:
  1. Verificação do webhook (GET) - a Meta chama isso uma vez, na hora
     de você configurar a URL do webhook no painel dela.
  2. Recebimento de mensagens (POST) - a Meta chama isso toda vez que
     alguém manda mensagem pro seu número. Aqui a gente extrai o texto,
     chama bot.process_message() (a mesma lógica do chat_cli.py!) e manda
     a resposta de volta via Graph API.

CREDENCIAIS: nunca ficam no código. Vêm de variáveis de ambiente:
  - WHATSAPP_TOKEN            -> token permanente (System User)
  - WHATSAPP_PHONE_NUMBER_ID  -> ID do número de teste/produção
  - WHATSAPP_VERIFY_TOKEN     -> senha que você mesmo inventou
  - WHATSAPP_NEGOCIO_SLUG     -> slug do negócio (no seu banco) que esse
                                  número de WhatsApp representa

No Render: Dashboard do serviço -> Environment -> Add Environment Variable.
Localmente (se quiser testar sem publicar): define no terminal antes de
rodar o uvicorn, ex (Windows / PowerShell):
    $env:WHATSAPP_TOKEN="seu_token_aqui"
"""

import os
import requests

import db
import bot

GRAPH_API_VERSION = "v21.0"

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_NEGOCIO_SLUG = os.environ.get("WHATSAPP_NEGOCIO_SLUG", "")


def verificar_configuracao():
    """Retorna lista de variáveis de ambiente que ainda faltam configurar."""
    faltando = []
    if not WHATSAPP_TOKEN:
        faltando.append("WHATSAPP_TOKEN")
    if not WHATSAPP_PHONE_NUMBER_ID:
        faltando.append("WHATSAPP_PHONE_NUMBER_ID")
    if not WHATSAPP_VERIFY_TOKEN:
        faltando.append("WHATSAPP_VERIFY_TOKEN")
    if not WHATSAPP_NEGOCIO_SLUG:
        faltando.append("WHATSAPP_NEGOCIO_SLUG")
    return faltando


def processar_verificacao(mode: str, token: str, challenge: str):
    """
    Handler da verificação GET que a Meta faz uma única vez ao configurar
    o webhook. Precisa devolver o `challenge` em texto puro se o modo e o
    token baterem, ou recusar (None) caso contrário.
    """
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return challenge
    return None


def enviar_mensagem_whatsapp(destinatario: str, texto: str):
    """Envia uma mensagem de texto simples via Graph API."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "text",
        "text": {"body": texto},
    }
    resposta = requests.post(url, headers=headers, json=payload, timeout=10)
    if resposta.status_code >= 400:
        print(f"[webhook_whatsapp] Erro ao enviar mensagem: {resposta.status_code} {resposta.text}")
    return resposta


def processar_mensagem_recebida(payload: dict):
    """
    Extrai a mensagem de texto do payload que a Meta manda no POST e
    devolve a resposta do bot, já enviada de volta pelo WhatsApp.
    Ignora silenciosamente eventos que não sejam mensagem de texto nova
    (ex: confirmações de leitura, mensagens de imagem/áudio por enquanto).
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        if "messages" not in value:
            return  # é um evento de status (entregue/lido), não uma mensagem nova

        mensagem = value["messages"][0]
        remetente = mensagem["from"]  # número de telefone de quem mandou

        if mensagem.get("type") != "text":
            enviar_mensagem_whatsapp(
                remetente,
                "No momento só consigo responder mensagens de texto. Pode escrever sua dúvida?",
            )
            return

        texto = mensagem["text"]["body"]

    except (KeyError, IndexError) as e:
        print(f"[webhook_whatsapp] Payload inesperado, ignorando: {e}")
        return

    negocio = db.get_negocio_by_slug(WHATSAPP_NEGOCIO_SLUG)
    if not negocio:
        print(f"[webhook_whatsapp] Negócio com slug '{WHATSAPP_NEGOCIO_SLUG}' não encontrado no banco.")
        return

    resposta_texto = bot.process_message(negocio["id"], remetente, texto)
    enviar_mensagem_whatsapp(remetente, resposta_texto)
