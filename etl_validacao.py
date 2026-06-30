from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from common import parse_datas_mistas, salvar_csv, chave_normalizada

CHAVES_TABELAS: Dict[str, List[str]] = {
    "f_volume_geral_diario": ["Data"],
    "f_volume_fila_diario": ["Data", "Fila"],
    "f_agent_contact_diario": ["Data", "Atendente_ID", "Grupo"],
    "f_agent_contact_fila_diario": ["Data", "Atendente_ID", "Grupo", "Fila"],
    "f_css_fila_diario": ["Data", "Fila"],
    "f_css_fila_detalhado_diario": ["Data", "Fila", "Script", "Pergunta", "Resposta"],
    "f_css_geral_diario": ["Data"],
}


def _ler_csv_saida(pasta: Path, tabela: str) -> pd.DataFrame:
    caminho = Path(pasta) / f"{tabela}.csv"
    if not caminho.exists():
        return pd.DataFrame()
    return pd.read_csv(caminho, dtype=str, sep=None, engine="python", encoding="utf-8-sig")


def _num(serie: pd.Series) -> pd.Series:
    if serie is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(
        serie.astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)


def _add(achados: List[Dict[str, object]], regra: str, severidade: str, tabela: str, mensagem: str, valor: object = "") -> None:
    achados.append({
        "Regra": regra,
        "Severidade": severidade,
        "Tabela": tabela,
        "Mensagem": mensagem,
        "Valor_Referencia": valor,
    })


def validar_duplicidades(pasta_saida: Path, achados: List[Dict[str, object]]) -> None:
    for tabela, chave in CHAVES_TABELAS.items():
        df = _ler_csv_saida(pasta_saida, tabela)
        if df.empty:
            continue
        faltantes = [c for c in chave if c not in df.columns]
        if faltantes:
            _add(achados, "chave_ausente", "ERRO", tabela, f"Colunas de chave ausentes: {faltantes}")
            continue
        chave_norm = chave_normalizada(df, chave)
        dup = chave_norm.duplicated(keep=False)
        if dup.any():
            exemplos = chave_norm.loc[dup].head(10).tolist()
            _add(achados, "duplicidade_chave", "ERRO", tabela, f"{int(dup.sum())} linha(s) com chave duplicada.", exemplos)


def validar_datas(pasta_saida: Path, achados: List[Dict[str, object]], bloquear_dia_atual: bool = True, data_maxima: Optional[pd.Timestamp] = None) -> None:
    if data_maxima is None:
        hoje = pd.Timestamp(date.today()).normalize()
        data_maxima = hoje - pd.Timedelta(days=1) if bloquear_dia_atual else hoje
    else:
        data_maxima = pd.Timestamp(data_maxima).normalize()

    for tabela in CHAVES_TABELAS:
        df = _ler_csv_saida(pasta_saida, tabela)
        if df.empty or "Data" not in df.columns:
            continue
        datas = parse_datas_mistas(df["Data"])
        invalidas = int(datas.isna().sum())
        if invalidas:
            _add(achados, "data_invalida", "ERRO", tabela, f"{invalidas} linha(s) sem data válida.")
        abertas = df.loc[datas > data_maxima]
        if not abertas.empty:
            _add(
                achados,
                "data_aberta_ou_futura",
                "ERRO",
                tabela,
                f"{len(abertas)} linha(s) acima da data máxima permitida {data_maxima.date()}.",
                sorted(datas.loc[datas > data_maxima].dt.strftime("%Y-%m-%d").dropna().unique().tolist()),
            )


def validar_volume_vs_fila(pasta_saida: Path, achados: List[Dict[str, object]]) -> None:
    geral = _ler_csv_saida(pasta_saida, "f_volume_geral_diario")
    fila = _ler_csv_saida(pasta_saida, "f_volume_fila_diario")
    if geral.empty or fila.empty:
        return
    for df in [geral, fila]:
        df["Data"] = parse_datas_mistas(df["Data"]).dt.strftime("%Y-%m-%d")
    colunas = ["Recebidas", "Atendidas", "Concluidas_SSRS", "Transferencias", "Estouro"]
    colunas = [c for c in colunas if c in geral.columns and c in fila.columns]
    if not colunas:
        return
    g = geral[["Data"] + colunas].copy()
    for c in colunas:
        g[c] = _num(g[c])
    f = fila[["Data"] + colunas].copy()
    for c in colunas:
        f[c] = _num(f[c])
    fs = f.groupby("Data", as_index=False)[colunas].sum()
    comp = g.merge(fs, on="Data", how="inner", suffixes=("_Geral", "_Fila"))
    for _, row in comp.iterrows():
        difs = {}
        for c in colunas:
            dif = float(row[f"{c}_Geral"] - row[f"{c}_Fila"])
            if abs(dif) > 0.001:
                difs[c] = dif
        if difs:
            _add(achados, "volume_geral_vs_fila", "ERRO", "f_volume_geral_diario/f_volume_fila_diario", f"Diferença em {row['Data']}", difs)


def validar_volume_vs_agent(pasta_saida: Path, achados: List[Dict[str, object]]) -> None:
    volume = _ler_csv_saida(pasta_saida, "f_volume_geral_diario")
    agent = _ler_csv_saida(pasta_saida, "f_agent_contact_fila_diario")
    if agent.empty:
        agent = _ler_csv_saida(pasta_saida, "f_agent_contact_diario")
    if volume.empty or agent.empty or "Atendidas" not in volume.columns or "Total_Ligacoes" not in agent.columns:
        return
    volume["Data"] = parse_datas_mistas(volume["Data"]).dt.strftime("%Y-%m-%d")
    agent["Data"] = parse_datas_mistas(agent["Data"]).dt.strftime("%Y-%m-%d")
    v = volume[["Data", "Atendidas"]].copy()
    a = agent[["Data", "Total_Ligacoes"]].copy()
    v["Atendidas"] = _num(v["Atendidas"])
    a["Total_Ligacoes"] = _num(a["Total_Ligacoes"])
    comp = v.merge(a.groupby("Data", as_index=False)["Total_Ligacoes"].sum(), on="Data", how="inner")
    comp["Diferenca"] = comp["Atendidas"] - comp["Total_Ligacoes"]
    ruins = comp.loc[comp["Diferenca"].abs() > 0.001]
    for _, row in ruins.iterrows():
        _add(
            achados,
            "volume_atendidas_vs_agent",
            "ERRO",
            "f_volume_geral_diario/f_agent_contact_fila_diario",
            f"Atendidas não bate com ligações dos agentes em {row['Data']}.",
            {"volume_atendidas": row["Atendidas"], "agent_total_ligacoes": row["Total_Ligacoes"], "diferenca": row["Diferenca"]},
        )


def validar_contact_center_processado(
    pasta_saida: Path,
    bloquear_dia_atual: bool = True,
    data_maxima: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    achados: List[Dict[str, object]] = []
    pasta_saida = Path(pasta_saida)
    validar_duplicidades(pasta_saida, achados)
    validar_datas(pasta_saida, achados, bloquear_dia_atual=bloquear_dia_atual, data_maxima=data_maxima)
    validar_volume_vs_fila(pasta_saida, achados)
    validar_volume_vs_agent(pasta_saida, achados)
    if not achados:
        achados.append({
            "Regra": "validacao_geral",
            "Severidade": "OK",
            "Tabela": "contact_center",
            "Mensagem": "Nenhuma inconsistência crítica encontrada nos CSVs processados.",
            "Valor_Referencia": "",
        })
    return pd.DataFrame(achados)


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida CSVs processados do RPA SSRS antes de gravar no banco.")
    parser.add_argument("--saida", required=True)
    parser.add_argument("--data-maxima", default=None, help="Data máxima permitida YYYY-MM-DD. Ex.: D-1 fechado.")
    parser.add_argument("--permitir-dia-atual", action="store_true")
    args = parser.parse_args()

    data_maxima = pd.to_datetime(args.data_maxima) if args.data_maxima else None
    df = validar_contact_center_processado(
        Path(args.saida),
        bloquear_dia_atual=not args.permitir_dia_atual,
        data_maxima=data_maxima,
    )
    caminho = salvar_csv(df, Path(args.saida), "auditoria_validacao_contact_center.csv")
    print(f"OK - auditoria gerada em {caminho}")
    print(df.to_string(index=False))
    if (df["Severidade"] == "ERRO").any():
        raise SystemExit(2)


if __name__ == "__main__":
    main()
