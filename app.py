import streamlit as st
import psycopg2
import psycopg2.extras
import pandas as pd
from datetime import datetime, timedelta
import hashlib
from fpdf import FPDF
import io

# Configuração da Página
st.set_page_config(page_title="Controle de Faturamento", layout="wide")

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE)
# ==========================================
def get_db_connection():
    conn = psycopg2.connect(
        st.secrets["postgres"]["url"],
        cursor_factory=psycopg2.extras.DictCursor
    )
    return conn

def registrar_log(acao, detalhes):
    if 'user_id' in st.session_state:
        conn = get_db_connection()
        c = conn.cursor()
        # Alterado para logsFat
        c.execute("INSERT INTO logsFat (usuario_id, acao, detalhes) VALUES (%s, %s, %s)",
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
    pdf.cell(50, 10, "Lancado Por", border=1)
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
        user = st.text_input("Usuário", key="login_user")
        senha = st.text_input("Senha", type="password", key="login_pass")
        if st.button("Entrar"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM usuarios WHERE usuario=%s AND senha=%s", (user, hash_senha(senha)))
            resultado = c.fetchone()
            if resultado:
                if resultado['aprovado'] == True:
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
                c.execute("INSERT INTO usuarios (nome, usuario, senha) VALUES (%s, %s, %s)", 
                          (n_nome, n_user, hash_senha(n_senha)))
                conn.commit()
                st.success("Cadastro solicitado com sucesso! Aguarde a aprovação.")
            except psycopg2.IntegrityError:
                st.error("Este nome de usuário já existe.")
            finally:
                conn.close()

def dashboard():
    st.header("📊 Dashboard de Faturamentos")
    
    hoje = datetime.today()
    ultimo_domingo = hoje - timedelta(days=(hoje.weekday() + 1) % 7)
    proximo_domingo = ultimo_domingo + timedelta(days=7)
    
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute("""
        SELECT status, SUM(valor) as total FROM faturamentos 
        WHERE data_lancamento BETWEEN %s AND %s GROUP BY status
    """, (ultimo_domingo.strftime("%Y-%m-%d"), proximo_domingo.strftime("%Y-%m-%d")))
    
    totais = {'FATURADO': 0, 'PENDENTE': 0, 'PAGO': 0}
    for row in c.fetchall():
        totais[row['status']] = float(row['total'])
        
    col1, col2, col3 = st.columns(3)
    col1.metric("🟢 Faturado", f"R$ {totais.get('FATURADO', 0):.2f}")
    col2.metric("🔴 Pendente", f"R$ {totais.get('PENDENTE', 0):.2f}")
    col3.metric("🔵 Pago", f"R$ {totais.get('PAGO', 0):.2f}")
    
    st.divider()
    
    st.subheader("Faturamentos da Semana (Domingo a Domingo)")
    c.execute("SELECT id, cliente, valor, status, data_lancamento FROM faturamentos WHERE data_lancamento BETWEEN %s AND %s", 
              (ultimo_domingo.strftime("%Y-%m-%d"), proximo_domingo.strftime("%Y-%m-%d")))
    rows_semana = c.fetchall()
    
    def colorir_status(val):
        cor = 'green' if val == 'FATURADO' else 'red' if val == 'PENDENTE' else 'blue'
        return f'color: {cor}; font-weight: bold'
    
    if rows_semana:
        df_semana = pd.DataFrame([dict(r) for r in rows_semana])
        df_semana.columns = ['ID', 'Cliente', 'Valor', 'Status', 'Data']
        st.dataframe(df_semana.style.map(colorir_status, subset=['Status']), use_container_width=True)
    else:
        st.info("Nenhum faturamento nesta semana.")

    st.divider()
    
    st.subheader("⚠️ Faturamentos Expirados / Não Pagos")
    c.execute("SELECT cliente, valor, status, data_lancamento FROM faturamentos WHERE status != 'PAGO'")
    rows_expirados = c.fetchall()
    if rows_expirados:
        df_expirados = pd.DataFrame([dict(r) for r in rows_expirados])
        df_expirados.columns = ['Cliente', 'Valor', 'Status', 'Data']
        st.dataframe(df_expirados.style.map(colorir_status, subset=['Status']), use_container_width=True)
    else:
        st.success("Tudo em dia! Nenhum faturamento pendente ou expirado.")
    
    conn.close()

def lancar_novo():
    st.header("📝 Lançar Novo Faturamento")
    
    cliente = st.selectbox("Selecione o Cliente", ["AWS", "ZFGROUP"])
    arquivo = st.file_uploader("Anexar Planilha de Faturamento (Excel)", type=['xlsx', 'xls'])
    
    valor_total = 0.0
    if arquivo:
        try:
            df = pd.read_excel(arquivo)
            col_valor = [col for col in df.columns if col.upper() == 'VALOR']
            if col_valor:
                valor_total = pd.to_numeric(df[col_valor[0]], errors='coerce').sum()
                st.success(f"Valor total calculado da planilha: R$ {valor_total:.2f}")
            else:
                st.error("Coluna 'Valor' ou 'Valor' não encontrada na planilha.")
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")

    concordo = st.checkbox("Concordo que a planilha foi devidamente conferida antes de anexar.")
    botao_desabilitado = not concordo or arquivo is None or valor_total == 0.0
    
    if st.button("Lançar Faturamento", disabled=botao_desabilitado):
        blob_arquivo = arquivo.getvalue()
        conn = get_db_connection()
        c = conn.cursor()
        data_hoje = datetime.today().strftime("%Y-%m-%d")
        
        c.execute("""INSERT INTO faturamentos (cliente, valor, arquivo_nome, arquivo_blob, status, data_lancamento, lancado_por)
                     VALUES (%s, %s, %s, %s, 'PENDENTE', %s, %s)""", 
                  (cliente, valor_total, arquivo.name, psycopg2.Binary(blob_arquivo), data_hoje, st.session_state['user_id']))
        conn.commit()
        conn.close()
        registrar_log("INSERÇÃO", f"Faturamento de R$ {valor_total} lançado para {cliente}.")
        st.success("Faturamento lançado com sucesso com status PENDENTE!")

def pesquisar_faturamento():
    st.header("🔍 Pesquisar Faturamento")
    col1, col2 = st.columns(2)
    busca_cliente = col1.text_input("Buscar por Cliente")
    
    conn = get_db_connection()
    c = conn.cursor()
    
    query = """
        SELECT f.id, f.cliente, f.valor, f.status, f.data_lancamento, f.arquivo_nome, u.nome as usuario 
        FROM faturamentos f 
        JOIN usuarios u ON f.lancado_por = u.id 
        WHERE 1=1
    """
    params = []
    if busca_cliente:
        query += " AND f.cliente ILIKE %s"
        params.append(f"%{busca_cliente}%")
        
    c.execute(query, params)
    rows = c.fetchall()
    
    if rows:
        for row in rows:
            with st.expander(f"{row['cliente']} - R$ {row['valor']} ({row['data_lancamento']})"):
                st.write(f"**Lançado por:** {row['usuario']}")
                st.write(f"**Arquivo original:** {row['arquivo_nome']}")
                
                c_blob = conn.cursor()
                c_blob.execute("SELECT arquivo_blob FROM faturamentos WHERE id = %s", (row['id'],))
                blob_row = c_blob.fetchone()
                
                if blob_row and blob_row['arquivo_blob']:
                    bytes_arquivo = bytes(blob_row['arquivo_blob'])
                    st.download_button(label="📥 Fazer Download da Planilha", data=bytes_arquivo, file_name=row['arquivo_nome'], key=f"dl_{row['id']}")
                
                novo_status = st.selectbox("Alterar Status", ['PENDENTE', 'FATURADO', 'PAGO'], index=['PENDENTE', 'FATURADO', 'PAGO'].index(row['status']), key=f"st_{row['id']}")
                
                c1, c2 = st.columns(2)
                if c1.button("Salvar Alteração", key=f"sv_{row['id']}"):
                    c_up = conn.cursor()
                    c_up.execute("UPDATE faturamentos SET status = %s WHERE id = %s", (novo_status, row['id']))
                    conn.commit()
                    registrar_log("ALTERAÇÃO", f"Status do faturamento ID {row['id']} alterado para {novo_status}")
                    st.success("Status updated!")
                    st.rerun()
                    
                if c2.button("Excluir Faturamento", type="primary", key=f"del_{row['id']}"):
                    st.session_state[f"confirm_del_{row['id']}"] = True
                    
                if st.session_state.get(f"confirm_del_{row['id']}", False):
                    st.warning("⚠️ Tem certeza absoluta que deseja excluir este faturamento?")
                    if st.button("Sim, Confirmar Exclusão", key=f"conf_yes_{row['id']}"):
                        c_del = conn.cursor()
                        c_del.execute("DELETE FROM faturamentos WHERE id = %s", (row['id'],))
                        conn.commit()
                        registrar_log("EXCLUSÃO", f"Faturamento ID {row['id']} excluído do sistema.")
                        st.success("Removido com sucesso!")
                        st.session_state[f"confirm_del_{row['id']}"] = False
                        st.rerun()
    else:
        st.write("Nenhum registro encontrado.")
    conn.close()

def relatorios():
    st.header("📄 Relatórios Gerenciais")
    
    tipo_relatorio = st.selectbox("Selecione o Filtro do Relatório", [
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
    
    data_inicio = st.date_input("Data Inicial")
    data_fim = st.date_input("Data Final")
    
    if st.button("Gerar Relatório em PDF"):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT f.data_lancamento, f.cliente, f.valor, u.nome as nome_usuario 
            FROM faturamentos f 
            JOIN usuarios u ON f.lancado_por = u.id 
            WHERE f.status = %s AND f.data_lancamento BETWEEN %s AND %s
        """, (status_selecionado, data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d")))
        
        dados = c.fetchall()
        conn.close()
        
        if dados:
            pdf_bytes = gerar_pdf(dados, tipo_relatorio)
            st.success("PDF gerado com sucesso!")
            st.download_button(label="📥 Baixar Relatório em PDF", data=pdf_bytes, file_name=f"relatorio_{status_selecionado}.pdf", mime='application/pdf')
        else:
            st.warning("Nenhum registro encontrado para os parâmetros selecionados.")

def log_interno():
    st.header("🔐 Painel de Controle Técnico e Auditoria (MASTER)")
    if st.session_state.get('cargo') != 'MASTER':
        st.error("Acesso estritamente Negado. Apenas usuários com nível MASTER podem visualizar.")
        return
    
    conn = get_db_connection()
    c = conn.cursor()
    
    st.subheader("Aprovações de Contas Pendentes")
    c.execute("SELECT id, nome, usuario, cargo FROM usuarios WHERE aprovado = FALSE")
    pendentes = c.fetchall()
    
    if pendentes:
        for p in pendentes:
            c1, c2 = st.columns([3, 1])
            c1.write(f"Solicitante: **{p['nome']}** | Usuário: `{p['usuario']}`")
            if c2.button("Liberar Acesso", key=f"apr_{p['id']}"):
                c_up = conn.cursor()
                c_up.execute("UPDATE usuarios SET aprovado = TRUE WHERE id = %s", (p['id'],))
                conn.commit()
                registrar_log("ALTERAÇÃO", f"O Administrador aprovou o acesso do usuário ID {p['id']}.")
                st.success("Usuário Liberado!")
                st.rerun()
    else:
        st.info("Nenhuma conta aguardando aprovação no momento.")
        
    st.divider()
    
    st.subheader("Histórico Completo de Auditoria (Últimas 100 ações)")
    # Query alterada de logs para logsFat
    c.execute("""
        SELECT l.data_hora, u.nome, l.acao, l.detalhes 
        FROM logsFat l 
        JOIN usuarios u ON l.usuario_id = u.id 
        ORDER BY l.data_hora DESC LIMIT 100
    """)
    rows_logs = c.fetchall()
    if rows_logs:
        df_logs = pd.DataFrame([dict(r) for r in rows_logs])
        df_logs.columns = ['Data e Hora', 'Usuário', 'Ação Efetuada', 'Detalhes da Operação']
        st.dataframe(df_logs, use_container_width=True)
    else:
        st.write("Sem logs registrados até o momento.")
    conn.close()

# ==========================================
# ROTEAMENTO DA SESSÃO PRINCIPAL
# ==========================================
def main():
    if 'logado' not in st.session_state:
        st.session_state['logado'] = False

    if not st.session_state['logado']:
        tela_login()
    else:
        st.sidebar.subheader(f"Logado como: {st.session_state['nome']}")
        st.sidebar.caption(f"Nível de Acesso: {st.session_state['cargo']}")
        
        menus = ["Dashboard", "Lançar Novo", "Pesquisar Faturamento", "Relatórios"]
        if st.session_state['cargo'] == 'MASTER':
            menus.append("Log Interno")
            
        escolha = st.sidebar.radio("Navegar para:", menus)
        
        st.sidebar.divider()
        if st.sidebar.button("Efetuar Logout (Sair)", type="secondary"):
            registrar_log("LOGOUT", "Usuário encerrou a sessão.")
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
