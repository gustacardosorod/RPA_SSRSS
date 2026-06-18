-- Schema base do projeto RPA_SSRS no CockroachDB.
-- Rode este script no SQL Shell do CockroachDB ou use a tela "🗄️ Banco CockroachDB" do Streamlit.

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

-- As tabelas finais são criadas automaticamente pelo app a partir dos CSVs gerados:
-- dim_atendentes
-- f_agent_contact_diario
-- f_css_atendente
-- f_css_geral_diario
-- f_fsr_tratado
-- f_indicadores_gerais
-- f_reclamacoes_sap_tratado
-- f_volume_fila_diario
-- f_volume_geral_diario
-- f_gov_chamados_tratado
-- dim_status_chamados
-- dim_unidades_chamados
-- dim_responsaveis_chamados
-- dim_categorias_chamados
