from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd
import streamlit as st
from pandas.errors import EmptyDataError, ParserError

from app import executar_carga
from common import VERSAO_APP
from etl_agent_contact_diario import processar_agent_contact
from etl_css_atendente import processar_css_atendente
from etl_fsr_tratado import processar_fsr
from etl_gov_chamados import processar_gov_chamados
from etl_indicadores_gerais import processar_indicadores_gerais
from etl_reclamacoes_sap import processar_reclamacoes_sap
from etl_volume_fila_diario import processar_volume_fila
from db_cockroach import (
    consultar_log_cargas,
    consultar_resumo_tabelas,
    enviar_pasta_saida_para_banco,
    listar_tabelas,
    obter_database_name,
    preparar_database,
    testar_conexao,
)

st.set_page_config(
    page_title="RPA SSRS + SAP + GOV",
    page_icon="🚌",
    layout="wide",
)



def aplicar_estilo() -> None:
    """Aplica uma camada visual no Streamlit (paleta, cards, métricas, tabs e estados)."""
    st.markdown(
        """
        <style>
            :root {
                --azul-900: #0f172a;
                --azul-800: #1e293b;
                --azul-700: #1d4ed8;
                --azul-600: #2563eb;
                --azul-claro: #38bdf8;
                --texto-claro: #f8fafc;
                --texto-suave: #64748b;
                --borda: #e2e8f0;
                --sombra: rgba(15, 23, 42, .12);
                --verde: #16a34a;
                --verde-bg: #f0fdf4;
                --amarelo: #d97706;
                --amarelo-bg: #fffbeb;
                --vermelho: #dc2626;
                --vermelho-bg: #fef2f2;
            }

            .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1180px;}

            h1, h2, h3 {color: var(--azul-900);}

            /* ---------- Sidebar ---------- */
            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, var(--azul-900) 0%, var(--azul-800) 100%);
                border-right: 1px solid rgba(255,255,255,.06);
            }
            [data-testid="stSidebar"] * {color: var(--texto-claro) !important;}
            [data-testid="stSidebar"] .stRadio label {
                font-weight: 600;
                padding: .35rem .5rem;
                border-radius: 10px;
                transition: background .15s ease;
            }
            [data-testid="stSidebar"] .stRadio label:hover {background: rgba(255,255,255,.06);}
            [data-testid="stSidebar"] hr {border-color: rgba(255,255,255,.12);}
            [data-testid="stSidebar"] code {
                background: rgba(255,255,255,.08) !important;
                font-size: .75rem;
            }

            /* ---------- Hero card ---------- */
            .hero-card {
                padding: 1.4rem 1.6rem;
                border-radius: 22px;
                background: linear-gradient(135deg, var(--azul-900) 0%, var(--azul-700) 55%, var(--azul-claro) 100%);
                color: var(--texto-claro);
                box-shadow: 0 16px 40px var(--sombra);
                margin-bottom: 1.1rem;
            }
            .hero-card h1 {margin: 0; font-size: 1.85rem; line-height: 1.2; color: var(--texto-claro);}
            .hero-card p {margin: .5rem 0 0 0; opacity: .92; font-size: .98rem;}

            /* ---------- Mini cards (passo a passo) ---------- */
            .mini-card {
                border: 1px solid var(--borda);
                background: #ffffff;
                border-radius: 18px;
                padding: 1rem 1.1rem;
                min-height: 120px;
                box-shadow: 0 8px 26px rgba(15, 23, 42, .06);
                transition: transform .15s ease, box-shadow .15s ease;
            }
            .mini-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 12px 30px rgba(15, 23, 42, .10);
            }
            .mini-card h3 {margin-top: 0; margin-bottom: .35rem; font-size: 1rem; color: var(--azul-900);}
            .mini-card p {margin: 0; color: var(--texto-suave); font-size: .9rem; line-height: 1.4;}

            /* ---------- Alerta suave (banner informativo customizado) ---------- */
            .soft-alert {
                border-left: 5px solid var(--azul-600);
                background: #eff6ff;
                padding: .85rem 1.1rem;
                border-radius: 12px;
                color: #1e3a8a;
                margin: .6rem 0 1.1rem 0;
                font-size: .93rem;
                line-height: 1.5;
            }

            /* ---------- Badges de status (checklist) ---------- */
            .status-badge {
                display: inline-flex;
                align-items: center;
                gap: .35rem;
                padding: .2rem .65rem;
                border-radius: 999px;
                font-size: .8rem;
                font-weight: 700;
            }
            .status-ok {background: var(--verde-bg); color: var(--verde);}
            .status-warn {background: var(--amarelo-bg); color: var(--amarelo);}
            .status-erro {background: var(--vermelho-bg); color: var(--vermelho);}

            /* ---------- Métricas nativas do Streamlit ---------- */
            div[data-testid="stMetric"] {
                background: #ffffff;
                border: 1px solid var(--borda);
                padding: .85rem 1.1rem;
                border-radius: 16px;
                box-shadow: 0 8px 24px rgba(15, 23, 42, .05);
            }
            div[data-testid="stMetric"] label {color: var(--texto-suave) !important;}
            div[data-testid="stMetricValue"] {color: var(--azul-900) !important;}

            /* ---------- Botões ---------- */
            .stButton > button, .stDownloadButton > button {
                border-radius: 12px;
                font-weight: 700;
                transition: filter .15s ease, transform .1s ease;
            }
            .stButton > button:hover, .stDownloadButton > button:hover {
                filter: brightness(0.95);
            }
            .stButton > button:active, .stDownloadButton > button:active {
                transform: scale(0.98);
            }
            .stButton > button[kind="primary"] {
                background: var(--azul-700);
                border: none;
            }

            /* ---------- Tabs ---------- */
            div[data-testid="stTabs"] div[role="tablist"],
            .stTabs [data-baseweb="tab-list"] {
                gap: .5rem;
                border-bottom: none;
            }

            div[data-testid="stTabs"] button[role="tab"],
            .stTabs [data-baseweb="tab"] {
                border-radius: 999px !important;
                padding: .55rem .95rem !important;
                background: #f1f5f9 !important;
                border: 1px solid var(--borda) !important;
                font-weight: 700 !important;
                color: var(--azul-900) !important;
            }

            div[data-testid="stTabs"] button[role="tab"] p,
            div[data-testid="stTabs"] button[role="tab"] span,
            .stTabs [data-baseweb="tab"] p,
            .stTabs [data-baseweb="tab"] span {
                color: var(--azul-900) !important;
                font-weight: 700 !important;
            }

            div[data-testid="stTabs"] button[role="tab"][aria-selected="true"],
            .stTabs [data-baseweb="tab"][aria-selected="true"] {
                background: var(--azul-700) !important;
                border-color: var(--azul-700) !important;
                color: var(--texto-claro) !important;
            }

            div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p,
            div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] span,
            .stTabs [data-baseweb="tab"][aria-selected="true"] p,
            .stTabs [data-baseweb="tab"][aria-selected="true"] span {
                color: var(--texto-claro) !important;
                font-weight: 700 !important;
            }

            div[data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
                background-color: transparent !important;
            }

            /* ---------- Inputs, selects, expanders ---------- */
            .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] {
                border-radius: 10px !important;
            }
            details[data-testid="stExpander"] {
                border: 1px solid var(--borda);
                border-radius: 14px;
                box-shadow: 0 4px 14px rgba(15, 23, 42, .04);
            }
            details[data-testid="stExpander"] summary {font-weight: 600; color: var(--azul-900);}

            /* ---------- Alertas nativos (success/info/warning/error) ---------- */
            div[data-testid="stNotificationContentSuccess"],
            div[data-testid="stNotificationContentInfo"],
            div[data-testid="stNotificationContentWarning"],
            div[data-testid="stNotificationContentError"] {
                border-radius: 12px;
            }

            /* ---------- Dataframes ---------- */
            div[data-testid="stDataFrame"] {
                border: 1px solid var(--borda);
                border-radius: 14px;
                overflow: hidden;
            }

            /* ---------- Barra de progresso ---------- */
            div[data-testid="stProgress"] div[role="progressbar"] > div {
                background: linear-gradient(90deg, var(--azul-700), var(--azul-claro));
            }

            /* ---------- Divider mais discreto ---------- */
            hr {margin: 1.1rem 0;}
        </style>
        """,
        unsafe_allow_html=True,
    )


aplicar_estilo()

TIPOS_CARGA = {
    "Contact Center SSRS": "entrada",
    "SAP Service / FSR": "entrada_fsr",
    "Reclamações SAP": "entrada_sap",
    "GOV Chamados": "entrada_gov_chamados",
}

CARGAS_ONLINE = {
    "Tudo disponível": "tudo_tolerante",
    "Contact Center completo": "contact",
    "Agent Contact": "agent",
    "CSS Atendente": "css",
    "Volume/Fila": "volume_fila",
    "Indicadores Gerais": "indicadores",
    "FSR / SAP Service": "fsr",
    "Reclamações SAP": "reclamacoes_sap",
    "GOV Chamados": "gov_chamados",
}

RELATORIOS_OBRIGATORIOS = {
    "dim_atendentes.csv": "Dimensão de atendentes para relacionamento no Power BI",
    "f_agent_contact_diario.csv": "Fato diária por agente, com ligações e TMA",
    "f_css_atendente.csv": "CSS por atendente",
    "f_css_geral_diario.csv": "CSS geral diário",
    "f_fsr_tratado.csv": "Base tratada de FSR / SAP Service",
    "f_indicadores_gerais.csv": "Indicadores consolidados gerais",
    "f_reclamacoes_sap_tratado.csv": "Reclamações SAP tratadas",
    "f_volume_fila_diario.csv": "Volume diário por fila",
    "f_volume_geral_diario.csv": "Volume geral diário",
    "f_gov_chamados_tratado.csv": "GOV Chamados tratado",
}

DIMENSOES_GOV = {
    "dim_status_chamados.csv": "Status padronizados dos chamados GOV",
    "dim_unidades_chamados.csv": "Unidades/empresas dos chamados GOV",
    "dim_responsaveis_chamados.csv": "Responsáveis dos chamados GOV",
    "dim_categorias_chamados.csv": "Categorias dos chamados GOV",
}

MODOS = {
    "Base zero - recriar saídas": (False, True),
    "Incremental - manter histórico e incluir só novos": (False, False),
    "Corrigir período - substituir chaves existentes": (True, False),
}



def card_html(titulo: str, texto: str, emoji: str = "📌") -> str:
    return f"""
    <div class="mini-card">
        <h3>{emoji} {titulo}</h3>
        <p>{texto}</p>
    </div>
    """


def status_badge_html(status: str) -> str:
    """Converte o status textual do checklist em um badge colorido."""
    if "Gerado" in status:
        classe, rotulo = "status-ok", "✅ Gerado"
    elif "Vazio" in status:
        classe, rotulo = "status-warn", "⚠️ Vazio"
    else:
        classe, rotulo = "status-erro", "❌ Faltando"
    return f'<span class="status-badge {classe}">{rotulo}</span>'


def numero_br(valor) -> str:
    try:
        return f"{int(valor):,}".replace(",", ".")
    except Exception:
        return str(valor)


def init_session() -> None:
    if "workspace" not in st.session_state:
        raiz = Path(tempfile.gettempdir()) / f"rpa_ssrs_streamlit_{int(time.time())}"
        st.session_state.workspace = raiz
        st.session_state.console = ""
        st.session_state.logs_execucao = []
        st.session_state.ultima_carga = None
        preparar_workspace(limpar=True)


def pastas() -> Dict[str, Path]:
    raiz = Path(st.session_state.workspace)
    return {
        "base": raiz,
        "entrada": raiz / "entrada",
        "entrada_fsr": raiz / "entrada_fsr",
        "entrada_sap": raiz / "entrada_sap",
        "entrada_gov_chamados": raiz / "entrada_gov_chamados",
        "saida": raiz / "saida",
        "logs": raiz / "LOGS",
    }


def preparar_workspace(limpar: bool = False) -> None:
    raiz = Path(st.session_state.workspace)
    if limpar and raiz.exists():
        shutil.rmtree(raiz, ignore_errors=True)
    for pasta in pastas().values():
        pasta.mkdir(parents=True, exist_ok=True)


def limpar_nome_arquivo(nome: str) -> str:
    nome = Path(nome).name
    for ruim in ["..", "/", "\\", ":", "*", "?", '"', "<", ">", "|"]:
        nome = nome.replace(ruim, "_")
    return nome.strip() or f"arquivo_{int(time.time())}"


def caminho_relativo_seguro(caminho: Path, base: Path) -> str:
    """Retorna caminho relativo sem quebrar quando o Windows alterna nome longo/8.3.

    Exemplo do drama: C:\\Users\\GustavoCardoso e C:\\Users\\GUSTAV~1 podem apontar
    para o mesmo lugar, mas Path.relative_to() compara texto puro e explode.
    """
    caminho = Path(caminho)
    base = Path(base)

    for c, b in ((caminho, base), (caminho.resolve(), base.resolve())):
        try:
            return str(c.relative_to(b))
        except ValueError:
            continue

    try:
        rel = os.path.relpath(str(caminho.resolve()), str(base.resolve()))
        if rel and not rel.startswith("..") and not os.path.isabs(rel):
            return rel
    except Exception:
        pass

    return caminho.name


def caminho_dentro_da_pasta(caminho: Path, base: Path) -> bool:
    """Confirma que o caminho final permanece dentro da pasta base."""
    caminho_resolvido = Path(caminho).resolve()
    base_resolvida = Path(base).resolve()

    try:
        caminho_resolvido.relative_to(base_resolvida)
        return True
    except ValueError:
        pass

    try:
        comum = os.path.commonpath(
            [
                os.path.normcase(str(caminho_resolvido)),
                os.path.normcase(str(base_resolvida)),
            ]
        )
        return comum == os.path.normcase(str(base_resolvida))
    except Exception:
        return False


def salvar_upload(uploaded_file, destino: Path) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / limpar_nome_arquivo(uploaded_file.name)
    caminho.write_bytes(uploaded_file.getbuffer())
    return caminho


def safe_extract_zip(zip_bytes: bytes, destino: Path) -> List[str]:
    """Extrai ZIP com proteção contra path traversal e sem erro de relative_to no Windows."""
    destino = Path(destino).resolve()
    destino.mkdir(parents=True, exist_ok=True)

    extraidos: List[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue

            nome = member.filename.replace("\\", "/").lstrip("/")
            partes = [p for p in nome.split("/") if p and p != "."]

            # Evita ZIP malicioso tentando gravar fora do workspace.
            if not partes or any(p == ".." for p in partes):
                continue

            caminho_destino = destino.joinpath(*partes).resolve()

            if not caminho_dentro_da_pasta(caminho_destino, destino):
                continue

            caminho_destino.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(member) as origem, open(caminho_destino, "wb") as saida:
                shutil.copyfileobj(origem, saida)

            # Não use relative_to() aqui. No Windows, resolve() pode expandir GUSTAV~1
            # para GustavoCardoso e causar ValueError, mesmo estando no mesmo diretório.
            extraidos.append(str(Path(*partes)))

    return extraidos

def listar_arquivos_relativos(pasta: Path) -> List[str]:
    pasta = Path(pasta).resolve()
    if not pasta.exists():
        return []
    return sorted(caminho_relativo_seguro(p, pasta) for p in pasta.rglob("*") if p.is_file())


def csv_tem_conteudo(caminho: Path) -> bool:
    """Retorna False para CSV inexistente, zerado ou só com espaços/quebras de linha."""
    caminho = Path(caminho)
    if not caminho.exists() or not caminho.is_file():
        return False
    if caminho.stat().st_size == 0:
        return False
    try:
        amostra = caminho.read_bytes()[:4096]
        return bool(amostra.strip())
    except Exception:
        return False


def ler_csv_saida(caminho: Path, nrows=None) -> pd.DataFrame:
    """Lê CSV de saída/log sem derrubar o Streamlit quando o arquivo estiver vazio ou estranho.

    Alguns ETLs podem criar arquivos vazios quando a carga falha, quando não há dados válidos
    ou quando o Streamlit Cloud reinicia no meio da brincadeira. Nesses casos, devolvemos
    um DataFrame vazio e a tela mostra um aviso amigável.
    """
    caminho = Path(caminho)
    if not csv_tem_conteudo(caminho):
        return pd.DataFrame()

    candidatos = []
    ultimo_erro = None
    for encoding in ("utf-8-sig", "utf-8", "latin1"):
        for sep in (";", ",", "\t", "|"):
            try:
                df = pd.read_csv(
                    caminho,
                    sep=sep,
                    encoding=encoding,
                    dtype=str,
                    nrows=nrows,
                    engine="python",
                    on_bad_lines="skip",
                )
                candidatos.append((len(df.columns), len(df), df))
            except EmptyDataError:
                return pd.DataFrame()
            except (UnicodeDecodeError, ParserError, ValueError, OSError) as exc:
                ultimo_erro = exc
                continue

    if candidatos:
        candidatos.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidatos[0][2]

    if ultimo_erro:
        raise ultimo_erro
    return pd.DataFrame()


def listar_csvs_validos(pasta: Path) -> Tuple[List[Path], List[Path]]:
    """Separa CSVs com conteúdo dos CSVs vazios/incompletos."""
    arquivos = sorted(Path(pasta).glob("*.csv")) if Path(pasta).exists() else []
    validos = [p for p in arquivos if csv_tem_conteudo(p)]
    vazios = [p for p in arquivos if p not in validos]
    return validos, vazios


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="dados")
    return buffer.getvalue()


def zipar_pasta_saida() -> bytes:
    buffer = io.BytesIO()
    ps = pastas()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for chave in ["saida", "logs"]:
            pasta = ps[chave]
            if not pasta.exists():
                continue
            for arquivo in pasta.rglob("*"):
                if arquivo.is_file():
                    rel = caminho_relativo_seguro(arquivo, pasta).replace("\\", "/")
                    zf.write(arquivo, arcname=f"{chave}/{rel}")
    return buffer.getvalue()


def executar_tudo_tolerante(substituir: bool, reprocessar_tudo: bool) -> Tuple[List[dict], str]:
    ps = pastas()
    tarefas = [
        ("Agent Contact", lambda: processar_agent_contact(ps["entrada"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("CSS Atendente", lambda: processar_css_atendente(ps["entrada"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("Volume/Fila", lambda: processar_volume_fila(ps["entrada"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("Indicadores Gerais", lambda: processar_indicadores_gerais(ps["entrada"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("FSR / SAP Service", lambda: processar_fsr(ps["entrada_fsr"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("Reclamações SAP", lambda: processar_reclamacoes_sap(ps["entrada_sap"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
        ("GOV Chamados", lambda: processar_gov_chamados(ps["entrada_gov_chamados"], ps["saida"], ps["logs"], substituir, reprocessar_tudo)),
    ]

    logs: List[dict] = []
    console = io.StringIO()
    progresso = st.progress(0)
    status = st.empty()

    with contextlib.redirect_stdout(console):
        for idx, (nome, func) in enumerate(tarefas, start=1):
            status.info(f"Executando: {nome}")
            try:
                _, log = func()
                log["Carga_Streamlit"] = nome
                logs.append(log)
                print(f"OK - {nome}")
            except Exception as exc:
                erro = {
                    "Carga_Streamlit": nome,
                    "Status": "Ignorado/Erro",
                    "Erro": str(exc),
                    "Observacao": "Carga não processada. Verifique se os arquivos esperados foram enviados.",
                }
                logs.append(erro)
                print(f"AVISO - {nome}: {exc}")
            progresso.progress(idx / len(tarefas))
    status.success("Processamento finalizado.")

    return logs, console.getvalue()


def executar_carga_online(carga: str, substituir: bool, reprocessar_tudo: bool) -> Tuple[List[dict], str]:
    if carga == "tudo_tolerante":
        return executar_tudo_tolerante(substituir, reprocessar_tudo)

    ps = pastas()
    console = io.StringIO()
    with contextlib.redirect_stdout(console):
        logs = executar_carga(carga, ps, substituir=substituir, reprocessar_tudo=reprocessar_tudo)
    return logs, console.getvalue()


def mostrar_kpis_saida() -> None:
    arquivos, vazios = listar_csvs_validos(pastas()["saida"])
    logs, _ = listar_csvs_validos(pastas()["logs"])
    cols = st.columns(4)
    cols[0].metric("Arquivos tratados", len(arquivos))
    total_linhas = 0
    for arq in arquivos:
        try:
            df_tmp = ler_csv_saida(arq, nrows=None)
            total_linhas += len(df_tmp)
        except Exception:
            pass
    cols[1].metric("Linhas nas saídas", f"{total_linhas:,}".replace(",", "."))
    cols[2].metric("Logs", len(logs))
    cols[3].metric("Versão", VERSAO_APP.split("_")[0])
    if vazios:
        st.caption(f"{len(vazios)} arquivo(s) CSV vazio(s)/incompleto(s) foram ignorados nos indicadores.")


def verificar_relatorios_obrigatorios() -> pd.DataFrame:
    """Monta checklist dos arquivos que o sistema precisa entregar para o Power BI."""
    saida = pastas()["saida"]
    linhas = []
    for nome, descricao in {**RELATORIOS_OBRIGATORIOS, **DIMENSOES_GOV}.items():
        caminho = saida / nome
        existe = caminho.exists()
        tem_conteudo = csv_tem_conteudo(caminho)
        linhas_csv = None
        colunas_csv = None
        if tem_conteudo:
            try:
                df = ler_csv_saida(caminho)
                linhas_csv = len(df)
                colunas_csv = len(df.columns)
            except Exception:
                linhas_csv = None
                colunas_csv = None
        if not existe:
            status = "❌ Faltando"
        elif not tem_conteudo:
            status = "⚠️ Vazio"
        else:
            status = "✅ Gerado"
        linhas.append(
            {
                "Status": status,
                "Arquivo": nome,
                "Tipo": "Obrigatório" if nome in RELATORIOS_OBRIGATORIOS else "Dimensão GOV",
                "Linhas": linhas_csv,
                "Colunas": colunas_csv,
                "Descrição": descricao,
            }
        )
    return pd.DataFrame(linhas)


def mostrar_checklist_relatorios(expandido: bool = True) -> None:
    st.subheader("📌 Relatórios que o sistema deve gerar")
    checklist = verificar_relatorios_obrigatorios()
    total = len(checklist)
    gerados = int(checklist["Status"].astype(str).str.contains("Gerado").sum()) if not checklist.empty else 0
    faltando = total - gerados

    c1, c2, c3 = st.columns(3)
    c1.metric("Esperados", total)
    c2.metric("Gerados", gerados)
    c3.metric("Faltando/vazios", faltando)

    if total:
        st.progress(gerados / total, text=f"{gerados} de {total} relatórios prontos")

    st.dataframe(
        checklist,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Arquivo": st.column_config.TextColumn("Arquivo", width="medium"),
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
        },
    )
    if faltando:
        st.warning("Há relatório obrigatório faltando ou vazio. Rode a carga correspondente ou envie os arquivos de entrada corretos.")
    else:
        st.success("Todos os relatórios obrigatórios foram encontrados com conteúdo.")


def tela_inicio() -> None:
    st.markdown(
        f"""
        <div class="hero-card">
            <h1>🚌 RPA SSRS + SAP + GOV Chamados</h1>
            <p>Tratamento de relatórios, geração das bases finais e envio incremental para o CockroachDB. Versão {VERSAO_APP}.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(card_html("1. Importar", "Suba ZIP completo ou arquivos separados por tipo de carga.", "📤"), unsafe_allow_html=True)
    with c2:
        st.markdown(card_html("2. Processar", "Rode o ETL online e gere CSVs tratados na pasta de saída.", "⚙️"), unsafe_allow_html=True)
    with c3:
        st.markdown(card_html("3. Validar", "Confira checklist, prévia das bases e logs de processamento.", "📊"), unsafe_allow_html=True)
    with c4:
        st.markdown(card_html("4. Banco", "Envie somente registros novos para o CockroachDB.", "🗄️"), unsafe_allow_html=True)

    st.markdown(
        """
        <div class="soft-alert">
            Fluxo recomendado: importe os arquivos, processe em modo incremental, valide o checklist e envie ao banco em <b>Incremental inteligente</b>.
        </div>
        """,
        unsafe_allow_html=True,
    )

    mostrar_kpis_saida()
    mostrar_checklist_relatorios(expandido=True)


def tela_importar() -> None:
    st.header("📤 Importar relatórios")
    ps = pastas()

    col1, col2 = st.columns([2, 1])
    with col1:
        zip_up = st.file_uploader(
            "Enviar ZIP completo do projeto ou ZIP com pastas entrada/entrada_fsr/entrada_sap/entrada_gov_chamados",
            type=["zip"],
            accept_multiple_files=False,
        )
        if zip_up and st.button("Extrair ZIP enviado", type="primary"):
            extraidos = safe_extract_zip(zip_up.getvalue(), ps["base"])
            st.success(f"{len(extraidos)} arquivo(s) extraído(s).")
            with st.expander("Arquivos extraídos"):
                st.write(extraidos[:300])
    with col2:
        st.warning("Evite subir base com dados sensíveis em repositório público. Upload na sessão é menos pior, veja só que barra.")

    st.divider()
    st.subheader("Upload por tipo de carga")

    tipo = st.selectbox("Tipo de relatório", list(TIPOS_CARGA.keys()))
    destino_base = ps[TIPOS_CARGA[tipo]]

    if tipo == "Contact Center SSRS":
        lote = st.text_input("Nome do lote/pasta diária", value=datetime.today().strftime("%d_%m_%Y"))
        destino = destino_base / limpar_nome_arquivo(lote)
        st.caption("Envie os CSVs do lote: Volume 4 - Daily, Agent Contact, Script Result 5 e Script Result 3.")
    else:
        destino = destino_base
        if tipo == "GOV Chamados":
            st.caption("Para GOV Chamados, envie o XLSX/CSV exportado do SAP/GOV. A saída obrigatória será f_gov_chamados_tratado.csv e as dimensões dim_*_chamados.csv.")

    uploads = st.file_uploader(
        "Selecione os arquivos",
        type=["csv", "txt", "xlsx", "xls"],
        accept_multiple_files=True,
        key=f"upload_{tipo}",
    )

    if uploads and st.button("Salvar arquivos enviados"):
        salvos = [salvar_upload(arq, destino) for arq in uploads]
        st.success(f"{len(salvos)} arquivo(s) salvo(s) em {caminho_relativo_seguro(destino, ps['base'])}.")
        st.write([caminho_relativo_seguro(p, ps["base"]) for p in salvos])

    st.divider()
    st.subheader("Arquivos atualmente na sessão")
    for nome, chave in TIPOS_CARGA.items():
        arquivos = listar_arquivos_relativos(ps[chave])
        with st.expander(f"{nome} ({len(arquivos)} arquivo(s))"):
            st.write(arquivos or "Nenhum arquivo enviado ainda.")

    if st.button("Limpar sessão e apagar arquivos temporários"):
        preparar_workspace(limpar=True)
        st.session_state.console = ""
        st.session_state.logs_execucao = []
        st.success("Sessão limpa.")


def tela_processar() -> None:
    st.header("⚙️ Processar ETL")

    col1, col2 = st.columns(2)
    with col1:
        carga_nome = st.selectbox("Carga", list(CARGAS_ONLINE.keys()))
        carga = CARGAS_ONLINE[carga_nome]
    with col2:
        modo_nome = st.selectbox("Modo de processamento", list(MODOS.keys()), index=0)
        substituir, reprocessar_tudo = MODOS[modo_nome]

    st.caption(
        "No modo online, 'Tudo disponível' tenta todas as cargas e não derruba o app se algum conjunto de arquivos não foi enviado. Até o erro ganha coleira."
    )

    if st.button("Executar tratamento", type="primary"):
        try:
            logs, console = executar_carga_online(carga, substituir, reprocessar_tudo)
            st.session_state.logs_execucao = logs
            st.session_state.console = console
            st.session_state.ultima_carga = carga_nome

            st.success("Tratamento concluído.")
            if logs:
                st.dataframe(pd.DataFrame(logs), use_container_width=True)
            if console:
                with st.expander("Console da execução"):
                    st.code(console)
        except Exception as exc:
            st.session_state.console = str(exc)
            st.error(f"Erro ao executar carga: {exc}")

    mostrar_kpis_saida()
    mostrar_checklist_relatorios(expandido=True)


def tela_preview() -> None:
    st.header("📊 Pré-visualizar dados")
    arquivos, vazios = listar_csvs_validos(pastas()["saida"])
    if vazios:
        with st.expander("Arquivos ignorados na prévia"):
            st.write([p.name for p in vazios])
            st.caption("Esses CSVs estão vazios ou incompletos. O app não vai cair por causa deles, porque já basta a gravidade.")
    if not arquivos:
        st.warning("Nenhum CSV tratado com conteúdo encontrado. Rode o ETL primeiro, essa etapa inconveniente chamada 'ter dados'.")
        return

    arquivo = st.selectbox("Arquivo tratado", arquivos, format_func=lambda p: p.name)
    df = ler_csv_saida(arquivo)
    if df.empty and len(df.columns) == 0:
        st.warning(f"O arquivo {arquivo.name} está vazio ou não pôde ser lido com segurança.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Linhas", f"{len(df):,}".replace(",", "."))
    c2.metric("Colunas", len(df.columns))
    c3.metric("Arquivo", arquivo.name)

    st.subheader("Primeiras linhas")
    st.dataframe(df.head(100), use_container_width=True)

    with st.expander("Tipos das colunas"):
        tipos = pd.DataFrame({"Coluna": df.columns, "Tipo": [str(t) for t in df.dtypes]})
        st.dataframe(tipos, use_container_width=True)

    st.subheader("Resumos automáticos")
    col_status = next((c for c in ["Status_Padronizado", "Status", "Fechado"] if c in df.columns), None)
    col_mes = next((c for c in ["Ano_Mes", "Mes_Referencia"] if c in df.columns), None)
    col_unidade = next((c for c in ["Unidade", "Empresa", "Fila", "Grupo"] if c in df.columns), None)

    colunas = st.columns(3)
    if col_status:
        with colunas[0]:
            st.markdown("**Por status**")
            resumo_status = df[col_status].fillna("").value_counts().reset_index()
            resumo_status.columns = [col_status, "Qtd"]
            st.dataframe(resumo_status, use_container_width=True)
    if col_mes:
        with colunas[1]:
            st.markdown("**Por mês**")
            resumo_mes = df[col_mes].fillna("").value_counts().sort_index().reset_index()
            resumo_mes.columns = [col_mes, "Qtd"]
            st.dataframe(resumo_mes, use_container_width=True)
    if col_unidade:
        with colunas[2]:
            st.markdown("**Por unidade/fila/grupo**")
            resumo_uni = df[col_unidade].fillna("").value_counts().head(30).reset_index()
            resumo_uni.columns = [col_unidade, "Qtd"]
            st.dataframe(resumo_uni, use_container_width=True)

    if "Chamado_Aberto" in df.columns:
        abertos = pd.to_numeric(df["Chamado_Aberto"], errors="coerce").fillna(0).sum()
        st.metric("Chamados em aberto", int(abertos))


def tela_exportar() -> None:
    st.header("📥 Exportar arquivos tratados")
    arquivos, arquivos_vazios = listar_csvs_validos(pastas()["saida"])
    logs, logs_vazios = listar_csvs_validos(pastas()["logs"])

    if not arquivos and not logs and not arquivos_vazios and not logs_vazios:
        st.warning("Nada para exportar ainda.")
        return

    if arquivos_vazios or logs_vazios:
        st.info(f"CSV(s) vazio(s)/incompleto(s) detectado(s): {len(arquivos_vazios) + len(logs_vazios)}. Eles entram no ZIP, mas não viram Excel.")

    st.download_button(
        "Baixar pacote completo ZIP",
        data=zipar_pasta_saida(),
        file_name=f"rpa_ssrs_saida_logs_{datetime.now():%Y%m%d_%H%M%S}.zip",
        mime="application/zip",
        type="primary",
    )

    st.divider()
    st.subheader("Arquivos obrigatórios para Power BI")
    mostrar_checklist_relatorios(expandido=True)

    st.divider()
    st.subheader("Arquivos tratados")
    arquivos_ordenados = sorted(
        arquivos,
        key=lambda p: (0 if p.name in RELATORIOS_OBRIGATORIOS else 1, p.name),
    )
    for arquivo in arquivos_ordenados:
        with st.expander(arquivo.name):
            data = arquivo.read_bytes()
            st.download_button(
                f"Baixar CSV - {arquivo.name}",
                data=data,
                file_name=arquivo.name,
                mime="text/csv",
                key=f"csv_{arquivo.name}",
            )
            try:
                df = ler_csv_saida(arquivo)
                st.download_button(
                    f"Baixar Excel - {arquivo.stem}.xlsx",
                    data=dataframe_to_excel_bytes(df),
                    file_name=f"{arquivo.stem}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"xlsx_{arquivo.name}",
                )
            except Exception as exc:
                st.caption(f"Excel indisponível para este arquivo: {exc}")

    st.subheader("Logs")
    for arquivo in logs:
        st.download_button(
            f"Baixar log - {arquivo.name}",
            data=arquivo.read_bytes(),
            file_name=arquivo.name,
            mime="text/csv",
            key=f"log_{arquivo.name}",
        )


def tela_logs() -> None:
    st.header("🧾 Logs do processamento")

    if st.session_state.logs_execucao:
        st.subheader("Última execução")
        st.dataframe(pd.DataFrame(st.session_state.logs_execucao), use_container_width=True)

    if st.session_state.console:
        with st.expander("Console capturado", expanded=True):
            st.code(st.session_state.console)

    st.subheader("Arquivos de log")
    arquivos, vazios = listar_csvs_validos(pastas()["logs"])
    if vazios:
        st.caption(f"{len(vazios)} log(s) vazio(s)/incompleto(s) foram ignorados na visualização.")
    if not arquivos:
        st.info("Nenhum log com conteúdo salvo ainda.")
        return

    arquivo = st.selectbox("Log", arquivos, format_func=lambda p: p.name)
    try:
        df_log = ler_csv_saida(arquivo)
        if df_log.empty and len(df_log.columns) == 0:
            st.info("Log vazio.")
        else:
            st.dataframe(df_log, use_container_width=True)
    except Exception:
        st.text(arquivo.read_text(encoding="utf-8-sig", errors="replace"))



def tela_banco_cockroach() -> None:
    st.markdown(
        """
        <div class="hero-card">
            <h1>🗄️ Banco CockroachDB</h1>
            <p>Central de conexão, envio incremental e auditoria das cargas tratadas.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nome_db = st.text_input("Nome do database do projeto", value=obter_database_name("rpa_ssrs"))

    tab_conexao, tab_envio, tab_monitoramento = st.tabs([
        "🔌 Conexão",
        "🚀 Envio incremental",
        "📈 Monitoramento",
    ])

    with tab_conexao:
        st.markdown(
            """
            <div class="soft-alert">
                Configure o segredo do Streamlit Cloud em <b>Manage app → Settings → Secrets</b>. Senha no código é crime contra a própria semana.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.code(
            """[cockroachdb]
database_url = "postgresql://USUARIO:SENHA@HOST:26257/defaultdb?sslmode=verify-full"
database_name = "rpa_ssrs""",
            language="toml",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Testar conexão", type="primary", use_container_width=True):
                try:
                    info = testar_conexao()
                    st.success("Conexão OK com o cluster.")
                    st.json(info)
                except Exception as exc:
                    st.error(f"Falha na conexão: {exc}")
        with col2:
            if st.button("Criar database/controles", use_container_width=True):
                try:
                    db = preparar_database(nome_db)
                    st.success(f"Database `{db}` pronto com tabelas de controle.")
                except Exception as exc:
                    st.error(f"Erro ao preparar database: {exc}")
        with col3:
            if st.button("Listar tabelas", use_container_width=True):
                try:
                    df_tabs = listar_tabelas(nome_db)
                    st.dataframe(df_tabs, use_container_width=True, hide_index=True)
                except Exception as exc:
                    st.error(f"Erro ao listar tabelas: {exc}")

    with tab_envio:
        st.subheader("Enviar CSVs tratados para o banco")
        st.caption("O modo recomendado compara as chaves no banco e grava somente registros novos. Revolucionário: não duplicar dados.")

        checklist = verificar_relatorios_obrigatorios()
        gerados = int(checklist["Status"].astype(str).str.contains("Gerado").sum()) if not checklist.empty else 0
        faltando = len(checklist) - gerados
        c1, c2, c3 = st.columns(3)
        c1.metric("Relatórios gerados", gerados)
        c2.metric("Pendentes/vazios", faltando)
        c3.metric("Arquivos oficiais", len(checklist))

        with st.expander("Ver checklist antes do envio", expanded=faltando > 0):
            st.dataframe(checklist, use_container_width=True, hide_index=True)

        apenas_padrao = st.checkbox("Enviar apenas relatórios oficiais do projeto", value=True)
        modo_label = st.radio(
            "Modo de gravação no banco",
            [
                "Incremental inteligente - somente novos",
                "Append bruto - empilha tudo",
                "Replace - apaga e recria",
            ],
            horizontal=True,
            index=0,
        )
        modo_envio = {
            "Incremental inteligente - somente novos": "incremental",
            "Append bruto - empilha tudo": "append",
            "Replace - apaga e recria": "replace",
        }[modo_label]

        if modo_envio == "incremental":
            st.success("Recomendado: lê as chaves já existentes no CockroachDB e envia apenas linhas novas.")
        elif modo_envio == "append":
            st.warning("Append bruto pode duplicar histórico. Útil só quando você tem certeza, o que estatisticamente é raro.")
        else:
            st.error("Replace apaga e recria a tabela destino. Use só para correção pesada ou teste controlado.")

        if st.button("Enviar para CockroachDB", type="primary", use_container_width=True):
            try:
                resultados = enviar_pasta_saida_para_banco(
                    pastas()["saida"],
                    database_name=nome_db,
                    apenas_padrao=apenas_padrao,
                    if_exists=modo_envio,
                )
                if resultados:
                    df_res = pd.DataFrame(resultados)
                    linhas_lidas = pd.to_numeric(df_res.get("Linhas_Lidas", 0), errors="coerce").fillna(0).sum()
                    linhas_gravadas = pd.to_numeric(df_res.get("Linhas_Gravadas", 0), errors="coerce").fillna(0).sum()
                    linhas_ignoradas = pd.to_numeric(df_res.get("Linhas_Ignoradas", 0), errors="coerce").fillna(0).sum()

                    st.success("Envio finalizado.")
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("Arquivos avaliados", len(df_res))
                    k2.metric("Linhas lidas", numero_br(linhas_lidas))
                    k3.metric("Linhas gravadas", numero_br(linhas_gravadas))
                    k4.metric("Ignoradas", numero_br(linhas_ignoradas))
                    st.dataframe(df_res, use_container_width=True, hide_index=True)
                else:
                    st.warning("Nenhum CSV encontrado para enviar. Rode o ETL antes, essa etapa chata chamada gerar dados.")
            except Exception as exc:
                st.error(f"Erro ao enviar dados para o CockroachDB: {exc}")

    with tab_monitoramento:
        st.subheader("Resumo das tabelas no banco")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Atualizar resumo das tabelas", type="primary", use_container_width=True):
                try:
                    df_resumo = consultar_resumo_tabelas(nome_db, apenas_padrao=True)
                    st.session_state.resumo_banco = df_resumo
                except Exception as exc:
                    st.error(f"Erro ao consultar resumo: {exc}")
        with col2:
            limite = st.number_input("Limite de registros do log", min_value=10, max_value=1000, value=100, step=10)

        if "resumo_banco" in st.session_state:
            df_resumo = st.session_state.resumo_banco
            if not df_resumo.empty:
                total_banco = pd.to_numeric(df_resumo["Linhas_Banco"], errors="coerce").fillna(0).sum()
                st.metric("Total de linhas nas tabelas oficiais", numero_br(total_banco))
            st.dataframe(df_resumo, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Histórico de cargas")
        if st.button("Consultar log_cargas", use_container_width=True):
            try:
                df_log = consultar_log_cargas(nome_db, limite=int(limite))
                st.dataframe(df_log, use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Erro ao consultar histórico: {exc}")


def tela_sobre() -> None:
    st.header("ℹ️ Sobre o projeto e deploy")

    st.markdown(
        """
### O que este app faz

Este Streamlit é o front-end do RPA de tratamento. Ele recebe os relatórios exportados, executa os ETLs e devolve as bases prontas para Power BI.

### Relatórios finais obrigatórios

- `dim_atendentes.csv`
- `f_agent_contact_diario.csv`
- `f_css_atendente.csv`
- `f_css_geral_diario.csv`
- `f_fsr_tratado.csv`
- `f_indicadores_gerais.csv`
- `f_reclamacoes_sap_tratado.csv`
- `f_volume_fila_diario.csv`
- `f_volume_geral_diario.csv`
- `f_gov_chamados_tratado.csv`

O GOV Chamados também gera dimensões auxiliares quando houver dados: `dim_status_chamados.csv`, `dim_unidades_chamados.csv`, `dim_responsaveis_chamados.csv` e `dim_categorias_chamados.csv`.

### O que ele não faz sozinho online

Ele não consegue acessar um SSRS interno da rede da empresa se estiver hospedado fora dela. Também não roda Power Automate Desktop dentro do Streamlit Cloud. Para extração automática de sistema interno, use uma VM/máquina agendada na rede e envie os arquivos gerados para este app, SharePoint, storage ou repositório controlado.

### Estrutura esperada

```text
RPA_SSRS/
├── entrada/
│   └── DD_MM_AAAA/
│       ├── Agent - Contact Handling Time 4 - Daily.csv
│       ├── Script Result 3 - Queue Volume per Day.csv
│       ├── Script Result 5 - Agent Volume.csv
│       └── Volume 4 - Daily.csv
├── entrada_fsr/
├── entrada_sap/
├── entrada_gov_chamados/
├── saida/
├── LOGS/
├── app.py
├── main.py
├── streamlit_app.py
└── requirements.txt
```

### Rodar local

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

### Rodar ETL local por linha de comando

```bash
python main.py --carga tudo --reprocessar-tudo
python main.py --carga gov_chamados --reprocessar-tudo
```

### Banco CockroachDB

O app possui uma aba `🗄️ Banco CockroachDB` para:

- testar conexão com o cluster;
- criar o database `rpa_ssrs`;
- criar `log_cargas` e `controle_cargas`;
- enviar os CSVs tratados da pasta `saida` para o banco;
- consultar histórico das cargas.

Configure os secrets no Streamlit Cloud usando:

```toml
[cockroachdb]
database_url = "postgresql://USUARIO:SENHA@HOST:26257/defaultdb?sslmode=verify-full"
database_name = "rpa_ssrs"
```

### Deploy no Streamlit Community Cloud

1. Suba este projeto em um repositório GitHub.
2. Garanta que `streamlit_app.py` e `requirements.txt` estejam na raiz.
3. No Streamlit Community Cloud, crie um novo app apontando para o repositório, branch e arquivo `streamlit_app.py`.
4. Depois de publicado, use o upload da interface para processar os relatórios.
        """
    )


init_session()

with st.sidebar:
    st.title("🚌 RPA Hub")
    st.caption(f"Tratamento, validação e banco · {VERSAO_APP.split('_')[0]}")
    st.divider()

    pagina = st.radio(
        "Navegação",
        [
            "🏠 Início",
            "📤 Importar relatórios",
            "⚙️ Processar ETL",
            "📊 Pré-visualizar dados",
            "📥 Exportar arquivos tratados",
            "🧾 Logs do processamento",
            "🗄️ Banco CockroachDB",
            "ℹ️ Sobre o projeto",
        ],
        label_visibility="collapsed",
    )

    st.divider()
    with st.expander("📁 Workspace temporário"):
        st.code(str(Path(st.session_state.workspace)), language="text")

if pagina == "🏠 Início":
    tela_inicio()
elif pagina == "📤 Importar relatórios":
    tela_importar()
elif pagina == "⚙️ Processar ETL":
    tela_processar()
elif pagina == "📊 Pré-visualizar dados":
    tela_preview()
elif pagina == "📥 Exportar arquivos tratados":
    tela_exportar()
elif pagina == "🧾 Logs do processamento":
    tela_logs()
elif pagina == "🗄️ Banco CockroachDB":
    tela_banco_cockroach()
else:
    tela_sobre()