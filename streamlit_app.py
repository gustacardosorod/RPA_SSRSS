from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd
import streamlit as st

from app import executar_carga
from common import VERSAO_APP
from etl_agent_contact_diario import processar_agent_contact
from etl_css_atendente import processar_css_atendente
from etl_fsr_tratado import processar_fsr
from etl_gov_chamados import processar_gov_chamados
from etl_indicadores_gerais import processar_indicadores_gerais
from etl_reclamacoes_sap import processar_reclamacoes_sap
from etl_volume_fila_diario import processar_volume_fila

st.set_page_config(
    page_title="RPA SSRS + SAP + GOV",
    page_icon="🚌",
    layout="wide",
)

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

MODOS = {
    "Base zero - recriar saídas": (False, True),
    "Incremental - manter histórico e incluir só novos": (False, False),
    "Corrigir período - substituir chaves existentes": (True, False),
}


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


def salvar_upload(uploaded_file, destino: Path) -> Path:
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / limpar_nome_arquivo(uploaded_file.name)
    caminho.write_bytes(uploaded_file.getbuffer())
    return caminho


def safe_extract_zip(zip_bytes: bytes, destino: Path) -> List[str]:
    destino.mkdir(parents=True, exist_ok=True)
    extraidos: List[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            nome = member.filename.replace("\\", "/")
            partes = [p for p in nome.split("/") if p and p not in {".", ".."}]
            if not partes:
                continue
            caminho_destino = destino.joinpath(*partes).resolve()
            if not str(caminho_destino).startswith(str(destino.resolve())):
                continue
            caminho_destino.parent.mkdir(parents=True, exist_ok=True)
            caminho_destino.write_bytes(zf.read(member))
            extraidos.append(str(caminho_destino.relative_to(destino)))
    return extraidos


def listar_arquivos_relativos(pasta: Path) -> List[str]:
    if not pasta.exists():
        return []
    return sorted(str(p.relative_to(pasta)) for p in pasta.rglob("*") if p.is_file())


def ler_csv_saida(caminho: Path, nrows=None) -> pd.DataFrame:
    return pd.read_csv(caminho, sep=";", encoding="utf-8-sig", dtype=str, nrows=nrows)


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
                    zf.write(arquivo, arcname=f"{chave}/{arquivo.relative_to(pasta)}")
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
    arquivos = sorted(pastas()["saida"].glob("*.csv"))
    cols = st.columns(4)
    cols[0].metric("Arquivos tratados", len(arquivos))
    total_linhas = 0
    for arq in arquivos:
        try:
            total_linhas += max(sum(1 for _ in arq.open("r", encoding="utf-8-sig")) - 1, 0)
        except Exception:
            pass
    cols[1].metric("Linhas nas saídas", f"{total_linhas:,}".replace(",", "."))
    cols[2].metric("Logs", len(list(pastas()["logs"].glob("*.csv"))))
    cols[3].metric("Versão", VERSAO_APP.split("_")[0])


def tela_inicio() -> None:
    st.title("🚌 RPA SSRS + SAP + GOV Chamados")
    st.caption(f"Versão: {VERSAO_APP}")

    st.markdown(
        """
Este app executa os tratamentos do projeto RPA em uma interface online:

- Contact Center SSRS: Agent, CSS, Volume/Fila e Indicadores Gerais.
- SAP Service / FSR.
- Reclamações SAP.
- GOV Chamados.
- Exportação em CSV, Excel e ZIP com logs.

A parte online processa arquivos enviados pelo usuário. Extração automática de sistemas internos, tipo SSRS em rede `10.x.x.x` ou Power Automate Desktop, continua dependendo de máquina/VM dentro da rede. Streamlit Cloud não é médium corporativo, infelizmente.
        """
    )

    mostrar_kpis_saida()

    st.info(
        "Fluxo recomendado: envie um ZIP com a estrutura completa do projeto ou suba os arquivos por tipo de carga, depois vá em ⚙️ Processar ETL."
    )


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

    uploads = st.file_uploader(
        "Selecione os arquivos",
        type=["csv", "txt", "xlsx", "xls"],
        accept_multiple_files=True,
        key=f"upload_{tipo}",
    )

    if uploads and st.button("Salvar arquivos enviados"):
        salvos = [salvar_upload(arq, destino) for arq in uploads]
        st.success(f"{len(salvos)} arquivo(s) salvo(s) em {destino.relative_to(ps['base'])}.")
        st.write([str(p.relative_to(ps["base"])) for p in salvos])

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


def tela_preview() -> None:
    st.header("📊 Pré-visualizar dados")
    arquivos = sorted(pastas()["saida"].glob("*.csv"))
    if not arquivos:
        st.warning("Nenhum CSV tratado encontrado. Rode o ETL primeiro, essa etapa inconveniente chamada 'ter dados'.")
        return

    arquivo = st.selectbox("Arquivo tratado", arquivos, format_func=lambda p: p.name)
    df = ler_csv_saida(arquivo)

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
    arquivos = sorted(pastas()["saida"].glob("*.csv"))
    logs = sorted(pastas()["logs"].glob("*.csv"))

    if not arquivos and not logs:
        st.warning("Nada para exportar ainda.")
        return

    st.download_button(
        "Baixar pacote completo ZIP",
        data=zipar_pasta_saida(),
        file_name=f"rpa_ssrs_saida_logs_{datetime.now():%Y%m%d_%H%M%S}.zip",
        mime="application/zip",
        type="primary",
    )

    st.divider()
    st.subheader("Arquivos tratados")
    for arquivo in arquivos:
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
    arquivos = sorted(pastas()["logs"].glob("*.csv"))
    if not arquivos:
        st.info("Nenhum log salvo ainda.")
        return

    arquivo = st.selectbox("Log", arquivos, format_func=lambda p: p.name)
    try:
        st.dataframe(ler_csv_saida(arquivo), use_container_width=True)
    except Exception:
        st.text(arquivo.read_text(encoding="utf-8-sig", errors="replace"))


def tela_sobre() -> None:
    st.header("ℹ️ Sobre o projeto e deploy")

    st.markdown(
        """
### O que este app faz

Este Streamlit é o front-end do RPA de tratamento. Ele recebe os relatórios exportados, executa os ETLs e devolve as bases prontas para Power BI.

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

### Deploy no Streamlit Community Cloud

1. Suba este projeto em um repositório GitHub.
2. Garanta que `streamlit_app.py` e `requirements.txt` estejam na raiz.
3. No Streamlit Community Cloud, crie um novo app apontando para o repositório, branch e arquivo `streamlit_app.py`.
4. Depois de publicado, use o upload da interface para processar os relatórios.
        """
    )


init_session()

with st.sidebar:
    st.title("Menu")
    pagina = st.radio(
        "Navegação",
        [
            "🏠 Início",
            "📤 Importar relatórios",
            "⚙️ Processar ETL",
            "📊 Pré-visualizar dados",
            "📥 Exportar arquivos tratados",
            "🧾 Logs do processamento",
            "ℹ️ Sobre o projeto",
        ],
    )

    st.divider()
    st.caption("Workspace temporário")
    st.code(str(Path(st.session_state.workspace)))

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
else:
    tela_sobre()
