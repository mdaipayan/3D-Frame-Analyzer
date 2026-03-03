import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import math

# --- PAGE SETUP ---
st.set_page_config(page_title="Practical 3D Frame Analyzer", layout="wide")
st.title("🏗️ Practical 3D Frame Analysis Engine")
st.caption("Includes: IS 1893 Cracked Modifiers | Local Tributary Loads | Span Moment Recovery")

# --- INITIALIZE STATE ---
if 'grids' not in st.session_state:
    st.session_state.floors = pd.DataFrame({"Floor": [1, 2], "Height (m)": [3.0, 3.0]})
    st.session_state.x_grids = pd.DataFrame({"Grid_ID": ["A", "B", "C"], "X_Coord (m)": [0.0, 4.0, 8.0]})
    st.session_state.y_grids = pd.DataFrame({"Grid_ID": ["1", "2", "3"], "Y_Coord (m)": [0.0, 5.0, 10.0]})
    st.session_state.cols = pd.DataFrame({
        "Col_ID": ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9"],
        "X_Grid": ["A", "B", "C", "A", "B", "C", "A", "B", "C"], 
        "Y_Grid": ["1", "1", "1", "2", "2", "2", "3", "3", "3"]
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
apply_cracked_modifiers = st.sidebar.checkbox("Use IS 1893 Cracked Sections", value=True, help="Reduces Beam Iy/Iz by 0.35, Col Iy/Iz by 0.7, and Torsion (J) by 0.1")

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
    with col1: st.write("Z-Elevations"); floors_df = st.data_editor(st.session_state.floors, num_rows="dynamic", use_container_width=True)
    with col2: st.write("X-Grids"); x_grids_df = st.data_editor(st.session_state.x_grids, num_rows="dynamic", use_container_width=True)
    with col3: st.write("Y-Grids"); y_grids_df = st.data_editor(st.session_state.y_grids, num_rows="dynamic", use_container_width=True)
    with col4: st.write("Columns"); cols_df = st.data_editor(st.session_state.cols, num_rows="dynamic", use_container_width=True)

# Generate Unique Sorted Grid Lines
x_coords_sorted = sorted(list(set([float(r['X_Coord (m)']) for _, r in x_grids_df.iterrows()])))
y_coords_sorted = sorted(list(set([float(r['Y_Coord (m)']) for _, r in y_grids_df.iterrows()])))

def get_tributary_width(coord, grid_list):
    """Calculates realistic tributary width based on adjacent grid spacings."""
    if coord not in grid_list: return 1.0
    idx = grid_list.index(coord)
    w_left = (coord - grid_list[idx-1])/2.0 if idx > 0 else 0.0
    w_right = (grid_list[idx+1] - coord)/2.0 if idx < len(grid_list)-1 else 0.0
    return max(w_left + w_right, 0.1)

# --- ENGINE: BUILD MESH ---
def build_mesh():
    nodes, elements = [], []
    z_elevs = {0: 0.0}
    curr_z = 0.0
    for _, r in floors_df.iterrows():
        curr_z += float(r['Height (m)'])
        z_elevs[int(r['Floor'])] = curr_z
        
    x_map = {str(r['Grid_ID']): float(r['X_Coord (m)']) for _, r in x_grids_df.iterrows()}
    y_map = {str(r['Grid_ID']): float(r['Y_Coord (m)']) for _, r in y_grids_df.iterrows()}
    
    primary_xy = []
    for _, r in cols_df.iterrows():
        xg, yg = str(r.get('X_Grid')), str(r.get('Y_Grid'))
        if xg in x_map and yg in y_map:
            primary_xy.append({'x': x_map[xg], 'y': y_map[yg]})
            
    nid = 0
    for f in range(len(floors_df) + 1):
        for pt in primary_xy:
            nodes.append({'id': nid, 'x': pt['x'], 'y': pt['y'], 'z': z_elevs.get(f, 0.0), 'floor': f})
            nid += 1
            
    eid = 0
    # Columns
    for z in range(len(floors_df)):
        b_nodes = [n for n in nodes if n['floor'] == z]
        t_nodes = [n for n in nodes if n['floor'] == z + 1]
        for bn in b_nodes:
            tn = next((n for n in t_nodes if abs(n['x']-bn['x'])<0.01 and abs(n['y']-bn['y'])<0.01), None)
            if tn:
                elements.append({'id': eid, 'ni': bn['id'], 'nj': tn['id'], 'type': 'Column', 'size': col_size, 'dir': 'Z'})
                eid += 1
    # Beams (Tolerance 0.1m)
    for z in range(1, len(floors_df) + 1):
        f_nodes = [n for n in nodes if n['floor'] == z]
        # X-Beams
        y_grps = {}
        for n in f_nodes:
            matched = False
            for yk in y_grps.keys():
                if abs(n['y'] - yk) < 0.1: y_grps[yk].append(n); matched = True; break
            if not matched: y_grps[n['y']] = [n]
        for yk, grp in y_grps.items():
            grp = sorted(grp, key=lambda k: k['x'])
            for i in range(len(grp)-1):
                elements.append({'id': eid, 'ni': grp[i]['id'], 'nj': grp[i+1]['id'], 'type': 'Beam', 'size': beam_size, 'dir': 'X'})
                eid += 1
        # Y-Beams
        x_grps = {}
        for n in f_nodes:
            matched = False
            for xk in x_grps.keys():
                if abs(n['x'] - xk) < 0.1: x_grps[xk].append(n); matched = True; break
            if not matched: x_grps[n['x']] = [n]
        for xk, grp in x_grps.items():
            grp = sorted(grp, key=lambda k: k['y'])
            for i in range(len(grp)-1):
                elements.append({'id': eid, 'ni': grp[i]['id'], 'nj': grp[i+1]['id'], 'type': 'Beam', 'size': beam_size, 'dir': 'Y'})
                eid += 1
                
    return nodes, elements

nodes, elements = build_mesh()

# --- RENDER 3D MODEL ---
st.subheader("🖥️ 3D Architectural Viewport")
fig = go.Figure()
for el in elements:
    ni = next(n for n in nodes if n['id'] == el['ni'])
    nj = next(n for n in nodes if n['id'] == el['nj'])
    color = '#1f77b4' if el['type'] == 'Column' else '#d62728'
    fig.add_trace(go.Scatter3d(x=[ni['x'], nj['x']], y=[ni['y'], nj['y']], z=[ni['z'], nj['z']], mode='lines', line=dict(color=color, width=5), hoverinfo='text', text=f"ID: {el['id']} ({el['type']})", showlegend=False))
fig.add_trace(go.Scatter3d(x=[n['x'] for n in nodes], y=[n['y'] for n in nodes], z=[n['z'] for n in nodes], mode='markers', marker=dict(size=3, color='black'), hoverinfo='none', showlegend=False))
fig.update_layout(scene=dict(xaxis_title='X', yaxis_title='Y', zaxis_title='Z', aspectmode='data'), margin=dict(l=0, r=0, b=0, t=0), height=450)
st.plotly_chart(fig, use_container_width=True)

# --- ENGINE: MATHS & SOLVER ---
def get_props(size_str, el_type):
    b, h = map(float, size_str.split('x'))
    b, h = b/1000.0, h/1000.0
    A = b * h
    Iy = (h * b**3) / 12.0
    Iz = (b * h**3) / 12.0
    dim_min, dim_max = min(b, h), max(b, h)
    J = (dim_min**3 * dim_max) * (1/3 - 0.21 * (dim_min/dim_max) * (1 - (dim_min**4) / (12 * dim_max**4)))
    
    # REAL WORLD UPGRADE: IS 1893 Property Modifiers for Cracked Concrete
    if apply_cracked_modifiers:
        if el_type == 'Column':
            Iy *= 0.7; Iz *= 0.7
        elif el_type == 'Beam':
            Iy *= 0.35; Iz *= 0.35
        # Drastic reduction in torsional constant for concrete to prevent fake torsional spikes
        J *= 0.1 
        
    return A, Iy, Iz, J

def local_k(A, Iy, Iz, J, L):
    k = np.zeros((12, 12))
    k[0,0]=k[6,6]= E_conc*A/L; k[0,6]=k[6,0]= -E_conc*A/L
    k[3,3]=k[9,9]= G_conc*J/L; k[3,9]=k[9,3]= -G_conc*J/L
    k[2,2]=k[8,8]= 12*E_conc*Iy/L**3; k[4,4]=k[10,10]= 4*E_conc*Iy/L
    k[2,4]=k[2,10]=k[4,2]=k[10,2]= -6*E_conc*Iy/L**2; k[8,4]=k[8,10]=k[4,8]=k[10,8]= 6*E_conc*Iy/L**2
    k[2,8]=k[8,2] = -12*E_conc*Iy/L**3; k[4,10]=k[10,4] = 2*E_conc*Iy/L
    k[1,1]=k[7,7]= 12*E_conc*Iz/L**3; k[5,5]=k[11,11]= 4*E_conc*Iz/L
    k[1,5]=k[1,11]=k[5,1]=k[11,1]= 6*E_conc*Iz/L**2; k[7,5]=k[7,11]=k[5,7]=k[11,7]= -6*E_conc*Iz/L**2
    k[1,7]=k[7,1] = -12*E_conc*Iz/L**3; k[5,11]=k[11,5] = 2*E_conc*Iz/L
    return k + (np.eye(12) * 1e-9) # Mechanism Stabilizer

def transform_matrix(ni, nj):
    dx, dy, dz = nj['x']-ni['x'], nj['y']-ni['y'], nj['z']-ni['z']
    L = math.sqrt(dx**2 + dy**2 + dz**2)
    if L == 0: return np.eye(12)
    cx, cy, cz = dx/L, dy/L, dz/L
    if abs(cx) < 1e-6 and abs(cy) < 1e-6:
        lam = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]]) if cz > 0 else np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]])
    else:
        D = math.sqrt(cx**2 + cy**2)
        lam = np.array([[cx, cy, cz], [-cx*cz/D, -cy*cz/D, D], [-cy/D, cx/D, 0]])
    T = np.zeros((12, 12))
    for i in range(4): T[i*3:(i+1)*3, i*3:(i+1)*3] = lam
    return T

st.divider()

if st.button("🚀 Execute Realistic Matrix Solver", type="primary", use_container_width=True):
    with st.spinner("Calculating Tributary Loads & Assembling Stiffness Matrix..."):
        ndof = len(nodes) * 6
        K_global = np.zeros((ndof, ndof))
        F_global = np.zeros(ndof)
        
        area_load_dl = (slab_thick/1000.0)*25.0 + floor_finish
        total_area_q = (f_dl * area_load_dl) + (f_ll * live_load)
        seismic_W = 0.0
        
        # Matrix Assembly
        for el in elements:
            ni = next(n for n in nodes if n['id'] == el['ni'])
            nj = next(n for n in nodes if n['id'] == el['nj'])
            L = math.sqrt((nj['x']-ni['x'])**2 + (nj['y']-ni['y'])**2 + (nj['z']-ni['z'])**2)
            el['L'] = L
            
            A, Iy, Iz, J = get_props(el['size'], el['type'])
            k_loc = local_k(A, Iy, Iz, J, L)
            T = transform_matrix(ni, nj)
            k_glob = T.T @ k_loc @ T
            
            i_dof, j_dof = ni['id'] * 6, nj['id'] * 6
            idx = [i_dof+i for i in range(6)] + [j_dof+i for i in range(6)]
            
            for r in range(12):
                for c in range(12):
                    K_global[idx[r], idx[c]] += k_glob[r, c]
                    
            # Load Synthesis (Local Tributary Calculation)
            w = 0.0
            if el['type'] == 'Beam':
                # REAL WORLD UPGRADE: Compute exact tributary width based on local gridding
                if el['dir'] == 'X':
                    trib_width = get_tributary_width(ni['y'], y_coords_sorted)
                else:
                    trib_width = get_tributary_width(ni['x'], x_coords_sorted)
                
                w_slab = total_area_q * trib_width
                w_wall = f_dl * (wall_thick/1000.0) * 3.0 * 20.0
                w_self = f_dl * A * 25.0
                w = w_slab + w_wall + w_self
                
                el['applied_w'] = w # Store for moment recovery
                
                seismic_slab = (area_load_dl + 0.25*live_load) * trib_width * L
                seismic_W += seismic_slab + (A*25.0*L) + ((wall_thick/1000.0)*3.0*20.0*L)
                
                # Apply Fixed End Moments
                V, M = (w * L) / 2.0, (w * L**2) / 12.0
                F_loc = np.zeros(12)
                F_loc[1], F_loc[5], F_loc[7], F_loc[11] = V, M, V, -M
                F_g = T.T @ F_loc
                for i in range(12): F_global[idx[i]] -= F_g[i]
                
        # Seismic Base Shear Application
        if f_eq > 0:
            Vb = eq_base_shear * seismic_W * f_eq
            num_stories = len(floors_df)
            floor_wt = seismic_W / num_stories
            sum_wh2 = sum([floor_wt * (floors_df.iloc[z]['Height (m)'] * (z+1))**2 for z in range(num_stories)])
            for n in nodes:
                if n['z'] > 0:
                    node_f = Vb * (floor_wt * (n['z']**2)) / sum_wh2 if sum_wh2 > 0 else 0
                    nodes_on_floor = len([nd for nd in nodes if nd['floor'] == n['floor']])
                    F_global[n['id'] * 6] += (node_f / nodes_on_floor) if nodes_on_floor > 0 else 0

        # Boundary Conditions (Base Fixed)
        fixed = [n['id']*6 + d for n in nodes if n['z'] == 0 for d in range(6)]
        free = sorted(list(set(range(ndof)) - set(fixed)))
        
        K_free = K_global[np.ix_(free, free)]
        F_free = F_global[free]
        
        # Robust Least Squares Solver
        U_free = np.linalg.lstsq(K_free, F_free, rcond=None)[0]
        
        U_glob = np.zeros(ndof)
        U_glob[free] = U_free
        
        # Member Force Extraction
        res_data = []
        for el in elements:
            ni = next(n for n in nodes if n['id'] == el['ni'])
            nj = next(n for n in nodes if n['id'] == el['nj'])
            T = transform_matrix(ni, nj)
            A, Iy, Iz, J = get_props(el['size'], el['type'])
            k_loc = local_k(A, Iy, Iz, J, el['L'])
            
            u_g = np.concatenate((U_glob[ni['id']*6:ni['id']*6+6], U_glob[nj['id']*6:nj['id']*6+6]))
            u_l = T @ u_g
            
            f_int = k_loc @ u_l
            
            axial = max(abs(f_int[0]), abs(f_int[6]))
            shear = max(abs(f_int[1]), abs(f_int[2]), abs(f_int[7]), abs(f_int[8]))
            
            # REAL WORLD UPGRADE: Span Moment Recovery (Parabolic Bending Diagram)
            Mz_end_i = abs(f_int[5])
            Mz_end_j = abs(f_int[11])
            moment_max = max(Mz_end_i, Mz_end_j)
            
            if el['type'] == 'Beam' and 'applied_w' in el:
                w = el['applied_w']
                Vy_i = f_int[1] # Local shear force at node i
                x_max = Vy_i / w if w > 0 else -1
                
                # Check if the maximum sagging moment occurs within the beam length
                if 0 < x_max < el['L']:
                    # M(x) = M_i + V_i*x - (w*x^2)/2
                    M_span = abs(f_int[5] + (Vy_i * x_max) - (0.5 * w * x_max**2))
                    moment_max = max(moment_max, M_span)

            res_data.append({
                "ID": el['id'], "Type": el['type'], "Floor": ni['floor'],
                "Size (mm)": el['size'], "Length (m)": round(el['L'], 2),
                "Axial (kN)": round(axial, 1), "Max Shear (kN)": round(shear, 1), "Max Moment (kN.m)": round(moment_max, 1)
            })

        df_res = pd.DataFrame(res_data)

        st.success("✅ Real-World Structural Analysis Complete!")
        
        # Display Tables
        colA, colB = st.columns(2)
        with colA:
            st.subheader("Column Forces")
            st.dataframe(df_res[df_res['Type'] == 'Column'].reset_index(drop=True), use_container_width=True)
        with colB:
            st.subheader("Beam Forces")
            st.dataframe(df_res[df_res['Type'] == 'Beam'].reset_index(drop=True), use_container_width=True)
            
        st.download_button(label="⬇️ Download Full Results (CSV)", data=df_res.to_csv(index=False), file_name=f"frame_results_{combo}.csv", mime="text/csv", use_container_width=True)
