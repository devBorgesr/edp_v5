"""
filters.py — Filtros de pré-processamento do EDP v3.
Interface idêntica ao v2. Adiciona normalização unicode.
"""
import re
import unicodedata

# ── Whitelist ──────────────────────────────────────────────────────────────────
WHITELIST: set[str] = {
    "rag","llm","llms","nlp","faiss","bert","gpt","pytorch",
    "tensorflow","transformer","transformers","embedding","embeddings",
    "retrieval","lora","rlhf","quantização","finetuning","tokenizer",
    "tokenização","inferência","similaridade","langchain","python",
    "frameworks","framework","chunk","chunks","pipeline","corpus",
    "benchmark","latência","precisão","recall","hnsw","cache","kv",
    "attention","flash","gpu","gpus","sparse","dense","rerank","bm25",
    "semantic","semântico","semântica","moe","mixtral","vllm","qlora",
    "speculative","decoding","distillation","knowledge","sentence",
}

def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFC", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Cc" or c in "\n\t")

def linha_whitelisted(texto: str) -> bool:
    palavras = set(re.findall(r"\b\w+\b", texto.lower()))
    return bool(palavras & WHITELIST)

# ── Spam ───────────────────────────────────────────────────────────────────────
SPAM_RULES: list[tuple] = [
    (r"comprar?\s+\w+\s+barato",       "compra-barato",         True),
    (r"clique\s+aqui",                 "clique-aqui",           True),
    (r"oferta\s+limitada",             "oferta-limitada",       True),
    (r"frete\s+gr[aá]tis",             "frete-gratis",          True),
    (r"compre\s+agora",                "compre-agora",          True),
    (r"lorem\s+ipsum",                 "lorem-ipsum",           True),
    (r"!!!+",                          "exclamacoes",           False),
    (r"\b[A-Z]{6,}\b",                 "maiusculas-excessivas", False),
    (
        r"\b(?=[a-zA-Z]{6,}\b)"
        r"(?:[^aeiouáéíóúâêîôûãõàèìòùAEIOUÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙ\W\d]){6,}\b",
        "sem-vogal",
        False,
    ),
]

def classificar_spam(texto: str) -> tuple[bool, str | None]:
    if linha_whitelisted(texto):
        return False, None
    for padrao, nome, icase in SPAM_RULES:
        flags = re.IGNORECASE if icase else 0
        if re.search(padrao, texto, flags):
            return True, nome
    return False, None

# ── Vocabulário ────────────────────────────────────────────────────────────────
VOCAB: set[str] = {
    "de","a","o","que","e","do","da","em","um","para","com","uma",
    "os","no","se","na","por","mais","as","dos","como","mas","ao",
    "ele","das","seu","sua","ou","quando","muito","nos","já","também",
    "pelo","pela","foi","são","está","era","tem","ter","ser","isso",
    "esse","esta","entre","sobre","após","durante","vez","parte",
    "sendo","apenas","mesmo","mesma","rag","llm","llms","ia","python",
    "modelo","modelos","dados","sistema","contexto","tokens","token",
    "busca","texto","embedding","embeddings","vetorial","vetoriais",
    "compressão","memória","transformer","transformers","nlp","faiss",
    "retrieval","reduz","reduzem","melhora","permite","utiliza",
    "amplamente","semântica","semântico","eficiente","significado",
    "custo","computacional","inteligência","artificial","frameworks",
    "framework","pytorch","tensorflow","pesquisa","bancos","atenção",
    "tarefas","alucinações","erros","parâmetros","quantização","lora",
    "inferência","treinamento","camadas","rlhf","bert","gpt","geração",
    "similaridade","recuperação","aumentada","pipeline","chunk","chunks",
    "indexação","corpus","vetores","tokenização","tokenizer","latência",
    "precisão","recall","benchmark","avaliação","ciência","utilizado",
    "usado","aceleram","melhoraram","permitem","utilizam","reduzem",
    "contextual","mantendo","capacidade","original","escala","dinâmico",
    "paralelo","qualidade","saída","resultado","matemático","linear",
    "complexidade","sparse","attention","flash","sram","gpu","gpus",
    "hnsw","cache","kv","prefill","decode","batching","continuous",
    "throughput","magnitude","pruning","structured","cabeças","hardware",
    "matrizes","rank","bilhões","milhões","treináveis","distância",
    "coseno","otimiza","cálculo","aluno","professor","replicar",
    "distribuição","recupera","relevante","menores","gigantes","fração",
    "escalar","experts","mixture","moe","mixtral","longformer","bigbird",
    "vllm","qlora","speculative","decoding","distillation","knowledge",
    "sentence","dimensão","relações","semânticas","corpora","documentos",
    "float32","int8","int4","tamanho","processado","infraestrutura",
    "significativa","exigem",
}

def classificar_lixo(texto: str) -> tuple[bool, str | None]:
    if linha_whitelisted(texto):
        return False, None
    palavras = re.findall(r"\b[a-zA-ZÀ-ÿ]{3,}\b", texto.lower())
    if not palavras:
        return True, "sem-palavras"
    ratio = sum(1 for p in palavras if p in VOCAB) / len(palavras)
    return (True, f"vocab-ratio={ratio:.2f}") if ratio < 0.15 else (False, None)

def preprocessar(text: str) -> tuple[list[str], str, list[dict]]:
    """Filtra spam e lixo linha a linha. Retorna (linhas_limpas, texto_unido, logs)."""
    text   = _normalizar(text)
    linhas: list[str] = []
    logs:   list[dict] = []

    for linha in text.split("\n"):
        l = linha.strip()
        if not l:
            continue
        is_spam, sr = classificar_spam(l)
        if is_spam:
            logs.append({"linha": l[:50], "action": "drop-spam", "reason": sr})
            continue
        is_lixo, lr = classificar_lixo(l)
        if is_lixo:
            logs.append({"linha": l[:50], "action": "drop-lixo", "reason": lr})
            continue
        linhas.append(l if l[-1] in ".!?" else l + ".")

    return linhas, " ".join(linhas), logs

