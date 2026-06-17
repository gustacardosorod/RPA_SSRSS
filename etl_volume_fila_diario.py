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
    filtrar_ate_ultima_data_com_movimento,
    filtrar_datas_lote,
    inteiro,
    ler_relatorio_linhas,
    localizar_arquivo,
    localizar_lotes,
    normalizar,
    percentual,
    salvar_log,
    salvar_tabela_incremental,
    segundos_hhmmss,
    tempo_segundos,
)


_CACHE_VOLUME: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = {}


def extrair_status_volume(linhas: List[List[str]]) -> pd.DataFrame:
    registros = []
    for r in linhas:
        if len(r) == 11 and normalizar(r[0]).lower() == "atendidas":
            data = pd.to_datetime(r[1], errors="coerce")
            if pd.isna(data):
                continue
            registros.append({
                "Data": data,
                "Atendidas_Status": inteiro(r[2]),
                "Tratado_Status": inteiro(r[4]),
                "Falsas_Tentativas": inteiro(r[6]),
                "Canceladas": inteiro(r[8]),
                "Servico_Fechado": inteiro(r[10]),
            })
    return pd.DataFrame(registros).drop_duplicates()


def tratar_volume_lote(pasta_lote: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    caminho = localizar_arquivo(pasta_lote, ARQUIVOS["volume"])
    linhas = ler_relatorio_linhas(caminho)

    registros_geral = []
    registros_fila = []

    for r in linhas:
        r = list(map(str, r))
        if len(r) >= 126 and pd.notna(pd.to_datetime(r[36].strip(), errors="coerce")):
            data = pd.to_datetime(r[36].strip(), errors="coerce")
            registros_geral.append({
                "Data": data,
                "Recebidas": inteiro(r[37]),
                "Concluidas_SSRS": inteiro(r[38]),
                "Atendidas": inteiro(r[41]),
                "Nivel_Servico_SSRS": percentual(r[42]),
                "Transferencias": inteiro(r[43]),
                "Taxa_Transferencia": percentual(r[44]),
                "Estouro": inteiro(r[45]),
                "Taxa_Estouro": percentual(r[46]),
                "TME_Geral_SSRS_Segundos": tempo_segundos(r[39]),
                "Total_Espera_Geral_Segundos": tempo_segundos(r[40]),
                "TME_Segundos": tempo_segundos(r[49]),
                "TME_Max_Segundos": tempo_segundos(r[50]),
                "Total_Espera_Atendidas_Segundos": tempo_segundos(r[51]),
                "TMA_Segundos": tempo_segundos(r[52]),
                "TMA_Max_Segundos": tempo_segundos(r[53]),
                "TMA_Total_Segundos": tempo_segundos(r[54]),
                "TME_Canceladas_Segundos": tempo_segundos(r[59]),
                "TME_Canceladas_Max_Segundos": tempo_segundos(r[60]),
                "Total_Espera_Canceladas_Segundos": tempo_segundos(r[61]),
                "Contatos_Simultaneos_Max": inteiro(r[64]),
            })

            fila = normalizar(r[96])
            if fila and str(r[97]).strip():
                registros_fila.append({
                    "Data": data,
                    "Fila": fila,
                    "Recebidas": inteiro(r[97]),
                    "Concluidas_SSRS": inteiro(r[98]),
                    "Atendidas": inteiro(r[101]),
                    "Nivel_Servico_SSRS": percentual(r[102]),
                    "Transferencias": inteiro(r[103]),
                    "Taxa_Transferencia": percentual(r[104]),
                    "Estouro": inteiro(r[105]),
                    "Taxa_Estouro": percentual(r[106]),
                    "TME_Geral_SSRS_Segundos": tempo_segundos(r[99]),
                    "Total_Espera_Geral_Segundos": tempo_segundos(r[100]),
                    "TME_Segundos": tempo_segundos(r[109]),
                    "TME_Max_Segundos": tempo_segundos(r[110]),
                    "Total_Espera_Atendidas_Segundos": tempo_segundos(r[111]),
                    "TMA_Segundos": tempo_segundos(r[112]),
                    "TMA_Max_Segundos": tempo_segundos(r[113]),
                    "TMA_Total_Segundos": tempo_segundos(r[114]),
                    "Falsas_Tentativas": inteiro(r[115]),
                    "Canceladas": inteiro(r[117]),
                    "TME_Canceladas_Segundos": tempo_segundos(r[119]),
                    "TME_Canceladas_Max_Segundos": tempo_segundos(r[120]),
                    "Total_Espera_Canceladas_Segundos": tempo_segundos(r[121]),
                    "Servico_Fechado": inteiro(r[122]),
                    "Contatos_Simultaneos_Max": inteiro(r[124]),
                })

    geral = pd.DataFrame(registros_geral).drop_duplicates()
    fila = pd.DataFrame(registros_fila).drop_duplicates()

    if geral.empty:
        raise ValueError(f"Nenhum dado diário encontrado no Volume 4 - Daily em {pasta_lote}")

    status = extrair_status_volume(linhas)
    if not status.empty:
        geral = geral.merge(status, on="Data", how="left", suffixes=("", "_StatusGrafico"))
        soma_atendidas_status = geral["Atendidas_Status"].fillna(0) + geral["Tratado_Status"].fillna(0)
        geral["Atendidas"] = soma_atendidas_status.where(soma_atendidas_status > 0, geral["Atendidas"])
        geral["Falsas_Tentativas"] = geral["Falsas_Tentativas"].fillna(0).astype(int)
        geral["Canceladas"] = geral["Canceladas"].fillna(0).astype(int)
        geral["Servico_Fechado"] = geral["Servico_Fechado"].fillna(0).astype(int)
    else:
        geral["Atendidas_Status"] = pd.NA
        geral["Tratado_Status"] = pd.NA
        geral["Falsas_Tentativas"] = pd.NA
        geral["Canceladas"] = pd.NA
        geral["Servico_Fechado"] = pd.NA

    geral["Abandonadas"] = geral["Canceladas"].fillna(0).astype(int)
    geral["Taxa_Atendimento"] = geral.apply(lambda x: divisao(x["Atendidas"], x["Recebidas"]), axis=1)
    geral["Taxa_Conclusao_SSRS"] = geral.apply(lambda x: divisao(x["Concluidas_SSRS"], x["Recebidas"]), axis=1)
    geral["Taxa_Abandono"] = geral.apply(lambda x: divisao(x["Abandonadas"], x["Recebidas"]), axis=1)
    geral["Taxa_Falsa_Tentativa"] = geral.apply(lambda x: divisao(x["Falsas_Tentativas"], x["Recebidas"]), axis=1)
    geral["TME_Minutos"] = geral["TME_Segundos"] / 60
    geral["TMA_Minutos"] = geral["TMA_Segundos"] / 60

    for col in ["TME_Geral_SSRS_Segundos", "TME_Segundos", "TME_Max_Segundos", "TMA_Segundos", "TMA_Max_Segundos"]:
        geral[col.replace("_Segundos", "")] = geral[col].apply(segundos_hhmmss)

    geral = adicionar_colunas_calendario(geral)
    geral = filtrar_ate_ultima_data_com_movimento(
        geral,
        ["Recebidas", "Atendidas", "Concluidas_SSRS", "Canceladas", "Falsas_Tentativas", "Servico_Fechado"],
    )
    geral = filtrar_datas_lote(geral, pasta_lote, "Data")

    if not fila.empty:
        fila["Abandonadas"] = fila["Canceladas"].fillna(0).astype(int)
        fila["Taxa_Atendimento"] = fila.apply(lambda x: divisao(x["Atendidas"], x["Recebidas"]), axis=1)
        fila["Taxa_Abandono"] = fila.apply(lambda x: divisao(x["Abandonadas"], x["Recebidas"]), axis=1)
        fila["TME_Minutos"] = fila["TME_Segundos"] / 60
        fila["TMA_Minutos"] = fila["TMA_Segundos"] / 60
        for col in ["TME_Segundos", "TME_Geral_SSRS_Segundos", "TMA_Segundos", "TMA_Max_Segundos"]:
            fila[col.replace("_Segundos", "")] = fila[col].apply(segundos_hhmmss)
        fila = adicionar_colunas_calendario(fila)
        fila = filtrar_datas_lote(fila, pasta_lote, "Data")

    return geral, fila


def carregar_volume_geral_e_fila(pasta_entrada: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    chave_cache = str(Path(pasta_entrada).resolve())
    if chave_cache in _CACHE_VOLUME:
        geral_cache, fila_cache = _CACHE_VOLUME[chave_cache]
        return geral_cache.copy(), fila_cache.copy()

    lotes = localizar_lotes(pasta_entrada, ["volume"])
    gerais = []
    filas = []
    for lote in lotes:
        print(f"Processando Volume/Fila: {lote.name}")
        geral, fila = tratar_volume_lote(lote)
        gerais.append(adicionar_origem_lote(geral, lote))
        filas.append(adicionar_origem_lote(fila, lote))

    geral_final, fila_final = concatenar(gerais), concatenar(filas)
    _CACHE_VOLUME[chave_cache] = (geral_final.copy(), fila_final.copy())
    return geral_final, fila_final


def processar_volume_fila(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> pd.DataFrame:
    geral, fila = carregar_volume_geral_e_fila(pasta_entrada)

    final_geral, log_geral = salvar_tabela_incremental(
        "f_volume_geral_diario",
        geral,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Pasta_Origem"],
    )

    final_fila, log_fila = salvar_tabela_incremental(
        "f_volume_fila_diario",
        fila,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Fila", "Pasta_Origem"],
    )

    salvar_log([log_geral], pasta_logs, "log_f_volume_geral_diario.csv")
    salvar_log([log_fila], pasta_logs, "log_f_volume_fila_diario.csv")

    # Retorna a tabela por fila para manter compatibilidade com o app anterior.
    return final_fila, log_fila


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera f_volume_fila_diario.csv")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_volume_fila(Path(args.entrada), Path(args.saida), Path(args.logs or Path(args.saida).parent / "LOGS"), args.substituir, args.reprocessar_tudo)
