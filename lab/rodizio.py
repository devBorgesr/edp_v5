"""
edp.lab.rodizio — O Rodizio da Bancada (Intruder do Burp).

Roda um PLANO de condicoes (cada uma = um formato sobre a mesma janela), N
amostras cada, dentro do isolamento, gravando no prontuario. Genérico: roda
qualquer plano (o exp001 e so um cliente).

NAO e produto cartesiano cego (a armadilha que os revisores cravaram): recebe um
PLANO desenhado (lista de condicoes), nao um `for f in formatos for m in modelos`.

FREIO DE ORCAMENTO (protege o dinheiro, postura "n sempre completo"):
  - Antes de iniciar cada condicao, estima o custo dela (n x custo medio real por
    amostra ate aqui) e so inicia se couber no que resta ate budget_max. Assim
    NENHUMA condicao roda pela metade — ou tem n completo, ou nao roda.
  - budget_max e FREIO DE EMERGENCIA, nao meta: o custo esperado fica bem abaixo
    dele; ele so corta se algo sair muito fora da estimativa.
  - A estimativa se refina com o custo REAL conforme as condicoes rodam (a 1a usa
    um chute inicial conservador).

O Rodizio so ORQUESTRA: cada condicao passa pelo run_variant ja provado (isola ->
distorce -> amostra N -> grava -> no-leak). Dry-run/conexao/armar sao do entry
point (run_once), nao daqui.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .repeater import run_variant

logger = logging.getLogger("edp.lab.rodizio")


@dataclass
class CondicaoResultado:
    rotulo: str
    formato_id: str
    run_id: str
    n_run: int = 0
    n_ok: int = 0
    distinct: int = 0
    cost_usd: float = 0.0
    leak_ok: Optional[bool] = None
    papel: str = ""


@dataclass
class RodizioResultado:
    condicoes: List[CondicaoResultado] = field(default_factory=list)
    n_condicoes_rodadas: int = 0
    n_condicoes_plano: int = 0
    chamadas_totais: int = 0
    custo_total_usd: float = 0.0
    budget_max_usd: float = 2.50
    parou_por: str = "plano_completo"   # "plano_completo" | "budget_freio"
    leak_global_ok: bool = True

    def as_dict(self) -> dict:
        return {
            "condicoes_rodadas": self.n_condicoes_rodadas,
            "condicoes_plano": self.n_condicoes_plano,
            "chamadas_totais": self.chamadas_totais,
            "custo_total_usd": round(self.custo_total_usd, 4),
            "budget_max_usd": self.budget_max_usd,
            "parou_por": self.parou_por,
            "leak_global_ok": self.leak_global_ok,
        }


def run_plan(
    *,
    plano: List[dict],
    sections: dict,
    query: str,
    client,
    modelo: str,
    andaime_base: dict,
    n: int,
    budget_max_usd: float = 2.50,
    cost_max_por_condicao_usd: float = 0.75,
    ceticismo: bool = True,
    require_armed: bool = True,
    store=None,
    prod_session: str = "default",
    verify_isolation: bool = True,
    custo_inicial_por_amostra: float = 0.008,
) -> RodizioResultado:
    """Roda o plano condicao por condicao, com freio de orcamento (n completo).

    plano — lista de condicoes: cada {rotulo, formato, params, papel?}.
    n — amostras POR condicao (igual para todas, p/ comparabilidade).
    budget_max_usd — freio de emergencia (nao meta). Uma condicao so inicia se
                     couber inteira no que resta ate aqui.
    """
    res = RodizioResultado(n_condicoes_plano=len(plano), budget_max_usd=budget_max_usd)
    custo = 0.0
    amostras_vistas = 0
    custo_amostras = 0.0

    for cond in plano:
        # estima o custo desta condicao (n x custo medio real por amostra ate aqui)
        avg = (custo_amostras / amostras_vistas) if amostras_vistas > 0 else custo_inicial_por_amostra
        estimativa = n * avg
        if custo + estimativa > budget_max_usd:
            res.parou_por = "budget_freio"
            logger.warning(
                "[rodizio] FREIO antes de '%s': gasto=$%.4f + estimativa=$%.4f passaria de "
                "budget=$%.2f. Condicao NAO iniciada (n sempre completo).",
                cond["rotulo"], custo, estimativa, budget_max_usd,
            )
            break

        andaime_cond = dict(andaime_base or {})
        andaime_cond["condicao_rotulo"] = cond["rotulo"]
        andaime_cond["condicao_papel"] = cond.get("papel", "")

        rec = run_variant(
            sections=sections, query=query,
            formato=cond["formato"], formato_params=cond.get("params"),
            client=client, modelo=modelo, andaime_base=andaime_cond,
            n=n, cost_max_usd=cost_max_por_condicao_usd, ceticismo=ceticismo,
            require_armed=require_armed, store=store,
            prod_session=prod_session, verify_isolation=verify_isolation,
        )

        custo += rec.cost_usd
        amostras_vistas += rec.n_ok
        custo_amostras += rec.cost_usd
        if rec.leak_ok is False:
            res.leak_global_ok = False

        res.condicoes.append(CondicaoResultado(
            rotulo=cond["rotulo"], formato_id=rec.formato_id, run_id=rec.run_id,
            n_run=rec.n_run, n_ok=rec.n_ok, distinct=rec.distinct,
            cost_usd=rec.cost_usd, leak_ok=rec.leak_ok, papel=cond.get("papel", ""),
        ))
        res.n_condicoes_rodadas += 1
        res.chamadas_totais += rec.n_run

    res.custo_total_usd = custo
    logger.info(
        "[rodizio] plano: %d/%d condicoes | %d chamadas | $%.4f | %s | leak_global_ok=%s",
        res.n_condicoes_rodadas, res.n_condicoes_plano, res.chamadas_totais,
        res.custo_total_usd, res.parou_por, res.leak_global_ok,
    )
    return res
