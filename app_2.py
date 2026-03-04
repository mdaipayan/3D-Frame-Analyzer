import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import math
import copy
import os
import tempfile
from fpdf import FPDF
import ezdxf
from ezdxf.enums import TextEntityAlignment

# --- PAGE SETUP ---
st.set_page_config(page_title="Practical 3D Frame Analyzer & Designer", layout="wide")
st.title("🏗️ 3D Frame Analysis & Complete Building Design")
st.caption("Audited: 3D Viewport | PDF Export | LibreCAD DXF (With Rebar Detailing) | BBS")

# --- INITIALIZE STATE ---
if 'grids' not in st.session_state:
    st.session_state.floors = pd.DataFrame({"Floor": [1, 2], "Height (m)": [3.0, 3.0]})
    st.session_state.x_grids = pd.DataFrame({"Grid_ID": ["A", "B", "C"], "X_Coord (m)": [0.0, 4.0, 8.0]})
    st.session_state.y_grids = pd.DataFrame({"Grid_ID": ["1", "2", "3"], "Y_Coord (m)": [0.0, 5.0, 10.0]})
    st.session_state.cols = pd.DataFrame({
        "Col_ID": ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"],
        "X_Grid": ["A", "B", "C", "A", "B", "C", "A", "B", "C"], 
        "Y_Grid": ["1", "1", "1", "2", "2", "2", "3", "3", "3"],
        "X_Offset (m)": [0.0]*9, "Y_Offset (m)": [0.0]*9, "Angle (deg)": [0.0]*9
    })
    st.session_state.last_uploaded = {}
    st.session_state.grids = True

# --- SIDEBAR: CSV IMPORT / EXPORT ---
st.sidebar.header("📂 CSV Import / Export")
csv_choice = st.sidebar.selectbox("Select Table to Modify:", ["Floors", "X-Grids", "Y-Grids", "Columns"])

mapping = {"Floors": "floors", "X-Grids": "x_grids", "Y-Grids": "y_grids", "Columns": "cols"}
active_key = mapping[csv_choice]

csv_data = st.session_state[active_key].to_csv(index=False).encode('utf-8')
st.sidebar.download_button(label=f"⬇️ Download {csv_choice} (CSV)", data=csv_data, file_name=f"{active_key}_template.csv", mime="text/csv", width="stretch")

uploaded_csv = st.sidebar.file_uploader(f"⬆️ Upload {csv_choice} (CSV)", type=["csv"])
if uploaded_csv is not None:
    if st.session_state.last_uploaded.get(csv_choice) != uploaded_csv.name:
        try:
            st.session_state[active_key] = pd.read_csv(uploaded_csv)
            st.session_state.last_uploaded[csv_choice] = uploaded_csv.name
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Error reading CSV: {e}")

st.sidebar.divider()

# --- SIDEBAR: INPUTS ---
st.sidebar.header("1. Material Properties")
fck = st.sidebar.number_input("Concrete Grade (fck - MPa)", value=25.0, step=5.0)
fy = st.sidebar.number_input("Steel Grade (fy - MPa)", value=500.0, step=85.0)
E_conc = 5000 * math.sqrt(max(fck, 1.0)) * 1000 
G_conc = E_conc / (2 * (1 + 0.2))

st.sidebar.header("2. Section Sizes (mm)")
col_size = st.sidebar.text_input("Column (b x h)", "300x450")
beam_size = st.sidebar.text_input("Beam (b x h)", "230x400")

st.sidebar.header("3. Applied Loads (IS 875)")
live_load = st.sidebar.number_input("Live Load (kN/m²)", value=3.0)
floor_finish = st.sidebar.number_input("Floor Finish (kN/m²)", value=1.5)
slab_thick = st.sidebar.number_input("Slab Thickness (mm)", value=150)
wall_thick = st.sidebar.number_input("Wall Thickness (mm)", value=230)
eq_base_shear = st.sidebar.slider("Seismic Base Shear Ah (%)", 0.0, 20.0, 2.5) / 100.0

st.sidebar.header("4. Soil & Footing Parameters")
sbc = st.sidebar.number_input("Safe Bearing Capacity (kN/m²)", value=150.0, step=10.0)

st.sidebar.header("5. Engine Settings")
apply_cracked_modifiers = st.sidebar.checkbox("Use IS 1893 Cracked Sections", value=True)
show_nodes = st.sidebar.checkbox("Show Node Numbers in 3D", value=False)
show_members = st.sidebar.checkbox("Show Member IDs in 3D", value=False)

st.sidebar.header("6. IS Code Combinations")
combo = st.sidebar.selectbox("Select Load Combination", ["1.5 DL + 1.5 LL", "1.2 DL + 1.2 LL + 1.2 EQ", "1.5 DL + 1.5 EQ", "0.9 DL + 1.5 EQ"])
f_dl, f_ll, f_eq = 1.5, 1.5, 0.0
if "1.2" in combo: f_dl, f_ll, f_eq = 1.2, 1.2, 1.2
elif "0.9" in combo: f_dl, f_ll, f_eq = 0.9, 0.0, 1.5
elif "1.5 EQ" in combo: f_dl, f_ll, f_eq = 1.5, 0.0, 1.5

# --- GEOMETRY DATA EDITORS ---
with st.expander("📐 Modify Building Grids & Geometry", expanded=False):
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.write("Z-Elevations"); floors_df = st.data_editor(st.session_state.floors, num_rows="dynamic", width="stretch")
    with col2: st.write("X-Grids"); x_grids_df = st.data_editor(st.session_state.x_grids, num_rows="dynamic", width="stretch")
    with col3: st.write("Y-Grids"); y_grids_df = st.data_editor(st.session_state.y_grids, num_rows="dynamic", width="stretch")
    with col4: st.write("Columns"); cols_df = st.data_editor(st.session_state.cols, num_rows="dynamic", width="stretch")

x_coords_sorted = sorted(list(set([float(r['X_Coord (m)']) for _, r in x_grids_df.iterrows()])))
y_coords_sorted = sorted(list(set([float(r['Y_Coord (m)']) for _, r in y_grids_df.iterrows()])))
z_elevs = {0: 0.0}
curr_z = 0.0
for _, r in floors_df.iterrows():
    curr_z += float(r['Height (m)'])
    z_elevs[int(r['Floor'])] = curr_z

# --- PRODUCTION PDF REPORT CLASS ---
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 8, 'PRODUCTION STRUCTURAL DETAILING REPORT', border=1, ln=1, align='C')
        self.set_font('Arial', 'I', 10)
        self.cell(0, 6, 'Designed as per IS 456:2000 & IS 1893', border=1, ln=1, align='C')
        self.ln(2)
        self.set_font('Arial', 'B', 11)
        self.cell(0, 8, 'Structural Engineer: Mr. D. Mandal, M.Tech. Structures', ln=1, align='R')
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')
    def chapter_title(self, title):
        self.set_font('Arial', 'B', 12)
        self.set_fill_color(200, 220, 255)
        self.cell(0, 8, title, 0, 1, 'L', 1)
        self.ln(4)
    def build_table(self, dataframe):
        self.set_font('Arial', 'B', 8)
        col_width = 190 / len(dataframe.columns)
        for col in dataframe.columns:
            self.cell(col_width, 6, str(col), border=1, align='C')
        self.ln()
        self.set_font('Arial', '', 8)
        for index, row in dataframe.iterrows():
            for val in row:
                self.cell(col_width, 6, str(val), border=1, align='C')
            self.ln()
        self.ln(5)

# --- REBAR DETAILING ENGINE ---
def get_rebar_detail(ast_req, member_type="Beam"):
    areas = {10: 78.5, 12: 113.1, 16: 201.0, 20: 314.1, 25: 490.8, 32: 804.2}
    dias = [10, 12, 16, 20, 25, 32]
    configs = []
    if member_type == "Beam":
        for d in [12, 16, 20, 25, 32]:
            for n in [2, 3, 4, 5, 6]: configs.append((n, d, 0, 0, n*areas[d]))
        for i in range(1, len(dias)):
            for n_main in [2, 3, 4]:
                for n_sec in [1, 2, 3]:
                    if n_main + n_sec <= 6:
                        configs.append((n_main, dias[i], n_sec, dias[i-1], n_main*areas[dias[i]] + n_sec*areas[dias[i-1]]))
    else: 
        for d in [12, 16, 20, 25, 32]:
            for n in [4, 6, 8, 10, 12, 16]: configs.append((n, d, 0, 0, n*areas[d]))
        for i in range(1, len(dias)):
            for n_face in [2, 4, 6, 8]:
                configs.append((4, dias[i], n_face, dias[i-1], 4*areas[dias[i]] + n_face*areas[dias[i-1]]))
    configs.sort(key=lambda x: x[4])
    for c in configs:
        if c[4] >= ast_req:
            if c[2] == 0: return f"{c[0]}-T{c[1]} (Prv: {int(c[4])})"
            else: return f"{c[0]}-T{c[1]} + {c[2]}-T{c[3]} (Prv: {int(c[4])})"
    return "Custom"

def parse_rebar_string(rebar_str):
    if "Prv" not in str(rebar_str): return []
    bars = []
    for part in rebar_str.split(" (Prv")[0].split(" + "):
        if "-T" in part:
            n, d = part.split("-T")
            bars.append((int(n), int(d)))
    return bars

# --- EXACT IS 456 DYNAMIC SHEAR & TORSION CALCULATION ---
def calculate_shear_spacing(Ve_kN, b, d, fck, fy, is_column=False):
    Ve = Ve_kN * 1000
    tau_ve = Ve / (b * d)
    tau_c_max = 0.62 * math.sqrt(max(fck, 1.0))
    Asv = 2 * (math.pi * 8**2 / 4) 
    tau_c = 0.25 * math.sqrt(fck) if not is_column else 0.35 * math.sqrt(fck) 
    if tau_ve > tau_c_max: return 100, "Shear Web Failure"
    if tau_ve <= tau_c: sv = (0.87 * fy * Asv) / (0.4 * b)
    else: sv = (0.87 * fy * Asv * d) / max(Ve - (tau_c * b * d), 0.001)
    sv_max = min(0.75 * d, 300) if not is_column else min(b, 300)
    sv_final = max(min(math.floor(sv / 10) * 10, sv_max), 100) 
    return int(sv_final), "Safe"

def design_beam_is456(b_m, h_m, Mu_pos_kNm, Mu_neg_kNm, Vu_kN, Tu_kNm, fck, fy):
    b, h = max(b_m * 1000, 1.0), max(h_m * 1000, 1.0)
    d = max(h - 40, 1.0) 
    Ve_kN = Vu_kN + 1.6 * (Tu_kNm / b_m) if b_m > 0 else Vu_kN
    Mt_kNm = Tu_kNm * (1 + (h_m / b_m)) / 1.7 if b_m > 0 else 0
    Me_pos, Me_neg = Mu_pos_kNm + Mt_kNm, Mu_neg_kNm + Mt_kNm 
    def calc_ast(Me_kNm):
        Me = Me_kNm * 1e6
        Mu_lim = (0.133 if fy >= 500 else 0.138) * fck * b * d**2
        if Me <= Mu_lim: ast = (0.5 * fck / fy) * (1 - math.sqrt(max(1 - (4.6 * Me) / max(fck * b * d**2, 1.0), 0))) * b * d
        else: ast = ((0.5 * fck / fy) * (1 - math.sqrt(max(1 - (4.6 * Mu_lim) / max(fck * b * d**2, 1.0), 0))) * b * d) + ((Me - Mu_lim) / max(0.87 * fy * d, 1.0))
        return max(ast, 0.85 * b * d / max(fy, 1.0))
    Ast_bot, Ast_top = calc_ast(Me_pos), calc_ast(Me_neg)
    sv, shear_stat = calculate_shear_spacing(Ve_kN, b, d, fck, fy)
    if Tu_kNm > 1.0: shear_stat += " (Closed)"
    return round(Ast_bot, 1), round(Ast_top, 1), sv, shear_stat

def design_column_is456(b_m, h_m, Pu_kN, Mu_kNm, Vu_kN, Tu_kNm, fck, fy):
    b, h = max(b_m * 1000, 1.0), max(h_m * 1000, 1.0)
    Ag, d = b * h, max(h - 40, 1.0)
    Ve_kN = Vu_kN + 1.6 * (Tu_kNm / b_m) if b_m > 0 else Vu_kN
    Me_kNm = Mu_kNm + (Tu_kNm * (1 + (h_m / b_m)) / 1.7 if b_m > 0 else 0)
    Pu, Me = Pu_kN * 1000, Me_kNm * 1e6 
    Asc_axial = (Pu - 0.4 * fck * Ag) / max(0.67 * fy - 0.4 * fck, 1.0) if Pu > 0.4 * fck * Ag else 0
    Asc_req = max(Asc_axial + (Me / max(0.87 * fy * d, 1.0)), 0.008 * Ag)
    status = "Safe"
    if Asc_req > 0.040 * Ag: status = "Over-Reinf"
    if Pu > (0.45 * fck * Ag + 0.75 * fy * (0.04 * Ag)): status = "Crush"
    sv, shear_stat = calculate_shear_spacing(Ve_kN, b, d, fck, fy, is_column=True)
    if "Fail" in shear_stat: status += " | Shear Fail"
    return round(Asc_req, 1), sv, status

# --- ENGINE: BUILD MESH ---
def build_mesh():
    nodes, elements = [], []
    x_map = {str(r['Grid_ID']).strip(): float(r['X_Coord (m)']) for _, r in x_grids_df.iterrows() if pd.notna(r['Grid_ID'])}
    y_map = {str(r['Grid_ID']).strip(): float(r['Y_Coord (m)']) for _, r in y_grids_df.iterrows() if pd.notna(r['Grid_ID'])}
    primary_xy = []
    for _, r in cols_df.iterrows():
        xg, yg = str(r.get('X_Grid', '')).strip(), str(r.get('Y_Grid', '')).strip()
        if xg in x_map and yg in y_map:
            primary_xy.append({'x': x_map[xg] + float(r.get('X_Offset (m)', 0.0)), 'y': y_map[yg] + float(r.get('Y_Offset (m)', 0.0)), 'angle': float(r.get('Angle (deg)', 0.0))})
    nid, eid = 0, 1 
    for f in range(len(floors_df) + 1):
        for pt in primary_xy:
            nodes.append({'id': nid, 'x': pt['x'], 'y': pt['y'], 'z': z_elevs.get(f, 0.0), 'floor': f, 'angle': pt['angle'], 'is_dummy': False})
            nid += 1
    for z in range(len(floors_df)):
        b_nodes = [n for n in nodes if n['floor'] == z and not n['is_dummy']]
        t_nodes = [n for n in nodes if n['floor'] == z + 1 and not n['is_dummy']]
        for bn in b_nodes:
            tn = next((n for n in t_nodes if abs(n['x']-bn['x'])<0.01 and abs(n['y']-bn['y'])<0.01), None)
            if tn:
                elements.append({'id': eid, 'ni': bn['id'], 'nj': tn['id'], 'type': 'Column', 'size': col_size, 'dir': 'Z', 'angle': bn['angle']})
                eid += 1
    for z in range(1, len(floors_df) + 1):
        f_nodes = [n for n in nodes if n['floor'] == z and not n['is_dummy']]
        y_grps = {}
        for n in f_nodes:
            matched = False
            for yk in y_grps.keys():
                if abs(n['y'] - yk) < 0.1: y_grps[yk].append(n); matched = True; break
            if not matched: y_grps[n['y']] = [n]
        for yk, grp in y_grps.items():
            grp = sorted(grp, key=lambda k: k['x'])
            for i in range(len(grp)-1):
                elements.append({'id': eid, 'ni': grp[i]['id'], 'nj': grp[i+1]['id'], 'type': 'Beam', 'size': beam_size, 'dir': 'X', 'angle': 0.0})
                eid += 1
        x_grps = {}
        for n in f_nodes:
            matched = False
            for xk in x_grps.keys():
                if abs(n['x'] - xk) < 0.1: x_grps[xk].append(n); matched = True; break
            if not matched: x_grps[n['x']] = [n]
        for xk, grp in x_grps.items():
            grp = sorted(grp, key=lambda k: k['y'])
            for i in range(len(grp)-1):
                elements.append({'id': eid, 'ni': grp[i]['id'], 'nj': grp[i+1]['id'], 'type': 'Beam', 'size': beam_size, 'dir': 'Y', 'angle': 0.0})
                eid += 1
    diaphragm_nodes = {}
    for z in range(1, len(floors_df) + 1):
        f_nodes = [n for n in nodes if n['floor'] == z and not n['is_dummy']]
        if f_nodes:
            xc, yc = sum(n['x'] for n in f_nodes) / len(f_nodes), sum(n['y'] for n in f_nodes) / len(f_nodes)
            dummy_node = {'id': nid, 'x': xc, 'y': yc, 'z': z_elevs.get(z, 0.0), 'floor': z, 'angle': 0.0, 'is_dummy': True}
            nodes.append(dummy_node)
            diaphragm_nodes[z] = dummy_node
            nid += 1
            for fn in f_nodes:
                elements.append({'id': eid, 'ni': dummy_node['id'], 'nj': fn['id'], 'type': 'Diaphragm', 'size': '0x0', 'dir': 'D', 'angle': 0.0})
                eid += 1
    return nodes, elements, diaphragm_nodes

nodes, elements, diaphragm_nodes = build_mesh()

st.subheader("🖥️ 3D Architectural Viewport")
fig = go.Figure()
for el in elements:
    if el['type'] == 'Diaphragm': continue
    ni, nj = next(n for n in nodes if n['id'] == el['ni']), next(n for n in nodes if n['id'] == el['nj'])
    color = '#1f77b4' if el['type'] == 'Column' else '#d62728'
    fig.add_trace(go.Scatter3d(x=[ni['x'], nj['x']], y=[ni['y'], nj['y']], z=[ni['z'], nj['z']], mode='lines', line=dict(color=color, width=4), hoverinfo='text', text=f"ID: {el['id']}", showlegend=False))
    if show_members: fig.add_trace(go.Scatter3d(x=[(ni['x']+nj['x'])/2], y=[(ni['y']+nj['y'])/2], z=[(ni['z']+nj['z'])/2], mode='text', text=[f"M{el['id']}"], textfont=dict(color='green', size=10), showlegend=False, hoverinfo='none'))

phy_nodes = [n for n in nodes if not n['is_dummy']]
fig.add_trace(go.Scatter3d(x=[n['x'] for n in phy_nodes], y=[n['y'] for n in phy_nodes], z=[n['z'] for n in phy_nodes], mode='markers', marker=dict(size=3, color='black'), hoverinfo='none', showlegend=False))

if show_nodes:
    fig.add_trace(go.Scatter3d(x=[n['x'] for n in phy_nodes], y=[n['y'] for n in phy_nodes], z=[n['z'] for n in phy_nodes], mode='text', text=[f"N{n['id']}" for n in phy_nodes], textfont=dict(color='purple', size=10), textposition="top center", showlegend=False, hoverinfo='none'))

fig.update_layout(scene=dict(xaxis_title='X', yaxis_title='Y', zaxis_title='Z', aspectmode='data'), margin=dict(l=0, r=0, b=0, t=0), height=500)
st.plotly_chart(fig, width="stretch")

def calc_yield_line_udl(ni, nj, el_dir, q_area):
    L_beam = math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2)
    if L_beam < 0.1: return 0.0
    if el_dir == 'X':
        y = ni['y']
        idx = y_coords_sorted.index(y) if y in y_coords_sorted else -1
        L_perp1 = abs(y_coords_sorted[idx+1] - y) if idx >= 0 and idx < len(y_coords_sorted)-1 else 0
        L_perp2 = abs(y - y_coords_sorted[idx-1]) if idx > 0 else 0
    else:
        x = ni['x']
        idx = x_coords_sorted.index(x) if x in x_coords_sorted else -1
        L_perp1 = abs(x_coords_sorted[idx+1] - x) if idx >= 0 and idx < len(x_coords_sorted)-1 else 0
        L_perp2 = abs(x - x_coords_sorted[idx-1]) if idx > 0 else 0
    def get_eq_load(Lb, Lp, q):
        Lb = max(Lb, 0.001)
        if Lp <= 0.01: return 0.0
        if Lb >= Lp: return (q * Lp / 6.0) * (3.0 - (Lp / Lb)**2)
        else: return (q * Lb / 3.0)
    return get_eq_load(L_beam, L_perp1, q_area) + get_eq_load(L_beam, L_perp2, q_area)

def get_props(size_str, el_type):
    if el_type == 'Diaphragm': return 100.0, 1e-6, 1e-6, 1e-6 
    b, h = map(float, size_str.split('x'))
    b, h = max(b/1000.0, 0.001), max(h/1000.0, 0.001)
    A = b * h
    Iy, Iz = (h * b**3) / 12.0, (b * h**3) / 12.0
    dim_min, dim_max = min(b, h), max(b, h)
    J = (dim_min**3 * dim_max) * (1/3 - 0.21 * (dim_min/dim_max) * (1 - (dim_min**4) / (12 * dim_max**4)))
    if apply_cracked_modifiers:
        if el_type == 'Column': Iy *= 0.7; Iz *= 0.7
        elif el_type == 'Beam': Iy *= 0.35; Iz *= 0.35
        J *= 0.1 
    return A, Iy, Iz, J

def local_k(A, Iy, Iz, J, L):
    L = max(L, 0.001)
    k = np.zeros((12, 12))
    k[0,0]=k[6,6]= E_conc*A/L; k[0,6]=k[6,0]= -E_conc*A/L
    k[3,3]=k[9,9]= G_conc*J/L; k[3,9]=k[9,3]= -G_conc*J/L
    k[2,2]=k[8,8]= 12*E_conc*Iy/L**3; k[4,4]=k[10,10]= 4*E_conc*Iy/L
    k[2,4]=k[2,10]=k[4,2]=k[10,2]= -6*E_conc*Iy/L**2; k[8,4]=k[8,10]=k[4,8]=k[10,8]= 6*E_conc*Iy/L**2
    k[2,8]=k[8,2] = -12*E_conc*Iy/L**3; k[4,10]=k[10,4] = 2*E_conc*Iy/L
    k[1,1]=k[7,7]= 12*E_conc*Iz/L**3; k[5,5]=k[11,11]= 4*E_conc*Iz/L
    k[1,5]=k[1,11]=k[5,1]=k[11,1]= 6*E_conc*Iz/L**2; k[7,5]=k[7,11]=k[5,7]=k[11,7]= -6*E_conc*Iz/L**2
    k[1,7]=k[7,1] = -12*E_conc*Iz/L**3; k[5,11]=k[11,5] = 2*E_conc*Iz/L
    return k + (np.eye(12) * 1e-9) 

def transform_matrix(ni, nj, angle_deg):
    dx, dy, dz = nj['x']-ni['x'], nj['y']-ni['y'], nj['z']-ni['z']
    L = max(math.sqrt(dx**2 + dy**2 + dz**2), 0.001)
    cx, cy, cz = dx/L, dy/L, dz/L
    if abs(cx) < 1e-6 and abs(cy) < 1e-6: lam = np.array([[0, 0, 1*np.sign(cz)], [0, 1, 0], [-1*np.sign(cz), 0, 0]])
    else: lam = np.array([[cx, cy, cz], [-cx*cz/math.sqrt(cx**2 + cy**2), -cy*cz/math.sqrt(cx**2 + cy**2), math.sqrt(cx**2 + cy**2)], [-cy/math.sqrt(cx**2 + cy**2), cx/math.sqrt(cx**2 + cy**2), 0]])
    if angle_deg != 0.0:
        c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
        lam = np.array([[1, 0, 0], [0, c, s], [0, -s, c]]) @ lam
    T = np.zeros((12, 12))
    for i in range(4): T[i*3:(i+1)*3, i*3:(i+1)*3] = lam
    return T

st.divider()

if st.button("🚀 Execute Analysis & Generate CAD / PDF", type="primary", width="stretch"):
    with st.spinner("Solving Matrix, Running Code Checks & Building CAD Files..."):
        ndof = len(nodes) * 6
        K_global = np.zeros((ndof, ndof))
        F_global = np.zeros(ndof)
        
        floor_seismic_W = {z: 0.0 for z in range(1, len(floors_df)+1)}
        area_dl = (slab_thick/1000.0)*25.0 + floor_finish
        total_q_area = (f_dl * area_dl) + (f_ll * live_load)
        
        for el in elements:
            if el['type'] == 'Diaphragm': continue
            ni, nj = next(n for n in nodes if n['id'] == el['ni']), next(n for n in nodes if n['id'] == el['nj'])
            L = max(math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2), 0.001)
            el['L'], el['ni_n'], el['nj_n'] = L, ni, nj
            el['A'], el['Iy'], el['Iz'], el['J'] = get_props(el['size'], el['type'])
            
            if el['type'] == 'Beam': floor_seismic_W[ni['floor']] += (calc_yield_line_udl(ni, nj, el['dir'], area_dl + 0.25*live_load) + (wall_thick/1000.0 * 3.0 * 20.0) + (el['A'] * 25.0)) * L
            elif el['type'] == 'Column':
                if ni['floor'] > 0: floor_seismic_W[ni['floor']] += (el['A'] * 25.0 * L) / 2.0
                if nj['floor'] > 0: floor_seismic_W[nj['floor']] += (el['A'] * 25.0 * L) / 2.0

        for el in elements:
            if 'L' not in el:
                ni, nj = next(n for n in nodes if n['id'] == el['ni']), next(n for n in nodes if n['id'] == el['nj'])
                el['L'] = max(math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2), 0.001)
                el['A'], el['Iy'], el['Iz'], el['J'] = get_props(el['size'], el['type'])
                el['ni_n'], el['nj_n'] = ni, nj
            k_glob = transform_matrix(el['ni_n'], el['nj_n'], el['angle']).T @ local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L']) @ transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            idx = [el['ni_n']['id']*6+i for i in range(6)] + [el['nj_n']['id']*6+i for i in range(6)]
            for r in range(12):
                for c in range(12): K_global[idx[r], idx[c]] += k_glob[r, c]
                    
            if el['type'] == 'Beam':
                w = calc_yield_line_udl(el['ni_n'], el['nj_n'], el['dir'], total_q_area) + (f_dl * wall_thick/1000.0 * 3.0 * 20.0) + (f_dl * el['A'] * 25.0)
                el['applied_w'] = w 
                V, M = (w * el['L']) / 2.0, (w * el['L']**2) / 12.0
                F_loc = np.zeros(12); F_loc[1], F_loc[5], F_loc[7], F_loc[11] = V, M, V, -M
                F_g = transform_matrix(el['ni_n'], el['nj_n'], el['angle']).T @ F_loc
                for i in range(12): F_global[idx[i]] -= F_g[i]
                
        if f_eq > 0:
            Vb = eq_base_shear * sum(floor_seismic_W.values()) * f_eq
            sum_wh2 = sum([floor_seismic_W[z] * (z_elevs[z]**2) for z in floor_seismic_W])
            for z in range(1, len(floors_df)+1):
                if sum_wh2 > 0 and z in diaphragm_nodes: F_global[diaphragm_nodes[z]['id'] * 6] += Vb * (floor_seismic_W[z] * (z_elevs[z]**2)) / sum_wh2

        fixed = [n['id']*6 + d for n in nodes if n['z'] == 0 for d in range(6)]
        free = sorted(list(set(range(ndof)) - set(fixed)))
        U_glob = np.zeros(ndof)
        if len(free) > 0: U_glob[free] = np.linalg.lstsq(K_global[np.ix_(free, free)], F_global[free], rcond=None)[0]
        
        analysis_data, design_data, bbs_records = [], [], []
        base_reactions = {}

        for el in elements:
            if el['type'] == 'Diaphragm': continue
            T = transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            k_loc = local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L'])
            i_dof, j_dof = el['ni_n']['id'] * 6, el['nj_n']['id'] * 6
            f_int = k_loc @ (T @ np.concatenate((U_glob[i_dof:i_dof+6], U_glob[j_dof:j_dof+6])))
            
            axial = max(abs(f_int[0]), abs(f_int[6]))
            shear = max(abs(f_int[1]), abs(f_int[2]), abs(f_int[7]), abs(f_int[8]))
            torsion_max = max(abs(f_int[3]), abs(f_int[9]))
            Mu_neg_max = max(abs(f_int[5]), abs(f_int[11]))
            Mu_pos_max = 0.0
            
            if el['type'] == 'Beam' and 'applied_w' in el:
                w, Vy_i = el['applied_w'], f_int[1] 
                x_max = Vy_i / max(w, 0.001) if w > 0 else -1
                if 0 < x_max < el['L']: Mu_pos_max = abs(f_int[5] + (Vy_i * x_max) - (0.5 * w * x_max**2))

            if el['type'] == 'Column' and el['ni_n']['z'] == 0:
                base_reactions[el['ni_n']['id']] = {'Pu': abs(f_int[0]), 'Col_Size': el['size'], 'x': el['ni_n']['x'], 'y': el['ni_n']['y']}

            analysis_data.append({"ID": f"M{el['id']}", "Type": el['type'], "Flr": el['ni_n']['floor'], "L(m)": round(el['L'],2), "P(kN)": round(axial,1), "V(kN)": round(shear,1), "M(kN.m)": round(max(Mu_pos_max, Mu_neg_max),1)})
            b_m, h_m = map(lambda x: float(x)/1000.0, el['size'].split('x'))
            if el['type'] == 'Beam':
                req_ast_bot, req_ast_top, sv_mm, stat = design_beam_is456(b_m, h_m, Mu_pos_max, Mu_neg_max, shear, torsion_max, fck, fy)
                rebar_bot, rebar_top = get_rebar_detail(req_ast_bot, "Beam"), get_rebar_detail(req_ast_top, "Beam")
                design_data.append({"ID": f"M{el['id']}", "Type": "Beam", "Flr": el['ni_n']['floor'], "Size": el['size'], "Bot Rebar": rebar_bot, "Top Rebar": rebar_top, "Ties": f"T8@{sv_mm}"})
                
                for (count, dia) in parse_rebar_string(rebar_bot):
                    cut_L = el['L'] - 0.05 + (50 * dia/1000.0) 
                    bbs_records.append({"Element": f"M{el['id']} (B)", "Type": "Bot Span", "Dia": dia, "No": count, "Cut L(m)": round(cut_L, 2), "Wt(kg)": round((dia**2/162.0)*cut_L*count, 2)})
                for (count, dia) in parse_rebar_string(rebar_top):
                    cut_L = el['L'] - 0.05 + (50 * dia/1000.0) 
                    bbs_records.append({"Element": f"M{el['id']} (B)", "Type": "Top Support", "Dia": dia, "No": count, "Cut L(m)": round(cut_L, 2), "Wt(kg)": round((dia**2/162.0)*cut_L*count, 2)})
                
                hanger_dia = 10 if b_m <= 0.25 else 12 
                hanger_L = el['L'] - 0.05 + (50 * hanger_dia/1000.0)
                bbs_records.append({"Element": f"M{el['id']} (B)", "Type": "Hanger", "Dia": hanger_dia, "No": 2, "Cut L(m)": round(hanger_L, 2), "Wt(kg)": round((hanger_dia**2/162.0)*hanger_L*2, 2)})

            else:
                req_ast, sv_mm, stat = design_column_is456(b_m, h_m, axial, max(Mu_neg_max, Mu_pos_max), shear, torsion_max, fck, fy)
                rebar_str = get_rebar_detail(req_ast, "Column")
                design_data.append({"ID": f"M{el['id']}", "Type": "Column", "Flr": el['ni_n']['floor'], "Size": el['size'], "Bot Rebar": "-", "Top Rebar": rebar_str, "Ties": f"T8@{sv_mm}"})
                for (count, dia) in parse_rebar_string(rebar_str):
                    cut_L = el['L'] + (50 * dia/1000.0) 
                    bbs_records.append({"Element": f"M{el['id']} (C)", "Type": "Main Vert", "Dia": dia, "No": count, "Cut L(m)": round(cut_L, 2), "Wt(kg)": round((dia**2/162.0)*cut_L*count, 2)})

            s_cut = 2*(b_m - 0.05 + h_m - 0.05) + (24 * 0.008) if el['type'] == 'Beam' else 2*(b_m - 0.08 + h_m - 0.08) + (24 * 0.008)
            n_st = int(el['L'] / (sv_mm / 1000.0)) + 1
            bbs_records.append({"Element": f"M{el['id']}", "Type": "Tie/Stirrup", "Dia": 8, "No": n_st, "Cut L(m)": round(s_cut, 2), "Wt(kg)": round((8**2/162.0)*s_cut*n_st, 2)})

        # --- SLAB CHECK & FOOTING DESIGN ---
        x_spans = [x_coords_sorted[i+1] - x_coords_sorted[i] for i in range(len(x_coords_sorted)-1) if (x_coords_sorted[i+1] - x_coords_sorted[i]) > 0.1]
        y_spans = [y_coords_sorted[i+1] - y_coords_sorted[i] for i in range(len(y_coords_sorted)-1) if (y_coords_sorted[i+1] - y_coords_sorted[i]) > 0.1]
        Lx, Ly = max(min(x_spans) if x_spans else 1.0, 0.001), max(max(y_spans) if y_spans else 1.0, 0.001)
        ratio = Ly / Lx
        alpha_pos = np.interp(ratio, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0], [0.032, 0.037, 0.043, 0.047, 0.051, 0.053, 0.060, 0.065]) if ratio <= 2.0 else 0.125
        alpha_neg = np.interp(ratio, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0], [0.043, 0.048, 0.057, 0.064, 0.068, 0.072, 0.080, 0.087]) if ratio <= 2.0 else 0.125
        w_u_slab = 1.5 * (live_load + floor_finish + (slab_thick/1000.0)*25.0)
        Mu_pos, Mu_neg = alpha_pos * w_u_slab * (Lx**2), alpha_neg * w_u_slab * (Lx**2)
        d_eff_slab = max(slab_thick - 25, 1.0)
        spc_pos = min(math.floor(1000 / (max((0.5*fck/max(fy,1.0))*(1-math.sqrt(max(1-(4.6*Mu_pos*1e6)/(max(fck,1.0)*1000*d_eff_slab**2),0)))*1000*d_eff_slab, 0.0012*1000*slab_thick) / 78.5) / 10)*10, 300) 
        spc_neg = min(math.floor(1000 / (max((0.5*fck/max(fy,1.0))*(1-math.sqrt(max(1-(4.6*Mu_neg*1e6)/(max(fck,1.0)*1000*d_eff_slab**2),0)))*1000*d_eff_slab, 0.0012*1000*slab_thick) / 78.5) / 10)*10, 300) 
        spc_tor = min(math.floor(1000 / (0.75 * max((0.5*fck/max(fy,1.0))*(1-math.sqrt(max(1-(4.6*Mu_pos*1e6)/(max(fck,1.0)*1000*d_eff_slab**2),0)))*1000*d_eff_slab, 0.0012*1000*slab_thick) / 78.5) / 10)*10, 300)
        
        d_req_flex = math.sqrt((max(Mu_pos, Mu_neg) * 1e6) / ((0.133 if fy>=500 else 0.138) * max(fck, 1.0) * 1000))
        d_req_def = (Lx * 1000) / 28.0 
        safe_slab = slab_thick >= max(d_req_flex, d_req_def) + 25

        for flr in range(1, len(floors_df)+1):
            n_main, l_main = int(Ly / (spc_pos/1000.0)) + 1, Lx + 1.0
            n_dist, l_dist = int(Lx / 0.20) + 1, Ly + 1.0
            bbs_records.append({"Element": f"Slab F{flr}", "Type": "Bot Main", "Dia": 10, "No": n_main, "Cut L(m)": round(l_main,2), "Wt(kg)": round((10**2/162.0)*l_main*n_main,2)})
            bbs_records.append({"Element": f"Slab F{flr}", "Type": "Bot Dist", "Dia": 10, "No": n_dist, "Cut L(m)": round(l_dist,2), "Wt(kg)": round((10**2/162.0)*l_dist*n_dist,2)})
            n_top, l_top = int(Ly / (spc_neg/1000.0)) + 1, 0.6 * Lx
            bbs_records.append({"Element": f"Slab F{flr}", "Type": "Top Supp", "Dia": 10, "No": n_top*2, "Cut L(m)": round(l_top,2), "Wt(kg)": round((10**2/162.0)*l_top*(n_top*2),2)})
            n_tor = int((Lx/5.0) / (spc_tor/1000.0)) * 2 
            bbs_records.append({"Element": f"Slab F{flr}", "Type": "Corner Tor", "Dia": 10, "No": n_tor*4, "Cut L(m)": round(Lx/5.0,2), "Wt(kg)": round((10**2/162.0)*(Lx/5.0)*(n_tor*4),2)})

        footing_geoms, footing_results = {}, []
        for nid, data in base_reactions.items():
            P_service = data['Pu'] / 1.5
            Side_L = max(math.ceil(math.sqrt((P_service * 1.1) / max(sbc, 1.0)) * 10) / 10.0, 1.0)
            footing_geoms[nid] = {'x': data['x'], 'y': data['y'], 'L': Side_L}
            col_b, col_h = map(lambda x: float(x)/1000.0, data['Col_Size'].split('x'))
            net_upward = data['Pu'] / (Side_L**2)
            Mu_footing = net_upward * Side_L * (max((Side_L - max(col_b, col_h)) / 2.0, 0.01)**2) / 2.0
            d_req_flex = math.sqrt((Mu_footing * 1e6) / ((0.133 if fy>=500 else 0.138) * max(fck, 1.0) * (Side_L*1000)))
            D_prov = max(300, math.ceil((d_req_flex + 50) / 50.0) * 50)
            d_eff = D_prov - 50 
            while True:
                d_m = d_eff / 1000.0
                V_punch = max(data['Pu'] - (net_upward * (col_b + d_m) * (col_h + d_m)), 0)
                if (V_punch * 1000) / (2 * ((col_b + d_m) + (col_h + d_m)) * 1000 * d_eff) <= min(0.5 + (min(col_b, col_h) / max(col_b, col_h)), 1.0) * 0.25 * math.sqrt(max(fck, 1.0)): break
                D_prov += 50; d_eff = D_prov - 50
            ftg_spacing = min(math.floor(1000 / (max((0.5*fck/max(fy,1.0))*(1-math.sqrt(max(1-(4.6*Mu_footing*1e6)/(max(fck,1.0)*(Side_L*1000)*d_eff**2),0)))*(Side_L*1000)*d_eff, 0.0012*(Side_L*1000)*D_prov) / Side_L / 113.1) / 10)*10, 300)
            footing_results.append({"Node": f"N{nid}", "P(kN)": round(data['Pu'], 1), "Size": f"{Side_L}x{Side_L}", "D(mm)": int(D_prov), "Mesh": f"T12@{int(ftg_spacing)}"})
            num_ftg, l_ftg = int((Side_L - 0.1) / (ftg_spacing/1000.0)) + 1, (Side_L - 0.1) + 2*(D_prov/1000.0 - 0.1)
            bbs_records.append({"Element": f"Foot N{nid}", "Type": "Base Mesh", "Dia": 12, "No": num_ftg*2, "Cut L(m)": round(l_ftg,2), "Wt(kg)": round((12**2/162.0)*l_ftg*(num_ftg*2),2)})

        clashes, processed = [], set()
        node_ids = list(footing_geoms.keys())
        for i in range(len(node_ids)):
            for j in range(i+1, len(node_ids)):
                n1, n2 = node_ids[i], node_ids[j]
                if n1 in processed or n2 in processed: continue
                f1, f2 = footing_geoms[n1], footing_geoms[n2]
                dist = math.hypot(f1['x'] - f2['x'], f1['y'] - f2['y'])
                if dist < (f1['L'] / 2.0) + (f2['L'] / 2.0):
                    clashes.append((n1, n2)); processed.add(n1); processed.add(n2)

        # --- GENERATE PDF REPORT ---
        pdf = PDFReport()
        pdf.add_page()
        pdf.chapter_title("1. BEAM & COLUMN DETAILING")
        pdf.build_table(pd.DataFrame(design_data))
        pdf.chapter_title("2. FOUNDATION SIZING (PUNCHING SHEAR SAFE)")
        pdf.build_table(pd.DataFrame(footing_results))
        pdf.chapter_title("3. MONOLITHIC TWO-WAY SLAB (IS 456 Annex D)")
        slab_data = [{"Panel": f"{round(Lx,2)}m x {round(Ly,2)}m", "Thickness": f"{slab_thick} mm", "Bot Span Mesh": f"T10 @ {int(spc_pos)} c/c", "Top Hogging": f"T10 @ {int(spc_neg)} c/c", "Corner Torsion": f"T10 @ {int(spc_tor)} c/c"}]
        pdf.build_table(pd.DataFrame(slab_data))
        pdf.chapter_title("4. BAR BENDING SCHEDULE (BBS)")
        df_bbs = pd.DataFrame(bbs_records)
        pdf.build_table(df_bbs)
        pdf.set_font('Arial', 'B', 12)
        total_wt_kg = df_bbs["Wt(kg)"].sum()
        pdf.cell(0, 10, f'TOTAL STEEL TONNAGE REQUIRED: {total_wt_kg / 1000.0:.2f} Metric Tons', 0, 1, 'R')
        pdf_bytes = pdf.output(dest='S').encode('latin-1')

        # --- GENERATE DXF (LibreCAD/AutoCAD WITH DETAILED SP-34 REBAR) ---
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        doc.layers.add('GRIDS', color=8, linetype='DASHED')
        doc.layers.add('CONCRETE_OUTLINE', color=2)
        doc.layers.add('REBAR_MAIN', color=1)
        doc.layers.add('REBAR_TIES', color=3)
        doc.layers.add('DIMENSIONS', color=6)
        doc.layers.add('ANNOTATIONS', color=7)

        def add_dim(p1, p2, offset, text, is_vert=False):
            if not is_vert:
                msp.add_line((p1[0], p1[1]), (p1[0], p1[1]+offset), dxfattribs={'layer': 'DIMENSIONS'})
                msp.add_line((p2[0], p2[1]), (p2[0], p2[1]+offset), dxfattribs={'layer': 'DIMENSIONS'})
                dy = p1[1]+offset - (0.2 if offset>0 else -0.2)
                msp.add_line((p1[0], dy), (p2[0], dy), dxfattribs={'layer': 'DIMENSIONS'})
                msp.add_text(text, dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.12}).set_placement(((p1[0]+p2[0])/2 - len(text)*0.04, dy+0.05))
            else:
                msp.add_line((p1[0], p1[1]), (p1[0]+offset, p1[1]), dxfattribs={'layer': 'DIMENSIONS'})
                msp.add_line((p2[0], p2[1]), (p2[0]+offset, p2[1]), dxfattribs={'layer': 'DIMENSIONS'})
                dx = p1[0]+offset - (0.2 if offset>0 else -0.2)
                msp.add_line((dx, p1[1]), (dx, p2[1]), dxfattribs={'layer': 'DIMENSIONS'})
                msp.add_text(text, dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.12}).set_placement((dx+0.05, (p1[1]+p2[1])/2 - 0.06))

        max_x = max(x_coords_sorted) if x_coords_sorted else 10
        max_y = max(y_coords_sorted) if y_coords_sorted else 10
        offset_x = max_x + 5.0 
        
        # 1. Floor Framing Plans
        for idx, row in floors_df.iterrows():
            f_num = int(row['Floor'])
            bx = idx * offset_x 
            msp.add_text(f"FLOOR {f_num} STRUCTURAL FRAMING PLAN", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.4}).set_placement((bx, max_y + 2.0))
            for _, gx in x_grids_df.iterrows():
                x = bx + float(gx['X_Coord (m)'])
                msp.add_line((x, -1.5), (x, max_y + 1.5), dxfattribs={'layer': 'GRIDS'})
                msp.add_circle((x, max_y + 1.9), radius=0.4, dxfattribs={'layer': 'GRIDS'})
                msp.add_text(str(gx['Grid_ID']), dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.3}).set_placement((x - 0.12, max_y + 1.75))
            for _, gy in y_grids_df.iterrows():
                y = float(gy['Y_Coord (m)'])
                msp.add_line((bx - 1.5, y), (bx + max_x + 1.5, y), dxfattribs={'layer': 'GRIDS'})
                msp.add_circle((bx - 1.9, y), radius=0.4, dxfattribs={'layer': 'GRIDS'})
                msp.add_text(str(gy['Grid_ID']), dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.3}).set_placement((bx - 2.05, y - 0.12))
                
            col_b_m, col_h_m = map(lambda val: float(val)/1000.0, col_size.split('x'))
            f_cols = [el for el in elements if el['type'] == 'Column' and el['nj_n']['floor'] == f_num]
            for col in f_cols:
                cx, cy = bx + col['nj_n']['x'], col['nj_n']['y']
                msp.add_lwpolyline([(cx - col_b_m/2, cy - col_h_m/2), (cx + col_b_m/2, cy - col_h_m/2), (cx + col_b_m/2, cy + col_h_m/2), (cx - col_b_m/2, cy + col_h_m/2), (cx - col_b_m/2, cy - col_h_m/2)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
                
            beam_b_m, beam_h_m = map(lambda val: float(val)/1000.0, beam_size.split('x'))
            f_beams = [el for el in elements if el['type'] == 'Beam' and el['ni_n']['floor'] == f_num]
            for beam in f_beams:
                nx1, ny1 = bx + beam['ni_n']['x'], beam['ni_n']['y']
                nx2, ny2 = bx + beam['nj_n']['x'], beam['nj_n']['y']
                if abs(ny1 - ny2) < 0.01:
                    msp.add_line((nx1, ny1 + beam_b_m/2), (nx2, ny2 + beam_b_m/2), dxfattribs={'layer': 'CONCRETE_OUTLINE'})
                    msp.add_line((nx1, ny1 - beam_b_m/2), (nx2, ny2 - beam_b_m/2), dxfattribs={'layer': 'CONCRETE_OUTLINE'})
                else: 
                    msp.add_line((nx1 + beam_b_m/2, ny1), (nx2 + beam_b_m/2, ny2), dxfattribs={'layer': 'CONCRETE_OUTLINE'})
                    msp.add_line((nx1 - beam_b_m/2, ny1), (nx2 - beam_b_m/2, ny2), dxfattribs={'layer': 'CONCRETE_OUTLINE'})

        # 2. Detailed Reinforcement Sections
        det_x = len(floors_df) * offset_x + 2.0
        msp.add_text("TYPICAL DETAILED REINFORCEMENT SECTIONS (IS 456 / SP 34)", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.5}).set_placement((det_x, max_y + 2.0))
        
        # 2A. Column Details (L-Sec and C/S)
        col_list = [d for d in design_data if d['Type'] == 'Column']
        if col_list:
            c_det = col_list[0] 
            cb, ch = map(lambda x: float(x)/1000.0, c_det['Size'].split('x'))
            cx, cy = det_x, max_y - 1.0
            
            # Column L-Sec
            msp.add_lwpolyline([(cx, cy), (cx+cb, cy), (cx+cb, cy-3.0), (cx, cy-3.0), (cx, cy)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_line((cx-0.3, cy), (cx+cb+0.3, cy), dxfattribs={'layer': 'GRIDS'}) 
            msp.add_line((cx-0.3, cy-3.0), (cx+cb+0.3, cy-3.0), dxfattribs={'layer': 'GRIDS'}) 
            msp.add_line((cx+0.04, cy+0.5), (cx+0.04, cy-3.0-0.5), dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_line((cx+cb-0.04, cy+0.5), (cx+cb-0.04, cy-3.0-0.5), dxfattribs={'layer': 'REBAR_MAIN'})
            # Splice
            msp.add_line((cx+0.06, cy-3.0), (cx+0.06, cy-3.0+0.6), dxfattribs={'layer': 'REBAR_MAIN'})
            sv_m = float(c_det['Ties'].split('@')[1].replace('c/c','').strip()) / 1000.0
            for i in range(int(3.0/sv_m)): msp.add_line((cx+0.04, cy-3.0+(i*sv_m)), (cx+cb-0.04, cy-3.0+(i*sv_m)), dxfattribs={'layer': 'REBAR_TIES'})
            add_dim((cx-0.3, cy-3.0), (cx-0.3, cy), -0.5, "Floor Ht 3.0m", True)
            add_dim((cx, cy+0.2), (cx+cb, cy+0.2), 0.4, f"w={int(cb*1000)}")
            msp.add_text("COLUMN L-SECTION", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cx, cy - 3.8))
            
            # Column C/S
            cs_x = cx + cb + 1.5
            msp.add_lwpolyline([(cs_x,cy-1.0), (cs_x+cb,cy-1.0), (cs_x+cb,cy-1.0-ch), (cs_x,cy-1.0-ch), (cs_x,cy-1.0)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_lwpolyline([(cs_x+0.04,cy-1.0-0.04), (cs_x+cb-0.04,cy-1.0-0.04), (cs_x+cb-0.04,cy-1.0-ch+0.04), (cs_x+0.04,cy-1.0-ch+0.04), (cs_x+0.04,cy-1.0-0.04)], dxfattribs={'layer': 'REBAR_TIES'})
            for px, py in [(cs_x+0.05,cy-1.0-0.05), (cs_x+cb-0.05,cy-1.0-0.05), (cs_x+cb-0.05,cy-1.0-ch+0.05), (cs_x+0.05,cy-1.0-ch+0.05)]:
                msp.add_circle((px, py), radius=0.015, dxfattribs={'layer': 'REBAR_MAIN'})
            add_dim((cs_x, cy-1.0+0.1), (cs_x+cb, cy-1.0+0.1), 0.3, f"{int(cb*1000)}")
            add_dim((cs_x+cb+0.1, cy-1.0-ch), (cs_x+cb+0.1, cy-1.0), 0.3, f"{int(ch*1000)}", True)
            msp.add_text("COLUMN C/S", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cs_x, cy - 1.0 - ch - 0.4))
            msp.add_text(f"Main: {c_det['Top Rebar']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.15}).set_placement((cs_x, cy - 1.0 - ch - 0.7))
            msp.add_text(f"Ties: {c_det['Ties']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.15}).set_placement((cs_x, cy - 1.0 - ch - 0.9))

        # 2B. Beam Details (L-Sec and C/S)
        bm_list = [d for d in design_data if d['Type'] == 'Beam']
        if bm_list:
            b_det = bm_list[0]
            bb, bh = map(lambda x: float(x)/1000.0, b_det['Size'].split('x'))
            cx, cy = det_x + 4.5, max_y - 1.0
            
            # Beam L-Sec
            span = 4.0
            msp.add_lwpolyline([(cx, cy), (cx+span, cy), (cx+span, cy-bh), (cx, cy-bh), (cx, cy)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_line((cx, cy+0.2), (cx, cy-bh-0.5), dxfattribs={'layer': 'GRIDS'}) # Supp 1
            msp.add_line((cx+span, cy+0.2), (cx+span, cy-bh-0.5), dxfattribs={'layer': 'GRIDS'}) # Supp 2
            
            # Bot main
            msp.add_line((cx+0.05, cy-bh+0.03), (cx+span-0.05, cy-bh+0.03), dxfattribs={'layer': 'REBAR_MAIN'})
            # Top extra (0.3L)
            msp.add_line((cx+0.05, cy-0.03), (cx+0.3*span, cy-0.03), dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_line((cx+span-0.3*span, cy-0.03), (cx+span-0.05, cy-0.03), dxfattribs={'layer': 'REBAR_MAIN'})
            # Hanger
            msp.add_line((cx+0.3*span, cy-0.03), (cx+span-0.3*span, cy-0.03), dxfattribs={'layer': 'REBAR_TIES'})
            
            sv_m = float(b_det['Ties'].split('@')[1].replace('c/c','').strip()) / 1000.0
            for i in range(int(span/sv_m)): msp.add_line((cx+(i*sv_m), cy-0.03), (cx+(i*sv_m), cy-bh+0.03), dxfattribs={'layer': 'REBAR_TIES'})
            
            add_dim((cx, cy+0.1), (cx+span, cy+0.1), 0.4, f"Clear Span L")
            msp.add_text("BEAM L-SECTION", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cx+span/2 - 0.8, cy - bh - 0.6))
            
            # Beam C/S
            cs_x = cx + span + 1.0
            msp.add_lwpolyline([(cs_x,cy), (cs_x+bb,cy), (cs_x+bb,cy-bh), (cs_x,cy-bh), (cs_x,cy)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_lwpolyline([(cs_x+0.025,cy-0.025), (cs_x+bb-0.025,cy-0.025), (cs_x+bb-0.025,cy-bh+0.025), (cs_x+0.025,cy-bh+0.025), (cs_x+0.025,cy-0.025)], dxfattribs={'layer': 'REBAR_TIES'})
            msp.add_circle((cs_x+0.04, cy-bh+0.04), radius=0.012, dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_circle((cs_x+bb-0.04, cy-bh+0.04), radius=0.012, dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_circle((cs_x+0.04, cy-0.04), radius=0.012, dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_circle((cs_x+bb-0.04, cy-0.04), radius=0.012, dxfattribs={'layer': 'REBAR_MAIN'})
            
            add_dim((cs_x, cy+0.1), (cs_x+bb, cy+0.1), 0.3, f"{int(bb*1000)}")
            add_dim((cs_x+bb+0.1, cy-bh), (cs_x+bb+0.1, cy), 0.3, f"{int(bh*1000)}", True)
            msp.add_text("BEAM C/S", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cs_x, cy - bh - 0.4))
            msp.add_text(f"Top: {b_det['Top Rebar']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.12}).set_placement((cs_x, cy - bh - 0.6))
            msp.add_text(f"Bot: {b_det['Bot Rebar']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.12}).set_placement((cs_x, cy - bh - 0.8))
            msp.add_text(f"Stirrups: {b_det['Ties']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.12}).set_placement((cs_x, cy - bh - 1.0))

        # 2C. Footing Detail (Plan & Elev)
        if footing_results:
            f_det = footing_results[0]
            fl = float(f_det['Size'].split('x')[0])
            fd = f_det['D(mm)'] / 1000.0
            cx, cy = det_x, max_y - 7.0
            
            # Plan
            msp.add_lwpolyline([(cx, cy), (cx+fl, cy), (cx+fl, cy-fl), (cx, cy-fl), (cx, cy)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_lwpolyline([(cx+fl/2-0.15, cy-fl/2+0.22), (cx+fl/2+0.15, cy-fl/2+0.22), (cx+fl/2+0.15, cy-fl/2-0.22), (cx+fl/2-0.15, cy-fl/2-0.22), (cx+fl/2-0.15, cy-fl/2+0.22)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            spc = float(f_det['Mesh'].split('@')[1].replace('c/c','').strip()) / 1000.0
            for i in range(int(fl/spc)):
                msp.add_line((cx+0.05+(i*spc), cy-0.05), (cx+0.05+(i*spc), cy-fl+0.05), dxfattribs={'layer': 'REBAR_MAIN'})
                msp.add_line((cx+0.05, cy-0.05-(i*spc)), (cx+fl-0.05, cy-0.05-(i*spc)), dxfattribs={'layer': 'REBAR_MAIN'})
            add_dim((cx, cy+0.1), (cx+fl, cy+0.1), 0.4, f"{fl}m")
            add_dim((cx-0.1, cy-fl), (cx-0.1, cy), -0.4, f"{fl}m", True)
            msp.add_text("FOOTING TOP VIEW (PLAN)", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cx, cy - fl - 0.4))
            
            # Elevation
            ex, ey = cx + fl + 2.0, cy - fl
            msp.add_lwpolyline([(ex, ey), (ex+fl, ey), (ex+fl, ey+0.15), (ex+fl/2+0.15, ey+fd), (ex+fl/2-0.15, ey+fd), (ex, ey+0.15), (ex, ey)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_lwpolyline([(ex+fl/2-0.15, ey+fd), (ex+fl/2-0.15, ey+fd+0.8), (ex+fl/2+0.15, ey+fd+0.8), (ex+fl/2+0.15, ey+fd)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
            msp.add_line((ex+0.05, ey+0.05), (ex+fl-0.05, ey+0.05), dxfattribs={'layer': 'REBAR_MAIN'}) 
            for i in range(12): msp.add_circle((ex+0.05+i*(fl-0.1)/11, ey+0.065), radius=0.01, dxfattribs={'layer': 'REBAR_MAIN'}) 
            # Column Starter Bars
            msp.add_line((ex+fl/2-0.1, ey+fd+0.8), (ex+fl/2-0.1, ey+0.06), dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_line((ex+fl/2-0.1, ey+0.06), (ex+fl/2-0.3, ey+0.06), dxfattribs={'layer': 'REBAR_MAIN'}) 
            msp.add_line((ex+fl/2+0.1, ey+fd+0.8), (ex+fl/2+0.1, ey+0.06), dxfattribs={'layer': 'REBAR_MAIN'})
            msp.add_line((ex+fl/2+0.1, ey+0.06), (ex+fl/2+0.3, ey+0.06), dxfattribs={'layer': 'REBAR_MAIN'}) 
            
            add_dim((ex+fl+0.1, ey), (ex+fl+0.1, ey+fd), 0.4, f"D={int(fd*1000)}", True)
            add_dim((ex, ey-0.1), (ex+fl, ey-0.1), -0.4, f"L={fl}m")
            msp.add_text("FOOTING SECTION", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((ex, ey - 0.8))
            msp.add_text(f"Bot Biaxial Mesh: {f_det['Mesh']}", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.15}).set_placement((ex, ey - 1.1))

        # 2D. Slab Detail (Section)
        cx, cy = det_x + 9.0, max_y - 7.0
        sd = slab_thick / 1000.0
        msp.add_lwpolyline([(cx,cy), (cx+4.0,cy), (cx+4.0,cy+sd), (cx,cy+sd), (cx,cy)], dxfattribs={'layer': 'CONCRETE_OUTLINE'})
        msp.add_line((cx+0.02, cy+0.02), (cx+3.98, cy+0.02), dxfattribs={'layer': 'REBAR_MAIN'}) 
        msp.add_line((cx+0.02, cy+sd-0.02), (cx+1.0, cy+sd-0.02), dxfattribs={'layer': 'REBAR_MAIN'}) 
        msp.add_line((cx+3.0, cy+sd-0.02), (cx+3.98, cy+sd-0.02), dxfattribs={'layer': 'REBAR_MAIN'}) 
        add_dim((cx+4.1, cy), (cx+4.1, cy+sd), 0.3, f"{slab_thick}mm", True)
        msp.add_text(f"SLAB SECTION", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.2}).set_placement((cx, cy - 0.4))
        msp.add_text(f"Bot Mesh: T10 @ {int(spc_pos)} c/c", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.15}).set_placement((cx, cy - 0.6))
        msp.add_text(f"Top Extra: T10 @ {int(spc_neg)} c/c", dxfattribs={'layer': 'ANNOTATIONS', 'height': 0.15}).set_placement((cx, cy - 0.8))

        # Save DXF to buffer
        fd, path = tempfile.mkstemp(suffix=".dxf")
        os.close(fd)
        doc.saveas(path)
        with open(path, "rb") as f: dxf_bytes = f.read()
        os.remove(path)

        # --- UI DISPLAY ---
        st.success("✅ Analysis, PDF Reporting & CAD Drafting Complete!")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1: st.download_button(label="📄 Download Production PDF Report", data=pdf_bytes, file_name="Structural_Detailing_Report.pdf", mime="application/pdf", type="primary", width="stretch")
        with col_dl2: st.download_button(label="📥 Download CAD Plan & Details (.dxf)", data=dxf_bytes, file_name="Floor_Framing_Plans.dxf", mime="application/dxf", type="primary", width="stretch")
            
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Raw Forces", "📐 Main Detailing", "🟦 Slabs & Footings", "🧾 Bar Bending Schedule"])
        
        with tab1:
            st.markdown("### Individual Member Internal Forces")
            st.dataframe(pd.DataFrame(analysis_data), width="stretch")
            
        with tab2:
            st.markdown("### IS 456 Dynamic Shear & Rebar Layout")
            st.dataframe(pd.DataFrame(design_data), width="stretch")
                
        with tab3:
            st.markdown("### IS 456 Restrained Two-Way Slab Check")
            st.write(f"- **Critical Panel:** {round(Lx,2)}m x {round(Ly,2)}m | **Max Hogging Moment:** {round(Mu_neg, 2)} kN.m")
            st.write(f"- **Required Thickness:** {round(max(d_req_flex, d_req_def)+25, 1)} mm | **Provided:** {slab_thick} mm")
            if safe_slab: st.success(f"✅ Slab Safe. \n- **Bot Span Mesh:** T10 @ {int(spc_pos)} c/c\n- **Top Support (Hogging):** T10 @ {int(spc_neg)} c/c\n- **Corner Torsion Mesh:** T10 @ {int(spc_tor)} c/c")
            else: st.error("❌ Slab Fails Deflection or Flexure. Increase Thickness.")
            
            st.divider()
            st.markdown("### Foundation Validation & Isolated Footings")
            if not clashes: st.success("✅ Foundation Validation Passed: No overlapping soil pressure bulbs.")
            else: st.error(f"🚨 {len(clashes)} Clash(es) Detected. Footings physically overlap or interact. Use Combined or Raft Foundation.")
            st.dataframe(pd.DataFrame(footing_results), width="stretch")
                
        with tab4:
            st.markdown("### 🧾 Comprehensive Bar Bending Schedule (BBS)")
            st.dataframe(df_bbs, width="stretch")
            st.metric(label="Total Steel Tonnage Required", value=f"{total_wt_kg / 1000.0:.2f} Metric Tons")
            st.download_button(label="⬇️ Download BBS (CSV)", data=df_bbs.to_csv(index=False), file_name="building_bbs.csv", mime="text/csv", width="stretch")
