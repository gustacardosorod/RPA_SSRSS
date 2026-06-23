from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


REQUIRED_FILES: Dict[str, str] = {
    "volume": "Volume 4 - Daily.csv",
    "agent": "Agent - Contact Handling Time 4 - Daily.csv",
    "css_agent": "Script Result 5 - Agent Volume.csv",
    "css_queue": "Script Result 3 - Queue Volume per Day.csv",
}

# Saídas que fazem sentido subir para o banco para consumo no Power BI.
# O script legado gera f_gente_contact_*; aqui criamos alias f_agent_contact_* também.
TABELAS_BI: Dict[str, str] = {
    "dim_atendentes.csv": "dim_atendentes",
    "f_agent_contact_diario.csv": "f_agent_contact_diario",
    "f_agent_contact_fila_diario.csv": "f_agent_contact_fila_diario",
    "f_gente_contact_diario.csv": "f_gente_contact_diario",
    "f_gente_contact_fila_diario.csv": "f_gente_contact_fila_diario",
    "f_volume_geral_diario.csv": "f_volume_geral_diario",
    "f_volume_fila_diario.csv": "f_volume_fila_diario",
    "f_css_atendente.csv": "f_css_atendente",
    "f_css_detalhado.csv": "f_css_detalhado",
    "f_css_periodo_atendente.csv": "f_css_periodo_atendente",
    "f_css_periodo_geral.csv": "f_css_periodo_geral",
    "f_css_fila_detalhado_diario.csv": "f_css_fila_detalhado_diario",
    "f_css_fila_diario.csv": "f_css_fila_diario",
    "f_css_geral_diario.csv": "f_css_geral_diario",
    "f_indicadores_agentes_diario.csv": "f_indicadores_agentes_diario",
    "f_indicadores_agentes_periodo.csv": "f_indicadores_agentes_periodo",
    "f_indicadores_gerais.csv": "f_indicadores_gerais",
    "f_indicadores_gerais_periodo.csv": "f_indicadores_gerais_periodo",
}


class RPAError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def read_csv_rows(path: Path) -> List[List[str]]:
    for enc in ("utf-8-sig", "utf-8", "latin1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                amostra = f.read(8192)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(amostra, delimiters=",;\t|")
                except Exception:
                    dialect = csv.excel
                return list(csv.reader(f, dialect))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, f"Não foi possível ler {path}")




def _texto_relatorio(linhas: List[List[str]], limite: int = 30) -> str:
    return "\n".join(" | ".join(map(str, linha)) for linha in linhas[:limite])


def extrair_valor_parametro_linhas(linhas: List[List[str]], nome_parametro: str) -> str:
    """Extrai o valor de um parâmetro do cabeçalho do SSRS.

    Exemplo esperado dentro do CSV:
    - Dias:    2026-06-22
    - Dias: All
    """
    texto = _texto_relatorio(linhas, limite=30)
    nome = re.escape(nome_parametro)
    # Captura até quebra de linha ou até outro parâmetro iniciado por "- X:".
    m = re.search(rf"-\s*{nome}\s*:\s*(.*?)(?=\n\s*-\s*[A-Za-zÀ-ÿ0-9_ ]+\s*:|\Z)", texto, flags=re.I | re.S)
    if not m:
        return ""
    valor = m.group(1)
    valor = valor.replace(" | ", " ").replace("\r", " ").replace("\n", " ")
    valor = re.sub(r"\s+", " ", valor).strip().strip('"').strip()
    return valor


def extrair_dias_parametro_arquivo(arquivo: Path) -> Dict[str, object]:
    """Lê o parâmetro Dias/Day do relatório e retorna status amigável para o dashboard."""
    info: Dict[str, object] = {
        "arquivo": Path(arquivo).name,
        "parametro_dias": "",
        "data_parametro": None,
        "tem_dia_explicitado": False,
        "status_dia": "não encontrado",
    }
    try:
        linhas = read_csv_rows(Path(arquivo))
    except Exception as exc:
        info["status_dia"] = f"erro leitura: {exc}"
        return info

    valor = extrair_valor_parametro_linhas(linhas, "Dias") or extrair_valor_parametro_linhas(linhas, "Day")
    info["parametro_dias"] = valor
    if not valor:
        return info

    if re.search(r"\ball\b", valor, flags=re.I):
        info["status_dia"] = "All/acumulado"
        return info

    datas = []
    for item in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", valor):
        dt = pd.to_datetime(item, errors="coerce")
        if pd.notna(dt):
            datas.append(dt.normalize())
    if len(set(datas)) == 1:
        info["data_parametro"] = datas[0].strftime("%Y-%m-%d")
        info["tem_dia_explicitado"] = True
        info["status_dia"] = "dia explícito"
    elif len(set(datas)) > 1:
        info["data_parametro"] = f"{min(datas).strftime('%Y-%m-%d')} a {max(datas).strftime('%Y-%m-%d')}"
        info["status_dia"] = "período com múltiplas datas"
    else:
        info["status_dia"] = "sem data ISO no parâmetro"
    return info


def detectar_data_parametro_arquivo(arquivo: Path) -> Optional[pd.Timestamp]:
    info = extrair_dias_parametro_arquivo(arquivo)
    if info.get("tem_dia_explicitado") and info.get("data_parametro"):
        dt = pd.to_datetime(str(info["data_parametro"]), errors="coerce")
        if pd.notna(dt):
            return dt.normalize()
    return None


def detectar_data_lote(arquivos: Iterable[Path]) -> pd.Timestamp:
    """
    Define a data do lote/snapshot.

    Prioridade atualizada para upload manual:
    1. Data explícita do parâmetro Dias no Script Result 5 - Agent Volume.
       Essa é a data correta do CSS diário por agente.
    2. Data explícita do parâmetro Dias no Agent Contact Handling.
    3. Data explícita do parâmetro Dias em qualquer outro relatório.
    4. Maior data encontrada nas primeiras linhas do conteúdo.
    5. Data atual.

    Não usa data de criação do arquivo como regra principal. Data de arquivo é fofoca do sistema operacional.
    """
    arquivos = list(arquivos)

    # 1) CSS por agente manda no lote quando tem Day/Dias explícito.
    for arquivo in arquivos:
        if arquivo.name.lower().startswith("script result 5"):
            dt = detectar_data_parametro_arquivo(arquivo)
            if dt is not None and pd.notna(dt):
                return dt

    # 2) Depois tenta Agent Daily.
    for arquivo in arquivos:
        if arquivo.name.lower().startswith("agent - contact handling time"):
            dt = detectar_data_parametro_arquivo(arquivo)
            if dt is not None and pd.notna(dt):
                return dt

    # 3) Qualquer relatório com parâmetro diário explícito.
    for arquivo in arquivos:
        dt = detectar_data_parametro_arquivo(arquivo)
        if dt is not None and pd.notna(dt):
            return dt

    datas: List[pd.Timestamp] = []
    for arquivo in arquivos:
        try:
            linhas = read_csv_rows(arquivo)
        except Exception:
            continue
        texto = _texto_relatorio(linhas, limite=80)
        for item in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", texto):
            dt = pd.to_datetime(item, errors="coerce")
            if pd.notna(dt):
                datas.append(dt.normalize())

        for linha in linhas[:500]:
            for celula in linha:
                celula = str(celula).strip()
                if re.match(r"^20\d{2}-\d{2}-\d{2}$", celula):
                    dt = pd.to_datetime(celula, errors="coerce")
                    if pd.notna(dt):
                        datas.append(dt.normalize())

    if datas:
        return max(datas)
    return pd.Timestamp.today().normalize()

def _base_relatorio(nome_arquivo: str) -> str:
    return Path(nome_arquivo).stem.lower().strip()


def _arquivo_corresponde(nome_arquivo: str, nome_padrao: str) -> bool:
    """Aceita arquivo exato e variações incrementadas do SSRS.

    Exemplos aceitos:
    - Volume 4 - Daily.csv
    - Volume 4 - Daily_1.csv
    - Volume 4 - Daily (1).csv
    - Volume 4 - Daily - 20260623.csv
    """
    nome = Path(nome_arquivo).stem.lower().strip()
    base = Path(nome_padrao).stem.lower().strip()
    return nome == base or nome.startswith(base + "_") or nome.startswith(base + " (") or nome.startswith(base + " -")


def listar_arquivos_assinatura(pasta_assinatura: Path, exigir_css_queue: bool = True) -> Dict[str, List[Path]]:
    pasta_assinatura = Path(pasta_assinatura)
    if not pasta_assinatura.exists():
        raise FileNotFoundError(f"Pasta de assinaturas não encontrada: {pasta_assinatura}")
    if not pasta_assinatura.is_dir():
        raise NotADirectoryError(f"O caminho informado não é uma pasta: {pasta_assinatura}")

    chaves_obrigatorias = [k for k in REQUIRED_FILES if exigir_css_queue or k != "css_queue"]
    encontrados: Dict[str, List[Path]] = {k: [] for k in chaves_obrigatorias}

    arquivos = [p for p in pasta_assinatura.iterdir() if p.is_file() and p.suffix.lower() == ".csv" and p.stat().st_size > 0]
    for arquivo in arquivos:
        for chave in chaves_obrigatorias:
            if _arquivo_corresponde(arquivo.name, REQUIRED_FILES[chave]):
                encontrados[chave].append(arquivo)
                break

    for chave in encontrados:
        encontrados[chave] = sorted(encontrados[chave], key=lambda p: (p.stat().st_mtime, p.name.lower()))
    return encontrados


def montar_resumo_assinaturas(pasta_assinatura: Path, exigir_css_queue: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame]:
    encontrados = listar_arquivos_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    linhas = []
    for chave, arquivos in encontrados.items():
        for p in arquivos:
            info_dias = extrair_dias_parametro_arquivo(p)
            linhas.append({
                "Relatorio": chave,
                "Arquivo": p.name,
                "Tamanho_KB": round(p.stat().st_size / 1024, 2),
                "Parametro_Dias": info_dias.get("parametro_dias", ""),
                "Data_Parametro": info_dias.get("data_parametro") or "",
                "Status_Dia": info_dias.get("status_dia", ""),
                "Modificado_Em": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "Caminho": str(p),
            })
    df_arquivos = pd.DataFrame(linhas)
    lotes, resumo = listar_lotes_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    df_lotes = pd.DataFrame([
        {
            "Lote": lote["indice"],
            "Data_Lote": lote["data_lote"],
            "Arquivos": " | ".join([Path(v).name for v in lote["arquivos"].values()]),
            "Status": "Completo",
        }
        for lote in lotes
    ])
    if not df_lotes.empty:
        df_lotes["Total_Lotes_Completos"] = resumo.get("total_lotes_completos", 0)
    return df_arquivos, df_lotes


def listar_lotes_assinatura(pasta_assinatura: Path, exigir_css_queue: bool = True) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Monta lotes completos usando a ordem de modificação dos arquivos.

    Isso permite deixar a assinatura do SSRS em modo de incremento de nome.
    Exemplo: se houver 3 Volumes, 3 Agents, 3 CSS Agents e 3 CSS Queues, serão montados 3 lotes.
    Se uma família tiver menos arquivos, os lotes excedentes ficam pendentes e não são apagados.
    """
    encontrados = listar_arquivos_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    contagens = {k: len(v) for k, v in encontrados.items()}
    faltando = [REQUIRED_FILES[k] for k, qtd in contagens.items() if qtd == 0]
    total_completos = min(contagens.values()) if contagens else 0

    lotes: List[Dict[str, object]] = []
    for i in range(total_completos):
        arquivos_lote = {chave: arquivos[i] for chave, arquivos in encontrados.items()}
        dt = detectar_data_lote(arquivos_lote.values())
        maior_mtime = max(p.stat().st_mtime for p in arquivos_lote.values())
        lotes.append({
            "indice": i + 1,
            "data_lote": dt.strftime("%Y-%m-%d"),
            "data_lote_ts": dt,
            "mtime_referencia": maior_mtime,
            "arquivos": arquivos_lote,
        })

    resumo = {
        "contagens": contagens,
        "faltando": faltando,
        "total_lotes_completos": total_completos,
        "total_arquivos_encontrados": sum(contagens.values()),
        "pasta": str(pasta_assinatura),
    }
    return lotes, resumo




def validar_lote_para_css_diario(lote: Dict[str, object], exigir_data_css_agent: bool = True) -> None:
    """Impede que Script Result 5 com Dias=All entre como CSS diário por agente."""
    if not exigir_data_css_agent:
        return
    arquivos = lote.get("arquivos", {}) if isinstance(lote, dict) else {}
    css = arquivos.get("css_agent") if isinstance(arquivos, dict) else None
    if not css:
        return
    info = extrair_dias_parametro_arquivo(Path(css))
    if not info.get("tem_dia_explicitado"):
        raise RPAError(
            "O arquivo Script Result 5 - Agent Volume não possui um Dia explícito no parâmetro Dias. "
            f"Valor encontrado: {info.get('parametro_dias') or 'não encontrado'}; status: {info.get('status_dia')}. "
            "Para CSS diário por agente, baixe o relatório manualmente com Day/Dias = uma data específica, "
            "por exemplo 2026-06-22. Arquivo acumulado/All não será carregado como diário, porque mentira estatística já tem demais no mundo."
        )


def validar_arquivos_assinatura(pasta_assinatura: Path, exigir_css_queue: bool = True) -> Dict[str, Path]:
    lotes, resumo = listar_lotes_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    if not lotes:
        faltando = resumo.get("faltando") or []
        detalhe = f" Faltando: {', '.join(faltando)}." if faltando else ""
        raise FileNotFoundError(
            f"Nenhum lote completo encontrado na pasta de assinaturas.{detalhe} Pasta analisada: {pasta_assinatura}"
        )
    return lotes[0]["arquivos"]  # compatibilidade com chamadas antigas


def _nome_pasta_lote(dt_lote: pd.Timestamp, arquivos: Iterable[Path], indice: int = 1) -> str:
    maior_mtime = max((p.stat().st_mtime for p in arquivos), default=datetime.now().timestamp())
    stamp = datetime.fromtimestamp(maior_mtime).strftime("%H%M%S")
    return f"{dt_lote:%Y_%m_%d}__{stamp}__lote_{indice:03d}"


def arquivar_lote_assinatura(
    arquivos_lote: Dict[str, Path],
    entrada_historico: Path,
    data_lote: Optional[str] = None,
    indice_lote: int = 1,
    origem: Optional[Path] = None,
) -> Path:
    dt_lote = pd.to_datetime(data_lote, errors="coerce").normalize() if data_lote else detectar_data_lote(arquivos_lote.values())
    if pd.isna(dt_lote):
        dt_lote = pd.Timestamp.today().normalize()

    pasta_lote = entrada_historico / _nome_pasta_lote(dt_lote, arquivos_lote.values(), indice_lote)
    tentativas = 1
    while pasta_lote.exists():
        tentativas += 1
        pasta_lote = entrada_historico / f"{_nome_pasta_lote(dt_lote, arquivos_lote.values(), indice_lote)}__{tentativas}"
    pasta_lote.mkdir(parents=True, exist_ok=True)

    manifest = {
        "data_lote": dt_lote.strftime("%Y-%m-%d"),
        "data_arquivamento": datetime.now().isoformat(timespec="seconds"),
        "origem": str(origem or ""),
        "arquivos": {},
    }

    for chave, origem_arquivo in arquivos_lote.items():
        destino = pasta_lote / REQUIRED_FILES[chave]
        shutil.copy2(origem_arquivo, destino)
        manifest["arquivos"][origem_arquivo.name] = {
            "relatorio": chave,
            "nome_canonico": destino.name,
            "tamanho_bytes": destino.stat().st_size,
            "sha256": sha256_file(destino),
            "origem": str(origem_arquivo),
        }

    (pasta_lote / "manifest_assinatura.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Lote arquivado em: {pasta_lote}")
    return pasta_lote


def arquivar_assinatura(
    pasta_assinatura: Path,
    entrada_historico: Path,
    data_lote: Optional[str] = None,
    exigir_css_queue: bool = True,
) -> Path:
    encontrados = validar_arquivos_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    return arquivar_lote_assinatura(encontrados, entrada_historico, data_lote=data_lote, indice_lote=1, origem=pasta_assinatura)


def limpar_arquivos_origem(arquivos: Iterable[Path], modo: str = "delete", pasta_destino: Optional[Path] = None) -> List[Dict[str, str]]:
    """Remove ou move arquivos da pasta Assinaturas depois de banco OK.

    Segurança básica: só opera em arquivos existentes. O histórico já deve ter sido copiado antes.
    """
    registros: List[Dict[str, str]] = []
    arquivos_unicos = []
    vistos = set()
    for p in arquivos:
        p = Path(p)
        if p in vistos:
            continue
        vistos.add(p)
        arquivos_unicos.append(p)

    if modo not in {"delete", "move"}:
        raise ValueError("modo deve ser 'delete' ou 'move'")

    destino_base = Path(pasta_destino) if pasta_destino else None
    if modo == "move":
        if destino_base is None:
            destino_base = arquivos_unicos[0].parent / "_processados_ssrs" if arquivos_unicos else Path("_processados_ssrs")
        destino_base = destino_base / datetime.now().strftime("%Y%m%d_%H%M%S")
        destino_base.mkdir(parents=True, exist_ok=True)

    for origem in arquivos_unicos:
        if not origem.exists():
            registros.append({"Arquivo": str(origem), "Acao": "IGNORADO", "Mensagem": "Arquivo já não existe"})
            continue
        try:
            if modo == "delete":
                origem.unlink()
                registros.append({"Arquivo": str(origem), "Acao": "EXCLUIDO", "Mensagem": "Arquivo removido da pasta Assinaturas"})
            else:
                destino = destino_base / origem.name
                contador = 1
                while destino.exists():
                    destino = destino_base / f"{origem.stem}_{contador}{origem.suffix}"
                    contador += 1
                shutil.move(str(origem), str(destino))
                registros.append({"Arquivo": str(origem), "Acao": "MOVIDO", "Mensagem": str(destino)})
        except Exception as exc:
            registros.append({"Arquivo": str(origem), "Acao": "ERRO", "Mensagem": str(exc)})
    return registros


def gerar_aliases_agent(saida: Path) -> None:
    aliases = {
        "f_gente_contact_diario.csv": "f_agent_contact_diario.csv",
        "f_gente_contact_fila_diario.csv": "f_agent_contact_fila_diario.csv",
    }
    for origem_nome, destino_nome in aliases.items():
        origem = saida / origem_nome
        destino = saida / destino_nome
        if origem.exists():
            shutil.copy2(origem, destino)
            log(f"Alias criado: {destino_nome} <- {origem_nome}")


def executar_etl_contact_center(base_dir: Path, entrada_historico: Path, saida: Path, data_lote: Optional[str]) -> None:
    # Importa o ETL legado testado para os arquivos reais do SSRS.
    sys.path.insert(0, str(base_dir))
    from backup import etl_contact_center_unificado as etl

    etl.DATA_ATUALIZACAO_CARGA_MANUAL = data_lote
    etl.processar_contact_center(
        pasta_entrada=entrada_historico,
        pasta_saida=saida,
        incremental=True,
        substituir_chaves_existentes=True,
    )
    gerar_aliases_agent(saida)


def enviar_banco(saida: Path, database_name: Optional[str], modo: str) -> List[Dict[str, object]]:
    from db_cockroach import enviar_csv_para_banco, preparar_database

    db = preparar_database(database_name)
    resultados: List[Dict[str, object]] = []

    for arquivo_nome, tabela in TABELAS_BI.items():
        caminho = saida / arquivo_nome
        if not caminho.exists():
            continue
        resultado = enviar_csv_para_banco(
            caminho,
            tabela=tabela,
            database_name=db,
            if_exists=modo,
        )
        resultados.append(resultado)
        log(
            f"Banco {resultado.get('Status')}: {arquivo_nome} -> {tabela} | "
            f"lidas={resultado.get('Linhas_Lidas')} gravadas={resultado.get('Linhas_Gravadas')}"
        )
    return resultados


def processar_assinaturas(
    pasta_assinatura: Path,
    base_dir: Path,
    entrada_historico: Path,
    saida: Path,
    data_lote: Optional[str] = None,
    exigir_css_queue: bool = True,
    enviar_para_banco: bool = False,
    database_name: Optional[str] = None,
    db_mode: str = "upsert",
    limpar_assinaturas_apos_banco: bool = False,
    modo_limpeza: str = "delete",
    max_lotes: Optional[int] = None,
    exigir_data_css_agent: bool = True,
) -> Dict[str, object]:
    """Fluxo central para dashboard e CLI.

    Arquiva os lotes completos, executa o ETL sobre o histórico e, se solicitado,
    envia ao banco em upsert. A limpeza da pasta Assinaturas só acontece se o banco
    terminar sem Status=ERRO. Dados primeiro, faxina depois. Humanidade quase salva.
    """
    base_dir = Path(base_dir).resolve()
    pasta_assinatura = Path(pasta_assinatura)
    entrada_historico = Path(entrada_historico).resolve()
    saida = Path(saida).resolve()
    entrada_historico.mkdir(parents=True, exist_ok=True)
    saida.mkdir(parents=True, exist_ok=True)

    lotes, resumo = listar_lotes_assinatura(pasta_assinatura, exigir_css_queue=exigir_css_queue)
    if max_lotes is not None:
        lotes = lotes[: int(max_lotes)]

    if not lotes:
        faltando = resumo.get("faltando") or []
        detalhe = f" Faltando: {', '.join(faltando)}." if faltando else ""
        raise RPAError(f"Nenhum lote completo para processar.{detalhe}")

    for lote in lotes:
        validar_lote_para_css_diario(lote, exigir_data_css_agent=exigir_data_css_agent)

    pastas_arquivadas: List[str] = []
    arquivos_origem_processados: List[Path] = []
    for lote in lotes:
        pasta_lote = arquivar_lote_assinatura(
            lote["arquivos"],
            entrada_historico,
            data_lote=data_lote or lote.get("data_lote"),
            indice_lote=int(lote.get("indice", 1)),
            origem=pasta_assinatura,
        )
        pastas_arquivadas.append(str(pasta_lote))
        arquivos_origem_processados.extend(list(lote["arquivos"].values()))

    data_lote_etl = data_lote
    if len(lotes) == 1 and not data_lote_etl:
        data_lote_etl = str(lotes[0].get("data_lote") or "")

    executar_etl_contact_center(base_dir, entrada_historico, saida, data_lote_etl)

    resultados_banco: List[Dict[str, object]] = []
    limpeza: List[Dict[str, str]] = []
    if enviar_para_banco:
        resultados_banco = enviar_banco(saida, database_name, db_mode)
        erros = [r for r in resultados_banco if str(r.get("Status", "")).upper() == "ERRO"]
        if erros:
            raise RPAError(f"Envio ao banco teve erro em {len(erros)} tabela(s). Originais NÃO foram excluídos.")
        if limpar_assinaturas_apos_banco:
            limpeza = limpar_arquivos_origem(arquivos_origem_processados, modo=modo_limpeza)
            erros_limpeza = [r for r in limpeza if r.get("Acao") == "ERRO"]
            if erros_limpeza:
                raise RPAError(f"Banco OK, mas houve erro ao limpar {len(erros_limpeza)} arquivo(s) da assinatura.")

    return {
        "status": "OK",
        "lotes_processados": len(lotes),
        "resumo_assinaturas": resumo,
        "pastas_arquivadas": pastas_arquivadas,
        "saida": str(saida),
        "resultados_banco": resultados_banco,
        "limpeza": limpeza,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automação para arquivos de assinatura SSRS: arquiva, executa ETL e opcionalmente envia ao CockroachDB."
    )
    parser.add_argument(
        "--assinaturas",
        default=os.getenv("SSRS_ASSINATURAS_PATH", r"H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas"),
        help="Pasta onde o SSRS grava os CSVs da assinatura. Para tarefa agendada, prefira UNC em vez de H:.",
    )
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parent), help="Pasta base do projeto.")
    parser.add_argument("--entrada-historico", default=None, help="Pasta onde os snapshots serão preservados.")
    parser.add_argument("--saida", default=None, help="Pasta dos CSVs finais tratados.")
    parser.add_argument("--logs", default=None, help="Pasta de logs.")
    parser.add_argument("--data-lote", default=None, help="Força a data do lote no formato AAAA-MM-DD.")
    parser.add_argument("--nao-exigir-css-queue", action="store_true", help="Permite rodar sem Script Result 3, mas o CSS diário fica pior. Que surpresa.")
    parser.add_argument("--enviar-banco", action="store_true", help="Envia os CSVs tratados para o banco após o ETL.")
    parser.add_argument("--database-name", default=None, help="Nome do database no CockroachDB. Se omitido, usa COCKROACH_DATABASE_NAME ou rpa_ssrs.")
    parser.add_argument(
        "--db-mode",
        default="upsert",
        choices=["upsert", "incremental", "append", "replace"],
        help="Modo de gravação no banco. Recomendado para base existente: upsert.",
    )
    parser.add_argument("--max-lotes", type=int, default=None, help="Quantidade máxima de lotes completos a processar.")
    parser.add_argument(
        "--permitir-css-agent-sem-day-explicito",
        action="store_true",
        help="Permite processar Script Result 5 sem Dias diário explícito. Não recomendado para CSS diário por agente.",
    )
    parser.add_argument(
        "--limpar-assinaturas-apos-banco",
        action="store_true",
        help="Depois de banco OK, exclui/move os arquivos originais da pasta Assinaturas.",
    )
    parser.add_argument(
        "--modo-limpeza",
        default="delete",
        choices=["delete", "move"],
        help="Como limpar a pasta Assinaturas após banco OK. delete remove; move manda para _processados_ssrs.",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    pasta_assinatura = Path(args.assinaturas)
    entrada_historico = Path(args.entrada_historico).resolve() if args.entrada_historico else base_dir / "entrada_assinaturas"
    saida = Path(args.saida).resolve() if args.saida else base_dir / "saida"
    logs_dir = Path(args.logs).resolve() if args.logs else base_dir / "LOGS"

    entrada_historico.mkdir(parents=True, exist_ok=True)
    saida.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    log("Iniciando automação SSRS.")
    log(f"Assinaturas: {pasta_assinatura}")
    log(f"Histórico local: {entrada_historico}")
    log(f"Saída: {saida}")

    try:
        resultado = processar_assinaturas(
            pasta_assinatura=pasta_assinatura,
            base_dir=base_dir,
            entrada_historico=entrada_historico,
            saida=saida,
            data_lote=args.data_lote,
            exigir_css_queue=not args.nao_exigir_css_queue,
            enviar_para_banco=args.enviar_banco,
            database_name=args.database_name,
            db_mode=args.db_mode,
            limpar_assinaturas_apos_banco=args.limpar_assinaturas_apos_banco,
            modo_limpeza=args.modo_limpeza,
            max_lotes=args.max_lotes,
            exigir_data_css_agent=not args.permitir_css_agent_sem_day_explicito,
        )
        log(f"Automação concluída com sucesso. Lotes processados: {resultado['lotes_processados']}")
        return 0

    except Exception as exc:
        erro_path = logs_dir / f"erro_assinaturas_{datetime.now():%Y%m%d_%H%M%S}.log"
        erro_path.write_text(traceback.format_exc(), encoding="utf-8")
        log(f"ERRO: {exc}")
        log(f"Detalhes gravados em: {erro_path}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
