# resilient_http

Wrapper sobre [httpx](https://www.python-httpx.org/) com quatro padrões de resiliência —
**retry com backoff exponencial + jitter**, **rate limiting por token bucket**,
**timeouts configuráveis** (por fase e prazo total) e **circuit breaker** — em versões
síncrona e assíncrona, com um dashboard visual para ver tudo funcionando.

```python
from resilient_http import (ResilientClient, RetryPolicy, RateLimitConfig,
                            CircuitBreakerConfig, TimeoutConfig, CircuitOpenError)

with ResilientClient(
    "https://api.exemplo.com",
    retry=RetryPolicy(max_attempts=4, base_delay=0.2, jitter="full"),
    rate_limit=RateLimitConfig(rate=10, capacity=20),
    circuit_breaker=CircuitBreakerConfig(failure_threshold=5, recovery_timeout=30),
    timeout=TimeoutConfig(connect=2, read=5, total=15),
    on_event=lambda e: print(e.kind, e.data),
) as client:
    try:
        r = client.get("/pedidos/42")
    except CircuitOpenError as e:
        print(f"serviço fora; tente em {e.retry_after:.0f}s")
```

A versão async é `AsyncResilientClient`, com a mesma API (`await client.get(...)`).

## Rodando

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -e ".[dev]"
pytest                          # ~150 testes, < 1s
python -m resilient_http.demo   # abre o dashboard em http://127.0.0.1:8765
```

## Dashboard (Resilience Lab)

Roda o **cliente real** contra um servidor falso instável (`httpx.MockTransport`) usando
um relógio virtual: 30 s simulados levam milissegundos, e a mesma semente sempre gera o
mesmo resultado. Mostra numa linha do tempo a janela de queda do servidor, os estados do
breaker (fechado / aberto / meio-aberto), o saldo de tokens e cada requisição com suas
tentativas — além de um log de eventos filtrável. Há presets como *Queda longa*,
*Sem breaker* e *Sem jitter* para comparar comportamentos.

## Design

```
resilient_http/
  config.py           políticas imutáveis (dataclasses congelados, validadas)
  clock.py            Clock (Protocol) · SystemClock · FakeClock
  backoff.py          exponencial + jitter (full / equal / none)      ┐
  rate_limiter.py     TokenBucket por reserva                         │ núcleo sans-IO:
  circuit_breaker.py  máquina de estados CLOSED/OPEN/HALF_OPEN        │ não dorme, não
  retry.py            RetryController: decide se/quanto esperar       ┘ chama rede
  client.py           _ResilienceCore + fachadas sync/async (só I/O)
  events.py           Event emitido para um callback opcional
  demo/               simulador, servidor stdlib e dashboard HTML
```

Decisões principais:

- **Sans-IO.** As políticas só *decidem*; quem dorme e chama a rede são as fachadas.
  Por isso `ResilientClient` e `AsyncResilientClient` têm laços de ~20 linhas quase
  idênticos e compartilham 100% da lógica.
- **Relógio injetável.** Testes e simulador usam `FakeClock`: `sleep` só avança o tempo e
  registra a espera, então dá para afirmar `clock.sleeps == [1, 2, 4]`.
- **Token bucket por reserva.** `reserve()` debita na hora (saldo pode ficar negativo) e
  devolve quanto esperar. O lock nunca é mantido durante a espera, o que serve a threads
  e a asyncio, e a ordem de chegada é respeitada.
- **Breaker preguiçoso.** OPEN → HALF_OPEN acontece na próxima consulta após o prazo; não
  há timers nem threads em segundo plano. 4xx não conta como falha; 5xx e erros de
  transporte contam.
- **Retry seguro.** Por padrão só métodos idempotentes são repetidos — exceto quando o
  erro prova que a requisição nunca saiu (`ConnectError`, `ConnectTimeout`,
  `PoolTimeout`), caso em que até `POST` é repetido. `Retry-After` é respeitado (e um valor
  acima de `max_delay` faz desistir em vez de esperar uma hora).
- **Prazo total.** `TimeoutConfig.total` é um orçamento para a operação inteira: nenhum
  retry é agendado se estouraria o prazo, e o timeout de cada tentativa é encurtado para
  caber no que resta.
- **Observabilidade sem acoplamento.** O cliente não faz log; emite `Event`s
  (`attempt`, `response`, `error`, `retry_scheduled`, `gave_up`, `rate_limited`,
  `circuit_rejected`, `circuit_state`) para quem quiser ouvir — o dashboard é só um ouvinte.

## Testes

- `test_backoff.py`, `test_rate_limiter.py`, `test_circuit_breaker.py` — políticas puras
  com `FakeClock`, sem rede.
- `test_client.py` — integração com `httpx.MockTransport` e um `ScriptedServer` que
  responde seguindo um roteiro (`[503, ConnectError(...), 200]`), cobrindo sync e async.
- `test_demo.py` — determinismo do simulador e o servidor HTTP de ponta a ponta.
