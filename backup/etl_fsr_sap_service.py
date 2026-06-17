from pathlib import Path
import argparse
import pandas as pd


def ler_arquivo(caminho: Path) -> pd.DataFrame:
    if caminho.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(caminho, sheet_name="_DATA_MASTER", header=4, dtype=str)
    elif caminho.suffix.lower() == ".csv":
        df = pd.read_csv(caminho, dtype=str, sep=None, engine="python")
    else:
        raise ValueError(f"Formato não suportado: {caminho.suffix}")

    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def tratar_datas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["Criado em"] = pd.to_datetime(df["Criado em"], errors="coerce", dayfirst=True)
    df["Data de conclusão"] = pd.to_datetime(df["Data de conclusão"], errors="coerce", dayfirst=True)

    df["Data_Criacao"] = df["Criado em"].dt.date
    df["Data_Conclusao"] = df["Data de conclusão"].dt.date

    df["Fechado"] = df.apply(
        lambda x: "Sim"
        if pd.notna(x["Data_Criacao"])
        and pd.notna(x["Data_Conclusao"])
        and x["Data_Criacao"] == x["Data_Conclusao"]
        else "Não",
        axis=1
    )

    df["Ano"] = df["Criado em"].dt.year
    df["Mes"] = df["Criado em"].dt.month
    df["Dia"] = df["Criado em"].dt.day
    df["Ano_Mes"] = df["Criado em"].dt.strftime("%Y-%m")

    return df


def localizar_arquivos(pasta_entrada: Path):
    arquivos = []
    for ext in ["*.xlsx", "*.xls", "*.csv"]:
        arquivos.extend(pasta_entrada.glob(ext))

    if not arquivos:
        raise FileNotFoundError(f"Nenhum arquivo encontrado em {pasta_entrada}")

    return arquivos


def salvar_csv(df: pd.DataFrame, caminho_saida: Path):
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(caminho_saida, index=False, sep=";", encoding="utf-8-sig")


def processar(entrada: Path, saida: Path):
    arquivos = localizar_arquivos(entrada)
    bases = []

    for arquivo in arquivos:
        print(f"Processando: {arquivo.name}")
        df = ler_arquivo(arquivo)
        df = tratar_datas(df)
        df["Arquivo_Origem"] = arquivo.name
        bases.append(df)

    consolidado = pd.concat(bases, ignore_index=True)

    caminho_saida = saida / "f_fsr_tratado.csv"
    salvar_csv(consolidado, caminho_saida)

    print(f"Arquivo gerado: {caminho_saida}")
    print(f"Total de linhas: {len(consolidado)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrada", required=True)
    parser.add_argument("--saida", required=True)
    args = parser.parse_args()

    processar(Path(args.entrada), Path(args.saida))


if __name__ == "__main__":
    main()