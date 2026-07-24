"""
bot.py - Núcleo de decisão do chatbot.

A função `process_message()` é o coração do sistema: recebe uma mensagem
de um cliente + o estado atual da conversa dele, e devolve uma resposta +
o novo estado. Essa função é 100% independente de WhatsApp — no futuro,
o webhook da Cloud API só vai chamar essa mesma função.

O negócio tem um `tipo_atendimento`:
  - 'agendamento' -> fluxo de marcar horário (ex: salão, clínica)
  - 'pedido'      -> fluxo de captar encomenda (ex: produtos personalizados)
O menu e a máquina de estados se ajustam de acordo.

Produtos/serviços têm uma `categoria` (ex: "Canecas", "Squeezes/Garrafas").
Se o negócio tiver mais de uma categoria em uso, o cliente escolhe a
categoria primeiro, depois o produto dentro dela — evita uma lista gigante
de uma vez só. Se só existir uma categoria (ou nenhuma), pula direto pra
lista de produtos, sem esse passo extra.
"""

import db

# Estados - fluxo de agendamento
ESTADO_INICIAL = "inicial"
ESTADO_AG_ESCOLHENDO_CATEGORIA = "ag_escolhendo_categoria"
ESTADO_AG_ESCOLHENDO_SERVICO = "ag_escolhendo_servico"
ESTADO_AG_ESCOLHENDO_HORARIO = "ag_escolhendo_horario"
ESTADO_AG_CONFIRMANDO_NOME = "ag_confirmando_nome"

# Estados - fluxo de pedido/encomenda
ESTADO_PED_ESCOLHENDO_CATEGORIA = "ped_escolhendo_categoria"
ESTADO_PED_ESCOLHENDO_PRODUTO = "ped_escolhendo_produto"
ESTADO_PED_QUANTIDADE = "ped_quantidade"
ESTADO_PED_PERSONALIZACAO = "ped_personalizacao"
ESTADO_PED_CONFIRMANDO_NOME = "ped_confirmando_nome"

# "Sessões" em memória: cliente_telefone -> dict de estado
# Em produção isso seria Redis ou uma tabela `sessoes` no banco,
# pra sobreviver a restarts do servidor.
sessions = {}


def get_session(cliente_telefone):
    if cliente_telefone not in sessions:
        sessions[cliente_telefone] = {"estado": ESTADO_INICIAL, "dados": {}}
    return sessions[cliente_telefone]


def detectar_intencao(texto: str, faqs: list, tipo_atendimento: str) -> str:
    texto = texto.lower().strip()

    if tipo_atendimento == "agendamento":
        gatilhos = ["agendar", "marcar", "horario disponivel", "horário disponível", "reservar"]
        if any(p in texto for p in gatilhos):
            return "iniciar_fluxo"
    else:  # pedido
        gatilhos = ["pedido", "encomendar", "encomenda", "comprar", "fazer pedido", "quero fazer"]
        if any(p in texto for p in gatilhos):
            return "iniciar_fluxo"

    for item in faqs:
        chaves = item["palavras_chave"].split(",")
        if any(chave.strip() in texto for chave in chaves):
            return f"faq:{item['id']}"

    saudacoes = ["oi", "ola", "olá", "bom dia", "boa tarde", "boa noite", "menu"]
    if any(texto.startswith(s) for s in saudacoes) or texto == "":
        return "saudacao"

    return "desconhecido"


def montar_menu(negocio: dict) -> str:
    if negocio["tipo_atendimento"] == "agendamento":
        return (
            f"Olá! Bem-vindo(a) ao *{negocio['nome']}* 👋\n\n"
            "Posso te ajudar com:\n"
            "1️⃣ Ver nossos serviços e preços\n"
            "2️⃣ Agendar um horário\n"
            "3️⃣ Horário de funcionamento, endereço, formas de pagamento\n\n"
            "É só me dizer o que precisa, ou digitar *agendar* para marcar um horário."
        )
    else:
        return (
            f"Olá! Bem-vindo(a) à *{negocio['nome']}* 👋\n\n"
            "Posso te ajudar com:\n"
            "1️⃣ Ver nosso catálogo e preços\n"
            "2️⃣ Fazer um pedido/encomenda\n"
            "3️⃣ Prazos, entrega e formas de pagamento\n\n"
            "É só me dizer o que precisa, ou digitar *pedido* para começar sua encomenda."
        )


def _formatar_item(s: dict, indice: int) -> str:
    linha = f"{indice}. {s['nome']} - R$ {s['preco']:.2f}"
    if s.get("estoque") is not None and s["estoque"] <= 0:
        linha += " (sem estoque no momento)"
    return linha


def montar_catalogo(servicos: list, categorias: list) -> str:
    """Catálogo agrupado por categoria (quando há mais de uma em uso)."""
    if len(categorias) <= 1:
        linhas = [f"• {s['nome']} - R$ {s['preco']:.2f}" +
                  (" (sem estoque no momento)" if s.get("estoque") is not None and s["estoque"] <= 0 else "")
                  for s in servicos]
        lista = "\n".join(linhas)
        return f"Nosso catálogo:\n\n{lista}\n\nQuer fazer um pedido? É só digitar *pedido*."

    blocos = []
    for cat in categorias:
        itens_da_cat = [s for s in servicos if s["categoria"] == cat]
        if not itens_da_cat:
            continue
        linhas = "\n".join(
            f"• {s['nome']} - R$ {s['preco']:.2f}" +
            (" (sem estoque no momento)" if s.get("estoque") is not None and s["estoque"] <= 0 else "")
            for s in itens_da_cat
        )
        blocos.append(f"*{cat}*\n{linhas}")

    corpo = "\n\n".join(blocos)
    return f"Nosso catálogo:\n\n{corpo}\n\nQuer fazer um pedido? É só digitar *pedido*."


def process_message(negocio_id: int, cliente_telefone: str, texto: str,
                     imagem_base64: str = None, imagem_mime_type: str = None) -> str:
    session = get_session(cliente_telefone)
    session["dados"]["negocio_id"] = negocio_id  # guardamos pra usar nos sub-estados
    estado = session["estado"]

    # Se essa mensagem trouxe uma imagem, guardamos junto na sessão — ela
    # vai junto quando o pedido for salvo no banco, independente de em
    # qual passo do fluxo o cliente decidiu mandar a foto.
    if imagem_base64:
        session["dados"]["imagem_base64"] = imagem_base64
        session["dados"]["imagem_mime_type"] = imagem_mime_type

    conn = db.get_conn()
    negocio_row = conn.execute("SELECT * FROM negocios WHERE id = ?", (negocio_id,)).fetchone()
    conn.close()
    negocio = dict(negocio_row)
    tipo = negocio["tipo_atendimento"]

    faqs = db.get_faq(negocio_id)
    servicos = db.get_servicos(negocio_id)
    categorias = db.listar_categorias(negocio_id)

    # --- Máquina de estados: continuação de um fluxo em andamento ---
    if estado == ESTADO_AG_ESCOLHENDO_CATEGORIA:
        return _tratar_escolha_categoria(session, categorias, servicos, tipo, texto)
    if estado == ESTADO_AG_ESCOLHENDO_SERVICO:
        return _ag_tratar_escolha_servico(session, texto)
    if estado == ESTADO_AG_ESCOLHENDO_HORARIO:
        return _ag_tratar_escolha_horario(session, texto)
    if estado == ESTADO_AG_CONFIRMANDO_NOME:
        return _ag_tratar_confirmacao_nome(session, negocio_id, cliente_telefone, texto)

    if estado == ESTADO_PED_ESCOLHENDO_CATEGORIA:
        return _tratar_escolha_categoria(session, categorias, servicos, tipo, texto)
    if estado == ESTADO_PED_ESCOLHENDO_PRODUTO:
        return _ped_tratar_escolha_produto(session, texto)
    if estado == ESTADO_PED_QUANTIDADE:
        return _ped_tratar_quantidade(session, texto)
    if estado == ESTADO_PED_PERSONALIZACAO:
        return _ped_tratar_personalizacao(session, texto)
    if estado == ESTADO_PED_CONFIRMANDO_NOME:
        return _ped_tratar_confirmacao_nome(session, negocio_id, cliente_telefone, texto)

    # --- Estado inicial: detectar intenção da mensagem livre ---
    # Trata primeiro os atalhos numéricos do menu (1, 2, 3).
    texto_limpo = texto.strip()
    if texto_limpo == "1":
        return montar_catalogo(servicos, categorias)
    if texto_limpo == "2":
        return _iniciar_fluxo(session, categorias, servicos, tipo)
    if texto_limpo == "3":
        info_faq = "\n".join(f"• {f['resposta']}" for f in faqs)
        return f"Aqui estão nossas informações:\n\n{info_faq}"

    intencao = detectar_intencao(texto, faqs, tipo)

    if intencao == "saudacao":
        return montar_menu(negocio)

    if intencao == "iniciar_fluxo":
        return _iniciar_fluxo(session, categorias, servicos, tipo)

    if intencao.startswith("faq:"):
        faq_id = int(intencao.split(":")[1])
        item = next(f for f in faqs if f["id"] == faq_id)
        return item["resposta"]

    if any(p in texto.lower() for p in ["servico", "serviço", "produto", "preco", "preço", "catalogo", "catálogo"]):
        return montar_catalogo(servicos, categorias)

    # Fallback: não entendeu -> encaminha pra humano
    return (
        "Não consegui entender sua mensagem 🤔\n"
        "Vou chamar alguém da nossa equipe para te atender por aqui em instantes.\n\n"
        "Se quiser, digite *menu* para ver as opções novamente."
    )


def _iniciar_fluxo(session, categorias, servicos, tipo):
    session["dados"]["tipo"] = tipo

    # Só pergunta a categoria se houver mais de uma em uso — senão, pula
    # direto pra lista de produtos/serviços, pra não criar fricção à toa.
    if len(categorias) > 1:
        if tipo == "agendamento":
            session["estado"] = ESTADO_AG_ESCOLHENDO_CATEGORIA
        else:
            session["estado"] = ESTADO_PED_ESCOLHENDO_CATEGORIA
        lista = "\n".join(f"{i+1}. {c}" for i, c in enumerate(categorias))
        verbo = "agendar" if tipo == "agendamento" else "encomendar"
        return f"Legal! Qual categoria você quer {verbo}?\n\n{lista}\n\nDigite o número da opção."

    return _listar_itens_para_escolha(session, servicos, tipo)


def _tratar_escolha_categoria(session, categorias, servicos, tipo, texto):
    texto = texto.strip()
    if not texto.isdigit() or not (1 <= int(texto) <= len(categorias)):
        return f"Não entendi. Digite um número de 1 a {len(categorias)} referente à categoria."

    categoria_escolhida = categorias[int(texto) - 1]
    itens_da_categoria = [s for s in servicos if s["categoria"] == categoria_escolhida]
    return _listar_itens_para_escolha(session, itens_da_categoria, tipo)


def _listar_itens_para_escolha(session, itens, tipo):
    """Mostra a lista de produtos/serviços (já filtrada por categoria, se
    for o caso) e guarda essa lista específica na sessão, porque os
    próximos passos (escolher quantidade/horário) precisam saber a quais
    itens os números '1, 2, 3...' se referem."""
    session["dados"]["itens_disponiveis"] = itens
    lista = "\n".join(_formatar_item(s, i + 1) for i, s in enumerate(itens))

    if not itens:
        session["estado"] = ESTADO_INICIAL
        return "Não encontrei itens nessa categoria no momento. Digite *menu* para ver as opções."

    if tipo == "agendamento":
        session["estado"] = ESTADO_AG_ESCOLHENDO_SERVICO
        return f"Qual serviço você quer agendar?\n\n{lista}\n\nDigite o número da opção."
    else:
        session["estado"] = ESTADO_PED_ESCOLHENDO_PRODUTO
        return f"Qual produto você quer encomendar?\n\n{lista}\n\nDigite o número da opção."


# ---------------------------------------------------------------------------
# Fluxo: AGENDAMENTO
# ---------------------------------------------------------------------------

def _ag_tratar_escolha_servico(session, texto):
    texto = texto.strip()
    itens = session["dados"].get("itens_disponiveis", [])
    if not texto.isdigit() or not (1 <= int(texto) <= len(itens)):
        return f"Não entendi. Digite um número de 1 a {len(itens)} referente ao serviço."

    servico = itens[int(texto) - 1]
    session["dados"]["servico_id"] = servico["id"]
    session["dados"]["servico_nome"] = servico["nome"]
    session["estado"] = ESTADO_AG_ESCOLHENDO_HORARIO

    horarios = db.horarios_disponiveis(session["dados"]["negocio_id"], servico["id"])
    session["dados"]["horarios_oferecidos"] = horarios

    if not horarios:
        session["estado"] = ESTADO_INICIAL
        return "Poxa, não temos horários disponíveis nos próximos dias. Tente novamente mais tarde."

    lista = "\n".join(f"{i+1}. {h}" for i, h in enumerate(horarios))
    return f"Show! Horários disponíveis para *{servico['nome']}*:\n\n{lista}\n\nDigite o número do horário desejado."


def _ag_tratar_escolha_horario(session, texto):
    texto = texto.strip()
    horarios = session["dados"].get("horarios_oferecidos", [])

    if not texto.isdigit() or not (1 <= int(texto) <= len(horarios)):
        return f"Não entendi. Digite um número de 1 a {len(horarios)} referente ao horário."

    horario_escolhido = horarios[int(texto) - 1]
    session["dados"]["data_hora"] = horario_escolhido
    session["estado"] = ESTADO_AG_CONFIRMANDO_NOME

    return "Perfeito! Para confirmar, me diga seu nome completo, por favor."


def _ag_tratar_confirmacao_nome(session, negocio_id, cliente_telefone, texto):
    nome = texto.strip()
    dados = session["dados"]

    db.criar_agendamento(
        negocio_id=negocio_id,
        cliente_telefone=cliente_telefone,
        cliente_nome=nome,
        produto_servico_id=dados["servico_id"],
        data_hora=dados["data_hora"],
    )

    resumo = (
        f"✅ Agendamento confirmado!\n\n"
        f"Serviço: {dados['servico_nome']}\n"
        f"Data/hora: {dados['data_hora']}\n"
        f"Nome: {nome}\n\n"
        "Te esperamos! Se precisar remarcar, é só chamar por aqui."
    )

    sessions[cliente_telefone] = {"estado": ESTADO_INICIAL, "dados": {}}
    return resumo


# ---------------------------------------------------------------------------
# Fluxo: PEDIDO / ENCOMENDA
# ---------------------------------------------------------------------------

def _ped_tratar_escolha_produto(session, texto):
    texto = texto.strip()
    itens = session["dados"].get("itens_disponiveis", [])
    if not texto.isdigit() or not (1 <= int(texto) <= len(itens)):
        return f"Não entendi. Digite um número de 1 a {len(itens)} referente ao produto."

    produto = itens[int(texto) - 1]
    session["dados"]["produto_id"] = produto["id"]
    session["dados"]["produto_nome"] = produto["nome"]
    session["dados"]["produto_preco"] = produto["preco"]
    session["estado"] = ESTADO_PED_QUANTIDADE

    return f"Legal, *{produto['nome']}*! Quantas unidades você quer?"


def _ped_tratar_quantidade(session, texto):
    texto = texto.strip()
    if not texto.isdigit() or int(texto) < 1:
        return "Não entendi. Digite a quantidade desejada (só o número, ex: 2)."

    session["dados"]["quantidade"] = int(texto)
    session["estado"] = ESTADO_PED_PERSONALIZACAO
    return (
        "Show! Agora me conta os detalhes da personalização "
        "(texto, foto que vai usar, cor, referência etc.).\n\n"
        "Se não tiver personalização, pode digitar *nenhuma*."
    )


def _ped_tratar_personalizacao(session, texto):
    session["dados"]["personalizacao"] = texto.strip()
    session["estado"] = ESTADO_PED_CONFIRMANDO_NOME
    return "Perfeito! Para fechar o pedido, me diga seu nome completo, por favor."


def _ped_tratar_confirmacao_nome(session, negocio_id, cliente_telefone, texto):
    nome = texto.strip()
    dados = session["dados"]

    db.criar_pedido(
        negocio_id=negocio_id,
        cliente_telefone=cliente_telefone,
        cliente_nome=nome,
        produto_servico_id=dados["produto_id"],
        quantidade=dados["quantidade"],
        personalizacao=dados["personalizacao"],
        imagem_base64=dados.get("imagem_base64"),
        imagem_mime_type=dados.get("imagem_mime_type"),
    )

    total = dados["produto_preco"] * dados["quantidade"]
    resumo = (
        f"✅ Pedido registrado!\n\n"
        f"Produto: {dados['produto_nome']}\n"
        f"Quantidade: {dados['quantidade']}\n"
        f"Personalização: {dados['personalizacao']}\n"
        f"Valor estimado: R$ {total:.2f}\n"
        f"Nome: {nome}\n\n"
        "Vamos confirmar os detalhes e o prazo por aqui em breve. Obrigado! 🙌"
    )

    sessions[cliente_telefone] = {"estado": ESTADO_INICIAL, "dados": {}}
    return resumo
