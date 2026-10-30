from __future__ import annotations
import json, random, sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DB = ROOT / "fila_eleicoes.db"

@dataclass
class Eleitor:
    id: int
    nome: str
    categoria: str
    chegada: datetime
    observacao: str = ""

    def espera(self, agora: datetime) -> int:
        return max(0, int((agora - self.chegada).total_seconds() // 60))

@dataclass
class Atendimento:
    eleitor_id: int
    nome: str
    categoria: str
    chegada: datetime
    instante: datetime
    espera_min: int
    pontuacao: int
    sla_min: int
    dentro_sla: bool

class PoliticaPrioridade:
    """Pontuação com aging: pontos-base * (1 + blocos completos de intervalo)."""
    BASE = {"Pessoa com deficiência": 3, "Pessoa idosa": 3, "Pessoa comum": 1}
    PREFERENCIA = {"Pessoa com deficiência": 0, "Pessoa idosa": 0, "Pessoa comum": 1}

    def __init__(self, intervalo=20):
        self.intervalo = max(1, int(intervalo))

    def pontuacao(self, eleitor: Eleitor, agora: datetime) -> int:
        return self.BASE[eleitor.categoria] * (1 + eleitor.espera(agora) // self.intervalo)

    def chave(self, eleitor: Eleitor, agora: datetime) -> tuple:
        # Ordenação: maior pontuação, preferencial em empate, chegada, ID.
        return (-self.pontuacao(eleitor, agora),
                self.PREFERENCIA[eleitor.categoria],
                eleitor.chegada.timestamp(), eleitor.id)

class RepositorioAuditoria:
    """Persistência transacional de entradas e saídas em SQLite e exportação SQL."""
    def __init__(self, caminho=DB):
        self.caminho = caminho
        with self.conectar() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS auditoria(
                id INTEGER PRIMARY KEY AUTOINCREMENT, evento TEXT NOT NULL,
                eleitor_id INTEGER NOT NULL, nome TEXT NOT NULL, categoria TEXT NOT NULL,
                instante TEXT NOT NULL, detalhes TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS atendimentos(
                id INTEGER PRIMARY KEY AUTOINCREMENT, eleitor_id INTEGER NOT NULL,
                nome TEXT NOT NULL, categoria TEXT NOT NULL, chegada TEXT NOT NULL,
                instante TEXT NOT NULL, espera_min INTEGER NOT NULL, pontuacao INTEGER NOT NULL,
                sla_min INTEGER NOT NULL, dentro_sla INTEGER NOT NULL)""")

    def conectar(self):
        c = sqlite3.connect(self.caminho)
        c.row_factory = sqlite3.Row
        return c

    def evento(self, tipo, e, instante, detalhes):
        with self.conectar() as c:
            c.execute("INSERT INTO auditoria(evento,eleitor_id,nome,categoria,instante,detalhes) VALUES(?,?,?,?,?,?)",
                      (tipo, e.id, e.nome, e.categoria, instante.isoformat(timespec="seconds"),
                       json.dumps(detalhes, ensure_ascii=False)))

    def atendimento(self, a: Atendimento):
        with self.conectar() as c:
            c.execute("""INSERT INTO atendimentos
                (eleitor_id,nome,categoria,chegada,instante,espera_min,pontuacao,sla_min,dentro_sla)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (a.eleitor_id,a.nome,a.categoria,a.chegada.isoformat(timespec="seconds"),
                 a.instante.isoformat(timespec="seconds"),a.espera_min,a.pontuacao,a.sla_min,int(a.dentro_sla)))

    def tabela(self, nome):
        with self.conectar() as c:
            return pd.read_sql_query(f"SELECT * FROM {nome} ORDER BY id DESC", c)

    def exportar_sql(self):
        # Produz SQL executável com os registros atuais.
        with self.conectar() as c:
            eventos = c.execute("SELECT * FROM auditoria ORDER BY id").fetchall()
            atendimentos = c.execute("SELECT * FROM atendimentos ORDER BY id").fetchall()
        def q(v):
            if v is None: return "NULL"
            if isinstance(v, (int, float)): return str(v)
            return "'" + str(v).replace("'", "''") + "'"
        out = ["-- Exportação do Simulador Fila Justa", "BEGIN TRANSACTION;"]
        for r in eventos:
            cols = ["id","evento","eleitor_id","nome","categoria","instante","detalhes"]
            out.append("INSERT INTO auditoria("+",".join(cols)+") VALUES ("+
                       ",".join(q(r[k]) for k in cols)+");")
        for r in atendimentos:
            cols = ["id","eleitor_id","nome","categoria","chegada","instante","espera_min","pontuacao","sla_min","dentro_sla"]
            out.append("INSERT INTO atendimentos("+",".join(cols)+") VALUES ("+
                       ",".join(q(r[k]) for k in cols)+");")
        out.append("COMMIT;")
        return "\\n".join(out)+"\\n"

class SimuladorFila:
    CATEGORIAS = list(PoliticaPrioridade.BASE)
    def __init__(self, repo):
        self.repo = repo
        self.politica = PoliticaPrioridade()
        self.fila = []
        self.agora = datetime.now().replace(second=0, microsecond=0)
        self.proximo_id = 1
        self.ultimo = None

    def adicionar(self, nome, categoria, observacao=""):
        e = Eleitor(self.proximo_id, nome.strip(), categoria, self.agora, observacao.strip())
        self.proximo_id += 1
        self.fila.append(e)
        self.repo.evento("ENTRADA", e, self.agora, {"origem":"manual"})
        return e

    def gerar_aleatorios(self, n, semente):
        rng = random.Random(semente)
        nomes = ["Ana","Bruno","Camila","Diego","Elisa","Felipe","Giovana","Henrique","Isabela","João",
                 "Karina","Lucas","Marina","Nicolas","Olívia","Pedro","Rafaela","Samuel","Tainá","Vitor"]
        for i in range(n):
            cat = rng.choices(self.CATEGORIAS, weights=[2,3,5], k=1)[0]
            e = Eleitor(self.proximo_id, f"{rng.choice(nomes)} {self.proximo_id}", cat,
                        self.agora - timedelta(minutes=rng.randint(0,120)), "Dado fictício")
            self.proximo_id += 1
            self.fila.append(e)
            self.repo.evento("ENTRADA_SIMULADA", e, self.agora,
                             {"origem":"aleatória","pontuacao":self.politica.pontuacao(e,self.agora)})

    def ordenar(self):
        return sorted(self.fila, key=lambda e: self.politica.chave(e, self.agora))

    def avancar(self, minutos):
        self.agora += timedelta(minutes=int(minutos))

    def atender(self, sla):
        fila = self.ordenar()
        if not fila: return None
        e = fila[0]
        espera = e.espera(self.agora)
        pontos = self.politica.pontuacao(e, self.agora)
        a = Atendimento(e.id,e.nome,e.categoria,e.chegada,self.agora,espera,pontos,sla,espera <= sla)
        self.fila.remove(e)
        self.repo.evento("SAIDA", e, self.agora, {"espera_min":espera,"pontuacao":pontos,"dentro_sla":a.dentro_sla})
        self.repo.atendimento(a)
        self.ultimo = a
        return a

    def limpar(self):
        for e in list(self.fila):
            self.repo.evento("LIMPEZA_SIMULACAO",e,self.agora,{"motivo":"fila limpa pelo operador"})
        self.fila.clear()

st.set_page_config(page_title="Fila Justa", page_icon="🗳️", layout="wide")
st.markdown("""
<style>
.stApp{background:#f4f7fb}.block-container{padding-top:1.4rem}
.hero{padding:1.3rem 1.5rem;border-radius:16px;color:white;background:linear-gradient(120deg,#13294b,#1d647a);margin-bottom:1rem}
.hero h1{margin:0}.hero p{margin:.4rem 0 0}
div[data-testid="stMetric"]{background:white;border:1px solid #e2e8f0;padding:12px;border-radius:12px}
</style>
<div class="hero"><h1>🗳️ Fila Justa</h1><p>Simulador de prioridade eleitoral • POO • Aging • SLA • Auditoria SQL</p></div>
""", unsafe_allow_html=True)

if "repo" not in st.session_state:
    st.session_state.repo = RepositorioAuditoria()
if "sim" not in st.session_state:
    st.session_state.sim = SimuladorFila(st.session_state.repo)
sim = st.session_state.sim
repo = st.session_state.repo

with st.sidebar:
    st.header("Configurações")
    intervalo = st.selectbox("Aging: intervalo em minutos", [20,10,5], index=0)
    sla = st.slider("SLA hipotético (minutos)", 20,180,60,10)
    sim.politica = PoliticaPrioridade(intervalo)
    st.divider()
    st.write("**Relógio simulado**")
    st.write(sim.agora.strftime("%d/%m/%Y %H:%M"))
    minutos = st.select_slider("Avançar o relógio", options=[0,5,10,20,30,60,120], value=0,
                               format_func=lambda x:f"{x} min")
    if st.button("⏩ Aplicar avanço", use_container_width=True):
        sim.avancar(minutos); st.rerun()
    st.caption("Dados e regras são fictícios para fins acadêmicos.")

painel, entrada, historico, auditoria, sobre = st.tabs(["📊 Painel","➕ Entrada","🧾 Histórico","🔎 Auditoria SQL","📘 Regras e POO"])

with painel:
    fila = sim.ordenar()
    atend = repo.tabela("atendimentos")
    n_atend = len(atend)
    dentro = int(atend["dentro_sla"].sum()) if n_atend else 0
    media = float(atend["espera_min"].mean()) if n_atend else 0
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Na fila",len(fila)); c2.metric("Atendidos",n_atend)
    c3.metric("Espera média",f"{media:.1f} min")
    c4.metric("Dentro do SLA",f"{(dentro/n_atend*100 if n_atend else 0):.0f}%")
    esquerda,direita = st.columns([1.6,1])
    with esquerda:
        st.subheader("Fila ordenada pela política")
        if fila:
            dados=[]
            for pos,e in enumerate(fila,1):
                espera=e.espera(sim.agora)
                dados.append({"Posição":pos,"ID":e.id,"Eleitor":e.nome,"Categoria":e.categoria,
                              "Chegada":e.chegada.strftime("%H:%M"),"Espera (min)":espera,
                              "Blocos de aging":espera//intervalo,
                              "Pontuação":sim.politica.pontuacao(e,sim.agora),
                              "SLA":"Excedido" if espera>sla else "Dentro"})
            df=pd.DataFrame(dados)
            st.dataframe(df,use_container_width=True,hide_index=True)
            st.download_button("Baixar fila CSV",df.to_csv(index=False).encode("utf-8-sig"),"fila_atual.csv","text/csv")
        else: st.info("Fila vazia. Adicione eleitores na aba Entrada.")
    with direita:
        st.subheader("Categorias na fila")
        if fila:
            st.bar_chart(pd.DataFrame({"Categoria":sim.CATEGORIAS,
                "Quantidade":[sum(e.categoria==c for e in fila) for c in sim.CATEGORIAS]}).set_index("Categoria"))
        if st.button("👤 Atender próximo",type="primary",use_container_width=True,disabled=not fila):
            sim.atender(sla); st.rerun()
        if st.button("🧹 Limpar fila",use_container_width=True,disabled=not fila):
            sim.limpar(); st.rerun()
        if sim.ultimo:
            st.success(f"Último: {sim.ultimo.nome} • {sim.ultimo.espera_min} min • {sim.ultimo.pontuacao} pontos • {'Dentro' if sim.ultimo.dentro_sla else 'Fora'} do SLA")

with entrada:
    st.subheader("Cadastrar eleitor")
    with st.form("manual",clear_on_submit=True):
        nome=st.text_input("Nome ou identificador")
        cat=st.selectbox("Categoria",sim.CATEGORIAS)
        obs=st.text_input("Observação opcional (não inclua dados sensíveis)")
        enviar=st.form_submit_button("Adicionar à fila",type="primary")
        if enviar:
            if nome.strip():
                e=sim.adicionar(nome,cat,obs); st.success(f"{e.nome} adicionado. ID {e.id}."); st.rerun()
            else: st.error("Informe um nome ou identificador.")
    st.divider()
    st.subheader("Gerar cenário aleatório")
    with st.form("random"):
        n=st.slider("Quantidade de eleitores",5,100,25,5)
        seed=st.number_input("Semente aleatória",min_value=0,max_value=999999,value=42)
        gerar=st.form_submit_button("🎲 Gerar eleitores")
        if gerar:
            sim.gerar_aleatorios(int(n),int(seed)); st.success(f"{n} eleitores adicionados."); st.rerun()
    st.caption("A distribuição é artificial e serve apenas para testar o algoritmo.")

with historico:
    st.subheader("Atendimentos concluídos")
    df=repo.tabela("atendimentos")
    if df.empty: st.info("Nenhum atendimento realizado.")
    else:
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button("Baixar histórico CSV",df.to_csv(index=False).encode("utf-8-sig"),"atendimentos.csv","text/csv")
        st.bar_chart(df["dentro_sla"].map({1:"Dentro do SLA",0:"Fora do SLA"}).value_counts())

with auditoria:
    st.subheader("Log de auditoria")
    st.write("A inserção e a saída da fila são registradas no SQLite.")
    df=repo.tabela("auditoria")
    if df.empty: st.info("Ainda não há eventos.")
    else:
        st.dataframe(df,use_container_width=True,hide_index=True)
        st.download_button("Baixar auditoria CSV",df.to_csv(index=False).encode("utf-8-sig"),"auditoria.csv","text/csv")
    st.download_button("Baixar logs.sql",repo.exportar_sql().encode("utf-8"),"logs.sql","application/sql")
    schema="""CREATE TABLE IF NOT EXISTS auditoria(
      id INTEGER PRIMARY KEY AUTOINCREMENT, evento TEXT NOT NULL, eleitor_id INTEGER NOT NULL,
      nome TEXT NOT NULL, categoria TEXT NOT NULL, instante TEXT NOT NULL, detalhes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS atendimentos(
      id INTEGER PRIMARY KEY AUTOINCREMENT, eleitor_id INTEGER NOT NULL, nome TEXT NOT NULL,
      categoria TEXT NOT NULL, chegada TEXT NOT NULL, instante TEXT NOT NULL, espera_min INTEGER NOT NULL,
      pontuacao INTEGER NOT NULL, sla_min INTEGER NOT NULL, dentro_sla INTEGER NOT NULL);
"""
    st.download_button("Baixar schema.sql",schema.encode("utf-8"),"schema.sql","application/sql")

with sobre:
    st.subheader("Regra de prioridade")
    st.markdown(f"""
- Pessoa com deficiência: **3 pontos-base**.
- Pessoa idosa: **3 pontos-base**.
- Pessoa comum: **1 ponto-base**.
- Aging: a cada **{intervalo} minutos completos**, o multiplicador aumenta em 1.
- Fórmula: `pontuação = pontos_base × (1 + minutos_de_espera // intervalo)`.
- Desempate: maior pontuação; categoria preferencial antes de comum; ordem de chegada; ID.
- SLA: compara a espera total no atendimento com a meta definida na barra lateral.
""")
    st.warning("A ordem e a pontuação entre categorias são escolhas hipotéticas para o exercício. Não representam regra eleitoral oficial.")
    st.subheader("Classes e responsabilidades")
    st.markdown("""
- `Eleitor`: entidade com ID, nome, categoria e chegada.
- `Atendimento`: dados do atendimento e resultado do SLA.
- `PoliticaPrioridade`: calcula pontuação e chave de ordenação.
- `RepositorioAuditoria`: grava eventos em SQLite e exporta SQL.
- `SimuladorFila`: coordena fila, relógio, geração aleatória e atendimento.
""")
    st.subheader("Complexidade")
    st.markdown("""
- Inserção na lista: **O(1)** amortizado.
- Ordenação: **O(n log n)**.
- Atendimento: **O(n log n)**, porque a fila é reordenada antes de selecionar o próximo.
- Memória da fila: **O(n)**.
""")
    st.info("Use dados fictícios. Este protótipo é acadêmico e não deve ser usado para operar uma fila eleitoral real.")

st.divider()
st.caption("Projeto acadêmico demonstrativo • Python + Streamlit + SQLite • Dados fictícios.")
