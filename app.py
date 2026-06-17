from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from common import VERSAO_APP, salvar_log
from etl_agent_contact_diario import processar_agent_contact
from etl_css_atendente import processar_css_atendente
from etl_fsr_tratado import processar_fsr
from etl_indicadores_gerais import processar_indicadores_gerais
from etl_volume_fila_diario import processar_volume_fila
from etl_reclamacoes_sap import processar_reclamacoes_sap
from etl_gov_chamados import processar_gov_chamados

CARGAS_VALIDAS = [
    "menu",
    "tudo",
    "contact",
    "agent",
    "css",
    "fsr",
    "sap",
    "reclamacoes_sap",
    "gov",
    "gov_chamados",
    "indicadores",
    "volume_fila",
]

MODOS_VALIDOS = [
    "menu",
    "base-zero",
    "incremental-diario",
    "corrigir-periodo",
]


def resolver_pastas(args) -> Dict[str, Path]:
    base_dir = Path(args.base_dir).resolve() if args.base_dir else Path(__file__).resolve().parent
    entrada = Path(args.entrada).resolve() if args.entrada else base_dir / "entrada"
    entrada_fsr = Path(args.entrada_fsr).resolve() if args.entrada_fsr else base_dir / "entrada_fsr"
    entrada_sap = Path(args.entrada_sap).resolve() if args.entrada_sap else base_dir / "entrada_sap"
    entrada_gov_chamados = (
        Path(args.entrada_gov_chamados).resolve()
        if getattr(args, "entrada_gov_chamados", None)
        else base_dir / "entrada_gov_chamados"
    )
    saida = Path(args.saida).resolve() if args.saida else base_dir / "saida"
    logs = Path(args.logs).resolve() if args.logs else base_dir / "LOGS"

    # Cria apenas pastas de saída/log. Entrada não é criada para não esconder caminho digitado errado.
    saida.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    return {
        "base": base_dir,
        "entrada": entrada,
        "entrada_fsr": entrada_fsr,
        "entrada_sap": entrada_sap,
        "entrada_gov_chamados": entrada_gov_chamados,
        "saida": saida,
        "logs": logs,
    }


def exibir_contexto(pastas: Dict[str, Path]) -> None:
    print(f"Base:        {pastas['base']}")
    print(f"Entrada:     {pastas['entrada']}")
    print(f"Entrada FSR: {pastas['entrada_fsr']}")
    print(f"Entrada SAP: {pastas['entrada_sap']}")
    print(f"Entrada GOV: {pastas['entrada_gov_chamados']}")
    print(f"Saída:       {pastas['saida']}")
    print(f"Logs:        {pastas['logs']}")


def backup_saida_csv(pastas: Dict[str, Path], motivo: str) -> Path | None:
    """Cria backup dos CSVs atuais antes de cargas que podem substituir dados."""
    saida = pastas["saida"]
    arquivos = sorted(saida.glob("*.csv")) if saida.exists() else []
    if not arquivos:
        return None
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = pastas["logs"] / f"backup_saida_{motivo}_{carimbo}"
    destino.mkdir(parents=True, exist_ok=True)
    for arquivo in arquivos:
        shutil.copy2(arquivo, destino / arquivo.name)
    print(f"Backup criado antes da carga: {destino}")
    return destino


def executar_contact(
    pastas: Dict[str, Path],
    substituir: bool,
    reprocessar_tudo: bool,
    recriar_indicadores_do_zero: bool = False,
) -> List[dict]:
    logs: List[dict] = []
    print("\n=== CARGA CONTACT CENTER ===")

    _, log = processar_agent_contact(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    _, log = processar_css_atendente(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    _, log = processar_volume_fila(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    # f_indicadores_gerais é derivada das fatos finais consolidadas.
    # Ela pode ser recalculada inteira sem perder histórico, desde que volume/agent/CSS já tenham sido atualizados.
    _, log = processar_indicadores_gerais(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    return logs


def executar_tudo(
    pastas: Dict[str, Path],
    substituir: bool,
    reprocessar_tudo: bool,
    recriar_indicadores_do_zero: bool = False,
) -> List[dict]:
    logs = executar_contact(pastas, substituir, reprocessar_tudo, recriar_indicadores_do_zero)

    print("\n=== CARGA FSR / SAP SERVICE ===")
    _, log = processar_fsr(pastas["entrada_fsr"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    _, log = processar_reclamacoes_sap(pastas["entrada_sap"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    print("\n=== CARGA GOV CHAMADOS ===")
    _, log = processar_gov_chamados(pastas["entrada_gov_chamados"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
    logs.append(log)

    salvar_log(logs, pastas["logs"], "log_carga_total.csv")
    return logs


def executar_carga(
    carga: str,
    pastas: Dict[str, Path],
    substituir: bool,
    reprocessar_tudo: bool,
    recriar_indicadores_do_zero: bool = False,
) -> List[dict]:
    carga = carga.lower().strip()

    if carga == "tudo":
        return executar_tudo(pastas, substituir, reprocessar_tudo, recriar_indicadores_do_zero)

    if carga == "contact":
        logs = executar_contact(pastas, substituir, reprocessar_tudo, recriar_indicadores_do_zero)
        salvar_log(logs, pastas["logs"], "log_carga_contact.csv")
        return logs

    if carga == "agent":
        _, log = processar_agent_contact(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga == "css":
        _, log = processar_css_atendente(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga == "fsr":
        _, log = processar_fsr(pastas["entrada_fsr"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga in ["reclamacoes_sap", "sap"]:
        _, log = processar_reclamacoes_sap(pastas["entrada_sap"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga in ["gov", "gov_chamados"]:
        _, log = processar_gov_chamados(pastas["entrada_gov_chamados"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga == "indicadores":
        _, log = processar_indicadores_gerais(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    if carga == "volume_fila":
        _, log = processar_volume_fila(pastas["entrada"], pastas["saida"], pastas["logs"], substituir, reprocessar_tudo)
        return [log]

    raise ValueError(f"Carga inválida: {carga}")


def parametros_modo(modo: str) -> Tuple[bool, bool, str]:
    """
    Retorna: substituir, reprocessar_tudo, descrição.

    base-zero: recria a tabela final ignorando a saída atual.
    incremental-diario: inclui somente chaves novas, bom para pastas diárias.
    corrigir-periodo: substitui chaves já existentes, bom quando o arquivo diário MTD corrige dias anteriores.
    """
    modo = modo.lower().strip()
    if modo == "base-zero":
        return False, True, "BASE DO ZERO: recria os CSVs finais usando todas as entradas disponíveis"
    if modo == "incremental-diario":
        return False, False, "INCREMENTAL DIÁRIO: inclui somente registros ainda não existentes"
    if modo == "corrigir-periodo":
        return True, False, "CORRIGIR PERÍODO: substitui chaves/datas existentes pelo arquivo novo"
    raise ValueError(f"Modo inválido: {modo}")


def executar_por_modo(modo: str, carga: str, pastas: Dict[str, Path], recriar_indicadores_do_zero: bool = False) -> None:
    substituir, reprocessar_tudo, descricao = parametros_modo(modo)
    carga_final = "tudo" if carga == "menu" else carga

    print("\n" + "=" * 60)
    print(f"Modo: {descricao}")
    print(f"Carga: {carga_final}")
    print("=" * 60)
    exibir_contexto(pastas)

    if reprocessar_tudo or substituir:
        backup_saida_csv(pastas, modo.replace("-", "_"))

    executar_carga(
        carga_final,
        pastas,
        substituir=substituir,
        reprocessar_tudo=reprocessar_tudo,
        recriar_indicadores_do_zero=recriar_indicadores_do_zero,
    )


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
        exibir_contexto(pastas)
        print("\nEscolha a carga:")
        print("1 - Criar BASE DO ZERO com todas as entradas")
        print("2 - Incrementar ATUALIZAÇÕES DIÁRIAS, só chaves novas")
        print("3 - Corrigir período, substituindo chaves/datas existentes")
        print("4 - Só f_agent_contact_diario")
        print("5 - Só f_css_atendente")
        print("6 - Só f_fsr_tratado")
        print("7 - Só f_indicadores_gerais")
        print("8 - Só f_volume_fila_diario")
        print("9 - Só f_reclamacoes_sap_tratado")
        print("10 - Só f_gov_chamados_tratado")
        print("0 - Sair")
        opcao = input("Opção: ").strip()

        try:
            if opcao == "1":
                if perguntar_bool("Confirma criar a base do zero? Os CSVs finais serão recriados.", padrao=False):
                    executar_por_modo("base-zero", "tudo", pastas)
            elif opcao == "2":
                executar_por_modo("incremental-diario", "tudo", pastas)
            elif opcao == "3":
                executar_por_modo("corrigir-periodo", "tudo", pastas)
            elif opcao == "4":
                executar_carga("agent", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "5":
                executar_carga("css", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "6":
                executar_carga("fsr", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "7":
                executar_carga("indicadores", pastas, substituir=perguntar_bool("Substituir datas existentes?", False), reprocessar_tudo=False)
            elif opcao == "8":
                executar_carga("volume_fila", pastas, substituir=perguntar_bool("Substituir chaves existentes?", False), reprocessar_tudo=False)
            elif opcao == "9":
                executar_carga("reclamacoes_sap", pastas, substituir=perguntar_bool("Substituir protocolos existentes?", False), reprocessar_tudo=False)
            elif opcao == "10":
                executar_carga("gov_chamados", pastas, substituir=perguntar_bool("Substituir chamados GOV existentes?", False), reprocessar_tudo=False)
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
    parser.add_argument("--entrada-sap", default=None, help="Pasta entrada_sap com reclamações SAP. Padrão: <base>/entrada_sap")
    parser.add_argument("--entrada-gov-chamados", default=None, help="Pasta entrada_gov_chamados. Padrão: <base>/entrada_gov_chamados")
    parser.add_argument("--saida", default=None, help="Pasta de saída. Padrão: <base>/saida")
    parser.add_argument("--logs", default=None, help="Pasta de logs. Padrão: <base>/LOGS")
    parser.add_argument("--carga", default="menu", choices=CARGAS_VALIDAS, help="Carga a executar. Padrão: menu interativo.")
    parser.add_argument("--modo", default=None, choices=MODOS_VALIDOS, help="Modo de execução: base-zero, incremental-diario ou corrigir-periodo.")
    parser.add_argument("--substituir", action="store_true", help="Substitui chaves/datas já existentes na saída.")
    parser.add_argument("--substituir-datas-existentes", action="store_true", help="Alias de --substituir.")
    parser.add_argument("--reprocessar-tudo", action="store_true", help="Ignora saída anterior e recria as tabelas selecionadas.")
    parser.add_argument(
        "--recriar-indicadores-do-zero",
        action="store_true",
        help="Compatibilidade: mantido, mas a V4 recalcula indicadores a partir das fatos consolidadas com segurança.",
    )
    args = parser.parse_args()

    pastas = resolver_pastas(args)
    print(f"Versão do app: {VERSAO_APP}")

    if args.modo == "menu" or (args.modo is None and args.carga == "menu"):
        menu_interativo(pastas)
        return

    if args.modo:
        executar_por_modo(args.modo, args.carga, pastas, recriar_indicadores_do_zero=args.recriar_indicadores_do_zero)
        return

    substituir = bool(args.substituir or args.substituir_datas_existentes)
    executar_carga(args.carga, pastas, substituir=substituir, reprocessar_tudo=args.reprocessar_tudo, recriar_indicadores_do_zero=args.recriar_indicadores_do_zero)


if __name__ == "__main__":
    main()
