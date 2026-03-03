import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import math
import copy

# --- PAGE SETUP ---
st.set_page_config(page_title="Practical 3D Frame Analyzer", layout="wide")
st.title("🏗️ Practical 3D Frame Analysis Engine")
st.caption("Yield-Line Theory | Rigid Diaphragm Shear Distribution | Cracked Section Modifiers")

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
    st.session_state.grids = True

# --- SIDEBAR: INPUTS ---
st.sidebar.header("1. Material Properties")
fck = st.sidebar.number_input("Concrete Grade (fck - MPa)", value=25.0, step=5.0)
fy = st.sidebar.number_input("Steel Grade (fy - MPa)", value=500.0, step=85.0)
E_conc = 5000 * math.sqrt(fck) * 1000  # kN/m^2
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

st.sidebar.header("4. Real-World Modifiers")
apply_cracked_modifiers = st.sidebar.checkbox("Use IS 1893 Cracked Sections", value=True)

st.sidebar.header("5. IS Code Combinations")
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
    # Replaced use_container_width with width="stretch"
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

# --- ENGINE: BUILD MESH ---
def build_mesh():
    nodes, elements = [], []
    x_map = {str(r['Grid_ID']).strip(): float(r['X_Coord (m)']) for _, r in x_grids_df.iterrows() if pd.notna(r['Grid_ID'])}
    y_map = {str(r['Grid_ID']).strip(): float(r['Y_Coord (m)']) for _, r in y_grids_df.iterrows() if pd.notna(r['Grid_ID'])}
    
    primary_xy = []
    for _, r in cols_df.iterrows():
        xg, yg = str(r.get('X_Grid', '')).strip(), str(r.get('Y_Grid', '')).strip()
        if xg in x_map and yg in y_map:
            px = x_map[xg] + float(r.get('X_Offset (m)', 0.0))
            py = y_map[yg] + float(r.get('Y_Offset (m)', 0.0))
            ang = float(r.get('Angle (deg)', 0.0))
            primary_xy.append({'x': px, 'y': py, 'angle': ang})
            
    nid = 0
    for f in range(len(floors_df) + 1):
        for pt in primary_xy:
            nodes.append({'id': nid, 'x': pt['x'], 'y': pt['y'], 'z': z_elevs.get(f, 0.0), 'floor': f, 'angle': pt['angle'], 'is_dummy': False})
            nid += 1
            
    eid = 0
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

# --- RENDER 3D MODEL ---
st.subheader("🖥️ 3D Architectural Viewport")
fig = go.Figure()
for el in elements:
    if el['type'] == 'Diaphragm': continue
    ni = next(n for n in nodes if n['id'] == el['ni'])
    nj = next(n for n in nodes if n['id'] == el['nj'])
    color = '#1f77b4' if el['type'] == 'Column' else '#d62728'
    fig.add_trace(go.Scatter3d(x=[ni['x'], nj['x']], y=[ni['y'], nj['y']], z=[ni['z'], nj['z']], mode='lines', line=dict(color=color, width=5), hoverinfo='text', text=f"ID: {el['id']} ({el['type']})", showlegend=False))
fig.add_trace(go.Scatter3d(x=[n['x'] for n in nodes if not n['is_dummy']], y=[n['y'] for n in nodes if not n['is_dummy']], z=[n['z'] for n in nodes if not n['is_dummy']], mode='markers', marker=dict(size=3, color='black'), hoverinfo='none', showlegend=False))
fig.update_layout(scene=dict(xaxis_title='X', yaxis_title='Y', zaxis_title='Z', aspectmode='data'), margin=dict(l=0, r=0, b=0, t=0), height=450)
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
        if Lp <= 0.01: return 0.0
        if Lb >= Lp: return (q * Lp / 6.0) * (3.0 - (Lp / Lb)**2)
        else: return (q * Lb / 3.0)
            
    return get_eq_load(L_beam, L_perp1, q_area) + get_eq_load(L_beam, L_perp2, q_area)

def get_props(size_str, el_type):
    if el_type == 'Diaphragm':
        return 100.0, 1e-6, 1e-6, 1e-6 
        
    b, h = map(float, size_str.split('x'))
    # SAFEGUARD: Prevent ZeroDivision if user enters "0x0"
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
    # SAFEGUARD: Prevent ZeroDivision if elements have 0.0 length
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
    L = math.sqrt(dx**2 + dy**2 + dz**2)
    if L == 0: return np.eye(12)
    cx, cy, cz = dx/L, dy/L, dz/L
    
    if abs(cx) < 1e-6 and abs(cy) < 1e-6:
        lam = np.array([[0, 0, 1*np.sign(cz)], [0, 1, 0], [-1*np.sign(cz), 0, 0]])
    else:
        D = math.sqrt(cx**2 + cy**2)
        lam = np.array([[cx, cy, cz], [-cx*cz/D, -cy*cz/D, D], [-cy/D, cx/D, 0]])
        
    if angle_deg != 0.0:
        rad = math.radians(angle_deg)
        c, s = math.cos(rad), math.sin(rad)
        R = np.array([[1, 0, 0], [0, c, s], [0, -s, c]])
        lam = R @ lam
        
    T = np.zeros((12, 12))
    for i in range(4): T[i*3:(i+1)*3, i*3:(i+1)*3] = lam
    return T

st.divider()

if st.button("🚀 Execute Validation Matrix", type="primary", width="stretch"):
    with st.spinner("Applying Kinematic Constraints & Synthesizing Local Gradients..."):
        ndof = len(nodes) * 6
        K_global = np.zeros((ndof, ndof))
        F_global = np.zeros(ndof)
        
        floor_seismic_W = {z: 0.0 for z in range(1, len(floors_df)+1)}
        area_dl = (slab_thick/1000.0)*25.0 + floor_finish
        total_q_area = (f_dl * area_dl) + (f_ll * live_load)
        
        # EXACT LOCAL MASS LUMPING
        for el in elements:
            if el['type'] == 'Diaphragm': continue
            ni = next(n for n in nodes if n['id'] == el['ni'])
            nj = next(n for n in nodes if n['id'] == el['nj'])
            L = math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2)
            el['L'], el['ni_n'], el['nj_n'] = L, ni, nj
            
            A, Iy, Iz, J = get_props(el['size'], el['type'])
            el['A'], el['Iy'], el['Iz'], el['J'] = A, Iy, Iz, J
            
            if el['type'] == 'Beam':
                w_slab_mass = calc_yield_line_udl(ni, nj, el['dir'], area_dl + 0.25*live_load)
                w_wall_mass = (wall_thick/1000.0) * 3.0 * 20.0
                w_self_mass = A * 25.0
                floor_seismic_W[ni['floor']] += (w_slab_mass + w_wall_mass + w_self_mass) * L
            elif el['type'] == 'Column':
                wt = A * 25.0 * L
                f_bot, f_top = ni['floor'], nj['floor']
                if f_bot > 0: floor_seismic_W[f_bot] += wt / 2.0
                if f_top > 0: floor_seismic_W[f_top] += wt / 2.0

        # MATRIX ASSEMBLY
        for el in elements:
            if 'L' not in el:
                ni, nj = next(n for n in nodes if n['id'] == el['ni']), next(n for n in nodes if n['id'] == el['nj'])
                el['L'] = math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2)
                el['A'], el['Iy'], el['Iz'], el['J'] = get_props(el['size'], el['type'])
                el['ni_n'], el['nj_n'] = ni, nj
                
            k_loc = local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L'])
            T = transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            k_glob = T.T @ k_loc @ T
            
            i_dof, j_dof = el['ni_n']['id'] * 6, el['nj_n']['id'] * 6
            idx = [i_dof+i for i in range(6)] + [j_dof+i for i in range(6)]
            
            for r in range(12):
                for c in range(12):
                    K_global[idx[r], idx[c]] += k_glob[r, c]
                    
            if el['type'] == 'Beam':
                w_slab = calc_yield_line_udl(el['ni_n'], el['nj_n'], el['dir'], total_q_area)
                w_wall = f_dl * (wall_thick/1000.0) * 3.0 * 20.0
                w_self = f_dl * el['A'] * 25.0
                w = w_slab + w_wall + w_self
                el['applied_w'] = w 
                
                V, M = (w * el['L']) / 2.0, (w * el['L']**2) / 12.0
                F_loc = np.zeros(12); F_loc[1]=V; F_loc[5]=M; F_loc[7]=V; F_loc[11]=-M
                F_g = T.T @ F_loc
                for i in range(12): F_global[idx[i]] -= F_g[i]
                
        # MASTER-SLAVE RIGID DIAPHRAGM FORCE INJECTION
        if f_eq > 0:
            total_W = sum(floor_seismic_W.values())
            Vb = eq_base_shear * total_W * f_eq
            sum_wh2 = sum([floor_seismic_W[z] * (z_elevs[z]**2) for z in floor_seismic_W])
            
            for z in range(1, len(floors_df)+1):
                if sum_wh2 > 0 and z in diaphragm_nodes:
                    floor_f = Vb * (floor_seismic_W[z] * (z_elevs[z]**2)) / sum_wh2
                    d_id = diaphragm_nodes[z]['id']
                    F_global[d_id * 6] += floor_f  

        # LEAST SQUARES SOLVER
        fixed = [n['id']*6 + d for n in nodes if n['z'] == 0 for d in range(6)]
        free = sorted(list(set(range(ndof)) - set(fixed)))
        
        K_free = K_global[np.ix_(free, free)]
        F_free = F_global[free]
        U_free = np.linalg.lstsq(K_free, F_free, rcond=None)[0]
        
        U_glob = np.zeros(ndof)
        U_glob[free] = U_free
        
        # EXTRACT FORCES & MOMENT RECOVERY
        res_data = []
        for el in elements:
            if el['type'] == 'Diaphragm': continue
            
            T = transform_matrix(el['ni_n'], el['nj_n'], el['angle'])
            k_loc = local_k(el['A'], el['Iy'], el['Iz'], el['J'], el['L'])
            
            i_dof, j_dof = el['ni_n']['id'] * 6, el['nj_n']['id'] * 6
            u_g = np.concatenate((U_glob[i_dof:i_dof+6], U_glob[j_dof:j_dof+6]))
            u_l = T @ u_g
            f_int = k_loc @ u_l
            
            axial = max(abs(f_int[0]), abs(f_int[6]))
            shear = max(abs(f_int[1]), abs(f_int[2]), abs(f_int[7]), abs(f_int[8]))
            
            Mz_i, Mz_j = f_int[5], f_int[11]
            moment_max = max(abs(Mz_i), abs(Mz_j))
            
            if el['type'] == 'Beam' and 'applied_w' in el:
                w = el['applied_w']
                Vy_i = f_int[1] 
                x_max = Vy_i / w if w > 0 else -1
                
                if 0 < x_max < el['L']:
                    M_span = Mz_i + (Vy_i * x_max) - (0.5 * w * x_max**2)
                    moment_max = max(moment_max, abs(M_span))

            res_data.append({
                "ID": el['id'], "Type": el['type'], "Floor": el['ni_n']['floor'],
                "Size (mm)": el['size'], "Length (m)": round(el['L'], 2),
                "Axial (kN)": round(axial, 1), "Max Shear (kN)": round(shear, 1), "Max Moment (kN.m)": round(moment_max, 1)
            })

        df_res = pd.DataFrame(res_data)

        st.success("✅ Real-World Structural Analysis Complete!")
        
        colA, colB = st.columns(2)
        with colA:
            st.subheader("Column Forces")
            st.dataframe(df_res[df_res['Type'] == 'Column'].reset_index(drop=True), width="stretch")
        with colB:
            st.subheader("Beam Forces")
            st.dataframe(df_res[df_res['Type'] == 'Beam'].reset_index(drop=True), width="stretch")
            
        st.download_button(label="⬇️ Download Full Results (CSV)", data=df_res.to_csv(index=False), file_name=f"frame_results_{combo}.csv", mime="text/csv", width="stretch")
