"""
edp.echo_chamber — Câmara de eco (Peça 2.2).

Princípio (Trindade sem divindade):
  - Pai = Usuário, Filho = Modelo, Espírito = EDP (espelho)
  - Câmara: Modelo A gera, Modelo(s) B refuta(m), A avalia reformulação.
  - EDP escolhe o texto vencedor por contagem de checks + peso de gravidade.

Fluxo (sem streaming na peça 2.2):
  1. A gera resposta completa
  2. B refuta com 7 checks + reformulação
  3. A avalia reformulação de B (vendo os checks)
  4. EDP escolhe vencedor por score (PASS × peso)
  5. Histórico vai para sessions/<id>_camara.json
  6. Entry ganha camara_id

Falhas → degradação graciosa: se qualquer passo falha, fallback para A solo.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Callable

from .clock import now as _now
from . import schema_v1 as _schema

logger = logging.getLogger("edp.echo_chamber")


# ─────────────────────────────────────────────────────────────────────
# Prompts da câmara
# ─────────────────────────────────────────────────────────────────────

# Prompt fixo de ceticismo (sempre injetado em A e B)
#
# Peça 2.3: refinado para incluir honestidade + transparência sobre dificuldade.
# Quando o modelo reconhece que uma resposta beira especulação, ele DEVE
# admitir explicitamente em vez de fabricar resposta confiante. O EDP detecta
# léxicamente essa admissão e ativa câmara de eco (modelo B reformula).
#
# Princípio (Trindade): Filho instruído opera com ceticismo → admite limite →
# Espírito ouve admissão → ativa câmara → B refina. EDP-Espírito não julga
# "isso é confabulação?", apenas reflete a instrução fielmente e responde ao
# sinal interno do Filho.
CETICISMO_DEFAULT = """\
Importante: opere com ceticismo honesto e transparência radical.

CETICISMO — sobre o que recebe:
- Não infle avaliações ("ótima pergunta", "isso é fascinante")
- Não projete sobre o usuário sem dado
- Não force completude quando deveria pausar
- Estruture só quando a estrutura realmente ajuda — prosa contínua é frequentemente melhor

HONESTIDADE — sobre o que entrega:
- Marque claramente o que você SABE vs o que está chutando
- Quando não tem base sólida para responder com confiança, ADMITA IMEDIATAMENTE
- Frases naturais para admitir limite são essas (use exatamente uma delas):
  • "Não consigo responder isso com confiança — seria especulação minha."
  • "Não tenho base sólida para afirmar isso."
  • "Isso está além do que posso afirmar com honestidade."
- Após admitir, pare. Não tente uma resposta especulativa "por via das dúvidas".
- O EDP detectará sua admissão e consultará outro modelo mais capaz para refinar.

A honestidade sobre seus limites é mais valiosa que qualquer resposta fabricada.
"""


# ─────────────────────────────────────────────────────────────────────
# Peça 2.3: detecção de auto-sinal de limite
# ─────────────────────────────────────────────────────────────────────
#
# Quando o modelo A admite que não consegue responder com confiança, ele usa
# uma das frases padronizadas no CETICISMO_DEFAULT. Este detector identifica
# a admissão e sinaliza ativação da câmara.
#
# Não é filtro: é GATILHO. O EDP não decide "isso é confabulação" — apenas
# detecta quando o próprio modelo já disse "não consigo".

import re as _re

AUTO_SINAL_LIMITE_REGEX = _re.compile(
    # Frases padronizadas (alta confiança)
    r"(?:n[ãa]o\s+consigo\s+responder\s+(?:isso|essa)\s+com\s+confian[çc]a"
    r"|n[ãa]o\s+tenho\s+base\s+s[óo]lida\s+para\s+afirmar"
    r"|isso\s+est[áa]\s+al[ée]m\s+do\s+que\s+posso\s+afirmar\s+com\s+honestidade"
    # Variações naturais (média confiança — modelo pode parafrasear)
    r"|seria\s+especula[çc][ãa]o\s+(?:minha|de\s+minha\s+parte)"
    r"|n[ãa]o\s+consigo\s+(?:afirmar|garantir)\s+(?:isso|essa)\s+com\s+confian[çc]a"
    r"|n[ãa]o\s+tenho\s+(?:dados|informa[çc][ãa]o|base)\s+suficiente"
    r"|al[ée]m\s+do\s+que\s+posso\s+(?:afirmar|responder)\s+com\s+honestidade"
    r")",
    _re.IGNORECASE,
)


def detectar_auto_sinal_de_limite(texto: str) -> dict:
    """
    Detecta se o modelo A admitiu limite na resposta gerada.

    Resultado dispara câmara de eco — modelo B mais capaz reformula.

    Args:
        texto: resposta gerada por A (pode ser texto completo ou primeiros chunks)

    Returns:
        dict com:
            - detectado: bool
            - trecho: str — trecho exato detectado (ou "" se nada)
            - confianca: "alta" | "media" | None
              alta = frase padronizada exata do CETICISMO_DEFAULT
              media = variação natural que ainda denota admissão
    """
    if not texto:
        return {"detectado": False, "trecho": "", "confianca": None}

    match = AUTO_SINAL_LIMITE_REGEX.search(texto)
    if not match:
        return {"detectado": False, "trecho": "", "confianca": None}

    trecho = match.group(0)
    trecho_lower = trecho.lower()

    # Classifica confiança da detecção
    frases_alta = [
        "não consigo responder",
        "nao consigo responder",
        "não tenho base sólida",
        "nao tenho base solida",
        "além do que posso afirmar com honestidade",
        "alem do que posso afirmar com honestidade",
    ]
    confianca = "alta" if any(f in trecho_lower for f in frases_alta) else "media"

    return {
        "detectado": True,
        "trecho":    trecho,
        "confianca": confianca,
    }


def _construir_prompt_B(
    contexto_original: str,
    texto_A: str,
    modelo_B_nome: str,
) -> str:
    """
    Prompt para o Modelo B refutar o texto de A.

    B recebe: mesmo contexto que A + texto de A + 7 checks específicos.
    B deve: marcar cada check como PASS/FAIL com justificativa breve + reformular.
    """
    checks_lista = []
    for i, check_id in enumerate(_schema.CHECKS_REFUTACAO, start=1):
        descr = _schema.CHECK_DESCRICOES[check_id]
        peso = _schema.CHECK_PESOS[check_id]
        checks_lista.append(f"{i}. **{check_id}** (peso {peso}): {descr}")

    checks_texto = "\n".join(checks_lista)

    return f"""\
{CETICISMO_DEFAULT}

# Sua tarefa

Você é o Modelo B na câmara de eco. Um outro modelo (A) gerou uma resposta. \
Seu papel é refutar honestamente, marcando defeitos específicos, e propor \
uma reformulação que corrige esses defeitos.

# Contexto original (o que A recebeu)

{contexto_original}

# Texto que A gerou

{texto_A}

# Sua avaliação

Para cada check abaixo, responda PASS (sem o defeito) ou FAIL (com o defeito) \
com justificativa breve em 1 frase.

{checks_texto}

# Sua reformulação

Reescreva a resposta corrigindo os defeitos que você marcou como FAIL. Se \
todos os checks foram PASS, escreva: "Reformulação não necessária — texto \
de A está limpo."

# Formato de resposta

Use EXATAMENTE este formato:

CHECKS:
1. confabulacao: PASS|FAIL — justificativa
2. inflacao_avaliativa: PASS|FAIL — justificativa
3. condescendencia: PASS|FAIL — justificativa
4. projecao_sem_dado: PASS|FAIL — justificativa
5. perda_de_fio: PASS|FAIL — justificativa
6. completude_forcada: PASS|FAIL — justificativa
7. estruturacao_imposta: PASS|FAIL — justificativa

REFORMULACAO:
[texto reformulado, ou "Reformulação não necessária — texto de A está limpo."]
"""


def _construir_prompt_A_avaliar_B(
    contexto_original: str,
    texto_A: str,
    texto_reformulado_B: str,
    checks_B: dict,
) -> str:
    """
    Prompt para A avaliar a reformulação de B.

    A vê: contexto original + texto próprio + texto reformulado por B + checks que B marcou.
    A deve: % de concordância com a reformulação + indicar onde discorda.
    """
    checks_str = "\n".join(
        f"- {k}: {v.get('verdict', '?')} ({v.get('justificativa', '')[:80]})"
        for k, v in checks_B.items()
    )

    return f"""\
{CETICISMO_DEFAULT}

# Sua tarefa

Você é o Modelo A na câmara de eco. Você gerou uma resposta inicial. Um outro \
modelo (B) refutou seu texto e propôs uma reformulação. Avalie honestamente \
se concorda com a reformulação de B.

# Contexto original

{contexto_original}

# Texto que VOCÊ (A) gerou

{texto_A}

# Avaliação de B sobre seu texto

{checks_str}

# Reformulação proposta por B

{texto_reformulado_B}

# Sua avaliação

Responda EXATAMENTE neste formato:

CONCORDANCIA: <0-100>%
DISCORDANCIA_PRINCIPAL: [se concordância < 100, descreva em 1-2 frases onde \
você acha que B errou ou exagerou. Se concordância = 100%, escreva "nenhuma".]
TEXTO_FINAL: [escreva o texto final que você considera o melhor. Pode ser \
exatamente o de B, pode ser misto, pode ser uma terceira versão. Este é o \
texto que vai para o usuário.]
"""


# ─────────────────────────────────────────────────────────────────────
# Parsing das respostas
# ─────────────────────────────────────────────────────────────────────

def _parse_resposta_B(texto: str) -> dict:
    """
    Parseia a resposta estruturada de B.

    Returns:
        dict com:
            - checks: dict[check_id, {verdict: PASS|FAIL, justificativa: str}]
            - reformulacao: str (ou "" se não houve)
            - parse_ok: bool
    """
    result = {
        "checks": {},
        "reformulacao": "",
        "parse_ok": False,
    }

    if not texto:
        return result

    # Divide em seções
    partes_lower = texto.lower()
    idx_checks = partes_lower.find("checks:")
    idx_reform = partes_lower.find("reformulacao:")
    if idx_reform == -1:
        idx_reform = partes_lower.find("reformulação:")

    if idx_checks == -1:
        # Sem estrutura — parse falhou
        return result

    secao_checks = texto[idx_checks:idx_reform] if idx_reform != -1 else texto[idx_checks:]
    secao_reform = texto[idx_reform:] if idx_reform != -1 else ""

    # Parse checks
    for check_id in _schema.CHECKS_REFUTACAO:
        # Procura linha tipo: "X. check_id: PASS|FAIL — justificativa"
        linhas = secao_checks.split("\n")
        for linha in linhas:
            linha_lower = linha.lower()
            if check_id in linha_lower:
                # Tem PASS ou FAIL?
                if "fail" in linha_lower:
                    verdict = "FAIL"
                elif "pass" in linha_lower:
                    verdict = "PASS"
                else:
                    continue
                # Extrai justificativa (depois do "—" ou "-")
                just = ""
                if "—" in linha:
                    just = linha.split("—", 1)[1].strip()
                elif " - " in linha:
                    just = linha.split(" - ", 1)[1].strip()
                result["checks"][check_id] = {
                    "verdict": verdict,
                    "justificativa": just,
                }
                break

    # Parse reformulação
    if secao_reform:
        # Remove cabeçalho "REFORMULACAO:" ou similar
        for prefix in ["reformulacao:", "reformulação:", "REFORMULACAO:", "REFORMULAÇÃO:"]:
            if secao_reform.lower().startswith(prefix.lower()):
                secao_reform = secao_reform[len(prefix):].strip()
                break
        result["reformulacao"] = secao_reform.strip()

    # Parse_ok se pelo menos alguns checks foram parseados
    result["parse_ok"] = len(result["checks"]) >= 4
    return result


def _parse_resposta_A_avaliacao(texto: str) -> dict:
    """
    Parseia a avaliação de A sobre a reformulação de B.

    Returns:
        dict com:
            - concordancia: int (0-100)
            - discordancia: str
            - texto_final: str
            - parse_ok: bool
    """
    result = {
        "concordancia": 0,
        "discordancia": "",
        "texto_final": "",
        "parse_ok": False,
    }
    if not texto:
        return result

    texto_lower = texto.lower()

    # Concordância
    idx_concord = texto_lower.find("concordancia:")
    if idx_concord == -1:
        idx_concord = texto_lower.find("concordância:")
    if idx_concord != -1:
        # Procura número seguinte
        trecho = texto[idx_concord:idx_concord + 100]
        import re
        m = re.search(r"(\d{1,3})\s*%?", trecho)
        if m:
            result["concordancia"] = min(100, int(m.group(1)))

    # Discordância
    idx_disc = texto_lower.find("discordancia_principal:")
    if idx_disc == -1:
        idx_disc = texto_lower.find("discordância_principal:")
    idx_final = texto_lower.find("texto_final:")

    if idx_disc != -1:
        end_disc = idx_final if idx_final != -1 else len(texto)
        trecho_disc = texto[idx_disc:end_disc]
        # Remove cabeçalho
        for prefix in ["discordancia_principal:", "discordância_principal:",
                       "DISCORDANCIA_PRINCIPAL:", "DISCORDÂNCIA_PRINCIPAL:"]:
            if trecho_disc.lower().startswith(prefix.lower()):
                trecho_disc = trecho_disc[len(prefix):].strip()
                break
        result["discordancia"] = trecho_disc.strip()

    # Texto final
    if idx_final != -1:
        trecho_final = texto[idx_final:]
        for prefix in ["texto_final:", "TEXTO_FINAL:"]:
            if trecho_final.lower().startswith(prefix.lower()):
                trecho_final = trecho_final[len(prefix):].strip()
                break
        result["texto_final"] = trecho_final.strip()

    result["parse_ok"] = (
        result["concordancia"] > 0 or
        len(result["texto_final"]) > 10
    )
    return result


# ─────────────────────────────────────────────────────────────────────
# Cálculo de score
# ─────────────────────────────────────────────────────────────────────

def calcular_score(checks: dict) -> dict:
    """
    Calcula score do texto baseado nos checks de B.

    score = sum(pesos onde PASS) - sum(pesos onde FAIL)

    Score máximo possível: sum(todos os pesos) = 3+2+2+2+2+1+1 = 13
    Score mínimo possível: -13

    Args:
        checks: dict[check_id, {verdict, justificativa}]

    Returns:
        dict com:
            - score: int
            - max_score: int (13)
            - pass_count: int
            - fail_count: int
            - fails_pesados: list[check_id] (FAILs com peso >= 2)
    """
    score = 0
    pass_count = 0
    fail_count = 0
    fails_pesados = []

    for check_id, peso in _schema.CHECK_PESOS.items():
        info = checks.get(check_id)
        if not info:
            continue
        verdict = info.get("verdict", "").upper()
        if verdict == "PASS":
            score += peso
            pass_count += 1
        elif verdict == "FAIL":
            score -= peso
            fail_count += 1
            if peso >= 2:
                fails_pesados.append(check_id)

    return {
        "score":         score,
        "max_score":     sum(_schema.CHECK_PESOS.values()),
        "pass_count":    pass_count,
        "fail_count":    fail_count,
        "fails_pesados": fails_pesados,
    }


# ─────────────────────────────────────────────────────────────────────
# Estrutura do registro de câmara
# ─────────────────────────────────────────────────────────────────────

@dataclass
class CamaraRecord:
    """Histórico detalhado de uma execução de câmara."""
    id: str
    timestamp: float
    edp_session_id: str
    block_id: Optional[str]
    entry_id: Optional[str]
    user_message: str
    modelo_A: str
    modelo_B: str
    texto_A: str
    texto_B_resposta_completa: str
    checks_B: dict
    score_texto_A: dict
    texto_final: str
    concordancia_A_com_B: int
    discordancia_principal: str
    venceu: str  # "A" ou "B" ou "ambos_similar"
    custo_total_usd: float
    latencia_total_ms: int
    fallback_para_A_solo: bool = False
    erro: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────
# EchoChamber — orquestrador
# ─────────────────────────────────────────────────────────────────────

class EchoChamber:
    """
    Orquestra uma execução da câmara de eco.

    Uso típico:
        chamber = EchoChamber(session_id, base_dir, llm_caller)
        resultado = chamber.executar(
            user_message=msg,
            contexto_completo=ctx,
            modelo_A="claude-haiku-4-5",
            modelo_B="claude-sonnet-4-6",
            edp_session_id=...,
            block_id=...,
        )
        # resultado["texto_final"] é o que vai pro usuário
        # resultado["camara_id"] é o ID do registro persistido
    """

    def __init__(
        self,
        session_id: str,
        base_dir: Path,
        llm_caller: Callable[[str, str, str], dict],
    ):
        """
        Args:
            session_id: id da sessão
            base_dir: diretório de persistência
            llm_caller: função que faz a chamada LLM síncrona.
                Assinatura: llm_caller(modelo, prompt_system, prompt_user) -> dict
                Retorno esperado: {"text": str, "tokens_in": int, "tokens_out": int,
                                  "cost_usd": float, "latency_ms": int}
        """
        self.session_id = session_id
        self.path = base_dir / f"{session_id}_camara.json"
        self.llm_caller = llm_caller
        self._lock = threading.RLock()
        self.records: list[CamaraRecord] = []
        self._load()

    def _load(self) -> None:
        from .memory import _safe_load_json
        if not self.path.exists():
            return
        try:
            data = _safe_load_json(self.path)
            if data and isinstance(data, list):
                self.records = [CamaraRecord(**r) for r in data]
        except Exception:
            self.records = []

    def save(self) -> None:
        from .memory import _atomic_write_json
        with self._lock:
            data = [r.to_dict() for r in self.records]
            _atomic_write_json(self.path, data, indent=2)

    def executar(
        self,
        user_message: str,
        contexto_completo: str,
        modelo_A: str,
        modelo_B: str,
        edp_session_id: str,
        block_id: Optional[str] = None,
        texto_A_ja_gerado: Optional[str] = None,
    ) -> dict:
        """
        Executa a câmara de eco completa.

        Args:
            user_message: mensagem do usuário (vai no registro)
            contexto_completo: contexto completo que foi enviado para A
            modelo_A: modelo gerador
            modelo_B: modelo refutador (deve estar acima de A na hierarquia)
            edp_session_id: sessão vitalícia do EDP
            block_id: bloco atual (opcional, para metadados)
            texto_A_ja_gerado: se A já gerou (ex: streaming visível ao usuário),
                              passa aqui para evitar chamar A duas vezes

        Returns:
            dict com:
                - sucesso: bool
                - texto_final: str (vai para o usuário)
                - camara_id: str (None se câmara falhou completamente)
                - vencedor: "A" | "B" | "ambos_similar"
                - fallback_para_A_solo: bool
                - custo_total_usd: float
                - latencia_total_ms: int
                - resumo: str (texto curto para log/debug)
        """
        camara_id = str(uuid.uuid4())
        t_start = _now()

        # ─────────────────────────────────────────────────────────────
        # Passo 1: A gera (ou usa texto já gerado)
        # ─────────────────────────────────────────────────────────────
        texto_A = texto_A_ja_gerado
        custo_total = 0.0
        latencia_total_ms = 0

        if not texto_A:
            try:
                logger.info("[camara %s] passo 1: A=%s gerando", camara_id[:8], modelo_A)
                resp_A = self.llm_caller(modelo_A, CETICISMO_DEFAULT, contexto_completo)
                texto_A = resp_A.get("text", "")
                custo_total += resp_A.get("cost_usd", 0.0)
                latencia_total_ms += resp_A.get("latency_ms", 0)
                if not texto_A:
                    raise ValueError("A gerou texto vazio")
            except Exception as e:
                logger.warning("[camara %s] passo 1 falhou: %s", camara_id[:8], e)
                return self._fallback_solo(
                    camara_id, modelo_A, "",
                    user_message, edp_session_id, block_id,
                    erro=f"A falhou: {e}",
                )

        # ─────────────────────────────────────────────────────────────
        # Passo 2: B refuta
        # ─────────────────────────────────────────────────────────────
        try:
            logger.info("[camara %s] passo 2: B=%s refutando", camara_id[:8], modelo_B)
            prompt_B = _construir_prompt_B(contexto_completo, texto_A, modelo_B)
            resp_B = self.llm_caller(modelo_B, CETICISMO_DEFAULT, prompt_B)
            texto_B = resp_B.get("text", "")
            custo_total += resp_B.get("cost_usd", 0.0)
            latencia_total_ms += resp_B.get("latency_ms", 0)
            if not texto_B:
                raise ValueError("B retornou texto vazio")
            parsed_B = _parse_resposta_B(texto_B)
            if not parsed_B["parse_ok"]:
                raise ValueError("parse de B falhou (< 4 checks identificados)")
        except Exception as e:
            logger.warning("[camara %s] passo 2 falhou: %s — fallback A solo",
                           camara_id[:8], e)
            return self._fallback_solo(
                camara_id, modelo_A, texto_A,
                user_message, edp_session_id, block_id,
                custo_parcial=custo_total, latencia_parcial=latencia_total_ms,
                erro=f"B falhou: {e}",
            )

        # ─────────────────────────────────────────────────────────────
        # Passo 3: A avalia reformulação de B
        # ─────────────────────────────────────────────────────────────
        # Se B disse "Reformulação não necessária", pula passo 3
        reformulacao = parsed_B["reformulacao"]
        reform_desnecessaria = (
            not reformulacao or
            "não necessária" in reformulacao.lower() or
            "nao necessaria" in reformulacao.lower()
        )

        score_A = calcular_score(parsed_B["checks"])
        texto_final = texto_A
        vencedor = "A"
        concordancia = 100
        discordancia = "nenhuma"

        if not reform_desnecessaria:
            try:
                logger.info("[camara %s] passo 3: A avalia reformulação de B",
                            camara_id[:8])
                prompt_A_eval = _construir_prompt_A_avaliar_B(
                    contexto_completo, texto_A, reformulacao, parsed_B["checks"]
                )
                resp_A_eval = self.llm_caller(modelo_A, CETICISMO_DEFAULT, prompt_A_eval)
                texto_A_eval = resp_A_eval.get("text", "")
                custo_total += resp_A_eval.get("cost_usd", 0.0)
                latencia_total_ms += resp_A_eval.get("latency_ms", 0)
                parsed_eval = _parse_resposta_A_avaliacao(texto_A_eval)

                concordancia = parsed_eval["concordancia"]
                discordancia = parsed_eval["discordancia"]
                texto_proposto_final = parsed_eval["texto_final"]

                if texto_proposto_final and len(texto_proposto_final) > 20:
                    texto_final = texto_proposto_final
                    # Vencedor: se A concorda >= 70%, considera B venceu (texto reformulado)
                    if concordancia >= 70:
                        vencedor = "B"
                    elif concordancia >= 40:
                        vencedor = "ambos_similar"
                    # else: A venceu (mantém texto original)
                    if vencedor == "A":
                        texto_final = texto_A
                elif reformulacao:
                    # Texto_final não veio, mas reformulação existe
                    texto_final = reformulacao if concordancia >= 70 else texto_A
                    if concordancia >= 70:
                        vencedor = "B"
            except Exception as e:
                logger.warning("[camara %s] passo 3 falhou: %s — usa texto de A",
                               camara_id[:8], e)
                texto_final = texto_A
                vencedor = "A"

        # ─────────────────────────────────────────────────────────────
        # Passo 4: Persiste registro
        # ─────────────────────────────────────────────────────────────
        record = CamaraRecord(
            id=camara_id,
            timestamp=t_start,
            edp_session_id=edp_session_id,
            block_id=block_id,
            entry_id=None,  # Preenchido depois pelo caller
            user_message=user_message[:500],  # trunca para arquivo não inchar
            modelo_A=modelo_A,
            modelo_B=modelo_B,
            texto_A=texto_A,
            texto_B_resposta_completa=texto_B,
            checks_B=parsed_B["checks"],
            score_texto_A=score_A,
            texto_final=texto_final,
            concordancia_A_com_B=concordancia,
            discordancia_principal=discordancia,
            venceu=vencedor,
            custo_total_usd=round(custo_total, 6),
            latencia_total_ms=latencia_total_ms,
            fallback_para_A_solo=False,
        )
        with self._lock:
            self.records.append(record)
            self.save()

        resumo = (
            f"câmara {camara_id[:8]}: {modelo_A}→{modelo_B} | "
            f"vencedor={vencedor} | concord={concordancia}% | "
            f"score_A={score_A['score']}/{score_A['max_score']} | "
            f"fails={score_A['fail_count']} | "
            f"custo=${custo_total:.4f} | lat={latencia_total_ms}ms"
        )
        logger.info("[camara %s] %s", camara_id[:8], resumo)

        return {
            "sucesso":              True,
            "texto_final":          texto_final,
            "camara_id":            camara_id,
            "vencedor":             vencedor,
            "fallback_para_A_solo": False,
            "custo_total_usd":      round(custo_total, 6),
            "latencia_total_ms":    latencia_total_ms,
            "concordancia":         concordancia,
            "score_A":              score_A,
            "resumo":               resumo,
        }

    def _fallback_solo(
        self,
        camara_id: str,
        modelo_A: str,
        texto_A: str,
        user_message: str,
        edp_session_id: str,
        block_id: Optional[str],
        custo_parcial: float = 0.0,
        latencia_parcial: int = 0,
        erro: str = "",
    ) -> dict:
        """Degradação graciosa: registra a tentativa de câmara mas usa A solo."""
        record = CamaraRecord(
            id=camara_id,
            timestamp=_now(),
            edp_session_id=edp_session_id,
            block_id=block_id,
            entry_id=None,
            user_message=user_message[:500],
            modelo_A=modelo_A,
            modelo_B="(fallback)",
            texto_A=texto_A,
            texto_B_resposta_completa="",
            checks_B={},
            score_texto_A={},
            texto_final=texto_A,
            concordancia_A_com_B=0,
            discordancia_principal="",
            venceu="A",
            custo_total_usd=round(custo_parcial, 6),
            latencia_total_ms=latencia_parcial,
            fallback_para_A_solo=True,
            erro=erro,
        )
        with self._lock:
            self.records.append(record)
            self.save()

        return {
            "sucesso":              False,
            "texto_final":          texto_A,
            "camara_id":            camara_id,
            "vencedor":             "A",
            "fallback_para_A_solo": True,
            "custo_total_usd":      round(custo_parcial, 6),
            "latencia_total_ms":    latencia_parcial,
            "erro":                 erro,
            "resumo":               f"câmara {camara_id[:8]}: FALLBACK A solo ({erro})",
        }
