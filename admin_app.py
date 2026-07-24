"""
admin_app.py - Painel administrativo web.

Roda com:  python -m uvicorn admin_app:app --reload

Duas áreas:
  - /admin            -> SEU painel interno: lista todos os negócios,
                          cria/exclui negócios, acessa qualquer um deles.
  - /loja/{slug}       -> Área do CLIENTE: um link único e "secreto" por
                          negócio (tipo um link de compartilhamento do
                          Google Docs). Manda esse link pro lojista e ele
                          edita só o catálogo/horários/FAQ dele — sem ver
                          nem mexer nos outros negócios.

O "slug" é gerado automaticamente na criação do negócio (db.criar_negocio).
Pra pegar o link de um negócio já existente, entra em /admin/{id} — o link
do cliente aparece no topo da página.
"""

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, JSONResponse

import db
import webhook_whatsapp as wa

app = FastAPI(title="Painel do Lojista - Protótipo")

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
STATUS_PEDIDO = ["novo", "em produção", "pronto", "entregue", "cancelado"]


def resolve_negocio(ref: str):
    """Resolve o negócio tanto por ID numérico (/admin/{id}) quanto por
    slug (/loja/{slug}) — as duas áreas chamam as mesmas funções de
    conteúdo, só muda como o negócio é localizado."""
    if ref.isdigit():
        return db.get_negocio(int(ref))
    return db.get_negocio_by_slug(ref)


# ---------------------------------------------------------------------------
# Lista de negócios / criação de novo negócio (SÓ existe em /admin)
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def pagina_negocios():
    negocios = db.listar_negocios()
    linhas = "".join(
        f"""<tr>
            <td><a href="/admin/{n['id']}">{n['nome']}</a></td>
            <td>{n['telefone_whatsapp'] or '-'}</td>
            <td>{'Agendamento' if n['tipo_atendimento'] == 'agendamento' else 'Pedido/Encomenda'}</td>
            <td>
                <form class="inline" method="post" action="/admin/negocios/{n['id']}/excluir"
                      onsubmit="return confirm('Excluir {n['nome']} e todos os dados dele? Essa ação não pode ser desfeita.');">
                    <button type="submit" style="background:#c33;">Excluir</button>
                </form>
            </td>
        </tr>"""
        for n in negocios
    )
    return f"""
    <html>
    <head>
        <meta charset="utf-8"><title>Painel - Negócios</title>
        <style>
            body {{ font-family: -apple-system, Arial, sans-serif; max-width: 900px;
                    margin: 30px auto; padding: 0 20px; background: #fafafa; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; background: white; }}
            th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 14px; }}
            a {{ color: #0a6; text-decoration: none; font-weight: 600; }}
            .card {{ background: white; padding: 16px 20px; border-radius: 8px;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-top: 12px; }}
            input, select {{ padding: 6px 8px; margin: 4px 6px 4px 0; border: 1px solid #ccc; border-radius: 4px; }}
            button {{ padding: 6px 14px; background: #0a6; color: white; border: none;
                      border-radius: 4px; cursor: pointer; }}
        </style>
    </head>
    <body>
        <h1>🏪 Negócios cadastrados</h1>
        <div class="card">
            <table>
                <tr><th>Nome</th><th>WhatsApp</th><th>Tipo</th><th></th></tr>
                {linhas or '<tr><td colspan="4">Nenhum negócio cadastrado ainda.</td></tr>'}
            </table>
        </div>

        <h2>Cadastrar novo negócio</h2>
        <div class="card">
            <form method="post" action="/admin/negocios">
                <div>
                    <input name="nome" placeholder="Nome do negócio" required style="width:250px;">
                    <input name="telefone_whatsapp" placeholder="Número WhatsApp (só dígitos, com DDI)" style="width:220px;">
                </div>
                <div style="margin-top:8px;">
                    <select name="tipo_atendimento">
                        <option value="agendamento">Agendamento (marca horário)</option>
                        <option value="pedido">Pedido/Encomenda (produtos personalizados etc.)</option>
                    </select>
                    <input name="horario_funcionamento" placeholder="Horário de funcionamento (texto livre)" style="width:250px;">
                </div>
                <button type="submit" style="margin-top:10px;">Criar negócio</button>
            </form>
        </div>
    </body>
    </html>
    """


@app.post("/admin/negocios")
def add_negocio(
    nome: str = Form(...),
    telefone_whatsapp: str = Form(""),
    tipo_atendimento: str = Form("agendamento"),
    horario_funcionamento: str = Form(""),
):
    novo_id = db.criar_negocio(nome, telefone_whatsapp or None, horario_funcionamento, tipo_atendimento)
    return RedirectResponse(f"/admin/{novo_id}", status_code=303)


@app.post("/admin/negocios/{negocio_id}/excluir")
def del_negocio(negocio_id: int):
    db.excluir_negocio(negocio_id)
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------------------
# Layout compartilhado entre /admin/{id} e /loja/{slug}
# ---------------------------------------------------------------------------

def layout(negocio: dict, conteudo: str, base: str, is_admin: bool) -> str:
    link_pedidos = f'<a href="{base}/pedidos">Pedidos</a>' if negocio["tipo_atendimento"] == "pedido" else ""
    nav_admin = '<a href="/admin">← Todos os negócios</a> |' if is_admin else ""

    link_cliente_html = ""
    if is_admin:
        # host/porta ficam a critério de quem está rodando; aqui deixamos relativo
        # e avisamos pra completar com o domínio/túnel público quando for enviar.
        link_cliente_html = f"""
        <div class="card" style="background:#eefbf3; border:1px solid #b6e8c8;">
            <strong>Link para enviar ao lojista (área dele, sem acesso aos outros negócios):</strong><br>
            <code>/loja/{negocio['slug']}</code>
            <p style="color:#666; font-size:12px; margin-top:6px;">
                Complete com o endereço público do seu servidor (ex: com ngrok,
                algo como <code>https://xxxx.ngrok-free.app/loja/{negocio['slug']}</code>)
                antes de mandar pro cliente — sozinho, esse caminho só funciona no seu computador.
            </p>
        </div>
        """

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Painel - {negocio['nome']}</title>
        <style>
            body {{ font-family: -apple-system, Arial, sans-serif; max-width: 900px;
                    margin: 30px auto; padding: 0 20px; color: #222; background: #fafafa; }}
            h1 {{ font-size: 22px; }}
            h2 {{ font-size: 17px; margin-top: 30px; border-bottom: 2px solid #eee; padding-bottom: 6px; }}
            nav a {{ margin-right: 14px; text-decoration: none; color: #0a6; font-weight: 600; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; background: white; }}
            th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 14px; }}
            form.inline {{ display: inline; }}
            .card {{ background: white; padding: 16px 20px; border-radius: 8px;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-top: 12px; }}
            input, select {{ padding: 6px 8px; margin: 4px 6px 4px 0; border: 1px solid #ccc; border-radius: 4px; }}
            button {{ padding: 6px 14px; background: #0a6; color: white; border: none;
                      border-radius: 4px; cursor: pointer; }}
            button.excluir {{ background: #c33; }}
            .form-row {{ margin-bottom: 8px; }}
            code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h1>🏪 {negocio['nome']}</h1>
        <nav>
            {nav_admin}
            <a href="{base}">Catálogo/Serviços</a>
            <a href="{base}/horarios">Horários</a>
            <a href="{base}/faq">FAQ</a>
            {link_pedidos}
        </nav>
        {link_cliente_html}
        {conteudo}
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Dados do negócio + Serviços
# ---------------------------------------------------------------------------

def _render_pagina_servicos(negocio, base, is_admin):
    negocio_id = negocio["id"]
    servicos = db.get_servicos(negocio_id)

    linhas = "".join(
        f"""<tr>
            <td>{s['nome']}</td>
            <td>{s['descricao'] or ''}</td>
            <td>R$ {s['preco']:.2f}</td>
            <td>{s['duracao_minutos']} min</td>
            <td>{'—' if s['estoque'] is None else s['estoque']}</td>
            <td>
                <a href="{base}/servicos/{s['id']}/editar">Editar</a>
                <form class="inline" method="post" action="{base}/servicos/{s['id']}/excluir" style="margin-left:8px;">
                    <button class="excluir" type="submit">Excluir</button>
                </form>
            </td>
        </tr>"""
        for s in servicos
    )

    conteudo = f"""
    <h2>Dados do negócio</h2>
    <div class="card">
        <form method="post" action="{base}/dados">
            <div class="form-row">
                <label>Nome<br><input name="nome" value="{negocio['nome']}" style="width:300px;" required></label>
            </div>
            <div class="form-row">
                <label>Número de WhatsApp<br>
                    <input name="telefone_whatsapp" value="{negocio['telefone_whatsapp'] or ''}"
                           placeholder="Ex: 5547999998888 (DDI+DDD+número, só dígitos)" style="width:300px;">
                </label>
            </div>
            <div class="form-row">
                <label>Descrição da empresa<br>
                    <textarea name="descricao" rows="3" style="width:450px; padding:6px 8px;
                              border:1px solid #ccc; border-radius:4px;"
                    >{negocio['descricao'] or ''}</textarea>
                </label>
            </div>
            <div class="form-row">
                <label>Horário de funcionamento (texto livre, usado na FAQ)<br>
                    <input name="horario_funcionamento" value="{negocio['horario_funcionamento'] or ''}"
                           style="width:300px;">
                </label>
            </div>
            <button type="submit">Salvar dados</button>
        </form>
    </div>

    <h2>Serviços / Produtos</h2>
    <div class="card">
        <table>
            <tr><th>Nome</th><th>Descrição</th><th>Preço</th><th>Duração</th><th>Estoque</th><th></th></tr>
            {linhas or '<tr><td colspan="6">Nenhum serviço cadastrado ainda.</td></tr>'}
        </table>
    </div>

    <h2>Adicionar serviço</h2>
    <div class="card">
        <form method="post" action="{base}/servicos">
            <div class="form-row">
                <input name="nome" placeholder="Nome do serviço" required>
                <input name="descricao" placeholder="Descrição (opcional)">
            </div>
            <div class="form-row">
                <input name="preco" type="number" step="0.01" placeholder="Preço (R$)" required>
                <input name="duracao_minutos" type="number" placeholder="Duração (min)" value="30" required>
                <input name="estoque" type="number" placeholder="Estoque (deixe vazio = ilimitado)">
            </div>
            <button type="submit">Adicionar</button>
        </form>
    </div>
    """
    return layout(negocio, conteudo, base, is_admin)


@app.get("/admin/{negocio_id}", response_class=HTMLResponse)
def admin_pagina_servicos(negocio_id: int):
    negocio = db.get_negocio(negocio_id)
    return _render_pagina_servicos(negocio, f"/admin/{negocio_id}", is_admin=True)


@app.get("/loja/{slug}", response_class=HTMLResponse)
def cliente_pagina_servicos(slug: str):
    negocio = db.get_negocio_by_slug(slug)
    if not negocio:
        return HTMLResponse("Link inválido ou expirado.", status_code=404)
    return _render_pagina_servicos(negocio, f"/loja/{slug}", is_admin=False)


@app.post("/admin/{negocio_id}/dados")
@app.post("/loja/{slug}/dados")
def salvar_dados_negocio(
    negocio_id: int = None,
    slug: str = None,
    nome: str = Form(...),
    telefone_whatsapp: str = Form(""),
    descricao: str = Form(""),
    horario_funcionamento: str = Form(""),
):
    negocio = db.get_negocio(negocio_id) if negocio_id else db.get_negocio_by_slug(slug)
    db.atualizar_negocio(negocio["id"], nome, telefone_whatsapp, descricao, horario_funcionamento)
    base = f"/admin/{negocio_id}" if negocio_id else f"/loja/{slug}"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/servicos")
@app.post("/loja/{slug}/servicos")
def add_servico(
    negocio_id: int = None,
    slug: str = None,
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: float = Form(...),
    duracao_minutos: int = Form(30),
    estoque: str = Form(""),
):
    negocio = db.get_negocio(negocio_id) if negocio_id else db.get_negocio_by_slug(slug)
    estoque_val = int(estoque) if estoque.strip() != "" else None
    db.criar_servico(negocio["id"], nome, descricao, preco, duracao_minutos, estoque_val)
    base = f"/admin/{negocio_id}" if negocio_id else f"/loja/{slug}"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/servicos/{servico_id}/excluir")
@app.post("/loja/{slug}/servicos/{servico_id}/excluir")
def del_servico(servico_id: int, negocio_id: int = None, slug: str = None):
    db.excluir_servico(servico_id)
    base = f"/admin/{negocio_id}" if negocio_id else f"/loja/{slug}"
    return RedirectResponse(base, status_code=303)


def _render_editar_servico(negocio, servico, base, is_admin):
    estoque_atual = "" if servico["estoque"] is None else servico["estoque"]
    conteudo = f"""
    <h2>Editar serviço/produto</h2>
    <div class="card">
        <form method="post" action="{base}/servicos/{servico['id']}/editar">
            <div class="form-row">
                <input name="nome" value="{servico['nome']}" placeholder="Nome" required>
                <input name="descricao" value="{servico['descricao'] or ''}" placeholder="Descrição" style="width:250px;">
            </div>
            <div class="form-row">
                <input name="preco" type="number" step="0.01" value="{servico['preco']}" placeholder="Preço (R$)" required>
                <input name="duracao_minutos" type="number" value="{servico['duracao_minutos']}" placeholder="Duração (min)" required>
                <input name="estoque" type="number" value="{estoque_atual}" placeholder="Estoque (vazio = ilimitado)">
            </div>
            <button type="submit">Salvar alterações</button>
            <a href="{base}" style="margin-left:10px;">Cancelar</a>
        </form>
    </div>
    """
    return layout(negocio, conteudo, base, is_admin)


@app.get("/admin/{negocio_id}/servicos/{servico_id}/editar", response_class=HTMLResponse)
def admin_editar_servico(negocio_id: int, servico_id: int):
    negocio = db.get_negocio(negocio_id)
    servico = db.get_servico(servico_id)
    return _render_editar_servico(negocio, servico, f"/admin/{negocio_id}", is_admin=True)


@app.get("/loja/{slug}/servicos/{servico_id}/editar", response_class=HTMLResponse)
def cliente_editar_servico(slug: str, servico_id: int):
    negocio = db.get_negocio_by_slug(slug)
    if not negocio:
        return HTMLResponse("Link inválido ou expirado.", status_code=404)
    servico = db.get_servico(servico_id)
    return _render_editar_servico(negocio, servico, f"/loja/{slug}", is_admin=False)


@app.post("/admin/{negocio_id}/servicos/{servico_id}/editar")
@app.post("/loja/{slug}/servicos/{servico_id}/editar")
def salvar_edicao_servico(
    servico_id: int,
    negocio_id: int = None,
    slug: str = None,
    nome: str = Form(...),
    descricao: str = Form(""),
    preco: float = Form(...),
    duracao_minutos: int = Form(30),
    estoque: str = Form(""),
):
    estoque_val = int(estoque) if estoque.strip() != "" else None
    db.atualizar_servico(servico_id, nome, descricao, preco, duracao_minutos, estoque_val)
    base = f"/admin/{negocio_id}" if negocio_id else f"/loja/{slug}"
    return RedirectResponse(base, status_code=303)


# ---------------------------------------------------------------------------
# Horários (regras por dia da semana + exceções pontuais)
# ---------------------------------------------------------------------------

def _render_pagina_horarios(negocio, base, is_admin):
    negocio_id = negocio["id"]
    regras = db.get_horario_regras(negocio_id)
    excecoes = db.get_excecoes(negocio_id)

    linhas_regras = "".join(
        f"""<tr>
            <td>{DIAS_SEMANA[r['dia_semana']]}</td>
            <td>{r['hora_inicio']} às {r['hora_fim']}</td>
            <td>
                <form class="inline" method="post" action="{base}/horarios/{r['id']}/excluir">
                    <button class="excluir" type="submit">Excluir</button>
                </form>
            </td>
        </tr>"""
        for r in regras
    )

    opcoes_dias = "".join(f'<option value="{i}">{d}</option>' for i, d in enumerate(DIAS_SEMANA))

    linhas_excecoes = "".join(
        f"""<tr>
            <td>{e['data']}</td>
            <td>{e['motivo'] or ''}</td>
            <td>
                <form class="inline" method="post" action="{base}/excecoes/{e['id']}/excluir">
                    <button class="excluir" type="submit">Excluir</button>
                </form>
            </td>
        </tr>"""
        for e in excecoes
    )

    conteudo = f"""
    <h2>Funcionamento por dia da semana</h2>
    <div class="card">
        <table>
            <tr><th>Dia</th><th>Horário</th><th></th></tr>
            {linhas_regras or '<tr><td colspan="3">Nenhuma regra cadastrada — o bot não vai oferecer horários.</td></tr>'}
        </table>
    </div>

    <h2>Adicionar regra de funcionamento</h2>
    <div class="card">
        <form method="post" action="{base}/horarios">
            <div class="form-row">
                <select name="dia_semana">{opcoes_dias}</select>
                <input name="hora_inicio" type="time" value="09:00" required>
                <input name="hora_fim" type="time" value="18:00" required>
            </div>
            <button type="submit">Adicionar</button>
        </form>
    </div>

    <h2>Fechamentos pontuais (feriado, folga, evento)</h2>
    <div class="card">
        <table>
            <tr><th>Data</th><th>Motivo</th><th></th></tr>
            {linhas_excecoes or '<tr><td colspan="3">Nenhum fechamento cadastrado.</td></tr>'}
        </table>
    </div>

    <h2>Adicionar fechamento</h2>
    <div class="card">
        <form method="post" action="{base}/excecoes">
            <div class="form-row">
                <input name="data" type="date" required>
                <input name="motivo" placeholder="Motivo (ex: Feriado, Folga)">
            </div>
            <button type="submit">Adicionar</button>
        </form>
    </div>
    """
    return layout(negocio, conteudo, base, is_admin)


@app.get("/admin/{negocio_id}/horarios", response_class=HTMLResponse)
def admin_pagina_horarios(negocio_id: int):
    negocio = db.get_negocio(negocio_id)
    return _render_pagina_horarios(negocio, f"/admin/{negocio_id}", is_admin=True)


@app.get("/loja/{slug}/horarios", response_class=HTMLResponse)
def cliente_pagina_horarios(slug: str):
    negocio = db.get_negocio_by_slug(slug)
    if not negocio:
        return HTMLResponse("Link inválido ou expirado.", status_code=404)
    return _render_pagina_horarios(negocio, f"/loja/{slug}", is_admin=False)


@app.post("/admin/{negocio_id}/horarios")
@app.post("/loja/{slug}/horarios")
def add_horario(
    negocio_id: int = None,
    slug: str = None,
    dia_semana: int = Form(...),
    hora_inicio: str = Form(...),
    hora_fim: str = Form(...),
):
    negocio = db.get_negocio(negocio_id) if negocio_id else db.get_negocio_by_slug(slug)
    db.criar_horario_regra(negocio["id"], dia_semana, hora_inicio, hora_fim)
    base = f"/admin/{negocio_id}/horarios" if negocio_id else f"/loja/{slug}/horarios"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/horarios/{regra_id}/excluir")
@app.post("/loja/{slug}/horarios/{regra_id}/excluir")
def del_horario(regra_id: int, negocio_id: int = None, slug: str = None):
    db.excluir_horario_regra(regra_id)
    base = f"/admin/{negocio_id}/horarios" if negocio_id else f"/loja/{slug}/horarios"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/excecoes")
@app.post("/loja/{slug}/excecoes")
def add_excecao(negocio_id: int = None, slug: str = None, data: str = Form(...), motivo: str = Form("")):
    negocio = db.get_negocio(negocio_id) if negocio_id else db.get_negocio_by_slug(slug)
    db.criar_excecao(negocio["id"], data, motivo)
    base = f"/admin/{negocio_id}/horarios" if negocio_id else f"/loja/{slug}/horarios"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/excecoes/{excecao_id}/excluir")
@app.post("/loja/{slug}/excecoes/{excecao_id}/excluir")
def del_excecao(excecao_id: int, negocio_id: int = None, slug: str = None):
    db.excluir_excecao(excecao_id)
    base = f"/admin/{negocio_id}/horarios" if negocio_id else f"/loja/{slug}/horarios"
    return RedirectResponse(base, status_code=303)


# ---------------------------------------------------------------------------
# FAQ
# ---------------------------------------------------------------------------

def _render_pagina_faq(negocio, base, is_admin):
    faqs = db.get_faq(negocio["id"])

    linhas = "".join(
        f"""<tr>
            <td>{f['palavras_chave']}</td>
            <td>{f['resposta']}</td>
            <td>
                <form class="inline" method="post" action="{base}/faq/{f['id']}/excluir">
                    <button class="excluir" type="submit">Excluir</button>
                </form>
            </td>
        </tr>"""
        for f in faqs
    )

    conteudo = f"""
    <h2>Perguntas frequentes (FAQ)</h2>
    <p style="color:#666; font-size:13px;">
        O bot procura por essas palavras-chave na mensagem do cliente. Separe várias
        palavras-chave por vírgula (ex: <em>horario, funcionamento, aberto</em>).
    </p>
    <div class="card">
        <table>
            <tr><th>Palavras-chave</th><th>Resposta</th><th></th></tr>
            {linhas or '<tr><td colspan="3">Nenhuma FAQ cadastrada ainda.</td></tr>'}
        </table>
    </div>

    <h2>Adicionar FAQ</h2>
    <div class="card">
        <form method="post" action="{base}/faq">
            <div class="form-row">
                <input name="palavras_chave" placeholder="palavras, chave, separadas, por, virgula"
                       style="width: 350px;" required>
            </div>
            <div class="form-row">
                <input name="resposta" placeholder="Resposta que o bot vai enviar" style="width: 450px;" required>
            </div>
            <button type="submit">Adicionar</button>
        </form>
    </div>
    """
    return layout(negocio, conteudo, base, is_admin)


@app.get("/admin/{negocio_id}/faq", response_class=HTMLResponse)
def admin_pagina_faq(negocio_id: int):
    negocio = db.get_negocio(negocio_id)
    return _render_pagina_faq(negocio, f"/admin/{negocio_id}", is_admin=True)


@app.get("/loja/{slug}/faq", response_class=HTMLResponse)
def cliente_pagina_faq(slug: str):
    negocio = db.get_negocio_by_slug(slug)
    if not negocio:
        return HTMLResponse("Link inválido ou expirado.", status_code=404)
    return _render_pagina_faq(negocio, f"/loja/{slug}", is_admin=False)


@app.post("/admin/{negocio_id}/faq")
@app.post("/loja/{slug}/faq")
def add_faq(negocio_id: int = None, slug: str = None, palavras_chave: str = Form(...), resposta: str = Form(...)):
    negocio = db.get_negocio(negocio_id) if negocio_id else db.get_negocio_by_slug(slug)
    db.criar_faq(negocio["id"], palavras_chave, resposta)
    base = f"/admin/{negocio_id}/faq" if negocio_id else f"/loja/{slug}/faq"
    return RedirectResponse(base, status_code=303)


@app.post("/admin/{negocio_id}/faq/{faq_id}/excluir")
@app.post("/loja/{slug}/faq/{faq_id}/excluir")
def del_faq(faq_id: int, negocio_id: int = None, slug: str = None):
    db.excluir_faq(faq_id)
    base = f"/admin/{negocio_id}/faq" if negocio_id else f"/loja/{slug}/faq"
    return RedirectResponse(base, status_code=303)


# ---------------------------------------------------------------------------
# Pedidos (negócios do tipo 'pedido')
# ---------------------------------------------------------------------------

def _render_pagina_pedidos(negocio, base, is_admin):
    pedidos = db.listar_pedidos(negocio["id"])

    def opcoes_status(status_atual):
        return "".join(
            f'<option value="{s}" {"selected" if s == status_atual else ""}>{s}</option>'
            for s in STATUS_PEDIDO
        )

    linhas = "".join(
        f"""<tr>
            <td>#{p['id']}</td>
            <td>{p['cliente_nome']}<br><span style="color:#888;font-size:12px;">{p['cliente_telefone']}</span></td>
            <td>{p['produto_nome']}</td>
            <td>{p['quantidade']}</td>
            <td>{p['personalizacao'] or '-'}</td>
            <td>
                {f'<a href="data:{p["imagem_mime_type"] or "image/jpeg"};base64,{p["imagem_base64"]}" target="_blank">'
                 f'<img src="data:{p["imagem_mime_type"] or "image/jpeg"};base64,{p["imagem_base64"]}" '
                 f'style="max-width:70px; max-height:70px; border-radius:6px; display:block;"></a>'
                 if p.get('imagem_base64') else '-'}
            </td>
            <td>R$ {p['produto_preco'] * p['quantidade']:.2f}</td>
            <td>{p['criado_em']}</td>
            <td>
                <form class="inline" method="post" action="{base}/pedidos/{p['id']}/status">
                    <select name="status" onchange="this.form.submit()">
                        {opcoes_status(p['status'])}
                    </select>
                </form>
            </td>
        </tr>"""
        for p in pedidos
    )

    conteudo = f"""
    <h2>Pedidos recebidos</h2>
    <div class="card">
        <table>
            <tr>
                <th>#</th><th>Cliente</th><th>Produto</th><th>Qtd</th>
                <th>Personalização</th><th>Foto</th><th>Total</th><th>Recebido em</th><th>Status</th>
            </tr>
            {linhas or '<tr><td colspan="9">Nenhum pedido recebido ainda.</td></tr>'}
        </table>
    </div>
    """
    return layout(negocio, conteudo, base, is_admin)


@app.get("/admin/{negocio_id}/pedidos", response_class=HTMLResponse)
def admin_pagina_pedidos(negocio_id: int):
    negocio = db.get_negocio(negocio_id)
    return _render_pagina_pedidos(negocio, f"/admin/{negocio_id}", is_admin=True)


@app.get("/loja/{slug}/pedidos", response_class=HTMLResponse)
def cliente_pagina_pedidos(slug: str):
    negocio = db.get_negocio_by_slug(slug)
    if not negocio:
        return HTMLResponse("Link inválido ou expirado.", status_code=404)
    return _render_pagina_pedidos(negocio, f"/loja/{slug}", is_admin=False)


@app.post("/admin/{negocio_id}/pedidos/{pedido_id}/status")
@app.post("/loja/{slug}/pedidos/{pedido_id}/status")
def mudar_status_pedido(pedido_id: int, negocio_id: int = None, slug: str = None, status: str = Form(...)):
    db.atualizar_status_pedido(pedido_id, status)
    base = f"/admin/{negocio_id}/pedidos" if negocio_id else f"/loja/{slug}/pedidos"
    return RedirectResponse(base, status_code=303)


@app.on_event("startup")
def startup():
    db.init_db()
    db.seed_cafune()


# ---------------------------------------------------------------------------
# Webhook do WhatsApp (Meta Cloud API)
# ---------------------------------------------------------------------------

@app.get("/webhook")
def whatsapp_verificar(request: Request):
    """A Meta chama isso UMA VEZ quando você configura a URL do webhook,
    pra confirmar que o endereço é seu de verdade."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    resultado = wa.processar_verificacao(mode, token, challenge)
    if resultado is not None:
        return PlainTextResponse(resultado)
    return PlainTextResponse("Token de verificação inválido.", status_code=403)


@app.post("/webhook")
async def whatsapp_receber(request: Request):
    """A Meta chama isso toda vez que alguém manda mensagem pro número."""
    payload = await request.json()
    wa.processar_mensagem_recebida(payload)
    return JSONResponse({"status": "ok"})


@app.get("/webhook/status", response_class=HTMLResponse)
def whatsapp_status():
    """Tela simples de diagnóstico: mostra quais variáveis de ambiente
    ainda faltam configurar, sem nunca exibir o valor delas."""
    faltando = wa.verificar_configuracao()
    if faltando:
        itens = "".join(f"<li><code>{v}</code></li>" for v in faltando)
        corpo = f"""
        <p style="color:#c33;"><strong>Faltam configurar estas variáveis de ambiente:</strong></p>
        <ul>{itens}</ul>
        """
    else:
        corpo = '<p style="color:#0a6;"><strong>✅ Todas as variáveis de ambiente estão configuradas.</strong></p>'

    return f"""
    <html><head><meta charset="utf-8"><title>Status do Webhook</title>
    <style>body {{ font-family: -apple-system, Arial, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; }}
    code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; }}</style>
    </head><body>
        <h1>Status do Webhook WhatsApp</h1>
        {corpo}
        <p style="color:#666; font-size:13px;">Essa página nunca mostra os valores, só se existem ou não.</p>
    </body></html>
    """
