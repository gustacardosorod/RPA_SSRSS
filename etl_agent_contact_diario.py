from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from common import (
    ARQUIVOS,
    adicionar_colunas_calendario,
    adicionar_origem_lote,
    concatenar,
    divisao,
    filtrar_datas_lote,
    id_atendente,
    inteiro,
    ler_relatorio_linhas,
    localizar_arquivo,
    localizar_lotes,
    normalizar,
    salvar_log,
    salvar_tabela_incremental,
    segundos_hhmmss,
    tempo_segundos,
    validar_chave_dataframe,
)


_CACHE_AGENT: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = {}


def _linha_agent_valida(r: List[str]) -> bool:
    return len(r) >= 44 and pd.notna(pd.to_datetime(str(r[28]).strip(), errors="coerce"))


def _registro_fila(r: List[str]) -> Dict[str, object] | None:
    """Extrai a granularidade correta do relatório Agent Contact.

    O SSRS entrega, na mesma linha, total do grupo, total do agente e detalhe por fila.
    Para a tabela por fila, a fonte correta são as posições 36 a 43. Usar 29 a 35
    na tabela por fila repete total diário do agente em cada fila, uma pequena bomba
    vestida de CSV.
    """
    if not _linha_agent_valida(r):
        return None

    atendente = normalizar(r[20])
    fila = normalizar(r[36])
    grupo = normalizar(r[12])
    if not atendente or atendente.lower() in ["agent_name_1", "agente", "agent"]:
        return None
    if not fila:
        return None

    data = pd.to_datetime(str(r[28]).strip(), errors="coerce")
    return {
        "Data": data,
        "Grupo": grupo,
        "Atendente_ID": id_atendente(atendente),
        "Atendente": atendente,
        "Fila": fila,
        "Total_Ligacoes": inteiro(r[37]),
        "TMA_Segundos": tempo_segundos(r[38]),
        "TMA_Max_Segundos": tempo_segundos(r[39]),
        "Tempo_Processamento_Total_Segundos": tempo_segundos(r[40]),
        "Tempo_Pos_Processamento_Medio_Segundos": tempo_segundos(r[41]),
        "Tempo_Pos_Processamento_Max_Segundos": tempo_segundos(r[42]),
        "Tempo_Pos_Processamento_Total_Segundos": tempo_segundos(r[43]),
    }


def _finalizar_tabela_fila(fila: pd.DataFrame, pasta_lote: Path) -> pd.DataFrame:
    if fila.empty:
        return fila

    fila = fila.drop_duplicates().copy()
    fila = adicionar_colunas_calendario(fila)
    fila = filtrar_datas_lote(fila, pasta_lote, "Data")
    if fila.empty:
        return fila

    fila["TMA_Minutos"] = fila["TMA_Segundos"] / 60
    for col in ["TMA_Segundos", "TMA_Max_Segundos", "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max_Segundos"]:
        fila[col.replace("_Segundos", "")] = fila[col].apply(segundos_hhmmss)

    ordem = [
        "Data", "Ano", "Mes", "Dia", "Ano_Mes", "Atendente_ID", "Atendente", "Grupo", "Fila",
        "Total_Ligacoes", "TMA", "TMA_Segundos", "TMA_Minutos", "TMA_Max", "TMA_Max_Segundos",
        "Tempo_Processamento_Total_Segundos", "Tempo_Pos_Processamento_Medio",
        "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max",
        "Tempo_Pos_Processamento_Max_Segundos", "Tempo_Pos_Processamento_Total_Segundos",
    ]
    for col in ordem:
        if col not in fila.columns:
            fila[col] = pd.NA

    fila = fila[ordem].sort_values(["Data", "Atendente", "Fila"]).reset_index(drop=True)
    validar_chave_dataframe(fila, ["Data", "Atendente_ID", "Grupo", "Fila"], "f_agent_contact_fila_diario")
    return fila


def _derivar_diario_da_fila(fila: pd.DataFrame) -> pd.DataFrame:
    if fila.empty:
        return pd.DataFrame()

    diario = (
        fila.groupby(["Data", "Atendente_ID", "Atendente", "Grupo"], as_index=False)
        .agg(
            Total_Ligacoes=("Total_Ligacoes", "sum"),
            TMA_Max_Segundos=("TMA_Max_Segundos", "max"),
            Tempo_Processamento_Total_Segundos=("Tempo_Processamento_Total_Segundos", "sum"),
            Tempo_Pos_Processamento_Total_Segundos=("Tempo_Pos_Processamento_Total_Segundos", "sum"),
            Tempo_Pos_Processamento_Max_Segundos=("Tempo_Pos_Processamento_Max_Segundos", "max"),
            Qtd_Filas_Atendidas=("Fila", "nunique"),
        )
    )

    diario["TMA_Segundos"] = diario.apply(
        lambda x: round(divisao(x["Tempo_Processamento_Total_Segundos"], x["Total_Ligacoes"]) or 0),
        axis=1,
    )
    diario["Tempo_Pos_Processamento_Medio_Segundos"] = diario.apply(
        lambda x: round(divisao(x["Tempo_Pos_Processamento_Total_Segundos"], x["Total_Ligacoes"]) or 0),
        axis=1,
    )
    diario["TMA_Minutos"] = diario["TMA_Segundos"] / 60

    for col in ["TMA_Segundos", "TMA_Max_Segundos", "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max_Segundos"]:
        diario[col.replace("_Segundos", "")] = diario[col].apply(segundos_hhmmss)

    diario = adicionar_colunas_calendario(diario)
    ordem = [
        "Data", "Ano", "Mes", "Dia", "Ano_Mes", "Atendente_ID", "Atendente", "Grupo",
        "Total_Ligacoes", "Qtd_Filas_Atendidas", "TMA", "TMA_Segundos", "TMA_Minutos",
        "TMA_Max", "TMA_Max_Segundos", "Tempo_Processamento_Total_Segundos",
        "Tempo_Pos_Processamento_Medio", "Tempo_Pos_Processamento_Medio_Segundos",
        "Tempo_Pos_Processamento_Max", "Tempo_Pos_Processamento_Max_Segundos",
        "Tempo_Pos_Processamento_Total_Segundos",
    ]
    for col in ordem:
        if col not in diario.columns:
            diario[col] = pd.NA

    diario = diario[ordem].sort_values(["Data", "Atendente"]).reset_index(drop=True)
    validar_chave_dataframe(diario, ["Data", "Atendente_ID", "Grupo"], "f_agent_contact_diario")
    return diario


def tratar_agent_lote(pasta_lote: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    caminho = localizar_arquivo(pasta_lote, ARQUIVOS["agent"])
    linhas = ler_relatorio_linhas(caminho)
    registros_fila = []

    for r in linhas:
        r = list(map(str, r))
        registro = _registro_fila(r)
        if registro is not None:
            registros_fila.append(registro)

    fila = pd.DataFrame(registros_fila)
    if fila.empty:
        raise ValueError(f"Nenhum dado diário encontrado no Agent Contact em {pasta_lote}")

    fila = _finalizar_tabela_fila(fila, pasta_lote)
    diario = _derivar_diario_da_fila(fila)
    return diario, fila


def carregar_agent(pasta_entrada: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    chave_cache = str(Path(pasta_entrada).resolve())
    if chave_cache in _CACHE_AGENT:
        diario_cache, fila_cache = _CACHE_AGENT[chave_cache]
        return diario_cache.copy(), fila_cache.copy()

    lotes = localizar_lotes(pasta_entrada, ["agent"])
    bases_diario = []
    bases_fila = []
    for lote in lotes:
        print(f"Processando Agent Contact: {lote.name}")
        diario, fila = tratar_agent_lote(lote)
        bases_diario.append(adicionar_origem_lote(diario, lote))
        bases_fila.append(adicionar_origem_lote(fila, lote))

    diario_final = concatenar(bases_diario)
    fila_final = concatenar(bases_fila)
    _CACHE_AGENT[chave_cache] = (diario_final.copy(), fila_final.copy())
    return diario_final, fila_final


def processar_agent_contact(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    diario, fila = carregar_agent(pasta_entrada)

    final_fila, log_fila = salvar_tabela_incremental(
        "f_agent_contact_fila_diario",
        fila,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Atendente", "Fila", "Pasta_Origem"],
    )

    final_diario, log_diario = salvar_tabela_incremental(
        "f_agent_contact_diario",
        diario,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Atendente", "Pasta_Origem"],
    )

    salvar_log([log_fila], pasta_logs, "log_f_agent_contact_fila_diario.csv")
    salvar_log([log_diario], pasta_logs, "log_f_agent_contact_diario.csv")
    return final_diario, log_diario


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera f_agent_contact_diario.csv e f_agent_contact_fila_diario.csv")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_agent_contact(Path(args.entrada), Path(args.saida), Path(args.logs or Path(args.saida).parent / "LOGS"), args.substituir, args.reprocessar_tudo)
