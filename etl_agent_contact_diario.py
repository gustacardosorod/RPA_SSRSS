from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from common import (
    ARQUIVOS,
    adicionar_colunas_calendario,
    adicionar_origem_lote,
    concatenar,
    id_atendente,
    inteiro,
    filtrar_datas_lote,
    ler_relatorio_linhas,
    localizar_arquivo,
    localizar_lotes,
    normalizar,
    salvar_log,
    salvar_tabela_incremental,
    segundos_hhmmss,
    tempo_segundos,
)


_CACHE_AGENT: Dict[str, pd.DataFrame] = {}


def tratar_agent_lote(pasta_lote: Path) -> pd.DataFrame:
    caminho = localizar_arquivo(pasta_lote, ARQUIVOS["agent"])
    linhas = ler_relatorio_linhas(caminho)
    registros_fila = []

    for r in linhas:
        r = list(map(str, r))
        if len(r) >= 44 and pd.notna(pd.to_datetime(r[28].strip(), errors="coerce")):
            atendente = normalizar(r[20])
            if not atendente or atendente.lower() in ["agent_name_1", "agente", "agent"]:
                continue
            registros_fila.append({
                "Data": pd.to_datetime(r[28].strip(), errors="coerce"),
                "Grupo": normalizar(r[12]),
                "Atendente_ID": id_atendente(atendente),
                "Atendente": atendente,
                "Total_Ligacoes": inteiro(r[29]),
                "TMA_Segundos": tempo_segundos(r[30]),
                "TMA_Max_Segundos": tempo_segundos(r[31]),
                "Tempo_Processamento_Total_Segundos": tempo_segundos(r[32]),
                "Tempo_Pos_Processamento_Medio_Segundos": tempo_segundos(r[33]),
                "Tempo_Pos_Processamento_Max_Segundos": tempo_segundos(r[34]),
                "Tempo_Pos_Processamento_Total_Segundos": tempo_segundos(r[35]),
                "Fila": normalizar(r[36]),
            })

    fila = pd.DataFrame(registros_fila).drop_duplicates()
    if fila.empty:
        raise ValueError(f"Nenhum dado diário encontrado no Agent Contact em {pasta_lote}")

    diario = (
        fila.groupby(["Data", "Atendente_ID", "Atendente", "Grupo"], as_index=False)
        .agg(
            Total_Ligacoes=("Total_Ligacoes", "max"),
            TMA_Segundos=("TMA_Segundos", "max"),
            TMA_Max_Segundos=("TMA_Max_Segundos", "max"),
            Tempo_Processamento_Total_Segundos=("Tempo_Processamento_Total_Segundos", "max"),
            Tempo_Pos_Processamento_Medio_Segundos=("Tempo_Pos_Processamento_Medio_Segundos", "max"),
            Tempo_Pos_Processamento_Max_Segundos=("Tempo_Pos_Processamento_Max_Segundos", "max"),
            Tempo_Pos_Processamento_Total_Segundos=("Tempo_Pos_Processamento_Total_Segundos", "max"),
            Qtd_Filas_Atendidas=("Fila", "nunique"),
        )
    )

    diario["TMA_Minutos"] = diario["TMA_Segundos"] / 60
    for col in ["TMA_Segundos", "TMA_Max_Segundos", "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max_Segundos"]:
        diario[col.replace("_Segundos", "")] = diario[col].apply(segundos_hhmmss)
    diario = adicionar_colunas_calendario(diario)
    diario = filtrar_datas_lote(diario, pasta_lote, "Data")

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
    return diario[ordem].sort_values(["Data", "Atendente"])


def carregar_agent(pasta_entrada: Path) -> pd.DataFrame:
    chave_cache = str(Path(pasta_entrada).resolve())
    if chave_cache in _CACHE_AGENT:
        return _CACHE_AGENT[chave_cache].copy()

    lotes = localizar_lotes(pasta_entrada, ["agent"])
    bases = []
    for lote in lotes:
        print(f"Processando Agent Contact: {lote.name}")
        df = tratar_agent_lote(lote)
        bases.append(adicionar_origem_lote(df, lote))
    final = concatenar(bases)
    _CACHE_AGENT[chave_cache] = final.copy()
    return final


def processar_agent_contact(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> pd.DataFrame:
    novo = carregar_agent(pasta_entrada)
    final, log = salvar_tabela_incremental(
        "f_agent_contact_diario",
        novo,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Atendente", "Pasta_Origem"],
    )
    salvar_log([log], pasta_logs, "log_f_agent_contact_diario.csv")
    return final, log


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera f_agent_contact_diario.csv")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_agent_contact(Path(args.entrada), Path(args.saida), Path(args.logs or Path(args.saida).parent / "LOGS"), args.substituir, args.reprocessar_tudo)
