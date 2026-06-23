from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from sqlalchemy import text

from rpa_ssrs_assinaturas import (
    REQUIRED_FILES,
    TABELAS_BI,
    extrair_dias_parametro_arquivo,
    processar_assinaturas,
)

BASE_DIR = Path(__file__).resolve().parent
ENTRADA_HISTORICO = BASE_DIR / "entrada_assinaturas"
SAIDA = BASE_DIR / "saida"
UPLOADS = BASE_DIR / "uploads_streamlit"
CONTROLE_RELATORIO = "script_result_5_agent_volume"


# =============================================================================
# Configuração e utilitários
# =============================================================================

def get_secret(secao: str, chave: str, default: Optional[str] = None) -> Optional[str]:
    try:
        bloco = st.secrets.get(secao, {})
        if hasattr(bloco, "get"):
            valor = bloco.get(chave)
            return str(valor).strip() if valor else default
    except Exception:
        pass
    return default


def default_database_name() -> str:
    return get_secret("cockroachdb", "database_name") or os.getenv("COCKROACH_DATABASE_NAME") or "rpa_ssrs"


def _arquivo_corresponde_upload(nome_arquivo: str, nome_padrao: str) -> bool:
    nome = Path(nome_arquivo).stem.lower().strip()
    base = Path(nome_padrao).stem.lower().strip()
    return nome == base or nome.startswith(base + "_") or nome.startswith(base + " (") or nome.startswith(base + " -")


def identificar_relatorio_upload(nome_arquivo: str) -> str:
    for chave, padrao in REQUIRED_FILES.items():
        if _arquivo_corresponde_upload(nome_arquivo, padrao):
            return chave
    return "não identificado"


def normalizar_data(valor: object) -> str:
    if valor is None:
        return ""
    texto = str(valor).strip()
    if not texto:
        return ""
    dt = pd.to_datetime(texto[:10], errors="coerce")
    if pd.isna(dt):
        return ""
    return dt.strftime("%Y-%m-%d")


def salvar_uploads(uploaded_files) -> Path:
    pasta = UPLOADS / datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    for arquivo in uploaded_files:
        destino = pasta / arquivo.name
        contador = 1
        while destino.exists():
            destino = pasta / f"{Path(arquivo.name).stem}_{contador}{Path(arquivo.name).suffix}"
            contador += 1
        with destino.open("wb") as f:
            f.write(arquivo.getbuffer())
    return pasta


def analisar_uploads(uploaded_files) -> pd.DataFrame:
    if not uploaded_files:
        return pd.DataFrame()

    linhas: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for arquivo in uploaded_files:
            destino = tmp_path / arquivo.name
            with destino.open("wb") as f:
                f.write(arquivo.getbuffer())

            info = extrair_dias_parametro_arquivo(destino)
            relatorio = identificar_relatorio_upload(arquivo.name)
            data_parametro = normalizar_data(info.get("data_parametro"))
            status = str(info.get("status_dia", ""))
            ok_data = relatorio == "css_agent" and status.lower() == "dia explícito" and bool(data_parametro)

            linhas.append(
                {
                    "Arquivo": arquivo.name,
                    "Relatorio": relatorio,
                    "Tamanho_KB": round(arquivo.size / 1024, 2),
                    "Parametro_Dias": info.get("parametro_dias", ""),
                    "Data_Parametro": data_parametro,
                    "Status_Dia": status,
                    "Validação": "OK" if relatorio != "não identificado" else "NÃO IDENTIFICADO",
                    "CSS_Diario_OK": ok_data,
                }
            )
    return pd.DataFrame(linhas)


def obter_data_css_agent(df_upload: pd.DataFrame) -> str:
    if df_upload is None or df_upload.empty:
        return ""
    css = df_upload[df_upload["Relatorio"].eq("css_agent")].copy()
    if css.empty:
        return ""
    datas = sorted({normalizar_data(v) for v in css["Data_Parametro"].tolist() if normalizar_data(v)})
    return datas[0] if len(datas) == 1 else ""


def validar_upload(df_upload: pd.DataFrame, exigir_css_queue: bool = True) -> Tuple[List[str], List[str], str]:
    erros: List[str] = []
    avisos: List[str] = []
    data_parametro = ""

    if df_upload is None or df_upload.empty:
        return ["Nenhum arquivo enviado."], avisos, data_parametro

    obrigatorios = [k for k in REQUIRED_FILES if exigir_css_queue or k != "css_queue"]
    contagens = df_upload["Relatorio"].value_counts().to_dict()

    desconhecidos = int(contagens.get("não identificado", 0))
    if desconhecidos:
        erros.append(f"{desconhecidos} arquivo(s) não foram reconhecidos como relatório SSRS esperado.")

    for chave in obrigatorios:
        qtd = int(contagens.get(chave, 0))
        nome_padrao = REQUIRED_FILES[chave]
        if qtd == 0:
            erros.append(f"Arquivo obrigatório ausente: {nome_padrao}")
        elif qtd > 1:
            erros.append(
                f"Foram enviados {qtd} arquivos para {nome_padrao}. "
                "Neste painel manual, envie apenas um lote/data por importação."
            )

    css = df_upload[df_upload["Relatorio"].eq("css_agent")].copy()
    if not css.empty:
        datas_css = sorted({normalizar_data(v) for v in css["Data_Parametro"].tolist() if normalizar_data(v)})
        status_css = " | ".join(css["Status_Dia"].astype(str).unique().tolist())
        if len(datas_css) == 1 and css["CSS_Diario_OK"].astype(bool).all():
            data_parametro = datas_css[0]
        elif len(datas_css) > 1:
            erros.append("O Script Result 5 trouxe mais de uma data no parâmetro. Envie uma data por importação.")
        else:
            erros.append(
                "O Script Result 5 - Agent Volume precisa estar com Day/Dias preenchido com uma única data. "
                f"Status encontrado: {status_css or 'não identificado'}."
            )
    else:
        erros.append("Script Result 5 - Agent Volume não encontrado. Ele é obrigatório para definir a data do lote.")

    return erros, avisos, data_parametro


def listar_saidas() -> pd.DataFrame:
    linhas = []
    if not SAIDA.exists():
        return pd.DataFrame()
    for nome, tabela in TABELAS_BI.items():
        p = SAIDA / nome
        if not p.exists():
            continue
        try:
            linhas_count = sum(1 for _ in p.open("r", encoding="utf-8-sig", errors="ignore")) - 1
        except Exception:
            linhas_count = None
        linhas.append(
            {
                "Arquivo": p.name,
                "Tabela_Banco": tabela,
                "Linhas_Aprox": linhas_count,
                "Tamanho_KB": round(p.stat().st_size / 1024, 2),
                "Atualizado_Em": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return pd.DataFrame(linhas)


def ler_tabela_tratada(arquivo: str, max_linhas: int = 500) -> pd.DataFrame:
    """Lê uma saída tratada que será enviada ao banco."""
    caminho = SAIDA / arquivo
    if not caminho.exists():
        return pd.DataFrame()

    # As saídas do ETL são padronizadas em ; e utf-8-sig. O fallback evita que um CSV rebelde derrube o front.
    try:
        return pd.read_csv(caminho, sep=";", encoding="utf-8-sig", dtype=str, nrows=max_linhas)
    except Exception:
        return pd.read_csv(caminho, dtype=str, nrows=max_linhas)


def exibir_tabelas_tratadas(titulo: str = "📊 Tabelas tratadas", key_prefix: str = "tabelas") -> None:
    """Mostra no front o resumo e a prévia das tabelas que vão para o banco."""
    df_saidas = listar_saidas()
    if df_saidas.empty:
        st.info("Nenhuma tabela tratada foi gerada ainda.")
        return

    st.markdown(f"#### {titulo}")

    total_linhas = int(pd.to_numeric(df_saidas["Linhas_Aprox"], errors="coerce").fillna(0).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Tabelas tratadas", len(df_saidas))
    c2.metric("Linhas geradas", f"{total_linhas:,}".replace(",", "."))
    c3.metric("Destino", "CockroachDB")

    st.dataframe(df_saidas, use_container_width=True, hide_index=True)

    max_linhas = st.number_input(
        "Linhas para pré-visualizar por tabela",
        min_value=50,
        max_value=5000,
        value=300,
        step=50,
        key=f"{key_prefix}_max_linhas",
    )

    modo = st.radio(
        "Visualização",
        ["Selecionar tabela", "Todas em abas"],
        horizontal=True,
        key=f"{key_prefix}_modo_visualizacao",
    )

    if modo == "Selecionar tabela":
        opcoes = df_saidas["Arquivo"].tolist()
        selecionado = st.selectbox(
            "Tabela tratada para visualizar",
            opcoes,
            format_func=lambda arq: f"{df_saidas.loc[df_saidas['Arquivo'].eq(arq), 'Tabela_Banco'].iloc[0]}  |  {arq}",
            key=f"{key_prefix}_select_tabela",
        )
        caminho = SAIDA / selecionado
        df = ler_tabela_tratada(selecionado, max_linhas=int(max_linhas))
        tabela_banco = df_saidas.loc[df_saidas["Arquivo"].eq(selecionado), "Tabela_Banco"].iloc[0]
        st.caption(f"Destino no banco: `{tabela_banco}` | Arquivo: `{selecionado}`")
        st.dataframe(df, use_container_width=True, hide_index=True)
        if caminho.exists():
            st.download_button(
                label=f"⬇️ Baixar {selecionado}",
                data=caminho.read_bytes(),
                file_name=selecionado,
                mime="text/csv",
                use_container_width=True,
                key=f"{key_prefix}_download_{selecionado}",
            )
    else:
        tabs = st.tabs(df_saidas["Tabela_Banco"].astype(str).tolist())
        for tab, row in zip(tabs, df_saidas.to_dict("records")):
            with tab:
                arquivo = row["Arquivo"]
                caminho = SAIDA / arquivo
                st.caption(f"Arquivo: `{arquivo}` | Linhas aproximadas: `{row.get('Linhas_Aprox')}`")
                st.dataframe(ler_tabela_tratada(arquivo, max_linhas=int(max_linhas)), use_container_width=True, hide_index=True)
                if caminho.exists():
                    st.download_button(
                        label=f"⬇️ Baixar {arquivo}",
                        data=caminho.read_bytes(),
                        file_name=arquivo,
                        mime="text/csv",
                        use_container_width=True,
                        key=f"{key_prefix}_tab_download_{arquivo}",
                    )


def criar_id_upload(uploaded_files) -> str:
    if not uploaded_files:
        return ""
    partes = [f"{arquivo.name}:{getattr(arquivo, 'size', 0)}" for arquivo in uploaded_files]
    return "|".join(sorted(partes))


# =============================================================================
# Banco: bloqueio de datas repetidas
# =============================================================================

def preparar_controle_importacao(database_name: str):
    from db_cockroach import criar_engine, preparar_database

    db = preparar_database(database_name)
    engine = criar_engine(database_name=db)
    ddl = """
    CREATE TABLE IF NOT EXISTS controle_importacao_ssrs_manual (
        data_parametro STRING NOT NULL,
        relatorio_referencia STRING NOT NULL,
        data_hora_importacao TIMESTAMPTZ NOT NULL DEFAULT now(),
        arquivos STRING NULL,
        qtd_arquivos INT8 NULL,
        status STRING NULL,
        mensagem STRING NULL,
        PRIMARY KEY (data_parametro, relatorio_referencia)
    )
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))
    return db, engine


def consultar_datas_importadas(database_name: str) -> pd.DataFrame:
    from db_cockroach import criar_engine, preparar_database, tabela_existe

    db = preparar_database(database_name)
    engine = criar_engine(database_name=db)

    linhas: List[Dict[str, object]] = []

    # Controle novo do app manual.
    try:
        preparar_controle_importacao(db)
        query = text(
            """
            SELECT data_parametro, relatorio_referencia, data_hora_importacao, arquivos, status, mensagem
            FROM controle_importacao_ssrs_manual
            WHERE relatorio_referencia = :relatorio
            ORDER BY data_parametro DESC
            """
        )
        df_controle = pd.read_sql(query, engine, params={"relatorio": CONTROLE_RELATORIO})
        for _, row in df_controle.iterrows():
            linhas.append(
                {
                    "Data_Parametro": normalizar_data(row.get("data_parametro")),
                    "Origem": "controle_importacao_ssrs_manual",
                    "Data_Hora_Importacao": str(row.get("data_hora_importacao", "")),
                    "Status": row.get("status", ""),
                    "Arquivos": row.get("arquivos", ""),
                    "Mensagem": row.get("mensagem", ""),
                }
            )
    except Exception:
        pass

    # Também consulta a fato já existente, para pegar histórico anterior ao painel manual.
    try:
        if tabela_existe(engine, "f_css_atendente"):
            df_fato = pd.read_sql(
                text(
                    """
                    SELECT DISTINCT data AS data_parametro
                    FROM f_css_atendente
                    WHERE data IS NOT NULL AND trim(data) <> ''
                    ORDER BY data_parametro DESC
                    """
                ),
                engine,
            )
            for _, row in df_fato.iterrows():
                data = normalizar_data(row.get("data_parametro"))
                if data:
                    linhas.append(
                        {
                            "Data_Parametro": data,
                            "Origem": "f_css_atendente",
                            "Data_Hora_Importacao": "",
                            "Status": "já existe na fato",
                            "Arquivos": "",
                            "Mensagem": "Detectado pelo campo data da tabela f_css_atendente",
                        }
                    )
    except Exception:
        pass

    if not linhas:
        return pd.DataFrame(columns=["Data_Parametro", "Origem", "Data_Hora_Importacao", "Status", "Arquivos", "Mensagem"])

    df = pd.DataFrame(linhas)
    df = df[df["Data_Parametro"].astype(str).str.len().gt(0)]
    df = df.drop_duplicates(subset=["Data_Parametro"], keep="first")
    return df.sort_values("Data_Parametro", ascending=False).reset_index(drop=True)


def data_ja_importada(database_name: str, data_parametro: str) -> bool:
    data_parametro = normalizar_data(data_parametro)
    if not data_parametro:
        return False
    df = consultar_datas_importadas(database_name)
    if df.empty:
        return False
    return data_parametro in set(df["Data_Parametro"].astype(str))


def registrar_data_importada(database_name: str, data_parametro: str, arquivos: List[str], mensagem: str = "Carga manual concluída") -> None:
    db, engine = preparar_controle_importacao(database_name)
    data_parametro = normalizar_data(data_parametro)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPSERT INTO controle_importacao_ssrs_manual
                (data_parametro, relatorio_referencia, data_hora_importacao, arquivos, qtd_arquivos, status, mensagem)
                VALUES (:data_parametro, :relatorio, now(), :arquivos, :qtd_arquivos, :status, :mensagem)
                """
            ),
            {
                "data_parametro": data_parametro,
                "relatorio": CONTROLE_RELATORIO,
                "arquivos": " | ".join(arquivos),
                "qtd_arquivos": len(arquivos),
                "status": "OK",
                "mensagem": mensagem[:4000],
            },
        )


def testar_conexao_banco(database_name: str):
    from db_cockroach import testar_conexao

    return testar_conexao(database_name)


def consultar_banco(database_name: str):
    from db_cockroach import consultar_log_cargas, consultar_resumo_tabelas

    return consultar_resumo_tabelas(database_name), consultar_log_cargas(database_name, limite=50)


# =============================================================================
# Processamento manual isolado
# =============================================================================

def processar_upload_manual(
    uploaded_files,
    database_name: str,
    data_parametro: str,
    enviar_banco: bool = True,
    exigir_css_queue: bool = True,
) -> Dict[str, object]:
    """Processa somente o lote manual enviado, sem reprocessar todo o histórico local."""
    pasta_upload = salvar_uploads(uploaded_files)
    entrada_run = pasta_upload / "_entrada_historico_run"
    saida_run = pasta_upload / "_saida_run"
    resultado: Dict[str, object]

    try:
        resultado = processar_assinaturas(
            pasta_assinatura=pasta_upload,
            base_dir=BASE_DIR,
            entrada_historico=entrada_run,
            saida=saida_run,
            data_lote=data_parametro,
            exigir_css_queue=exigir_css_queue,
            enviar_para_banco=enviar_banco,
            database_name=database_name,
            db_mode="upsert",
            limpar_assinaturas_apos_banco=False,
            max_lotes=1,
            exigir_data_css_agent=True,
        )

        # Guarda uma cópia operacional do último processamento para conferência/download.
        SAIDA.mkdir(parents=True, exist_ok=True)
        if saida_run.exists():
            for arquivo in saida_run.glob("*.csv"):
                shutil.copy2(arquivo, SAIDA / arquivo.name)

        # Guarda histórico do lote manual processado.
        ENTRADA_HISTORICO.mkdir(parents=True, exist_ok=True)
        pasta_hist_destino = ENTRADA_HISTORICO / f"manual_{data_parametro}_{datetime.now():%Y%m%d_%H%M%S}"
        pasta_hist_destino.mkdir(parents=True, exist_ok=True)
        for arquivo in pasta_upload.glob("*.csv"):
            shutil.copy2(arquivo, pasta_hist_destino / arquivo.name)
        resultado["historico_manual"] = str(pasta_hist_destino)
        resultado["saida_manual"] = str(SAIDA)
        return resultado
    finally:
        try:
            if pasta_upload.exists():
                shutil.rmtree(pasta_upload, ignore_errors=True)
        except Exception:
            pass


# =============================================================================
# UI
# =============================================================================
st.set_page_config(
    page_title="Sistema de Carga SSRS",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container {padding-top: 1.0rem; padding-bottom: 2rem;}
        [data-testid="stMetricValue"] {font-size: 1.55rem;}
        .hero {
            border: 1px solid rgba(49, 91, 190, .20);
            border-radius: 18px;
            padding: 18px 22px;
            background: linear-gradient(120deg, rgba(49,91,190,.12), rgba(255,255,255,.04));
            margin-bottom: 18px;
        }
        .system-card {
            border: 1px solid rgba(128,128,128,.22);
            border-radius: 16px;
            padding: 16px 18px;
            background: rgba(128,128,128,.045);
            min-height: 112px;
        }
        .ok-box {
            border: 1px solid rgba(20, 160, 90, .35);
            border-radius: 12px;
            padding: 12px 14px;
            background: rgba(20, 160, 90, .08);
        }
        .warn-box {
            border: 1px solid rgba(245, 170, 35, .40);
            border-radius: 12px;
            padding: 12px 14px;
            background: rgba(245, 170, 35, .10);
        }
        .danger-box {
            border: 1px solid rgba(220, 70, 70, .40);
            border-radius: 12px;
            padding: 12px 14px;
            background: rgba(220, 70, 70, .08);
        }
        .muted {opacity: .75; font-size: .92rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### 🧭 Menu do sistema")
    pagina = st.radio(
        "Navegação",
        ["Nova importação", "Banco e histórico", "Arquivos tratados", "Ajuda operacional"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("### ⚙️ Configuração")
    database_name = st.text_input("Database CockroachDB", value=default_database_name())
    exigir_css_queue = st.checkbox("Exigir Script Result 3 - Queue Volume per Day", value=True)
    st.caption("Modo banco fixo: UPSERT. Datas repetidas são bloqueadas antes da carga.")
    st.divider()
    if st.button("🔌 Testar conexão", use_container_width=True):
        try:
            info = testar_conexao_banco(database_name)
            st.success(f"Conectado como {info.get('usuario')} em {info.get('database_name')}")
        except Exception as exc:
            st.error(f"Falha na conexão: {exc}")

st.markdown(
    """
    <div class="hero">
        <h2 style="margin:0">🚌 Sistema de Carga SSRS para BI</h2>
        <div class="muted">Upload manual dos CSVs, validação do parâmetro <b>Dias</b>, bloqueio de datas repetidas e envio controlado ao CockroachDB.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Estado compartilhado da página de importação
if "df_upload" not in st.session_state:
    st.session_state["df_upload"] = pd.DataFrame()
if "data_parametro" not in st.session_state:
    st.session_state["data_parametro"] = ""
if "preview_resultado" not in st.session_state:
    st.session_state["preview_resultado"] = None
if "preview_data_parametro" not in st.session_state:
    st.session_state["preview_data_parametro"] = ""
if "preview_upload_id" not in st.session_state:
    st.session_state["preview_upload_id"] = ""

if pagina == "Nova importação":
    st.subheader("📤 Nova importação manual")

    st.markdown(
        """
        <div class="warn-box">
            <b>Regra do painel:</b> envie um lote por vez. A data oficial da carga vem do parâmetro <code>Dias</code> do arquivo <b>Script Result 5 - Agent Volume</b>. Se essa data já existir no banco, a importação é bloqueada.
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded_files = st.file_uploader(
        "Selecione os CSVs exportados do SSRS",
        type=["csv"],
        accept_multiple_files=True,
        help="Envie os 4 relatórios esperados do mesmo dia. O Script Result 5 precisa estar com Day/Dias = data específica.",
    )

    df_upload = analisar_uploads(uploaded_files) if uploaded_files else pd.DataFrame()
    st.session_state["df_upload"] = df_upload

    erros: List[str] = []
    avisos: List[str] = []
    data_parametro = ""
    data_repetida = False
    erro_banco_validacao = ""

    if uploaded_files:
        erros, avisos, data_parametro = validar_upload(df_upload, exigir_css_queue=exigir_css_queue)
        st.session_state["data_parametro"] = data_parametro
        if data_parametro:
            try:
                data_repetida = data_ja_importada(database_name, data_parametro)
            except Exception as exc:
                erro_banco_validacao = str(exc)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Arquivos enviados", len(uploaded_files or []))
    c2.metric("Data detectada", data_parametro or "-")
    c3.metric("Duplicidade", "Sim" if data_repetida else "Não")
    c4.metric("Modo banco", "UPSERT")

    if uploaded_files:
        st.markdown("#### Validação dos arquivos")
        st.dataframe(df_upload.drop(columns=["CSS_Diario_OK"], errors="ignore"), use_container_width=True, hide_index=True)

        if avisos:
            for aviso in avisos:
                st.warning(aviso)
        if erros:
            for erro in erros:
                st.error(erro)
        elif erro_banco_validacao:
            st.error(f"Não consegui validar duplicidade no banco: {erro_banco_validacao}")
        elif data_repetida:
            st.markdown(
                f"""
                <div class="danger-box">
                    <b>Importação bloqueada.</b><br>
                    A data <code>{data_parametro}</code> já existe no banco. O painel não envia data repetida.
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"""
                <div class="ok-box">
                    <b>Pacote liberado para importação.</b><br>
                    Data do lote: <code>{data_parametro}</code>. Essa data ainda não foi identificada no banco.
                </div>
                """,
                unsafe_allow_html=True,
            )

    upload_id_atual = criar_id_upload(uploaded_files)
    preview_pronto = (
        bool(st.session_state.get("preview_resultado"))
        and st.session_state.get("preview_data_parametro") == data_parametro
        and st.session_state.get("preview_upload_id") == upload_id_atual
    )

    bloqueado_preview = not uploaded_files or bool(erros)
    bloqueado_importacao = (
        not uploaded_files
        or bool(erros)
        or bool(erro_banco_validacao)
        or data_repetida
        or not preview_pronto
    )

    st.divider()
    st.markdown("#### 1) Gerar e revisar tabelas tratadas")
    st.caption("Antes de enviar ao banco, gere a prévia. O painel mostra exatamente os CSVs tratados que serão usados na carga. Porque mandar dado no escuro é uma tradição humana horrível, não uma estratégia de BI.")

    col1, col2 = st.columns([1, 1])
    with col1:
        gerar_preview = st.button(
            "👁️ Gerar prévia das tabelas tratadas",
            type="secondary",
            use_container_width=True,
            disabled=bloqueado_preview,
        )
    with col2:
        limpar_preview = st.button("🧹 Limpar prévia", use_container_width=True, disabled=not bool(st.session_state.get("preview_resultado")))

    if limpar_preview:
        st.session_state["preview_resultado"] = None
        st.session_state["preview_data_parametro"] = ""
        st.session_state["preview_upload_id"] = ""
        st.rerun()

    if gerar_preview:
        try:
            with st.status("Gerando tabelas tratadas para conferência", expanded=True) as status:
                st.write("1/3 Salvando arquivos enviados")
                st.write("2/3 Executando ETL sem gravar no banco")
                resultado = processar_upload_manual(
                    uploaded_files,
                    database_name=database_name,
                    data_parametro=data_parametro or datetime.now().strftime("%Y-%m-%d"),
                    enviar_banco=False,
                    exigir_css_queue=exigir_css_queue,
                )
                st.write("3/3 Preparando prévia no front")
                status.update(label="Prévia gerada", state="complete")

            st.session_state["preview_resultado"] = resultado
            st.session_state["preview_data_parametro"] = data_parametro
            st.session_state["preview_upload_id"] = upload_id_atual
            st.success("Tabelas tratadas geradas. Revise a prévia antes de importar.")
        except Exception as exc:
            st.session_state["preview_resultado"] = None
            st.error(f"Falha ao gerar a prévia das tabelas tratadas: {exc}")

    preview_pronto = (
        bool(st.session_state.get("preview_resultado"))
        and st.session_state.get("preview_data_parametro") == data_parametro
        and st.session_state.get("preview_upload_id") == upload_id_atual
    )

    if preview_pronto:
        exibir_tabelas_tratadas(
            titulo=f"Tabelas tratadas que irão para o banco | Data {data_parametro}",
            key_prefix="preview_importacao",
        )
        with st.expander("Detalhe técnico da prévia"):
            st.json(st.session_state.get("preview_resultado"), expanded=False)
    elif uploaded_files and not erros:
        st.info("Gere a prévia das tabelas tratadas antes de liberar o envio ao banco.")

    st.divider()
    st.markdown("#### 2) Enviar ao banco")
    confirmar = st.checkbox("Confirmo que revisei as tabelas tratadas acima e quero enviar ao banco", value=False)
    importar = st.button(
        "✅ Importar tabelas tratadas no banco",
        type="primary",
        use_container_width=True,
        disabled=bloqueado_importacao or not confirmar,
    )

    if data_repetida:
        st.warning("Envio bloqueado porque a data detectada já existe no banco.")
    elif not preview_pronto and uploaded_files and not erros:
        st.warning("Envio bloqueado até você gerar e revisar a prévia das tabelas tratadas.")

    if importar:
        try:
            with st.status("Importando tabelas tratadas no banco", expanded=True) as status:
                st.write("1/4 Salvando arquivos enviados")
                st.write("2/4 Executando ETL isolado do lote")
                resultado = processar_upload_manual(
                    uploaded_files,
                    database_name=database_name,
                    data_parametro=data_parametro,
                    enviar_banco=True,
                    exigir_css_queue=exigir_css_queue,
                )
                st.write("3/4 Registrando data importada no controle")
                registrar_data_importada(
                    database_name=database_name,
                    data_parametro=data_parametro,
                    arquivos=[a.name for a in uploaded_files],
                    mensagem="Carga manual importada pelo painel Streamlit após prévia das tabelas tratadas",
                )
                st.write("4/4 Finalizando")
                status.update(label="Carga concluída", state="complete")

            st.session_state["preview_resultado"] = resultado
            st.session_state["preview_data_parametro"] = data_parametro
            st.session_state["preview_upload_id"] = upload_id_atual

            st.success(f"Importação concluída para a data {data_parametro}.")
            if resultado.get("resultados_banco"):
                st.markdown("#### Resultado do envio ao banco")
                st.dataframe(pd.DataFrame(resultado["resultados_banco"]), use_container_width=True, hide_index=True)
            exibir_tabelas_tratadas(
                titulo=f"Tabelas tratadas importadas | Data {data_parametro}",
                key_prefix="pos_importacao",
            )
            with st.expander("Detalhe técnico do processamento"):
                st.json(resultado, expanded=False)
        except Exception as exc:
            st.error(f"Falha na importação: {exc}")

elif pagina == "Banco e histórico":
    st.subheader("🗄️ Banco e histórico de importações")
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🔄 Atualizar histórico de datas", use_container_width=True):
            st.session_state["_atualizar_historico"] = True
    with col2:
        if st.button("📊 Atualizar resumo das tabelas", use_container_width=True):
            st.session_state["_atualizar_banco"] = True

    try:
        df_datas = consultar_datas_importadas(database_name)
        st.markdown("#### Datas já importadas/bloqueadas")
        if df_datas.empty:
            st.info("Nenhuma data importada encontrada ainda.")
        else:
            c1, c2 = st.columns(2)
            c1.metric("Datas detectadas", len(df_datas))
            c2.metric("Última data", df_datas["Data_Parametro"].max())
            st.dataframe(df_datas, use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(f"Erro ao consultar datas importadas: {exc}")

    st.divider()
    try:
        resumo_tabelas, logs = consultar_banco(database_name)
        st.markdown("#### Linhas por tabela")
        st.dataframe(resumo_tabelas, use_container_width=True, hide_index=True)
        st.markdown("#### Últimas cargas")
        st.dataframe(logs, use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error(f"Erro ao consultar banco: {exc}")

elif pagina == "Arquivos tratados":
    st.subheader("📁 Arquivos tratados da última execução")
    st.caption("Aqui ficam as tabelas finais geradas pelo ETL. São os mesmos arquivos que o processo usa para alimentar o CockroachDB.")
    exibir_tabelas_tratadas("Tabelas tratadas disponíveis", key_prefix="arquivos_tratados")

else:
    st.subheader("🧾 Ajuda operacional")
    st.markdown(
        """
        ### Fluxo correto
        1. Baixe manualmente os CSVs no SSRS.
        2. No **Script Result 5 - Agent Volume**, informe uma data específica no parâmetro **Day/Dias**.
        3. Entre neste painel e envie os arquivos do mesmo dia.
        4. O painel valida a data do Script Result 5.
        5. Clique em **Gerar prévia das tabelas tratadas** para ver no front os CSVs finais que irão para o banco.
        6. Revise as tabelas geradas, linha a linha se o tédio corporativo permitir.
        7. Se a data ainda não existir no banco, confirme e importe em modo **UPSERT**.
        8. Se a data já existir, a importação é bloqueada.

        ### Arquivos esperados
        - `Volume 4 - Daily.csv`
        - `Agent - Contact Handling Time 4 - Daily.csv`
        - `Script Result 5 - Agent Volume.csv`
        - `Script Result 3 - Queue Volume per Day.csv`

        ### Regra do CSS por agente
        `Dias: All` não é aceito para carga diária. O painel exige uma data explícita, como `2026-06-22`.
        """
    )
