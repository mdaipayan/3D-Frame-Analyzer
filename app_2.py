import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import math
import copy

# --- PAGE SETUP ---
st.set_page_config(page_title="Practical 3D Frame Analyzer & Designer", layout="wide")
st.title("🏗️ 3D Frame Analysis & Complete Building Design")
st.caption("Includes: Automated Plan Validation | Combined Footing Auto-Sizing | Mixed Rebar Detailing")

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
st.sidebar.download_button(
    label=f"⬇️ Download {csv_choice} (CSV)",
    data=csv_data,
    file_name=f"{active_key}_template.csv",
    mime="text/csv",
    width="stretch"
)

uploaded_csv = st.sidebar.file_uploader(f"⬆️ Upload {csv_choice} (CSV)", type=["csv"])
if uploaded_csv is not None:
    if st.session_state.last_uploaded.get(csv_choice) != uploaded_csv.name:
        try:
            new_df = pd.read_csv(uploaded_csv)
            st.session_state[active_key] = new_df
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
combo = st.sidebar.selectbox("Select Load Combination", [
    "1.5 DL + 1.5 LL", 
    "1.2 DL + 1.2 LL + 1.2 EQ", 
    "1.5 DL + 1.5 EQ",
    "0.9 DL + 1.5 EQ"
])

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

# --- REAL-WORLD MIXED REBAR DETAILING ENGINE ---
def get_rebar_detail(ast_req, member_type="Beam"):
    if ast_req <= 0: return "Nominal"
    areas = {10: 78.5, 12: 113.1, 16: 201.0, 20: 314.1, 25: 490.8, 32: 804.2}
    dias = [10, 12, 16, 20, 25, 32]
    configs = []

    if member_type == "Beam":
        for d in [12, 16, 20, 25, 32]:
            for n in [2, 3, 4, 5, 6]: configs.append((n, d, 0, 0, n*areas[d]))
        for i in range(1, len(dias)):
            d_main, d_sec = dias[i], dias[i-1] 
            for n_main in [2, 3, 4]:
                for n_sec in [1, 2, 3]:
                    if n_main + n_sec <= 6:
                        configs.append((n_main, d_main, n_sec, d_sec, n_main*areas[d_main] + n_sec*areas[d_sec]))
    else: 
        for d in [12, 16, 20, 25, 32]:
            for n in [4, 6, 8, 10, 12, 16]: configs.append((n, d, 0, 0, n*areas[d]))
        for i in range(1, len(dias)):
            d_corner, d_face = dias[i], dias[i-1]
            for n_face in [2, 4, 6, 8]:
                configs.append((4, d_corner, n_face, d_face, 4*areas[d_corner] + n_face*areas[d_face]))

    configs.sort(key=lambda x: x[4])

    for c in configs:
        if c[4] >= ast_req:
            if c[2] == 0: return f"{c[0]}-T{c[1]} (Prv: {int(c[4])})"
            else: return f"{c[0]}-T{c[1]} + {c[2]}-T{c[3]} (Prv: {int(c[4])})"
    return "Custom (High Ast)"

# --- IS 456 DESIGN FUNCTIONS ---
def design_beam_is456(b_m, h_m, Mu_kNm, Vu_kN, fck, fy):
    b, h = max(b_m * 1000, 1.0), max(h_m * 1000, 1.0)
    d = max(h - 40, 1.0) 
    Mu = Mu_kNm * 1e6 
    coeff = 0.133 if fy >= 500 else 0.138
    Mu_lim = coeff * fck * b * d**2
    
    status = "Singly Reinf."
    if Mu == 0: Ast_req = 0.0
    elif Mu <= Mu_lim:
        sqrt_term = max(1 - (4.6 * Mu) / max(fck * b * d**2, 1.0), 0)
        Ast_req = (0.5 * fck / fy) * (1 - math.sqrt(sqrt_term)) * b * d
    else:
        Ast1 = (0.5 * fck / fy) * (1 - math.sqrt(max(1 - (4.6 * Mu_lim) / max(fck * b * d**2, 1.0), 0))) * b * d
        Ast2 = (Mu - Mu_lim) / (0.87 * fy * d) 
        Ast_req = Ast1 + Ast2
        status = "Doubly Reinf."
        
    Ast_req = max(Ast_req, 0.85 * b * d / max(fy, 1.0))
    tau_v = (Vu_kN * 1000) / (b * d)
    if tau_v > 0.62 * math.sqrt(fck): status += " | Shear Fail"
    return round(Ast_req, 1), status

def design_column_is456(b_m, h_m, Pu_kN, Mu_kNm, fck, fy):
    b, h = max(b_m * 1000, 1.0), max(h_m * 1000, 1.0)
    Ag, d = b * h, max(h - 40, 1.0)
    Pu, Mu = Pu_kN * 1000, Mu_kNm * 1e6 
    
    Asc_axial = (Pu - 0.4 * fck * Ag) / max(0.67 * fy - 0.4 * fck, 1.0) if Pu > 0.4 * fck * Ag else 0
    Asc_bend = Mu / (0.87 * fy * d)
    Asc_req = max(Asc_axial + Asc_bend, 0.008 * Ag)
    
    status = "Safe"
    if Asc_req > 0.040 * Ag: status = "Over-Reinf (>4%)"
    if Pu > (0.45 * fck * Ag + 0.75 * fy * (0.04 * Ag)): status = "Crushing Fail"
    return round(Asc_req, 1), status

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
            xc = sum(n['x'] for n in f_nodes) / len(f_nodes)
            yc = sum(n['y'] for n in f_nodes) / len(f_nodes)
            dummy_node = {'id': nid, 'x': xc, 'y': yc, 'z': z_elevs.get(z, 0.0), 'floor': z, 'angle': 0.0, 'is_dummy': True}
            nodes.append(dummy_node)
            diaphragm_nodes[z] = dummy_node
            nid += 1
            for fn in f_nodes:
                elements.append({'id': eid, 'ni': dummy_node['id'], 'nj': fn['id'], 'type': 'Diaphragm', 'size': '0x0', 'dir': 'D', 'angle': 0.0})
                eid += 1
                
    return nodes, elements, diaphragm_nodes

nodes, elements, diaphragm_nodes = build_mesh()

# --- RENDER 3D MODEL WITH ANNOTATIONS ---
st.subheader("🖥️ 3D Architectural Viewport")
fig = go.Figure()
for el in elements:
    if el['type'] == 'Diaphragm': continue
    ni = next(n for n in nodes if n['id'] == el['ni'])
    nj = next(n for n in nodes if n['id'] == el['nj'])
    color = '#1f77b4' if el['type'] == 'Column' else '#d62728'
    fig.add_trace(go.Scatter3d(x=[ni['x'], nj['x']], y=[ni['y'], nj['y']], z=[ni['z'], nj['z']], mode='lines', line=dict(color=color, width=4), hoverinfo='text', text=f"ID: {el['id']}", showlegend=False))
    
    if show_members:
        fig.add_trace(go.Scatter3d(x=[(ni['x']+nj['x'])/2], y=[(ni['y']+nj['y'])/2], z=[(ni['z']+nj['z'])/2], mode='text', text=[f"M{el['id']}"], textfont=dict(color='green', size=10), showlegend=False, hoverinfo='none'))

phy_nodes = [n for n in nodes if not n['is_dummy']]
fig.add_trace(go.Scatter3d(x=[n['x'] for n in phy_nodes], y=[n['y'] for n in phy_nodes], z=[n['z'] for n in phy_nodes], mode='markers', marker=dict(size=3, color='black'), hoverinfo='none', showlegend=False))

if show_nodes:
    fig.add_trace(go.Scatter3d(x=[n['x'] for n in phy_nodes], y=[n['y'] for n in phy_nodes], z=[n['z'] for n in phy_nodes], mode='text', text=[f"N{n['id']}" for n in phy_nodes], textfont=dict(color='purple', size=10), textposition="top center", showlegend=False, hoverinfo='none'))

fig.update_layout(scene=dict(xaxis_title='X', yaxis_title='Y', zaxis_title='Z', aspectmode='data'), margin=dict(l=0, r=0, b=0, t=0), height=500)
st.plotly_chart(fig, width="stretch")

# --- ENGINE: MATHS & SOLVER ---
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
    else:
        D = math.sqrt(cx**2 + cy**2)
        lam = np.array([[cx, cy, cz], [-cx*cz/D, -cy*cz/D, D], [-cy/D, cx/D, 0]])
    if angle_deg != 0.0:
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)
        lam = np.array([[1, 0, 0], [0, c, s], [0, -s, c]]) @ lam
    T = np.zeros((12, 12))
    for i in range(4): T[i*3:(i+1)*3, i*3:(i+1)*3] = lam
    return T

st.divider()

if st.button("🚀 Execute Analysis & Grouped Design", type="primary", width="stretch"):
    with st.spinner("Processing Matrix, Extracting Forces & Detailing Members..."):
        ndof = len(nodes) * 6
        K_global = np.zeros((ndof, ndof))
        F_global = np.zeros(ndof)
        
        floor_seismic_W = {z: 0.0 for z in range(1, len(floors_df)+1)}
        area_dl = (slab_thick/1000.0)*25.0 + floor_finish
        total_q_area = (f_dl * area_dl) + (f_ll * live_load)
        
        # Assemble Loads & Matrices
        for el in elements:
            if el['type'] == 'Diaphragm': continue
            ni = next(n for n in nodes if n['id'] == el['ni'])
            nj = next(n for n in nodes if n['id'] == el['nj'])
            L = max(math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2), 0.001)
            el['L'], el['ni_n'], el['nj_n'] = L, ni, nj
            
            A, Iy, Iz, J = get_props(el['size'], el['type'])
            el['A'], el['Iy'], el['Iz'], el['J'] = A, Iy, Iz, J
            
            if el['type'] == 'Beam':
                w_slab_mass = calc_yield_line_udl(ni, nj, el['dir'], area_dl + 0.25*live_load)
                floor_seismic_W[ni['floor']] += (w_slab_mass + (wall_thick/1000.0 * 3.0 * 20.0) + (A * 25.0)) * L
            elif el['type'] == 'Column':
                wt = A * 25.0 * L
                if ni['floor'] > 0: floor_seismic_W[ni['floor']] += wt / 2.0
                if nj['floor'] > 0: floor_seismic_W[nj['floor']] += wt / 2.0

        for el in elements:
            if 'L' not in el:
                ni, nj = next(n for n in nodes if n['id'] == el['ni']), next(n for n in nodes if n['id'] == el['nj'])
                el['L'] = max(math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2), 0.001)
                el['A'], el['Iy'], el['Iz'], el['J'] = get_props(el['size'], el['type'])
                el['ni_n'], el['nj_n'] = ni, nj
                
            k_loc = local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L'])
            T = transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            k_glob = T.T @ k_loc @ T
            
            i_dof, j_dof = el['ni_n']['id'] * 6, el['nj_n']['id'] * 6
            idx = [i_dof+i for i in range(6)] + [j_dof+i for i in range(6)]
            
            for r in range(12):
                for c in range(12): K_global[idx[r], idx[c]] += k_glob[r, c]
                    
            if el['type'] == 'Beam':
                w = calc_yield_line_udl(el['ni_n'], el['nj_n'], el['dir'], total_q_area) + (f_dl * wall_thick/1000.0 * 3.0 * 20.0) + (f_dl * el['A'] * 25.0)
                el['applied_w'] = w 
                V, M = (w * el['L']) / 2.0, (w * el['L']**2) / 12.0
                F_loc = np.zeros(12); F_loc[1], F_loc[5], F_loc[7], F_loc[11] = V, M, V, -M
                F_g = T.T @ F_loc
                for i in range(12): F_global[idx[i]] -= F_g[i]
                
        if f_eq > 0:
            Vb = eq_base_shear * sum(floor_seismic_W.values()) * f_eq
            sum_wh2 = sum([floor_seismic_W[z] * (z_elevs[z]**2) for z in floor_seismic_W])
            for z in range(1, len(floors_df)+1):
                if sum_wh2 > 0 and z in diaphragm_nodes:
                    F_global[diaphragm_nodes[z]['id'] * 6] += Vb * (floor_seismic_W[z] * (z_elevs[z]**2)) / sum_wh2

        # Solve
        fixed = [n['id']*6 + d for n in nodes if n['z'] == 0 for d in range(6)]
        free = sorted(list(set(range(ndof)) - set(fixed)))
        
        U_glob = np.zeros(ndof)
        if len(free) > 0:
            U_glob[free] = np.linalg.lstsq(K_global[np.ix_(free, free)], F_global[free], rcond=None)[0]
        
        # Post-Processing
        analysis_data, design_data = [], []
        base_reactions = {}

        for el in elements:
            if el['type'] == 'Diaphragm': continue
            
            T = transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            k_loc = local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L'])
            i_dof, j_dof = el['ni_n']['id'] * 6, el['nj_n']['id'] * 6
            f_int = k_loc @ (T @ np.concatenate((U_glob[i_dof:i_dof+6], U_glob[j_dof:j_dof+6])))
            
            axial = max(abs(f_int[0]), abs(f_int[6]))
            shear = max(abs(f_int[1]), abs(f_int[2]), abs(f_int[7]), abs(f_int[8]))
            moment_max = max(abs(f_int[5]), abs(f_int[11]))
            
            if el['type'] == 'Beam' and 'applied_w' in el:
                w, Vy_i = el['applied_w'], f_int[1] 
                x_max = Vy_i / max(w, 0.001) if w > 0 else -1
                if 0 < x_max < el['L']:
                    moment_max = max(moment_max, abs(f_int[5] + (Vy_i * x_max) - (0.5 * w * x_max**2)))

            if el['type'] == 'Column' and el['ni_n']['z'] == 0:
                base_reactions[el['ni_n']['id']] = {
                    'Pu': abs(f_int[0]), 'Col_Size': el['size'],
                    'x': el['ni_n']['x'], 'y': el['ni_n']['y']
                }

            analysis_data.append({
                "Member ID": f"M{el['id']}", "Type": el['type'], "Floor": el['ni_n']['floor'],
                "Length (m)": round(el['L'], 2), "Axial (kN)": round(axial, 1), 
                "Shear (kN)": round(shear, 1), "Moment (kN.m)": round(moment_max, 1)
            })

            b_m, h_m = map(lambda x: float(x)/1000.0, el['size'].split('x'))
            if el['type'] == 'Beam':
                req_ast, stat = design_beam_is456(b_m, h_m, moment_max, shear, fck, fy)
            else:
                req_ast, stat = design_column_is456(b_m, h_m, axial, moment_max, fck, fy)
                
            design_data.append({
                "Member ID": f"M{el['id']}", "Type": el['type'], "Floor": el['ni_n']['floor'],
                "Size (mm)": el['size'], "Max Pu (kN)": round(axial, 1), "Max Mu (kN.m)": round(moment_max, 1),
                "Req Ast (mm²)": req_ast, "Status": stat
            })

        df_analysis = pd.DataFrame(analysis_data)
        df_design = pd.DataFrame(design_data)
        
        # --- TABULAR PRESENTATION ---
        tab1, tab2, tab3 = st.tabs(["📊 Raw Internal Forces", "📐 Detailed Reinforcement", "🟦 Slabs & Footings"])
        
        with tab1:
            st.markdown("### Individual Member Internal Forces")
            st.dataframe(df_analysis, width="stretch")
            
        with tab2:
            st.markdown("### IS 456 Grouped Design & Rebar Combinations")
            st.info("Members are grouped to find the critical force envelope. The algorithm selects practical, site-ready rebar combinations.")
            
            df_col = df_design[df_design['Type'] == 'Column']
            if not df_col.empty:
                col_group = df_col.groupby(['Floor', 'Size (mm)']).agg(
                    Max_Pu=('Max Pu (kN)', 'max'), Max_Mu=('Max Mu (kN.m)', 'max'), Max_Ast=('Req Ast (mm²)', 'max')
                ).reset_index()
                col_group['Detailing (Mixed Rebar)'] = col_group['Max_Ast'].apply(lambda ast: get_rebar_detail(ast, "Column"))
                st.subheader("Column Groups")
                st.dataframe(col_group, width="stretch")
                
            df_beam = df_design[df_design['Type'] == 'Beam']
            if not df_beam.empty:
                beam_group = df_beam.groupby(['Floor', 'Size (mm)']).agg(
                    Max_Mu=('Max Mu (kN.m)', 'max'), Max_Ast=('Req Ast (mm²)', 'max')
                ).reset_index()
                beam_group['Detailing (Mixed Rebar)'] = beam_group['Max_Ast'].apply(lambda ast: get_rebar_detail(ast, "Beam"))
                st.subheader("Beam Groups")
                st.dataframe(beam_group, width="stretch")
                
        with tab3:
            st.markdown("### IS 456 Two-Way Slab Spacing Check")
            x_spans = [x_coords_sorted[i+1] - x_coords_sorted[i] for i in range(len(x_coords_sorted)-1) if (x_coords_sorted[i+1] - x_coords_sorted[i]) > 0.1]
            y_spans = [y_coords_sorted[i+1] - y_coords_sorted[i] for i in range(len(y_coords_sorted)-1) if (y_coords_sorted[i+1] - y_coords_sorted[i]) > 0.1]
            Lx = max(min(x_spans) if x_spans else 1.0, 0.001)
            Ly = max(max(y_spans) if y_spans else 1.0, 0.001)
            
            ratio = Ly / Lx
            alpha_x = np.interp(ratio, [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0], [0.062, 0.074, 0.084, 0.093, 0.099, 0.104, 0.113, 0.118]) if ratio <= 2.0 else 0.125
            
            w_u_slab = 1.5 * (live_load + floor_finish + (slab_thick/1000.0)*25.0)
            Mu_slab = alpha_x * w_u_slab * (Lx**2)
            d_eff_slab = max(slab_thick - 25, 1.0)
            
            sqrt_term = max(1 - (4.6 * Mu_slab * 1e6) / (max(fck, 1.0) * 1000 * d_eff_slab**2), 0)
            Ast_req_slab = (0.5 * fck / max(fy, 1.0)) * (1 - math.sqrt(sqrt_term)) * 1000 * d_eff_slab
            Ast_min_slab = 0.0012 * 1000 * slab_thick
            Ast_slab = max(Ast_req_slab, Ast_min_slab)
            
            slab_spacing = min(math.floor(1000 / (Ast_slab / 78.5) / 10)*10, 300) 
            d_req_flex = math.sqrt((Mu_slab * 1e6) / ((0.133 if fy>=500 else 0.138) * max(fck, 1.0) * 1000))
            d_req_def = (Lx * 1000) / 28.0 
            
            safe_slab = slab_thick >= max(d_req_flex, d_req_def) + 25
            
            st.write(f"- **Critical Panel:** {round(Lx,2)}m x {round(Ly,2)}m ({'Two-Way' if ratio <= 2.0 else 'One-Way'})")
            st.write(f"- **Max Factored Moment:** {round(Mu_slab, 2)} kN.m")
            st.write(f"- **Required Thickness:** {round(max(d_req_flex, d_req_def)+25, 1)} mm | **Provided:** {slab_thick} mm")
            
            if safe_slab: 
                st.success(f"✅ Slab is Safe. **Main Reinforcement (Bottom): T10 @ {int(slab_spacing)} mm c/c**")
            else: 
                st.error("❌ Slab Thickness Fails Deflection/Flexure limits. Increase Thickness.")
            
            st.divider()

            # --- ISOLATED FOOTING SIZING & GEOMETRIC COLLECTION ---
            footing_geoms = {}
            footing_results = []
            
            for nid, data in base_reactions.items():
                P_service = data['Pu'] / 1.5
                Area_req = (P_service * 1.1) / max(sbc, 1.0)
                Side_L = max(math.ceil(math.sqrt(Area_req) * 10) / 10.0, 1.0)
                
                footing_geoms[nid] = {
                    'x': data['x'], 'y': data['y'], 
                    'L': Side_L, 'Pu': data['Pu']
                }
                
                net_upward_p = data['Pu'] / (Side_L**2)
                col_c = max(map(float, data['Col_Size'].split('x'))) / 1000.0
                proj_x = max((Side_L - col_c) / 2.0, 0.01)
                Mu_footing = net_upward_p * Side_L * (proj_x**2) / 2.0
                
                d_req_footing = math.sqrt((Mu_footing * 1e6) / ((0.133 if fy>=500 else 0.138) * max(fck, 1.0) * (Side_L*1000)))
                D_prov = max(300, math.ceil((d_req_footing + 50) / 50.0) * 50)
                d_eff_footing = max(D_prov - 50, 1.0)
                
                sqrt_ftg = max(1 - (4.6 * Mu_footing * 1e6) / (max(fck, 1.0) * (Side_L*1000) * d_eff_footing**2), 0)
                Ast_req_ftg = (0.5 * fck / max(fy, 1.0)) * (1 - math.sqrt(sqrt_ftg)) * (Side_L*1000) * d_eff_footing
                Ast_min_ftg = 0.0012 * (Side_L*1000) * D_prov
                Ast_ftg = max(Ast_req_ftg, Ast_min_ftg)
                
                ast_per_meter = Ast_ftg / Side_L
                ftg_spacing = min(math.floor(1000 / (ast_per_meter / 113.1) / 10)*10, 300)
                
                footing_results.append({
                    "Node ID": f"N{nid}",
                    "Factored Pu (kN)": round(data['Pu'], 1),
                    "Footing Size (m)": f"{Side_L} x {Side_L}",
                    "Pad Depth (mm)": int(D_prov),
                    "Base Mesh (B/W)": f"T12 @ {int(ftg_spacing)} c/c"
                })

            st.markdown("### 1. Isolated Pad Footings (Based on SBC)")
            st.info(f"Using Unfactored Service Loads (Pu / 1.5) + 10% Self-Weight against SBC = {sbc} kN/m²")
            st.dataframe(pd.DataFrame(footing_results), width="stretch")

            st.divider()

            # --- NEW: CLASH DETECTION & COMBINED FOOTING ENGINE ---
            st.markdown("### 2. 🕵️ Foundation Plan Validation (Clash Detection)")
            clashes = []
            processed_nodes = set()
            combined_footing_results = []
            
            node_ids = list(footing_geoms.keys())
            for i in range(len(node_ids)):
                for j in range(i+1, len(node_ids)):
                    n1, n2 = node_ids[i], node_ids[j]
                    if n1 in processed_nodes or n2 in processed_nodes:
                        continue
                    
                    f1, f2 = footing_geoms[n1], footing_geoms[n2]
                    dist = math.hypot(f1['x'] - f2['x'], f1['y'] - f2['y'])
                    req_dist = (f1['L'] / 2.0) + (f2['L'] / 2.0)
                    
                    if dist < req_dist:
                        clashes.append((n1, n2))
                        processed_nodes.add(n1)
                        processed_nodes.add(n2)
                        
                        # CG Balancing & Rectangular Combine Logic
                        P1, P2 = f1['Pu'], f2['Pu']
                        P_tot_service = (P1 + P2) / 1.5
                        Area_req = (P_tot_service * 1.1) / max(sbc, 1.0)
                        
                        CG_dist = (P2 * dist) / max(P1 + P2, 1.0)
                        
                        # Minimum edge clearance assumed 0.5m past column centerline
                        half_L = max(CG_dist, dist - CG_dist) + 0.5
                        L_comb = max(math.ceil((2 * half_L) * 10) / 10.0, 1.0)
                        B_comb = max(math.ceil((Area_req / L_comb) * 10) / 10.0, 1.0)
                        
                        status = "Combined Rectangular"
                        if B_comb > L_comb * 1.5:
                            status = "Requires Raft/Mat"
                            
                        combined_footing_results.append({
                            "Clashing Nodes": f"N{n1} & N{n2}",
                            "Dist Between Cols (m)": round(dist, 2),
                            "Total Service Load (kN)": round(P_tot_service, 1),
                            "Combined L x B (m)": f"{L_comb} x {B_comb}",
                            "Design Recommendation": status
                        })

            if not clashes:
                st.success("✅ Foundation Validation Passed: All isolated footings have adequate spatial clearance. No overlapping soil pressure bulbs detected.")
            else:
                st.error(f"🚨 Validation Failed: {len(clashes)} footing clash(es) detected. Overlapping soil pressure zones will cause severe differential settlement. Implementing Combined Footing solutions.")
                st.dataframe(pd.DataFrame(combined_footing_results), width="stretch")
