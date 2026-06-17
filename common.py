from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

VERSAO_APP = "2026-06-17_RPA_SSRS_V6_RELATORIOS_OBRIGATORIOS_GOV"

ARQUIVOS = {
    "volume": "Volume 4 - Daily",
    "agent": "Agent - Contact Handling Time 4 - Daily",
    "css": "Script Result 5 - Agent Volume",
    "css_fila_diario": "Script Result 3 - Queue Volume per Day",
}

CHAVES_INCREMENTAIS = {
    "f_agent_contact_diario": ["Data", "Atendente_ID", "Grupo"],
    "f_css_atendente": ["Periodo_Inicio", "Periodo_Fim", "Atendente_ID"],
    "f_fsr_tratado": ["Chave_FSR"],
    "f_reclamacoes_sap_tratado": ["Chave_SAP"],
    "f_gov_chamados_tratado": ["Chave_GOV"],
    "f_indicadores_gerais": ["Data"],
    "f_css_geral_diario": ["Data"],
    "f_volume_fila_diario": ["Data", "Fila"],
    "f_volume_geral_diario": ["Data"],
}

COLUNAS_DATA_CHAVE = {"Data", "Data_Lote", "Periodo_Inicio", "Periodo_Fim", "Data_Criacao", "Data_Conclusao", "Data_Abertura", "Data_Fechamento", "Data_Limite_Conclusao", "Data_Limite_Resolucao", "Data_Reportado_Em", "Data_Revisao_Inicial_Ate", "Data_Revisao_Inicial", "Data_Proxima_Resposta_Ate"}
COLUNAS_DATA_SAIDA = COLUNAS_DATA_CHAVE | {"Data_Atualizacao_Carga"}

COLUNAS_TEXTO_SAIDA = {
    "Data", "Data_Lote", "Data_Atualizacao_Carga", "Periodo_Inicio", "Periodo_Fim", "Ano_Mes",
    "Mes_Referencia", "Pasta_Origem", "Arquivo_Origem", "Atendente_ID", "Atendente", "Grupo",
    "Fila", "Script", "Pergunta", "Resposta", "Classificacao_CSS", "Tem_Data_Diaria",
    "Tem_CSS_Diario", "Observacao_CSS", "Fonte_CSS_Diario", "TME", "TMA", "TMA_Atendentes",
    "Fechado", "Chave_FSR", "Chave_SAP", "Chave_GOV", "Numero_Chamado", "Protocolo",
    "Prioridade", "Status", "Status_Padronizado", "Status_Macro", "Cliente", "Unidade", "Empresa",
    "Responsavel", "Departamento_Responsavel", "Categoria", "Subcategoria", "Categoria_Causa",
    "Categoria_Resolucao", "Categoria_Objeto", "Descricao", "Origem", "Canal", "Canal1", "Canal_Internet",
    "Criado_Por", "SLA_Status", "Analise_Interna", "Prestador_Servicos",
}


def normalizar(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    texto = str(valor).strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def normalizar_coluna(valor) -> str:
    texto = normalizar(valor).lower()
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    return texto.strip("_")


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


def parse_datas_mistas(serie: pd.Series) -> pd.Series:
    """
    Converte datas em série aceitando ISO (YYYY-MM-DD) e formato brasileiro (DD/MM/YYYY).

    Não use dayfirst=True cegamente em ISO, porque 2026-06-12 pode virar 2026-12-06.
    Sim, isso é uma armadilha real e não um teste de sanidade coletiva.
    """
    if serie is None:
        return pd.Series(dtype="datetime64[ns]")
    texto = serie.astype(str).str.strip()
    resultado = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    mascara_iso = texto.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", na=False)
    if mascara_iso.any():
        resultado.loc[mascara_iso] = pd.to_datetime(texto.loc[mascara_iso], errors="coerce", dayfirst=False)
    if (~mascara_iso).any():
        resultado.loc[~mascara_iso] = pd.to_datetime(texto.loc[~mascara_iso], errors="coerce", dayfirst=True)
    return resultado


def coluna_percentual_powerbi(coluna: str) -> bool:
    c = coluna.lower()
    return (
        c.startswith("taxa_")
        or "percentual" in c
        or c.startswith("css_positivo")
        or c.startswith("css_neutro")
        or c.startswith("css_negativo")
    )


def preparar_saida_csv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    saida = df.copy()
    for col in saida.columns:
        # Mantém datas em padrão ISO. Isso evita o Power BI ler cada carga com um humor diferente.
        if col in COLUNAS_DATA_SAIDA:
            datas = parse_datas_mistas(saida[col])
            saida[col] = datas.dt.strftime("%Y-%m-%d").fillna("")
            continue

        if col in COLUNAS_TEXTO_SAIDA or col.upper().endswith("_ID"):
            saida[col] = saida[col].fillna("")
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


def salvar_csv(df: pd.DataFrame, pasta_saida: Path, nome_arquivo: str) -> Path:
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho = pasta_saida / nome_arquivo
    df_saida = preparar_saida_csv(df)
    df_saida.to_csv(caminho, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    return caminho


def ler_saida_existente(pasta_saida: Path, nome_tabela: str) -> pd.DataFrame:
    caminho = pasta_saida / f"{nome_tabela}.csv"
    if not caminho.exists():
        return pd.DataFrame()
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
            serie_dt = parse_datas_mistas(df[col])
            serie = serie_dt.dt.strftime("%Y-%m-%d").fillna("")
        else:
            serie = df[col].fillna("").astype(str).str.strip().str.upper()
        partes.append(serie)
    chave = partes[0]
    for parte in partes[1:]:
        chave = chave + "|" + parte
    return chave


def alinhar_colunas_para_concat(existente: pd.DataFrame, novo: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    colunas = list(existente.columns)
    for col in novo.columns:
        if col not in colunas:
            colunas.append(col)
    existente_alinhado = existente.copy()
    novo_alinhado = novo.copy()
    for col in colunas:
        if col not in existente_alinhado.columns:
            existente_alinhado[col] = pd.NA
        if col not in novo_alinhado.columns:
            novo_alinhado[col] = pd.NA
    return existente_alinhado[colunas], novo_alinhado[colunas]


def aplicar_incremental(
    nome_tabela: str,
    novo: pd.DataFrame,
    pasta_saida: Path,
    substituir_chaves_existentes: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    novo = novo.copy() if novo is not None else pd.DataFrame()
    colunas_chave = CHAVES_INCREMENTAIS.get(nome_tabela)
    linhas_processadas_original = len(novo)

    if colunas_chave and not novo.empty:
        novo = deduplicar_por_chave(novo, colunas_chave)
    linhas_duplicadas_removidas = linhas_processadas_original - len(novo)

    existente = pd.DataFrame() if reprocessar_tudo else ler_saida_existente(pasta_saida, nome_tabela)

    if existente.empty:
        final = novo
        if colunas_chave and not final.empty:
            final = deduplicar_por_chave(final, colunas_chave)
        return final, {
            "Arquivo": f"{nome_tabela}.csv",
            "Linhas_Existentes": 0,
            "Linhas_Processadas": linhas_processadas_original,
            "Linhas_Novas": len(final),
            "Linhas_Ignoradas": 0,
            "Linhas_Substituidas": 0,
            "Duplicidades_Removidas_Na_Carga": linhas_duplicadas_removidas,
            "Modo": "reprocessar_tudo" if reprocessar_tudo else "primeira_carga",
            "Chave": ", ".join(colunas_chave or []),
        }

    if novo.empty:
        final = existente
        if colunas_chave and not final.empty:
            final = deduplicar_por_chave(final, colunas_chave)
        return final, {
            "Arquivo": f"{nome_tabela}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": 0,
            "Linhas_Novas": 0,
            "Linhas_Ignoradas": 0,
            "Linhas_Substituidas": 0,
            "Duplicidades_Removidas_Na_Carga": linhas_duplicadas_removidas,
            "Modo": "sem_novos_registros",
            "Chave": ", ".join(colunas_chave or []),
        }

    existente, novo = alinhar_colunas_para_concat(existente, novo)

    if not colunas_chave:
        final = pd.concat([existente, novo], ignore_index=True, sort=False).drop_duplicates()
        linhas_novas = max(len(final) - len(existente), 0)
        return final, {
            "Arquivo": f"{nome_tabela}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": linhas_processadas_original,
            "Linhas_Novas": linhas_novas,
            "Linhas_Ignoradas": max(len(novo) - linhas_novas, 0),
            "Linhas_Substituidas": 0,
            "Duplicidades_Removidas_Na_Carga": linhas_duplicadas_removidas,
            "Modo": "incremental_drop_duplicates",
        }

    chave_existente = chave_normalizada(existente, colunas_chave)
    chave_novo = chave_normalizada(novo, colunas_chave)

    if substituir_chaves_existentes:
        chaves_novas = set(chave_novo.dropna().astype(str))
        manter_existente = ~chave_existente.isin(chaves_novas)
        existente_filtrado = existente.loc[manter_existente].copy()
        final = pd.concat([existente_filtrado, novo], ignore_index=True, sort=False)
        final = deduplicar_por_chave(final, colunas_chave)
        linhas_substituidas = len(existente) - len(existente_filtrado)
        return final, {
            "Arquivo": f"{nome_tabela}.csv",
            "Linhas_Existentes": len(existente),
            "Linhas_Processadas": linhas_processadas_original,
            "Linhas_Novas": len(novo),
            "Linhas_Ignoradas": 0,
            "Linhas_Substituidas": linhas_substituidas,
            "Duplicidades_Removidas_Na_Carga": linhas_duplicadas_removidas,
            "Modo": "substituir_chaves_existentes",
            "Chave": ", ".join(colunas_chave),
        }

    chaves_existentes = set(chave_existente.dropna().astype(str))
    mascara_novos = ~chave_novo.isin(chaves_existentes)
    novos_reais = novo.loc[mascara_novos].copy()
    final = pd.concat([existente, novos_reais], ignore_index=True, sort=False)
    final = deduplicar_por_chave(final, colunas_chave)
    return final, {
        "Arquivo": f"{nome_tabela}.csv",
        "Linhas_Existentes": len(existente),
        "Linhas_Processadas": linhas_processadas_original,
        "Linhas_Novas": len(novos_reais),
        "Linhas_Ignoradas": len(novo) - len(novos_reais),
        "Linhas_Substituidas": 0,
        "Duplicidades_Removidas_Na_Carga": linhas_duplicadas_removidas,
        "Modo": "append_somente_chaves_novas",
        "Chave": ", ".join(colunas_chave),
    }

def salvar_tabela_incremental(
    nome_tabela: str,
    novo: pd.DataFrame,
    pasta_saida: Path,
    substituir_chaves_existentes: bool = False,
    reprocessar_tudo: bool = False,
    ordenar_por: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    final, log = aplicar_incremental(
        nome_tabela,
        novo,
        pasta_saida,
        substituir_chaves_existentes=substituir_chaves_existentes,
        reprocessar_tudo=reprocessar_tudo,
    )
    if ordenar_por and not final.empty:
        cols = [c for c in ordenar_por if c in final.columns]
        if cols:
            final = final.sort_values(cols).reset_index(drop=True)
    caminho = salvar_csv(final, pasta_saida, f"{nome_tabela}.csv")
    print(f"OK - {caminho.name} ({len(final)} linhas finais)")
    return final, log


def salvar_log(logs: Iterable[Dict[str, object]], pasta_logs: Path, nome: str) -> None:
    pasta_logs.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(list(logs))
    if df.empty:
        return
    caminho = pasta_logs / nome
    df.to_csv(caminho, index=False, sep=";", encoding="utf-8-sig")


def localizar_arquivo(pasta_entrada: Path, nome_base: str) -> Path:
    for ext in [".csv", ".xlsx", ".xls"]:
        caminho = pasta_entrada / f"{nome_base}{ext}"
        if caminho.exists():
            return caminho
    encontrados = sorted(pasta_entrada.glob(f"{nome_base}*"))
    encontrados = [p for p in encontrados if not p.name.startswith("~$")]
    if encontrados:
        return encontrados[0]
    raise FileNotFoundError(f"Arquivo não encontrado: {nome_base} em {pasta_entrada}")


def pasta_tem_relatorios(pasta: Path, requeridos: Iterable[str]) -> bool:
    if not pasta.is_dir():
        return False
    try:
        for chave in requeridos:
            localizar_arquivo(pasta, ARQUIVOS[chave])
        return True
    except FileNotFoundError:
        return False


def parse_data_lote(nome: str) -> Tuple[pd.Timestamp, str]:
    n = nome.strip()
    padroes = [
        (r"^(\d{2})[_\-](\d{2})[_\-](\d{4})$", "dmy"),
        (r"^(\d{4})[_\-](\d{2})[_\-](\d{2})$", "ymd"),
        (r"^(\d{2})[_\-](\d{4})$", "my"),
        (r"^(\d{4})[_\-](\d{2})$", "ym"),
    ]
    for regex, tipo in padroes:
        m = re.match(regex, n)
        if not m:
            continue
        try:
            if tipo == "dmy":
                dia, mes, ano = map(int, m.groups())
                dt = pd.Timestamp(year=ano, month=mes, day=dia)
            elif tipo == "ymd":
                ano, mes, dia = map(int, m.groups())
                dt = pd.Timestamp(year=ano, month=mes, day=dia)
            elif tipo == "my":
                mes, ano = map(int, m.groups())
                dt = pd.Timestamp(year=ano, month=mes, day=1)
            else:
                ano, mes = map(int, m.groups())
                dt = pd.Timestamp(year=ano, month=mes, day=1)
            return dt, dt.strftime("%Y-%m")
        except Exception:
            pass
    return pd.NaT, ""




def granularidade_lote(nome: str) -> str:
    """Retorna 'diario', 'mensal' ou 'desconhecido' conforme o nome do lote."""
    n = nome.strip()
    if re.match(r"^\d{2}[_\-]\d{2}[_\-]\d{4}$", n) or re.match(r"^\d{4}[_\-]\d{2}[_\-]\d{2}$", n):
        return "diario"
    if re.match(r"^\d{2}[_\-]\d{4}$", n) or re.match(r"^\d{4}[_\-]\d{2}$", n):
        return "mensal"
    return "desconhecido"


def janela_lote(pasta_lote: Path) -> Tuple[pd.Timestamp, pd.Timestamp, str]:
    """
    Define a janela aceita para cada lote.
    - Lote diário, ex.: 09_06_2026: aceita do dia 01 do mês até a data do lote.
      Isso corrige exportações MTD e bloqueia datas futuras do próprio arquivo.
    - Lote mensal, ex.: 06_2026: aceita o mês inteiro.
    """
    data_lote, _ = parse_data_lote(pasta_lote.name)
    tipo = granularidade_lote(pasta_lote.name)
    if pd.isna(data_lote):
        return pd.NaT, pd.NaT, tipo
    inicio = pd.Timestamp(year=data_lote.year, month=data_lote.month, day=1)
    if tipo == "diario":
        fim = data_lote.normalize()
    elif tipo == "mensal":
        fim = inicio + pd.offsets.MonthEnd(0)
    else:
        fim = data_lote.normalize()
    return inicio, fim, tipo


def lote_historico_detectado(df: pd.DataFrame, pasta_lote: Path, coluna_data: str = "Data") -> bool:
    """
    Detecta quando uma pasta com nome diário, por exemplo 08_06_2026, contém uma base histórica.

    Esse foi o caso encontrado na entrada enviada: a pasta 08_06_2026 tem dados desde janeiro.
    Nessa situação, filtrar pela janela de junho amputaria o histórico e faria o Power BI jurar
    que o ano começou em junho. Computadores obedientes demais, essa praga.
    """
    if df is None or df.empty or coluna_data not in df.columns:
        return False

    inicio, fim, tipo = janela_lote(pasta_lote)
    if tipo != "diario" or pd.isna(inicio) or pd.isna(fim):
        return False

    datas = parse_datas_mistas(df[coluna_data]).dropna()
    if datas.empty:
        return False

    menor_data = datas.min().normalize()
    maior_data = datas.max().normalize()

    # Regra principal: se há dados antes do primeiro dia do mês do lote, a pasta não é diária pura.
    if menor_data < inicio.normalize():
        return True

    # Regra de segurança: um lote diário com amplitude absurda também é tratado como histórico.
    if (maior_data - menor_data).days > 45:
        return True

    return False


def filtrar_datas_lote(df: pd.DataFrame, pasta_lote: Path, coluna_data: str = "Data") -> pd.DataFrame:
    """
    Remove linhas fora da janela permitida pelo nome do lote, mas preserva bases históricas detectadas.

    - Lote diário normal 15_06_2026: mantém 01/06/2026 a 15/06/2026.
    - Lote diário com histórico dentro, como 08_06_2026 contendo janeiro a junho: mantém o histórico.
    """
    if df is None or df.empty or coluna_data not in df.columns:
        return df

    base = df.copy()
    datas = parse_datas_mistas(base[coluna_data])
    base[coluna_data] = datas

    datas_validas = datas.dropna()
    if datas_validas.empty:
        return base.iloc[0:0].copy()

    if lote_historico_detectado(base, pasta_lote, coluna_data):
        invalidas = int(datas.isna().sum())
        if invalidas:
            print(f"AVISO - {pasta_lote.name}: {invalidas} linha(s) sem data válida removida(s) em {coluna_data}.")
        print(
            f"AVISO - {pasta_lote.name}: histórico detectado em {coluna_data} "
            f"({datas_validas.min().date()} a {datas_validas.max().date()}). "
            "Filtro pelo nome da pasta foi ignorado para preservar meses anteriores."
        )
        return base.loc[datas.notna()].copy()

    inicio, fim, tipo = janela_lote(pasta_lote)
    if pd.isna(inicio) or pd.isna(fim):
        return base.loc[datas.notna()].copy()

    mascara = datas.between(inicio, fim, inclusive="both")
    removidas = int((~mascara).sum())
    if removidas:
        print(
            f"AVISO - {pasta_lote.name}: {removidas} linha(s) removida(s) fora da janela "
            f"{inicio.date()} a {fim.date()} na coluna {coluna_data}."
        )
    return base.loc[mascara].copy()


def deduplicar_por_chave(
    df: pd.DataFrame,
    colunas_chave: List[str],
    ordenar_por: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Deduplica mantendo a versão mais recente.
    A preferência padrão usa Data_Lote/Data_Atualizacao_Carga/Pasta_Origem quando existirem.
    """
    if df is None or df.empty or not colunas_chave:
        return df
    base = df.copy()
    ordem_usuario = ordenar_por or []
    ordem_padrao = ["Data_Lote", "Data_Atualizacao_Carga", "Pasta_Origem", "Arquivo_Origem"]
    colunas_ordem = []
    for col in ordem_usuario + ordem_padrao:
        if col in base.columns and col not in colunas_ordem:
            colunas_ordem.append(col)

    temporarias = []
    for col in colunas_ordem:
        tmp = f"__ordem_{col}"
        temporarias.append(tmp)
        if col in COLUNAS_DATA_CHAVE or "Data" in col:
            base[tmp] = parse_datas_mistas(base[col])
        else:
            base[tmp] = base[col].fillna("").astype(str)

    if temporarias:
        base = base.sort_values(temporarias, na_position="first", kind="mergesort")

    chave = chave_normalizada(base, colunas_chave)
    # Se alguma linha ficar sem chave, não deixa todas virarem uma duplicidade artificial.
    chave = chave.where(chave.astype(str).str.strip() != "", "__linha_sem_chave__" + base.index.astype(str))
    base["__chave_incremental"] = chave
    antes = len(base)
    base = base.drop_duplicates(subset=["__chave_incremental"], keep="last")
    removidas = antes - len(base)
    if removidas:
        print(f"AVISO - {removidas} duplicidade(s) removida(s) pela chave: {', '.join(colunas_chave)}")
    base = base.drop(columns=["__chave_incremental"] + temporarias, errors="ignore")
    return base.reset_index(drop=True)

def localizar_lotes(pasta_entrada: Path, requeridos: Iterable[str]) -> List[Path]:
    pasta_entrada = Path(pasta_entrada)
    if not pasta_entrada.exists():
        nomes = ", ".join(ARQUIVOS[c] for c in requeridos)
        raise FileNotFoundError(f"Pasta de entrada não encontrada: {pasta_entrada}. Relatórios esperados: {nomes}")

    if pasta_tem_relatorios(pasta_entrada, requeridos):
        return [pasta_entrada]

    subpastas = [p for p in pasta_entrada.iterdir() if p.is_dir()]
    validas = [p for p in subpastas if pasta_tem_relatorios(p, requeridos)]

    def chave_ordenacao(pasta: Path):
        data_lote, _ = parse_data_lote(pasta.name)
        return (data_lote if pd.notna(data_lote) else pd.Timestamp.max, pasta.name)

    validas = sorted(validas, key=chave_ordenacao)
    if not validas:
        nomes = ", ".join(ARQUIVOS[c] for c in requeridos)
        raise FileNotFoundError(f"Nenhum lote com os relatórios requeridos ({nomes}) em {pasta_entrada}")
    return validas


def adicionar_origem_lote(df: pd.DataFrame, pasta_lote: Path) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    df = df.copy()
    data_lote, mes_ref = parse_data_lote(pasta_lote.name)
    df["Pasta_Origem"] = pasta_lote.name
    df["Data_Lote"] = data_lote if pd.notna(data_lote) else pd.NaT
    if mes_ref:
        df["Mes_Referencia"] = mes_ref
    elif "Ano_Mes" in df.columns and df["Ano_Mes"].notna().any():
        df["Mes_Referencia"] = df["Ano_Mes"].dropna().astype(str).iloc[0]
    else:
        df["Mes_Referencia"] = ""
    return df


def ler_relatorio_linhas(caminho: Path) -> List[List[str]]:
    if caminho.suffix.lower() == ".csv":
        for enc in ["utf-8-sig", "latin1"]:
            try:
                with open(caminho, encoding=enc, newline="") as f:
                    amostra = f.read(4096)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(amostra, delimiters=",;\t|")
                    except Exception:
                        dialect = csv.excel
                    return list(csv.reader(f, dialect))
            except UnicodeDecodeError:
                continue
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(caminho, header=None, dtype=str)
        return df.fillna("").values.tolist()
    raise ValueError(f"Formato não suportado: {caminho.suffix}")


def extrair_datas_parametros(linhas: List[List[str]]) -> List[pd.Timestamp]:
    texto = "\n".join([" | ".join(map(str, linha)) for linha in linhas[:8]])
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
    if df is None or df.empty or coluna_data not in df.columns:
        return df
    df = df.copy()
    df[coluna_data] = parse_datas_mistas(df[coluna_data])
    df["Ano"] = df[coluna_data].dt.year
    df["Mes"] = df[coluna_data].dt.month
    df["Dia"] = df[coluna_data].dt.day
    df["Ano_Mes"] = df[coluna_data].dt.strftime("%Y-%m")
    return df


def filtrar_ate_ultima_data_com_movimento(df: pd.DataFrame, colunas_movimento: List[str], coluna_data: str = "Data") -> pd.DataFrame:
    if df is None or df.empty or coluna_data not in df.columns:
        return df
    base = df.copy()
    base[coluna_data] = parse_datas_mistas(base[coluna_data])
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


def concatenar(lista: List[pd.DataFrame]) -> pd.DataFrame:
    lista = [df for df in lista if df is not None and not df.empty]
    if not lista:
        return pd.DataFrame()
    return pd.concat(lista, ignore_index=True, sort=False).drop_duplicates()
