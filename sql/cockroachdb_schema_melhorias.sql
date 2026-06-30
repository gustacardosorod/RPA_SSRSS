-- RPA_SSRS - Schema recomendado para cargas estáveis no CockroachDB
-- Versão: 2026-06-29
-- Objetivo: parar de depender de append/delete+append e usar chaves primárias reais.

CREATE DATABASE IF NOT EXISTS rpa_ssrs;
USE rpa_ssrs;

CREATE TABLE IF NOT EXISTS log_cargas (
    id_carga STRING NOT NULL,
    data_hora_carga TIMESTAMPTZ NOT NULL DEFAULT now(),
    arquivo_origem STRING NULL,
    tabela_destino STRING NULL,
    linhas_lidas INT8 NULL,
    linhas_gravadas INT8 NULL,
    modo_carga STRING NULL,
    status_processamento STRING NULL,
    mensagem STRING NULL,
    PRIMARY KEY (id_carga, tabela_destino, arquivo_origem)
);

CREATE TABLE IF NOT EXISTS controle_cargas (
    id_carga STRING NOT NULL,
    data_hora_carga TIMESTAMPTZ NOT NULL DEFAULT now(),
    tabela_destino STRING NOT NULL,
    arquivo_origem STRING NOT NULL,
    tamanho_bytes INT8 NULL,
    hash_arquivo STRING NULL,
    linhas_gravadas INT8 NULL,
    PRIMARY KEY (id_carga, tabela_destino, arquivo_origem)
);

CREATE TABLE IF NOT EXISTS controle_execucao_etl (
    id_execucao STRING PRIMARY KEY,
    data_hora_inicio TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_hora_fim TIMESTAMPTZ NULL,
    status STRING NOT NULL,
    origem STRING NULL,
    modo_carga STRING NULL,
    mensagem STRING NULL
);

CREATE TABLE IF NOT EXISTS auditoria_cargas (
    id_carga STRING NOT NULL,
    tabela_destino STRING NOT NULL,
    regra STRING NOT NULL,
    severidade STRING NOT NULL,
    mensagem STRING NULL,
    valor_referencia STRING NULL,
    data_hora_auditoria TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id_carga, tabela_destino, regra)
);

-- Tabelas principais tipadas. Caso você já tenha tabelas antigas sem primary key,
-- crie tabelas novas com sufixo _v2, faça carga full validada e depois renomeie.
-- É menos glamouroso que "alterar em produção", mas também menos suicida.

CREATE TABLE IF NOT EXISTS f_volume_geral_diario (
    data STRING NOT NULL,
    ano STRING NULL,
    mes STRING NULL,
    dia STRING NULL,
    ano_mes STRING NULL,
    recebidas STRING NULL,
    concluidas_ssrs STRING NULL,
    atendidas STRING NULL,
    nivel_servico_ssrs STRING NULL,
    transferencias STRING NULL,
    taxa_transferencia STRING NULL,
    estouro STRING NULL,
    taxa_estouro STRING NULL,
    tme_segundos STRING NULL,
    total_espera_atendidas_segundos STRING NULL,
    tma_segundos STRING NULL,
    tma_total_segundos STRING NULL,
    falsas_tentativas STRING NULL,
    canceladas STRING NULL,
    abandonadas STRING NULL,
    servico_fechado STRING NULL,
    taxa_atendimento STRING NULL,
    taxa_abandono STRING NULL,
    pasta_origem STRING NULL,
    data_lote STRING NULL,
    mes_referencia STRING NULL,
    id_carga STRING NULL,
    data_hora_carga STRING NULL,
    arquivo_origem STRING NULL,
    hash_arquivo STRING NULL,
    PRIMARY KEY (data)
);

CREATE TABLE IF NOT EXISTS f_volume_fila_diario (
    data STRING NOT NULL,
    fila STRING NOT NULL,
    ano STRING NULL,
    mes STRING NULL,
    dia STRING NULL,
    ano_mes STRING NULL,
    recebidas STRING NULL,
    concluidas_ssrs STRING NULL,
    atendidas STRING NULL,
    transferencias STRING NULL,
    estouro STRING NULL,
    tme_segundos STRING NULL,
    total_espera_atendidas_segundos STRING NULL,
    tma_segundos STRING NULL,
    tma_total_segundos STRING NULL,
    falsas_tentativas STRING NULL,
    canceladas STRING NULL,
    abandonadas STRING NULL,
    servico_fechado STRING NULL,
    taxa_atendimento STRING NULL,
    taxa_abandono STRING NULL,
    pasta_origem STRING NULL,
    data_lote STRING NULL,
    mes_referencia STRING NULL,
    id_carga STRING NULL,
    data_hora_carga STRING NULL,
    arquivo_origem STRING NULL,
    hash_arquivo STRING NULL,
    PRIMARY KEY (data, fila)
);

CREATE TABLE IF NOT EXISTS f_agent_contact_fila_diario (
    data STRING NOT NULL,
    atendente_id STRING NOT NULL,
    grupo STRING NOT NULL,
    fila STRING NOT NULL,
    ano STRING NULL,
    mes STRING NULL,
    dia STRING NULL,
    ano_mes STRING NULL,
    atendente STRING NULL,
    total_ligacoes STRING NULL,
    tma STRING NULL,
    tma_segundos STRING NULL,
    tma_minutos STRING NULL,
    tma_max STRING NULL,
    tma_max_segundos STRING NULL,
    tempo_processamento_total_segundos STRING NULL,
    tempo_pos_processamento_medio STRING NULL,
    tempo_pos_processamento_medio_segundos STRING NULL,
    tempo_pos_processamento_max STRING NULL,
    tempo_pos_processamento_max_segundos STRING NULL,
    tempo_pos_processamento_total_segundos STRING NULL,
    pasta_origem STRING NULL,
    data_lote STRING NULL,
    mes_referencia STRING NULL,
    id_carga STRING NULL,
    data_hora_carga STRING NULL,
    arquivo_origem STRING NULL,
    hash_arquivo STRING NULL,
    PRIMARY KEY (data, atendente_id, grupo, fila)
);

CREATE TABLE IF NOT EXISTS f_agent_contact_diario (
    data STRING NOT NULL,
    atendente_id STRING NOT NULL,
    grupo STRING NOT NULL,
    ano STRING NULL,
    mes STRING NULL,
    dia STRING NULL,
    ano_mes STRING NULL,
    atendente STRING NULL,
    total_ligacoes STRING NULL,
    qtd_filas_atendidas STRING NULL,
    tma STRING NULL,
    tma_segundos STRING NULL,
    tma_minutos STRING NULL,
    tma_max STRING NULL,
    tma_max_segundos STRING NULL,
    tempo_processamento_total_segundos STRING NULL,
    tempo_pos_processamento_medio STRING NULL,
    tempo_pos_processamento_medio_segundos STRING NULL,
    tempo_pos_processamento_max STRING NULL,
    tempo_pos_processamento_max_segundos STRING NULL,
    tempo_pos_processamento_total_segundos STRING NULL,
    pasta_origem STRING NULL,
    data_lote STRING NULL,
    mes_referencia STRING NULL,
    id_carga STRING NULL,
    data_hora_carga STRING NULL,
    arquivo_origem STRING NULL,
    hash_arquivo STRING NULL,
    PRIMARY KEY (data, atendente_id, grupo)
);

-- Views de indicador com cast defensivo. Ajuste nomes/colunas conforme a saída real.
CREATE OR REPLACE VIEW vw_indicadores_contact_center_diario AS
SELECT
    data,
    CAST(NULLIF(recebidas, '') AS INT8) AS recebidas,
    CAST(NULLIF(atendidas, '') AS INT8) AS atendidas,
    CASE WHEN CAST(NULLIF(recebidas, '') AS DECIMAL) = 0 THEN NULL
         ELSE CAST(NULLIF(atendidas, '') AS DECIMAL) / CAST(NULLIF(recebidas, '') AS DECIMAL)
    END AS taxa_atendimento,
    CAST(NULLIF(tme_segundos, '') AS DECIMAL) AS tme_segundos,
    CAST(NULLIF(tma_segundos, '') AS DECIMAL) AS tma_segundos
FROM f_volume_geral_diario;
