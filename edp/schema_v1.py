"""
edp.schema_v1 — Schema v1 do EDP (Peça 0.3).

Centraliza vocabulários e constantes do schema temporal/vivencial.
Princípio: strings de schema NUNCA espalhadas pelo código; sempre via constantes daqui.

Decisões de design:
  - Schema versionado (SCHEMA_VERSION). Mudanças futuras incrementam versão.
  - Vocabulários inicialmente FECHADOS (gap_cause, gap_resolution).
  - Expansão sob demanda: peça 2 detecta gap não-classificável, pede ao usuário
    com evidência, usuário aprova expansão.

Camadas (correspondem às 3 perspectivas da peça 0):
  (i)   tempo absoluto       — t_absolute
  (ii)  tempo vivencial      — t_user_*, t_model_*, gap_*
  (iii) origem do conhecimento — origin, t_loaded, source, internal_temporal_claims
"""

from __future__ import annotations

# ───────────────────────────────────────────────────────────────────
# Versão do schema
# ───────────────────────────────────────────────────────────────────

SCHEMA_VERSION = "v1"


# ───────────────────────────────────────────────────────────────────
# Camada (ii) — Tempo vivencial: vocabulário de gaps
# ───────────────────────────────────────────────────────────────────

# Causas do gap (o que aconteceu DURANTE o gap)
GAP_CAUSE_SLEEP        = "sleep"
GAP_CAUSE_MEAL_BREAK   = "meal_break"
GAP_CAUSE_LONG_ABSENCE = "long_absence"

GAP_CAUSES = (
    GAP_CAUSE_SLEEP,
    GAP_CAUSE_MEAL_BREAK,
    GAP_CAUSE_LONG_ABSENCE,
)

# Resoluções do gap (o que aconteceu DEPOIS do gap)
GAP_RES_CONTINUATION         = "continuation"
GAP_RES_ABANDONMENT_URGENCY  = "abandonment_urgency"
GAP_RES_FORGOTTEN            = "forgotten"
GAP_RES_SUBSTITUTION         = "substitution"
GAP_RES_MODEL_RECALL         = "model_recall"
GAP_RES_EXTERNAL_TRIGGER     = "external_trigger"

GAP_RESOLUTIONS = (
    GAP_RES_CONTINUATION,
    GAP_RES_ABANDONMENT_URGENCY,
    GAP_RES_FORGOTTEN,
    GAP_RES_SUBSTITUTION,
    GAP_RES_MODEL_RECALL,
    GAP_RES_EXTERNAL_TRIGGER,
)


# ───────────────────────────────────────────────────────────────────
# Camada (iii) — Origem do conhecimento
# ───────────────────────────────────────────────────────────────────

ORIGIN_MEASURED  = "measured"   # entry gerado por interação real no EDP
ORIGIN_REFERENCE = "reference"  # entry vindo de fonte externa (PDF, TXT, bloco histórico)

ORIGINS = (ORIGIN_MEASURED, ORIGIN_REFERENCE)


# ───────────────────────────────────────────────────────────────────
# Nomes de campos do schema (centralizado)
# ───────────────────────────────────────────────────────────────────

# Camada (i)
FIELD_T_ABSOLUTE = "t_absolute"

# Peça 0.4 (reduzida): sessão do EDP — vitalícia
# Único ID gerado na primeira ignição, persiste até a morte do usuário.
# Distinto das sessões do usuário (login/logout, futuro) e do modelo (peça 2).
FIELD_EDP_SESSION_ID    = "edp_session_id"
FIELD_EDP_SESSION_START = "edp_session_start"

# Peça 2.0: vinculação a bloco
# Bloco é a unidade real de validação. Entry novo é vinculado ao bloco aberto
# atual. Entries antigos (pré-fresh-start) ficam sem block_id (None).
FIELD_BLOCK_ID = "block_id"

# Peça 2.2: câmara de eco
# Quando câmara é ativada para um turno, EDP grava um camara_id no entry,
# apontando para histórico detalhado em sessions/<id>_camara.json
FIELD_CAMARA_ID = "camara_id"  # None se câmara não foi ativada


# ───────────────────────────────────────────────────────────────────
# Peça 2.2 — Checks de refutação (modelo B avalia)
# ───────────────────────────────────────────────────────────────────
#
# Cada check é uma dimensão de avaliação da resposta de A.
# Pesos refletem gravidade da falha:
#   3 = FATAL (confabulação compromete tudo)
#   2 = ALTO (compromete confiança/relação)
#   1 = MÉDIO (estilo, não substância)

CHECK_CONFABULACAO          = "confabulacao"
CHECK_INFLACAO_AVALIATIVA   = "inflacao_avaliativa"
CHECK_CONDESCENDENCIA       = "condescendencia"
CHECK_PROJECAO_SEM_DADO     = "projecao_sem_dado"
CHECK_PERDA_DE_FIO          = "perda_de_fio"
CHECK_COMPLETUDE_FORCADA    = "completude_forcada"
CHECK_ESTRUTURACAO_IMPOSTA  = "estruturacao_imposta"

CHECKS_REFUTACAO = (
    CHECK_CONFABULACAO,
    CHECK_INFLACAO_AVALIATIVA,
    CHECK_CONDESCENDENCIA,
    CHECK_PROJECAO_SEM_DADO,
    CHECK_PERDA_DE_FIO,
    CHECK_COMPLETUDE_FORCADA,
    CHECK_ESTRUTURACAO_IMPOSTA,
)

# Pesos por gravidade
CHECK_PESOS = {
    CHECK_CONFABULACAO:          3,  # fatal
    CHECK_INFLACAO_AVALIATIVA:   2,  # alto
    CHECK_CONDESCENDENCIA:       2,  # alto
    CHECK_PROJECAO_SEM_DADO:     2,  # alto
    CHECK_PERDA_DE_FIO:          2,  # alto
    CHECK_COMPLETUDE_FORCADA:    1,  # médio
    CHECK_ESTRUTURACAO_IMPOSTA:  1,  # médio
}

# Descrições para o prompt de B (passar para o modelo refutador)
CHECK_DESCRICOES = {
    CHECK_CONFABULACAO:
        "Afirmar fatos sem base real (inventar dados, fontes, estatísticas, "
        "ou afirmar com confiança coisas que o modelo não sabe).",
    CHECK_INFLACAO_AVALIATIVA:
        "Embelezamento automático sem substância: 'ótima pergunta!', 'isso é "
        "fascinante!', 'marco histórico', 'trabalho extraordinário'.",
    CHECK_CONDESCENDENCIA:
        "Tom superior (tipo 1 — explicando como se o usuário fosse ingênuo) "
        "ou complacente (tipo 2 — concordando para agradar).",
    CHECK_PROJECAO_SEM_DADO:
        "Assumir estado emocional, intenção, tempo decorrido, ou contexto do "
        "usuário sem que ele tenha dito ('você está cansado', 'você quer X').",
    CHECK_PERDA_DE_FIO:
        "Ignorar algo importante mencionado anteriormente no contexto ou "
        "perder a continuidade da linha de pensamento da conversa.",
    CHECK_COMPLETUDE_FORCADA:
        "Resposta tenta fechar/concluir/dar próximo passo quando deveria "
        "pausar, escutar, ou processar com o usuário.",
    CHECK_ESTRUTURACAO_IMPOSTA:
        "Uso desnecessário de listas, tabelas, marcadores, headers quando a "
        "prosa contínua serviria melhor.",
}

# Camada (ii)
FIELD_T_USER_SESSION_START   = "t_user_session_start"
FIELD_T_USER_TURN_N          = "t_user_turn_n"
FIELD_T_MODEL_CONTEXT_START  = "t_model_context_start"
FIELD_T_MODEL_TURN_N         = "t_model_turn_n"
FIELD_GAP_BEFORE             = "gap_before"
FIELD_GAP_CAUSE              = "gap_cause"
FIELD_GAP_RESOLUTION         = "gap_resolution"

# Camada (iii)
FIELD_ORIGIN                    = "origin"
FIELD_T_LOADED                  = "t_loaded"
FIELD_REFERENCE_SOURCE          = "reference_source"  # nome do arquivo/bloco; renomeado de FIELD_SOURCE para não colidir com `source` de provenance ("user"/"llm"/...) já existente em make_entry
FIELD_INTERNAL_TEMPORAL_CLAIMS  = "internal_temporal_claims"

# Confiabilidade temporal (transversal a todas as camadas)
FIELD_TEMPORAL_UNRELIABLE = "temporal_unreliable"

# Schema version (sempre presente em entries novos)
FIELD_SCHEMA_VERSION = "schema_version"


# ───────────────────────────────────────────────────────────────────
# Defaults para entries novos (medidos no EDP)
# ───────────────────────────────────────────────────────────────────

def measured_entry_defaults() -> dict:
    """
    Retorna dict com campos default do schema v1 para entries 'measured'.

    Campos t_user_*, t_model_*, gap_cause, gap_resolution ficam None até
    serem preenchidos pela peça 2 (detecção de sessões/contextos/gaps).

    edp_session_id e edp_session_start NÃO entram aqui — são preenchidos
    em runtime via memory._get_edp_lifetime() porque dependem do estado
    do EDP, não são defaults estáticos.

    gap_before é calculado no momento de add(); aqui só placeholder.
    temporal_unreliable é setado conforme estado do clock no momento.
    """
    return {
        FIELD_SCHEMA_VERSION:            SCHEMA_VERSION,
        # Camada (ii) — tempo vivencial (preenchimento parcial em peça 0.4)
        FIELD_T_USER_SESSION_START:      None,
        FIELD_T_USER_TURN_N:             None,
        FIELD_T_MODEL_CONTEXT_START:     None,
        FIELD_T_MODEL_TURN_N:            None,
        FIELD_GAP_BEFORE:                None,   # calculado em add()
        FIELD_GAP_CAUSE:                 None,   # peça 2 preenche
        FIELD_GAP_RESOLUTION:            None,   # peça 2 preenche
        # Camada (iii) — origem
        FIELD_ORIGIN:                    ORIGIN_MEASURED,
        FIELD_T_LOADED:                  None,
        FIELD_REFERENCE_SOURCE:          None,
        FIELD_INTERNAL_TEMPORAL_CLAIMS:  None,
        # Confiabilidade temporal
        FIELD_TEMPORAL_UNRELIABLE:       False,  # setado conforme clock
    }


def reference_entry_defaults(source: str, internal_claims: dict = None) -> dict:
    """
    Defaults para entries 'reference' (PDF, TXT, bloco histórico).
    O mecanismo de import propriamente dito vem na peça 4.

    Args:
      source: identificador da fonte (ex: 'historico_pre_relogio_interno_v1',
              'pdf:thesis_2026.pdf')
      internal_claims: dict opcional com tempos declarados internamente
              pelo documento (NÃO é verdade do EDP, apenas metadata)
    """
    return {
        FIELD_SCHEMA_VERSION:            SCHEMA_VERSION,
        # Camada (ii) — tempo vivencial não se aplica
        FIELD_T_USER_SESSION_START:      None,
        FIELD_T_USER_TURN_N:             None,
        FIELD_T_MODEL_CONTEXT_START:     None,
        FIELD_T_MODEL_TURN_N:            None,
        FIELD_GAP_BEFORE:                None,
        FIELD_GAP_CAUSE:                 None,
        FIELD_GAP_RESOLUTION:            None,
        # Camada (iii) — origem
        FIELD_ORIGIN:                    ORIGIN_REFERENCE,
        FIELD_T_LOADED:                  None,   # será preenchido com edp.now()
        FIELD_REFERENCE_SOURCE:          source,
        FIELD_INTERNAL_TEMPORAL_CLAIMS:  internal_claims or {},
        # Confiabilidade temporal não se aplica a referências
        FIELD_TEMPORAL_UNRELIABLE:       False,
    }


# ───────────────────────────────────────────────────────────────────
# Validação
# ───────────────────────────────────────────────────────────────────

def is_valid_gap_cause(value) -> bool:
    """True se valor é gap_cause conhecido ou None."""
    return value is None or value in GAP_CAUSES


def is_valid_gap_resolution(value) -> bool:
    """True se valor é gap_resolution conhecido ou None."""
    return value is None or value in GAP_RESOLUTIONS


def is_valid_origin(value) -> bool:
    """True se origin é válido."""
    return value in ORIGINS
