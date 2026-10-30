# Fila Justa — Simulador de prioridade em dia de eleições

Projeto acadêmico em Python, POO, Streamlit e SQLite. Inclui aging, métricas de SLA, dados aleatórios e exportação de auditoria SQL.

**Importante:** categorias, pontos, SLA e cenário são hipotéticos para fins didáticos. Não representam regras oficiais eleitorais e não devem ser usados em uma fila real sem validação jurídica e operacional.

## Executar no Windows
```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```
Se `python` não funcionar, tente `py`.

## Executar no macOS/Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

## Como demonstrar
1. Na aba Entrada, gere 25 eleitores aleatórios.
2. No Painel, confira a fila ordenada, as pontuações e o SLA.
3. Avance o relógio simulado em 20, 30 ou 60 minutos para demonstrar aging.
4. Atenda o próximo eleitor e veja o histórico.
5. Na aba Auditoria SQL, exporte `logs.sql` e `schema.sql`.

## Regra didática
Pessoa com deficiência = 3 pontos-base; pessoa idosa = 3; pessoa comum = 1.
`pontuação = pontos_base × (1 + minutos_de_espera // intervalo_aging)`.
Empate: maior pontuação; preferencial antes de comum; ordem de chegada; ID.
O intervalo padrão é 20 minutos. É possível usar 10 ou 5 para testes.

## Classes
- `Eleitor`: dados da pessoa e cálculo de espera.
- `Atendimento`: registro do atendimento e SLA.
- `PoliticaPrioridade`: pontuação e ordenação.
- `RepositorioAuditoria`: persistência SQLite e exportação SQL.
- `SimuladorFila`: operações da fila, relógio e atendimento.

## Complexidade
Ordenar e atender: O(n log n). Inserção em lista: O(1) amortizado. Espaço da fila: O(n).

## Sugestões de commits
- `feat: implementar classes e política de prioridade`
- `feat: adicionar persistência e auditoria SQL`
- `feat: construir interface Streamlit`
- `test: validar aging, desempate e SLA`
- `docs: documentar instalação e regras`
