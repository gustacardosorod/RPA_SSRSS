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
from sqlalchemy import create_engine, text
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
    "f_css_atendente.csv": "f_css_atendente",
    "f_css_geral_diario.csv": "f_css_geral_diario",
    "f_fsr_tratado.csv": "f_fsr_tratado",
    "f_indicadores_gerais.csv": "f_indicadores_gerais",
    "f_reclamacoes_sap_tratado.csv": "f_reclamacoes_sap_tratado",
    "f_volume_fila_diario.csv": "f_volume_fila_diario",
    "f_volume_geral_diario.csv": "f_volume_geral_diario",
    "f_gov_chamados_tratado.csv": "f_gov_chamados_tratado",
    "dim_status_chamados.csv": "dim_status_chamados",
    "dim_unidades_chamados.csv": "dim_unidades_chamados",
    "dim_responsaveis_chamados.csv": "dim_responsaveis_chamados",
    "dim_categorias_chamados.csv": "dim_categorias_chamados",
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

    # Se estiver usando verify-full, aponta para o certificado baixado
    if "sslmode=verify-full" in url and "sslrootcert=" not in url:
        separador = "&" if "?" in url else "?"
        url = f"{url}{separador}sslrootcert=C:/Users/GustavoCardoso/AppData/Roaming/postgresql/root.crt"

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
    out = normalizar_colunas_para_sql(df)
    out = out.astype("string").fillna("")
    out["id_carga"] = id_carga
    out["data_hora_carga"] = datetime.now(timezone.utc).isoformat()
    out["arquivo_origem"] = arquivo_origem

    dtype = {col: Text() for col in out.columns}
    out.to_sql(
        tabela_sql,
        engine,
        if_exists=if_exists,
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
                "Mensagem": "Arquivo vazio ou ilegível",
            }
        gravadas = enviar_dataframe(df, tabela_destino, engine, id_carga, caminho.name, if_exists=if_exists)
        registrar_log(engine, id_carga, caminho.name, tabela_destino, len(df), gravadas, "OK", "Carga gravada", if_exists)
        return {
            "Status": "OK",
            "Arquivo": caminho.name,
            "Tabela": tabela_destino,
            "Linhas_Lidas": len(df),
            "Linhas_Gravadas": gravadas,
            "Mensagem": "Carga gravada",
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
