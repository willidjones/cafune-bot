"""
db.py - Camada de dados do protótipo de chatbot para pequenas empresas.

Arquitetura multi-tenant: cada "negócio" (lojista) tem seu próprio conjunto
de produtos/serviços, FAQ e horários de agendamento, identificado por
`negocio_id`. No mundo real, `negocio_id` seria amarrado ao número de
telefone da Cloud API que recebeu a mensagem.
"""

import sqlite3
import secrets
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).parent / "bot.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS negocios (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        telefone_whatsapp TEXT UNIQUE,
        horario_funcionamento TEXT,
        tipo_atendimento TEXT NOT NULL DEFAULT 'agendamento',  -- 'agendamento' ou 'pedido'
        descricao TEXT,
        slug TEXT UNIQUE  -- link secreto que dá acesso ao painel do cliente (/loja/{slug})
    );

    CREATE TABLE IF NOT EXISTS produtos_servicos (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        nome TEXT NOT NULL,
        descricao TEXT,
        preco REAL,
        duracao_minutos INTEGER DEFAULT 30,  -- usado para agendamento
        FOREIGN KEY (negocio_id) REFERENCES negocios(id)
    );

    CREATE TABLE IF NOT EXISTS faq (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        palavras_chave TEXT NOT NULL,  -- separadas por vírgula
        resposta TEXT NOT NULL,
        FOREIGN KEY (negocio_id) REFERENCES negocios(id)
    );

    CREATE TABLE IF NOT EXISTS agendamentos (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        cliente_telefone TEXT NOT NULL,
        cliente_nome TEXT,
        produto_servico_id INTEGER NOT NULL,
        data_hora TEXT NOT NULL,
        status TEXT DEFAULT 'confirmado',
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (negocio_id) REFERENCES negocios(id),
        FOREIGN KEY (produto_servico_id) REFERENCES produtos_servicos(id)
    );

    -- Regra de funcionamento por dia da semana (0=segunda ... 6=domingo).
    -- Um negócio pode ter múltiplas regras (ex: manhã e tarde separadas).
    CREATE TABLE IF NOT EXISTS horario_regras (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        dia_semana INTEGER NOT NULL,
        hora_inicio TEXT NOT NULL,
        hora_fim TEXT NOT NULL,
        FOREIGN KEY (negocio_id) REFERENCES negocios(id)
    );

    -- Exceções pontuais: feriado, folga, evento — sobrepõe a regra padrão
    -- pra uma data específica.
    CREATE TABLE IF NOT EXISTS horario_excecoes (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        motivo TEXT,
        FOREIGN KEY (negocio_id) REFERENCES negocios(id)
    );

    -- Pedidos/encomendas capturados pelo bot (usado por negócios do tipo 'pedido',
    -- ex: produtos personalizados sob encomenda).
    CREATE TABLE IF NOT EXISTS pedidos (
        id INTEGER PRIMARY KEY,
        negocio_id INTEGER NOT NULL,
        cliente_telefone TEXT NOT NULL,
        cliente_nome TEXT,
        produto_servico_id INTEGER NOT NULL,
        quantidade INTEGER DEFAULT 1,
        personalizacao TEXT,
        status TEXT DEFAULT 'novo',  -- novo, em produção, pronto, entregue, cancelado
        criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (negocio_id) REFERENCES negocios(id),
        FOREIGN KEY (produto_servico_id) REFERENCES produtos_servicos(id)
    );
    """)

    conn.commit()
    _migrar_schema(conn)
    conn.commit()
    conn.close()


def _migrar_schema(conn):
    """
    Adiciona colunas novas em bancos já existentes, criados por versões
    anteriores deste script. SQLite não faz isso sozinho quando o
    CREATE TABLE muda — só roda a criação se a tabela ainda não existe.
    Sempre que uma coluna nova for adicionada ao schema, o ajuste também
    deve entrar aqui, senão bancos antigos ficam desatualizados.
    """
    cur = conn.cursor()
    colunas_negocios = {row[1] for row in cur.execute("PRAGMA table_info(negocios)")}
    if "tipo_atendimento" not in colunas_negocios:
        cur.execute(
            "ALTER TABLE negocios ADD COLUMN tipo_atendimento TEXT NOT NULL DEFAULT 'agendamento'"
        )
    if "descricao" not in colunas_negocios:
        cur.execute("ALTER TABLE negocios ADD COLUMN descricao TEXT")
    if "slug" not in colunas_negocios:
        cur.execute("ALTER TABLE negocios ADD COLUMN slug TEXT")

    # Garante que todo negócio (novo ou antigo) tenha um slug de acesso.
    sem_slug = cur.execute("SELECT id FROM negocios WHERE slug IS NULL OR slug = ''").fetchall()
    for row in sem_slug:
        novo_slug = secrets.token_urlsafe(8)
        cur.execute("UPDATE negocios SET slug = ? WHERE id = ?", (novo_slug, row[0]))


def seed_demo_data():
    """Popula um negócio fictício: um salão de beleza simples, pra testar o fluxo."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM negocios")
    if cur.fetchone()["c"] > 0:
        conn.close()
        return  # já tem dados, não duplica

    cur.execute(
        "INSERT INTO negocios (nome, telefone_whatsapp, horario_funcionamento, slug) VALUES (?, ?, ?, ?)",
        ("Studio Bella Hair", "5547999990000", "Seg a Sáb, 9h às 19h", secrets.token_urlsafe(8)),
    )
    negocio_id = cur.lastrowid

    servicos = [
        ("Corte feminino", "Corte + escova", 80.0, 60),
        ("Corte masculino", "Corte + acabamento", 45.0, 30),
        ("Coloração", "Coloração completa", 150.0, 120),
        ("Escova", "Escova modeladora", 50.0, 45),
    ]
    for nome, desc, preco, duracao in servicos:
        cur.execute(
            "INSERT INTO produtos_servicos (negocio_id, nome, descricao, preco, duracao_minutos) "
            "VALUES (?, ?, ?, ?, ?)",
            (negocio_id, nome, desc, preco, duracao),
        )

    faqs = [
        ("horario,funcionamento,aberto,abre,fecha", "Funcionamos de Seg a Sáb, das 9h às 19h."),
        ("endereco,onde,localizacao,fica", "Ficamos na Rua das Flores, 123 - Centro, Joinville/SC."),
        ("pagamento,pix,cartao,dinheiro", "Aceitamos Pix, cartão de débito/crédito e dinheiro."),
        ("estacionamento,vaga", "Temos vagas gratuitas em frente ao salão."),
    ]
    for palavras, resposta in faqs:
        cur.execute(
            "INSERT INTO faq (negocio_id, palavras_chave, resposta) VALUES (?, ?, ?)",
            (negocio_id, palavras, resposta),
        )

    # Seg a Sex: 9h-19h | Sábado: 9h-13h | Domingo: fechado (sem regra)
    for dia in range(0, 5):  # 0=segunda ... 4=sexta
        cur.execute(
            "INSERT INTO horario_regras (negocio_id, dia_semana, hora_inicio, hora_fim) VALUES (?, ?, ?, ?)",
            (negocio_id, dia, "09:00", "19:00"),
        )
    cur.execute(
        "INSERT INTO horario_regras (negocio_id, dia_semana, hora_inicio, hora_fim) VALUES (?, ?, ?, ?)",
        (negocio_id, 5, "09:00", "13:00"),  # 5 = sábado
    )

    conn.commit()
    conn.close()
    return negocio_id


def seed_cafune():
    """
    Popula a Personalizados Cafuné como negócio real, tipo 'pedido'.
    Os produtos abaixo são só ponto de partida — edite tudo pelo painel
    (/admin/{id}) com os itens e preços reais.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM negocios WHERE nome = ?", ("Personalizados Cafuné",))
    existente = cur.fetchone()
    if existente:
        conn.close()
        return existente["id"]

    cur.execute(
        "INSERT INTO negocios (nome, telefone_whatsapp, horario_funcionamento, tipo_atendimento, slug) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Personalizados Cafuné", "5547900000000", "Seg a Sex, 9h às 18h", "pedido", "personalizados-cafune"),
    )
    negocio_id = cur.lastrowid

    produtos = [
        ("Caneca personalizada", "Caneca de porcelana com foto/texto à sua escolha", 39.90, 0),
        ("Quadro personalizado", "Quadro decorativo com foto e moldura", 79.90, 0),
        ("Camiseta personalizada", "Camiseta 100% algodão com estampa personalizada", 49.90, 0),
        ("Kit lembrancinha", "Kit de lembrancinhas personalizadas (mín. 10 un.)", 8.50, 0),
    ]
    for nome, desc, preco, duracao in produtos:
        cur.execute(
            "INSERT INTO produtos_servicos (negocio_id, nome, descricao, preco, duracao_minutos) "
            "VALUES (?, ?, ?, ?, ?)",
            (negocio_id, nome, desc, preco, duracao),
        )

    faqs = [
        ("prazo,entrega,demora,quando fica pronto", "O prazo médio de produção é de 3 a 5 dias úteis após confirmação do pedido."),
        ("pagamento,pix,cartao,dinheiro", "Aceitamos Pix e cartão. O pagamento é combinado após a confirmação do pedido."),
        ("entrega,retirada,frete,envio", "Fazemos retirada no ateliê ou envio pelos Correios (frete à parte, consultar prazo)."),
        ("horario,funcionamento,aberto", "Atendemos de Segunda a Sexta, das 9h às 18h."),
    ]
    for palavras, resposta in faqs:
        cur.execute(
            "INSERT INTO faq (negocio_id, palavras_chave, resposta) VALUES (?, ?, ?)",
            (negocio_id, palavras, resposta),
        )

    conn.commit()
    conn.close()
    return negocio_id
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM negocios WHERE telefone_whatsapp = ?", (telefone,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_servicos(negocio_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM produtos_servicos WHERE negocio_id = ? ORDER BY id", (negocio_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_faq(negocio_id: int):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM faq WHERE negocio_id = ?", (negocio_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_horario_regras(negocio_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM horario_regras WHERE negocio_id = ? ORDER BY dia_semana, hora_inicio",
        (negocio_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_excecoes(negocio_id: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM horario_excecoes WHERE negocio_id = ? ORDER BY data", (negocio_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def horarios_disponiveis(negocio_id: int, produto_servico_id: int, dias_a_frente: int = 7):
    """
    Gera horários disponíveis respeitando as regras de funcionamento por dia
    da semana (`horario_regras`) e excluindo datas com exceção
    (`horario_excecoes`, ex: feriado/folga) e horários já ocupados.
    """
    conn = get_conn()
    ocupados = conn.execute(
        "SELECT data_hora FROM agendamentos WHERE negocio_id = ? AND status = 'confirmado'",
        (negocio_id,),
    ).fetchall()
    ocupados_set = {row["data_hora"] for row in ocupados}

    regras = conn.execute(
        "SELECT * FROM horario_regras WHERE negocio_id = ?", (negocio_id,)
    ).fetchall()
    regras_por_dia = {}
    for r in regras:
        regras_por_dia.setdefault(r["dia_semana"], []).append(r)

    excecoes = conn.execute(
        "SELECT data FROM horario_excecoes WHERE negocio_id = ?", (negocio_id,)
    ).fetchall()
    datas_fechadas = {row["data"] for row in excecoes}
    conn.close()

    disponiveis = []
    agora = datetime.now()
    for d in range(1, dias_a_frente + 1):
        dia = agora + timedelta(days=d)
        dia_str = dia.strftime("%Y-%m-%d")
        if dia_str in datas_fechadas:
            continue  # feriado/folga cadastrada

        dia_semana = dia.weekday()  # 0=segunda ... 6=domingo
        regras_do_dia = regras_por_dia.get(dia_semana, [])

        for regra in regras_do_dia:
            hora_ini = int(regra["hora_inicio"].split(":")[0])
            hora_fim = int(regra["hora_fim"].split(":")[0])
            for hora in range(hora_ini, hora_fim):
                slot = dia.replace(hour=hora, minute=0, second=0, microsecond=0)
                slot_str = slot.strftime("%Y-%m-%d %H:%M")
                if slot_str not in ocupados_set:
                    disponiveis.append(slot_str)

    return disponiveis[:6]  # mostra só os 6 primeiros pra não poluir o chat


def criar_agendamento(negocio_id, cliente_telefone, cliente_nome, produto_servico_id, data_hora):
    conn = get_conn()
    conn.execute(
        "INSERT INTO agendamentos (negocio_id, cliente_telefone, cliente_nome, "
        "produto_servico_id, data_hora) VALUES (?, ?, ?, ?, ?)",
        (negocio_id, cliente_telefone, cliente_nome, produto_servico_id, data_hora),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# CRUD usado pelo painel administrativo (admin_app.py)
# ---------------------------------------------------------------------------

def listar_negocios():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM negocios ORDER BY nome").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_negocio(negocio_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM negocios WHERE id = ?", (negocio_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def criar_negocio(nome, telefone_whatsapp, horario_funcionamento="", tipo_atendimento="agendamento"):
    slug = secrets.token_urlsafe(8)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO negocios (nome, telefone_whatsapp, horario_funcionamento, tipo_atendimento, slug) "
        "VALUES (?, ?, ?, ?, ?)",
        (nome, telefone_whatsapp, horario_funcionamento, tipo_atendimento, slug),
    )
    conn.commit()
    novo_id = cur.lastrowid
    conn.close()
    return novo_id


def get_negocio_by_slug(slug: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM negocios WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def excluir_negocio(negocio_id: int):
    """Remove o negócio e tudo que depende dele (cascata manual, já que
    SQLite não força FK por padrão em todas as conexões)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM agendamentos WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM pedidos WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM horario_excecoes WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM horario_regras WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM faq WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM produtos_servicos WHERE negocio_id = ?", (negocio_id,))
    cur.execute("DELETE FROM negocios WHERE id = ?", (negocio_id,))
    conn.commit()
    conn.close()


def atualizar_negocio(negocio_id, nome, telefone_whatsapp, descricao, horario_funcionamento):
    conn = get_conn()
    conn.execute(
        "UPDATE negocios SET nome = ?, telefone_whatsapp = ?, descricao = ?, "
        "horario_funcionamento = ? WHERE id = ?",
        (nome, telefone_whatsapp or None, descricao, horario_funcionamento, negocio_id),
    )
    conn.commit()
    conn.close()


def atualizar_servico(servico_id, nome, descricao, preco, duracao_minutos):
    conn = get_conn()
    conn.execute(
        "UPDATE produtos_servicos SET nome = ?, descricao = ?, preco = ?, duracao_minutos = ? "
        "WHERE id = ?",
        (nome, descricao, preco, duracao_minutos, servico_id),
    )
    conn.commit()
    conn.close()


def get_servico(servico_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM produtos_servicos WHERE id = ?", (servico_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def criar_servico(negocio_id, nome, descricao, preco, duracao_minutos):
    conn = get_conn()
    conn.execute(
        "INSERT INTO produtos_servicos (negocio_id, nome, descricao, preco, duracao_minutos) "
        "VALUES (?, ?, ?, ?, ?)",
        (negocio_id, nome, descricao, preco, duracao_minutos),
    )
    conn.commit()
    conn.close()


def excluir_servico(servico_id):
    conn = get_conn()
    conn.execute("DELETE FROM produtos_servicos WHERE id = ?", (servico_id,))
    conn.commit()
    conn.close()


def criar_faq(negocio_id, palavras_chave, resposta):
    conn = get_conn()
    conn.execute(
        "INSERT INTO faq (negocio_id, palavras_chave, resposta) VALUES (?, ?, ?)",
        (negocio_id, palavras_chave, resposta),
    )
    conn.commit()
    conn.close()


def excluir_faq(faq_id):
    conn = get_conn()
    conn.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
    conn.commit()
    conn.close()


def criar_horario_regra(negocio_id, dia_semana, hora_inicio, hora_fim):
    conn = get_conn()
    conn.execute(
        "INSERT INTO horario_regras (negocio_id, dia_semana, hora_inicio, hora_fim) VALUES (?, ?, ?, ?)",
        (negocio_id, dia_semana, hora_inicio, hora_fim),
    )
    conn.commit()
    conn.close()


def excluir_horario_regra(regra_id):
    conn = get_conn()
    conn.execute("DELETE FROM horario_regras WHERE id = ?", (regra_id,))
    conn.commit()
    conn.close()


def criar_excecao(negocio_id, data, motivo):
    conn = get_conn()
    conn.execute(
        "INSERT INTO horario_excecoes (negocio_id, data, motivo) VALUES (?, ?, ?)",
        (negocio_id, data, motivo),
    )
    conn.commit()
    conn.close()


def excluir_excecao(excecao_id):
    conn = get_conn()
    conn.execute("DELETE FROM horario_excecoes WHERE id = ?", (excecao_id,))
    conn.commit()
    conn.close()


def criar_pedido(negocio_id, cliente_telefone, cliente_nome, produto_servico_id, quantidade, personalizacao):
    conn = get_conn()
    conn.execute(
        "INSERT INTO pedidos (negocio_id, cliente_telefone, cliente_nome, produto_servico_id, "
        "quantidade, personalizacao) VALUES (?, ?, ?, ?, ?, ?)",
        (negocio_id, cliente_telefone, cliente_nome, produto_servico_id, quantidade, personalizacao),
    )
    conn.commit()
    conn.close()


def listar_pedidos(negocio_id: int):
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*, ps.nome as produto_nome, ps.preco as produto_preco
           FROM pedidos p
           JOIN produtos_servicos ps ON p.produto_servico_id = ps.id
           WHERE p.negocio_id = ?
           ORDER BY p.criado_em DESC""",
        (negocio_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def atualizar_status_pedido(pedido_id, novo_status):
    conn = get_conn()
    conn.execute("UPDATE pedidos SET status = ? WHERE id = ?", (novo_status, pedido_id))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    nid = seed_demo_data()
    print(f"Banco inicializado. negocio_id de exemplo: {nid or '(já existia)'}")
