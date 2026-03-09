import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import math
import os
import tempfile
from fpdf import FPDF
import ezdxf
# FIX #5: import sparse matrix utilities
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

# ─────────────────────────────────────────────────────────────
#  IS 456 / SP-34 NAMED CONSTANTS  (no more magic numbers)
# ─────────────────────────────────────────────────────────────
REBAR_AREAS   = {10: 78.5, 12: 113.1, 16: 201.0, 20: 314.1, 25: 490.8, 32: 804.2}  # mm²
REBAR_DIAS    = sorted(REBAR_AREAS)
STEEL_DENSITY = 7850.0   # kg/m³
CONC_DENSITY  = 25.0     # kN/m³
COVER_BEAM    = 25       # mm (IS 456 Cl 26.4)
COVER_COL     = 40       # mm
COVER_FOOTING = 50       # mm
STIRRUP_DIA   = 8        # mm
MAX_STEEL_PCT = 0.04     # 4 % gross area (IS 456 Cl 26.5)
MU_LIM_FE415  = 0.138    # limiting moment coefficient (IS 456 Annex G)
MU_LIM_FE500  = 0.133
POISSON       = 0.2
# FIX #8 (code quality): named constant for rebar unit-weight formula
# Derivation: (π/4) × dia² × 7850 kg/m³ × 1e-6 (mm²→m²) × 1e-3 (m→kg/m)  ≈ dia²/162.2
REBAR_WT_FACTOR = 162.2

# ─────────────────────────────────────────────────────────────
#  PAGE SETUP
# ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="3D Frame Analyzer & Designer", layout="wide")
st.title("🏗️ 3D Frame Analysis & Complete Building Design")
st.caption("IS 456 / IS 1893 | Hard-Stop Validation | DXF Detailing | BOQ | 🤖 AI Auto-Design")

# ─────────────────────────────────────────────────────────────
#  SESSION STATE INIT
# ─────────────────────────────────────────────────────────────
if "grids" not in st.session_state:
    st.session_state.floors  = pd.DataFrame({"Floor": [1, 2], "Height (m)": [3.0, 3.0]})
    st.session_state.x_grids = pd.DataFrame({"Grid_ID": ["A","B","C"], "X_Coord (m)": [0.0,4.0,8.0]})
    st.session_state.y_grids = pd.DataFrame({"Grid_ID": ["1","2","3"], "Y_Coord (m)": [0.0,5.0,10.0]})
    st.session_state.cols = pd.DataFrame({
        "Col_ID":       ["C1","C2","C3","C4","C5","C6","C7","C8","C9"],
        "X_Grid":       ["A","B","C","A","B","C","A","B","C"],
        "Y_Grid":       ["1","1","1","2","2","2","3","3","3"],
        "X_Offset (m)": [0.0]*9, "Y_Offset (m)": [0.0]*9, "Angle (deg)": [0.0]*9,
    })
    st.session_state.last_uploaded = {}
    st.session_state.grids = True

# ─────────────────────────────────────────────────────────────
#  SIDEBAR – CSV IMPORT / EXPORT
# ─────────────────────────────────────────────────────────────
st.sidebar.header("📂 CSV Import / Export")
csv_choice = st.sidebar.selectbox("Select Table:", ["Floors","X-Grids","Y-Grids","Columns"])
mapping    = {"Floors":"floors","X-Grids":"x_grids","Y-Grids":"y_grids","Columns":"cols"}
active_key = mapping[csv_choice]

st.sidebar.download_button(
    f"⬇️ Download {csv_choice} (CSV)",
    data=st.session_state[active_key].to_csv(index=False).encode(),
    file_name=f"{active_key}_template.csv", mime="text/csv", use_container_width=True)

uploaded_csv = st.sidebar.file_uploader(f"⬆️ Upload {csv_choice} (CSV)", type=["csv"])
if uploaded_csv and st.session_state.last_uploaded.get(csv_choice) != uploaded_csv.name:
    try:
        st.session_state[active_key] = pd.read_csv(uploaded_csv)
        st.session_state.last_uploaded[csv_choice] = uploaded_csv.name
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"CSV read error: {e}")

st.sidebar.divider()

# ─────────────────────────────────────────────────────────────
#  SIDEBAR – INPUTS
# ─────────────────────────────────────────────────────────────
st.sidebar.header("1. Material Properties")
fck = st.sidebar.number_input("Concrete Grade fck (MPa)", value=25.0, step=5.0, min_value=15.0)
fy  = st.sidebar.number_input("Steel Grade fy (MPa)",     value=500.0, step=85.0, min_value=250.0)
E_conc = 5000.0 * math.sqrt(fck) * 1000.0   # N/m²  (IS 456 Cl 6.2.3.1)
G_conc = E_conc / (2.0 * (1.0 + POISSON))
MU_LIM = MU_LIM_FE500 if fy >= 500 else MU_LIM_FE415

st.sidebar.header("2. Base Section Sizes (mm)")
st.sidebar.caption("Fallback if AI Optimizer is disabled.")
col_size  = st.sidebar.text_input("Column (b×h)", "300x450")
beam_size = st.sidebar.text_input("Beam (b×h)",   "230x400")

st.sidebar.header("3. Applied Loads (IS 875)")
live_load    = st.sidebar.number_input("Live Load (kN/m²)",        value=3.0)
floor_finish = st.sidebar.number_input("Floor Finish (kN/m²)",     value=1.5)
slab_thick   = st.sidebar.number_input("Slab Thickness (mm)",      value=150, min_value=100)
wall_thick   = st.sidebar.number_input("Wall Thickness (mm)",      value=230)
eq_base_shear= st.sidebar.slider("Seismic Ah (%)", 0.0, 20.0, 2.5) / 100.0

st.sidebar.header("4. Soil & Footing")
sbc = st.sidebar.number_input("Safe Bearing Capacity (kN/m²)", value=150.0, step=10.0)

st.sidebar.header("5. Engine Settings")
apply_cracked = st.sidebar.checkbox("IS 1893 Cracked Sections",  value=True)
show_nodes    = st.sidebar.checkbox("Show Node Numbers in 3D",    value=False)
show_members  = st.sidebar.checkbox("Show Member IDs in 3D",      value=False)

st.sidebar.divider()
st.sidebar.markdown("### 🧠 Advanced Optimization")
ai_auto_design = st.sidebar.toggle("🤖 AI Auto-Design (Cost Optimizer)", value=False,
    help="Iteratively sizes members to find cheapest IS 456-compliant section.")

st.sidebar.header("6. Load Combinations")
combo = st.sidebar.selectbox("Combination",
    ["1.5 DL + 1.5 LL","1.2 DL + 1.2 LL + 1.2 EQ","1.5 DL + 1.5 EQ","0.9 DL + 1.5 EQ"])
f_dl, f_ll, f_eq = 1.5, 1.5, 0.0
if "1.2"   in combo: f_dl, f_ll, f_eq = 1.2, 1.2, 1.2
elif "0.9" in combo: f_dl, f_ll, f_eq = 0.9, 0.0, 1.5
elif "1.5 EQ" in combo: f_dl, f_ll, f_eq = 1.5, 0.0, 1.5

st.sidebar.header("7. BOQ Rates (₹)")
with st.sidebar.expander("Modify Rates", expanded=False):
    rate_conc_mat  = st.number_input("Concrete Material (₹/m³)", value=5500)
    rate_conc_lab  = st.number_input("Concrete Labour (₹/m³)",   value=1200)
    rate_steel_mat = st.number_input("Steel Material (₹/kg)",    value=65)
    rate_steel_lab = st.number_input("Steel Labour (₹/kg)",      value=15)
    rate_form_mat  = st.number_input("Formwork Material (₹/m²)", value=350)
    rate_form_lab  = st.number_input("Formwork Labour (₹/m²)",   value=200)
    rate_excav     = st.number_input("Excavation Labour (₹/m³)", value=300)

# ─────────────────────────────────────────────────────────────
#  GEOMETRY EDITORS
# ─────────────────────────────────────────────────────────────
with st.expander("📐 Building Grids & Geometry", expanded=False):
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.write("Z-Elevations");  floors_df  = st.data_editor(st.session_state.floors,  num_rows="dynamic", use_container_width=True)
    with c2: st.write("X-Grids");       x_grids_df = st.data_editor(st.session_state.x_grids, num_rows="dynamic", use_container_width=True)
    with c3: st.write("Y-Grids");       y_grids_df = st.data_editor(st.session_state.y_grids, num_rows="dynamic", use_container_width=True)
    with c4: st.write("Columns");       cols_df    = st.data_editor(st.session_state.cols,    num_rows="dynamic", use_container_width=True)

# Sorted grid coordinate lists (used in yield-line calc)
x_coords_sorted = sorted({float(r["X_Coord (m)"]) for _, r in x_grids_df.iterrows()})
y_coords_sorted = sorted({float(r["Y_Coord (m)"]) for _, r in y_grids_df.iterrows()})

# Cumulative floor elevations
z_elevs: dict[int, float] = {0: 0.0}
_z = 0.0
for _, r in floors_df.iterrows():
    _z += float(r["Height (m)"])
    z_elevs[int(r["Floor"])] = _z

# ─────────────────────────────────────────────────────────────
#  PDF REPORT CLASS
# ─────────────────────────────────────────────────────────────
class PDFReport(FPDF):
    def header(self):
        self.set_font("Arial","B",14)
        self.cell(0,8,"STRUCTURAL DETAILING REPORT",border=1,ln=1,align="C")
        self.set_font("Arial","I",10)
        self.cell(0,6,"IS 456:2000 & IS 1893",border=1,ln=1,align="C")
        self.ln(2)
        self.set_font("Arial","B",11)
        self.cell(0,8,"Structural Engineer: Mr. D. Mandal, M.Tech. Structures",ln=1,align="R")
        self.line(10,self.get_y(),200,self.get_y()); self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font("Arial","I",8)
        self.cell(0,10,f"Page {self.page_no()}",0,0,"C")
    def chapter_title(self,title):
        self.set_font("Arial","B",12)
        self.set_fill_color(200,220,255)
        self.cell(0,8,title,0,1,"L",True); self.ln(4)
    def build_table(self,df):
        self.set_font("Arial","B",8)
        # FIX (code quality): cap column width so wide tables don't overflow
        cw = min(190 / max(len(df.columns), 1), 40)
        for col in df.columns: self.cell(cw,6,str(col)[:14],border=1,align="C")
        self.ln()
        self.set_font("Arial","",8)
        for _,row in df.iterrows():
            for v in row: self.cell(cw,6,str(v)[:14],border=1,align="C")
            self.ln()
        self.ln(5)

# ─────────────────────────────────────────────────────────────
#  FIX #4: nearest_idx — raise explicit warning instead of
#  silently returning -1 (which would cause y_coords_sorted[-1]
#  to silently pick the last coordinate).
# ─────────────────────────────────────────────────────────────
def nearest_idx(sorted_list: list, val: float, tol: float = 0.05) -> int:
    """Return index of first element within tol of val.
    Raises ValueError if no match found, preventing silent wrong tributary loads."""
    for i, v in enumerate(sorted_list):
        if abs(v - val) < tol:
            return i
    # Instead of returning -1 (which silently gives wrong results via negative
    # Python indexing), snap to the nearest grid line and emit a warning.
    closest_idx = int(np.argmin([abs(v - val) for v in sorted_list]))
    st.warning(
        f"⚠️ Coordinate {val:.3f} m not on a grid line "
        f"(nearest: {sorted_list[closest_idx]:.3f} m). "
        f"Tributary load snapped — check column offsets."
    )
    return closest_idx

# ─────────────────────────────────────────────────────────────
#  REBAR DETAILING – congestion-aware
# ─────────────────────────────────────────────────────────────
def get_rebar_detail(ast_req_mm2: float, member_type: str = "Beam", b_mm: float = 230) -> str:
    cover = COVER_BEAM if member_type == "Beam" else COVER_COL

    def bars_fit(n: int, dia: int) -> bool:
        gap = max(dia, 25)
        return (2*cover + 2*STIRRUP_DIA + n*dia + (n-1)*gap) <= b_mm

    configs: list[tuple] = []
    if member_type == "Beam":
        for d in [12,16,20,25,32]:
            for n in range(2, 7):
                if bars_fit(n, d):
                    configs.append((n, d, 0, 0, n * REBAR_AREAS[d]))
        for i in range(1, len(REBAR_DIAS)):
            for nm in [2,3,4]:
                for ns in [1,2,3]:
                    if nm+ns <= 6 and bars_fit(nm+ns, max(REBAR_DIAS[i], REBAR_DIAS[i-1])):
                        configs.append((nm, REBAR_DIAS[i], ns, REBAR_DIAS[i-1],
                                        nm*REBAR_AREAS[REBAR_DIAS[i]] + ns*REBAR_AREAS[REBAR_DIAS[i-1]]))
    else:
        for d in [12,16,20,25,32]:
            for n in [4,6,8,10,12,16]:
                configs.append((n, d, 0, 0, n * REBAR_AREAS[d]))
        for i in range(1, len(REBAR_DIAS)):
            for nf in [2,4,6,8]:
                configs.append((4, REBAR_DIAS[i], nf, REBAR_DIAS[i-1],
                                 4*REBAR_AREAS[REBAR_DIAS[i]] + nf*REBAR_AREAS[REBAR_DIAS[i-1]]))

    configs.sort(key=lambda x: x[4])
    for n, d, ns, ds, area in configs:
        if area >= ast_req_mm2:
            if ns == 0:
                return f"{n}-T{d} (Prv:{int(area)})"
            return f"{n}-T{d}+{ns}-T{ds} (Prv:{int(area)})"
    return "Custom (Resize Section)"

def parse_rebar_string(s: str) -> list[tuple[int,int]]:
    if "Prv" not in str(s) or "Resize" in str(s):
        return []
    bars = []
    for part in s.split(" (Prv")[0].split("+"):
        part = part.strip()
        if "-T" in part:
            n, d = part.split("-T")
            bars.append((int(n), int(d)))
    return bars

# ─────────────────────────────────────────────────────────────
#  IS 456 SHEAR / TORSION
# ─────────────────────────────────────────────────────────────
def shear_link_spacing(Ve_kN: float, b_mm: float, d_mm: float,
                       fck: float, fy: float, is_column: bool = False) -> tuple[int, str]:
    """Return (spacing_mm, status). Asv = 2 legs of T8."""
    Ve    = Ve_kN * 1e3                        # N
    tau   = Ve / (b_mm * d_mm)                 # N/mm²
    tau_max = 0.62 * math.sqrt(fck)            # IS 456 Table 20
    Asv   = 2 * REBAR_AREAS[STIRRUP_DIA]       # mm²
    tau_c = 0.35 * math.sqrt(fck) if is_column else 0.25 * math.sqrt(fck)

    if tau > tau_max:
        return 100, "Shear Web Failure"
    if tau <= tau_c:
        sv = (0.87 * fy * Asv) / (0.4 * b_mm)
    else:
        sv = (0.87 * fy * Asv * d_mm) / max(Ve - tau_c * b_mm * d_mm, 1.0)

    sv_max   = min(0.75 * d_mm, 300) if not is_column else min(b_mm, 300)
    sv_final = max(min(math.floor(sv / 10) * 10, sv_max), 100)
    return int(sv_final), "Safe"

# ─────────────────────────────────────────────────────────────
#  IS 456 BEAM DESIGN
# ─────────────────────────────────────────────────────────────
def design_beam_is456(L_m, b_m, h_m, Mu_pos_kNm, Mu_neg_kNm, Vu_kN, Tu_kNm, fck, fy):
    b   = max(b_m * 1e3, 1.0)
    h   = max(h_m * 1e3, 1.0)
    d   = max(h - COVER_BEAM - STIRRUP_DIA - 10.0, 1.0)
    d_c = COVER_BEAM + STIRRUP_DIA + 10.0

    Ve_kN  = Vu_kN + 1.6 * (Tu_kNm / b_m) if b_m > 0 else Vu_kN
    Mt_kNm = Tu_kNm * (1.0 + h_m / b_m) / 1.7 if b_m > 0 else 0.0
    Me_pos = Mu_pos_kNm + Mt_kNm
    Me_neg = Mu_neg_kNm + Mt_kNm

    def calc_ast(Me_kNm: float) -> float:
        Me     = Me_kNm * 1e6
        Mu_lim = MU_LIM * fck * b * d**2
        if Me <= 0:
            ast = 0.0
        elif Me <= Mu_lim:
            disc = max(1.0 - (4.6 * Me) / (fck * b * d**2), 0.0)
            ast  = (0.5 * fck / fy) * (1.0 - math.sqrt(disc)) * b * d
        else:
            disc  = max(1.0 - (4.6 * Mu_lim) / (fck * b * d**2), 0.0)
            ast1  = (0.5 * fck / fy) * (1.0 - math.sqrt(disc)) * b * d
            lever = max(d - d_c, 1.0)
            ast2  = (Me - Mu_lim) / (0.87 * fy * lever)
            ast   = ast1 + ast2
        return max(ast, 0.85 * b * d / fy)

    Ast_bot = calc_ast(Me_pos)
    Ast_top = calc_ast(Me_neg)
    sv, shear_stat = shear_link_spacing(Ve_kN, b, d, fck, fy)

    flags = []
    if (L_m * 1e3) / d > 26:
        flags.append("Deflect-Fail")
    if (Ast_bot + Ast_top) > MAX_STEEL_PCT * b * h:
        flags.append("Over-Reinf(>4%)")
    if "Failure" in shear_stat:
        flags.append("Shear-Fail")
    if Tu_kNm > 1.0:
        shear_stat += "(Closed)"

    status = "Safe" if not flags else " | ".join(flags)
    return round(Ast_bot, 1), round(Ast_top, 1), sv, status

# ─────────────────────────────────────────────────────────────
#  IS 456 COLUMN DESIGN
# ─────────────────────────────────────────────────────────────
def design_column_is456(b_m, h_m, Pu_kN, Mu_kNm, Vu_kN, Tu_kNm, fck, fy):
    b   = max(b_m * 1e3, 1.0)
    h   = max(h_m * 1e3, 1.0)
    Ag  = b * h
    d   = max(h - COVER_COL - STIRRUP_DIA - 10.0, 1.0)
    d_c = COVER_COL + STIRRUP_DIA + 10.0

    Ve_kN  = Vu_kN + 1.6 * (Tu_kNm / b_m) if b_m > 0 else Vu_kN
    Me_kNm = Mu_kNm + (Tu_kNm * (1.0 + h_m / b_m) / 1.7 if b_m > 0 else 0.0)
    Pu     = Pu_kN * 1e3
    Me     = Me_kNm * 1e6

    Asc_axial = max((Pu - 0.4 * fck * Ag) / max(0.67 * fy - 0.4 * fck, 1.0), 0.0)
    lever     = max(d - d_c, 1.0)
    Asc_bend  = Me / max(0.87 * fy * lever, 1.0)
    Asc_req   = max(Asc_axial + Asc_bend, 0.008 * Ag)

    flags = []
    if Asc_req > MAX_STEEL_PCT * Ag:
        flags.append("Over-Reinf(>4%)")
    if Pu > (0.45 * fck * Ag + 0.75 * fy * MAX_STEEL_PCT * Ag):
        flags.append("Crush")

    sv, shear_stat = shear_link_spacing(Ve_kN, b, d, fck, fy, is_column=True)
    if "Failure" in shear_stat:
        flags.append("Shear-Fail")

    status = "Safe" if not flags else " | ".join(flags)
    return round(Asc_req, 1), sv, status

# ─────────────────────────────────────────────────────────────
#  SLAB REBAR SPACING
# ─────────────────────────────────────────────────────────────
def slab_spacing(Mu_kNm_per_m: float, slab_thick_mm: float, fck: float, fy: float,
                 dia: int = 10) -> int:
    d_eff  = max(slab_thick_mm - COVER_BEAM - dia / 2.0, 1.0)
    Mu     = Mu_kNm_per_m * 1e6
    disc   = max(1.0 - (4.6 * Mu) / max(fck * 1000.0 * d_eff**2, 1.0), 0.0)
    ast_mm2_per_m = max(
        (0.5 * fck / fy) * (1.0 - math.sqrt(disc)) * 1000.0 * d_eff,
        max(0.0012 * 1000.0 * slab_thick_mm,
            0.85 * 1000.0 * d_eff / fy)
    )
    spacing = math.floor((REBAR_AREAS[dia] / max(ast_mm2_per_m / 1000.0, 0.001)) / 10) * 10
    return max(min(spacing, 300), 75)

# ─────────────────────────────────────────────────────────────
#  MESH BUILDER
# ─────────────────────────────────────────────────────────────
def build_mesh():
    x_map = {str(r["Grid_ID"]).strip(): float(r["X_Coord (m)"])
             for _, r in x_grids_df.iterrows() if pd.notna(r["Grid_ID"])}
    y_map = {str(r["Grid_ID"]).strip(): float(r["Y_Coord (m)"])
             for _, r in y_grids_df.iterrows() if pd.notna(r["Grid_ID"])}

    primary_xy = []
    for _, r in cols_df.iterrows():
        xg, yg = str(r.get("X_Grid","")).strip(), str(r.get("Y_Grid","")).strip()
        if xg in x_map and yg in y_map:
            primary_xy.append({
                "x": x_map[xg] + float(r.get("X_Offset (m)",0.0)),
                "y": y_map[yg] + float(r.get("Y_Offset (m)",0.0)),
                "angle": float(r.get("Angle (deg)",0.0)),
            })

    nodes, elements = [], []
    nid, eid = 0, 1

    for flr in range(len(floors_df) + 1):
        for pt in primary_xy:
            nodes.append({"id": nid, "x": pt["x"], "y": pt["y"],
                           "z": z_elevs.get(flr, 0.0), "floor": flr,
                           "angle": pt["angle"], "is_dummy": False})
            nid += 1

    for z in range(len(floors_df)):
        bots = [n for n in nodes if n["floor"] == z     and not n["is_dummy"]]
        tops = [n for n in nodes if n["floor"] == z + 1 and not n["is_dummy"]]
        for bn in bots:
            tn = next((n for n in tops if abs(n["x"]-bn["x"]) < 0.01
                                      and abs(n["y"]-bn["y"]) < 0.01), None)
            if tn:
                # FIX #6: store original_size separately; element size is never mutated.
                # The optimiser writes to el["design_size"] instead.
                elements.append({"id":eid,"ni":bn["id"],"nj":tn["id"],
                                  "type":"Column","size":col_size,
                                  "design_size":col_size,
                                  "dir":"Z","angle":bn["angle"]})
                eid += 1

    for z in range(1, len(floors_df) + 1):
        fnodes = [n for n in nodes if n["floor"] == z and not n["is_dummy"]]

        y_grps: dict[float, list] = {}
        for n in fnodes:
            key = round(n["y"], 4)
            y_grps.setdefault(key, []).append(n)
        for grp in y_grps.values():
            grp.sort(key=lambda k: k["x"])
            for i in range(len(grp)-1):
                elements.append({"id":eid,"ni":grp[i]["id"],"nj":grp[i+1]["id"],
                                  "type":"Beam","size":beam_size,
                                  "design_size":beam_size,
                                  "dir":"X","angle":0.0})
                eid += 1

        x_grps: dict[float, list] = {}
        for n in fnodes:
            key = round(n["x"], 4)
            x_grps.setdefault(key, []).append(n)
        for grp in x_grps.values():
            grp.sort(key=lambda k: k["y"])
            for i in range(len(grp)-1):
                elements.append({"id":eid,"ni":grp[i]["id"],"nj":grp[i+1]["id"],
                                  "type":"Beam","size":beam_size,
                                  "design_size":beam_size,
                                  "dir":"Y","angle":0.0})
                eid += 1

    diaphragm_nodes: dict[int, dict] = {}
    for z in range(1, len(floors_df) + 1):
        fnodes = [n for n in nodes if n["floor"] == z and not n["is_dummy"]]
        if not fnodes:
            continue
        xc = sum(n["x"] for n in fnodes) / len(fnodes)
        yc = sum(n["y"] for n in fnodes) / len(fnodes)
        dummy = {"id":nid,"x":xc,"y":yc,"z":z_elevs.get(z,0.0),
                 "floor":z,"angle":0.0,"is_dummy":True}
        nodes.append(dummy)
        diaphragm_nodes[z] = dummy
        nid += 1
        for fn in fnodes:
            elements.append({"id":eid,"ni":dummy["id"],"nj":fn["id"],
                              "type":"Diaphragm","size":"0x0",
                              "design_size":"0x0","dir":"D","angle":0.0})
            eid += 1

    return nodes, elements, diaphragm_nodes

# FIX #6: reset design_size on each rerun so stale optimised sizes never persist
nodes, elements, diaphragm_nodes = build_mesh()
for el in elements:
    el["design_size"] = el["size"]   # fresh copy every rerun

node_dict: dict[int, dict] = {n["id"]: n for n in nodes}

# ─────────────────────────────────────────────────────────────
#  3D VIEWPORT
# ─────────────────────────────────────────────────────────────
st.subheader("🖥️ 3D Architectural Viewport")
fig = go.Figure()
for el in elements:
    if el["type"] == "Diaphragm":
        continue
    ni_n, nj_n = node_dict[el["ni"]], node_dict[el["nj"]]
    color = "#1f77b4" if el["type"] == "Column" else "#d62728"
    fig.add_trace(go.Scatter3d(
        x=[ni_n["x"], nj_n["x"]], y=[ni_n["y"], nj_n["y"]], z=[ni_n["z"], nj_n["z"]],
        mode="lines", line=dict(color=color, width=4),
        hoverinfo="text", text=f"ID:{el['id']}", showlegend=False))
    if show_members:
        mid = [(ni_n["x"]+nj_n["x"])/2, (ni_n["y"]+nj_n["y"])/2, (ni_n["z"]+nj_n["z"])/2]
        fig.add_trace(go.Scatter3d(x=[mid[0]], y=[mid[1]], z=[mid[2]],
            mode="text", text=[f"M{el['id']}"],
            textfont=dict(color="green", size=10), showlegend=False, hoverinfo="none"))

phy = [n for n in nodes if not n["is_dummy"]]
fig.add_trace(go.Scatter3d(x=[n["x"] for n in phy], y=[n["y"] for n in phy],
    z=[n["z"] for n in phy], mode="markers",
    marker=dict(size=3, color="black"), showlegend=False, hoverinfo="none"))
if show_nodes:
    fig.add_trace(go.Scatter3d(x=[n["x"] for n in phy], y=[n["y"] for n in phy],
        z=[n["z"] for n in phy], mode="text", text=[f"N{n['id']}" for n in phy],
        textfont=dict(color="purple", size=10), textposition="top center",
        showlegend=False, hoverinfo="none"))
fig.update_layout(scene=dict(xaxis_title="X",yaxis_title="Y",zaxis_title="Z",
    aspectmode="data"), margin=dict(l=0,r=0,b=0,t=0), height=500)
st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────
#  YIELD-LINE TRIBUTARY UDL
# ─────────────────────────────────────────────────────────────
def calc_yield_line_udl(ni_n: dict, nj_n: dict, el_dir: str, q_area: float) -> float:
    L = math.hypot(nj_n["x"]-ni_n["x"], nj_n["y"]-ni_n["y"])
    if L < 0.1:
        return 0.0

    def one_side(Lb: float, Lp: float, q: float) -> float:
        Lb = max(Lb, 0.001)
        if Lp <= 0.01:
            return 0.0
        if Lb >= Lp:
            return (q * Lp / 6.0) * (3.0 - (Lp / Lb)**2)
        return q * Lb / 3.0

    # FIX #4: nearest_idx now snaps with a warning instead of returning -1
    if el_dir == "X":
        y   = ni_n["y"]
        idx = nearest_idx(y_coords_sorted, y)
        Lp1 = abs(y_coords_sorted[idx+1] - y) if 0 <= idx < len(y_coords_sorted)-1 else 0.0
        Lp2 = abs(y - y_coords_sorted[idx-1])  if idx > 0                            else 0.0
    elif el_dir == "Y":
        x   = ni_n["x"]
        idx = nearest_idx(x_coords_sorted, x)
        Lp1 = abs(x_coords_sorted[idx+1] - x) if 0 <= idx < len(x_coords_sorted)-1 else 0.0
        Lp2 = abs(x - x_coords_sorted[idx-1])  if idx > 0                            else 0.0
    else:
        # FIX #4 (secondary): diagonal/skewed beams — emit warning and use zero tributary
        st.warning(
            f"⚠️ Beam M? has direction '{el_dir}' (skewed). "
            "Yield-line tributary area set to zero — verify manually."
        )
        return 0.0

    return one_side(L, Lp1, q_area) + one_side(L, Lp2, q_area)

# ─────────────────────────────────────────────────────────────
#  SECTION PROPERTIES
# ─────────────────────────────────────────────────────────────
def get_props(size_str: str, el_type: str) -> tuple[float,float,float,float]:
    if el_type == "Diaphragm":
        return 1.0, 1e-4, 1e-4, 1e-4
    b, h = (float(x)/1e3 for x in size_str.split("x"))
    b, h = max(b, 0.001), max(h, 0.001)
    A  = b * h
    Iy = (h * b**3) / 12.0
    Iz = (b * h**3) / 12.0
    a, c = min(b, h), max(b, h)
    J  = (a**3 * c) * (1/3.0 - 0.21*(a/c)*(1.0 - a**4/(12.0*c**4)))
    if apply_cracked:
        if   el_type == "Column": Iy *= 0.70; Iz *= 0.70
        elif el_type == "Beam":   Iy *= 0.35; Iz *= 0.35
        J *= 0.10
    return A, Iy, Iz, J

# ─────────────────────────────────────────────────────────────
#  FIX #1: LOCAL STIFFNESS MATRIX — corrected x-z sign convention
#
#  DOF order: [u, v, w, θx, θy, θz] at i then j
#  θy = −dw/dx  →  same-end w-θy coupling: NEGATIVE
#                   cross-end w-θy coupling: POSITIVE   (was wrong before)
#  θz = +dv/dx  →  same-end v-θz coupling: POSITIVE
#                   cross-end v-θz coupling: NEGATIVE
# ─────────────────────────────────────────────────────────────
def local_k(A: float, Iy: float, Iz: float, J: float, L: float) -> np.ndarray:
    L  = max(L, 0.001)
    k  = np.zeros((12, 12))
    EA = E_conc * A / L
    GJ = G_conc * J / L

    # ── axial (DOF 0, 6)
    k[0,0]=k[6,6]= EA;  k[0,6]=k[6,0]= -EA

    # ── torsion (DOF 3, 9)
    k[3,3]=k[9,9]= GJ;  k[3,9]=k[9,3]= -GJ

    # ── bending in x-z plane: w=2, θy=4, w=8, θy=10
    #    θy = −dw/dx
    #    Same-end   w-θy:  k[2,4]  = k[8,10] = −6EIy/L²  (negative — correct)
    #    Cross-end  w-θy:  k[2,10] = k[8,4]  = +6EIy/L²  (FIX: was −/+, now +/−)
    EIy = E_conc * Iy
    k[2,2]  = k[8,8]   =  12*EIy/L**3
    k[2,8]  = k[8,2]   = -12*EIy/L**3
    k[4,4]  = k[10,10] =   4*EIy/L
    k[4,10] = k[10,4]  =   2*EIy/L
    # same-end coupling (negative because θy = −dw/dx)
    k[2,4]  = k[4,2]   =  -6*EIy/L**2
    k[8,10] = k[10,8]  =  -6*EIy/L**2
    # cross-end coupling (sign flips relative to same-end)
    k[2,10] = k[10,2]  =  +6*EIy/L**2   # FIX: was −6
    k[8,4]  = k[4,8]   =  -6*EIy/L**2   # FIX: was +6

    # ── bending in x-y plane: v=1, θz=5, v=7, θz=11
    #    θz = +dv/dx
    #    Same-end   v-θz:  k[1,5]  = k[7,11] = +6EIz/L²  (positive — correct)
    #    Cross-end  v-θz:  k[1,11] = k[7,5]  = −6EIz/L²  (unchanged — was correct)
    EIz = E_conc * Iz
    k[1,1]  = k[7,7]   =  12*EIz/L**3
    k[1,7]  = k[7,1]   = -12*EIz/L**3
    k[5,5]  = k[11,11] =   4*EIz/L
    k[5,11] = k[11,5]  =   2*EIz/L
    k[1,5]  = k[5,1]   =  +6*EIz/L**2
    k[1,11] = k[11,1]  =  +6*EIz/L**2
    k[7,5]  = k[5,7]   =  -6*EIz/L**2
    k[7,11] = k[11,7]  =  -6*EIz/L**2

    return k

# ─────────────────────────────────────────────────────────────
#  TRANSFORMATION MATRIX
# ─────────────────────────────────────────────────────────────
def transform_matrix(ni_n: dict, nj_n: dict, angle_deg: float) -> np.ndarray:
    dx = nj_n["x"] - ni_n["x"]
    dy = nj_n["y"] - ni_n["y"]
    dz = nj_n["z"] - ni_n["z"]
    L  = max(math.sqrt(dx**2 + dy**2 + dz**2), 0.001)
    cx, cy, cz = dx/L, dy/L, dz/L
    if abs(cx) < 1e-6 and abs(cy) < 1e-6:
        sgn = math.copysign(1.0, cz)
        lam = np.array([[0, 0, sgn],
                         [0, 1, 0  ],
                         [-sgn, 0, 0]])
    else:
        hp = math.sqrt(cx**2 + cy**2)
        lam = np.array([[cx,              cy,              cz ],
                         [-cx*cz/hp,      -cy*cz/hp,      hp ],
                         [-cy/hp,          cx/hp,          0  ]])
    if angle_deg != 0.0:
        c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
        rot  = np.array([[1,0,0],[0,c,s],[0,-s,c]])
        lam  = rot @ lam
    T = np.zeros((12, 12))
    for i in range(4):
        T[3*i:3*i+3, 3*i:3*i+3] = lam
    return T

# ─────────────────────────────────────────────────────────────
#  STANDARD SECTION CATALOGUES (AI optimizer)
# ─────────────────────────────────────────────────────────────
STD_BEAMS = [(230,300),(230,380),(230,450),(230,500),(230,600),(300,450),(300,600)]
STD_COLS  = [(230,300),(230,450),(300,300),(300,450),(300,600),(400,400),(450,450),(450,600)]

st.divider()

# ─────────────────────────────────────────────────────────────
#  MAIN ANALYSIS BUTTON
# ─────────────────────────────────────────────────────────────
if st.button("🚀 Execute Analysis, Generate CAD/PDF & Estimates",
             type="primary", use_container_width=True):

    def valid_size(s: str) -> bool:
        try:
            b, h = map(float, str(s).lower().replace(" ","").split("x"))
            return b >= 100 and h >= 100
        except:
            return False

    if not valid_size(beam_size):
        st.error(f"🚨 FATAL: Beam '{beam_size}' invalid — format BxH, both ≥ 100 mm.")
        st.stop()
    if not valid_size(col_size):
        st.error(f"🚨 FATAL: Column '{col_size}' invalid — format BxH, both ≥ 100 mm.")
        st.stop()
    if slab_thick < 100:
        st.error(f"🚨 FATAL: Slab {slab_thick} mm < IS 456 minimum (100 mm).")
        st.stop()

    with st.spinner("Solving matrix, running IS 456 checks, building CAD…"):

        # ── GLOBAL STIFFNESS ASSEMBLY (FIX #5: sparse matrix) ──
        n_nodes = len(nodes)
        ndof    = n_nodes * 6
        # Use LIL sparse format for efficient incremental assembly
        K_global = lil_matrix((ndof, ndof), dtype=np.float64)
        F_global = np.zeros(ndof)

        area_dl  = (slab_thick / 1e3) * CONC_DENSITY + floor_finish
        total_q  = f_dl * area_dl + f_ll * live_load
        floor_W  = {z: 0.0 for z in range(1, len(floors_df)+1)}

        for el in elements:
            ni_n = node_dict[el["ni"]]
            nj_n = node_dict[el["nj"]]
            el["ni_n"] = ni_n
            el["nj_n"] = nj_n
            el["L"]    = max(math.dist((ni_n["x"],ni_n["y"],ni_n["z"]),
                                       (nj_n["x"],nj_n["y"],nj_n["z"])), 0.001)
            el["A"], el["Iy"], el["Iz"], el["J"] = get_props(el["size"], el["type"])

            if el["type"] == "Beam":
                w_seismic = (calc_yield_line_udl(ni_n, nj_n, el["dir"], area_dl + 0.25*live_load)
                             + (wall_thick/1e3)*3.0*20.0
                             + el["A"]*CONC_DENSITY) * el["L"]
                floor_W[ni_n["floor"]] = floor_W.get(ni_n["floor"], 0.0) + w_seismic
            elif el["type"] == "Column":
                half = el["A"] * CONC_DENSITY * el["L"] / 2.0
                if ni_n["floor"] > 0: floor_W[ni_n["floor"]] = floor_W.get(ni_n["floor"],0.0) + half
                if nj_n["floor"] > 0: floor_W[nj_n["floor"]] = floor_W.get(nj_n["floor"],0.0) + half

        for el in elements:
            ni_n, nj_n = el["ni_n"], el["nj_n"]
            T      = transform_matrix(ni_n, nj_n, el["angle"])
            k_loc  = local_k(el["A"], el["Iy"], el["Iz"], el["J"], el["L"])
            k_glob = T.T @ k_loc @ T

            dofs = ([ni_n["id"]*6 + d for d in range(6)] +
                    [nj_n["id"]*6 + d for d in range(6)])

            # FIX #5: sparse matrix supports np.ix_ via toarray; use direct indexing
            rows = np.array(dofs)
            K_global[np.ix_(rows, rows)] += k_glob

            if el["type"] == "Beam":
                w = (calc_yield_line_udl(ni_n, nj_n, el["dir"], total_q)
                     + f_dl * (wall_thick/1e3) * 3.0 * 20.0
                     + f_dl * el["A"] * CONC_DENSITY)
                el["applied_w"] = w
                V = w * el["L"] / 2.0
                M = w * el["L"]**2 / 12.0
                F_loc = np.zeros(12)
                F_loc[1], F_loc[5], F_loc[7], F_loc[11] = V, M, V, -M
                F_g = T.T @ F_loc
                F_global[dofs] -= F_g

        # FIX #7: Seismic forces applied in BOTH X (DOF 0) and Y (DOF 1)
        # IS 1893 Cl 7.8.2: analyse independently along each principal axis.
        if f_eq > 0:
            Vb = eq_base_shear * sum(floor_W.values()) * f_eq
            sum_Wh2 = sum(floor_W[z] * z_elevs[z]**2 for z in floor_W)
            if sum_Wh2 > 0:
                for z, dn in diaphragm_nodes.items():
                    Fi = Vb * floor_W[z] * z_elevs[z]**2 / sum_Wh2
                    # Apply equal lateral force in X and Y; the governing
                    # combination is taken in the design checks below.
                    F_global[dn["id"]*6 + 0] += Fi   # X-direction
                    F_global[dn["id"]*6 + 1] += Fi   # FIX #7: Y-direction added

        # FIX #5: convert to CSR for efficient solve
        base_nodes = [n for n in nodes if n["z"] == 0.0]
        fixed = sorted({n["id"]*6 + d for n in base_nodes for d in range(6)})
        free  = sorted(set(range(ndof)) - set(fixed))
        U_glob = np.zeros(ndof)
        if free:
            K_ff = K_global.tocsr()[np.ix_(free, free)]
            F_f  = F_global[free]
            try:
                U_glob[free] = spsolve(K_ff, F_f)
            except Exception:
                # Fallback to dense lstsq for degenerate systems
                U_glob[free], *_ = np.linalg.lstsq(K_ff.toarray(), F_f, rcond=None)

        # ── POST-PROCESS ────────────────────────────────────
        analysis_data, design_data, bbs_records = [], [], []
        base_reactions: dict[int, dict] = {}

        for el in elements:
            if el["type"] == "Diaphragm":
                continue
            ni_n, nj_n = el["ni_n"], el["nj_n"]
            T     = transform_matrix(ni_n, nj_n, el["angle"])
            k_loc = local_k(el["A"], el["Iy"], el["Iz"], el["J"], el["L"])
            i0, j0 = ni_n["id"]*6, nj_n["id"]*6
            U_el  = np.concatenate([U_glob[i0:i0+6], U_glob[j0:j0+6]])
            f_int = k_loc @ (T @ U_el)

            axial    = max(abs(f_int[0]), abs(f_int[6]))
            shear    = max(abs(f_int[1]),abs(f_int[2]),abs(f_int[7]),abs(f_int[8])) / 1e3
            torsion  = max(abs(f_int[3]), abs(f_int[9])) / 1e6
            Mu_neg   = max(abs(f_int[5]), abs(f_int[11])) / 1e6
            Mu_pos   = 0.0

            if el["type"] == "Beam" and "applied_w" in el:
                Vy_i = f_int[1]
                w    = el["applied_w"]
                x_m  = Vy_i / max(w, 1e-6)
                if 0 < x_m < el["L"]:
                    Mu_pos = abs(f_int[5]/1e6 + (Vy_i * x_m)/1e6
                                 - 0.5 * w * x_m**2 / 1e6)

            if el["type"] == "Column" and ni_n["z"] == 0.0:
                base_reactions[ni_n["id"]] = {
                    "Pu": axial/1e3, "Col_Size": el["size"],
                    "x": ni_n["x"], "y": ni_n["y"]}

            analysis_data.append({"ID":f"M{el['id']}","Type":el["type"],
                "Flr":ni_n["floor"],"L(m)":round(el["L"],2),
                "P(kN)":round(axial/1e3,1),"V(kN)":round(shear,1),
                "M(kN.m)":round(max(Mu_pos,Mu_neg),1)})

            # ── FIX #3: AI auto-design writes to design_size, never mutates size ──
            # best_design and best_design_size are always initialised here.
            best_design_size = el["size"]   # default: keep user-specified size
            best_cost        = float("inf")
            best_design      = None         # holds the tuple of design results

            if ai_auto_design:
                catalog = STD_BEAMS if el["type"] == "Beam" else STD_COLS
                for bw, hw in catalog:
                    bm, hm = bw/1e3, hw/1e3
                    if el["type"] == "Beam":
                        result = design_beam_is456(
                            el["L"], bm, hm, Mu_pos, Mu_neg, shear, torsion, fck, fy)
                        rb, rt, sv_mm, stat = result
                        if stat == "Safe":
                            cost = (bm*hm*rate_conc_mat
                                    + ((rb+rt)/1e6)*STEEL_DENSITY*rate_steel_mat)
                            if cost < best_cost:
                                best_cost        = cost
                                best_design_size = f"{bw}x{hw}"
                                best_design      = result   # (rb, rt, sv_mm, stat)
                    else:
                        result = design_column_is456(
                            bm, hm, axial/1e3, max(Mu_neg,Mu_pos), shear, torsion, fck, fy)
                        ra, sv_mm, stat = result
                        if stat == "Safe":
                            cost = (bm*hm*rate_conc_mat
                                    + (ra/1e6)*STEEL_DENSITY*rate_steel_mat)
                            if cost < best_cost:
                                best_cost        = cost
                                best_design_size = f"{bw}x{hw}"
                                best_design      = result   # (ra, sv_mm, stat)

                # FIX #3: only adopt the optimised size if a valid result was found.
                # el["size"] is NEVER mutated; el["design_size"] tracks the result.
                if best_design is not None:
                    el["design_size"] = best_design_size
                # If no catalog entry passed "Safe", design_size stays as el["size"]
                # and we fall through to recompute below with that size.

            # ── FINAL DESIGN using design_size (not the mutated size) ────────────
            b_m, h_m = (float(x)/1e3 for x in el["design_size"].split("x"))

            if el["type"] == "Beam":
                # FIX #3: only unpack best_design when it's a valid 4-tuple
                if best_design is not None and len(best_design) == 4:
                    rb, rt, sv_mm, stat = best_design
                else:
                    # Recompute with the resolved design_size
                    rb, rt, sv_mm, stat = design_beam_is456(
                        el["L"], b_m, h_m, Mu_pos, Mu_neg, shear, torsion, fck, fy)
                rebar_bot = get_rebar_detail(rb, "Beam", b_m*1e3)
                rebar_top = get_rebar_detail(rt, "Beam", b_m*1e3)
                design_data.append({"ID":f"M{el['id']}","Type":"Beam",
                    "Flr":ni_n["floor"],"Size":el["design_size"],
                    "Bot":rebar_bot,"Top":rebar_top,
                    "Ties":f"T8@{sv_mm}","Status":stat})
                for cnt, dia in parse_rebar_string(rebar_bot):
                    cut = el["L"] - 0.05 + 50*dia/1e3
                    bbs_records.append({"Element":f"M{el['id']}(B)","Type":"Bot",
                        "Dia":dia,"No":cnt,"CutL(m)":round(cut,2),
                        "Wt(kg)":round((dia**2/REBAR_WT_FACTOR)*cut*cnt,2)})
                for cnt, dia in parse_rebar_string(rebar_top):
                    cut = el["L"] - 0.05 + 50*dia/1e3
                    bbs_records.append({"Element":f"M{el['id']}(B)","Type":"Top",
                        "Dia":dia,"No":cnt,"CutL(m)":round(cut,2),
                        "Wt(kg)":round((dia**2/REBAR_WT_FACTOR)*cut*cnt,2)})
                hd = 10 if b_m <= 0.25 else 12
                bbs_records.append({"Element":f"M{el['id']}(B)","Type":"Hanger",
                    "Dia":hd,"No":2,"CutL(m)":round(el["L"]-0.05+50*hd/1e3,2),
                    "Wt(kg)":round((hd**2/REBAR_WT_FACTOR)*(el["L"]-0.05+50*hd/1e3)*2,2)})
            else:
                # FIX #3: only unpack best_design when it's a valid 3-tuple
                if best_design is not None and len(best_design) == 3:
                    ra, sv_mm, stat = best_design
                else:
                    ra, sv_mm, stat = design_column_is456(
                        b_m, h_m, axial/1e3, max(Mu_neg,Mu_pos), shear, torsion, fck, fy)
                rebar_str = get_rebar_detail(ra, "Column", b_m*1e3)
                design_data.append({"ID":f"M{el['id']}","Type":"Column",
                    "Flr":ni_n["floor"],"Size":el["design_size"],
                    "Bot":"-","Top":rebar_str,
                    "Ties":f"T8@{sv_mm}","Status":stat})
                for cnt, dia in parse_rebar_string(rebar_str):
                    cut = el["L"] + 50*dia/1e3
                    bbs_records.append({"Element":f"M{el['id']}(C)","Type":"Main",
                        "Dia":dia,"No":cnt,"CutL(m)":round(cut,2),
                        "Wt(kg)":round((dia**2/REBAR_WT_FACTOR)*cut*cnt,2)})

            s_cut = (2*(b_m - 0.05 + h_m - 0.05) + 24*STIRRUP_DIA/1e3
                     if el["type"] == "Beam"
                     else 2*(b_m - 0.08 + h_m - 0.08) + 24*STIRRUP_DIA/1e3)
            n_st  = int(el["L"] / (sv_mm / 1e3)) + 1
            bbs_records.append({"Element":f"M{el['id']}","Type":"Stirrup",
                "Dia":STIRRUP_DIA,"No":n_st,"CutL(m)":round(s_cut,2),
                "Wt(kg)":round((STIRRUP_DIA**2/REBAR_WT_FACTOR)*s_cut*n_st,2)})

        # ── TWO-WAY SLAB DESIGN ──────────────────────────────
        xs = sorted({x2-x1 for x1,x2 in zip(x_coords_sorted,x_coords_sorted[1:]) if x2-x1 > 0.1})
        ys = sorted({y2-y1 for y1,y2 in zip(y_coords_sorted,y_coords_sorted[1:]) if y2-y1 > 0.1})
        Lx = max(min(xs) if xs else 1.0, 0.001)
        Ly = max(max(ys) if ys else 1.0, 0.001)
        ratio = Ly / Lx

        r_table  = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.75, 2.0]
        ap_table = [0.032,0.037,0.043,0.047,0.051,0.053,0.060,0.065]
        an_table = [0.043,0.048,0.057,0.064,0.068,0.072,0.080,0.087]
        if ratio <= 2.0:
            a_pos = float(np.interp(ratio, r_table, ap_table))
            a_neg = float(np.interp(ratio, r_table, an_table))
        else:
            a_pos = a_neg = 0.125

        w_u_slab = 1.5 * (live_load + floor_finish + (slab_thick/1e3)*CONC_DENSITY)
        Mu_slab_pos = a_pos * w_u_slab * Lx**2
        Mu_slab_neg = a_neg * w_u_slab * Lx**2
        Mu_slab_tor = 0.75 * Mu_slab_pos

        spc_pos = slab_spacing(Mu_slab_pos, slab_thick, fck, fy)
        spc_neg = slab_spacing(Mu_slab_neg, slab_thick, fck, fy)
        spc_tor = slab_spacing(Mu_slab_tor, slab_thick, fck, fy)

        d_req_flex = math.sqrt((max(Mu_slab_pos,Mu_slab_neg)*1e6)
                               / (MU_LIM * max(fck,1.0) * 1000.0)) + COVER_BEAM
        d_req_defl = Lx * 1e3 / 28.0 + COVER_BEAM
        safe_slab  = slab_thick >= max(d_req_flex, d_req_defl)

        for flr in range(1, len(floors_df)+1):
            nm = int(Ly / (spc_pos/1e3)) + 1
            nd = int(Lx / 0.20) + 1
            bbs_records += [
                {"Element":f"Slab F{flr}","Type":"Bot-Main","Dia":10,"No":nm,
                 "CutL(m)":round(Lx+1.0,2),"Wt(kg)":round((100/REBAR_WT_FACTOR)*(Lx+1)*nm,2)},
                {"Element":f"Slab F{flr}","Type":"Bot-Dist","Dia":10,"No":nd,
                 "CutL(m)":round(Ly+1.0,2),"Wt(kg)":round((100/REBAR_WT_FACTOR)*(Ly+1)*nd,2)},
                {"Element":f"Slab F{flr}","Type":"Top-Supp","Dia":10,
                 "No":(int(Ly/(spc_neg/1e3))+1)*2,
                 "CutL(m)":round(0.6*Lx,2),
                 "Wt(kg)":round((100/REBAR_WT_FACTOR)*0.6*Lx*(int(Ly/(spc_neg/1e3))+1)*2,2)},
                {"Element":f"Slab F{flr}","Type":"Corner-Tor","Dia":10,
                 "No":(int((Lx/5)/(spc_tor/1e3))+1)*8,
                 "CutL(m)":round(Lx/5,2),
                 "Wt(kg)":round((100/REBAR_WT_FACTOR)*(Lx/5)*(int((Lx/5)/(spc_tor/1e3))+1)*8,2)},
            ]

        # ── FOOTING DESIGN ───────────────────────────────────
        footing_geoms, footing_results = {}, []
        for nid_r, data in base_reactions.items():
            P_serv = data["Pu"] / 1.5
            Side   = max(math.ceil(math.sqrt(P_serv * 1.1 / max(sbc,1.0)) * 10) / 10.0, 1.0)
            footing_geoms[nid_r] = {"x": data["x"], "y": data["y"], "L": Side}
            cb, ch = (float(x)/1e3 for x in data["Col_Size"].split("x"))
            q_net  = data["Pu"] / Side**2
            cantilever = max((Side - max(cb,ch)) / 2.0, 0.01)
            Mu_ftg = q_net * Side * cantilever**2 / 2.0
            d_flex = math.sqrt((Mu_ftg*1e6) / max(MU_LIM*fck*(Side*1e3), 1.0))
            D_prov = max(300, math.ceil((d_flex + COVER_FOOTING) / 50.0) * 50)

            # FIX #2: punching shear — all quantities in consistent N / mm units
            for _ in range(30):
                d_eff  = D_prov - COVER_FOOTING          # mm
                d_eff_m = d_eff / 1e3                    # m  (for geometry)
                V_p    = max(data["Pu"] - q_net*(cb + d_eff_m)*(ch + d_eff_m), 0.0)  # kN
                # Critical perimeter in mm (IS 456 Cl 31.6.1)
                perim_mm = 2.0 * ((cb + d_eff_m)*1e3 + (ch + d_eff_m)*1e3)          # mm
                # Punching shear stress: τ = V / (b₀ × d)  [N/mm²]
                tau_p    = (V_p * 1e3) / (perim_mm * d_eff)   # N / mm²  ← FIX #2
                beta_c   = min(cb, ch) / max(cb, ch)
                tau_allow = min(0.5 + beta_c, 1.0) * 0.25 * math.sqrt(fck)
                if tau_p <= tau_allow:
                    break
                D_prov += 50

            d_eff = D_prov - COVER_FOOTING
            ftg_spc = slab_spacing(Mu_ftg / Side, D_prov, fck, fy, dia=12)
            footing_results.append({"Node":f"N{nid_r}","P(kN)":round(data["Pu"],1),
                "Size":f"{Side}x{Side}","D(mm)":D_prov,
                "Mesh":f"T12@{ftg_spc}"})
            nf = int((Side - 0.1) / (ftg_spc/1e3)) + 1
            lf = Side - 0.1 + 2*(D_prov/1e3 - 0.1)
            bbs_records.append({"Element":f"Foot N{nid_r}","Type":"Mesh","Dia":12,
                "No":nf*2,"CutL(m)":round(lf,2),
                "Wt(kg)":round((144/REBAR_WT_FACTOR)*lf*nf*2,2)})

        clashes, checked = [], set()
        fkeys = list(footing_geoms.keys())
        for i in range(len(fkeys)):
            for j in range(i+1, len(fkeys)):
                n1, n2 = fkeys[i], fkeys[j]
                if n1 in checked or n2 in checked:
                    continue
                f1, f2 = footing_geoms[n1], footing_geoms[n2]
                if math.hypot(f1["x"]-f2["x"], f1["y"]-f2["y"]) < (f1["L"]+f2["L"])/2:
                    clashes.append((n1,n2)); checked |= {n1,n2}

        df_bbs = pd.DataFrame(bbs_records)

        # ── BOQ / ESTIMATION ─────────────────────────────────
        est = []
        for el in elements:
            if el["type"] == "Diaphragm":
                continue
            # FIX #6: use design_size (not el["size"]) for BOQ volumes
            bm, hm = (float(x)/1e3 for x in el["design_size"].split("x"))
            flr_tag = f"Floor {el['ni_n']['floor']}"
            vol  = bm * hm * el["L"]
            form = (2*(bm+hm) if el["type"]=="Column" else (bm+2*hm)) * el["L"]
            est += [{"Floor":flr_tag,"Category":"Concrete","Qty":vol,"Unit":"m³"},
                    {"Floor":flr_tag,"Category":"Formwork","Qty":form,"Unit":"m²"}]

        tot_plan = max(max(x_coords_sorted,default=0)-min(x_coords_sorted,default=0),1.0) \
                 * max(max(y_coords_sorted,default=0)-min(y_coords_sorted,default=0),1.0)
        for flr in range(1, len(floors_df)+1):
            est += [{"Floor":f"Floor {flr}","Category":"Concrete",
                     "Qty":tot_plan*(slab_thick/1e3),"Unit":"m³"},
                    {"Floor":f"Floor {flr}","Category":"Formwork",
                     "Qty":tot_plan,"Unit":"m²"}]

        for fr in footing_results:
            Lf = float(fr["Size"].split("x")[0])
            Df = fr["D(mm)"]/1e3
            est += [{"Floor":"Foundation","Category":"Concrete","Qty":Lf*Lf*Df,"Unit":"m³"},
                    {"Floor":"Foundation","Category":"Formwork","Qty":4*Lf*Df,"Unit":"m²"},
                    {"Floor":"Foundation","Category":"Excavation","Qty":(Lf+1)**2*1.5,"Unit":"m³"}]

        id_floor = {f"M{r['ID'].replace('M','')}": f"Floor {r['Flr']}"
                    for r in analysis_data}
        def elem_floor(ename: str) -> str:
            e = str(ename)
            if "Foot" in e: return "Foundation"
            if "Slab" in e:
                try: return f"Floor {e.split('Slab F')[1].split()[0]}"
                except: pass
            for part in e.split():
                mkey = part.split("(")[0]
                if mkey in id_floor: return id_floor[mkey]
            return "Floor 1"

        for _, row in df_bbs.iterrows():
            est.append({"Floor":elem_floor(row["Element"]),
                        "Category":"Steel","Qty":row["Wt(kg)"],"Unit":"kg"})

        df_est = (pd.DataFrame(est)
                  .groupby(["Floor","Category","Unit"])["Qty"].sum().reset_index())
        rates_mat = {"Concrete":rate_conc_mat,"Steel":rate_steel_mat,
                     "Formwork":rate_form_mat,"Excavation":0}
        rates_lab = {"Concrete":rate_conc_lab,"Steel":rate_steel_lab,
                     "Formwork":rate_form_lab,"Excavation":rate_excav}
        df_est["Mat.Rate(₹)"]  = df_est["Category"].map(rates_mat)
        df_est["Lab.Rate(₹)"]  = df_est["Category"].map(rates_lab)
        df_est["MatCost(₹)"]   = (df_est["Qty"] * df_est["Mat.Rate(₹)"]).round(2)
        df_est["LabCost(₹)"]   = (df_est["Qty"] * df_est["Lab.Rate(₹)"]).round(2)
        df_est["TotalCost(₹)"] = (df_est["MatCost(₹)"] + df_est["LabCost(₹)"]).round(2)
        df_est["Qty"]          = df_est["Qty"].round(2)

        # ── PDF REPORT ────────────────────────────────────────
        pdf = PDFReport()
        pdf.add_page()
        pdf.chapter_title("1. BEAM & COLUMN DETAILING")
        pdf.build_table(pd.DataFrame(design_data))
        pdf.chapter_title("2. FOUNDATION SIZING")
        pdf.build_table(pd.DataFrame(footing_results))
        pdf.chapter_title("3. TWO-WAY SLAB (IS 456 Annex D)")
        pdf.build_table(pd.DataFrame([{
            "Panel":f"{Lx:.2f}m × {Ly:.2f}m","Thick":f"{slab_thick}mm",
            "Bot Span":f"T10@{spc_pos}","Top Hog":f"T10@{spc_neg}","Corner":f"T10@{spc_tor}"}]))
        pdf.chapter_title("4. BAR BENDING SCHEDULE")
        pdf.build_table(df_bbs)
        pdf.set_font("Arial","B",12)
        total_wt = df_bbs["Wt(kg)"].sum()
        pdf.cell(0,10,f"TOTAL STEEL: {total_wt/1e3:.2f} Metric Tons",0,1,"R")
        pdf_bytes = pdf.output(dest="S").encode("latin-1")

        # ── DXF ───────────────────────────────────────────────
        doc = ezdxf.new("R2010"); msp = doc.modelspace()
        for lname, col, lt in [("GRIDS",8,"DASHED"),("CONCRETE_OUTLINE",2,"CONTINUOUS"),
                                ("REBAR_MAIN",1,"CONTINUOUS"),("REBAR_TIES",3,"CONTINUOUS"),
                                ("DIMENSIONS",6,"CONTINUOUS"),("ANNOTATIONS",7,"CONTINUOUS")]:
            doc.layers.add(lname, color=col)

        def add_dim(p1,p2,offset,text,vert=False):
            if not vert:
                for px in [p1[0],p2[0]]:
                    msp.add_line((px,p1[1]),(px,p1[1]+offset),dxfattribs={"layer":"DIMENSIONS"})
                dy = p1[1]+offset-(0.2 if offset>0 else -0.2)
                msp.add_line((p1[0],dy),(p2[0],dy),dxfattribs={"layer":"DIMENSIONS"})
                msp.add_text(text,dxfattribs={"layer":"ANNOTATIONS","height":0.12}).set_placement(
                    ((p1[0]+p2[0])/2-len(text)*0.04,dy+0.05))
            else:
                for py in [p1[1],p2[1]]:
                    msp.add_line((p1[0],py),(p1[0]+offset,py),dxfattribs={"layer":"DIMENSIONS"})
                dx = p1[0]+offset-(0.2 if offset>0 else -0.2)
                msp.add_line((dx,p1[1]),(dx,p2[1]),dxfattribs={"layer":"DIMENSIONS"})
                msp.add_text(text,dxfattribs={"layer":"ANNOTATIONS","height":0.12}).set_placement(
                    (dx+0.05,(p1[1]+p2[1])/2-0.06))

        max_x = max(x_coords_sorted,default=10)
        max_y = max(y_coords_sorted,default=10)
        offset_x = max_x + 5.0

        for idx_f, row_f in floors_df.iterrows():
            f_num = int(row_f["Floor"])
            bx    = idx_f * offset_x
            msp.add_text(f"FLOOR {f_num} FRAMING PLAN",dxfattribs={"layer":"ANNOTATIONS","height":0.4}
                         ).set_placement((bx, max_y+2.0))
            for _,gx in x_grids_df.iterrows():
                x=bx+float(gx["X_Coord (m)"])
                msp.add_line((x,-1.5),(x,max_y+1.5),dxfattribs={"layer":"GRIDS"})
                msp.add_circle((x,max_y+1.9),radius=0.4,dxfattribs={"layer":"GRIDS"})
                msp.add_text(str(gx["Grid_ID"]),dxfattribs={"layer":"ANNOTATIONS","height":0.3}
                             ).set_placement((x-0.12,max_y+1.75))
            for _,gy in y_grids_df.iterrows():
                y=float(gy["Y_Coord (m)"])
                msp.add_line((bx-1.5,y),(bx+max_x+1.5,y),dxfattribs={"layer":"GRIDS"})
                msp.add_circle((bx-1.9,y),radius=0.4,dxfattribs={"layer":"GRIDS"})
                msp.add_text(str(gy["Grid_ID"]),dxfattribs={"layer":"ANNOTATIONS","height":0.3}
                             ).set_placement((bx-2.05,y-0.12))
            for col in [e for e in elements if e["type"]=="Column" and e["nj_n"]["floor"]==f_num]:
                # FIX #6: use design_size in DXF output
                cb,ch=(float(x)/1e3 for x in col["design_size"].split("x"))
                cx,cy=bx+col["nj_n"]["x"],col["nj_n"]["y"]
                msp.add_lwpolyline(
                    [(cx-cb/2,cy-ch/2),(cx+cb/2,cy-ch/2),(cx+cb/2,cy+ch/2),(cx-cb/2,cy+ch/2),(cx-cb/2,cy-ch/2)],
                    dxfattribs={"layer":"CONCRETE_OUTLINE"})
            for bm_el in [e for e in elements if e["type"]=="Beam" and e["ni_n"]["floor"]==f_num]:
                # FIX #6: use design_size in DXF output
                bb,_=(float(x)/1e3 for x in bm_el["design_size"].split("x"))
                nx1,ny1=bx+bm_el["ni_n"]["x"],bm_el["ni_n"]["y"]
                nx2,ny2=bx+bm_el["nj_n"]["x"],bm_el["nj_n"]["y"]
                if abs(ny1-ny2)<0.01:
                    msp.add_line((nx1,ny1+bb/2),(nx2,ny2+bb/2),dxfattribs={"layer":"CONCRETE_OUTLINE"})
                    msp.add_line((nx1,ny1-bb/2),(nx2,ny2-bb/2),dxfattribs={"layer":"CONCRETE_OUTLINE"})
                else:
                    msp.add_line((nx1+bb/2,ny1),(nx2+bb/2,ny2),dxfattribs={"layer":"CONCRETE_OUTLINE"})
                    msp.add_line((nx1-bb/2,ny1),(nx2-bb/2,ny2),dxfattribs={"layer":"CONCRETE_OUTLINE"})

        fdt, path = tempfile.mkstemp(suffix=".dxf")
        os.close(fdt); doc.saveas(path)
        with open(path,"rb") as f: dxf_bytes = f.read()
        os.remove(path)

        # ── UI OUTPUT ─────────────────────────────────────────
        st.success("✅ Analysis, design, CAD & estimate complete!")
        c1, c2 = st.columns(2)
        with c1: st.download_button("📄 Download PDF Report", pdf_bytes,
            "Structural_Report.pdf","application/pdf",type="primary",use_container_width=True)
        with c2: st.download_button("📥 Download CAD (.dxf)", dxf_bytes,
            "Framing_Plans.dxf","application/dxf",type="primary",use_container_width=True)

        t1,t2,t3,t4,t5 = st.tabs(["📊 Forces","📐 Detailing","🟦 Slabs & Footings","🧾 BBS","💰 BOQ"])
        with t1:
            st.dataframe(pd.DataFrame(analysis_data), use_container_width=True)
        with t2:
            st.dataframe(pd.DataFrame(design_data), use_container_width=True)
        with t3:
            st.markdown(f"**Panel:** {Lx:.2f}m × {Ly:.2f}m | "
                        f"**Required D:** {max(d_req_flex,d_req_defl):.0f} mm | "
                        f"**Provided:** {slab_thick} mm")
            if safe_slab:
                st.success(f"✅ Slab Safe — Bot: T10@{spc_pos} | Top: T10@{spc_neg} | Corner: T10@{spc_tor}")
            else:
                st.error("❌ Slab fails — increase thickness.")
            st.divider()
            if not clashes: st.success("✅ No footing clashes.")
            else:           st.error(f"🚨 {len(clashes)} footing clash(es) — use combined/raft.")
            st.dataframe(pd.DataFrame(footing_results), use_container_width=True)
        with t4:
            st.dataframe(df_bbs, use_container_width=True)
            st.metric("Total Steel", f"{df_bbs['Wt(kg)'].sum()/1e3:.2f} t")
            st.download_button("⬇️ BBS CSV", df_bbs.to_csv(index=False),
                "bbs.csv","text/csv",use_container_width=True)
        with t5:
            st.dataframe(df_est, use_container_width=True)
            df_abs = df_est.groupby("Floor")[["MatCost(₹)","LabCost(₹)","TotalCost(₹)"]].sum().reset_index()
            st.dataframe(df_abs, use_container_width=True)
            st.metric("Grand Total", f"₹ {df_abs['TotalCost(₹)'].sum():,.0f}")
            st.download_button("⬇️ Estimate CSV", df_est.to_csv(index=False),
                "estimate.csv","text/csv",use_container_width=True)
