import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import hashlib
from fpdf import FPDF
import io

# Configuração da Página
st.set_page_config(page_title="Controle de Faturamento", layout="wide")

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS E FUNÇÕES
# ==========================================
DB_NAME = "faturamento.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS usuarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT, usuario TEXT UNIQUE, senha TEXT,
                    cargo TEXT DEFAULT 'USER', aprovado BOOLEAN DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS faturamentos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cliente TEXT, valor REAL, arquivo_nome TEXT, arquivo_blob BLOB,
                    status TEXT DEFAULT 'PENDENTE', data_lancamento DATE, lancado_por INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_hora DATETIME DEFAULT CURRENT_TIMESTAMP,
                    usuario_id INTEGER, acao TEXT, detalhes TEXT)''')
    
    # Criar um admin padrão se não existir (apenas para o primeiro acesso)
    c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
    if not c.fetchone():
        senha_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        c.execute("INSERT INTO usuarios (nome, usuario, senha, cargo, aprovado) VALUES (?, ?, ?, ?, ?)",
                  ('Administrador', 'admin', senha_hash, 'MASTER', 1))
    conn.commit()
    conn.close()

def registrar_log(acao, detalhes):
    if 'user_id' in st.session_state:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO logs (usuario_id, acao, detalhes) VALUES (?, ?, ?)",
                  (st.session_state['user_id'], acao, detalhes))
        conn.commit()
        conn.close()

def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

# ==========================================
# FUNÇÕES DE RELATÓRIO (PDF)
# ==========================================
class PDFRelatorio(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'Relatório de Faturamento', 0, 1, 'C')
        self.ln(5)

def gerar_pdf(dados, titulo):
    pdf = PDFRelatorio()
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, titulo, 0, 1, 'L')
    pdf.set_font("Arial", size=10)
    
    pdf.cell(40, 10, "Data", border=1)
    pdf.cell(50, 10, "Cliente", border=1)
    pdf.cell(40, 10, "Valor (R$)", border=1)
    pdf.cell(50, 10, "Lançado Por", border=1)
    pdf.ln()
    
    for row in dados:
        pdf.cell(40, 10, str(row['data_lancamento']), border=1)
        pdf.cell(50, 10, str(row['cliente']), border=1)
        pdf.cell(40, 10, f"R$ {row['valor']:.2f}", border=1)
        pdf.cell(50, 10, str(row['nome_usuario']), border=1)
        pdf.ln()
        
    return pdf.output(dest='S').encode('latin1')

# ==========================================
# TELAS DO SISTEMA
# ==========================================
def tela_login():
    st.title("SISTEMA DE FATURAMENTO")
    abas = st.tabs(["Login", "Cadastrar"])
    
    with abas[0]:
        user = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM usuarios WHERE usuario=? AND senha=?", (user, hash_senha(senha)))
            resultado = c.fetchone()
            if resultado:
                if resultado['aprovado'] == 1:
                    st.session_state['logado'] = True
                    st.session_state['user_id'] = resultado['id']
                    st.session_state['cargo'] = resultado['cargo']
                    st.session_state['nome'] = resultado['nome']
                    registrar_log("LOGIN", "Usuário acessou o sistema")
                    st.rerun()
                else:
                    st.error("Sua conta aguarda aprovação de um Administrador.")
            else:
                st.error("Usuário ou senha incorretos.")
            conn.close()

    with abas[1]:
        n_nome = st.text_input("Nome Completo")
        n_user = st.text_input("Novo Usuário")
        n_senha = st.text_input("Nova Senha", type="password")
        if st.button("Solicitar Cadastro"):
            conn = get_db_connection()
            c = conn.cursor()
            try:
                c.execute("INSERT INTO usuarios (nome, usuario, senha) VALUES (?, ?, ?)", 
                          (n_nome, n_user, hash_senha(n_senha)))
                conn.commit()
                st.success("Cadastro solicitado com sucesso! Aguarde a aprovação.")
            except sqlite3.IntegrityError:
                st.error("Este nome de usuário já existe.")
            conn.close()

def dashboard():
    st.header("📊 Dashboard de Faturamentos")
    
    # Lógica de Domingo a Domingo
    hoje = datetime.today()
    ultimo_domingo = hoje - timedelta(days=(hoje.weekday() + 1) % 7)
    proximo_domingo = ultimo_domingo + timedelta(days=7)
    
    conn = get_db_connection()
    c = conn.cursor()
    
    # Resumo do período
    c.execute("""
        SELECT status, SUM(valor) as total FROM faturamentos 
        WHERE data_lancamento BETWEEN ? AND ? GROUP BY status
    """, (ultimo_domingo.strftime("%Y-%m-%d"), proximo_domingo.strftime("%Y-%m-%d")))
    
    totais = {'FATURADO': 0, 'PENDENTE': 0, 'PAGO': 0}
    for row in c.fetchall():
        totais[row['status']] = row['total']
        
    col1, col2, col3 = st.columns(3)
    col1.metric("🟢 Faturado", f"R$ {totais.get('FATURADO', 0):.2f}")
    col2.metric("🔴 Pendente", f"R$ {totais.get('PENDENTE', 0):.2f}")
    col3.metric("🔵 Pago", f"R$ {totais.get('PAGO', 0):.2f}")
    
    st.divider()
    
    # Lista de todos os clientes no período
    st.subheader("Faturamentos da Semana")
    c.execute("SELECT id, cliente, valor, status, data_lancamento FROM faturamentos WHERE data_lancamento BETWEEN ? AND ?", 
              (ultimo_domingo.strftime("%Y-%m-%d"), proximo_domingo.strftime("%Y-%m-%d")))
    df_semana = pd.DataFrame(c.fetchall(), columns=['ID', 'Cliente', 'Valor', 'Status', 'Data'])
    
    def colorir_status(val):
        cor = 'green' if val == 'FATURADO' else 'red' if val == 'PENDENTE' else 'blue'
        return f'color: {cor}; font-weight: bold'
    
    if not df_semana.empty:
        st.dataframe(df_semana.style.map(colorir_status, subset=['Status']), use_container_width=True)
    else:
        st.info("Nenhum faturamento nesta semana.")

    st.divider()
    
    # Expirados (NÃO Pagos) de todos os tempos
    st.subheader("⚠️ Faturamentos Expirados / Não Pagos")
    c.execute("SELECT cliente, valor, status, data_lancamento FROM faturamentos WHERE status != 'PAGO'")
    df_expirados = pd.DataFrame(c.fetchall(), columns=['Cliente', 'Valor', 'Status', 'Data'])
    if not df_expirados.empty:
         st.dataframe(df_expirados.style.map(colorir_status, subset=['Status']), use_container_width=True)
    
    conn.close()

def lancar_novo():
    st.header("📝 Lançar Novo Faturamento")
    
    cliente = st.selectbox("Selecione o Cliente", ["AWS", "ZFGROUP", "Outros"])
    arquivo = st.file_uploader("Anexar Planilha de Faturamento (Excel)", type=['xlsx', 'xls'])
    
    valor_total = 0.0
    if arquivo:
        try:
            df = pd.read_excel(arquivo)
            # Tenta encontrar a coluna 'Valor' ou 'VALOR' ignorando maiúsculas/minúsculas
            col_valor = [col for col in df.columns if col.upper() == 'VALOR']
            if col_valor:
                # Soma convertendo para numérico e ignorando erros/textos
                valor_total = pd.to_numeric(df[col_valor[0]], errors='coerce').sum()
                st.success(f"Valor total calculado da planilha: R$ {valor_total:.2f}")
            else:
                st.error("Coluna 'Valor' não encontrada na planilha.")
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")

    concordo = st.checkbox("Concordo que a planilha foi devidamente conferida antes de anexar.")
    
    if st.button("Lançar Faturamento", disabled=not concordo or arquivo is None):
        if arquivo and valor_total > 0:
            blob_arquivo = arquivo.getvalue()
            conn = get_db_connection()
            c = conn.cursor()
            data_hoje = datetime.today().strftime("%Y-%m-%d")
            c.execute("""INSERT INTO faturamentos (cliente, valor, arquivo_nome, arquivo_blob, status, data_lancamento, lancado_por)
                         VALUES (?, ?, ?, ?, 'PENDENTE', ?, ?)""", 
                      (cliente, valor_total, arquivo.name, blob_arquivo, data_hoje, st.session_state['user_id']))
            conn.commit()
            conn.close()
            registrar_log("INSERÇÃO", f"Faturamento de R$ {valor_total} lançado para {cliente}.")
            st.success("Faturamento lançado com sucesso!")

def pesquisar_faturamento():
    st.header("🔍 Pesquisar Faturamento")
    col1, col2 = st.columns(2)
    busca_cliente = col1.text_input("Buscar por Cliente")
    conn = get_db_connection()
    
    query = "SELECT f.id, f.cliente, f.valor, f.status, f.data_lancamento, f.arquivo_nome, u.nome as usuario FROM faturamentos f JOIN usuarios u ON f.lancado_por = u.id WHERE 1=1"
    params = []
    
    if busca_cliente:
        query += " AND f.cliente LIKE ?"
        params.append(f"%{busca_cliente}%")
        
    df_busca = pd.read_sql_query(query, conn, params=params)
    
    if not df_busca.empty:
        for index, row in df_busca.iterrows():
            with st.expander(f"{row['cliente']} - R$ {row['valor']} ({row['data_lancamento']})"):
                st.write(f"**Lançado por:** {row['usuario']}")
                st.write(f"**Arquivo:** {row['arquivo_nome']}")
                
                # Fetch do blob para download
                c = conn.cursor()
                c.execute("SELECT arquivo_blob FROM faturamentos WHERE id = ?", (row['id'],))
                blob = c.fetchone()['arquivo_blob']
                
                if blob:
                    st.download_button(label="Baixar Planilha", data=blob, file_name=row['arquivo_nome'], key=f"dl_{row['id']}")
                
                novo_status = st.selectbox("Status", ['PENDENTE', 'FATURADO', 'PAGO'], index=['PENDENTE', 'FATURADO', 'PAGO'].index(row['status']), key=f"st_{row['id']}")
                
                c1, c2 = st.columns(2)
                if c1.button("Salvar Alteração", key=f"sv_{row['id']}"):
                    c.execute("UPDATE faturamentos SET status = ? WHERE id = ?", (novo_status, row['id']))
                    conn.commit()
                    registrar_log("ALTERAÇÃO", f"Status do faturamento ID {row['id']} alterado para {novo_status}")
                    st.success("Status atualizado!")
                    st.rerun()
                    
                if c2.button("Excluir", type="primary", key=f"del_{row['id']}"):
                    c.execute("DELETE FROM faturamentos WHERE id = ?", (row['id'],))
                    conn.commit()
                    registrar_log("EXCLUSÃO", f"Faturamento ID {row['id']} excluído.")
                    st.warning("Faturamento Excluído!")
                    st.rerun()
    else:
        st.write("Nenhum registro encontrado.")
    conn.close()

def relatorios():
    st.header("📄 Relatórios")
    
    tipo_relatorio = st.selectbox("Selecione o Relatório", [
        "Faturamentos Pagos (PAGO)", 
        "Faturamentos Pendentes (PENDENTE)", 
        "Faturamentos Aguardando Pagamento (FATURADO)"
    ])
    
    mapa_status = {
        "Faturamentos Pagos (PAGO)": "PAGO",
        "Faturamentos Pendentes (PENDENTE)": "PENDENTE",
        "Faturamentos Aguardando Pagamento (FATURADO)": "FATURADO"
    }
    status_selecionado = mapa_status[tipo_relatorio]
    
    data_inicio = st.date_input("Data Início")
    data_fim = st.date_input("Data Fim")
    
    if st.button("Gerar Relatório PDF"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT f.data_lancamento, f.cliente, f.valor, u.nome as nome_usuario 
            FROM faturamentos f 
            JOIN usuarios u ON f.lancado_por = u.id 
            WHERE f.status = ? AND f.data_lancamento BETWEEN ? AND ?
        """, (status_selecionado, data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d")))
        
        dados = c.fetchall()
        conn.close()
        
        if dados:
            pdf_bytes = gerar_pdf(dados, tipo_relatorio)
            st.success("Relatório gerado!")
            st.download_button(label="📥 Baixar PDF", data=pdf_bytes, file_name=f"relatorio_{status_selecionado}.pdf", mime='application/pdf')
        else:
            st.warning("Nenhum dado encontrado para este período.")

def log_interno():
    st.header("🔐 Log Interno do Sistema")
    if st.session_state.get('cargo') != 'MASTER':
        st.error("Acesso Negado. Apenas o cargo MASTER pode ver os logs.")
        return
    
    conn = get_db_connection()
    
    # Aprovação de Usuários
    st.subheader("Gerenciar Usuários")
    c = conn.cursor()
    c.execute("SELECT id, nome, usuario, cargo, aprovado FROM usuarios WHERE aprovado = 0")
    pendentes = c.fetchall()
    if pendentes:
        for p in pendentes:
            c1, c2 = st.columns([3, 1])
            c1.write(f"Usuário: **{p['nome']}** ({p['usuario']})")
            if c2.button("Aprovar", key=f"apr_{p['id']}"):
                c.execute("UPDATE usuarios SET aprovado = 1 WHERE id = ?", (p['id'],))
                conn.commit()
                registrar_log("ALTERAÇÃO", f"Usuário ID {p['id']} aprovado no sistema.")
                st.rerun()
    else:
        st.write("Nenhum usuário aguardando aprovação.")
        
    st.divider()
    
    # Tabela de Logs
    st.subheader("Auditoria de Ações")
    df_logs = pd.read_sql_query("""
        SELECT l.data_hora, u.nome, l.acao, l.detalhes 
        FROM logs l JOIN usuarios u ON l.usuario_id = u.id 
        ORDER BY l.data_hora DESC LIMIT 100
    """, conn)
    st.dataframe(df_logs, use_container_width=True)
    conn.close()

# ==========================================
# ROTEAMENTO PRINCIPAL
# ==========================================
def main():
    init_db()
    
    if 'logado' not in st.session_state:
        st.session_state['logado'] = False

    if not st.session_state['logado']:
        tela_login()
    else:
        st.sidebar.write(f"Bem-vindo, **{st.session_state['nome']}**!")
        
        menus = ["Dashboard", "Lançar Novo", "Pesquisar Faturamento", "Relatórios"]
        if st.session_state['cargo'] == 'MASTER':
            menus.append("Log Interno")
            
        escolha = st.sidebar.radio("Navegação", menus)
        
        if st.sidebar.button("Sair"):
            registrar_log("LOGOUT", "Usuário saiu do sistema")
            st.session_state.clear()
            st.rerun()

        if escolha == "Dashboard":
            dashboard()
        elif escolha == "Lançar Novo":
            lancar_novo()
        elif escolha == "Pesquisar Faturamento":
            pesquisar_faturamento()
        elif escolha == "Relatórios":
            relatorios()
        elif escolha == "Log Interno":
            log_interno()

if __name__ == "__main__":
    main()
