from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from common import (
    ARQUIVOS,
    adicionar_colunas_calendario,
    adicionar_origem_lote,
    classificar_css,
    concatenar,
    deduplicar_por_chave,
    inteiro,
    filtrar_datas_lote,
    ler_relatorio_linhas,
    ler_saida_existente,
    localizar_arquivo,
    localizar_lotes,
    normalizar,
    parse_datas_mistas,
    salvar_csv,
    salvar_log,
    salvar_tabela_incremental,
    segundos_hhmmss,
)


COLUNAS_INDICADORES_GERAIS = [
    "Data", "Ano", "Mes", "Dia", "Ano_Mes", "Recebidas", "Atendidas", "Abandonadas", "Canceladas", "Concluidas_SSRS",
    "Atendidas_Status", "Tratado_Status", "Falsas_Tentativas", "Servico_Fechado", "Taxa_Atendimento", "Taxa_Abandono",
    "Taxa_Falsa_Tentativa", "Taxa_Conclusao_SSRS", "Nivel_Servico_SSRS", "TME", "TME_Segundos", "TME_Minutos",
    "TMA", "TMA_Segundos", "TMA_Minutos", "Total_Ligacoes_Atendentes", "Atendentes_Ativos", "TMA_Atendentes",
    "TMA_Atendentes_Segundos", "TMA_Atendentes_Minutos", "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra",
    "Qtd_Negativa", "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral", "CSS_Neutro_Geral",
    "CSS_Negativo_Geral", "Tem_CSS_Diario", "Fonte_CSS_Diario", "Observacao_CSS", "Pasta_Origem", "Data_Lote", "Mes_Referencia",
]


COLUNAS_NUMERICAS_VOLUME = [
    "Recebidas", "Atendidas", "Abandonadas", "Canceladas", "Concluidas_SSRS", "Atendidas_Status", "Tratado_Status",
    "Falsas_Tentativas", "Servico_Fechado", "Taxa_Atendimento", "Taxa_Abandono", "Taxa_Falsa_Tentativa",
    "Taxa_Conclusao_SSRS", "Nivel_Servico_SSRS", "TME_Segundos", "TME_Minutos", "TMA_Segundos", "TMA_Minutos",
]

COLUNAS_NUMERICAS_AGENT = ["Total_Ligacoes", "TMA_Segundos"]

COLUNAS_NUMERICAS_CSS = [
    "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa", "Soma_Nota_Ponderada",
    "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral", "CSS_Neutro_Geral", "CSS_Negativo_Geral",
]


def _converter_numero_serie(serie: pd.Series) -> pd.Series:
    texto = serie.fillna("").astype(str).str.strip()
    texto = texto.replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    texto = texto.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    texto = texto.str.replace(r"[^0-9.\-]", "", regex=True)
    texto = texto.replace({"": pd.NA, "-": pd.NA, ".": pd.NA, "-.": pd.NA})
    return pd.to_numeric(texto, errors="coerce")


def _converter_numericas(df: pd.DataFrame, colunas: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    for col in colunas:
        if col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = _converter_numero_serie(df[col])
    return df


def _ler_saida(pasta_saida: Path, nome_tabela: str) -> pd.DataFrame:
    return ler_saida_existente(pasta_saida, nome_tabela)


def tratar_css_geral_diario_lote(pasta_lote: Path) -> pd.DataFrame:
    """Lê o Script Result 3 - Queue Volume per Day e gera CSS diário geral por Data."""
    try:
        caminho = localizar_arquivo(pasta_lote, ARQUIVOS["css_fila_diario"])
    except FileNotFoundError:
        return pd.DataFrame()

    linhas = ler_relatorio_linhas(caminho)
    registros = []
    for r in linhas:
        r = list(map(str, r))
        if len(r) >= 25 and pd.notna(pd.to_datetime(r[10].strip(), errors="coerce")):
            data = pd.to_datetime(r[10].strip(), errors="coerce")
            fila = normalizar(r[11])
            pergunta = normalizar(r[18])
            resposta = normalizar(r[21])
            qtd = inteiro(r[22])
            if pd.isna(data) or not fila or not pergunta or not resposta or qtd <= 0:
                continue
            nota = pd.to_numeric(resposta, errors="coerce")
            classificacao = classificar_css(nota)
            nota_x_qtd = 0 if classificacao == "Inválida" else nota * qtd
            registros.append({
                "Data": data,
                "Fila": fila,
                "Quantidade": qtd,
                "Classificacao_CSS": classificacao,
                "Nota_x_Qtd": nota_x_qtd,
                "Qtd_Positiva": qtd if classificacao == "Positiva" else 0,
                "Qtd_Neutra": qtd if classificacao == "Neutra" else 0,
                "Qtd_Negativa": qtd if classificacao == "Negativa" else 0,
            })

    detalhe = pd.DataFrame(registros)
    if detalhe.empty:
        return pd.DataFrame()

    detalhe = filtrar_datas_lote(detalhe, pasta_lote, "Data")
    if detalhe.empty:
        return pd.DataFrame()

    validas = detalhe[detalhe["Classificacao_CSS"] != "Inválida"].copy()
    if validas.empty:
        return pd.DataFrame()

    geral = (
        validas.groupby("Data", as_index=False)
        .agg(
            Total_Respostas_CSS=("Quantidade", "sum"),
            Qtd_Positiva=("Qtd_Positiva", "sum"),
            Qtd_Neutra=("Qtd_Neutra", "sum"),
            Qtd_Negativa=("Qtd_Negativa", "sum"),
            Soma_Nota_Ponderada=("Nota_x_Qtd", "sum"),
        )
    )
    geral["CSS_Medio_Geral"] = geral["Soma_Nota_Ponderada"] / geral["Total_Respostas_CSS"].replace(0, pd.NA)
    geral["CSS_Aproveitamento_Percentual_Geral"] = geral["CSS_Medio_Geral"] / 5
    geral["CSS_Positivo_Geral"] = geral["Qtd_Positiva"] / geral["Total_Respostas_CSS"].replace(0, pd.NA)
    geral["CSS_Neutro_Geral"] = geral["Qtd_Neutra"] / geral["Total_Respostas_CSS"].replace(0, pd.NA)
    geral["CSS_Negativo_Geral"] = geral["Qtd_Negativa"] / geral["Total_Respostas_CSS"].replace(0, pd.NA)
    geral = adicionar_colunas_calendario(geral)
    return geral


def carregar_css_geral_diario(pasta_entrada: Path) -> pd.DataFrame:
    lotes = localizar_lotes(pasta_entrada, ["css_fila_diario"])
    bases = []
    for lote in lotes:
        print(f"Processando CSS Geral Diário: {lote.name}")
        df = tratar_css_geral_diario_lote(lote)
        if df is not None and not df.empty:
            bases.append(adicionar_origem_lote(df, lote))
    return concatenar(bases)


def atualizar_css_geral_diario(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Atualiza f_css_geral_diario.csv, usado como fonte diária oficial do CSS geral."""
    try:
        novo = carregar_css_geral_diario(pasta_entrada)
    except FileNotFoundError:
        novo = pd.DataFrame()

    final, log = salvar_tabela_incremental(
        "f_css_geral_diario",
        novo,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data", "Pasta_Origem"],
    )
    salvar_log([log], pasta_logs, "log_f_css_geral_diario.csv")
    return final, log


def criar_dim_atendentes_final(pasta_saida: Path) -> pd.DataFrame:
    """Cria dim_atendentes.csv usando as fatos finais já consolidadas."""
    bases = []

    agent = _ler_saida(pasta_saida, "f_agent_contact_diario")
    if agent is not None and not agent.empty and {"Atendente_ID", "Atendente"}.issubset(agent.columns):
        cols = [c for c in ["Atendente_ID", "Atendente", "Grupo"] if c in agent.columns]
        tmp = agent[cols].copy().drop_duplicates()
        if "Grupo" not in tmp.columns:
            tmp["Grupo"] = ""
        bases.append(tmp[["Atendente_ID", "Atendente", "Grupo"]])

    css = _ler_saida(pasta_saida, "f_css_atendente")
    if css is not None and not css.empty and {"Atendente_ID", "Atendente"}.issubset(css.columns):
        tmp = css[["Atendente_ID", "Atendente"]].copy().drop_duplicates()
        tmp["Grupo"] = ""
        bases.append(tmp[["Atendente_ID", "Atendente", "Grupo"]])

    if not bases:
        dim = pd.DataFrame(columns=["Atendente_ID", "Atendente", "Grupo"])
    else:
        dim = pd.concat(bases, ignore_index=True, sort=False).drop_duplicates()
        dim["Atendente_ID"] = dim["Atendente_ID"].fillna("").astype(str).str.strip()
        dim["Atendente"] = dim["Atendente"].fillna("").astype(str).str.strip()
        dim["Grupo"] = dim["Grupo"].fillna("").astype(str).str.strip()
        dim = dim.loc[dim["Atendente_ID"] != ""].copy()
        dim = (
            dim.sort_values(["Atendente", "Grupo"])
            .groupby(["Atendente_ID", "Atendente"], as_index=False)
            .agg(Grupo=("Grupo", lambda x: next((str(v) for v in x if str(v).strip()), "")))
            .sort_values("Atendente")
            .reset_index(drop=True)
        )

    salvar_csv(dim, pasta_saida, "dim_atendentes.csv")
    print(f"OK - dim_atendentes.csv ({len(dim)} linhas)")
    return dim


def montar_indicadores_gerais_consolidados(pasta_saida: Path) -> pd.DataFrame:
    """
    Recalcula f_indicadores_gerais usando as fatos finais já consolidadas.

    Essa é a correção importante: indicadores gerais não devem depender apenas do lote que acabou
    de chegar. Eles devem ser um retrato das bases finais, senão o histórico desaparece como ata de reunião
    que ninguém quer ler.
    """
    volume = _ler_saida(pasta_saida, "f_volume_geral_diario")
    if volume is None or volume.empty:
        raise ValueError("f_volume_geral_diario.csv não encontrado ou vazio. Rode a carga de volume antes dos indicadores gerais.")

    agent = _ler_saida(pasta_saida, "f_agent_contact_diario")
    css_diario = _ler_saida(pasta_saida, "f_css_geral_diario")

    volume = volume.copy()
    volume["Data"] = parse_datas_mistas(volume["Data"])
    volume = volume.dropna(subset=["Data"])
    volume = _converter_numericas(volume, COLUNAS_NUMERICAS_VOLUME)
    volume = deduplicar_por_chave(volume, ["Data"])
    volume = adicionar_colunas_calendario(volume)

    geral = volume.copy()

    if agent is not None and not agent.empty:
        a = agent.copy()
        a["Data"] = parse_datas_mistas(a["Data"])
        a = a.dropna(subset=["Data"])
        a = _converter_numericas(a, COLUNAS_NUMERICAS_AGENT)
        a = deduplicar_por_chave(a, ["Data", "Atendente_ID", "Grupo"])
        if not a.empty:
            a["TMA_x_Ligacoes"] = a["TMA_Segundos"].fillna(0) * a["Total_Ligacoes"].fillna(0)
            agent_dia = (
                a.groupby("Data", as_index=False)
                .agg(
                    Total_Ligacoes_Atendentes=("Total_Ligacoes", "sum"),
                    Atendentes_Ativos=("Atendente_ID", "nunique"),
                    Soma_TMA_x_Ligacoes=("TMA_x_Ligacoes", "sum"),
                )
            )
            agent_dia["TMA_Atendentes_Segundos"] = (
                agent_dia["Soma_TMA_x_Ligacoes"] / agent_dia["Total_Ligacoes_Atendentes"].replace(0, pd.NA)
            )
            agent_dia["TMA_Atendentes"] = agent_dia["TMA_Atendentes_Segundos"].apply(segundos_hhmmss)
            agent_dia["TMA_Atendentes_Minutos"] = agent_dia["TMA_Atendentes_Segundos"] / 60
            geral = geral.merge(
                agent_dia[[
                    "Data", "Total_Ligacoes_Atendentes", "Atendentes_Ativos", "TMA_Atendentes",
                    "TMA_Atendentes_Segundos", "TMA_Atendentes_Minutos",
                ]],
                on="Data",
                how="left",
            )

    geral["Tem_CSS_Diario"] = False
    geral["Fonte_CSS_Diario"] = ""
    geral["Observacao_CSS"] = "CSS diário não importado. Envie o relatório Script Result 3 - Queue Volume per Day."

    if css_diario is not None and not css_diario.empty:
        cd = css_diario.copy()
        cd["Data"] = parse_datas_mistas(cd["Data"])
        cd = cd.dropna(subset=["Data"])
        cd = _converter_numericas(cd, COLUNAS_NUMERICAS_CSS)
        cd = deduplicar_por_chave(cd, ["Data"])
        if not cd.empty:
            geral = geral.merge(
                cd[[
                    "Data", "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
                    "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral",
                    "CSS_Neutro_Geral", "CSS_Negativo_Geral",
                ]],
                on="Data",
                how="left",
            )
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Tem_CSS_Diario"] = True
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Fonte_CSS_Diario"] = "Script Result 3 - Queue Volume per Day"
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Observacao_CSS"] = "CSS diário importado por Data/Fila."

    for col in COLUNAS_INDICADORES_GERAIS:
        if col not in geral.columns:
            geral[col] = pd.NA

    return geral[COLUNAS_INDICADORES_GERAIS].sort_values("Data").reset_index(drop=True)


def processar_indicadores_gerais(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    # 1) Atualiza CSS diário consolidado, pois ele é usado na montagem dos indicadores.
    atualizar_css_geral_diario(
        pasta_entrada,
        pasta_saida,
        pasta_logs,
        substituir=substituir,
        reprocessar_tudo=reprocessar_tudo,
    )

    # 2) Recalcula indicadores a partir das fatos finais já salvas.
    existente = _ler_saida(pasta_saida, "f_indicadores_gerais")
    final = montar_indicadores_gerais_consolidados(pasta_saida)
    salvar_csv(final, pasta_saida, "f_indicadores_gerais.csv")
    print(f"OK - f_indicadores_gerais.csv ({len(final)} linhas finais, recalculado das fatos consolidadas)")

    # 3) Gera dimensão de atendentes no mesmo ciclo, porque ela depende das mesmas bases finais.
    criar_dim_atendentes_final(pasta_saida)

    log = {
        "Arquivo": "f_indicadores_gerais.csv",
        "Linhas_Existentes": len(existente),
        "Linhas_Processadas": len(final),
        "Linhas_Novas": max(len(final) - len(existente), 0),
        "Linhas_Ignoradas": 0,
        "Linhas_Substituidas": len(existente),
        "Duplicidades_Removidas_Na_Carga": 0,
        "Modo": "recalculado_a_partir_das_fatos_consolidadas",
        "Chave": "Data",
    }
    salvar_log([log], pasta_logs, "log_f_indicadores_gerais.csv")
    return final, log


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gera f_indicadores_gerais.csv a partir das fatos consolidadas")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_indicadores_gerais(
        Path(args.entrada),
        Path(args.saida),
        Path(args.logs or Path(args.saida).parent / "LOGS"),
        args.substituir,
        args.reprocessar_tudo,
    )
