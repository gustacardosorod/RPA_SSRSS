from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Dict, List

from common import VERSAO_APP, salvar_log
from etl_agent_contact_diario import processar_agent_contact
from etl_css_atendente import processar_css_atendente
from etl_fsr_tratado import processar_fsr
from etl_indicadores_gerais import processar_indicadores_gerais
from etl_volume_fila_diario import processar_volume_fila


def resolver_pastas(args) -> Dict[str, Path]:
    base_dir = Path(args.base_dir).resolve() if args.base_dir else Path(__file__).resolve().parent
    entrada = Path(args.entrada).resolve() if args.entrada else base_dir / "entrada"
    entrada_fsr = Path(args.entrada_fsr).resolve() if args.entrada_fsr else base_dir / "entrada_fsr"
    saida = Path(args.saida).resolve() if args.saida else base_dir / "saida"
    logs = Path(args.logs).resolve() if args.logs else base_dir / "LOGS"
    saida.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    return {"base": base_dir, "entrada": entrada, "entrada_fsr": entrada_fsr, "saida": saida, "logs": logs}


def executar_contact(pastas: Dict[str, Path], substituir: bool, reprocessar_tudo: bool) -> List[dict]:
    logs = []
    print("\n=== CARGA CONTACT CENTER ===")
    _, log = processar_agent_contact(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)
    _, log = processar_css_atendente(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)
    _, log = processar_volume_fila(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)
    _, log = processar_indicadores_gerais(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)
    return logs


def executar_tudo(pastas: Dict[str, Path], substituir: bool, reprocessar_tudo: bool) -> None:
    logs = executar_contact(pastas, substituir, reprocessar_tudo)
    print("\n=== CARGA FSR / SAP SERVICE ===")
    _, log = processar_fsr(pastas["entrada_fsr"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)
    salvar_log(logs, pastas["logs"], "log_carga_total.csv")


def executar_carga(carga: str, pastas: Dict[str, Path], substituir: bool, reprocessar_tudo: bool) -> None:
    carga = carga.lower().strip()
    if carga == "tudo":
        executar_tudo(pastas, substituir, reprocessar_tudo)
    elif carga == "contact":
        logs = executar_contact(pastas, substituir, reprocessar_tudo)
        salvar_log(logs, pastas["logs"], "log_carga_contact.csv")
    elif carga == "agent":
        processar_agent_contact(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    elif carga == "css":
        processar_css_atendente(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    elif carga == "fsr":
        processar_fsr(pastas["entrada_fsr"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    elif carga == "indicadores":
        processar_indicadores_gerais(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    elif carga == "volume_fila":
        processar_volume_fila(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    else:
        raise ValueError(f"Carga inválida: {carga}")


def perguntar_bool(texto: str, padrao: bool = False) -> bool:
    sufixo = "[S/n]" if padrao else "[s/N]"
    resposta = input(f"{texto} {sufixo}: ").strip().lower()
    if not resposta:
        return padrao
    return resposta in ["s", "sim", "y", "yes"]


def menu_interativo(pastas: Dict[str, Path]) -> None:
    while True:
        print("\n" + "=" * 60)
        print(f"RPA SSRS - Menu de Cargas | Versão {VERSAO_APP}")
        print("=" * 60)
        print(f"Base:        {pastas['base']}")
        print(f"Entrada:     {pastas['entrada']}")
        print(f"Entrada FSR: {pastas['entrada_fsr']}")
        print(f"Saída:       {pastas['saida']}")
        print(f"Logs:        {pastas['logs']}")
        print("\nEscolha a carga:")
        print("1 - Carga TOTAL incremental")
        print("2 - Carga TOTAL substituindo chaves/datas existentes")
        print("3 - Reprocessar TUDO do zero")
        print("4 - Só f_agent_contact_diario")
        print("5 - Só f_css_atendente")
        print("6 - Só f_fsr_tratado")
        print("7 - Só f_indicadores_gerais")
        print("8 - Só f_volume_fila_diario")
        print("0 - Sair")
        opcao = input("Opção: ").strip()

        try:
            if opcao == "1":
                executar_carga("tudo", pastas, substituir=False, reprocessar_tudo=False)
            elif opcao == "2":
                executar_carga("tudo", pastas, substituir=True, reprocessar_tudo=False)
            elif opcao == "3":
                if perguntar_bool("Confirma reprocessar tudo do zero? Isso recria as 5 tabelas finais.", padrao=False):
                    executar_carga("tudo", pastas, substituir=False, reprocessar_tudo=True)
            elif opcao == "4":
                executar_carga("agent", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "5":
                executar_carga("css", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "6":
                executar_carga("fsr", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "7":
                executar_carga("indicadores", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "8":
                executar_carga("volume_fila", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "0":
                print("Encerrado.")
                break
            else:
                print("Opção inválida. Até menu precisa de limite, infelizmente.")
        except Exception as e:
            print(f"ERRO: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="App gerenciador das cargas RPA SSRS.")
    parser.add_argument("--base-dir", default=None, help="Pasta raiz do repo RPA_SSRS. Padrão: pasta onde está o app.py")
    parser.add_argument("--entrada", default=None, help="Pasta entrada com subpastas diárias. Padrão: <base>/entrada")
    parser.add_argument("--entrada-fsr", default=None, help="Pasta entrada_fsr. Padrão: <base>/entrada_fsr")
    parser.add_argument("--saida", default=None, help="Pasta de saída. Padrão: <base>/saida")
    parser.add_argument("--logs", default=None, help="Pasta de logs. Padrão: <base>/LOGS")
    parser.add_argument(
        "--carga",
        default="menu",
        choices=["menu", "tudo", "contact", "agent", "css", "fsr", "indicadores", "volume_fila"],
        help="Carga a executar. Padrão: menu interativo.",
    )
    parser.add_argument("--substituir", action="store_true", help="Substitui chaves/datas já existentes na saída.")
    parser.add_argument("--substituir-datas-existentes", action="store_true", help="Alias de --substituir.")
    parser.add_argument("--reprocessar-tudo", action="store_true", help="Ignora saída anterior e recria as tabelas selecionadas.")
    args = parser.parse_args()

    pastas = resolver_pastas(args)
    substituir = bool(args.substituir or args.substituir_datas_existentes)

    print(f"Versão do app: {VERSAO_APP}")
    if args.carga == "menu":
        menu_interativo(pastas)
    else:
        executar_carga(args.carga, pastas, substituir=substituir, reprocessar_tudo=args.reprocessar_tudo)


if __name__ == "__main__":
    main()
