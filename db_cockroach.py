from __future__ import annotations

import os
import re
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import pandas as pd
from pandas.errors import EmptyDataError, ParserError
from sqlalchemy import create_engine, text, bindparam
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.types import Text
from dotenv import load_dotenv

load_dotenv()
try:
    import streamlit as st
except Exception:  # pragma: no cover - uso local sem Streamlit
    st = None


ARQUIVOS_PADRAO: Dict[str, str] = {
    "dim_atendentes.csv": "dim_atendentes",
    "f_agent_contact_diario.csv": "f_agent_contact_diario",
    "f_agent_contact_fila_diario.csv": "f_agent_contact_fila_diario",
    "f_gente_contact_diario.csv": "f_gente_contact_diario",
    "f_gente_contact_fila_diario.csv": "f_gente_contact_fila_diario",
    "f_css_atendente.csv": "f_css_atendente",
    "f_css_detalhado.csv": "f_css_detalhado",
    "f_css_periodo_atendente.csv": "f_css_periodo_atendente",
    "f_css_periodo_geral.csv": "f_css_periodo_geral",
    "f_css_fila_detalhado_diario.csv": "f_css_fila_detalhado_diario",
    "f_css_fila_diario.csv": "f_css_fila_diario",
    "f_css_geral_diario.csv": "f_css_geral_diario",
    "f_fsr_tratado.csv": "f_fsr_tratado",
    "f_indicadores_gerais.csv": "f_indicadores_gerais",
    "f_indicadores_gerais_periodo.csv": "f_indicadores_gerais_periodo",
    "f_indicadores_agentes_diario.csv": "f_indicadores_agentes_diario",
    "f_indicadores_agentes_periodo.csv": "f_indicadores_agentes_periodo",
    "f_reclamacoes_sap_tratado.csv": "f_reclamacoes_sap_tratado",
    "f_volume_fila_diario.csv": "f_volume_fila_diario",
    "f_volume_geral_diario.csv": "f_volume_geral_diario",
    "f_gov_chamados_tratado.csv": "f_gov_chamados_tratado",
    "dim_status_chamados.csv": "dim_status_chamados",
    "dim_unidades_chamados.csv": "dim_unidades_chamados",
    "dim_responsaveis_chamados.csv": "dim_responsaveis_chamados",
    "dim_categorias_chamados.csv": "dim_categorias_chamados",
}

# Chaves usadas no envio incremental para o banco.
# Os nomes abaixo já estão no padrão SQL, ou seja, minúsculos e sem acento.
# Sem isso, o banco vira um depósito de duplicidade com iluminação ruim.
CHAVES_UNICAS_BANCO: Dict[str, List[str]] = {
    "dim_atendentes": ["atendente_id"],
    "f_agent_contact_diario": ["data", "atendente_id", "grupo"],
    "f_agent_contact_fila_diario": ["data", "atendente_id", "grupo", "fila"],
    "f_gente_contact_diario": ["data", "atendente_id", "grupo"],
    "f_gente_contact_fila_diario": ["data", "atendente_id", "grupo", "fila"],
    "f_css_atendente": ["data", "periodo_inicio", "periodo_fim", "atendente_id"],
    "f_css_detalhado": ["data", "periodo_inicio", "periodo_fim", "atendente_id", "script", "pergunta", "resposta"],
    "f_css_periodo_atendente": ["periodo_inicio", "periodo_fim", "atendente_id"],
    "f_css_periodo_geral": ["periodo_inicio", "periodo_fim"],
    "f_css_fila_detalhado_diario": ["data", "fila", "script", "pergunta", "resposta"],
    "f_css_fila_diario": ["data", "fila"],
    "f_css_geral_diario": ["data"],
    "f_fsr_tratado": ["chave_fsr"],
    "f_indicadores_gerais": ["data"],
    "f_indicadores_gerais_periodo": ["periodo_inicio", "periodo_fim"],
    "f_indicadores_agentes_diario": ["data", "atendente_id", "grupo"],
    "f_indicadores_agentes_periodo": ["periodo_inicio", "periodo_fim", "atendente_id"],
    "f_reclamacoes_sap_tratado": ["chave_sap"],
    "f_volume_fila_diario": ["data", "fila"],
    "f_volume_geral_diario": ["data"],
    "f_gov_chamados_tratado": ["chave_gov"],
    "dim_status_chamados": ["status_padronizado"],
    "dim_unidades_chamados": ["unidade", "empresa"],
    "dim_responsaveis_chamados": ["responsavel", "departamento_responsavel"],
    "dim_categorias_chamados": ["categoria", "subcategoria", "categoria_causa", "categoria_resolucao", "categoria_objeto"],
}


def _get_secret_value(secao: str, chave: str) -> Optional[str]:
    """Lê secrets do Streamlit sem quebrar execução local."""
    if st is None:
        return None
    try:
        bloco = st.secrets.get(secao, {})
        valor = bloco.get(chave) if hasattr(bloco, "get") else None
        return str(valor).strip() if valor else None
    except Exception:
        return None


def obter_database_url() -> str:
    """Obtém a URL do CockroachDB por Streamlit Secrets ou variável de ambiente.

    Prioridade:
    1. st.secrets["cockroachdb"]["database_url"]
    2. COCKROACH_DATABASE_URL
    3. DATABASE_URL
    """
    url = (
        _get_secret_value("cockroachdb", "database_url")
        or os.getenv("COCKROACH_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )
    if not url:
        raise RuntimeError(
            "Conexão do CockroachDB não configurada. Configure st.secrets['cockroachdb']['database_url'] "
            "no Streamlit Cloud ou a variável de ambiente COCKROACH_DATABASE_URL."
        )
    return normalizar_url_sqlalchemy(url)


def obter_database_name(default: str = "rpa_ssrs") -> str:
    nome = _get_secret_value("cockroachdb", "database_name") or os.getenv("COCKROACH_DATABASE_NAME") or default
    return limpar_identificador(nome, max_len=60)


def normalizar_url_sqlalchemy(url: str) -> str:
    """Converte URL do CockroachDB Cloud para o dialeto correto do SQLAlchemy."""

    url = str(url or "").strip().strip('"').strip("'")

    # Remove prefixos colados por engano
    if url.startswith("DATABASE_URL="):
        url = url.replace("DATABASE_URL=", "", 1).strip()

    if url.startswith("COCKROACH_DATABASE_URL="):
        url = url.replace("COCKROACH_DATABASE_URL=", "", 1).strip()

    # Remove quebras de linha e espaços acidentais
    url = "".join(url.split())

    # A string do painel vem como postgresql://, mas no SQLAlchemy precisa usar cockroachdb
    if url.startswith("postgresql://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql://"):]

    elif url.startswith("postgres://"):
        url = "cockroachdb+psycopg://" + url[len("postgres://"):]

    elif url.startswith("postgresql+psycopg://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql+psycopg://"):]

    elif url.startswith("cockroachdb://"):
        url = "cockroachdb+psycopg://" + url[len("cockroachdb://"):]

    elif url.startswith("cockroachdb+psycopg://"):
        pass

    else:
        raise ValueError(
            "URL inválida. Use uma URL começando com postgresql://, postgres://, "
            "cockroachdb:// ou cockroachdb+psycopg://"
        )

def normalizar_url_sqlalchemy(url: str) -> str:
    """Converte URL do CockroachDB Cloud para o dialeto correto do SQLAlchemy."""

    url = str(url or "").strip().strip('"').strip("'")

    if url.startswith("DATABASE_URL="):
        url = url.replace("DATABASE_URL=", "", 1).strip()

    if url.startswith("COCKROACH_DATABASE_URL="):
        url = url.replace("COCKROACH_DATABASE_URL=", "", 1).strip()

    url = "".join(url.split())

    if url.startswith("postgresql://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql://"):]

    elif url.startswith("postgres://"):
        url = "cockroachdb+psycopg://" + url[len("postgres://"):]

    elif url.startswith("postgresql+psycopg://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql+psycopg://"):]

    elif url.startswith("cockroachdb://"):
        url = "cockroachdb+psycopg://" + url[len("cockroachdb://"):]

    elif url.startswith("cockroachdb+psycopg://"):
        pass

    else:
        raise ValueError(
            "URL inválida. Use uma URL começando com postgresql://, postgres://, "
            "cockroachdb:// ou cockroachdb+psycopg://"
        )

    # Correção do erro do root.crt
    if "sslmode=verify-full" in url and "sslrootcert=" not in url:
        separador = "&" if "?" in url else "?"
        root_cert = os.getenv("COCKROACH_SSLROOTCERT", "system")
        url = f"{url}{separador}sslrootcert={root_cert}"

    return url


def limpar_identificador(nome: str, max_len: int = 63) -> str:
    """Cria nomes seguros para tabelas/colunas no SQL."""
    nome = str(nome or "").strip().lower()
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    nome = re.sub(r"[^a-z0-9_]+", "_", nome)
    nome = re.sub(r"_+", "_", nome).strip("_")
    if not nome:
        nome = "campo"
    if nome[0].isdigit():
        nome = f"c_{nome}"
    return nome[:max_len]


def normalizar_colunas_para_sql(df: pd.DataFrame) -> pd.DataFrame:
    usados: Dict[str, int] = {}
    novas = []
    for col in df.columns:
        base = limpar_identificador(col)
        qtd = usados.get(base, 0)
        usados[base] = qtd + 1
        novas.append(base if qtd == 0 else f"{base}_{qtd + 1}")
    out = df.copy()
    out.columns = novas
    return out


def criar_engine(database_name: Optional[str] = None) -> Engine:
    url_original = obter_database_url()
    url = make_url(url_original)
    if database_name:
        url = url.set(database=database_name)
    return create_engine(url, pool_pre_ping=True, future=True)


def url_para_database(database_name: str) -> str:
    url = make_url(obter_database_url())
    return str(url.set(database=database_name))


def testar_conexao(database_name: Optional[str] = None) -> Dict[str, str]:
    engine = criar_engine(database_name=database_name)
    with engine.connect() as conn:
        linha = conn.execute(
            text("SELECT current_database() AS database_name, current_user AS usuario, version() AS versao")
        ).mappings().one()
    return dict(linha)


def criar_database(database_name: Optional[str] = None) -> str:
    """Cria o database do projeto no cluster, se ainda não existir."""
    nome = limpar_identificador(database_name or obter_database_name())
    engine = criar_engine(database_name=None)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE IF NOT EXISTS "{nome}"'))
    return nome


def criar_tabelas_controle(database_name: Optional[str] = None) -> None:
    db = database_name or obter_database_name()
    engine = criar_engine(database_name=db)
    ddl = """
    CREATE TABLE IF NOT EXISTS log_cargas (
        id_carga STRING NOT NULL,
        data_hora_carga TIMESTAMPTZ NOT NULL DEFAULT now(),
        arquivo_origem STRING NULL,
        tabela_destino STRING NULL,
        linhas_lidas INT8 NULL,
        linhas_gravadas INT8 NULL,
        modo_carga STRING NULL,
        status_processamento STRING NULL,
        mensagem STRING NULL,
        PRIMARY KEY (id_carga, tabela_destino, arquivo_origem)
    );

    CREATE TABLE IF NOT EXISTS controle_cargas (
        id_carga STRING NOT NULL,
        data_hora_carga TIMESTAMPTZ NOT NULL DEFAULT now(),
        tabela_destino STRING NOT NULL,
        arquivo_origem STRING NOT NULL,
        tamanho_bytes INT8 NULL,
        hash_arquivo STRING NULL,
        linhas_gravadas INT8 NULL,
        PRIMARY KEY (id_carga, tabela_destino, arquivo_origem)
    );
    """
    with engine.begin() as conn:
        for comando in [c.strip() for c in ddl.split(";") if c.strip()]:
            conn.execute(text(comando))


def preparar_database(database_name: Optional[str] = None) -> str:
    db = criar_database(database_name)
    criar_tabelas_controle(db)
    return db


def arquivo_tem_conteudo(caminho: Path) -> bool:
    caminho = Path(caminho)
    if not caminho.exists() or caminho.stat().st_size == 0:
        return False
    try:
        return bool(caminho.read_bytes()[:4096].strip())
    except Exception:
        return False


def ler_csv_seguro(caminho: Path) -> pd.DataFrame:
    caminho = Path(caminho)
    if not arquivo_tem_conteudo(caminho):
        return pd.DataFrame()
    candidatos: List[Tuple[int, int, pd.DataFrame]] = []
    ultimo_erro: Optional[Exception] = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        for sep in (";", ",", "\t", "|"):
            try:
                df = pd.read_csv(
                    caminho,
                    sep=sep,
                    encoding=encoding,
                    dtype=str,
                    engine="python",
                    on_bad_lines="skip",
                )
                candidatos.append((len(df.columns), len(df), df))
            except EmptyDataError:
                return pd.DataFrame()
            except (UnicodeDecodeError, ParserError, ValueError, OSError) as exc:
                ultimo_erro = exc
    if candidatos:
        candidatos.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidatos[0][2]
    if ultimo_erro:
        raise ultimo_erro
    return pd.DataFrame()



def preparar_dataframe_para_banco(
    df: pd.DataFrame,
    id_carga: str,
    arquivo_origem: str,
) -> pd.DataFrame:
    """Normaliza colunas e adiciona metadados técnicos antes da gravação."""
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame()
    out = normalizar_colunas_para_sql(df)
    out = out.astype("string").fillna("")
    out["id_carga"] = id_carga
    out["data_hora_carga"] = datetime.now(timezone.utc).isoformat()
    out["arquivo_origem"] = arquivo_origem
    return out


def tabela_existe(engine: Engine, tabela: str) -> bool:
    tabela_sql = limpar_identificador(tabela)
    query = text(
        """
        SELECT count(*) AS qtd
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = :tabela
        """
    )
    with engine.connect() as conn:
        qtd = conn.execute(query, {"tabela": tabela_sql}).scalar_one()
    return int(qtd or 0) > 0


def colunas_da_tabela(engine: Engine, tabela: str) -> List[str]:
    tabela_sql = limpar_identificador(tabela)
    query = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :tabela
        ORDER BY ordinal_position
        """
    )
    with engine.connect() as conn:
        return [str(row[0]) for row in conn.execute(query, {"tabela": tabela_sql}).all()]


def garantir_colunas_tabela(engine: Engine, tabela: str, df: pd.DataFrame) -> None:
    """Adiciona colunas novas no banco antes do append.

    Isso evita erro quando o ETL passa a gerar uma coluna nova. Porque claro,
    até coluna resolve nascer depois da reunião de homologação.
    """
    if df.empty:
        return
    tabela_sql = limpar_identificador(tabela)
    if not tabela_existe(engine, tabela_sql):
        return
    existentes = set(colunas_da_tabela(engine, tabela_sql))
    faltantes = [col for col in df.columns if col not in existentes]
    if not faltantes:
        return
    with engine.begin() as conn:
        for col in faltantes:
            col_sql = limpar_identificador(col)
            conn.execute(text(f'ALTER TABLE "{tabela_sql}" ADD COLUMN IF NOT EXISTS "{col_sql}" STRING'))


def chave_linha_banco(df: pd.DataFrame, colunas_chave: List[str]) -> pd.Series:
    """Gera chave normalizada para comparação entre CSV tratado e banco."""
    if df is None or df.empty:
        return pd.Series(dtype=str)
    partes = []
    for col in colunas_chave:
        if col not in df.columns:
            serie = pd.Series([""] * len(df), index=df.index, dtype="string")
        else:
            serie = df[col].fillna("").astype(str).str.strip().str.upper()
        partes.append(serie)
    chave = partes[0]
    for parte in partes[1:]:
        chave = chave + "|" + parte
    return chave


def ler_chaves_existentes(engine: Engine, tabela: str, colunas_chave: List[str]) -> set[str]:
    tabela_sql = limpar_identificador(tabela)
    existentes = set(colunas_da_tabela(engine, tabela_sql))
    chaves_validas = [c for c in colunas_chave if c in existentes]
    if len(chaves_validas) != len(colunas_chave):
        return set()

    expr = " || '|' || ".join([f"upper(trim(coalesce(\"{c}\", '')))" for c in chaves_validas])
    query = text(f'SELECT {expr} AS chave FROM "{tabela_sql}"')
    with engine.connect() as conn:
        return {str(row[0]) for row in conn.execute(query).all() if row[0] is not None}




def preparar_upsert_por_chave_banco(
    df: pd.DataFrame,
    tabela: str,
    engine: Engine,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Prepara carga segura para banco existente: remove no banco as chaves que virão no CSV e depois faz append.

    Isso preserva o histórico que já está no banco e atualiza somente as chaves presentes na nova saída do ETL.
    É o modo mais indicado quando o Power BI já consome uma base existente e o SSRS substitui arquivos diariamente.
    """
    tabela_sql = limpar_identificador(tabela)
    colunas_chave = CHAVES_UNICAS_BANCO.get(tabela_sql)

    if df.empty or not colunas_chave:
        return df, {
            "Chave_Banco": ", ".join(colunas_chave or []),
            "Linhas_Substituidas_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": 0,
            "Modo_Deduplicacao": "upsert_sem_chave_configurada",
        }

    colunas_disponiveis = set(df.columns)
    if any(c not in colunas_disponiveis for c in colunas_chave):
        return df, {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Substituidas_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": 0,
            "Modo_Deduplicacao": "upsert_chave_nao_encontrada_no_csv",
        }

    antes = len(df)
    out = df.copy()
    out["__chave_banco__"] = chave_linha_banco(out, colunas_chave)
    out = out.drop_duplicates("__chave_banco__", keep="last")
    chaves = sorted(set(out["__chave_banco__"].dropna().astype(str)))
    out = out.drop(columns=["__chave_banco__"])
    duplicadas_carga = antes - len(out)

    if not tabela_existe(engine, tabela_sql):
        return out, {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Substituidas_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
            "Modo_Deduplicacao": "upsert_primeira_carga_banco",
        }

    existentes = set(colunas_da_tabela(engine, tabela_sql))
    if any(c not in existentes for c in colunas_chave):
        return out, {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Substituidas_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
            "Modo_Deduplicacao": "upsert_chave_nao_encontrada_na_tabela_existente",
        }

    garantir_colunas_tabela(engine, tabela_sql, out)
    expr = " || '|' || ".join([f"upper(trim(coalesce(\"{c}\", '')))" for c in colunas_chave])
    total_removido = 0
    tamanho_chunk = 500
    with engine.begin() as conn:
        for i in range(0, len(chaves), tamanho_chunk):
            parte = chaves[i:i + tamanho_chunk]
            if not parte:
                continue
            stmt = text(f'DELETE FROM "{tabela_sql}" WHERE ({expr}) IN :chaves').bindparams(
                bindparam("chaves", expanding=True)
            )
            res = conn.execute(stmt, {"chaves": parte})
            if res.rowcount and res.rowcount > 0:
                total_removido += int(res.rowcount)

    return out, {
        "Chave_Banco": ", ".join(colunas_chave),
        "Linhas_Substituidas_Banco": total_removido,
        "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
        "Modo_Deduplicacao": "upsert_delete_append_por_chave",
    }
def filtrar_linhas_novas_banco(
    df: pd.DataFrame,
    tabela: str,
    engine: Engine,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Mantém apenas linhas cuja chave ainda não existe na tabela de destino."""
    tabela_sql = limpar_identificador(tabela)
    colunas_chave = CHAVES_UNICAS_BANCO.get(tabela_sql)

    if df.empty or not colunas_chave:
        return df, {
            "Chave_Banco": ", ".join(colunas_chave or []),
            "Linhas_Ja_Existentes_Banco": 0,
            "Modo_Deduplicacao": "sem_chave_configurada",
        }

    colunas_disponiveis = set(df.columns)
    chaves_validas = [c for c in colunas_chave if c in colunas_disponiveis]
    if len(chaves_validas) != len(colunas_chave):
        return df, {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Ja_Existentes_Banco": 0,
            "Modo_Deduplicacao": "chave_nao_encontrada_no_csv",
        }

    antes = len(df)
    df = df.copy()
    df["__chave_banco__"] = chave_linha_banco(df, colunas_chave)
    df = df.drop_duplicates("__chave_banco__", keep="last")
    duplicadas_carga = antes - len(df)

    if not tabela_existe(engine, tabela_sql):
        return df.drop(columns=["__chave_banco__"]), {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Ja_Existentes_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
            "Modo_Deduplicacao": "primeira_carga_banco",
        }

    chaves_existentes = ler_chaves_existentes(engine, tabela_sql, colunas_chave)
    if not chaves_existentes:
        return df.drop(columns=["__chave_banco__"]), {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Ja_Existentes_Banco": 0,
            "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
            "Modo_Deduplicacao": "sem_chaves_existentes_lidas",
        }

    mascara_novos = ~df["__chave_banco__"].isin(chaves_existentes)
    novos = df.loc[mascara_novos].drop(columns=["__chave_banco__"])
    return novos, {
        "Chave_Banco": ", ".join(colunas_chave),
        "Linhas_Ja_Existentes_Banco": int((~mascara_novos).sum()),
        "Duplicidades_Removidas_Na_Carga": duplicadas_carga,
        "Modo_Deduplicacao": "incremental_somente_novos",
    }


def registrar_log(
    engine: Engine,
    id_carga: str,
    arquivo_origem: str,
    tabela_destino: str,
    linhas_lidas: int,
    linhas_gravadas: int,
    status: str,
    mensagem: str = "",
    modo_carga: str = "append",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPSERT INTO log_cargas
                (id_carga, data_hora_carga, arquivo_origem, tabela_destino, linhas_lidas, linhas_gravadas,
                 modo_carga, status_processamento, mensagem)
                VALUES
                (:id_carga, now(), :arquivo_origem, :tabela_destino, :linhas_lidas, :linhas_gravadas,
                 :modo_carga, :status_processamento, :mensagem)
                """
            ),
            {
                "id_carga": id_carga,
                "arquivo_origem": arquivo_origem,
                "tabela_destino": tabela_destino,
                "linhas_lidas": int(linhas_lidas or 0),
                "linhas_gravadas": int(linhas_gravadas or 0),
                "modo_carga": modo_carga,
                "status_processamento": status,
                "mensagem": str(mensagem or "")[:4000],
            },
        )


def enviar_dataframe(
    df: pd.DataFrame,
    tabela: str,
    engine: Engine,
    id_carga: str,
    arquivo_origem: str,
    if_exists: str = "append",
) -> int:
    if df.empty and len(df.columns) == 0:
        return 0
    tabela_sql = limpar_identificador(tabela)
    out = preparar_dataframe_para_banco(df, id_carga, arquivo_origem)

    if if_exists == "incremental":
        out, _ = filtrar_linhas_novas_banco(out, tabela_sql, engine)
        if_exists_sql = "append"
    elif if_exists == "upsert":
        out, _ = preparar_upsert_por_chave_banco(out, tabela_sql, engine)
        if_exists_sql = "append"
    else:
        if_exists_sql = if_exists

    if out.empty:
        return 0

    if if_exists_sql == "append":
        garantir_colunas_tabela(engine, tabela_sql, out)

    dtype = {col: Text() for col in out.columns}
    out.to_sql(
        tabela_sql,
        engine,
        if_exists=if_exists_sql,
        index=False,
        chunksize=1000,
        method="multi",
        dtype=dtype,
    )
    return len(out)


def enviar_csv_para_banco(
    caminho: Path,
    tabela: Optional[str] = None,
    database_name: Optional[str] = None,
    id_carga: Optional[str] = None,
    if_exists: str = "append",
) -> Dict[str, object]:
    caminho = Path(caminho)
    tabela_destino = tabela or ARQUIVOS_PADRAO.get(caminho.name, caminho.stem)
    tabela_sql = limpar_identificador(tabela_destino)
    id_carga = id_carga or f"carga_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    db = database_name or obter_database_name()
    criar_tabelas_controle(db)
    engine = criar_engine(database_name=db)

    try:
        df = ler_csv_seguro(caminho)
        if df.empty and len(df.columns) == 0:
            registrar_log(engine, id_carga, caminho.name, tabela_destino, 0, 0, "IGNORADO", "Arquivo vazio ou ilegível", if_exists)
            return {
                "Status": "IGNORADO",
                "Arquivo": caminho.name,
                "Tabela": tabela_destino,
                "Linhas_Lidas": 0,
                "Linhas_Gravadas": 0,
                "Linhas_Ignoradas": 0,
                "Mensagem": "Arquivo vazio ou ilegível",
            }

        out = preparar_dataframe_para_banco(df, id_carga, caminho.name)
        meta_incremental: Dict[str, object] = {}
        if if_exists == "incremental":
            out, meta_incremental = filtrar_linhas_novas_banco(out, tabela_sql, engine)
            modo_sql = "append"
        elif if_exists == "upsert":
            out, meta_incremental = preparar_upsert_por_chave_banco(out, tabela_sql, engine)
            modo_sql = "append"
        else:
            modo_sql = if_exists

        if out.empty:
            mensagem = "Nenhuma linha nova para gravar"
            registrar_log(engine, id_carga, caminho.name, tabela_destino, len(df), 0, "IGNORADO", mensagem, if_exists)
            return {
                "Status": "IGNORADO",
                "Arquivo": caminho.name,
                "Tabela": tabela_destino,
                "Linhas_Lidas": len(df),
                "Linhas_Gravadas": 0,
                "Linhas_Ignoradas": len(df),
                "Mensagem": mensagem,
                **meta_incremental,
            }

        if modo_sql == "append":
            garantir_colunas_tabela(engine, tabela_sql, out)

        dtype = {col: Text() for col in out.columns}
        out.to_sql(
            tabela_sql,
            engine,
            if_exists=modo_sql,
            index=False,
            chunksize=1000,
            method="multi",
            dtype=dtype,
        )
        gravadas = len(out)
        ignoradas = max(len(df) - gravadas, 0) if if_exists == "incremental" else 0
        registrar_log(engine, id_carga, caminho.name, tabela_destino, len(df), gravadas, "OK", "Carga gravada", if_exists)
        return {
            "Status": "OK",
            "Arquivo": caminho.name,
            "Tabela": tabela_destino,
            "Linhas_Lidas": len(df),
            "Linhas_Gravadas": gravadas,
            "Linhas_Ignoradas": ignoradas,
            "Mensagem": "Carga gravada",
            **meta_incremental,
        }
    except Exception as exc:
        try:
            registrar_log(engine, id_carga, caminho.name, tabela_destino, 0, 0, "ERRO", str(exc), if_exists)
        except Exception:
            pass
        return {
            "Status": "ERRO",
            "Arquivo": caminho.name,
            "Tabela": tabela_destino,
            "Linhas_Lidas": 0,
            "Linhas_Gravadas": 0,
            "Linhas_Ignoradas": 0,
            "Mensagem": str(exc),
        }


def enviar_pasta_saida_para_banco(
    pasta_saida: Path,
    database_name: Optional[str] = None,
    apenas_padrao: bool = True,
    if_exists: str = "append",
) -> List[Dict[str, object]]:
    db = preparar_database(database_name or obter_database_name())
    pasta_saida = Path(pasta_saida)
    id_carga = f"carga_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    resultados: List[Dict[str, object]] = []

    arquivos = sorted(pasta_saida.glob("*.csv")) if pasta_saida.exists() else []
    if apenas_padrao:
        arquivos = [p for p in arquivos if p.name in ARQUIVOS_PADRAO]

    for arquivo in arquivos:
        resultado = enviar_csv_para_banco(
            arquivo,
            tabela=ARQUIVOS_PADRAO.get(arquivo.name, arquivo.stem),
            database_name=db,
            id_carga=id_carga,
            if_exists=if_exists,
        )
        resultado["ID_Carga"] = id_carga
        resultados.append(resultado)
    return resultados



def consultar_resumo_tabelas(database_name: Optional[str] = None, apenas_padrao: bool = True) -> pd.DataFrame:
    """Retorna quantidade de linhas por tabela oficial do projeto no banco."""
    db = database_name or obter_database_name()
    engine = criar_engine(database_name=db)
    tabelas = sorted(set(ARQUIVOS_PADRAO.values())) if apenas_padrao else None

    if tabelas is None:
        df_tabelas = listar_tabelas(db)
        tabelas = df_tabelas["table_name"].astype(str).tolist() if not df_tabelas.empty else []

    linhas = []
    for tabela in tabelas:
        tabela_sql = limpar_identificador(tabela)
        if not tabela_existe(engine, tabela_sql):
            linhas.append({"Tabela": tabela_sql, "Linhas_Banco": 0, "Status": "Não criada"})
            continue
        with engine.connect() as conn:
            qtd = conn.execute(text(f'SELECT count(*) FROM "{tabela_sql}"')).scalar_one()
        linhas.append({"Tabela": tabela_sql, "Linhas_Banco": int(qtd or 0), "Status": "OK"})
    return pd.DataFrame(linhas)


def consultar_log_cargas(database_name: Optional[str] = None, limite: int = 100) -> pd.DataFrame:
    db = database_name or obter_database_name()
    engine = criar_engine(database_name=db)
    query = text(
        """
        SELECT id_carga, data_hora_carga, arquivo_origem, tabela_destino, linhas_lidas,
               linhas_gravadas, modo_carga, status_processamento, mensagem
        FROM log_cargas
        ORDER BY data_hora_carga DESC
        LIMIT :limite
        """
    )
    return pd.read_sql(query, engine, params={"limite": int(limite)})


def listar_tabelas(database_name: Optional[str] = None) -> pd.DataFrame:
    db = database_name or obter_database_name()
    engine = criar_engine(database_name=db)
    query = text(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
        """
    )
    return pd.read_sql(query, engine)
