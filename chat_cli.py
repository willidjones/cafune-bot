"""
chat_cli.py - Simula uma conversa de WhatsApp no terminal.

Roda com: python -m chat_cli  (ou: python chat_cli.py)

Isso representa exatamente o que vai acontecer quando o webhook da
Cloud API receber uma mensagem: chama process_message() e devolve a
resposta pro cliente. Aqui, "cliente" é você digitando no terminal.
"""

import db
import bot

CLIENTE_TELEFONE_TESTE = "5547988887777"  # simula o número de quem está mandando mensagem


def escolher_negocio():
    negocios = db.listar_negocios()
    print("Negócios disponíveis para teste:\n")
    for i, n in enumerate(negocios):
        print(f"  {i+1}. {n['nome']}  (tipo: {n['tipo_atendimento']})")
    print()

    while True:
        escolha = input("Escolha o número do negócio: ").strip()
        if escolha.isdigit() and 1 <= int(escolha) <= len(negocios):
            return negocios[int(escolha) - 1]
        print("Opção inválida, tente de novo.")


def main():
    db.init_db()
    db.seed_demo_data()   # Studio Bella Hair (agendamento)
    db.seed_cafune()      # Personalizados Cafuné (pedido)

    negocio = escolher_negocio()

    print("=" * 60)
    print(f"  SIMULADOR DE WHATSAPP - {negocio['nome']}")
    print("  Digite como se fosse o cliente. Ctrl+C para sair.")
    print("=" * 60)
    print()

    while True:
        try:
            texto = input("Você (cliente): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\nConversa encerrada.")
            break

        if texto.lower() in ("sair", "exit", "quit"):
            print("\nConversa encerrada.")
            break

        resposta = bot.process_message(negocio["id"], CLIENTE_TELEFONE_TESTE, texto)
        print(f"\n🤖 Bot: {resposta}\n")


if __name__ == "__main__":
    main()
