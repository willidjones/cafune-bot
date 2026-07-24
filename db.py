"""
db.py - Camada de dados do chatbot para pequenas empresas.

Arquitetura multi-tenant: cada "negócio" (lojista) tem seu próprio conjunto
de produtos/serviços, FAQ e horários de agendamento, identificado por
`negocio_id`.

Usa PostgreSQL (via variável de ambiente DATABASE_URL) em vez de SQLite,
para os dados sobreviverem a cada novo deploy — SQLite num serviço web
comum perde tudo a cada reimplantação, já que o disco não é permanente.

O restante do projeto (bot.py, admin_app.py, webhook_whatsapp.py) chama
get_conn() e usa conn.execute(...) com placeholders '?', exatamente como
fazia com sqlite3. O adaptador abaixo (_ConnWrapper) traduz isso pra
psycopg2 por baixo dos panos, então essas outras partes não precisam
saber que o banco mudou.
"""

import os
import secrets
from datetime import datetime, timedelta

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")


class _TranslatingCursor(psycopg2.extras.RealDictCursor):
    """Cursor que aceita queries escritas com '?' (estilo sqlite3) e
    traduz pra '%s' (estilo psycopg2) antes de executar. Também devolve
    linhas com acesso por chave (row['coluna']), igual sqlite3.Row."""

    def execute(self, query, params=None):
        if params is None:
            params = ()
        return super().execute(query.replace("?", "%s"), params)


class _ConnWrapper:
    """Faz uma conexão psycopg2 se comportar o suficiente como uma conexão
    sqlite3: permite conn.execute(...) encadeado com .fetchall()/.fetchone(),
    e usa sempre o _TranslatingCursor."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, query, params=()):
        cur = self._conn.cursor(cursor_factory=_TranslatingCursor)
        cur.execute(query, params)
        return cur

    def cursor(self):
        return self._conn.cursor(cursor_factory=_TranslatingCursor)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL não configurada. Defina essa variável de ambiente "
            "com a connection string do PostgreSQL (ex: do Neon)."
        )
    pg_conn = psycopg2.connect(DATABASE_URL)
    return _ConnWrapper(pg_conn)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS negocios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        telefone_whatsapp TEXT UNIQUE,
        horario_funcionamento TEXT,
        tipo_atendimento TEXT NOT NULL DEFAULT 'agendamento',
        descricao TEXT,
        slug TEXT UNIQUE
    );

    CREATE TABLE IF NOT EXISTS produtos_servicos (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        nome TEXT NOT NULL,
        descricao TEXT,
        preco REAL,
        duracao_minutos INTEGER DEFAULT 30,
        estoque INTEGER  -- NULL = não controla estoque (ilimitado); número = quantidade disponível
    );

    CREATE TABLE IF NOT EXISTS faq (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        palavras_chave TEXT NOT NULL,
        resposta TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS agendamentos (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        cliente_telefone TEXT NOT NULL,
        cliente_nome TEXT,
        produto_servico_id INTEGER NOT NULL REFERENCES produtos_servicos(id),
        data_hora TEXT NOT NULL,
        status TEXT DEFAULT 'confirmado',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP(0)
    );

    CREATE TABLE IF NOT EXISTS horario_regras (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        dia_semana INTEGER NOT NULL,
        hora_inicio TEXT NOT NULL,
        hora_fim TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS horario_excecoes (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        data TEXT NOT NULL,
        motivo TEXT
    );

    CREATE TABLE IF NOT EXISTS pedidos (
        id SERIAL PRIMARY KEY,
        negocio_id INTEGER NOT NULL REFERENCES negocios(id),
        cliente_telefone TEXT NOT NULL,
        cliente_nome TEXT,
        produto_servico_id INTEGER NOT NULL REFERENCES produtos_servicos(id),
        quantidade INTEGER DEFAULT 1,
        personalizacao TEXT,
        imagem_base64 TEXT,
        imagem_mime_type TEXT,
        status TEXT DEFAULT 'novo',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP(0)
    );
    """)

    conn.commit()
    _migrar_schema(conn)
    conn.commit()
    conn.close()


def _migrar_schema(conn):
    """
    Adiciona colunas novas em bancos já existentes. O Postgres já suporta
    'ADD COLUMN IF NOT EXISTS' nativamente, então não precisamos checar
    manualmente se a coluna existe antes (diferente do SQLite).
    """
    cur = conn.cursor()
    cur.execute("ALTER TABLE negocios ADD COLUMN IF NOT EXISTS tipo_atendimento TEXT NOT NULL DEFAULT 'agendamento'")
    cur.execute("ALTER TABLE negocios ADD COLUMN IF NOT EXISTS descricao TEXT")
    cur.execute("ALTER TABLE negocios ADD COLUMN IF NOT EXISTS slug TEXT")
    cur.execute("ALTER TABLE produtos_servicos ADD COLUMN IF NOT EXISTS estoque INTEGER")
    cur.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS imagem_base64 TEXT")
    cur.execute("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS imagem_mime_type TEXT")

    # Garante que todo negócio (novo ou antigo) tenha um slug de acesso.
    cur.execute("SELECT id FROM negocios WHERE slug IS NULL OR slug = ''")
    sem_slug = cur.fetchall()
    for row in sem_slug:
        novo_slug = secrets.token_urlsafe(8)
        cur.execute("UPDATE negocios SET slug = ? WHERE id = ?", (novo_slug, row["id"]))


def seed_demo_data():
    """Popula um negócio fictício: um salão de beleza simples, pra testar o fluxo."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as c FROM negocios")
    if cur.fetchone()["c"] > 0:
        conn.close()
        return  # já tem dados, não duplica

    cur.execute(
        "INSERT INTO negocios (nome, telefone_whatsapp, horario_funcionamento, slug) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        ("Studio Bella Hair", "5547999990000", "Seg a Sáb, 9h às 19h", secrets.token_urlsafe(8)),
    )
    negocio_id = cur.fetchone()["id"]

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

    for dia in range(0, 5):
        cur.execute(
            "INSERT INTO horario_regras (negocio_id, dia_semana, hora_inicio, hora_fim) VALUES (?, ?, ?, ?)",
            (negocio_id, dia, "09:00", "19:00"),
        )
    cur.execute(
        "INSERT INTO horario_regras (negocio_id, dia_semana, hora_inicio, hora_fim) VALUES (?, ?, ?, ?)",
        (negocio_id, 5, "09:00", "13:00"),
    )

    conn.commit()
    conn.close()
    return negocio_id


def seed_cafune():
    """
    Popula a Personalizados Cafuné como negócio real, tipo 'pedido'.
    Slug fixo ('personalizados-cafune') de propósito, pra sobreviver a
    qualquer reset/recriação do banco sem quebrar o link já configurado
    na variável WHATSAPP_NEGOCIO_SLUG.
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
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        ("Personalizados Cafuné", "5547900000000", "Seg a Sex, 9h às 18h", "pedido", "personalizados-cafune"),
    )
    negocio_id = cur.fetchone()["id"]

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


def get_negocio_by_telefone(telefone: str):
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
            continue

        dia_semana = dia.weekday()
        regras_do_dia = regras_por_dia.get(dia_semana, [])

        for regra in regras_do_dia:
            hora_ini = int(regra["hora_inicio"].split(":")[0])
            hora_fim = int(regra["hora_fim"].split(":")[0])
            for hora in range(hora_ini, hora_fim):
                slot = dia.replace(hour=hora, minute=0, second=0, microsecond=0)
                slot_str = slot.strftime("%Y-%m-%d %H:%M")
                if slot_str not in ocupados_set:
                    disponiveis.append(slot_str)

    return disponiveis[:6]


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
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (nome, telefone_whatsapp, horario_funcionamento, tipo_atendimento, slug),
    )
    novo_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return novo_id


def get_negocio_by_slug(slug: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM negocios WHERE slug = ?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def excluir_negocio(negocio_id: int):
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


def atualizar_servico(servico_id, nome, descricao, preco, duracao_minutos, estoque=None):
    conn = get_conn()
    conn.execute(
        "UPDATE produtos_servicos SET nome = ?, descricao = ?, preco = ?, duracao_minutos = ?, "
        "estoque = ? WHERE id = ?",
        (nome, descricao, preco, duracao_minutos, estoque, servico_id),
    )
    conn.commit()
    conn.close()


def get_servico(servico_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM produtos_servicos WHERE id = ?", (servico_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def criar_servico(negocio_id, nome, descricao, preco, duracao_minutos, estoque=None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO produtos_servicos (negocio_id, nome, descricao, preco, duracao_minutos, estoque) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (negocio_id, nome, descricao, preco, duracao_minutos, estoque),
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


def criar_pedido(negocio_id, cliente_telefone, cliente_nome, produto_servico_id, quantidade,
                  personalizacao, imagem_base64=None, imagem_mime_type=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pedidos (negocio_id, cliente_telefone, cliente_nome, produto_servico_id, "
        "quantidade, personalizacao, imagem_base64, imagem_mime_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (negocio_id, cliente_telefone, cliente_nome, produto_servico_id, quantidade,
         personalizacao, imagem_base64, imagem_mime_type),
    )
    # Baixa automática no estoque — só afeta produtos com controle de estoque (estoque não-nulo).
    # Pode ficar negativo de propósito: é o sinal de que vendeu mais do que tinha disponível.
    cur.execute(
        "UPDATE produtos_servicos SET estoque = estoque - ? WHERE id = ? AND estoque IS NOT NULL",
        (quantidade, produto_servico_id),
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
