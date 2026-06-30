from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from common import salvar_manifest, salvar_csv
from etl_agent_contact_diario import processar_agent_contact
from etl_css_atendente import processar_css_atendente
from etl_volume_fila_diario import processar_volume_fila
from etl_validacao import validar_contact_center_processado


def _processar_css_fila(pasta_entrada: Path, pasta_saida: Path, pasta_logs: Path, substituir: bool, reprocessar_tudo: bool):
    # O módulo legado de CSS por fila fica dentro do ETL unificado em alguns repositórios.
    # Mantemos import tardio para não quebrar ambientes onde o arquivo não existe.
    try:
        from backup import etl_contact_center_unificado as legado
    except Exception:
        return None
    if hasattr(legado, "processar_css_fila_diario"):
        return legado.processar_css_fila_diario(pasta_entrada, pasta_saida, pasta_logs, substituir, reprocessar_tudo)
    return None


def processar_pacote_contact_center(
    entrada: Path,
    saida: Path,
    logs: Path,
    substituir: bool = True,
    reprocessar_tudo: bool = False,
    validar: bool = True,
    bloquear_dia_atual: bool = True,
    data_maxima: Optional[pd.Timestamp] = None,
    enviar_banco: bool = False,
    database_name: Optional[str] = None,
    db_mode: str = "upsert",
) -> Dict[str, object]:
    entrada = Path(entrada)
    saida = Path(saida)
    logs = Path(logs)
    saida.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    manifest = salvar_manifest(entrada)
    resultados: Dict[str, object] = {
        "entrada": str(entrada),
        "saida": str(saida),
        "manifest": str(manifest),
        "tabelas_processadas": [],
        "auditoria": None,
        "banco": [],
    }

    processar_volume_fila(entrada, saida, logs, substituir=substituir, reprocessar_tudo=reprocessar_tudo)
    resultados["tabelas_processadas"].extend(["f_volume_geral_diario", "f_volume_fila_diario"])

    processar_agent_contact(entrada, saida, logs, substituir=substituir, reprocessar_tudo=reprocessar_tudo)
    resultados["tabelas_processadas"].extend(["f_agent_contact_diario", "f_agent_contact_fila_diario"])

    try:
        processar_css_atendente(entrada, saida, logs, substituir=substituir, reprocessar_tudo=reprocessar_tudo)
        resultados["tabelas_processadas"].append("f_css_atendente")
    except Exception as exc:
        resultados.setdefault("avisos", []).append(f"CSS atendente não processado: {exc}")

    try:
        _processar_css_fila(entrada, saida, logs, substituir=substituir, reprocessar_tudo=reprocessar_tudo)
    except Exception as exc:
        resultados.setdefault("avisos", []).append(f"CSS fila não processado pelo legado: {exc}")

    if validar:
        auditoria = validar_contact_center_processado(
            saida,
            bloquear_dia_atual=bloquear_dia_atual,
            data_maxima=data_maxima,
        )
        caminho_auditoria = salvar_csv(auditoria, saida, "auditoria_validacao_contact_center.csv")
        resultados["auditoria"] = str(caminho_auditoria)
        if (auditoria["Severidade"] == "ERRO").any():
            erros = auditoria.loc[auditoria["Severidade"] == "ERRO"].to_dict("records")
            raise RuntimeError(f"Carga bloqueada pela auditoria: {erros[:5]}")

    if enviar_banco:
        from db_cockroach import enviar_pasta_saida_para_banco

        resultados["banco"] = enviar_pasta_saida_para_banco(
            saida,
            database_name=database_name,
            apenas_padrao=True,
            if_exists=db_mode,
        )
        erros = [r for r in resultados["banco"] if r.get("Status") == "ERRO"]
        if erros:
            raise RuntimeError(f"Carga no banco com erro: {erros[:5]}")

    resumo = saida / "resumo_execucao_contact_center.json"
    resumo.write_text(json.dumps(resultados, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return resultados


def main() -> None:
    parser = argparse.ArgumentParser(description="Processa pacote SSRS com validação antes do banco.")
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    parser.add_argument("--logs", default=None)
    parser.add_argument("--substituir", action="store_true", default=True)
    parser.add_argument("--reprocessar-tudo", action="store_true")
    parser.add_argument("--sem-validacao", action="store_true")
    parser.add_argument("--permitir-dia-atual", action="store_true")
    parser.add_argument("--data-maxima", default=None)
    parser.add_argument("--enviar-banco", action="store_true")
    parser.add_argument("--database-name", default=None)
    parser.add_argument("--db-mode", default="upsert", choices=["upsert", "incremental", "append", "replace"])
    args = parser.parse_args()

    data_maxima = pd.to_datetime(args.data_maxima) if args.data_maxima else None
    resultado = processar_pacote_contact_center(
        entrada=Path(args.entrada),
        saida=Path(args.saida),
        logs=Path(args.logs or Path(args.saida).parent / "LOGS"),
        substituir=args.substituir,
        reprocessar_tudo=args.reprocessar_tudo,
        validar=not args.sem_validacao,
        bloquear_dia_atual=not args.permitir_dia_atual,
        data_maxima=data_maxima,
        enviar_banco=args.enviar_banco,
        database_name=args.database_name,
        db_mode=args.db_mode,
    )
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
