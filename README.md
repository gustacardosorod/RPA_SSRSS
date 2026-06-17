# RPA SSRS + SAP + GOV Chamados - Streamlit

Projeto ajustado para executar o RPA completo de tratamento de relatórios:

- Contact Center SSRS
  - `f_agent_contact_diario.csv`
  - `f_css_atendente.csv`
  - `f_volume_geral_diario.csv`
  - `f_volume_fila_diario.csv`
  - `f_css_geral_diario.csv`
  - `f_indicadores_gerais.csv`
  - `dim_atendentes.csv`
- SAP Service / FSR
  - `f_fsr_tratado.csv`
- Reclamações SAP
  - `f_reclamacoes_sap_tratado.csv`
- GOV Chamados
  - `f_gov_chamados_tratado.csv`
  - `dim_status_chamados.csv`
  - `dim_unidades_chamados.csv`
  - `dim_responsaveis_chamados.csv`
  - `dim_categorias_chamados.csv`

A interface em Streamlit permite importar arquivos, executar o ETL, visualizar prévias, baixar saídas e consultar logs.

## Estrutura principal

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
├── etl_gov_chamados.py
├── requirements.txt
└── README.md
```

## Instalação local

Use Python 3.10 ou superior.

```bash
pip install -r requirements.txt
```

## Rodar a interface

```bash
streamlit run streamlit_app.py
```

## Rodar o RPA completo por linha de comando

```bash
python main.py --carga tudo --reprocessar-tudo
```

## Rodar somente GOV Chamados

```bash
python main.py --carga gov_chamados --reprocessar-tudo
```

## Rodar cargas separadas

```bash
python main.py --carga agent
python main.py --carga css
python main.py --carga volume_fila
python main.py --carga indicadores
python main.py --carga fsr
python main.py --carga reclamacoes_sap
python main.py --carga gov_chamados
```

## Modos de processamento

### Base zero

Recria a saída usando todos os arquivos da entrada.

```bash
python main.py --carga tudo --reprocessar-tudo
```

### Incremental

Mantém o histórico e inclui somente chaves novas.

```bash
python main.py --carga tudo
```

### Corrigir período

Substitui registros já existentes pela mesma chave.

```bash
python main.py --carga tudo --substituir
```

## GOV Chamados

A nova carga processa arquivos da pasta:

```text
entrada_gov_chamados/
```

Formatos aceitos:

- `.xlsx`
- `.csv`
- `.txt`

Saída principal:

```text
saida/f_gov_chamados_tratado.csv
```

Dimensões auxiliares:

```text
saida/dim_status_chamados.csv
saida/dim_unidades_chamados.csv
saida/dim_responsaveis_chamados.csv
saida/dim_categorias_chamados.csv
```

### Regra de chave GOV

A chave principal é:

```text
Chave_GOV = Numero_Chamado / Protocolo
```

Quando não houver número/protocolo, o ETL usa uma chave hash com:

```text
Data_Abertura + Unidade + Categoria + Descricao + Cliente
```

## Logs

Os logs ficam na pasta:

```text
LOGS/
```

Principais logs:

```text
LOGS/log_carga_total.csv
LOGS/log_f_gov_chamados_tratado.csv
LOGS/log_gov_chamados_arquivos.csv
```

O log GOV informa:

- arquivos lidos
- arquivos ignorados
- linhas brutas
- linhas tratadas
- duplicidades removidas
- registros com data inválida
- registros sem status
- registros sem responsável
- período mínimo e máximo da base

## Interface Streamlit

A interface possui:

- Início
- Importar relatórios
- Processar ETL
- Pré-visualizar dados
- Exportar arquivos tratados
- Logs do processamento
- Sobre o projeto

### Upload por ZIP

Você pode enviar um ZIP contendo a estrutura:

```text
entrada/
entrada_fsr/
entrada_sap/
entrada_gov_chamados/
```

### Upload por tipo

Também é possível enviar arquivos separados por carga:

- Contact Center SSRS
- SAP Service / FSR
- Reclamações SAP
- GOV Chamados

Para Contact Center, a interface cria uma subpasta diária dentro de `entrada/`, por exemplo:

```text
entrada/17_06_2026/
```

## Deploy no Streamlit Community Cloud

1. Crie um repositório no GitHub.
2. Suba os arquivos do projeto.
3. Verifique se estes arquivos estão na raiz:
   - `streamlit_app.py`
   - `requirements.txt`
   - `app.py`
   - scripts `etl_*.py`
   - `common.py`
4. Acesse o Streamlit Community Cloud.
5. Crie um novo app.
6. Selecione:
   - repositório
   - branch
   - arquivo principal: `streamlit_app.py`
7. Publique.

## Importante sobre RPA online

O Streamlit hospedado online não acessa automaticamente sistemas internos da empresa, como SSRS em IP privado ou pastas de rede locais.

Então existem dois cenários:

### Cenário 1 - RPA de tratamento online

O usuário exporta os relatórios, sobe no app e baixa os arquivos tratados.

Este projeto já faz isso.

### Cenário 2 - Extração automática total

Para extrair automaticamente do SSRS/SAP sem upload manual, é necessário rodar a extração em uma máquina com acesso à rede interna, por exemplo:

- VM corporativa
- servidor Windows
- Power Automate Desktop
- tarefa agendada
- pipeline interno

Depois essa máquina envia os arquivos para o app, SharePoint, storage ou pasta monitorada.

Não coloque credenciais dentro do código. Use variáveis de ambiente ou secrets do ambiente de hospedagem. Sim, senha no código ainda é um crime administrativo, mesmo quando “é só temporário”.

## Arquivos alterados/criados

Criados:

- `etl_gov_chamados.py`
- `streamlit_app.py`
- `main.py`
- `requirements.txt`
- `.gitignore`

Alterados:

- `app.py`
- `common.py`
- `README.md`

## Validação realizada

Foi executado:

```bash
python main.py --carga gov_chamados --reprocessar-tudo
python main.py --carga tudo --reprocessar-tudo
```

Resultado da carga completa validada com os arquivos do ZIP:

- `f_agent_contact_diario.csv`: 2.645 linhas
- `f_css_atendente.csv`: 126 linhas
- `f_volume_geral_diario.csv`: 167 linhas
- `f_volume_fila_diario.csv`: 1.002 linhas
- `f_css_geral_diario.csv`: 167 linhas
- `f_indicadores_gerais.csv`: 167 linhas
- `dim_atendentes.csv`: 38 linhas
- `f_fsr_tratado.csv`: 28.310 linhas
- `f_reclamacoes_sap_tratado.csv`: 562 linhas
- `f_gov_chamados_tratado.csv`: 7.408 linhas

A planilha GOV veio com referência de célula inválida no XML interno. O ETL GOV possui leitor próprio para esse caso e não depende do `read_excel` puro.
