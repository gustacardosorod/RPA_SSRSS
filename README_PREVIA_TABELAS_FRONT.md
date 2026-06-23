# Prévia das tabelas tratadas no Streamlit

Esta versão do painel foi ajustada para o fluxo manual:

1. Upload manual dos CSVs do SSRS.
2. Validação do parâmetro `Dias` do `Script Result 5 - Agent Volume`.
3. Bloqueio de datas já importadas.
4. Geração da prévia das tabelas tratadas no front.
5. Revisão das tabelas pelo usuário.
6. Envio ao CockroachDB somente após confirmação.

## O que mudou no app.py

A tela **Nova importação** agora possui duas etapas:

### 1) Gerar e revisar tabelas tratadas

Botão:

```text
👁️ Gerar prévia das tabelas tratadas
```

Este botão roda o ETL sem gravar no banco e exibe no front:

- resumo dos arquivos tratados;
- nome do CSV gerado;
- tabela de destino no banco;
- quantidade de linhas;
- tamanho do arquivo;
- prévia dos dados tratados;
- opção de download do CSV tratado.

### 2) Enviar ao banco

Botão:

```text
✅ Importar tabelas tratadas no banco
```

Este botão só é liberado quando:

- os arquivos obrigatórios foram enviados;
- o `Script Result 5` possui `Dias` com data explícita;
- a data ainda não existe no banco;
- a prévia das tabelas tratadas foi gerada;
- o usuário marcou a confirmação de revisão.

## Regra de data

A data oficial da carga continua sendo lida do parâmetro `Dias` do arquivo:

```text
Script Result 5 - Agent Volume.csv
```

Exemplo aceito:

```text
Dias: 2026-06-22
```

Exemplo bloqueado:

```text
Dias: All
```

## Tela Arquivos tratados

A tela **Arquivos tratados** também passou a mostrar as tabelas no front, não apenas o botão de download. Assim você consegue revisar as saídas finais mesmo depois da execução.
