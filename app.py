import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, timedelta
import hashlib
from fpdf import FPDF
import io

# Configuração da Página
st.set_page_config(page_title="Controle de Faturamento", layout="wide")

# ==========================================
# CONFIGURAÇÃO GLOBAL DE CLIENTES DO SISTEMA
# ==========================================
LISTA_CLIENTES = ["AWS", "ZFGROUP"]

# ==========================================
# FUNÇÕES AUXILIARES DE FORMATAÇÃO (BRL e DD/MM/AAAA)
# ==========================================
def formatar_brl(valor):
    """Transforma um float/int no formato R$ X.XXX,XX"""
    try:
        return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return str(valor)

def formatar_data(data_orig):
    """Transforma uma data YYYY-MM-DD ou objeto date para DD/MM/AAAA"""
    if not data_orig or data_orig == "-":
        return data_orig
    try:
        if isinstance(data_orig, str):
            if "T" in data_orig:
                dt = datetime.strptime(data_orig.split("T")[0], "%Y-%m-%d")
            else:
                dt = datetime.strptime(data_orig, "%Y-%m-%d")
        else:
            dt = data_orig
        return dt.strftime("%d/%m/%Y")
    except:
        return str(data_orig)

def formatar_data_hora(dt_str):
    """Transforma um timestamp do banco para DD/MM/AAAA HH:MM:SS"""
    if not dt_str:
        return dt_str
    try:
        dt_str = dt_str.replace("T", " ").split(".")[0]
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(dt_str)

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SUPABASE API)
# ==========================================
@st.cache_resource
def get_supabase_client() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = get_supabase_client()

def registrar_log(acao, detalhes):
    if 'user_id' in st.session_state:
        try:
            supabase.table("logsfat").insert({
                "usuario_id": st.session_state['user_id'],
                "acao": acao,
                "detalhes": detalhes
            }).execute()
        except Exception as e:
            pass # Evita travar o app se o log falhar

def hash_senha(senha):
    return hashlib.sha256(senha.encode()).hexdigest()

# ==========================================
# FUNÇÕES DE RELATÓRIO (PDF)
# ==========================================
class PDFRelatorio(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'Relatorio de Faturamento', 0, 1, 'C')
        self.ln(5)

def gerar_pdf(dados, titulo):
    pdf = PDFRelatorio()
    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, titulo, 0, 1, 'L')
    pdf.set_font("Arial", size=10)
    
    pdf.cell(40, 10, "Data", border=1)
    pdf.cell(50, 10, "Cliente", border=1)
    pdf.cell(40, 10, "Valor", border=1)
    pdf.cell(50, 10, "Lancado Por", border=1)
    pdf.ln()
    
    for row in dados:
        pdf.cell(40, 10, formatar_data(row['data_lancamento']), border=1)
        pdf.cell(50, 10, str(row['cliente']), border=1)
        pdf.cell(40, 10, formatar_brl(row['valor']), border=1)
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
            res = supabase.table("usuarios").select("*").eq("usuario", user).eq("senha", hash_senha(senha)).execute()
            
            if res.data:
                resultado = res.data[0]
                if resultado['aprovado']:
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

    with abas[1]:
        n_nome = st.text_input("Nome Completo")
        n_user = st.text_input("Novo Usuário")
        n_senha = st.text_input("Nova Senha", type="password")
        if st.button("Solicitar Cadastro"):
            try:
                supabase.table("usuarios").insert({
                    "nome": n_nome,
                    "usuario": n_user,
                    "senha": hash_senha(n_senha)
                }).execute()
                st.success("Cadastro solicitado com sucesso! Aguarde a aprovação.")
            except Exception as e:
                st.error(f"Erro real do Supabase: {e}")

def dashboard():
    st.header("📊 Dashboard de Faturamentos")
    
    hoje = datetime.today()
    ultimo_domingo = hoje - timedelta(days=(hoje.weekday() + 1) % 7)
    proximo_domingo = ultimo_domingo + timedelta(days=7)
    
    # Buscar faturamentos reais do período
    res = supabase.table("faturamentos")\
        .select("id, cliente, valor, status, data_lancamento")\
        .gte("data_lancamento", ultimo_domingo.strftime("%Y-%m-%d"))\
        .lte("data_lancamento", proximo_domingo.strftime("%Y-%m-%d"))\
        .execute()
        
    totais = {'FATURADO': 0.0, 'PENDENTE': 0.0, 'PAGO': 0.0}
    df_semana = pd.DataFrame(res.data)
    
    # --- INTEGRAÇÃO DA REGRA DE NEGÓCIO (CLIENTES PENDENTES NA DATA) ---
    linhas_extras = []
    if not df_semana.empty:
        df_semana['data_lancamento'] = df_semana['data_lancamento'].astype(str)
        datas_com_lancamento = df_semana['data_lancamento'].unique()
        
        for data_v in datas_com_lancamento:
            clientes_na_data = df_semana[df_semana['data_lancamento'] == data_v]['cliente'].unique()
            for cli in LISTA_CLIENTES:
                if cli not in clientes_na_data:
                    linhas_extras.append({
                        "id": "-",
                        "cliente": cli,
                        "valor": 0.0,
                        "status": "PENDENTE",
                        "data_lancamento": data_v
                    })
        if linhas_extras:
            df_semana = pd.concat([df_semana, pd.DataFrame(linhas_extras)], ignore_index=True)
    else:
        data_hoje_str = hoje.strftime("%Y-%m-%d")
        for cli in LISTA_CLIENTES:
            linhas_extras.append({
                "id": "-",
                "cliente": cli,
                "valor": 0.0,
                "status": "PENDENTE",
                "data_lancamento": data_hoje_str
            })
        df_semana = pd.DataFrame(linhas_extras)
    # -------------------------------------------------------------------
        
    if not df_semana.empty:
        for status_tipo in totais.keys():
            totais[status_tipo] = float(df_semana[df_semana['status'] == status_tipo]['valor'].sum())
            
    col1, col2, col3 = st.columns(3)
    col1.metric("🟢 Faturado", formatar_brl(totais['FATURADO']))
    col2.metric("🔴 Pendente", formatar_brl(totais['PENDENTE']))
    col3.metric("🔵 Pago", formatar_brl(totais['PAGO']))
    
    st.divider()
    
    st.subheader("Faturamentos da Semana (Domingo a Domingo)")
    
    def colorir_status(val):
        cor = 'green' if val == 'FATURADO' else 'red' if val == 'PENDENTE' else 'blue'
        return f'color: {cor}; font-weight: bold'
    
    if not df_semana.empty:
        df_display = df_semana[['id', 'cliente', 'valor', 'status', 'data_lancamento']].copy()
        df_display['valor'] = df_display['valor'].apply(formatar_brl)
        df_display['data_lancamento'] = df_display['data_lancamento'].apply(formatar_data)
        df_display.columns = ['ID', 'Cliente', 'Valor', 'Status', 'Data']
        st.dataframe(df_display.style.map(colorir_status, subset=['Status']), use_container_width=True)
    else:
        st.info("Nenhum faturamento nesta semana.")

    st.divider()
    
    st.subheader("⚠️ Faturamentos Expirados / Não Pagos")
    res_exp = supabase.table("faturamentos").select("cliente, valor, status, data_lancamento").neq("status", "PAGO").execute()
    df_expirados = pd.DataFrame(res_exp.data)
    
    if not df_expirados.empty:
        df_exp_display = df_expirados[['cliente', 'valor', 'status', 'data_lancamento']].copy()
        df_exp_display['valor'] = df_exp_display['valor'].apply(formatar_brl)
        df_exp_display['data_lancamento'] = df_exp_display['data_lancamento'].apply(formatar_data)
        df_exp_display.columns = ['Cliente', 'Valor', 'Status', 'Data']
        st.dataframe(df_exp_display.style.map(colorir_status, subset=['Status']), use_container_width=True)
    else:
        st.success("Tudo em dia! Nenhum faturamento pendente ou expirado.")

def lancar_novo():
    st.header("📝 Lançar Novo Faturamento")
    
    cliente = st.selectbox("Selecione o Cliente", LISTA_CLIENTES)
    arquivo = st.file_uploader("Anexar Planilha de Faturamento (Excel)", type=['xlsx', 'xls'])
    
    valor_total = 0.0
    if arquivo:
        try:
            df = pd.read_excel(arquivo)
            col_valor = [col for col in df.columns if col.upper() == 'VALOR']
            if col_valor:
                valores_coluna = pd.to_numeric(df[col_valor[0]], errors='coerce')
                valor_total = float(valores_coluna.iloc[:-1].sum())
                
                st.success(f"Valor total calculado da planilha (última linha ignorada): {formatar_brl(valor_total)}")
            else:
                st.error("Coluna 'Valor' não encontrada na planilha.")
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")

    concordo = st.checkbox("Concordo que a planilha foi devidamente conferida antes de anexar.")
    botao_desabilitado = not concordo or arquivo is None or valor_total == 0.0
    
    if st.button("Lançar Faturamento", disabled=botao_desabilitado):
        blob_hex = f"\\x{arquivo.getvalue().hex()}"
        data_hoje = datetime.today().strftime("%Y-%m-%d")
        
        try:
            supabase.table("faturamentos").insert({
                "cliente": cliente,
                "valor": valor_total,
                "arquivo_nome": arquivo.name,
                "arquivo_blob": blob_hex,
                "status": "FATURADO",
                "data_lancamento": data_hoje,
                "lancado_por": st.session_state['user_id']
            }).execute()
            
            registrar_log("INSERÇÃO", f"Faturamento de {formatar_brl(valor_total)} lançado para {cliente} com status FATURADO.")
            st.success("Faturamento lançado com sucesso com status FATURADO!")
        except Exception as e:
            st.error(f"Erro ao salvar no banco de dados: {e}")

def pesquisar_faturamento():
    st.header("🔍 Pesquisar Faturamento")
    
    # 1. Busca APENAS a coluna 'cliente' no banco (garante privacidade e leveza)
    try:
        dados_clientes = supabase.table("faturamentos").select("cliente").execute()
        # Remove nomes duplicados e organiza em ordem alfabética
        lista_clientes = sorted(list(set([row['cliente'] for row in dados_clientes.data if row.get('cliente')])))
    except Exception as e:
        st.error("Erro ao carregar a lista de clientes.")
        lista_clientes = []

    # Cria as opções do menu com o texto inicial neutro
    opcoes_menu = ["Selecione um cliente..."] + lista_clientes
    
    # 2. Menu de seleção exibindo APENAS os nomes dos clientes
    cliente_selecionado = st.selectbox("Selecione o Cliente", options=opcoes_menu)
    
    # 3. Os faturamentos e valores SÓ aparecem se um cliente real for clicado
    if cliente_selecionado != "Selecione um cliente...":
        
        # Busca os dados completos (valores, status, etc.) APENAS do cliente selecionado
        res = supabase.table("faturamentos").select("*, usuarios(nome)").eq("cliente", cliente_selecionado).execute()
        rows = res.data
        
        if rows:
            for row in rows:
                nome_usuario = row['usuarios']['nome'] if row.get('usuarios') else "Desconhecido"
                v_brl = formatar_brl(row['valor'])
                d_pt = formatar_data(row['data_lancamento'])
                
                # Os valores financeiros só aparecem aqui dentro, após a seleção
                with st.expander(f"{row['cliente']} - {v_brl} ({d_pt})"):
                    st.write(f"**Lançado por:** {nome_usuario}")
                    st.write(f"**Arquivo original:** {row['arquivo_nome']}")
                    
                    if row.get('arquivo_blob'):
                        try:
                            hex_str = row['arquivo_blob']
                            if hex_str.startswith('\\x'):
                                hex_str = hex_str[2:]
                            bytes_arquivo = bytes.fromhex(hex_str)
                            st.download_button(label="📥 Fazer Download da Planilha", data=bytes_arquivo, file_name=row['arquivo_nome'], key=f"dl_{row['id']}")
                        except:
                            st.caption("Não foi possível processar o arquivo anexo.")
                    
                    novo_status = st.selectbox("Alterar Status", ['PENDENTE', 'FATURADO', 'PAGO'], index=['PENDENTE', 'FATURADO', 'PAGO'].index(row['status']), key=f"st_{row['id']}")
                    
                    c1, c2 = st.columns(2)
                    if c1.button("Salvar Alteração", key=f"sv_{row['id']}"):
                        supabase.table("faturamentos").update({"status": novo_status}).eq("id", row['id']).execute()
                        registrar_log("ALTERAÇÃO", f"Status do faturamento ID {row['id']} alterado para {novo_status}")
                        st.success("Status atualizado!")
                        st.rerun()
                        
                    if c2.button("Excluir Faturamento", type="primary", key=f"del_{row['id']}"):
                        st.session_state[f"confirm_del_{row['id']}"] = True
                        
                    if st.session_state.get(f"confirm_del_{row['id']}", False):
                        st.warning("⚠️ Tem certeza absoluta que deseja excluir este faturamento?")
                        if st.button("Sim, Confirmar Exclusão", key=f"conf_yes_{row['id']}"):
                            supabase.table("faturamentos").delete().eq("id", row['id']).execute()
                            registrar_log("EXCLUSÃO", f"Faturamento ID {row['id']} excluído do sistema.")
                            st.success("Removido com sucesso!")
                            st.session_state[f"confirm_del_{row['id']}"] = False
                            st.rerun()
        else:
            st.info("Nenhum faturamento encontrado para este cliente.")

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
        res = supabase.table("faturamentos")\
            .select("data_lancamento, cliente, valor, usuarios(nome)")\
            .eq("status", status_selecionado)\
            .gte("data_lancamento", data_inicio.strftime("%Y-%m-%d"))\
            .lte("data_lancamento", data_fim.strftime("%Y-%m-%d"))\
            .execute()
            
        if res.data:
            dados_formatados = []
            for row in res.data:
                dados_formatados.append({
                    'data_lancamento': row['data_lancamento'],
                    'cliente': row['cliente'],
                    'valor': float(row['valor']),
                    'nome_usuario': row['usuarios']['nome'] if row.get('usuarios') else "Desconhecido"
                })
                
            pdf_bytes = gerar_pdf(dados_formatados, tipo_relatorio)
            st.success("PDF gerado com sucesso!")
            st.download_button(label="📥 Baixar Relatório em PDF", data=pdf_bytes, file_name=f"relatorio_{status_selecionado}.pdf", mime='application/pdf')
        else:
            st.warning("Nenhum registro encontrado para os parâmetros selecionados.")

def log_interno():
    st.header("🔐 Painel de Controle Técnico e Auditoria (MASTER)")
    if st.session_state.get('cargo') != 'MASTER':
        st.error("Acesso estritamente Negado.")
        return
    
    st.subheader("Aprovações de Contas Pendentes")
    res_pendentes = supabase.table("usuarios").select("id, nome, usuario, cargo").eq("aprovado", False).execute()
    pendentes = res_pendentes.data
    
    if pendentes:
        for p in pendentes:
            c1, c2 = st.columns([3, 1])
            c1.write(f"Solicitante: **{p['nome']}** | Usuário: `{p['usuario']}`")
            if c2.button("Liberar Acesso", key=f"apr_{p['id']}"):
                supabase.table("usuarios").update({"aprovado": True}).eq("id", p['id']).execute()
                registrar_log("ALTERAÇÃO", f"O Administrador aprovou o acesso do usuário ID {p['id']}.")
                st.success("Usuário Liberado!")
                st.rerun()
    else:
        st.info("Nenhuma conta aguardando aprovação no momento.")
        
    st.divider()
    
    st.subheader("Histórico Completo de Auditoria (Últimas 100 ações)")
    res_logs = supabase.table("logsfat").select("data_hora, acao, detalhes, usuarios(nome)").order("data_hora", desc=True).limit(100).execute()
    
    if res_logs.data:
        logs_formatados = []
        for l in res_logs.data:
            logs_formatados.append({
                'Data e Hora': formatar_data_hora(l['data_hora']),
                'Usuário': l['usuarios']['nome'] if l.get('usuarios') else "Desconhecido",
                'Ação Efetuada': l['acao'],
                'Detalhes da Operação': l['detalhes']
            })
        df_logs = pd.DataFrame(logs_formatados)
        st.dataframe(df_logs, use_container_width=True)
    else:
        st.write("Sem logs registrados até o momento.")

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
