from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from pandas.errors import EmptyDataError, ParserError
from sqlalchemy import create_engine, text, bindparam
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.types import Text
from dotenv import load_dotenv

load_dotenv()
try:
    import streamlit as st
except Exception:  # pragma: no cover
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

# Chaves oficiais para cargas seguras. Se faltar chave, a carga falha.
# Banco que aceita chave ausente é basicamente uma lixeira com endpoint.
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
    if st is None:
        return None
    try:
        bloco = st.secrets.get(secao, {})
        valor = bloco.get(chave) if hasattr(bloco, "get") else None
        return str(valor).strip() if valor else None
    except Exception:
        return None


def limpar_identificador(nome: str, max_len: int = 63) -> str:
    nome = str(nome or "").strip().lower()
    nome = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    nome = re.sub(r"[^a-z0-9_]+", "_", nome)
    nome = re.sub(r"_+", "_", nome).strip("_")
    if not nome:
        nome = "campo"
    if nome[0].isdigit():
        nome = f"c_{nome}"
    return nome[:max_len]


def quote_ident(nome: str) -> str:
    return '"' + limpar_identificador(nome).replace('"', '""') + '"'


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


def obter_database_url() -> str:
    url = (
        _get_secret_value("cockroachdb", "database_url")
        or os.getenv("COCKROACH_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )
    if not url:
        raise RuntimeError(
            "Conexão do CockroachDB não configurada. Configure st.secrets['cockroachdb']['database_url'] "
            "ou COCKROACH_DATABASE_URL."
        )
    return normalizar_url_sqlalchemy(url)


def obter_database_name(default: str = "rpa_ssrs") -> str:
    nome = _get_secret_value("cockroachdb", "database_name") or os.getenv("COCKROACH_DATABASE_NAME") or default
    return limpar_identificador(nome, max_len=60)


def normalizar_url_sqlalchemy(url: str) -> str:
    url = str(url or "").strip().strip('"').strip("'")
    for prefixo in ["DATABASE_URL=", "COCKROACH_DATABASE_URL="]:
        if url.startswith(prefixo):
            url = url.replace(prefixo, "", 1).strip()
    url = "".join(url.split())

    if url.startswith("postgresql://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "cockroachdb+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql+psycopg://"):
        url = "cockroachdb+psycopg://" + url[len("postgresql+psycopg://"):]
    elif url.startswith("cockroachdb://"):
        url = "cockroachdb+psycopg://" + url[len("cockroachdb://"):]
    elif not url.startswith("cockroachdb+psycopg://"):
        raise ValueError("URL inválida para CockroachDB/PostgreSQL.")

    url_obj = make_url(url)
    query = dict(url_obj.query)
    sslmode = str(query.get("sslmode") or "").strip().lower()
    if not sslmode:
        sslmode = "verify-full"
        query["sslmode"] = sslmode

    if sslmode == "verify-full":
        base_dir = Path(__file__).resolve().parent
        cert_padrao = base_dir / "certs" / "root.crt"
        root_cert_configurado = (
            _get_secret_value("cockroachdb", "sslrootcert")
            or os.getenv("COCKROACH_SSLROOTCERT")
            or query.get("sslrootcert")
        )
        root_cert_final: Optional[Path] = None
        if root_cert_configurado and str(root_cert_configurado).lower() != "system":
            candidato = Path(str(root_cert_configurado))
            if not candidato.is_absolute():
                candidato = base_dir / candidato
            root_cert_final = candidato
        elif cert_padrao.exists():
            root_cert_final = cert_padrao
        if root_cert_final is None or not root_cert_final.exists():
            raise RuntimeError(
                f"Certificado SSL do CockroachDB não encontrado. Esperado em {cert_padrao} "
                "ou configure COCKROACH_SSLROOTCERT."
            )
        query["sslrootcert"] = str(root_cert_final)

    return url_obj.set(query=query).render_as_string(hide_password=False)


def criar_engine(database_name: Optional[str] = None) -> Engine:
    url = make_url(obter_database_url())
    if database_name:
        url = url.set(database=database_name)
    return create_engine(url, pool_pre_ping=True, future=True)


def criar_engine_defaultdb() -> Engine:
    url = make_url(obter_database_url()).set(database="defaultdb")
    return create_engine(url, pool_pre_ping=True, future=True)


def testar_conexao(database_name: Optional[str] = None) -> Dict[str, str]:
    engine = criar_engine(database_name=database_name)
    with engine.connect() as conn:
        linha = conn.execute(
            text("SELECT current_database() AS database_name, current_user AS usuario, version() AS versao")
        ).mappings().one()
    return dict(linha)


def criar_database(database_name: Optional[str] = None) -> str:
    nome = limpar_identificador(database_name or obter_database_name())
    engine = criar_engine_defaultdb()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'CREATE DATABASE IF NOT EXISTS {quote_ident(nome)}'))
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

    CREATE TABLE IF NOT EXISTS controle_execucao_etl (
        id_execucao STRING PRIMARY KEY,
        data_hora_inicio TIMESTAMPTZ NOT NULL DEFAULT now(),
        data_hora_fim TIMESTAMPTZ NULL,
        status STRING NOT NULL,
        origem STRING NULL,
        modo_carga STRING NULL,
        mensagem STRING NULL
    );

    CREATE TABLE IF NOT EXISTS auditoria_cargas (
        id_carga STRING NOT NULL,
        tabela_destino STRING NOT NULL,
        regra STRING NOT NULL,
        severidade STRING NOT NULL,
        mensagem STRING NULL,
        valor_referencia STRING NULL,
        data_hora_auditoria TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (id_carga, tabela_destino, regra)
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
    return bool(caminho.read_bytes()[:4096].strip())


def calcular_sha256(caminho: Path, bloco: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(caminho).open("rb") as f:
        for pedaco in iter(lambda: f.read(bloco), b""):
            h.update(pedaco)
    return h.hexdigest()


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
                    on_bad_lines="error",
                )
                if len(df.columns) >= 1:
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
    hash_arquivo: str = "",
) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return pd.DataFrame()
    out = normalizar_colunas_para_sql(df)
    out = out.astype("string").fillna("")
    out["id_carga"] = id_carga
    out["data_hora_carga"] = datetime.now(timezone.utc).isoformat()
    out["arquivo_origem"] = arquivo_origem
    out["hash_arquivo"] = hash_arquivo
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
        return [str(row[0]) for row in conn.execute(query, {"tabela": limpar_identificador(tabela)}).all()]


def primary_key_da_tabela(engine: Engine, tabela: str) -> List[str]:
    query = text(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = 'public'
          AND tc.table_name = :tabela
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """
    )
    with engine.connect() as conn:
        return [str(row[0]) for row in conn.execute(query, {"tabela": limpar_identificador(tabela)}).all()]


def garantir_colunas_tabela(engine: Engine, tabela: str, df: pd.DataFrame) -> None:
    if df.empty or not tabela_existe(engine, tabela):
        return
    existentes = set(colunas_da_tabela(engine, tabela))
    faltantes = [limpar_identificador(c) for c in df.columns if limpar_identificador(c) not in existentes]
    if not faltantes:
        return
    with engine.begin() as conn:
        for col in faltantes:
            conn.execute(text(f'ALTER TABLE {quote_ident(tabela)} ADD COLUMN IF NOT EXISTS {quote_ident(col)} STRING'))


def chave_linha_banco(df: pd.DataFrame, colunas_chave: List[str]) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=str)
    partes = []
    for col in colunas_chave:
        if col not in df.columns:
            raise ValueError(f"Coluna de chave ausente: {col}")
        serie = df[col].fillna("").astype(str).str.strip().str.upper()
        partes.append(serie)
    chave = partes[0]
    for parte in partes[1:]:
        chave = chave + "|" + parte
    return chave


def validar_chaves_para_carga(df: pd.DataFrame, tabela: str) -> List[str]:
    tabela_sql = limpar_identificador(tabela)
    chaves = CHAVES_UNICAS_BANCO.get(tabela_sql)
    if not chaves:
        raise ValueError(f"Tabela {tabela_sql} não tem chave configurada para upsert/incremental.")
    faltantes = [c for c in chaves if c not in df.columns]
    if faltantes:
        raise ValueError(f"Tabela {tabela_sql}: chave(s) ausente(s) no CSV tratado: {faltantes}")
    chave = chave_linha_banco(df, chaves)
    vazias = chave.astype(str).str.replace("|", "", regex=False).str.strip().eq("")
    if vazias.any():
        raise ValueError(f"Tabela {tabela_sql}: {int(vazias.sum())} linha(s) com chave vazia.")
    duplicadas = chave.duplicated(keep=False)
    if duplicadas.any():
        exemplos = chave.loc[duplicadas].head(10).tolist()
        raise ValueError(f"Tabela {tabela_sql}: duplicidade na carga para chave {chaves}. Exemplos: {exemplos}")
    return chaves


def garantir_tabela_final_com_pk(engine: Engine, tabela: str, df: pd.DataFrame, colunas_chave: List[str]) -> None:
    tabela_sql = limpar_identificador(tabela)
    colunas = [limpar_identificador(c) for c in df.columns]
    chaves = [limpar_identificador(c) for c in colunas_chave]
    if not tabela_existe(engine, tabela_sql):
        defs = []
        for col in colunas:
            nulo = "NOT NULL" if col in chaves else "NULL"
            defs.append(f'{quote_ident(col)} STRING {nulo}')
        pk = ", ".join(quote_ident(c) for c in chaves)
        ddl = f'CREATE TABLE IF NOT EXISTS {quote_ident(tabela_sql)} (\n  ' + ",\n  ".join(defs) + f",\n  PRIMARY KEY ({pk})\n)"
        with engine.begin() as conn:
            conn.execute(text(ddl))
        return

    pk_atual = primary_key_da_tabela(engine, tabela_sql)
    if [c.lower() for c in pk_atual] != chaves:
        raise RuntimeError(
            f"Tabela {tabela_sql} existe com primary key {pk_atual or 'nenhuma'}, mas a carga exige {chaves}. "
            "Pare a carga e rode a migração SQL de chaves primárias antes de continuar. "
            "Sim, é chato; mais chato é duplicidade no fechamento mensal."
        )
    garantir_colunas_tabela(engine, tabela_sql, df)


def ler_chaves_existentes(engine: Engine, tabela: str, colunas_chave: List[str]) -> set[str]:
    if not tabela_existe(engine, tabela):
        return set()
    existentes = set(colunas_da_tabela(engine, tabela))
    if any(c not in existentes for c in colunas_chave):
        raise ValueError(f"Tabela {tabela}: chave não existe no banco: {colunas_chave}")
    expr = " || '|' || ".join([f"upper(trim(coalesce({quote_ident(c)}, '')))" for c in colunas_chave])
    query = text(f'SELECT {expr} AS chave FROM {quote_ident(tabela)}')
    with engine.connect() as conn:
        return {str(row[0]) for row in conn.execute(query).all() if row[0] is not None}


def executar_com_retry(func, tentativas: int = 3):
    for tentativa in range(1, tentativas + 1):
        try:
            return func()
        except Exception as exc:
            msg = str(exc).lower()
            retry = "40001" in msg or "restart transaction" in msg or "serialization" in msg
            if not retry or tentativa >= tentativas:
                raise
            time.sleep(min(2 ** tentativa, 10))


def gravar_upsert_com_staging(df: pd.DataFrame, tabela: str, engine: Engine) -> int:
    tabela_sql = limpar_identificador(tabela)
    chaves = validar_chaves_para_carga(df, tabela_sql)
    garantir_tabela_final_com_pk(engine, tabela_sql, df, chaves)
    colunas = [limpar_identificador(c) for c in df.columns]
    staging = limpar_identificador(f"stg_{tabela_sql}_{uuid.uuid4().hex[:10]}")
    dtype = {col: Text() for col in df.columns}

    def _rodar():
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS {quote_ident(staging)}'))
            df.to_sql(staging, conn, if_exists="replace", index=False, chunksize=1000, method="multi", dtype=dtype)
            lista_colunas = ", ".join(quote_ident(c) for c in colunas)
            select_colunas = ", ".join(quote_ident(c) for c in colunas)
            conn.execute(text(f'UPSERT INTO {quote_ident(tabela_sql)} ({lista_colunas}) SELECT {select_colunas} FROM {quote_ident(staging)}'))
            conn.execute(text(f'DROP TABLE IF EXISTS {quote_ident(staging)}'))
        return len(df)

    return int(executar_com_retry(_rodar))


def filtrar_linhas_novas_banco(df: pd.DataFrame, tabela: str, engine: Engine) -> Tuple[pd.DataFrame, Dict[str, object]]:
    tabela_sql = limpar_identificador(tabela)
    colunas_chave = validar_chaves_para_carga(df, tabela_sql)
    if not tabela_existe(engine, tabela_sql):
        return df, {
            "Chave_Banco": ", ".join(colunas_chave),
            "Linhas_Ja_Existentes_Banco": 0,
            "Modo_Deduplicacao": "primeira_carga_banco",
        }
    chaves_existentes = ler_chaves_existentes(engine, tabela_sql, colunas_chave)
    chave_novo = chave_linha_banco(df, colunas_chave)
    mascara_novos = ~chave_novo.isin(chaves_existentes)
    novos = df.loc[mascara_novos].copy()
    return novos, {
        "Chave_Banco": ", ".join(colunas_chave),
        "Linhas_Ja_Existentes_Banco": int((~mascara_novos).sum()),
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


def registrar_controle_carga(engine: Engine, id_carga: str, caminho: Path, tabela: str, hash_arquivo: str, linhas: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPSERT INTO controle_cargas
                (id_carga, data_hora_carga, tabela_destino, arquivo_origem, tamanho_bytes, hash_arquivo, linhas_gravadas)
                VALUES (:id_carga, now(), :tabela, :arquivo, :tamanho, :hash, :linhas)
                """
            ),
            {
                "id_carga": id_carga,
                "tabela": tabela,
                "arquivo": Path(caminho).name,
                "tamanho": int(Path(caminho).stat().st_size) if Path(caminho).exists() else 0,
                "hash": hash_arquivo,
                "linhas": int(linhas or 0),
            },
        )


def enviar_dataframe(
    df: pd.DataFrame,
    tabela: str,
    engine: Engine,
    id_carga: str,
    arquivo_origem: str,
    if_exists: str = "append",
    hash_arquivo: str = "",
) -> int:
    if df.empty and len(df.columns) == 0:
        return 0
    tabela_sql = limpar_identificador(tabela)
    out = preparar_dataframe_para_banco(df, id_carga, arquivo_origem, hash_arquivo=hash_arquivo)

    if if_exists == "upsert":
        return gravar_upsert_com_staging(out, tabela_sql, engine)

    if if_exists == "incremental":
        out, _ = filtrar_linhas_novas_banco(out, tabela_sql, engine)
        if out.empty:
            return 0
        if not tabela_existe(engine, tabela_sql):
            chaves = CHAVES_UNICAS_BANCO.get(tabela_sql)
            if chaves:
                garantir_tabela_final_com_pk(engine, tabela_sql, out, chaves)
        else:
            garantir_colunas_tabela(engine, tabela_sql, out)
        if_exists_sql = "append"
    else:
        if_exists_sql = if_exists
        if if_exists_sql == "append":
            garantir_colunas_tabela(engine, tabela_sql, out)

    dtype = {col: Text() for col in out.columns}
    out.to_sql(tabela_sql, engine, if_exists=if_exists_sql, index=False, chunksize=1000, method="multi", dtype=dtype)
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
        hash_arquivo = calcular_sha256(caminho) if caminho.exists() else ""
        df = ler_csv_seguro(caminho)
        if df.empty and len(df.columns) == 0:
            registrar_log(engine, id_carga, caminho.name, tabela_destino, 0, 0, "IGNORADO", "Arquivo vazio ou ilegível", if_exists)
            return {"Status": "IGNORADO", "Arquivo": caminho.name, "Tabela": tabela_destino, "Linhas_Lidas": 0, "Linhas_Gravadas": 0, "Linhas_Ignoradas": 0, "Mensagem": "Arquivo vazio ou ilegível"}

        out = preparar_dataframe_para_banco(df, id_carga, caminho.name, hash_arquivo=hash_arquivo)
        meta_incremental: Dict[str, object] = {}

        if if_exists == "upsert":
            gravadas = gravar_upsert_com_staging(out, tabela_sql, engine)
        elif if_exists == "incremental":
            out, meta_incremental = filtrar_linhas_novas_banco(out, tabela_sql, engine)
            if out.empty:
                registrar_log(engine, id_carga, caminho.name, tabela_destino, len(df), 0, "IGNORADO", "Nenhuma linha nova para gravar", if_exists)
                return {"Status": "IGNORADO", "Arquivo": caminho.name, "Tabela": tabela_destino, "Linhas_Lidas": len(df), "Linhas_Gravadas": 0, "Linhas_Ignoradas": len(df), "Mensagem": "Nenhuma linha nova para gravar", **meta_incremental}
            gravadas = enviar_dataframe(pd.DataFrame(), tabela_sql, engine, id_carga, caminho.name) if False else 0
            if not tabela_existe(engine, tabela_sql):
                chaves = CHAVES_UNICAS_BANCO.get(tabela_sql)
                if chaves:
                    garantir_tabela_final_com_pk(engine, tabela_sql, out, chaves)
            else:
                garantir_colunas_tabela(engine, tabela_sql, out)
            dtype = {col: Text() for col in out.columns}
            out.to_sql(tabela_sql, engine, if_exists="append", index=False, chunksize=1000, method="multi", dtype=dtype)
            gravadas = len(out)
        else:
            if if_exists == "append":
                garantir_colunas_tabela(engine, tabela_sql, out)
            dtype = {col: Text() for col in out.columns}
            out.to_sql(tabela_sql, engine, if_exists=if_exists, index=False, chunksize=1000, method="multi", dtype=dtype)
            gravadas = len(out)

        ignoradas = max(len(df) - gravadas, 0) if if_exists == "incremental" else 0
        registrar_controle_carga(engine, id_carga, caminho, tabela_destino, hash_arquivo, gravadas)
        registrar_log(engine, id_carga, caminho.name, tabela_destino, len(df), gravadas, "OK", "Carga gravada com validação", if_exists)
        return {"Status": "OK", "Arquivo": caminho.name, "Tabela": tabela_destino, "Linhas_Lidas": len(df), "Linhas_Gravadas": gravadas, "Linhas_Ignoradas": ignoradas, "Mensagem": "Carga gravada com validação", "Hash_Arquivo": hash_arquivo, **meta_incremental}
    except Exception as exc:
        try:
            registrar_log(engine, id_carga, caminho.name, tabela_destino, 0, 0, "ERRO", str(exc), if_exists)
        except Exception:
            pass
        return {"Status": "ERRO", "Arquivo": caminho.name, "Tabela": tabela_destino, "Linhas_Lidas": 0, "Linhas_Gravadas": 0, "Linhas_Ignoradas": 0, "Mensagem": str(exc)}


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
            linhas.append({"Tabela": tabela_sql, "Linhas_Banco": 0, "Primary_Key": "", "Status": "Não criada"})
            continue
        with engine.connect() as conn:
            qtd = conn.execute(text(f'SELECT count(*) FROM {quote_ident(tabela_sql)}')).scalar_one()
        pk = ", ".join(primary_key_da_tabela(engine, tabela_sql))
        linhas.append({"Tabela": tabela_sql, "Linhas_Banco": int(qtd or 0), "Primary_Key": pk, "Status": "OK"})
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
