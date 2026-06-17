from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import List, Optional
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from common import normalizar_coluna, salvar_log, salvar_tabela_incremental

COLUNAS_TECNICAS_FSR = {
    "Arquivo_Origem", "Data_Atualizacao_Carga", "Chave_FSR", "Ano", "Mes", "Dia", "Ano_Mes",
    "Data_Criacao", "Data_Conclusao", "Fechado",
}


def reparar_xlsx_sap_referencia_celula(caminho: Path) -> Optional[Path]:
    """
    Corrige temporariamente XLSX do SAP com referência de célula inválida, por exemplo r="E".
    O arquivo original não é alterado. Porque aparentemente arquivo corporativo agora vem com pegadinha embutida.
    """
    if caminho.suffix.lower() != ".xlsx":
        return None

    temporario = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name)
    houve_correcao = False

    try:
        with ZipFile(caminho, "r") as entrada, ZipFile(temporario, "w", ZIP_DEFLATED) as saida:
            for item in entrada.infolist():
                dados = entrada.read(item.filename)
                if item.filename.startswith("xl/worksheets/sheet") and item.filename.endswith(".xml"):
                    texto = dados.decode("utf-8", errors="replace")

                    def corrigir_linha(match):
                        numero_linha = match.group(1)
                        xml_linha = match.group(0)

                        def corrigir_celula(cell_match):
                            inicio = cell_match.group(1)
                            referencia = cell_match.group(2)
                            fim = cell_match.group(3)
                            if re.fullmatch(r"[A-Z]+", referencia):
                                return f"{inicio}{referencia}{numero_linha}{fim}"
                            return cell_match.group(0)

                        return re.sub(r'(<c\b[^>]*\br=")([A-Z]+)(")', corrigir_celula, xml_linha)

                    texto_corrigido = re.sub(r'<row\b[^>]*\br="(\d+)"[\s\S]*?</row>', corrigir_linha, texto)
                    if texto_corrigido != texto:
                        houve_correcao = True
                        dados = texto_corrigido.encode("utf-8")
                saida.writestr(item, dados)

        if houve_correcao:
            return temporario
        temporario.unlink(missing_ok=True)
        return None
    except Exception:
        temporario.unlink(missing_ok=True)
        return None


def ler_excel_fsr_com_fallback(caminho: Path) -> pd.DataFrame:
    tentativas = [
        {"sheet_name": "_DATA_MASTER", "header": 4, "dtype": str},
        {"header": 4, "dtype": str},
        {"dtype": str},
    ]
    reparado = reparar_xlsx_sap_referencia_celula(caminho)
    caminhos = [p for p in [reparado, caminho] if p is not None]
    erros = []
    try:
        for caminho_leitura in caminhos:
            for kwargs in tentativas:
                try:
                    return pd.read_excel(caminho_leitura, **kwargs)
                except Exception as e:
                    erros.append(f"{caminho_leitura.name} | {kwargs}: {e}")
        raise RuntimeError("Não foi possível ler o Excel FSR. Tentativas:\n- " + "\n- ".join(erros[-10:]))
    finally:
        if reparado is not None:
            reparado.unlink(missing_ok=True)


def ler_arquivo_fsr(caminho: Path) -> pd.DataFrame:
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        df = ler_excel_fsr_com_fallback(caminho)
    elif caminho.suffix.lower() == ".csv":
        df = pd.read_csv(caminho, dtype=str, sep=None, engine="python")
    else:
        raise ValueError(f"Formato não suportado para FSR: {caminho.suffix}")
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def localizar_arquivos_fsr(pasta_entrada_fsr: Path) -> List[Path]:
    arquivos = []
    for ext in ["*.xlsx", "*.xls", "*.csv"]:
        arquivos.extend(pasta_entrada_fsr.glob(ext))
    arquivos = [a for a in arquivos if not a.name.startswith("~$") and a.name.lower() != "f_fsr_tratado.csv"]
    if not arquivos:
        raise FileNotFoundError(f"Nenhum arquivo FSR encontrado em {pasta_entrada_fsr}")
    return sorted(arquivos)


def achar_coluna(df: pd.DataFrame, candidatos: List[str]) -> str:
    mapa = {normalizar_coluna(c): c for c in df.columns}
    for candidato in candidatos:
        chave = normalizar_coluna(candidato)
        if chave in mapa:
            return mapa[chave]
    raise ValueError(f"Coluna obrigatória não encontrada. Candidatos: {candidatos}")


def converter_data_fsr(serie: pd.Series) -> pd.Series:
    texto = serie.astype(str).str.strip()
    resultado = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    mascara_iso = texto.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", na=False)
    if mascara_iso.any():
        resultado.loc[mascara_iso] = pd.to_datetime(texto.loc[mascara_iso], errors="coerce", dayfirst=False)
    if (~mascara_iso).any():
        resultado.loc[~mascara_iso] = pd.to_datetime(texto.loc[~mascara_iso], errors="coerce", dayfirst=True)
    return resultado


def gerar_chave_fsr(df: pd.DataFrame) -> pd.Series:
    candidatos = [
        "ID", "Id", "ID do caso", "ID Caso", "Número", "Numero", "Número do chamado", "Nº do chamado",
        "Chamado", "Protocolo", "Ticket", "Código", "Codigo",
    ]
    existentes = [col for col in candidatos if col in df.columns]
    if existentes:
        base = df[existentes].fillna("").astype(str).agg("|".join, axis=1)
    else:
        colunas_base = [col for col in df.columns if col not in COLUNAS_TECNICAS_FSR]
        base = df[colunas_base].fillna("").astype(str).agg("|".join, axis=1)
    return base.apply(lambda texto: hashlib.md5(texto.encode("utf-8", errors="ignore")).hexdigest())


def tratar_fsr(df: pd.DataFrame, arquivo_origem: str) -> pd.DataFrame:
    df = df.copy()
    col_criado = achar_coluna(df, ["Criado em", "Criado", "Data de criação", "Data Criacao", "Data Criação"])
    col_conclusao = achar_coluna(df, ["Data de conclusão", "Data conclusao", "Data Conclusão", "Concluído em", "Concluido em"])

    df[col_criado] = converter_data_fsr(df[col_criado])
    df[col_conclusao] = converter_data_fsr(df[col_conclusao])

    if col_criado != "Criado em":
        df["Criado em"] = df[col_criado]
    if col_conclusao != "Data de conclusão":
        df["Data de conclusão"] = df[col_conclusao]

    df["Data_Criacao"] = pd.to_datetime(df["Criado em"], errors="coerce").dt.date
    df["Data_Conclusao"] = pd.to_datetime(df["Data de conclusão"], errors="coerce").dt.date
    df["Fechado"] = df.apply(
        lambda linha: "Sim"
        if pd.notna(linha["Data_Criacao"]) and pd.notna(linha["Data_Conclusao"]) and linha["Data_Criacao"] == linha["Data_Conclusao"]
        else "Não",
        axis=1,
    )
    criado_dt = pd.to_datetime(df["Criado em"], errors="coerce")
    df["Ano"] = criado_dt.dt.year
    df["Mes"] = criado_dt.dt.month
    df["Dia"] = criado_dt.dt.day
    df["Ano_Mes"] = criado_dt.dt.strftime("%Y-%m")
    df["Arquivo_Origem"] = arquivo_origem
    df["Data_Atualizacao_Carga"] = pd.Timestamp.today().normalize()
    df["Chave_FSR"] = gerar_chave_fsr(df)
    return df


def carregar_fsr(pasta_entrada_fsr: Path) -> pd.DataFrame:
    bases = []
    for arquivo in localizar_arquivos_fsr(pasta_entrada_fsr):
        print(f"Processando FSR: {arquivo.name}")
        df = ler_arquivo_fsr(arquivo)
        bases.append(tratar_fsr(df, arquivo.name))
    return pd.concat(bases, ignore_index=True, sort=False).drop_duplicates() if bases else pd.DataFrame()


def processar_fsr(
    pasta_entrada_fsr: Path,
    pasta_saida: Path,
    pasta_logs: Path,
    substituir: bool = False,
    reprocessar_tudo: bool = False,
) -> pd.DataFrame:
    novo = carregar_fsr(pasta_entrada_fsr)
    final, log = salvar_tabela_incremental(
        "f_fsr_tratado",
        novo,
        pasta_saida,
        substituir_chaves_existentes=substituir,
        reprocessar_tudo=reprocessar_tudo,
        ordenar_por=["Data_Criacao", "Arquivo_Origem"],
    )
    salvar_log([log], pasta_logs, "log_f_fsr_tratado.csv")
    return final, log


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera f_fsr_tratado.csv")
    parser.add_argument("--entrada-fsr", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true")
    parser.add_argument("--reprocessar-tudo", action="store_true")
    args = parser.parse_args()
    processar_fsr(Path(args.entrada_fsr), Path(args.saida), Path(args.logs or Path(args.saida).parent / "LOGS"), args.substituir, args.reprocessar_tudo)
