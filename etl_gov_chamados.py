from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from common import (
    adicionar_colunas_calendario,
    deduplicar_por_chave,
    normalizar,
    normalizar_coluna,
    parse_datas_mistas,
    salvar_csv,
    salvar_log,
    salvar_tabela_incremental,
)

NOME_TABELA = "f_gov_chamados_tratado"

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

ARQUIVOS_VALIDOS = {".csv", ".txt", ".xlsx"}

STATUS_CONCLUIDO = {
    "concluido",
    "concluida",
    "conclusao",
    "fechado",
    "fechada",
    "finalizado",
    "finalizada",
    "resolvido",
    "resolvida",
    "encerrado",
    "encerrada",
}
STATUS_CANCELADO = {"cancelado", "cancelada", "cancelamento"}
STATUS_ANDAMENTO = {
    "em andamento",
    "em processamento",
    "andamento",
    "processamento",
    "em tratativa",
    "em atendimento",
}
STATUS_PENDENTE = {"pendente", "aguardando", "aguardando cliente", "aguardando retorno"}
STATUS_ABERTO = {"aberto", "aberta", "novo", "nova"}

COLUNAS_RELEVANTES = [
    "Data",
    "Ano",
    "Mes",
    "Dia",
    "Ano_Mes",
    "Numero_Chamado",
    "Protocolo",
    "Prioridade",
    "Status",
    "Status_Padronizado",
    "Status_Macro",
    "Cliente",
    "Unidade",
    "Empresa",
    "Responsavel",
    "Departamento_Responsavel",
    "Categoria",
    "Subcategoria",
    "Categoria_Causa",
    "Categoria_Resolucao",
    "Categoria_Objeto",
    "Descricao",
    "Origem",
    "Canal",
    "Canal1",
    "Canal_Internet",
    "Criado_Por",
    "Data_Abertura",
    "Data_Fechamento",
    "Data_Limite_Conclusao",
    "Data_Limite_Resolucao",
    "Data_Reportado_Em",
    "Data_Revisao_Inicial_Ate",
    "Data_Revisao_Inicial",
    "Data_Proxima_Resposta_Ate",
    "Dias_Em_Aberto",
    "Tempo_Resolucao_Dias",
    "SLA_Status",
    "Chamado_Aberto",
    "Chamado_Fechado",
    "Telefone",
    "Celular",
    "Analise_Interna",
    "Prestador_Servicos",
    "Arquivo_Origem",
    "Data_Atualizacao_Carga",
    "Chave_GOV",
]


def _remover_acentos(valor: object) -> str:
    texto = "" if valor is None else str(valor)
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def _texto(valor: object) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).replace("\u00a0", " ").strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def _canon(valor: object) -> str:
    return _remover_acentos(_texto(valor)).lower().strip()


def _nome_coluna(valor: object) -> str:
    texto = _remover_acentos(_texto(valor))
    texto = re.sub(r"[^A-Za-z0-9]+", "_", texto).strip("_")
    return texto or "Coluna"


def _deduplicar_colunas(colunas: List[str]) -> List[str]:
    vistas: Dict[str, int] = {}
    saida = []
    for col in colunas:
        base = col or "Coluna"
        if base not in vistas:
            vistas[base] = 1
            saida.append(base)
        else:
            vistas[base] += 1
            saida.append(f"{base}_{vistas[base]}")
    return saida


def _excel_serial_para_datetime(valor: object) -> pd.Timestamp:
    texto = _texto(valor)
    if texto == "":
        return pd.NaT

    try:
        numero = float(texto.replace(",", "."))
        if 1 <= numero <= 90000:
            return pd.Timestamp(datetime(1899, 12, 30) + timedelta(days=numero))
    except Exception:
        pass

    formatos = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]
    texto_limpo = texto.replace("Z", "").split("+")[0].strip()
    for formato in formatos:
        try:
            return pd.Timestamp(datetime.strptime(texto_limpo, formato))
        except Exception:
            continue

    dt = pd.to_datetime(texto_limpo, errors="coerce", dayfirst=True)
    return pd.Timestamp(dt) if pd.notna(dt) else pd.NaT


def _formatar_data(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    if pd.isna(dt):
        return ""
    return pd.Timestamp(dt).strftime("%Y-%m-%d")


def _formatar_datetime(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    if pd.isna(dt):
        return ""
    return pd.Timestamp(dt).strftime("%Y-%m-%d %H:%M:%S")


def _cell_text(cell: ET.Element, shared_strings: List[str]) -> str:
    valor = cell.find(f"{NS_MAIN}v")
    if valor is not None:
        raw = valor.text or ""
        if cell.attrib.get("t") == "s" and raw:
            try:
                return shared_strings[int(raw)]
            except Exception:
                return raw
        return raw

    inline = cell.find(f"{NS_MAIN}is")
    if inline is not None:
        return "".join(t.text or "" for t in inline.iter(f"{NS_MAIN}t"))

    return ""


def _sheet_path_por_nome(zip_ref: zipfile.ZipFile, nome_sheet: Optional[str] = "_DATA_MASTER") -> str:
    workbook = ET.fromstring(zip_ref.read("xl/workbook.xml"))
    rels = ET.fromstring(zip_ref.read("xl/_rels/workbook.xml.rels"))
    mapa_rels = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall(f"{NS_REL}Relationship")}

    sheets = workbook.find(f"{NS_MAIN}sheets")
    if sheets is None:
        raise ValueError("Nenhuma aba encontrada no XLSX.")

    selecionada = None
    for sheet in sheets.findall(f"{NS_MAIN}sheet"):
        nome = sheet.attrib.get("name", "")
        if nome_sheet and nome.lower() == nome_sheet.lower():
            selecionada = sheet
            break
        if selecionada is None:
            selecionada = sheet

    if selecionada is None:
        raise ValueError("Nenhuma aba encontrada no XLSX.")

    rid = selecionada.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    target = mapa_rels.get(rid, "worksheets/sheet1.xml")
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def ler_xlsx_sap(path: Path) -> pd.DataFrame:
    """Lê XLSX exportado do SAP mesmo quando o XML vem com referência de célula inválida.

    O arquivo recebido veio com célula `r="E"` em vez de `E3`.
    O openpyxl, sendo uma criatura literal, quebra. Este leitor percorre as células
    na ordem física do XML e ignora a referência defeituosa.
    """
    with zipfile.ZipFile(path) as zip_ref:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zip_ref.namelist():
            root_ss = ET.fromstring(zip_ref.read("xl/sharedStrings.xml"))
            for item in root_ss.findall(f"{NS_MAIN}si"):
                shared_strings.append("".join(t.text or "" for t in item.iter(f"{NS_MAIN}t")))

        sheet_path = _sheet_path_por_nome(zip_ref)
        root = ET.fromstring(zip_ref.read(sheet_path))
        sheet_data = root.find(f"{NS_MAIN}sheetData")
        if sheet_data is None:
            return pd.DataFrame()

        linhas: List[List[str]] = []
        for row in sheet_data.findall(f"{NS_MAIN}row"):
            valores = [_cell_text(cell, shared_strings) for cell in row.findall(f"{NS_MAIN}c")]
            if any(_texto(v) for v in valores):
                linhas.append(valores)

    if not linhas:
        return pd.DataFrame()

    indice_cabecalho = None
    for idx, linha in enumerate(linhas):
        normalizados = [_canon(v) for v in linha]
        if "protocolo" in normalizados or "numero do chamado" in normalizados or "numero chamado" in normalizados:
            indice_cabecalho = idx
            break

    if indice_cabecalho is None:
        # último recurso: primeira linha com muitas colunas preenchidas
        tamanhos = [(idx, len([v for v in linha if _texto(v)])) for idx, linha in enumerate(linhas)]
        indice_cabecalho = max(tamanhos, key=lambda x: x[1])[0]

    cabecalho = _deduplicar_colunas([_nome_coluna(col) for col in linhas[indice_cabecalho]])
    registros = []
    for linha in linhas[indice_cabecalho + 1 :]:
        if not any(_texto(v) for v in linha):
            continue
        linha = linha + [""] * (len(cabecalho) - len(linha))
        registros.append(dict(zip(cabecalho, linha[: len(cabecalho)])))

    return pd.DataFrame(registros)


def _detectar_encoding(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        return "utf-8-sig"
    try:
        from charset_normalizer import from_bytes

        melhor = from_bytes(raw).best()
        if melhor and melhor.encoding:
            return melhor.encoding
    except Exception:
        pass
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        try:
            raw.decode(enc)
            return enc
        except Exception:
            continue
    return "latin1"


def _detectar_delimitador(texto: str) -> str:
    amostra = texto[:8192]
    try:
        dialect = csv.Sniffer().sniff(amostra, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        contagens = {";": amostra.count(";"), ",": amostra.count(","), "\t": amostra.count("\t"), "|": amostra.count("|")}
        return max(contagens, key=contagens.get) if max(contagens.values()) > 0 else ";"


def ler_csv_generico(path: Path) -> pd.DataFrame:
    encoding = _detectar_encoding(path)
    texto = path.read_text(encoding=encoding, errors="replace")
    sep = _detectar_delimitador(texto)
    try:
        df = pd.read_csv(path, dtype=str, encoding=encoding, sep=sep, engine="python")
    except Exception:
        linhas = list(csv.reader(texto.splitlines(), delimiter=sep))
        if not linhas:
            return pd.DataFrame()
        cabecalho = _deduplicar_colunas([_nome_coluna(c) for c in linhas[0]])
        registros = []
        for linha in linhas[1:]:
            if not any(_texto(v) for v in linha):
                continue
            linha = linha + [""] * (len(cabecalho) - len(linha))
            registros.append(dict(zip(cabecalho, linha[: len(cabecalho)])))
        df = pd.DataFrame(registros)
    df.columns = _deduplicar_colunas([_nome_coluna(c) for c in df.columns])
    return df


def ler_arquivo_gov(path: Path) -> pd.DataFrame:
    sufixo = path.suffix.lower()
    if sufixo == ".xlsx":
        return ler_xlsx_sap(path)
    if sufixo in {".csv", ".txt"}:
        return ler_csv_generico(path)
    return pd.DataFrame()


def _buscar_coluna(df: pd.DataFrame, candidatos: Iterable[str]) -> Optional[str]:
    mapa = {_canon(col): col for col in df.columns}
    for candidato in candidatos:
        chave = _canon(_nome_coluna(candidato)).replace("_", " ")
        # tenta por nome original e por nome normalizado
        for k, col in mapa.items():
            if k == _canon(candidato) or k == chave:
                return col
    # tenta contains, porque relatório SAP adora inventar variação
    for candidato in candidatos:
        alvo = _canon(candidato).replace("_", " ")
        for k, col in mapa.items():
            if alvo and alvo in k.replace("_", " "):
                return col
    return None


def _serie(df: pd.DataFrame, candidatos: Iterable[str]) -> pd.Series:
    col = _buscar_coluna(df, candidatos)
    if col and col in df.columns:
        return df[col].fillna("").astype(str).map(_texto)
    return pd.Series([""] * len(df), index=df.index, dtype=str)


def _serie_data(df: pd.DataFrame, candidatos: Iterable[str]) -> pd.Series:
    return _serie(df, candidatos).map(_formatar_datetime)


def padronizar_status(valor: object) -> str:
    bruto = _texto(valor)
    status = _canon(bruto)
    if not status:
        return ""
    if status in STATUS_CONCLUIDO:
        return "Concluído"
    if status in STATUS_CANCELADO:
        return "Cancelado"
    if status in STATUS_ANDAMENTO:
        return "Em andamento"
    if status in STATUS_PENDENTE:
        return "Pendente"
    if status in STATUS_ABERTO:
        return "Aberto"
    if "conclu" in status or "finaliz" in status or "fech" in status or "resolvid" in status:
        return "Concluído"
    if "cancel" in status:
        return "Cancelado"
    if "andamento" in status or "process" in status or "tratativa" in status:
        return "Em andamento"
    if "pendent" in status or "aguard" in status:
        return "Pendente"
    if "abert" in status or "novo" in status:
        return "Aberto"
    return bruto.title()


def status_macro(status_padronizado: object) -> str:
    status = _canon(status_padronizado)
    if status in {"concluido", "fechado", "finalizado", "resolvido", "cancelado"}:
        return "Fechado"
    if status:
        return "Aberto"
    return ""


def _hash_linha(*partes: object) -> str:
    texto = "|".join(_canon(p) for p in partes)
    return hashlib.md5(texto.encode("utf-8")).hexdigest()


def _calcular_sla(data_limite: object, data_fechamento: object, status_padronizado: object) -> str:
    limite = _excel_serial_para_datetime(data_limite)
    fechamento = _excel_serial_para_datetime(data_fechamento)
    macro = status_macro(status_padronizado)
    hoje = pd.Timestamp.today().normalize()

    if pd.isna(limite):
        return "Sem SLA"
    if pd.notna(fechamento):
        return "No prazo" if fechamento <= limite else "Fora do prazo"
    if macro == "Fechado":
        return "Sem data de fechamento"
    return "Vencido" if hoje > limite.normalize() else "No prazo"


def tratar_dataframe_gov(df: pd.DataFrame, arquivo_origem: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUNAS_RELEVANTES)

    base = df.copy()
    base.columns = _deduplicar_colunas([_nome_coluna(c) for c in base.columns])

    data_abertura = _serie_data(base, ["Criado_em", "Data_Abertura", "Reportado_em", "Data_de_Abertura"])
    data_fechamento = _serie_data(base, ["Resolvido_em", "Data_de_conclusao", "Data_Fechamento", "Data_de_Fechamento"])
    data_limite_conclusao = _serie_data(base, ["Conclusao_ate", "Resolucao_ate", "Data_Limite_Conclusao"])
    status_original = _serie(base, ["Status", "Situacao"])
    status_pad = status_original.map(padronizar_status)

    saida = pd.DataFrame(index=base.index)
    saida["Numero_Chamado"] = _serie(base, ["Numero_Chamado", "Numero_do_Chamado", "Protocolo", "Ticket"])
    saida["Protocolo"] = _serie(base, ["Protocolo", "Ticket", "Numero_Chamado"])
    saida["Prioridade"] = _serie(base, ["Prioridade"])
    saida["Status"] = status_original
    saida["Status_Padronizado"] = status_pad
    saida["Status_Macro"] = status_pad.map(status_macro)
    saida["Cliente"] = _serie(base, ["Cliente", "Solicitante"])
    saida["Empresa"] = _serie(base, ["Empresa"])
    unidade = _serie(base, ["Unidade", "Empresa", "Filial"])
    saida["Unidade"] = unidade
    saida["Responsavel"] = _serie(base, ["Atribuido_a", "Responsavel", "Atribuido", "Analista"])
    saida["Departamento_Responsavel"] = _serie(base, ["Departamento_Responsavel", "Departamento"])
    saida["Categoria"] = _serie(base, ["Categoria_de_servico", "Categoria", "Tipo"])
    saida["Subcategoria"] = _serie(base, ["Categoria_da_ocorrencia", "Subcategoria"])
    saida["Categoria_Causa"] = _serie(base, ["Categoria_da_causa", "Causa"])
    saida["Categoria_Resolucao"] = _serie(base, ["Categoria_de_resolucao", "Resolucao"])
    saida["Categoria_Objeto"] = _serie(base, ["Categoria_do_objeto", "Objeto"])
    saida["Descricao"] = _serie(base, ["Assunto", "Descricao", "Resumo"])
    saida["Origem"] = _serie(base, ["Origem"])
    saida["Canal"] = _serie(base, ["Canal"])
    saida["Canal1"] = _serie(base, ["Canal1"])
    saida["Canal_Internet"] = _serie(base, ["Canal_Internet"])
    saida["Criado_Por"] = _serie(base, ["Criado_por"])
    saida["Data_Abertura"] = data_abertura
    saida["Data_Fechamento"] = data_fechamento
    saida["Data_Limite_Conclusao"] = data_limite_conclusao
    saida["Data_Limite_Resolucao"] = _serie_data(base, ["Resolucao_ate"])
    saida["Data_Reportado_Em"] = _serie_data(base, ["Reportado_em"])
    saida["Data_Revisao_Inicial_Ate"] = _serie_data(base, ["Revisao_inicial_ate"])
    saida["Data_Revisao_Inicial"] = _serie_data(base, ["Data_de_revisao_inicial"])
    saida["Data_Proxima_Resposta_Ate"] = _serie_data(base, ["Proxima_resposta_ate"])
    saida["Telefone"] = _serie(base, ["Telefone"])
    saida["Celular"] = _serie(base, ["Celular"])
    saida["Analise_Interna"] = _serie(base, ["Analise_Interna"])
    saida["Prestador_Servicos"] = _serie(base, ["Prestador_de_servicos", "Prestador_Servicos"])
    saida["Arquivo_Origem"] = arquivo_origem
    saida["Data_Atualizacao_Carga"] = pd.Timestamp.today().strftime("%Y-%m-%d")

    abertura_dt = parse_datas_mistas(saida["Data_Abertura"])
    fechamento_dt = parse_datas_mistas(saida["Data_Fechamento"])
    hoje = pd.Timestamp.today().normalize()

    saida["Tempo_Resolucao_Dias"] = (fechamento_dt - abertura_dt).dt.days
    saida.loc[saida["Tempo_Resolucao_Dias"] < 0, "Tempo_Resolucao_Dias"] = pd.NA

    aberto = saida["Status_Macro"].eq("Aberto")
    saida["Dias_Em_Aberto"] = (hoje - abertura_dt.dt.normalize()).dt.days
    saida.loc[~aberto | abertura_dt.isna(), "Dias_Em_Aberto"] = pd.NA
    saida.loc[saida["Dias_Em_Aberto"] < 0, "Dias_Em_Aberto"] = pd.NA

    saida["SLA_Status"] = [
        _calcular_sla(limite, fechamento, status)
        for limite, fechamento, status in zip(saida["Data_Limite_Conclusao"], saida["Data_Fechamento"], saida["Status_Padronizado"])
    ]
    saida["Chamado_Aberto"] = saida["Status_Macro"].eq("Aberto").astype(int)
    saida["Chamado_Fechado"] = saida["Status_Macro"].eq("Fechado").astype(int)

    chaves = []
    for _, row in saida.iterrows():
        numero = _texto(row.get("Numero_Chamado"))
        if numero:
            chaves.append(numero)
        else:
            chaves.append(
                _hash_linha(
                    row.get("Data_Abertura"),
                    row.get("Unidade"),
                    row.get("Categoria"),
                    row.get("Descricao"),
                    row.get("Cliente"),
                )
            )
    saida["Chave_GOV"] = chaves

    saida["Data"] = saida["Data_Abertura"]
    saida = adicionar_colunas_calendario(saida, "Data")

    for col in COLUNAS_RELEVANTES:
        if col not in saida.columns:
            saida[col] = pd.NA

    return saida[COLUNAS_RELEVANTES].copy()


def listar_arquivos_entrada(pasta_entrada: Path) -> List[Path]:
    if not pasta_entrada.exists():
        return []
    arquivos = []
    for path in sorted(pasta_entrada.rglob("*")):
        if path.is_file() and not path.name.startswith("~$") and path.suffix.lower() in ARQUIVOS_VALIDOS:
            arquivos.append(path)
    return arquivos


def carregar_gov_chamados(pasta_entrada: Path) -> Tuple[pd.DataFrame, List[Dict[str, object]]]:
    arquivos = listar_arquivos_entrada(pasta_entrada)
    bases: List[pd.DataFrame] = []
    logs_arquivos: List[Dict[str, object]] = []

    for arquivo in arquivos:
        registro_log: Dict[str, object] = {
            "Arquivo_Origem": arquivo.name,
            "Caminho": str(arquivo),
            "Status_Processamento": "Processado",
            "Motivo": "",
            "Linhas_Lidas": 0,
            "Linhas_Tratadas": 0,
        }
        try:
            bruto = ler_arquivo_gov(arquivo)
            registro_log["Linhas_Lidas"] = len(bruto)
            if bruto.empty:
                registro_log["Status_Processamento"] = "Ignorado"
                registro_log["Motivo"] = "Arquivo vazio ou sem linhas úteis"
                logs_arquivos.append(registro_log)
                continue

            tratado = tratar_dataframe_gov(bruto, arquivo.name)
            registro_log["Linhas_Tratadas"] = len(tratado)
            if tratado.empty:
                registro_log["Status_Processamento"] = "Ignorado"
                registro_log["Motivo"] = "Nenhum registro tratado após padronização"
            else:
                bases.append(tratado)
        except Exception as exc:
            registro_log["Status_Processamento"] = "Erro"
            registro_log["Motivo"] = str(exc)
        logs_arquivos.append(registro_log)

    if not bases:
        return pd.DataFrame(columns=COLUNAS_RELEVANTES), logs_arquivos

    final = pd.concat(bases, ignore_index=True, sort=False)
    return final, logs_arquivos


def criar_dimensoes_gov(df: pd.DataFrame, pasta_saida: Path) -> None:
    if df is None or df.empty:
        return

    dimensoes = {
        "dim_status_chamados.csv": ["Status_Padronizado", "Status_Macro"],
        "dim_unidades_chamados.csv": ["Unidade", "Empresa"],
        "dim_responsaveis_chamados.csv": ["Responsavel", "Departamento_Responsavel"],
        "dim_categorias_chamados.csv": ["Categoria", "Subcategoria", "Categoria_Causa", "Categoria_Resolucao", "Categoria_Objeto"],
    }

    for nome_arquivo, colunas in dimensoes.items():
        cols = [c for c in colunas if c in df.columns]
        if not cols:
            continue
        dim = df[cols].copy()
        for col in cols:
            dim[col] = dim[col].fillna("").astype(str).map(_texto)
        dim = dim.drop_duplicates()
        if cols:
            mascara_vazia = dim[cols].apply(lambda s: s.astype(str).str.strip().eq("")).all(axis=1)
            dim = dim.loc[~mascara_vazia].copy()
        dim = dim.sort_values(cols).reset_index(drop=True)
        salvar_csv(dim, pasta_saida, nome_arquivo)


def resumo_qualidade(df_bruto: pd.DataFrame, df_final: pd.DataFrame, logs_arquivos: List[Dict[str, object]]) -> Dict[str, object]:
    datas = parse_datas_mistas(df_final["Data_Abertura"]) if "Data_Abertura" in df_final.columns else pd.Series(dtype="datetime64[ns]")
    return {
        "Arquivos_Lidos": sum(1 for item in logs_arquivos if item.get("Status_Processamento") == "Processado"),
        "Arquivos_Ignorados_Ou_Erro": sum(1 for item in logs_arquivos if item.get("Status_Processamento") != "Processado"),
        "Linhas_Brutas": int(sum(int(item.get("Linhas_Lidas") or 0) for item in logs_arquivos)),
        "Linhas_Tratadas_Carga": int(len(df_bruto)),
        "Linhas_Finais": int(len(df_final)),
        "Registros_Data_Abertura_Invalida": int(parse_datas_mistas(df_bruto["Data_Abertura"]).isna().sum()) if "Data_Abertura" in df_bruto.columns and len(df_bruto) else 0,
        "Registros_Sem_Status": int(df_bruto["Status_Padronizado"].fillna("").astype(str).str.strip().eq("").sum()) if "Status_Padronizado" in df_bruto.columns and len(df_bruto) else 0,
        "Registros_Sem_Responsavel": int(df_bruto["Responsavel"].fillna("").astype(str).str.strip().eq("").sum()) if "Responsavel" in df_bruto.columns and len(df_bruto) else 0,
        "Periodo_Min": datas.min().strftime("%Y-%m-%d") if len(datas.dropna()) else "",
        "Periodo_Max": datas.max().strftime("%Y-%m-%d") if len(datas.dropna()) else "",
    }


def processar_gov_chamados(
    pasta_entrada: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    novo, logs_arquivos = carregar_gov_chamados(Path(pasta_entrada))

    final, log = salvar_tabela_incremental(
        NOME_TABELA,
        novo,
        Path(pasta_saida),
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data_Abertura", "Numero_Chamado", "Arquivo_Origem"],
    )

    criar_dimensoes_gov(final, Path(pasta_saida))

    resumo = resumo_qualidade(novo, final, logs_arquivos)
    log.update(resumo)
    log["Chave"] = "Chave_GOV (Numero_Chamado/Protocolo; fallback hash de Data_Abertura+Unidade+Categoria+Descricao+Cliente)"
    log["Observacao"] = "ETL GOV Chamados integrado ao RPA SSRS."

    salvar_log([log], Path(pasta_logs), "log_f_gov_chamados_tratado.csv")
    if logs_arquivos:
        salvar_log(logs_arquivos, Path(pasta_logs), "log_gov_chamados_arquivos.csv")

    return final, log


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gera f_gov_chamados_tratado.csv")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()

    processar_gov_chamados(
        Path(args.entrada),
        Path(args.saida),
        Path(args.logs or Path(args.saida).parent / "LOGS"),
        substituir=args.substituir,
        reprocessar_tudo=args.reprocessar_tudo,
    )
