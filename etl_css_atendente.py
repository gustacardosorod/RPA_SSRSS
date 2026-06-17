from __future__ import annotations

from pathlib import Path

import pandas as pd

from common import (
    ARQUIVOS,
    adicionar_origem_lote,
    classificar_css,
    concatenar,
    extrair_periodo_parametros,
    janela_lote,
    id_atendente,
    inteiro,
    ler_relatorio_linhas,
    localizar_arquivo,
    localizar_lotes,
    normalizar,
    parse_data_lote,
    salvar_log,
    salvar_tabela_incremental,
)


def tratar_css_lote(pasta_lote: Path) -> pd.DataFrame:
    caminho = localizar_arquivo(pasta_lote, ARQUIVOS["css"])
    linhas = ler_relatorio_linhas(caminho)
    periodo_inicio, periodo_fim, unico_dia = extrair_periodo_parametros(linhas)
    if pd.isna(periodo_inicio) or pd.isna(periodo_fim):
        print(f"IGNORADO - CSS Atendente {pasta_lote.name}: relatório sem período explícito em Dias. Evita jogar acumulado/all dentro de junho.")
        return pd.DataFrame()

    inicio_lote, fim_lote, tipo_lote = janela_lote(pasta_lote)
    if pd.notna(inicio_lote) and pd.notna(fim_lote):
        if periodo_inicio < inicio_lote or periodo_fim > fim_lote:
            print(
                f"IGNORADO - CSS Atendente {pasta_lote.name}: período do arquivo "
                f"{periodo_inicio.date()} a {periodo_fim.date()} fora da janela do lote "
                f"{inicio_lote.date()} a {fim_lote.date()}."
            )
            return pd.DataFrame()

    data_ref = periodo_inicio if unico_dia else pd.NaT
    data_lote, mes_lote = parse_data_lote(pasta_lote.name)
    data_atualizacao = data_lote if pd.notna(data_lote) else pd.Timestamp.today().normalize()
    registros = []

    for r in linhas:
        r = list(map(str, r))
        if len(r) >= 18:
            atendente = normalizar(r[7])
            script = normalizar(r[10])
            pergunta = normalizar(r[13])
            resposta = normalizar(r[16])
            qtd = inteiro(r[17])
            if atendente and atendente.lower() not in ["agent_name_1", "agente", "agent"] and pergunta and resposta and qtd > 0:
                nota = pd.to_numeric(resposta, errors="coerce")
                classificacao = classificar_css(nota)
                nota_x_qtd = 0 if classificacao == "Inválida" else nota * qtd
                registros.append({
                    "Data": data_ref,
                    "Data_Atualizacao_Carga": data_atualizacao,
                    "Periodo_Inicio": periodo_inicio,
                    "Periodo_Fim": periodo_fim,
                    "Ano_Mes": periodo_inicio.strftime("%Y-%m") if pd.notna(periodo_inicio) else mes_lote,
                    "Tem_Data_Diaria": bool(unico_dia),
                    "Atendente_ID": id_atendente(atendente),
                    "Atendente": atendente,
                    "Script": script,
                    "Pergunta": pergunta,
                    "Resposta": resposta,
                    "Nota_CSS": nota,
                    "Quantidade": qtd,
                    "Classificacao_CSS": classificacao,
                    "Nota_x_Qtd": nota_x_qtd,
                    "Qtd_Positiva": qtd if classificacao == "Positiva" else 0,
                    "Qtd_Neutra": qtd if classificacao == "Neutra" else 0,
                    "Qtd_Negativa": qtd if classificacao == "Negativa" else 0,
                    "Qtd_Invalida": qtd if classificacao == "Inválida" else 0,
                })

    detalhe = pd.DataFrame(registros)
    if detalhe.empty:
        raise ValueError(f"Nenhum dado encontrado no CSS por atendente em {pasta_lote}")

    validas = detalhe[detalhe["Classificacao_CSS"] != "Inválida"].copy()
    resumo = (
        validas.groupby(
            ["Data", "Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes", "Tem_Data_Diaria", "Atendente_ID", "Atendente"],
            dropna=False,
            as_index=False,
        )
        .agg(
            Total_Respostas_CSS=("Quantidade", "sum"),
            Qtd_Positiva=("Qtd_Positiva", "sum"),
            Qtd_Neutra=("Qtd_Neutra", "sum"),
            Qtd_Negativa=("Qtd_Negativa", "sum"),
            Soma_Nota_Ponderada=("Nota_x_Qtd", "sum"),
        )
    )
    resumo["CSS_Medio"] = resumo["Soma_Nota_Ponderada"] / resumo["Total_Respostas_CSS"].replace(0, pd.NA)
    resumo["CSS_Aproveitamento_Percentual"] = resumo["CSS_Medio"] / 5
    resumo["CSS_Positivo"] = resumo["Qtd_Positiva"] / resumo["Total_Respostas_CSS"].replace(0, pd.NA)
    resumo["CSS_Neutro"] = resumo["Qtd_Neutra"] / resumo["Total_Respostas_CSS"].replace(0, pd.NA)
    resumo["CSS_Negativo"] = resumo["Qtd_Negativa"] / resumo["Total_Respostas_CSS"].replace(0, pd.NA)
    return resumo.sort_values(["Atendente"])


def carregar_css_atendente(pasta_entrada: Path) -> pd.DataFrame:
    lotes = localizar_lotes(pasta_entrada, ["css"])
    bases = []
    for lote in lotes:
        print(f"Processando CSS Atendente: {lote.name}")
        df = tratar_css_lote(lote)
        if df is not None and not df.empty:
            bases.append(adicionar_origem_lote(df, lote))
    final = concatenar(bases)
    if final.empty:
        raise ValueError("Nenhum CSS por atendente válido encontrado. Confira se o relatório tem parâmetros de Dias explícitos.")
    return final


def processar_css_atendente(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> pd.DataFrame:
    novo = carregar_css_atendente(pasta_entrada)
    final, log = salvar_tabela_incremental(
        "f_css_atendente",
        novo,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data_Lote", "Atendente", "Pasta_Origem"],
    )
    salvar_log([log], pasta_logs, "log_f_css_atendente.csv")
    return final, log


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera f_css_atendente.csv")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_css_atendente(Path(args.entrada), Path(args.saida), Path(args.logs or Path(args.saida).parent / "LOGS"), args.substituir, args.reprocessar_tudo)
