"""
edp.lab — Bancada de Contexto (Burp Suite aplicado a LLMs).

Pacote do laboratorio de experimentacao de janelas de contexto. SEPARADO do
runtime de producao: edp/lab importa de edp/runtime e edp/, NUNCA o contrario
(arrancar o lab nao pode quebrar producao — INV-5).

Base: prontuario (store longitudinal) + isolation (sessao de lab dedicada).
Construido para durar tempo indeterminado.
"""
