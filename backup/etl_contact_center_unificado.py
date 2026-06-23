from pathlib import Path
import argparse
import csv
import hashlib
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

import pandas as pd


# ============================================================
# ETL CONTACT CENTER - MODELO POWER BI - HISTÓRICO INCREMENTAL + PERCENTUAIS PADRÃO POWER BI
# ============================================================
# Entrada esperada:
#   Volume 4 - Daily.csv
#   Agent - Contact Handling Time 4 - Daily.csv
#   Script Result 5 - Agent Volume.csv
#
# Saídas principais:
#   dim_atendentes.csv
#   f_volume_geral_diario.csv
#   f_volume_fila_diario.csv
#   f_gente_contact_diario.csv
#   f_gente_contact_fila_diario.csv
#   f_css_detalhado.csv
#   f_css_atendente.csv
#   f_css_periodo_atendente.csv
#   f_css_periodo_geral.csv
#   f_indicadores_gerais.csv
#   f_indicadores_gerais_periodo.csv
#
# V6:
# - Exporta CSV no padrão pt-BR para Power Query: separador ; e decimal ,.
# - Campos percentuais/taxas saem como razão 0 a 1, não como 0 a 100.
#   Exemplo: 0,9019 no CSV = 90,19% no Power BI.
# - Campos percentuais/taxas são arredondados em 4 casas no CSV para exibir 2 casas como %.
# - Demais números são arredondados em 2 casas.
# - Considera notas válidas de CSS somente de 1 a 5.
#
# Correções importantes:
# - Volume:
#   O campo "Concluído" do SSRS pode ficar maior que "Recebido".
#   Por isso, para Taxa de Atendimento usamos o total realmente atendido/tratado
#   do relatório, que corresponde à coluna 41 do layout e bate com:
#   Atendidas + Tratado do bloco de status do gráfico.
#
# - CSS:
#   O relatório "Script Result 5 - Agent Volume" NÃO possui data por linha
#   quando exportado com vários dias. Nesse caso, ele é consolidado de período.
#   Ele entra no arquivo f_indicadores_gerais_periodo.csv, mas não é forçado
#   no diário para não criar indicador falso.
#
# Para rodar:
#   python etl_contact_center_modelo_powerbi_v6_powerbi_percentual_br.py
#   python etl_contact_center_modelo_powerbi_v6_powerbi_percentual_br.py --base-dir "C:\RPA_SSRS"
#   python etl_contact_center_modelo_powerbi_v6_powerbi_percentual_br.py --entrada "C:\RPA_SSRS\entrada" --saida "C:\RPA_SSRS\saida"
# ============================================================


ARQUIVOS = {
    "volume": "Volume 4 - Daily",
    "agent": "Agent - Contact Handling Time 4 - Daily",
    "css": "Script Result 5 - Agent Volume",
    "css_fila_diario": "Script Result 3 - Queue Volume per Day",
}


# -------------------------
# Correções aplicadas (v4 -> v4_fix):
# 1. Volume Fila: filas sem dados (r[97] vazio) não geram mais registros zerados.
#    Antes: ~700 linhas fantasma com zeros eram inseridas por mês; agora são filtradas.
# 2. TME_Minutos / TMA_Minutos (Volume Geral e Fila): calculados ANTES da conversão
#    _Segundos para string hhmmss. No código anterior a divisão /60 operava sobre
#    a string '00:10:23' e produzia NaN silenciosamente.
# 3. TMA_Minutos no Agent Contact (tabelas fila e diario): mesma correção do item 2.
# 4. CSS indicadores_gerais_periodo: substituído iloc[0] por agregação real de todas
#    as linhas do período, evitando perda de dados quando o CSS contiver mais de um
#    bloco de período consolidado.
# -------------------------


# -------------------------
# Funções utilitárias
# -------------------------

def normalizar(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    texto = str(valor).strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def id_atendente(nome: str) -> str:
    texto = normalizar(nome).upper()
    texto = re.sub(r"[^A-Z0-9 ]", "", texto)
    texto = re.sub(r"\s+", "_", texto).strip("_")
    return texto


def numero(valor) -> float:
    if valor is None or pd.isna(valor):
        return 0.0

    texto = str(valor).strip()
    if texto == "":
        return 0.0

    texto = texto.replace("%", "")
    texto = texto.replace(".", "")
    texto = texto.replace(",", ".")
    texto = re.sub(r"[^0-9.\-]", "", texto)

    if texto in ["", "-", ".", "-."]:
        return 0.0

    try:
        return float(texto)
    except Exception:
        return 0.0


def inteiro(valor) -> int:
    return int(round(numero(valor)))


def percentual(valor) -> float:
    return numero(valor) / 100.0


def tempo_segundos(valor) -> int:
    if valor is None or pd.isna(valor):
        return 0

    texto = str(valor).strip()
    if texto == "":
        return 0

    partes = texto.split(":")
    try:
        partes = [int(float(p)) for p in partes]
        if len(partes) == 3:
            h, m, s = partes
            return h * 3600 + m * 60 + s
        if len(partes) == 2:
            m, s = partes
            return m * 60 + s
        if len(partes) == 1:
            return partes[0]
    except Exception:
        return 0

    return 0


def segundos_hhmmss(segundos) -> str:
    if pd.isna(segundos):
        segundos = 0
    segundos = int(round(segundos or 0))
    h = segundos // 3600
    m = (segundos % 3600) // 60
    s = segundos % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def divisao(numerador, denominador):
    if denominador is None or pd.isna(denominador) or denominador == 0:
        return pd.NA
    return numerador / denominador


def localizar_arquivo(pasta_entrada: Path, nome_base: str) -> Path:
    for ext in [".csv", ".xlsx", ".xls"]:
        caminho = pasta_entrada / f"{nome_base}{ext}"
        if caminho.exists():
            return caminho

    encontrados = sorted(pasta_entrada.glob(f"{nome_base}*"))
    if encontrados:
        return encontrados[0]

    raise FileNotFoundError(f"Arquivo não encontrado: {nome_base} em {pasta_entrada}")


def ler_arquivo(caminho: Path) -> List[List[str]]:
    if caminho.suffix.lower() == ".csv":
        for enc in ["utf-8-sig", "latin1"]:
            try:
                with open(caminho, encoding=enc, newline="") as f:
                    return list(csv.reader(f))
            except UnicodeDecodeError:
                continue

    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(caminho, header=None, dtype=str)
        return df.fillna("").values.tolist()

    raise ValueError(f"Formato não suportado: {caminho.suffix}")


COLUNAS_TEXTO_SAIDA = {
    "Data", "Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes", "Mes_Referencia", "Pasta_Origem",
    "Atendente_ID", "Atendente", "Grupo", "Fila", "Script", "Pergunta", "Resposta",
    "Classificacao_CSS", "Tem_Data_Diaria", "Tem_CSS_Diario", "Observacao_CSS", "Fonte_CSS_Diario",
    "TME", "TMA", "TMA_Atendentes", "Tempo_Pos_Processamento_Medio",
    "Arquivo_Origem", "Tipo_Carga", "Chave_FSR",
    "Tempo_Pos_Processamento_Max", "Tempo_Pos_Processamento_Fila_Medio", "Tempo_Pos_Processamento_Fila_Max",
}


def coluna_percentual_powerbi(coluna: str) -> bool:
    """
    Identifica campos que devem sair como razão 0 a 1 no CSV.

    Power BI/Power Query espera percentuais como fração:
    - 0,8000 vira 80,00% quando o campo é formatado como porcentagem.
    - 80,00 vira 8000,00% quando o campo é formatado como porcentagem.

    A vida já é difícil sem percentual sendo multiplicado duas vezes.
    """
    c = coluna.lower()
    return (
        c.startswith("taxa_")
        or "percentual" in c
        or c.startswith("css_positivo")
        or c.startswith("css_neutro")
        or c.startswith("css_negativo")
    )


def preparar_saida_csv(df: pd.DataFrame) -> pd.DataFrame:
    """
    Padroniza a saída final para o Power BI:
    - mantém colunas de texto/data como texto;
    - converte colunas numéricas possíveis;
    - percentuais/taxas ficam como razão 0 a 1 com 4 casas decimais;
    - demais números ficam com 2 casas decimais.

    Por que 4 casas nos percentuais?
    Porque 0,9333 formatado como % no Power BI aparece como 93,33%.
    Se salvar 0,93, o visual só consegue mostrar 93,00%.
    Matemática básica, mas aparentemente ela cobra pedágio no Power Query.
    """
    if df is None or df.empty:
        return df

    saida = df.copy()

    for col in saida.columns:
        if col in COLUNAS_TEXTO_SAIDA or col.upper().endswith("_ID"):
            continue

        casas = 4 if coluna_percentual_powerbi(col) else 2

        if pd.api.types.is_numeric_dtype(saida[col]):
            saida[col] = pd.to_numeric(saida[col], errors="coerce").round(casas)
            continue

        serie_texto = saida[col].astype(str).str.strip()
        serie_texto = serie_texto.replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA, "<NA>": pd.NA})
        serie_num = pd.to_numeric(serie_texto.str.replace(",", ".", regex=False), errors="coerce")

        total_nao_vazio = serie_texto.notna().sum()
        total_convertido = serie_num.notna().sum()

        if total_nao_vazio > 0 and (total_convertido / total_nao_vazio) >= 0.80:
            saida[col] = serie_num.round(casas)

    return saida


def salvar(df: pd.DataFrame, pasta_saida: Path, nome: str) -> Path:
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / nome
    df_saida = preparar_saida_csv(df)
    # Saída em padrão brasileiro: ; como separador e , como decimal.
    # Isso evita o Power Query transformar 90.19 em 9019, essa pequena tragédia regional.
    df_saida.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    return caminho


def extrair_datas_parametros(linhas: List[List[str]]) -> List[pd.Timestamp]:
    texto = "\n".join([" | ".join(map(str, linha)) for linha in linhas[:5]])
    datas = re.findall(r"\d{4}-\d{2}-\d{2}", texto)
    datas = sorted(set(datas))
    return [pd.to_datetime(d, errors="coerce") for d in datas if pd.notna(pd.to_datetime(d, errors="coerce"))]


def extrair_periodo_parametros(linhas: List[List[str]]) -> Tuple[pd.Timestamp, pd.Timestamp, bool]:
    datas = extrair_datas_parametros(linhas)
    if not datas:
        return pd.NaT, pd.NaT, False

    inicio = min(datas)
    fim = max(datas)
    unico_dia = len(set(datas)) == 1
    return inicio, fim, unico_dia


def adicionar_colunas_calendario(df: pd.DataFrame, coluna_data: str = "Data") -> pd.DataFrame:
    if coluna_data not in df.columns:
        return df

    df[coluna_data] = pd.to_datetime(df[coluna_data], errors="coerce")
    df["Ano"] = df[coluna_data].dt.year
    df["Mes"] = df[coluna_data].dt.month
    df["Dia"] = df[coluna_data].dt.day
    df["Ano_Mes"] = df[coluna_data].dt.strftime("%Y-%m")
    return df


def filtrar_ate_ultima_data_com_movimento(df: pd.DataFrame, colunas_movimento: List[str], coluna_data: str = "Data") -> pd.DataFrame:
    """
    Remove dias futuros/sem carga quando o SSRS exporta o ano inteiro com linhas zeradas.
    Mantém todos os dias até a última data em que houve algum movimento real.
    """
    if df is None or df.empty or coluna_data not in df.columns:
        return df

    base = df.copy()
    base[coluna_data] = pd.to_datetime(base[coluna_data], errors="coerce")
    colunas = [c for c in colunas_movimento if c in base.columns]

    if not colunas:
        return base

    movimento = base[colunas].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    datas_com_movimento = base.loc[movimento > 0, coluna_data].dropna()

    if datas_com_movimento.empty:
        return base

    ultima_data = datas_com_movimento.max()
    return base.loc[base[coluna_data] <= ultima_data].copy()


def classificar_css(nota) -> str:
    if pd.isna(nota) or nota < 1 or nota > 5:
        return "Inválida"
    if nota >= 4:
        return "Positiva"
    if nota == 3:
        return "Neutra"
    return "Negativa"


DATA_ATUALIZACAO_CARGA_MANUAL = None


def obter_data_atualizacao_carga() -> pd.Timestamp:
    """Data técnica da atualização, usada principalmente no CSS sem data por linha."""
    if DATA_ATUALIZACAO_CARGA_MANUAL:
        return pd.to_datetime(DATA_ATUALIZACAO_CARGA_MANUAL, errors="coerce").normalize()
    return pd.Timestamp.now().normalize()


# -------------------------
# VOLUME 4 - DAILY
# -------------------------

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


def tratar_volume_diario(pasta_entrada: Path, pasta_saida: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    caminho = localizar_arquivo(pasta_entrada, ARQUIVOS["volume"])
    linhas = ler_arquivo(caminho)

    registros_geral = []
    registros_fila = []

    for r in linhas:
        r = list(map(str, r))

        if len(r) >= 126 and re.match(r"\d{4}-\d{2}-\d{2}", r[36].strip()):
            data = pd.to_datetime(r[36].strip(), errors="coerce")
            if pd.isna(data):
                continue

            # Geral do dia
            registros_geral.append({
                "Data": data,
                "Recebidas": inteiro(r[37]),
                "Concluidas_SSRS": inteiro(r[38]),
                "Atendidas": inteiro(r[41]),  # campo corrigido para taxa
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

            # Por fila - só registra quando a fila tem dados reais (r[97] preenchido)
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
        raise ValueError("Nenhum dado diário encontrado no Volume 4 - Daily.")

    status = extrair_status_volume(linhas)
    if not status.empty:
        geral = geral.merge(status, on="Data", how="left", suffixes=("", "_StatusGrafico"))

        # Quando o bloco de status existir, ele é o melhor jeito de validar a coluna Atendidas.
        # Atendidas finais = Atendidas_Status + Tratado_Status.
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

    # Calcular minutos ANTES de converter _Segundos para string hhmmss
    geral["TME_Minutos"] = geral["TME_Segundos"] / 60
    geral["TMA_Minutos"] = geral["TMA_Segundos"] / 60

    for col in [
        "TME_Geral_SSRS_Segundos",
        "TME_Segundos",
        "TME_Max_Segundos",
        "TMA_Segundos",
        "TMA_Max_Segundos",
        "TME_Canceladas_Segundos",
        "TME_Canceladas_Max_Segundos",
    ]:
        nome = col.replace("_Segundos", "")
        geral[nome] = geral[col].apply(segundos_hhmmss)

    geral = adicionar_colunas_calendario(geral)
    geral = filtrar_ate_ultima_data_com_movimento(
        geral,
        ["Recebidas", "Atendidas", "Concluidas_SSRS", "Canceladas", "Falsas_Tentativas", "Servico_Fechado"],
    )

    ordem_geral = [
        "Data", "Ano", "Mes", "Dia", "Ano_Mes",
        "Recebidas", "Atendidas", "Abandonadas", "Canceladas",
        "Concluidas_SSRS", "Atendidas_Status", "Tratado_Status",
        "Falsas_Tentativas", "Servico_Fechado",
        "Taxa_Atendimento", "Taxa_Abandono", "Taxa_Falsa_Tentativa", "Taxa_Conclusao_SSRS",
        "Nivel_Servico_SSRS",
        "Transferencias", "Taxa_Transferencia", "Estouro", "Taxa_Estouro",
        "TME", "TME_Segundos", "TME_Minutos",
        "TME_Geral_SSRS", "TME_Geral_SSRS_Segundos",
        "TME_Max", "TME_Max_Segundos",
        "Total_Espera_Atendidas_Segundos", "Total_Espera_Geral_Segundos",
        "TMA", "TMA_Segundos", "TMA_Minutos",
        "TMA_Max", "TMA_Max_Segundos", "TMA_Total_Segundos",
        "TME_Canceladas", "TME_Canceladas_Segundos",
        "TME_Canceladas_Max", "TME_Canceladas_Max_Segundos",
        "Total_Espera_Canceladas_Segundos",
        "Contatos_Simultaneos_Max",
    ]

    for c in ordem_geral:
        if c not in geral.columns:
            geral[c] = pd.NA

    geral = geral[ordem_geral].sort_values("Data")

    if not fila.empty:
        fila["Abandonadas"] = fila["Canceladas"].fillna(0).astype(int)
        fila["Taxa_Atendimento"] = fila.apply(lambda x: divisao(x["Atendidas"], x["Recebidas"]), axis=1)
        fila["Taxa_Abandono"] = fila.apply(lambda x: divisao(x["Abandonadas"], x["Recebidas"]), axis=1)
        # Calcular minutos ANTES de converter _Segundos para string hhmmss
        fila["TME_Minutos"] = fila["TME_Segundos"] / 60
        fila["TMA_Minutos"] = fila["TMA_Segundos"] / 60
        for col in ["TME_Segundos", "TME_Geral_SSRS_Segundos", "TMA_Segundos", "TMA_Max_Segundos"]:
            nome = col.replace("_Segundos", "")
            fila[nome] = fila[col].apply(segundos_hhmmss)
        fila = adicionar_colunas_calendario(fila)
        fila = fila.sort_values(["Data", "Fila"])

    salvar(geral, pasta_saida, "f_volume_geral_diario.csv")
    salvar(fila, pasta_saida, "f_volume_fila_diario.csv")
    return geral, fila


# -------------------------
# AGENT CONTACT HANDLING
# -------------------------

def tratar_agent_contact_diario(pasta_entrada: Path, pasta_saida: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    caminho = localizar_arquivo(pasta_entrada, ARQUIVOS["agent"])
    linhas = ler_arquivo(caminho)

    registros_fila = []

    for r in linhas:
        r = list(map(str, r))

        if len(r) >= 44 and re.match(r"\d{4}-\d{2}-\d{2}", r[28].strip()):
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
                "Total_Ligacoes_Fila": inteiro(r[37]),
                "TMA_Fila_Segundos": tempo_segundos(r[38]),
                "TMA_Fila_Max_Segundos": tempo_segundos(r[39]),
                "Tempo_Processamento_Fila_Total_Segundos": tempo_segundos(r[40]),
                "Tempo_Pos_Processamento_Fila_Medio_Segundos": tempo_segundos(r[41]),
                "Tempo_Pos_Processamento_Fila_Max_Segundos": tempo_segundos(r[42]),
                "Tempo_Pos_Processamento_Fila_Total_Segundos": tempo_segundos(r[43]),
            })

    fila = pd.DataFrame(registros_fila).drop_duplicates()

    if fila.empty:
        raise ValueError("Nenhum dado diário encontrado no Agent Contact.")

    fila = fila.dropna(subset=["Data"])

    # Calcular minutos ANTES de converter _Segundos para string hhmmss
    fila["TMA_Minutos"] = fila["TMA_Segundos"] / 60
    fila["TMA_Fila_Minutos"] = fila["TMA_Fila_Segundos"] / 60

    for col in [
        "TMA_Segundos", "TMA_Max_Segundos",
        "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max_Segundos",
        "TMA_Fila_Segundos", "TMA_Fila_Max_Segundos",
        "Tempo_Pos_Processamento_Fila_Medio_Segundos", "Tempo_Pos_Processamento_Fila_Max_Segundos",
    ]:
        nome = col.replace("_Segundos", "")
        fila[nome] = fila[col].apply(segundos_hhmmss)

    fila = adicionar_colunas_calendario(fila)

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

    # Calcular minutos ANTES de converter _Segundos para string hhmmss
    diario["TMA_Minutos"] = diario["TMA_Segundos"] / 60

    for col in [
        "TMA_Segundos", "TMA_Max_Segundos",
        "Tempo_Pos_Processamento_Medio_Segundos", "Tempo_Pos_Processamento_Max_Segundos",
    ]:
        nome = col.replace("_Segundos", "")
        diario[nome] = diario[col].apply(segundos_hhmmss)

    diario = adicionar_colunas_calendario(diario)

    ordem_diario = [
        "Data", "Ano", "Mes", "Dia", "Ano_Mes",
        "Atendente_ID", "Atendente", "Grupo",
        "Total_Ligacoes", "Qtd_Filas_Atendidas",
        "TMA", "TMA_Segundos", "TMA_Minutos",
        "TMA_Max", "TMA_Max_Segundos",
        "Tempo_Processamento_Total_Segundos",
        "Tempo_Pos_Processamento_Medio", "Tempo_Pos_Processamento_Medio_Segundos",
        "Tempo_Pos_Processamento_Max", "Tempo_Pos_Processamento_Max_Segundos",
        "Tempo_Pos_Processamento_Total_Segundos",
    ]

    for c in ordem_diario:
        if c not in diario.columns:
            diario[c] = pd.NA

    diario = diario[ordem_diario].sort_values(["Data", "Atendente"])
    fila = fila.sort_values(["Data", "Atendente", "Fila"])

    salvar(diario, pasta_saida, "f_gente_contact_diario.csv")
    salvar(fila, pasta_saida, "f_gente_contact_fila_diario.csv")
    return diario, fila



# -------------------------
# CSS
# -------------------------

def tratar_css(pasta_entrada: Path, pasta_saida: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Trata o relatório Script Result 5 - Agent Volume.

    Observação importante, porque aparentemente até relatório tem personalidade:
    quando o arquivo vem com vários dias, ele não traz uma data por linha. Nesse caso,
    os resultados por agente são considerados consolidados de período. Quando o arquivo
    é exportado com apenas um dia, a data é capturada nos parâmetros e entra como diária.
    """
    caminho = localizar_arquivo(pasta_entrada, ARQUIVOS["css"])
    linhas = ler_arquivo(caminho)

    periodo_inicio, periodo_fim, unico_dia = extrair_periodo_parametros(linhas)
    data_ref = periodo_inicio if unico_dia else pd.NaT
    data_atualizacao_carga = obter_data_atualizacao_carga()

    registros = []

    for r in linhas:
        r = list(map(str, r))

        if len(r) >= 18:
            atendente = normalizar(r[7])
            script = normalizar(r[10])
            pergunta = normalizar(r[13])
            resposta = normalizar(r[16])
            qtd = inteiro(r[17])

            if (
                atendente
                and atendente.lower() not in ["agent_name_1", "agente", "agent"]
                and pergunta
                and resposta
                and qtd > 0
            ):
                nota = pd.to_numeric(resposta, errors="coerce")
                classificacao = classificar_css(nota)

                if classificacao == "Inválida":
                    nota_x_qtd = 0
                    nota_aproveitamento_percentual = pd.NA
                else:
                    nota_x_qtd = nota * qtd
                    nota_aproveitamento_percentual = nota / 5

                ano_mes = ""
                if pd.notna(periodo_inicio):
                    ano_mes = periodo_inicio.strftime("%Y-%m")

                registros.append({
                    "Data": data_ref,
                    "Data_Atualizacao_Carga": data_atualizacao_carga,
                    "Periodo_Inicio": periodo_inicio,
                    "Periodo_Fim": periodo_fim,
                    "Ano_Mes": ano_mes,
                    "Tem_Data_Diaria": bool(unico_dia),
                    "Atendente_ID": id_atendente(atendente),
                    "Atendente": atendente,
                    "Script": script,
                    "Pergunta": pergunta,
                    "Resposta": resposta,
                    "Nota_CSS": nota,
                    "Nota_CSS_Aproveitamento_Percentual": nota_aproveitamento_percentual,
                    "Quantidade": qtd,
                    "Classificacao_CSS": classificacao,
                    "Nota_x_Qtd": nota_x_qtd,
                    "Qtd_Positiva": qtd if classificacao == "Positiva" else 0,
                    "Qtd_Neutra": qtd if classificacao == "Neutra" else 0,
                    "Qtd_Negativa": qtd if classificacao == "Negativa" else 0,
                    "Qtd_Invalida": qtd if classificacao == "Inválida" else 0,
                })

    df = pd.DataFrame(registros)

    if df.empty:
        raise ValueError("Nenhum dado encontrado no CSS por agente.")

    validas = df[df["Classificacao_CSS"] != "Inválida"].copy()

    resumo = (
        validas.groupby(
            ["Data", "Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes", "Tem_Data_Diaria", "Atendente_ID", "Atendente"],
            dropna=False,
            as_index=False
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

    periodo_atendente = (
        validas.groupby(
            ["Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes", "Atendente_ID", "Atendente"],
            dropna=False,
            as_index=False
        )
        .agg(
            Total_Respostas_CSS=("Quantidade", "sum"),
            Qtd_Positiva=("Qtd_Positiva", "sum"),
            Qtd_Neutra=("Qtd_Neutra", "sum"),
            Qtd_Negativa=("Qtd_Negativa", "sum"),
            Soma_Nota_Ponderada=("Nota_x_Qtd", "sum"),
        )
    )

    periodo_atendente["CSS_Medio"] = periodo_atendente["Soma_Nota_Ponderada"] / periodo_atendente["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_atendente["CSS_Aproveitamento_Percentual"] = periodo_atendente["CSS_Medio"] / 5
    periodo_atendente["CSS_Positivo"] = periodo_atendente["Qtd_Positiva"] / periodo_atendente["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_atendente["CSS_Neutro"] = periodo_atendente["Qtd_Neutra"] / periodo_atendente["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_atendente["CSS_Negativo"] = periodo_atendente["Qtd_Negativa"] / periodo_atendente["Total_Respostas_CSS"].replace(0, pd.NA)

    periodo_geral = (
        validas.groupby(
            ["Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes"],
            dropna=False,
            as_index=False
        )
        .agg(
            Total_Respostas_CSS=("Quantidade", "sum"),
            Qtd_Positiva=("Qtd_Positiva", "sum"),
            Qtd_Neutra=("Qtd_Neutra", "sum"),
            Qtd_Negativa=("Qtd_Negativa", "sum"),
            Soma_Nota_Ponderada=("Nota_x_Qtd", "sum"),
        )
    )

    periodo_geral["CSS_Medio_Geral"] = periodo_geral["Soma_Nota_Ponderada"] / periodo_geral["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_geral["CSS_Aproveitamento_Percentual_Geral"] = periodo_geral["CSS_Medio_Geral"] / 5
    periodo_geral["CSS_Positivo_Geral"] = periodo_geral["Qtd_Positiva"] / periodo_geral["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_geral["CSS_Neutro_Geral"] = periodo_geral["Qtd_Neutra"] / periodo_geral["Total_Respostas_CSS"].replace(0, pd.NA)
    periodo_geral["CSS_Negativo_Geral"] = periodo_geral["Qtd_Negativa"] / periodo_geral["Total_Respostas_CSS"].replace(0, pd.NA)

    salvar(df, pasta_saida, "f_css_detalhado.csv")
    salvar(resumo.sort_values(["Atendente"]), pasta_saida, "f_css_atendente.csv")
    salvar(periodo_atendente.sort_values(["Atendente"]), pasta_saida, "f_css_periodo_atendente.csv")
    salvar(periodo_geral, pasta_saida, "f_css_periodo_geral.csv")

    return df, resumo, periodo_atendente, periodo_geral


def tratar_css_fila_diario(pasta_entrada: Path, pasta_saida: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Trata o relatório Script Result 3 - Queue Volume per Day.

    Esse é o relatório que resolve o CSS diário geral. O Agent Volume aprofunda por agente,
    mas não traz data por linha quando exportado para vários dias. Este aqui traz Data + Fila,
    então vira a base diária oficial para CSS geral e CSS por fila.
    """
    try:
        caminho = localizar_arquivo(pasta_entrada, ARQUIVOS["css_fila_diario"])
    except FileNotFoundError:
        vazio_fila = pd.DataFrame()
        vazio_geral = pd.DataFrame()
        vazio_detalhe = pd.DataFrame()
        salvar(vazio_detalhe, pasta_saida, "f_css_fila_detalhado_diario.csv")
        salvar(vazio_fila, pasta_saida, "f_css_fila_diario.csv")
        salvar(vazio_geral, pasta_saida, "f_css_geral_diario.csv")
        return vazio_detalhe, vazio_fila, vazio_geral

    linhas = ler_arquivo(caminho)
    data_atualizacao_carga = obter_data_atualizacao_carga()
    registros = []

    for r in linhas:
        r = list(map(str, r))

        if len(r) >= 25 and re.match(r"\d{4}-\d{2}-\d{2}", r[10].strip()):
            data = pd.to_datetime(r[10].strip(), errors="coerce")
            fila = normalizar(r[11])
            script = normalizar(r[15])
            pergunta = normalizar(r[18])
            resposta = normalizar(r[21])
            qtd = inteiro(r[22])

            if pd.isna(data) or not fila or not pergunta or not resposta or qtd <= 0:
                continue

            nota = pd.to_numeric(resposta, errors="coerce")
            classificacao = classificar_css(nota)

            if classificacao == "Inválida":
                nota_x_qtd = 0
                nota_aproveitamento_percentual = pd.NA
            else:
                nota_x_qtd = nota * qtd
                nota_aproveitamento_percentual = nota / 5

            registros.append({
                "Data": data,
                "Data_Atualizacao_Carga": data_atualizacao_carga,
                "Fila": fila,
                "Script": script,
                "Pergunta": pergunta,
                "Resposta": resposta,
                "Nota_CSS": nota,
                "Nota_CSS_Aproveitamento_Percentual": nota_aproveitamento_percentual,
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
        vazio_fila = pd.DataFrame()
        vazio_geral = pd.DataFrame()
        salvar(detalhe, pasta_saida, "f_css_fila_detalhado_diario.csv")
        salvar(vazio_fila, pasta_saida, "f_css_fila_diario.csv")
        salvar(vazio_geral, pasta_saida, "f_css_geral_diario.csv")
        return detalhe, vazio_fila, vazio_geral

    validas = detalhe[detalhe["Classificacao_CSS"] != "Inválida"].copy()

    fila = (
        validas.groupby(["Data", "Data_Atualizacao_Carga", "Fila"], as_index=False)
        .agg(
            Total_Respostas_CSS=("Quantidade", "sum"),
            Qtd_Positiva=("Qtd_Positiva", "sum"),
            Qtd_Neutra=("Qtd_Neutra", "sum"),
            Qtd_Negativa=("Qtd_Negativa", "sum"),
            Soma_Nota_Ponderada=("Nota_x_Qtd", "sum"),
        )
    )

    fila["CSS_Medio"] = fila["Soma_Nota_Ponderada"] / fila["Total_Respostas_CSS"].replace(0, pd.NA)
    fila["CSS_Aproveitamento_Percentual"] = fila["CSS_Medio"] / 5
    fila["CSS_Positivo"] = fila["Qtd_Positiva"] / fila["Total_Respostas_CSS"].replace(0, pd.NA)
    fila["CSS_Neutro"] = fila["Qtd_Neutra"] / fila["Total_Respostas_CSS"].replace(0, pd.NA)
    fila["CSS_Negativo"] = fila["Qtd_Negativa"] / fila["Total_Respostas_CSS"].replace(0, pd.NA)
    fila = adicionar_colunas_calendario(fila)

    geral = (
        validas.groupby(["Data", "Data_Atualizacao_Carga"], as_index=False)
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

    detalhe = adicionar_colunas_calendario(detalhe)

    salvar(detalhe.sort_values(["Data", "Fila", "Resposta"]), pasta_saida, "f_css_fila_detalhado_diario.csv")
    salvar(fila.sort_values(["Data", "Fila"]), pasta_saida, "f_css_fila_diario.csv")
    salvar(geral.sort_values(["Data"]), pasta_saida, "f_css_geral_diario.csv")

    return detalhe, fila, geral


# -------------------------
# DIMENSÃO E INDICADORES
# -------------------------

def criar_dim_atendentes(agent: pd.DataFrame, css_atendente: pd.DataFrame, pasta_saida: Path) -> pd.DataFrame:
    bases = []

    if agent is not None and not agent.empty:
        bases.append(agent[["Atendente_ID", "Atendente", "Grupo"]].drop_duplicates())

    if css_atendente is not None and not css_atendente.empty:
        temp = css_atendente[["Atendente_ID", "Atendente"]].drop_duplicates()
        temp["Grupo"] = ""
        bases.append(temp)

    if not bases:
        dim = pd.DataFrame(columns=["Atendente_ID", "Atendente", "Grupo"])
    else:
        dim = pd.concat(bases, ignore_index=True).drop_duplicates()
        dim = (
            dim.sort_values(["Atendente", "Grupo"])
            .groupby(["Atendente_ID", "Atendente"], as_index=False)
            .agg(Grupo=("Grupo", lambda x: next((str(v) for v in x if str(v).strip()), "")))
        )

    salvar(dim, pasta_saida, "dim_atendentes.csv")
    return dim



def criar_indicadores_agentes(
    agent: pd.DataFrame,
    css_atendente: pd.DataFrame,
    pasta_saida: Path,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Cria uma tabela de indicadores por atendente.
    No recorte diário, o CSS por agente entra quando o Agent Volume foi exportado com 1 dia.
    No período, o CSS por agente entra mesmo quando o relatório veio consolidado.
    """
    if agent is None or agent.empty:
        diario = pd.DataFrame()
    else:
        diario = agent.copy()
        diario["Data"] = pd.to_datetime(diario["Data"], errors="coerce")

    if css_atendente is not None and not css_atendente.empty:
        c = css_atendente.copy()
        c["Data"] = pd.to_datetime(c["Data"], errors="coerce")

        c_diario = c.dropna(subset=["Data"]).copy()
        if not c_diario.empty and not diario.empty:
            diario = diario.merge(
                c_diario[[
                    "Data", "Atendente_ID", "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
                    "CSS_Medio", "CSS_Aproveitamento_Percentual", "CSS_Positivo", "CSS_Neutro", "CSS_Negativo",
                ]],
                on=["Data", "Atendente_ID"],
                how="left",
            )

        periodo = (
            c.groupby(["Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes", "Atendente_ID", "Atendente"], dropna=False, as_index=False)
            .agg(
                Total_Respostas_CSS=("Total_Respostas_CSS", "sum"),
                Qtd_Positiva=("Qtd_Positiva", "sum"),
                Qtd_Neutra=("Qtd_Neutra", "sum"),
                Qtd_Negativa=("Qtd_Negativa", "sum"),
                Soma_Nota_Ponderada=("Soma_Nota_Ponderada", "sum"),
            )
        )
        periodo["CSS_Medio"] = periodo["Soma_Nota_Ponderada"] / periodo["Total_Respostas_CSS"].replace(0, pd.NA)
        periodo["CSS_Aproveitamento_Percentual"] = periodo["CSS_Medio"] / 5
        periodo["CSS_Positivo"] = periodo["Qtd_Positiva"] / periodo["Total_Respostas_CSS"].replace(0, pd.NA)
        periodo["CSS_Neutro"] = periodo["Qtd_Neutra"] / periodo["Total_Respostas_CSS"].replace(0, pd.NA)
        periodo["CSS_Negativo"] = periodo["Qtd_Negativa"] / periodo["Total_Respostas_CSS"].replace(0, pd.NA)
    else:
        periodo = pd.DataFrame()

    if not diario.empty:
        for col in [
            "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
            "CSS_Medio", "CSS_Aproveitamento_Percentual", "CSS_Positivo", "CSS_Neutro", "CSS_Negativo",
        ]:
            if col not in diario.columns:
                diario[col] = pd.NA
        diario = diario.sort_values(["Data", "Atendente"])

    salvar(diario, pasta_saida, "f_indicadores_agentes_diario.csv")
    salvar(periodo.sort_values(["Atendente"]) if not periodo.empty else periodo, pasta_saida, "f_indicadores_agentes_periodo.csv")
    return diario, periodo


def criar_indicadores_gerais(
    volume: pd.DataFrame,
    agent: pd.DataFrame,
    css_atendente: pd.DataFrame,
    css_periodo_geral: pd.DataFrame,
    css_geral_diario: Optional[pd.DataFrame],
    pasta_saida: Path,
) -> Tuple[pd.DataFrame, pd.DataFrame]:

    geral = volume.copy()
    geral["Data"] = pd.to_datetime(geral["Data"], errors="coerce")

    # Agent por dia
    if agent is not None and not agent.empty:
        a = agent.copy()
        a["Data"] = pd.to_datetime(a["Data"], errors="coerce")
        a["TMA_x_Ligacoes"] = a["TMA_Segundos"] * a["Total_Ligacoes"]

        agent_dia = (
            a.groupby("Data", as_index=False)
            .agg(
                Total_Ligacoes_Atendentes=("Total_Ligacoes", "sum"),
                Atendentes_Ativos=("Atendente_ID", "nunique"),
                Soma_TMA_x_Ligacoes=("TMA_x_Ligacoes", "sum"),
            )
        )

        agent_dia["TMA_Atendentes_Segundos"] = (
            agent_dia["Soma_TMA_x_Ligacoes"]
            / agent_dia["Total_Ligacoes_Atendentes"].replace(0, pd.NA)
        )
        agent_dia["TMA_Atendentes"] = agent_dia["TMA_Atendentes_Segundos"].apply(segundos_hhmmss)
        agent_dia["TMA_Atendentes_Minutos"] = agent_dia["TMA_Atendentes_Segundos"] / 60

        geral = geral.merge(
            agent_dia[[
                "Data", "Total_Ligacoes_Atendentes", "Atendentes_Ativos",
                "TMA_Atendentes", "TMA_Atendentes_Segundos", "TMA_Atendentes_Minutos"
            ]],
            on="Data",
            how="left",
        )
    else:
        geral["Total_Ligacoes_Atendentes"] = 0
        geral["Atendentes_Ativos"] = 0
        geral["TMA_Atendentes"] = "00:00:00"
        geral["TMA_Atendentes_Segundos"] = 0
        geral["TMA_Atendentes_Minutos"] = 0

    # CSS diário oficial: primeiro tenta o Queue Volume per Day. Se não existir, cai no Agent Volume diário.
    geral["Tem_CSS_Diario"] = False
    geral["Fonte_CSS_Diario"] = ""
    geral["Observacao_CSS"] = "CSS diário não importado. Envie o relatório Script Result 3 - Queue Volume per Day ou o Agent Volume filtrado em 1 dia."

    if css_geral_diario is not None and not css_geral_diario.empty:
        cd = css_geral_diario.copy()
        cd["Data"] = pd.to_datetime(cd["Data"], errors="coerce")
        cd = cd.dropna(subset=["Data"])

        if not cd.empty:
            geral = geral.merge(
                cd[[
                    "Data", "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
                    "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral",
                    "CSS_Neutro_Geral", "CSS_Negativo_Geral"
                ]],
                on="Data",
                how="left",
            )
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Tem_CSS_Diario"] = True
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Fonte_CSS_Diario"] = "Script Result 3 - Queue Volume per Day"
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Observacao_CSS"] = "CSS diário importado por Data/Fila."

    if ("Total_Respostas_CSS" not in geral.columns or geral["Total_Respostas_CSS"].isna().all()) and css_atendente is not None and not css_atendente.empty:
        c = css_atendente.copy()
        c["Data"] = pd.to_datetime(c["Data"], errors="coerce")
        c = c.dropna(subset=["Data"])

        if not c.empty:
            c["CSS_x_Respostas"] = c["CSS_Medio"] * c["Total_Respostas_CSS"]
            css_dia = (
                c.groupby("Data", as_index=False)
                .agg(
                    Total_Respostas_CSS=("Total_Respostas_CSS", "sum"),
                    Qtd_Positiva=("Qtd_Positiva", "sum"),
                    Qtd_Neutra=("Qtd_Neutra", "sum"),
                    Qtd_Negativa=("Qtd_Negativa", "sum"),
                    Soma_CSS_x_Respostas=("CSS_x_Respostas", "sum"),
                )
            )
            css_dia["CSS_Medio_Geral"] = css_dia["Soma_CSS_x_Respostas"] / css_dia["Total_Respostas_CSS"].replace(0, pd.NA)
            css_dia["CSS_Aproveitamento_Percentual_Geral"] = css_dia["CSS_Medio_Geral"] / 5
            css_dia["CSS_Positivo_Geral"] = css_dia["Qtd_Positiva"] / css_dia["Total_Respostas_CSS"].replace(0, pd.NA)
            css_dia["CSS_Neutro_Geral"] = css_dia["Qtd_Neutra"] / css_dia["Total_Respostas_CSS"].replace(0, pd.NA)
            css_dia["CSS_Negativo_Geral"] = css_dia["Qtd_Negativa"] / css_dia["Total_Respostas_CSS"].replace(0, pd.NA)

            geral = geral.merge(
                css_dia[[
                    "Data", "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra",
                    "Qtd_Negativa", "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral",
                    "CSS_Neutro_Geral", "CSS_Negativo_Geral"
                ]],
                on="Data",
                how="left",
                suffixes=("", "_AgentVolume"),
            )

            for col in [
                "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
                "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral", "CSS_Neutro_Geral", "CSS_Negativo_Geral",
            ]:
                col_agent = f"{col}_AgentVolume"
                if col_agent in geral.columns:
                    geral[col] = geral[col].combine_first(geral[col_agent]) if col in geral.columns else geral[col_agent]
                    geral = geral.drop(columns=[col_agent])

            geral.loc[geral["Total_Respostas_CSS"].notna(), "Tem_CSS_Diario"] = True
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Fonte_CSS_Diario"] = "Script Result 5 - Agent Volume filtrado em 1 dia"
            geral.loc[geral["Total_Respostas_CSS"].notna(), "Observacao_CSS"] = "CSS diário importado pelo Agent Volume filtrado em 1 dia."

    for col in [
        "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
        "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral", "CSS_Neutro_Geral", "CSS_Negativo_Geral",
    ]:
        if col not in geral.columns:
            geral[col] = pd.NA

    colunas_diario = [
        "Data", "Ano", "Mes", "Dia", "Ano_Mes",
        "Recebidas", "Atendidas", "Abandonadas", "Canceladas", "Concluidas_SSRS",
        "Atendidas_Status", "Tratado_Status", "Falsas_Tentativas", "Servico_Fechado",
        "Taxa_Atendimento", "Taxa_Abandono", "Taxa_Falsa_Tentativa", "Taxa_Conclusao_SSRS",
        "Nivel_Servico_SSRS",
        "TME", "TME_Segundos", "TME_Minutos",
        "TMA", "TMA_Segundos", "TMA_Minutos",
        "Total_Ligacoes_Atendentes", "Atendentes_Ativos",
        "TMA_Atendentes", "TMA_Atendentes_Segundos", "TMA_Atendentes_Minutos",
        "Total_Respostas_CSS", "Qtd_Positiva", "Qtd_Neutra", "Qtd_Negativa",
        "CSS_Medio_Geral", "CSS_Aproveitamento_Percentual_Geral", "CSS_Positivo_Geral", "CSS_Neutro_Geral", "CSS_Negativo_Geral",
        "Tem_CSS_Diario", "Fonte_CSS_Diario", "Observacao_CSS",
    ]

    for col in colunas_diario:
        if col not in geral.columns:
            geral[col] = pd.NA

    geral = geral[colunas_diario].sort_values("Data")

    # Indicadores de período
    v = volume.copy()
    periodo_inicio = v["Data"].min()
    periodo_fim = v["Data"].max()
    ano_mes = periodo_inicio.strftime("%Y-%m") if pd.notna(periodo_inicio) else ""

    resumo_periodo = {
        "Periodo_Inicio": periodo_inicio,
        "Periodo_Fim": periodo_fim,
        "Ano_Mes": ano_mes,
        "Recebidas": v["Recebidas"].sum(),
        "Atendidas": v["Atendidas"].sum(),
        "Abandonadas": v["Abandonadas"].sum(),
        "Canceladas": v["Canceladas"].sum(),
        "Concluidas_SSRS": v["Concluidas_SSRS"].sum(),
        "Falsas_Tentativas": v["Falsas_Tentativas"].sum(),
        "Servico_Fechado": v["Servico_Fechado"].sum(),
    }

    resumo_periodo["Taxa_Atendimento"] = divisao(resumo_periodo["Atendidas"], resumo_periodo["Recebidas"])
    resumo_periodo["Taxa_Abandono"] = divisao(resumo_periodo["Abandonadas"], resumo_periodo["Recebidas"])
    resumo_periodo["Taxa_Falsa_Tentativa"] = divisao(resumo_periodo["Falsas_Tentativas"], resumo_periodo["Recebidas"])
    resumo_periodo["Taxa_Conclusao_SSRS"] = divisao(resumo_periodo["Concluidas_SSRS"], resumo_periodo["Recebidas"])

    # Médias ponderadas do período
    tme_base = (v["TME_Segundos"] * v["Atendidas"]).sum()
    tma_base = (v["TMA_Segundos"] * v["Atendidas"]).sum()
    resumo_periodo["TME_Segundos"] = divisao(tme_base, resumo_periodo["Atendidas"])
    resumo_periodo["TMA_Segundos"] = divisao(tma_base, resumo_periodo["Atendidas"])
    resumo_periodo["TME"] = segundos_hhmmss(resumo_periodo["TME_Segundos"])
    resumo_periodo["TMA"] = segundos_hhmmss(resumo_periodo["TMA_Segundos"])

    if agent is not None and not agent.empty:
        total_lig = agent["Total_Ligacoes"].sum()
        resumo_periodo["Total_Ligacoes_Atendentes"] = total_lig
        resumo_periodo["Atendentes_Ativos"] = agent["Atendente_ID"].nunique()
        resumo_periodo["TMA_Atendentes_Segundos"] = divisao((agent["TMA_Segundos"] * agent["Total_Ligacoes"]).sum(), total_lig)
        resumo_periodo["TMA_Atendentes"] = segundos_hhmmss(resumo_periodo["TMA_Atendentes_Segundos"])
    else:
        resumo_periodo["Total_Ligacoes_Atendentes"] = 0
        resumo_periodo["Atendentes_Ativos"] = 0
        resumo_periodo["TMA_Atendentes_Segundos"] = pd.NA
        resumo_periodo["TMA_Atendentes"] = "00:00:00"

    fonte_periodo = None
    if css_geral_diario is not None and not css_geral_diario.empty:
        cpg_total = css_geral_diario["Total_Respostas_CSS"].sum()
        cpg_pos = css_geral_diario["Qtd_Positiva"].sum()
        cpg_neu = css_geral_diario["Qtd_Neutra"].sum()
        cpg_neg = css_geral_diario["Qtd_Negativa"].sum()
        soma_pond = css_geral_diario["Soma_Nota_Ponderada"].sum()
        fonte_periodo = "Script Result 3 - Queue Volume per Day"
    elif css_periodo_geral is not None and not css_periodo_geral.empty:
        cpg_total = css_periodo_geral["Total_Respostas_CSS"].sum()
        cpg_pos = css_periodo_geral["Qtd_Positiva"].sum()
        cpg_neu = css_periodo_geral["Qtd_Neutra"].sum()
        cpg_neg = css_periodo_geral["Qtd_Negativa"].sum()
        soma_pond = css_periodo_geral["Soma_Nota_Ponderada"].sum() if "Soma_Nota_Ponderada" in css_periodo_geral.columns else (css_periodo_geral["CSS_Medio_Geral"] * css_periodo_geral["Total_Respostas_CSS"]).sum()
        fonte_periodo = "Script Result 5 - Agent Volume"
    else:
        cpg_total = cpg_pos = cpg_neu = cpg_neg = pd.NA
        soma_pond = pd.NA

    if fonte_periodo:
        css_medio = soma_pond / cpg_total if cpg_total > 0 else pd.NA
        resumo_periodo.update({
            "Total_Respostas_CSS": cpg_total,
            "Qtd_Positiva": cpg_pos,
            "Qtd_Neutra": cpg_neu,
            "Qtd_Negativa": cpg_neg,
            "CSS_Medio_Geral": css_medio,
            "CSS_Aproveitamento_Percentual_Geral": (css_medio / 5) if pd.notna(css_medio) else pd.NA,
            "CSS_Positivo_Geral": divisao(cpg_pos, cpg_total),
            "CSS_Neutro_Geral": divisao(cpg_neu, cpg_total),
            "CSS_Negativo_Geral": divisao(cpg_neg, cpg_total),
            "Fonte_CSS_Periodo": fonte_periodo,
        })
    else:
        resumo_periodo.update({
            "Total_Respostas_CSS": pd.NA,
            "Qtd_Positiva": pd.NA,
            "Qtd_Neutra": pd.NA,
            "Qtd_Negativa": pd.NA,
            "CSS_Medio_Geral": pd.NA,
            "CSS_Aproveitamento_Percentual_Geral": pd.NA,
            "CSS_Positivo_Geral": pd.NA,
            "CSS_Neutro_Geral": pd.NA,
            "CSS_Negativo_Geral": pd.NA,
            "Fonte_CSS_Periodo": "",
        })

    periodo = pd.DataFrame([resumo_periodo])

    salvar(geral, pasta_saida, "f_indicadores_gerais.csv")
    salvar(periodo, pasta_saida, "f_indicadores_gerais_periodo.csv")
    return geral, periodo




# -------------------------
# PROCESSAMENTO HISTÓRICO POR PASTAS MENSAIS
# -------------------------

def parse_mes_pasta(nome: str) -> Tuple[int, int]:
    """
    Aceita nomes como:
    - 01_2026
    - 01-2026
    - 2026_01
    - 2026-01
    Retorna (ano, mes). Se não reconhecer, joga para o fim da ordenação.
    """
    n = nome.strip()

    m = re.match(r"^(0[1-9]|1[0-2])[_\-](\d{4})$", n)
    if m:
        return int(m.group(2)), int(m.group(1))

    m = re.match(r"^(\d{4})[_\-](0[1-9]|1[0-2])$", n)
    if m:
        return int(m.group(1)), int(m.group(2))

    return 9999, 99


def pasta_tem_relatorios(pasta: Path) -> bool:
    if not pasta.is_dir():
        return False

    try:
        localizar_arquivo(pasta, ARQUIVOS["volume"])
        localizar_arquivo(pasta, ARQUIVOS["agent"])
        localizar_arquivo(pasta, ARQUIVOS["css"])
        return True
    except FileNotFoundError:
        return False


def localizar_pastas_processamento(pasta_entrada: Path) -> List[Path]:
    """
    Se a pasta de entrada tiver arquivos direto nela, processa como recorte único.
    Se tiver subpastas mensais 01_2026, 02_2026 etc., processa todas.
    """
    if pasta_tem_relatorios(pasta_entrada):
        return [pasta_entrada]

    subpastas = [p for p in pasta_entrada.iterdir() if p.is_dir()]

    # Primeiro prioriza padrão mensal. Se não achar, tenta qualquer subpasta com os 3 relatórios.
    mensais = [p for p in subpastas if parse_mes_pasta(p.name) != (9999, 99)]
    candidatas = mensais if mensais else subpastas

    pastas_validas = [p for p in candidatas if pasta_tem_relatorios(p)]
    pastas_validas = sorted(pastas_validas, key=lambda p: parse_mes_pasta(p.name))

    if not pastas_validas:
        raise FileNotFoundError(
            f"Nenhum conjunto completo de relatórios encontrado em {pasta_entrada}. "
            "A estrutura esperada é entrada\\01_2026, entrada\\02_2026 etc., "
            "cada uma com Volume, Agent Contact e Script Result CSS. O Queue Volume per Day é recomendado para CSS diário."
        )

    return pastas_validas


def adicionar_origem_lote(df: pd.DataFrame, pasta_mes: Path) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    df = df.copy()
    ano, mes = parse_mes_pasta(pasta_mes.name)

    df["Pasta_Origem"] = pasta_mes.name

    if ano != 9999 and mes != 99:
        df["Mes_Referencia"] = f"{ano}-{mes:02d}"
    elif "Ano_Mes" in df.columns and df["Ano_Mes"].notna().any():
        df["Mes_Referencia"] = df["Ano_Mes"].dropna().astype(str).iloc[0]
    else:
        df["Mes_Referencia"] = ""

    return df


def concatenar_tabelas(lista: List[pd.DataFrame]) -> pd.DataFrame:
    lista = [df for df in lista if df is not None and not df.empty]
    if not lista:
        return pd.DataFrame()

    df = pd.concat(lista, ignore_index=True, sort=False)
    df = df.drop_duplicates()
    return df


def ordenar_tabela(df: pd.DataFrame, preferencia: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    cols = [c for c in preferencia if c in df.columns]
    if cols:
        return df.sort_values(cols).reset_index(drop=True)
    return df.reset_index(drop=True)


def salvar_consolidado(df: pd.DataFrame, pasta_saida: Path, nome: str, ordenar_por: Optional[List[str]] = None) -> Path:
    if ordenar_por:
        df = ordenar_tabela(df, ordenar_por)
    return salvar(df, pasta_saida, nome)


def processar_lote_mensal(pasta_mes: Path, pasta_temp: Path) -> Dict[str, pd.DataFrame]:
    """
    Processa uma pasta mensal isoladamente e retorna todas as tabelas tratadas.
    A gravação intermediária vai para uma pasta temporária apenas para reaproveitar as funções do ETL.
    """
    pasta_temp.mkdir(parents=True, exist_ok=True)

    volume, volume_fila = tratar_volume_diario(pasta_mes, pasta_temp)
    agent, agent_fila = tratar_agent_contact_diario(pasta_mes, pasta_temp)
    css_detalhado, css_atendente, css_periodo_atendente, css_periodo_geral = tratar_css(pasta_mes, pasta_temp)
    css_fila_detalhado, css_fila_diario, css_geral_diario = tratar_css_fila_diario(pasta_mes, pasta_temp)
    indicadores_agentes_diario, indicadores_agentes_periodo = criar_indicadores_agentes(
        agent,
        css_atendente,
        pasta_temp,
    )
    indicadores_diario, indicadores_periodo = criar_indicadores_gerais(
        volume,
        agent,
        css_atendente,
        css_periodo_geral,
        css_geral_diario,
        pasta_temp,
    )

    return {
        "f_volume_geral_diario": adicionar_origem_lote(volume, pasta_mes),
        "f_volume_fila_diario": adicionar_origem_lote(volume_fila, pasta_mes),
        "f_gente_contact_diario": adicionar_origem_lote(agent, pasta_mes),
        "f_gente_contact_fila_diario": adicionar_origem_lote(agent_fila, pasta_mes),
        "f_css_detalhado": adicionar_origem_lote(css_detalhado, pasta_mes),
        "f_css_atendente": adicionar_origem_lote(css_atendente, pasta_mes),
        "f_css_periodo_atendente": adicionar_origem_lote(css_periodo_atendente, pasta_mes),
        "f_css_periodo_geral": adicionar_origem_lote(css_periodo_geral, pasta_mes),
        "f_css_fila_detalhado_diario": adicionar_origem_lote(css_fila_detalhado, pasta_mes),
        "f_css_fila_diario": adicionar_origem_lote(css_fila_diario, pasta_mes),
        "f_css_geral_diario": adicionar_origem_lote(css_geral_diario, pasta_mes),
        "f_indicadores_agentes_diario": adicionar_origem_lote(indicadores_agentes_diario, pasta_mes),
        "f_indicadores_agentes_periodo": adicionar_origem_lote(indicadores_agentes_periodo, pasta_mes),
        "f_indicadores_gerais": adicionar_origem_lote(indicadores_diario, pasta_mes),
        "f_indicadores_gerais_periodo": adicionar_origem_lote(indicadores_periodo, pasta_mes),
    }


def criar_dim_atendentes_consolidada(agent_hist: pd.DataFrame, css_hist: pd.DataFrame, pasta_saida: Path) -> pd.DataFrame:
    bases = []

    if agent_hist is not None and not agent_hist.empty:
        bases.append(agent_hist[["Atendente_ID", "Atendente", "Grupo"]].drop_duplicates())

    if css_hist is not None and not css_hist.empty:
        temp = css_hist[["Atendente_ID", "Atendente"]].drop_duplicates()
        temp["Grupo"] = ""
        bases.append(temp)

    if not bases:
        dim = pd.DataFrame(columns=["Atendente_ID", "Atendente", "Grupo"])
    else:
        dim = pd.concat(bases, ignore_index=True).drop_duplicates()
        dim = (
            dim.sort_values(["Atendente", "Grupo"])
            .groupby(["Atendente_ID", "Atendente"], as_index=False)
            .agg(Grupo=("Grupo", lambda x: next((str(v) for v in x if str(v).strip()), "")))
        )

    salvar(dim, pasta_saida, "dim_atendentes.csv")
    return dim



# -------------------------
# CARGA INCREMENTAL / APPEND HISTÓRICO
# -------------------------

CHAVES_INCREMENTAIS = {
    "f_volume_geral_diario": ["Data"],
    "f_volume_fila_diario": ["Data", "Fila"],
    "f_gente_contact_diario": ["Data", "Atendente_ID", "Grupo"],
    "f_gente_contact_fila_diario": ["Data", "Atendente_ID", "Grupo", "Fila"],
    "f_css_detalhado": ["Data", "Periodo_Inicio", "Periodo_Fim", "Atendente_ID", "Script", "Pergunta", "Resposta"],
    "f_css_atendente": ["Data", "Periodo_Inicio", "Periodo_Fim", "Atendente_ID"],
    "f_css_periodo_atendente": ["Periodo_Inicio", "Periodo_Fim", "Atendente_ID"],
    "f_css_periodo_geral": ["Periodo_Inicio", "Periodo_Fim"],
    "f_css_fila_detalhado_diario": ["Data", "Fila", "Script", "Pergunta", "Resposta"],
    "f_css_fila_diario": ["Data", "Fila"],
    "f_css_geral_diario": ["Data"],
    "f_indicadores_agentes_diario": ["Data", "Atendente_ID", "Grupo"],
    "f_indicadores_agentes_periodo": ["Periodo_Inicio", "Periodo_Fim", "Atendente_ID"],
    "f_indicadores_gerais": ["Data"],
    "f_indicadores_gerais_periodo": ["Periodo_Inicio", "Periodo_Fim"],
    "f_fsr_tratado": ["Chave_FSR"],
}

COLUNAS_DATA_CHAVE = {"Data", "Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim"}


def ler_saida_existente(pasta_saida: Path, nome: str) -> pd.DataFrame:
    caminho = pasta_saida / f"{nome}.csv"
    if not caminho.exists():
        return pd.DataFrame()

    # sep=None permite ler tanto a saída antiga com vírgula quanto a nova com ponto e vírgula.
    # Assim a carga incremental não quebra na transição, porque claro que até delimitador vira legado.
    try:
        return pd.read_csv(caminho, dtype=str, encoding="utf-8-sig", sep=None, engine="python")
    except UnicodeDecodeError:
        return pd.read_csv(caminho, dtype=str, encoding="latin1", sep=None, engine="python")


def chave_normalizada(df: pd.DataFrame, colunas_chave: List[str]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=str)

    partes = []
    for col in colunas_chave:
        if col not in df.columns:
            serie = pd.Series([""] * len(df), index=df.index)
        elif col in COLUNAS_DATA_CHAVE:
            serie_dt = pd.to_datetime(df[col], errors="coerce")
            serie = serie_dt.dt.strftime("%Y-%m-%d").fillna("")
        else:
            serie = df[col].fillna("").astype(str).str.strip().str.upper()

        partes.append(serie)

    chave = partes[0]
    for parte in partes[1:]:
        chave = chave + "|" + parte
    return chave


def alinhar_colunas_para_concat(existente: pd.DataFrame, novo: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    colunas = list(existente.columns)
    for col in novo.columns:
        if col not in colunas:
            colunas.append(col)

    for col in existente.columns:
        if col not in colunas:
            colunas.append(col)

    existente_alinhado = existente.copy()
    novo_alinhado = novo.copy()

    for col in colunas:
        if col not in existente_alinhado.columns:
            existente_alinhado[col] = pd.NA
        if col not in novo_alinhado.columns:
            novo_alinhado[col] = pd.NA

    return existente_alinhado[colunas], novo_alinhado[colunas], colunas


def aplicar_carga_incremental(
    nome: str,
    novo: pd.DataFrame,
    pasta_saida: Path,
    substituir_chaves_existentes: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """
    Mantém a base antiga e adiciona apenas chaves novas.

    Padrão:
    - Se a Data/chave já existe na saída, o registro novo é ignorado.
    - Se a Data/chave não existe, o registro é anexado.

    Com substituir_chaves_existentes=True:
    - Remove da base antiga as chaves que vieram no lote novo.
    - Insere o lote novo no lugar.
    """
    novo = novo.copy() if novo is not None else pd.DataFrame()
    existente = ler_saida_existente(pasta_saida, nome)

    # Compatibilidade com FSR gerado pelo script antigo, que não possuía chave técnica.
    if nome == "f_fsr_tratado":
        if not existente.empty and "Chave_FSR" not in existente.columns:
            existente["Chave_FSR"] = gerar_chave_fsr(existente)
        if not novo.empty and "Chave_FSR" not in novo.columns:
            novo["Chave_FSR"] = gerar_chave_fsr(novo)

    if novo.empty and existente.empty:
        return pd.DataFrame(), {
            "Arquivo": f"{nome}.csv",
            "Linhas_Existentes": 0,
            "Linhas_Processadas": 0,
            "Linhas_Novas": 0,
            "Linhas_Ignoradas": 0,
            "Modo": "sem_dados",
        }

    if existente.empty:
        return novo, {
            "Arquivo": f"{nome}.csv",
            "Linhas_Existentes": 0,
            "Linhas_Processadas": len(novo),
            "Linhas_Novas": len(novo),
            "Linhas_Ignoradas": 0,
            "Modo": "primeira_carga",
        }

    if novo.empty:
        return existente, {
            "Arquivo": f"{nome}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": 0,
            "Linhas_Novas": 0,
            "Linhas_Ignoradas": 0,
            "Modo": "sem_novos_registros",
        }

    colunas_chave = CHAVES_INCREMENTAIS.get(nome)
    existente, novo, _ = alinhar_colunas_para_concat(existente, novo)

    if not colunas_chave:
        final = pd.concat([existente, novo], ignore_index=True, sort=False).drop_duplicates()
        linhas_novas = max(len(final) - len(existente), 0)
        return final, {
            "Arquivo": f"{nome}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": len(novo),
            "Linhas_Novas": linhas_novas,
            "Linhas_Ignoradas": max(len(novo) - linhas_novas, 0),
            "Modo": "incremental_drop_duplicates",
        }

    chave_existente = chave_normalizada(existente, colunas_chave)
    chave_novo = chave_normalizada(novo, colunas_chave)

    if substituir_chaves_existentes:
        chaves_novas = set(chave_novo.dropna().astype(str))
        manter_existente = ~chave_existente.isin(chaves_novas)
        existente_filtrado = existente.loc[manter_existente].copy()
        final = pd.concat([existente_filtrado, novo], ignore_index=True, sort=False).drop_duplicates()
        linhas_substituidas = len(existente) - len(existente_filtrado)
        return final, {
            "Arquivo": f"{nome}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": len(novo),
            "Linhas_Novas": len(novo),
            "Linhas_Ignoradas": 0,
            "Linhas_Substituidas": linhas_substituidas,
            "Modo": "substituir_chaves_existentes",
            "Chave": ", ".join(colunas_chave),
        }

    chaves_existentes = set(chave_existente.dropna().astype(str))
    mascara_novos = ~chave_novo.isin(chaves_existentes)
    novos_reais = novo.loc[mascara_novos].copy()

    final = pd.concat([existente, novos_reais], ignore_index=True, sort=False).drop_duplicates()
    return final, {
        "Arquivo": f"{nome}.csv",
        "Linhas_Existentes": len(existente),
        "Linhas_Processadas": len(novo),
        "Linhas_Novas": len(novos_reais),
        "Linhas_Ignoradas": len(novo) - len(novos_reais),
        "Modo": "append_somente_chaves_novas",
        "Chave": ", ".join(colunas_chave),
    }


def aplicar_incremental_em_todas_as_tabelas(
    historico_novo: Dict[str, pd.DataFrame],
    pasta_saida: Path,
    substituir_chaves_existentes: bool = False,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    historico_final = {}
    logs = []

    for nome, df_novo in historico_novo.items():
        final, log = aplicar_carga_incremental(
            nome,
            df_novo,
            pasta_saida,
            substituir_chaves_existentes=substituir_chaves_existentes,
        )
        historico_final[nome] = final
        logs.append(log)

    return historico_final, pd.DataFrame(logs)

def gerar_saida_historica(
    pastas: List[Path],
    pasta_saida: Path,
    incremental: bool = True,
    substituir_chaves_existentes: bool = False,
) -> Dict[str, pd.DataFrame]:
    pasta_saida.mkdir(parents=True, exist_ok=True)
    pasta_temp_raiz = pasta_saida / "_temp_processamento_mensal"
    pasta_temp_raiz.mkdir(parents=True, exist_ok=True)

    acumulados: Dict[str, List[pd.DataFrame]] = {
        "f_volume_geral_diario": [],
        "f_volume_fila_diario": [],
        "f_gente_contact_diario": [],
        "f_gente_contact_fila_diario": [],
        "f_css_detalhado": [],
        "f_css_atendente": [],
        "f_css_periodo_atendente": [],
        "f_css_periodo_geral": [],
        "f_css_fila_detalhado_diario": [],
        "f_css_fila_diario": [],
        "f_css_geral_diario": [],
        "f_indicadores_agentes_diario": [],
        "f_indicadores_agentes_periodo": [],
        "f_indicadores_gerais": [],
        "f_indicadores_gerais_periodo": [],
    }

    erros = []

    for pasta_mes in pastas:
        print(f"\nProcessando lote: {pasta_mes.name}")
        try:
            tabelas = processar_lote_mensal(pasta_mes, pasta_temp_raiz / pasta_mes.name)
            for nome, df in tabelas.items():
                acumulados[nome].append(df)
                print(f"  OK - {nome}.csv ({len(df)} linhas processadas no lote)")
        except Exception as e:
            erros.append((pasta_mes.name, str(e)))
            print(f"  ERRO - {pasta_mes.name}: {e}")

    historico_novo = {nome: concatenar_tabelas(lista) for nome, lista in acumulados.items()}

    if all(df.empty for df in historico_novo.values()):
        msg = "Nenhum lote mensal foi processado com sucesso."
        if erros:
            msg += " Erros: " + " | ".join([f"{p}: {e}" for p, e in erros])
        raise RuntimeError(msg)

    if incremental:
        print("\nModo incremental ativado: mantendo CSVs existentes e adicionando somente novas datas/chaves...")
        historico, log_incremental = aplicar_incremental_em_todas_as_tabelas(
            historico_novo,
            pasta_saida,
            substituir_chaves_existentes=substituir_chaves_existentes,
        )
        salvar(log_incremental, pasta_saida, "log_carga_incremental.csv")
        for _, row in log_incremental.iterrows():
            print(
                f"  {row['Arquivo']}: existentes={row['Linhas_Existentes']} | "
                f"processadas={row['Linhas_Processadas']} | novas={row['Linhas_Novas']} | "
                f"ignoradas={row['Linhas_Ignoradas']}"
            )
    else:
        print("\nModo reprocessamento total ativado: os CSVs finais serão recriados do zero.")
        historico = historico_novo

    ordenacoes = {
        "f_volume_geral_diario": ["Data", "Pasta_Origem"],
        "f_volume_fila_diario": ["Data", "Fila", "Pasta_Origem"],
        "f_gente_contact_diario": ["Data", "Atendente", "Pasta_Origem"],
        "f_gente_contact_fila_diario": ["Data", "Atendente", "Fila", "Pasta_Origem"],
        "f_css_detalhado": ["Periodo_Inicio", "Atendente", "Pergunta", "Resposta", "Pasta_Origem"],
        "f_css_atendente": ["Periodo_Inicio", "Atendente", "Pasta_Origem"],
        "f_css_periodo_atendente": ["Periodo_Inicio", "Atendente", "Pasta_Origem"],
        "f_css_periodo_geral": ["Periodo_Inicio", "Pasta_Origem"],
        "f_css_fila_detalhado_diario": ["Data", "Fila", "Resposta", "Pasta_Origem"],
        "f_css_fila_diario": ["Data", "Fila", "Pasta_Origem"],
        "f_css_geral_diario": ["Data", "Pasta_Origem"],
        "f_indicadores_agentes_diario": ["Data", "Atendente", "Pasta_Origem"],
        "f_indicadores_agentes_periodo": ["Atendente", "Pasta_Origem"],
        "f_indicadores_gerais": ["Data", "Pasta_Origem"],
        "f_indicadores_gerais_periodo": ["Periodo_Inicio", "Pasta_Origem"],
    }

    print("\nGravando arquivos finais...")
    for nome, df in historico.items():
        caminho = salvar_consolidado(df, pasta_saida, f"{nome}.csv", ordenacoes.get(nome))
        print(f"OK - {caminho.name} ({len(df)} linhas finais)")

    dim = criar_dim_atendentes_consolidada(
        historico.get("f_gente_contact_diario", pd.DataFrame()),
        historico.get("f_css_atendente", pd.DataFrame()),
        pasta_saida,
    )
    historico["dim_atendentes"] = dim
    print(f"OK - dim_atendentes.csv ({len(dim)} linhas)")

    if erros:
        log = pd.DataFrame(erros, columns=["Pasta_Origem", "Erro"])
        salvar(log, pasta_saida, "log_erros_processamento.csv")
        print(f"ATENÇÃO - Alguns lotes falharam. Veja log_erros_processamento.csv ({len(log)} erros).")

    try:
        import shutil
        shutil.rmtree(pasta_temp_raiz, ignore_errors=True)
    except Exception:
        pass

    return historico


# -------------------------
# FSR SAP SERVICE
# -------------------------

COLUNAS_TECNICAS_FSR = {
    "Arquivo_Origem", "Data_Atualizacao_Carga", "Chave_FSR", "Ano", "Mes", "Dia", "Ano_Mes",
    "Data_Criacao", "Data_Conclusao", "Fechado"
}


def ler_arquivo_fsr(caminho: Path) -> pd.DataFrame:
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        try:
            df = pd.read_excel(caminho, sheet_name="_DATA_MASTER", header=4, dtype=str)
        except ValueError:
            # fallback para arquivos exportados sem a aba padrão
            df = pd.read_excel(caminho, dtype=str)
    elif caminho.suffix.lower() == ".csv":
        df = pd.read_csv(caminho, dtype=str, sep=None, engine="python")
    else:
        raise ValueError(f"Formato não suportado para FSR: {caminho.suffix}")

    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def localizar_arquivos_fsr(pasta_entrada: Path) -> List[Path]:
    arquivos = []
    for ext in ["*.xlsx", "*.xls", "*.csv"]:
        arquivos.extend(pasta_entrada.glob(ext))

    arquivos = [a for a in arquivos if not a.name.startswith("~$") and a.name.lower() != "f_fsr_tratado.csv"]

    if not arquivos:
        raise FileNotFoundError(f"Nenhum arquivo FSR encontrado em {pasta_entrada}")

    return sorted(arquivos)


def gerar_chave_fsr(df: pd.DataFrame) -> pd.Series:
    candidatos = [
        "ID", "Id", "ID do caso", "ID Caso", "Número", "Numero", "Número do chamado",
        "Nº do chamado", "Chamado", "Protocolo", "Ticket", "Código", "Codigo"
    ]
    existentes = [c for c in candidatos if c in df.columns]

    if existentes:
        base = df[existentes].fillna("").astype(str).agg("|".join, axis=1)
    else:
        cols = [c for c in df.columns if c not in COLUNAS_TECNICAS_FSR]
        base = df[cols].fillna("").astype(str).agg("|".join, axis=1)

    return base.apply(lambda x: hashlib.md5(x.encode("utf-8", errors="ignore")).hexdigest())


def tratar_fsr_datas(df: pd.DataFrame, arquivo_origem: str) -> pd.DataFrame:
    df = df.copy()

    obrigatorias = ["Criado em", "Data de conclusão"]
    faltantes = [c for c in obrigatorias if c not in df.columns]
    if faltantes:
        raise ValueError(f"Arquivo {arquivo_origem} sem colunas obrigatórias do FSR: {', '.join(faltantes)}")

    df["Criado em"] = pd.to_datetime(df["Criado em"], errors="coerce", dayfirst=True)
    df["Data de conclusão"] = pd.to_datetime(df["Data de conclusão"], errors="coerce", dayfirst=True)

    df["Data_Criacao"] = df["Criado em"].dt.date
    df["Data_Conclusao"] = df["Data de conclusão"].dt.date

    df["Fechado"] = df.apply(
        lambda x: "Sim"
        if pd.notna(x["Data_Criacao"])
        and pd.notna(x["Data_Conclusao"])
        and x["Data_Criacao"] == x["Data_Conclusao"]
        else "Não",
        axis=1,
    )

    df["Ano"] = df["Criado em"].dt.year
    df["Mes"] = df["Criado em"].dt.month
    df["Dia"] = df["Criado em"].dt.day
    df["Ano_Mes"] = df["Criado em"].dt.strftime("%Y-%m")
    df["Arquivo_Origem"] = arquivo_origem
    df["Data_Atualizacao_Carga"] = obter_data_atualizacao_carga()
    df["Chave_FSR"] = gerar_chave_fsr(df)

    return df


def processar_fsr_historico(
    entrada_fsr: Path,
    pasta_saida: Path,
    incremental: bool = True,
    substituir_chaves_existentes: bool = False,
) -> pd.DataFrame:
    arquivos = localizar_arquivos_fsr(entrada_fsr)
    bases = []

    for arquivo in arquivos:
        print(f"Processando FSR: {arquivo.name}")
        df = ler_arquivo_fsr(arquivo)
        df = tratar_fsr_datas(df, arquivo.name)
        bases.append(df)

    novo = pd.concat(bases, ignore_index=True, sort=False).drop_duplicates()

    if incremental:
        final, log = aplicar_carga_incremental(
            "f_fsr_tratado",
            novo,
            pasta_saida,
            substituir_chaves_existentes=substituir_chaves_existentes,
        )
        salvar(pd.DataFrame([log]), pasta_saida, "log_carga_incremental_fsr.csv")
    else:
        final = novo

    caminho = salvar_consolidado(final, pasta_saida, "f_fsr_tratado.csv", ["Data_Criacao", "Arquivo_Origem"])
    print(f"OK - {caminho.name} ({len(final)} linhas finais)")
    return final


# -------------------------
# MENU / MAIN
# -------------------------

def selecionar_carga_menu() -> str:
    print("\nEscolha o tipo de carga:")
    print("1 - Tudo: Contact Center + FSR")
    print("2 - Somente Contact Center / SSRS")
    print("3 - Somente FSR / SAP Service")
    print("4 - Sair")

    try:
        opcao = input("Opção: ").strip()
    except EOFError:
        print("Sem entrada interativa detectada. Executando carga completa.")
        return "tudo"

    mapa = {"1": "tudo", "2": "contact", "3": "fsr", "4": "sair"}
    return mapa.get(opcao, "tudo")


def processar_contact_center(
    pasta_entrada: Path,
    pasta_saida: Path,
    incremental: bool,
    substituir_chaves_existentes: bool,
) -> Dict[str, pd.DataFrame]:
    print("\nIniciando ETL Contact Center histórico...")
    print(f"Entrada Contact Center: {pasta_entrada}")
    print(f"Saída final:           {pasta_saida}")

    pastas = localizar_pastas_processamento(pasta_entrada)

    if len(pastas) == 1 and pastas[0] == pasta_entrada:
        print("\nModo detectado: recorte único, relatórios encontrados direto na pasta de entrada.")
    else:
        print("\nModo detectado: histórico por pastas mensais.")
        print("Pastas encontradas:")
        for p in pastas:
            print(f"- {p.name}")

    return gerar_saida_historica(
        pastas,
        pasta_saida,
        incremental=incremental,
        substituir_chaves_existentes=substituir_chaves_existentes,
    )


def main():
    parser = argparse.ArgumentParser(description="ETL unificado Contact Center + FSR para modelo Power BI.")
    parser.add_argument(
        "--base-dir",
        default=r"C:\Users\GustavoCardoso\Downloads\RPA_SSRS",
        help="Pasta base com subpastas de entrada e saida. Ex.: C:\\RPA_SSRS",
    )
    parser.add_argument(
        "--entrada",
        default=None,
        help="Compatibilidade: pasta raiz do Contact Center/SSRS. Ex.: C:\\RPA_SSRS\\entrada",
    )
    parser.add_argument(
        "--entrada-contact",
        default=None,
        help="Pasta raiz onde estão os relatórios do Contact Center ou subpastas mensais.",
    )
    parser.add_argument(
        "--entrada-fsr",
        default=None,
        help="Pasta onde estão os arquivos FSR/SAP Service.",
    )
    parser.add_argument(
        "--saida",
        default=None,
        help="Pasta onde serão salvos os CSVs finais consolidados.",
    )
    parser.add_argument(
        "--carga",
        choices=["menu", "tudo", "contact", "fsr"],
        default="menu",
        help="Tipo de carga. Use 'menu' para escolher na execução.",
    )
    parser.add_argument(
        "--data-atualizacao-carga",
        default=None,
        help="Data técnica da carga no formato AAAA-MM-DD. Se omitida, usa a data atual.",
    )
    parser.add_argument(
        "--reprocessar-tudo",
        action="store_true",
        help="Recria os arquivos finais do zero. Sem esse parâmetro, mantém a saída antiga e adiciona apenas chaves novas.",
    )
    parser.add_argument(
        "--substituir-datas-existentes",
        action="store_true",
        help="Atualiza registros de datas/chaves já existentes com os valores recém-processados.",
    )
    args = parser.parse_args()

    global DATA_ATUALIZACAO_CARGA_MANUAL
    DATA_ATUALIZACAO_CARGA_MANUAL = args.data_atualizacao_carga

    base_dir = Path(args.base_dir)
    pasta_saida = Path(args.saida) if args.saida else base_dir / "saida"
    pasta_saida.mkdir(parents=True, exist_ok=True)

    pasta_entrada_contact = Path(args.entrada_contact or args.entrada) if (args.entrada_contact or args.entrada) else base_dir / "entrada"
    pasta_entrada_fsr = Path(args.entrada_fsr) if args.entrada_fsr else base_dir / "entrada_fsr"

    carga = args.carga
    if carga == "menu":
        carga = selecionar_carga_menu()

    if carga == "sair":
        print("Processamento cancelado pelo usuário.")
        return

    incremental = not args.reprocessar_tudo
    substituir = args.substituir_datas_existentes

    if carga in ["tudo", "contact"]:
        processar_contact_center(
            pasta_entrada_contact,
            pasta_saida,
            incremental=incremental,
            substituir_chaves_existentes=substituir,
        )

    if carga in ["tudo", "fsr"]:
        if not pasta_entrada_fsr.exists():
            msg = f"Pasta FSR não encontrada: {pasta_entrada_fsr}"
            if carga == "fsr":
                raise FileNotFoundError(msg)
            print(f"ATENÇÃO - {msg}. Pulando FSR nesta execução.")
        else:
            print("\nIniciando ETL FSR / SAP Service...")
            print(f"Entrada FSR: {pasta_entrada_fsr}")
            print(f"Saída final: {pasta_saida}")
            processar_fsr_historico(
                pasta_entrada_fsr,
                pasta_saida,
                incremental=incremental,
                substituir_chaves_existentes=substituir,
            )

    print("\nConcluído. Arquivos finais gravados na pasta de saída.")


if __name__ == "__main__":
    main()
