# RPA SSRS - Gerenciador Streamlit

Dashboard central para gerenciar os arquivos de assinatura do SSRS, processar o ETL e enviar os dados tratados para o CockroachDB usado pelo Power BI.

## Ponto crítico sobre Streamlit Cloud

O Streamlit Cloud **não acessa** `H:\...` nem `\\VANADIS\...` diretamente, porque ele roda fora da rede corporativa.

Para leitura automática da pasta Assinaturas, use uma destas opções:

1. Hospedar o app em uma VM/servidor Windows da empresa com acesso ao compartilhamento de rede.
2. Rodar localmente no seu computador enquanto ele estiver ligado.
3. Usar Streamlit Cloud apenas com upload manual dos CSVs.
4. Mudar a entrega do SSRS para um armazenamento em nuvem acessível pelo app, como SharePoint/OneDrive/Azure Blob, caso a empresa permita.

A melhor arquitetura para produção é uma VM/servidor corporativo.

## Fluxo recomendado

```text
SSRS gera CSVs na pasta Assinaturas
        ↓
SSRS usa incremento de nome e não sobrescreve arquivos
        ↓
Dashboard lê todos os arquivos Volume/Agent/CSS por prefixo
        ↓
Cada conjunto completo vira um lote
        ↓
RPA copia lote para entrada_assinaturas
        ↓
ETL trata os arquivos
        ↓
Banco recebe upsert
        ↓
Após banco OK, arquivos originais da pasta Assinaturas são excluídos ou movidos
```

## Relatórios esperados

O app procura estes arquivos e variações incrementadas:

```text
Volume 4 - Daily*.csv
Agent - Contact Handling Time 4 - Daily*.csv
Script Result 5 - Agent Volume*.csv
Script Result 3 - Queue Volume per Day*.csv
```

Exemplos aceitos:

```text
Volume 4 - Daily.csv
Volume 4 - Daily_1.csv
Volume 4 - Daily (1).csv
Volume 4 - Daily - 20260623.csv
```

## Rodar o dashboard localmente

Na pasta do projeto:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Ou execute:

```bat
applocais\abrir_dashboard_streamlit.bat
```

## Configurar banco

Crie `.streamlit\secrets.toml` com base em `.streamlit\secrets.toml.example`:

```toml
[cockroachdb]
database_url = "postgresql://USUARIO:SENHA@HOST:26257/defaultdb?sslmode=verify-full"
database_name = "rpa_ssrs"

[ssrs]
assinaturas_path = "\\\\VANADIS\\Groups1\\Groups\\VAB_TQC\\SGQ - 2021\\1. SGQ - 2023\\4. Demanda de Dados\\28. BI Clientes\\Assinaturas"
```

Também funciona com variáveis de ambiente:

```text
COCKROACH_DATABASE_URL
COCKROACH_DATABASE_NAME
SSRS_ASSINATURAS_PATH
```

## Modo banco recomendado

Para base existente, use:

```text
upsert
```

Ele remove/substitui no banco apenas as chaves que estão chegando na nova carga e preserva o restante do histórico.

Evite `replace` na rotina diária, porque ele recria a tabela e pode apagar histórico que já existe.

## Limpeza da pasta Assinaturas

A limpeza só acontece quando:

1. O lote foi copiado para `entrada_assinaturas`.
2. O ETL terminou.
3. O envio ao banco terminou sem erro.
4. A opção `Limpar arquivos originais após banco OK` está ativada.

Opções:

```text
delete = exclui os arquivos originais da pasta Assinaturas
move   = move os arquivos para _processados_ssrs
```

Mesmo usando `delete`, o snapshot fica preservado em `entrada_assinaturas`. Não apague essa pasta sem ter certeza, porque ela é o seu seguro contra o fim de semana, essa entidade cruel.

## Rotina automática sem dashboard

Para rodar via Agendador de Tarefas:

```bat
applocais\rodar_assinaturas_ssrs.bat
```

O `.bat` já está configurado para:

```text
--enviar-banco
--db-mode upsert
--limpar-assinaturas-apos-banco
--modo-limpeza delete
```

Ajuste o caminho `ASSINATURAS` para UNC em produção.

## Publicar no Streamlit Cloud

Funciona para:

- upload manual dos CSVs;
- gerenciamento visual das saídas;
- envio ao CockroachDB, desde que os secrets estejam configurados.

Não funciona para ler automaticamente uma pasta `H:` ou `\\VANADIS` da rede interna. Para isso, use servidor/VM interna.

## Upload manual com data do parâmetro

Nesta versão, o `Script Result 5 - Agent Volume` é validado pelo parâmetro `Dias` dentro do próprio CSV. Para CSS diário por agente, o arquivo precisa estar com uma data específica, por exemplo `2026-06-22`. Se vier `All`, o app bloqueia a carga diária.

A data do lote passa a ser priorizada assim:

1. `Dias` do `Script Result 5 - Agent Volume`
2. `Dias` do `Agent - Contact Handling Time 4 - Daily`
3. Outras datas explícitas encontradas nos relatórios

No modo Upload manual, use o botão `Processar arquivos enviados` na aba `Processamento`.
