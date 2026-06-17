from __future__ import annotations

import csv
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

NOME_SAIDA = "f_reclamacoes_sap_tratado.csv"
DELIMITADOR_SAIDA = ";"
ENCODING_SAIDA = "utf-8-sig"

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

COLUNAS_DATAS = {
    "conclusao_ate",
    "data_de_conclusao",
    "reportado_em",
    "criado_em",
    "revisao_inicial_ate",
    "data_de_revisao_inicial",
    "proxima_resposta_ate",
    "resolucao_ate",
    "resolvido_em",
}

COLUNAS_FINAIS = [
    "chave_protocolo",
    "protocolo",
    "prioridade",
    "conclusao_ate",
    "data_de_conclusao",
    "categoria_de_servico",
    "categoria_da_ocorrencia",
    "categoria_da_causa",
    "categoria_de_resolucao",
    "categoria_do_objeto",
    "assunto",
    "status",
    "status_macro",
    "cliente",
    "departamento_responsavel",
    "origem",
    "canal",
    "canal1",
    "canal_internet",
    "reportado_em",
    "criado_em",
    "data_criacao",
    "ano_mes_criacao",
    "semana_inicio_criacao",
    "criado_por",
    "atribuido_a",
    "revisao_inicial_ate",
    "data_de_revisao_inicial",
    "proxima_resposta_ate",
    "resolucao_ate",
    "resolvido_em",
    "tempo_ate_revisao_horas",
    "tempo_resolucao_horas",
    "dias_em_aberto",
    "situacao_sla_revisao_inicial",
    "situacao_sla_resolucao",
    "prestador_de_servicos",
    "empresa",
    "telefone",
    "telefone_limpo",
    "celular",
    "celular_limpo",
    "analise_interna",
    "arquivo_origem",
    "data_processamento",
]

STATUS_FECHADO = {"fechado", "concluido", "concluído", "encerrado pela area", "encerrado pela área"}
STATUS_ABERTO = {"em processamento", "aguardando analise interna", "aguardando análise interna", "acao do cliente", "ação do cliente"}


def _normalizar_texto(valor: object) -> str:
    if valor is None:
        return ""
    texto = str(valor).replace("\xa0", " ").strip()
    texto = re.sub(r"\s+", " ", texto)
    if texto.lower() in {"nan", "none", "null"}:
        return ""
    return texto


def _remover_acentos(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in texto if not unicodedata.combining(ch))


def _normalizar_nome_coluna(coluna: object) -> str:
    texto = _normalizar_texto(coluna).lower()
    texto = _remover_acentos(texto)
    texto = re.sub(r"[^a-z0-9]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")
    ajustes = {
        "conclusao_ate": "conclusao_ate",
        "data_de_conclusao": "data_de_conclusao",
        "categoria_de_servico": "categoria_de_servico",
        "categoria_da_ocorrencia": "categoria_da_ocorrencia",
        "categoria_da_causa": "categoria_da_causa",
        "categoria_de_resolucao": "categoria_de_resolucao",
        "categoria_do_objeto": "categoria_do_objeto",
        "departamento_responsavel": "departamento_responsavel",
        "canal_1": "canal1",
        "canal1": "canal1",
        "canal_internet": "canal_internet",
        "reportado_em": "reportado_em",
        "criado_em": "criado_em",
        "criado_por": "criado_por",
        "atribuido_a": "atribuido_a",
        "revisao_inicial_ate": "revisao_inicial_ate",
        "data_de_revisao_inicial": "data_de_revisao_inicial",
        "proxima_resposta_ate": "proxima_resposta_ate",
        "resolucao_ate": "resolucao_ate",
        "resolvido_em": "resolvido_em",
        "prestador_de_servicos": "prestador_de_servicos",
        "analise_interna": "analise_interna",
    }
    return ajustes.get(texto, texto)


def _excel_serial_para_datetime(valor: object) -> Optional[datetime]:
    texto = _normalizar_texto(valor)
    if not texto:
        return None

    # SAP costuma exportar datas do Excel como número serial, inclusive em XLSX XML mapeado.
    try:
        numero = float(texto.replace(",", "."))
        if 1 <= numero <= 90000:
            return datetime(1899, 12, 30) + timedelta(days=numero)
    except ValueError:
        pass

    formatos = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d",
    ]
    texto_limpo = texto.replace("Z", "").split("+")[0].strip()
    for formato in formatos:
        try:
            return datetime.strptime(texto_limpo, formato)
        except ValueError:
            continue
    return None


def _formatar_datetime(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else _normalizar_texto(valor)


def _formatar_data(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    return dt.strftime("%Y-%m-%d") if dt else ""


def _inicio_semana(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    if not dt:
        return ""
    inicio = dt.date() - timedelta(days=dt.weekday())
    return inicio.strftime("%Y-%m-%d")


def _ano_mes(valor: object) -> str:
    dt = _excel_serial_para_datetime(valor)
    return dt.strftime("%Y-%m") if dt else ""


def _limpar_numero(valor: object) -> str:
    return re.sub(r"\D+", "", _normalizar_texto(valor))


def _horas_entre(inicio: object, fim: object) -> str:
    dt_inicio = _excel_serial_para_datetime(inicio)
    dt_fim = _excel_serial_para_datetime(fim)
    if not dt_inicio or not dt_fim:
        return ""
    segundos = max((dt_fim - dt_inicio).total_seconds(), 0)
    return f"{(segundos / 3600):.2f}"


def _dias_em_aberto(criado_em: object, resolvido_em: object, status: object) -> str:
    status_norm = _remover_acentos(_normalizar_texto(status).lower())
    if status_norm in {_remover_acentos(s) for s in STATUS_FECHADO}:
        return ""
    dt_criado = _excel_serial_para_datetime(criado_em)
    if not dt_criado:
        return ""
    return str(max((datetime.now() - dt_criado).days, 0))


def _status_macro(status: object) -> str:
    status_txt = _normalizar_texto(status)
    status_norm = _remover_acentos(status_txt.lower())
    fechados = {_remover_acentos(s) for s in STATUS_FECHADO}
    abertos = {_remover_acentos(s) for s in STATUS_ABERTO}
    if status_norm in fechados:
        return "Fechado"
    if status_norm in abertos:
        return "Aberto"
    return "Outros" if status_txt else ""


def _situacao_sla(data_realizada: object, data_limite: object, status: object) -> str:
    limite = _excel_serial_para_datetime(data_limite)
    realizada = _excel_serial_para_datetime(data_realizada)
    if not limite:
        return ""
    if realizada:
        return "No prazo" if realizada <= limite else "Fora do prazo"

    macro = _status_macro(status)
    if macro == "Fechado":
        return "Sem data realizada"
    return "Vencido" if datetime.now() > limite else "No prazo"


def _cell_text(cell: ET.Element, shared_strings: List[str]) -> str:
    valor = cell.find(f"{NS_MAIN}v")
    if valor is not None:
        raw = valor.text or ""
        if cell.attrib.get("t") == "s" and raw:
            return shared_strings[int(raw)]
        return raw

    inline = cell.find(f"{NS_MAIN}is")
    if inline is not None:
        return "".join(t.text or "" for t in inline.iter(f"{NS_MAIN}t"))
    return ""


def _sheet_path_por_nome(zip_ref: zipfile.ZipFile, nome_sheet: Optional[str] = None) -> str:
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


def _ler_xlsx_sap(path: Path) -> List[Dict[str, str]]:
    with zipfile.ZipFile(path) as zip_ref:
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in zip_ref.namelist():
            root_ss = ET.fromstring(zip_ref.read("xl/sharedStrings.xml"))
            for item in root_ss.findall(f"{NS_MAIN}si"):
                shared_strings.append("".join(t.text or "" for t in item.iter(f"{NS_MAIN}t")))

        sheet_path = _sheet_path_por_nome(zip_ref, "_DATA_MASTER")
        root = ET.fromstring(zip_ref.read(sheet_path))
        sheet_data = root.find(f"{NS_MAIN}sheetData")
        if sheet_data is None:
            return []

        linhas: List[List[str]] = []
        for row in sheet_data.findall(f"{NS_MAIN}row"):
            valores = [_cell_text(cell, shared_strings) for cell in row.findall(f"{NS_MAIN}c")]
            linhas.append(valores)

    indice_cabecalho = None
    for idx, linha in enumerate(linhas):
        normalizados = [_normalizar_texto(v).lower() for v in linha]
        if "protocolo" in normalizados:
            indice_cabecalho = idx
            break

    if indice_cabecalho is None:
        raise ValueError(f"Cabeçalho com a coluna 'Protocolo' não encontrado no arquivo {path.name}.")

    cabecalho = [_normalizar_nome_coluna(col) for col in linhas[indice_cabecalho]]
    registros: List[Dict[str, str]] = []
    for linha in linhas[indice_cabecalho + 1 :]:
        if not any(_normalizar_texto(v) for v in linha):
            continue
        linha = linha + [""] * (len(cabecalho) - len(linha))
        registro = dict(zip(cabecalho, linha[: len(cabecalho)]))
        registros.append(registro)
    return registros


def _detectar_delimitador(texto: str) -> str:
    amostra = texto[:4096]
    try:
        dialect = csv.Sniffer().sniff(amostra, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ";" if texto.count(";") >= texto.count(",") else ","


def _ler_csv_sap(path: Path) -> List[Dict[str, str]]:
    texto = path.read_text(encoding="utf-8-sig", errors="replace")
    delimitador = _detectar_delimitador(texto)
    linhas = list(csv.reader(texto.splitlines(), delimiter=delimitador))

    indice_cabecalho = None
    for idx, linha in enumerate(linhas):
        normalizados = [_normalizar_texto(v).lower() for v in linha]
        if "protocolo" in normalizados:
            indice_cabecalho = idx
            break

    if indice_cabecalho is None:
        raise ValueError(f"Cabeçalho com a coluna 'Protocolo' não encontrado no arquivo {path.name}.")

    cabecalho = [_normalizar_nome_coluna(col) for col in linhas[indice_cabecalho]]
    registros: List[Dict[str, str]] = []
    for linha in linhas[indice_cabecalho + 1 :]:
        if not any(_normalizar_texto(v) for v in linha):
            continue
        linha = linha + [""] * (len(cabecalho) - len(linha))
        registros.append(dict(zip(cabecalho, linha[: len(cabecalho)])))
    return registros


def _ler_arquivo_sap(path: Path) -> List[Dict[str, str]]:
    sufixo = path.suffix.lower()
    if sufixo == ".xlsx":
        return _ler_xlsx_sap(path)
    if sufixo in {".csv", ".txt"}:
        return _ler_csv_sap(path)
    return []


def _tratar_registro(registro: Dict[str, str], arquivo_origem: str, data_processamento: str) -> Dict[str, str]:
    tratado: Dict[str, str] = {}
    for chave, valor in registro.items():
        nome = _normalizar_nome_coluna(chave)
        tratado[nome] = _normalizar_texto(valor)

    for coluna in COLUNAS_DATAS:
        tratado[coluna] = _formatar_datetime(tratado.get(coluna, ""))

    protocolo = _normalizar_texto(tratado.get("protocolo", ""))
    protocolo = re.sub(r"\.0$", "", protocolo)
    tratado["protocolo"] = protocolo
    tratado["chave_protocolo"] = protocolo

    criado_em = tratado.get("criado_em", "")
    resolvido_em = tratado.get("resolvido_em", "")
    data_revisao = tratado.get("data_de_revisao_inicial", "")
    status = tratado.get("status", "")

    tratado["data_criacao"] = _formatar_data(criado_em)
    tratado["ano_mes_criacao"] = _ano_mes(criado_em)
    tratado["semana_inicio_criacao"] = _inicio_semana(criado_em)
    tratado["status_macro"] = _status_macro(status)
    tratado["telefone_limpo"] = _limpar_numero(tratado.get("telefone", ""))
    tratado["celular_limpo"] = _limpar_numero(tratado.get("celular", ""))
    tratado["tempo_ate_revisao_horas"] = _horas_entre(criado_em, data_revisao)
    tratado["tempo_resolucao_horas"] = _horas_entre(criado_em, resolvido_em)
    tratado["dias_em_aberto"] = _dias_em_aberto(criado_em, resolvido_em, status)
    tratado["situacao_sla_revisao_inicial"] = _situacao_sla(data_revisao, tratado.get("revisao_inicial_ate", ""), status)
    tratado["situacao_sla_resolucao"] = _situacao_sla(resolvido_em, tratado.get("resolucao_ate", ""), status)
    tratado["arquivo_origem"] = arquivo_origem
    tratado["data_processamento"] = data_processamento

    return {coluna: tratado.get(coluna, "") for coluna in COLUNAS_FINAIS}


def _listar_arquivos(entrada: Path) -> List[Path]:
    if not entrada.exists():
        entrada.mkdir(parents=True, exist_ok=True)
        return []
    permitidos = {".xlsx", ".csv", ".txt"}
    return sorted(
        [p for p in entrada.rglob("*") if p.is_file() and p.suffix.lower() in permitidos and not p.name.startswith("~$")],
        key=lambda p: (p.stat().st_mtime, p.name.lower()),
    )


def _ler_saida_existente(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding=ENCODING_SAIDA, newline="") as f:
        leitor = csv.DictReader(f, delimiter=DELIMITADOR_SAIDA)
        return [{coluna: linha.get(coluna, "") for coluna in COLUNAS_FINAIS} for linha in leitor]


def _salvar_csv(path: Path, linhas: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=ENCODING_SAIDA, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUNAS_FINAIS, delimiter=DELIMITADOR_SAIDA, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(linhas)


def processar_reclamacoes_sap(
    entrada: Path,
    saida: Path,
    logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> Tuple[Path, dict]:
    """
    Trata arquivos de reclamações/tickets SAP exportados em .xlsx, .csv ou .txt.

    Saída padrão:
        <saida>/f_reclamacoes_sap_tratado.csv

    Incremental:
        - chave: protocolo
        - substituir=False: mantém protocolo já existente e inclui só novos
        - substituir=True: substitui protocolos existentes pelos registros novos
        - reprocessar_tudo=True: recria a saída somente com os arquivos da entrada
    """
    entrada = Path(entrada)
    saida = Path(saida)
    logs = Path(logs)
    saida.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    arquivo_saida = saida / NOME_SAIDA
    arquivos = _listar_arquivos(entrada)
    data_processamento = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    registros_novos: List[Dict[str, str]] = []
    erros: List[str] = []

    print("\n=== CARGA RECLAMAÇÕES SAP ===")
    if not arquivos:
        print(f"AVISO - nenhum arquivo .xlsx/.csv encontrado em: {entrada}")

    for arquivo in arquivos:
        try:
            print(f"Processando Reclamações SAP: {arquivo.name}")
            registros = _ler_arquivo_sap(arquivo)
            for registro in registros:
                tratado = _tratar_registro(registro, arquivo.name, data_processamento)
                if tratado["protocolo"]:
                    registros_novos.append(tratado)
        except Exception as exc:  # mantém a carga viva e registra o problema
            erros.append(f"{arquivo.name}: {exc}")
            print(f"ERRO - {arquivo.name}: {exc}")

    existentes = [] if reprocessar_tudo else _ler_saida_existente(arquivo_saida)
    por_protocolo: Dict[str, Dict[str, str]] = {linha.get("chave_protocolo", ""): linha for linha in existentes if linha.get("chave_protocolo")}

    lidos = len(registros_novos)
    incluidos = 0
    substituidos = 0
    ignorados = 0

    for linha in registros_novos:
        chave = linha["chave_protocolo"]
        if chave in por_protocolo:
            if substituir or reprocessar_tudo:
                por_protocolo[chave] = linha
                substituidos += 1
            else:
                ignorados += 1
        else:
            por_protocolo[chave] = linha
            incluidos += 1

    linhas_finais = sorted(
        por_protocolo.values(),
        key=lambda x: (x.get("criado_em", ""), x.get("protocolo", "")),
    )
    _salvar_csv(arquivo_saida, linhas_finais)

    status = "erro_parcial" if erros else "ok"
    log = {
        "carga": "reclamacoes_sap",
        "status": status,
        "arquivos_processados": len(arquivos),
        "linhas_lidas": lidos,
        "linhas_incluidas": incluidos,
        "linhas_substituidas": substituidos,
        "linhas_ignoradas": ignorados,
        "linhas_saida": len(linhas_finais),
        "arquivo_saida": str(arquivo_saida),
        "erros": " | ".join(erros),
        "data_processamento": data_processamento,
    }

    print(f"OK - {NOME_SAIDA} ({len(linhas_finais)} linhas finais)")
    if erros:
        print("Carga concluída com erro parcial. Porque aparentemente até exportação do SAP gosta de testar limites.")
    return arquivo_saida, log


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Trata Reclamações SAP e gera CSV final.")
    parser.add_argument("--entrada", default="entrada_sap", help="Pasta com arquivos .xlsx/.csv do SAP")
    parser.add_argument("--saida", default="saida", help="Pasta de saída")
    parser.add_argument("--logs", default="LOGS", help="Pasta de logs")
    parser.add_argument("--substituir", action="store_true", help="Substitui protocolos existentes")
    parser.add_argument("--reprocessar-tudo", action="store_true", help="Recria o CSV do zero")
    args = parser.parse_args()

    processar_reclamacoes_sap(
        Path(args.entrada),
        Path(args.saida),
        Path(args.logs),
        substituir=args.substituir,
        reprocessar_tudo=args.reprocessar_tudo,
    )
