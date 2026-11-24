import streamlit as st
import re
import json
import calendar
import pymssql
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_pdf import PdfPages
import io
import warnings

warnings.filterwarnings('ignore')

# ==============================================================================
# CONFIGURACIÓN DE PÁGINA STREAMLIT
# ==============================================================================
st.set_page_config(page_title="Reportes OEE - FAMMA & FUMISCOR", layout="wide")
st.title("📊 Generador de Reportes OEE - FAMMA & FUMISCOR")
st.markdown("Seleccione la empresa y el período para consultar la base de datos y descargar los reportes PDF.")

if 'pdfs_generados' not in st.session_state:
    st.session_state.pdfs_generados = {}

# ==============================================================================
# 0. MAPEOS, CONSTANTES Y HELPERS GLOBALES
# ==============================================================================
OBJETIVOS = {'OEE': 75.0, 'PERFORMANCE': 90.0, 'DISPONIBILIDAD': 88.0, 'CALIDAD': 95.0}
MESES_NOMBRES = {1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'}

COLOR_MACRO = {
    'Gestión': '#5DADE2', 'Mantenimiento': '#CD6155', 'Matricería': '#F5B041',
    'Dispositivo': '#C06C84', 'Logística': '#F4D03F', 'Calidad': '#48C9B0',
    'Tecnología': '#AF7AC5'
}

FAMMA_MAQUINAS_MAP = {
    "LINEA 1.2": "LINEA 1.2", "LINEA 1.4": "LINEA 1.4", "LINEA 1.5": "LINEA 1.5",
    "LINEA 2": "LINEA 2", "LINEA 3": "LINEA 3", "LINEA 4": "LINEA 4",
    "Cell 1 Famma": "CELDAS", "Cell 2 Famma": "CELDAS", "Cell 3 Famma": "CELDAS",
    "Cell 4 Famma": "CELDAS", "Cell 5 Famma": "CELDAS", "Cell 6 Famma": "CELDAS",
    "Cell 7 Famma": "CELDAS", "Cell 8 Famma": "CELDAS", "Cell 9 Famma": "CELDAS",
    "Cell 10 Famma": "CELDAS", "Cell 11 Famma": "CELDAS", "Cell 12 Famma": "CELDAS",
    "Cell 13 Famma": "CELDAS", "Cell 14 Famma": "CELDAS", "Cell 15A Famma": "CELDAS",
    "Cell 15B Famma": "CELDAS", "Cell 16 Famma": "CELDAS", "Cell 17 Famma": "CELDAS",
    "PRP 1": "PRP", "PRP 2": "PRP", "PRP 3": "PRP", "PRP 4": "PRP", "PRP 5": "PRP", "PRP 6": "PRP",
    "MIG 1": "MIG", "MIG 2": "MIG"
}
FAMMA_MAPA_LIMPIO = {str(k).strip().upper(): str(v).strip().upper() for k, v in FAMMA_MAQUINAS_MAP.items()}

FAMMA_TARGET_TO_DB_LINE = {
    'LINEA 1.2': 'L1.2', 'LINEA 1.4': 'L1.4', 'LINEA 1.5': 'L1',
    'LINEA 2': 'L2', 'LINEA 3': 'L3', 'LINEA 4': 'L4',
    'CELDAS': 'B', 'PRP': 'PRP', 'MIG': 'MIG'
}

FUMISCOR_LINE_NAMES = {
    'BAL': 'BALANCÍN (BAL)', 'HID': 'PRENSA HIDRÁULICA (HID)', 'MEC': 'PRENSA MECÁNICA (MEC)',
    'PROG': 'PRENSA PROGRESIVA (PROG)', 'PPG': 'PRENSAS PROG. GRANDES (PPG)', 'CL': 'CORTADORA LÁSER (CL)',
    'ROB': 'ROBOT (ROB)', 'PRP': 'PRP', 'DOB': 'DOBLADORA (DOB)',
    'SOC': 'COLGANTE (SOC)', 'SOM': 'SOLDADORA MANUAL (SOM)', '1': 'CELDAS NUEVAS'
}

def color_condicional(valor, objetivo):
    return '#2ECC71' if valor >= objetivo else '#E74C3C'

def format_kpi(val):
    if pd.isnull(val): return 0.0
    if val <= 1.5 and val > 0: val *= 100
    return round(val, 1)

def conectar_desde_secreto(nombre_secreto):
    if nombre_secreto not in st.secrets:
        raise ValueError(f"Falta configurar el secreto '{nombre_secreto}' en Streamlit Cloud.")
    raw = str(st.secrets[nombre_secreto]).strip()
    if raw.startswith("{"):
        cfg = json.loads(raw)
        return pymssql.connect(
            server=cfg.get("server") or cfg.get("SERVER"),
            database=cfg.get("database") or cfg.get("DATABASE"),
            user=cfg.get("user") or cfg.get("USERNAME"),
            password=cfg.get("password") or cfg.get("PASSWORD")
        )
    if "://" in raw:
        m = re.match(r".*://([^:]+):([^@]+)@([^/]+)/(.+)", raw)
        user, pwd, host, db = m.groups()
        return pymssql.connect(server=host.replace(",", ":"), user=user, password=pwd, database=db)
    partes = {}
    for item in raw.split(";"):
        if "=" in item:
            k, v = item.split("=", 1)
            partes[k.strip().lower()] = v.strip()
    server = (partes.get("server") or partes.get("data source") or "").replace(",", ":")
    database = partes.get("database") or partes.get("initial catalog")
    user = partes.get("uid") or partes.get("user id") or partes.get("user")
    password = partes.get("pwd") or partes.get("password")
    return pymssql.connect(server=server, user=user, password=password, database=database)

def ejecutar_sql(cursor, query):
    cursor.execute(query)
    cols = [desc[0] for desc in cursor.description]
    return pd.DataFrame(cursor.fetchall(), columns=cols)

def parse_event_tree(row):
    niveles = [str(row.get(f'Nivel Evento {i}', '')).strip().upper() for i in range(1, 7)]
    validos = [n for n in niveles if n and n not in ['NONE', 'NAN', 'NULL', '']]
    if not validos: return 'Falla/Gestión', 'Sin Clasificar', 'Sin detalle'
    texto = " > ".join(validos)
    estado = 'Falla/Gestión'
    if any(x in texto for x in ['BAÑO', 'BANO', 'REFRIGERIO', 'DESCANSO']): estado = 'Descanso'
    elif any(x in texto for x in ['PARADA PROGRAMADA', 'SMED', 'PROYECTO']): estado = 'Parada Programada'
    elif 'PRODUCCION' in validos[0]: estado = 'Producción'
    macro = None
    areas = {
        'MANTENIMIENTO': 'Mantenimiento', 'MATRICERIA': 'Matricería', 'DISPOSITIVO': 'Dispositivo',
        'GESTION': 'Gestión', 'LOGISTICA': 'Logística', 'CALIDAD': 'Calidad', 'TECNOLOGIA': 'Tecnología'
    }
    for n in reversed(validos):
        for k, v in areas.items():
            if k in n: macro = v; break
        if macro: break
    if not macro:
        macro = validos[1].title() if len(validos) >= 2 else validos[0].title()
    return estado, macro, validos[-1]

def extraer_datos_empresa(nombre_secreto, año, mes):
    conn = conectar_desde_secreto(nombre_secreto)
    cursor = conn.cursor()
    ultimo_dia = calendar.monthrange(año, mes)[1]
    fin_str = f"{año}-{mes:02d}-{ultimo_dia:02d} 23:59:59"

    q_m03 = f"""
        SELECT 
            p.Month, p.FactoryId,
            UPPER(LTRIM(RTRIM(CAST(p.Line AS VARCHAR(50))))) as Linea_Real,
            c.Name as Máquina, 
            CAST(p.Performance AS FLOAT) as Perf_Num, 
            CAST(p.Availability AS FLOAT) as Disp_Num, 
            CAST(p.Quality AS FLOAT) as Cal_Num, 
            CAST(p.Oee AS FLOAT) as OEE_Num, 
            COALESCE(p.ProductiveTime, 0) as T_Operativo, 
            (COALESCE(p.ProductiveTime, 0) + COALESCE(p.DownTime, 0)) as T_Planificado 
        FROM PROD_M_03 p JOIN CELL c ON p.CellId = c.CellId 
        WHERE p.Year = {año} AND p.Month <= {mes}
    """
    df_m03 = ejecutar_sql(cursor, q_m03)
    for col in ['Perf_Num', 'Disp_Num', 'Cal_Num', 'OEE_Num', 'T_Operativo', 'T_Planificado']:
        if col in df_m03.columns:
            df_m03[col] = pd.to_numeric(df_m03[col], errors='coerce').fillna(0.0)

    q_m05 = f"""
        SELECT 
            Month, UPPER(LTRIM(RTRIM(CAST(Factory AS VARCHAR(50))))) as Grupo, 
            CAST(Oee AS FLOAT) as OEE_Num, CAST(Performance AS FLOAT) as Perf_Num, 
            CAST(Availability AS FLOAT) as Disp_Num, CAST(Quality AS FLOAT) as Cal_Num 
        FROM PROD_M_05 
        WHERE Year = {año} AND Month <= {mes}
    """
    df_m05 = ejecutar_sql(cursor, q_m05)
    for col in ['OEE_Num', 'Perf_Num', 'Disp_Num', 'Cal_Num']:
        if col in df_m05.columns:
            df_m05[col] = pd.to_numeric(df_m05[col], errors='coerce').fillna(0.0)

    q_m04 = f"""
        SELECT 
            Month, UPPER(LTRIM(RTRIM(CAST(Line AS VARCHAR(50))))) as Grupo, 
            CAST(Oee AS FLOAT) as OEE_Num, CAST(Performance AS FLOAT) as Perf_Num, 
            CAST(Availability AS FLOAT) as Disp_Num, CAST(Quality AS FLOAT) as Cal_Num 
        FROM PROD_M_04 
        WHERE Year = {año} AND Month <= {mes}
    """
    df_m04 = ejecutar_sql(cursor, q_m04)
    for col in ['OEE_Num', 'Perf_Num', 'Disp_Num', 'Cal_Num']:
        if col in df_m04.columns:
            df_m04[col] = pd.to_numeric(df_m04[col], errors='coerce').fillna(0.0)

    q_event = f"""
        SELECT 
            e.FactoryId,
            UPPER(LTRIM(RTRIM(CAST(e.Line AS VARCHAR(50))))) as Linea_Real,
            c.Name as Máquina, e.Interval as [Tiempo (Min)], 
            t1.Name as [Nivel Evento 1], t2.Name as [Nivel Evento 2], t3.Name as [Nivel Evento 3], 
            t4.Name as [Nivel Evento 4], t5.Name as [Nivel Evento 5], t6.Name as [Nivel Evento 6]
        FROM EVENT_01 e JOIN CELL c ON e.CellId = c.CellId 
        LEFT JOIN EVENTTYPE t1 ON e.EventTypeLevel1 = t1.EventTypeId 
        LEFT JOIN EVENTTYPE t2 ON e.EventTypeLevel2 = t2.EventTypeId 
        LEFT JOIN EVENTTYPE t3 ON e.EventTypeLevel3 = t3.EventTypeId 
        LEFT JOIN EVENTTYPE t4 ON e.EventTypeLevel4 = t4.EventTypeId 
        LEFT JOIN EVENTTYPE t5 ON e.EventTypeLevel5 = t5.EventTypeId 
        LEFT JOIN EVENTTYPE t6 ON e.EventTypeLevel6 = t6.EventTypeId 
        WHERE COALESCE(e.Date, e.Started) BETWEEN '{año}-{mes:02d}-01 00:00:00' AND '{fin_str}'
    """
    df_raw = ejecutar_sql(cursor, q_event)
    conn.close()

    if not df_raw.empty:
        cols_texto = [c for c in df_raw.columns if 'Nivel' in c or c in ['Máquina', 'Linea_Real']]
        df_raw[cols_texto] = df_raw[cols_texto].fillna('')
        if 'Tiempo (Min)' in df_raw.columns:
            df_raw['Tiempo (Min)'] = pd.to_numeric(df_raw['Tiempo (Min)'], errors='coerce').fillna(0.0)
        df_raw[['Estado_Global', 'Categoria_Macro', 'Detalle_Final']] = df_raw.apply(lambda r: pd.Series(parse_event_tree(r)), axis=1)

    return df_m03, df_m04, df_m05, df_raw

# ==============================================================================
# 1. FUNCIÓN DE DIBUJO DE PÁGINA PDF
# ==============================================================================
def dibujar_pagina_pdf(pdf, empresa, titulo_planta, color_encabezado, valores_hist, df_r_t, año_filtro, mes_filtro):
    v_oee = valores_hist['OEE'].get(mes_filtro, 0.0)
    v_perf = valores_hist['PERFORMANCE'].get(mes_filtro, 0.0)
    v_disp = valores_hist['DISPONIBILIDAD'].get(mes_filtro, 0.0)
    v_cal = valores_hist['CALIDAD'].get(mes_filtro, 0.0)

    def preparar_grafico(col):
        dict_vals = valores_hist[col]
        meses_ord = sorted(dict_vals.keys())
        vals_año = [dict_vals[m] for m in meses_ord if dict_vals[m] > 0]
        ytd_v = sum(vals_año) / len(vals_año) if vals_año else 0.0
        etiq = [MESES_NOMBRES.get(m, str(m)) for m in meses_ord] + ['Acum.']
        datos = [dict_vals[m] for m in meses_ord] + [round(ytd_v, 1)]
        return etiq, datos

    etiquetas_m, oee_data = preparar_grafico('OEE')
    _, perf_data = preparar_grafico('PERFORMANCE')
    _, disp_data = preparar_grafico('DISPONIBILIDAD')
    _, calidad_data = preparar_grafico('CALIDAD')

    df_f = df_r_t[df_r_t['Estado_Global'] == 'Falla/Gestión'] if not df_r_t.empty else pd.DataFrame()
    if not df_f.empty and df_f['Tiempo (Min)'].sum() > 0:
        top5 = df_f.groupby('Detalle_Final', as_index=False)['Tiempo (Min)'].sum().sort_values(by='Tiempo (Min)', ascending=False).head(5)
        suma_top5 = top5['Tiempo (Min)'].sum()
        top5['%'] = (top5['Tiempo (Min)'] / suma_top5 * 100).round(1).astype(str) + '%'
        top5['MIN'] = top5['Tiempo (Min)'].round(0).astype(int)
        df_defectos = top5[['Detalle_Final', 'MIN', '%']].rename(columns={'Detalle_Final': 'FALLO / DEFECTO'})
        df_f_top5 = df_f[df_f['Detalle_Final'].isin(top5['Detalle_Final'])]
        df_macro = df_f_top5.groupby('Categoria_Macro', as_index=False)['Tiempo (Min)'].sum()
        tt_macro = df_macro['Tiempo (Min)'].sum()
        df_macro['%'] = (df_macro['Tiempo (Min)'] / tt_macro) * 100
        df_macro['Horas'] = df_macro['Tiempo (Min)'] / 60.0
        df_macro = df_macro.sort_values(by='Tiempo (Min)', ascending=False)
    else:
        df_defectos = pd.DataFrame(columns=['FALLO / DEFECTO', 'MIN', '%'])
        df_macro = pd.DataFrame(columns=['Categoria_Macro', 'Tiempo (Min)', '%', 'Horas'])

    fig = plt.figure(figsize=(16, 10), facecolor='#F4F6F9')
    gs = gridspec.GridSpec(4, 4, height_ratios=[1, 2.5, 2.5, 2], hspace=0.32, wspace=0.28)
    fig.suptitle(f'PERIODO: {mes_filtro}/{año_filtro}   |   {titulo_planta}   |   EMPRESA: {empresa}', fontsize=15, fontweight='bold', color='white', backgroundcolor=color_encabezado, y=0.96)

    kpi_axes = [fig.add_subplot(gs[0, i]) for i in range(4)]
    for ax, name, val in zip(kpi_axes, ['OEE', 'PERFORMANCE', 'DISPONIBILIDAD', 'CALIDAD'], [v_oee, v_perf, v_disp, v_cal]):
        ax.axis('off')
        ax.add_patch(Rectangle((0, 0), 1, 1, facecolor=color_condicional(val, OBJETIVOS[name]), transform=ax.transAxes))
        ax.text(0.06, 0.70, name, color='white', fontsize=11, fontweight='bold', transform=ax.transAxes)
        ax.text(0.50, 0.28, f"{val:.1f}%", color='white', fontsize=26, fontweight='bold', ha='center', transform=ax.transAxes)

    def plot_bar_trend(ax, nombre_kpi, data, objetivo):
        colors = [color_condicional(v, objetivo) for v in data]
        bars = ax.bar(etiquetas_m, data, color=colors, width=0.6)
        ax.axhline(y=objetivo, color='#2C3E50', linestyle='--', linewidth=1)
        ax.text(len(etiquetas_m) - 0.4, objetivo + 2, f"Obj: {objetivo:.0f}%", fontsize=8.5, ha='left', fontweight='bold')
        ax.axvline(x=len(etiquetas_m)-1.5, color='#7F8C8D', linestyle=':', linewidth=1.2)
        for bar in bars:
            yval = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2, yval + 1.2, f"{yval:.1f}%", ha='center', va='bottom', fontsize=8.5)
        ax.set_title(f"{nombre_kpi} (%)", loc='left', fontsize=11, fontweight='bold', color='#2C3E50')
        ax.set_ylim(0, max(max(data), objetivo) + 18)
        ax.set_xlim(-0.6, len(etiquetas_m) + 0.8)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.yaxis.set_visible(False)

    plot_bar_trend(fig.add_subplot(gs[1, 0:2]), 'OEE', oee_data, OBJETIVOS['OEE'])
    plot_bar_trend(fig.add_subplot(gs[1, 2:4]), 'PERFORMANCE', perf_data, OBJETIVOS['PERFORMANCE'])
    plot_bar_trend(fig.add_subplot(gs[2, 0:2]), 'DISPONIBILIDAD', disp_data, OBJETIVOS['DISPONIBILIDAD'])
    plot_bar_trend(fig.add_subplot(gs[2, 2:4]), 'CALIDAD', calidad_data, OBJETIVOS['CALIDAD'])

    ax_table = fig.add_subplot(gs[3, 0:2])
    ax_table.axis('off')
    if not df_defectos.empty:
        df_defectos_display = df_defectos.copy()
        df_defectos_display['FALLO / DEFECTO'] = df_defectos_display['FALLO / DEFECTO'].astype(str).apply(lambda x: (x[:57] + '...') if len(x) > 60 else x)
        table = ax_table.table(cellText=df_defectos_display.values, colLabels=df_defectos_display.columns, colWidths=[0.72, 0.14, 0.14], cellLoc='center', loc='center', colColours=[color_encabezado]*3)
        table.auto_set_font_size(False)
        table.set_fontsize(8.0)
        table.scale(1, 1.6)
        for (r, col_idx), cell in table.get_celld().items():
            cell.set_edgecolor('#D0D3D4')
            if r == 0: cell.get_text().set_color('white'); cell.set_text_props(weight='bold', fontsize=8.5)
            else:
                cell.set_facecolor('white')
                if col_idx == 0: cell.get_text().set_ha('left')

    ax_dist = fig.add_subplot(gs[3, 2:4])
    ax_dist.axis('off')
    if not df_macro.empty and df_macro['Tiempo (Min)'].sum() > 0:
        left_offset = 0
        etiquetas_leyenda = []
        for idx, row in df_macro.iterrows():
            pct = row['%']
            horas = row['Horas']
            etiquetas_leyenda.append(f"{row['Categoria_Macro']} ({horas:.1f}h | {pct:.1f}%)")
            c = COLOR_MACRO.get(row['Categoria_Macro'], '#95A5A6')
            ax_dist.barh(0, pct, left=left_offset, color=c, height=0.45)
            left_offset += pct
        ax_dist.set_xlim(0, 100)
        ax_dist.legend(etiquetas_leyenda, loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=min(len(etiquetas_leyenda), 3), frameon=False, fontsize=8.5)

    pdf.savefig(fig, bbox_inches='tight', pad_inches=0.4)
    plt.close(fig)

# ==============================================================================
# 2. GENERADORES POR EMPRESA
# ==============================================================================
def generar_reportes_famma(año_filtro, mes_filtro):
    df_m03, df_m04, df_m05, df_raw = extraer_datos_empresa("CONN_FAMMA", año_filtro, mes_filtro)
    if not df_m03.empty:
        df_m03['Grupo'] = df_m03['Máquina'].astype(str).str.strip().str.upper().map(FAMMA_MAPA_LIMPIO).fillna('OTRO')
    if not df_raw.empty:
        df_raw['Grupo'] = df_raw['Máquina'].astype(str).str.strip().str.upper().map(FAMMA_MAPA_LIMPIO).fillna('OTRO')

    config_famma = [
        {"key": "FAMMA_EST", "label": "📥 FAMMA - Estampado", "area": "ESTAMPADO", "db_key": "EST", "grupos": ['LINEA 1.2', 'LINEA 1.4', 'LINEA 1.5', 'LINEA 2', 'LINEA 3', 'LINEA 4'], "color": '#0F4C81', "archivo": f"FAMMA_OEE_ESTAMPADO_{año_filtro}_{mes_filtro}.pdf"},
        {"key": "FAMMA_SOL", "label": "📥 FAMMA - Soldadura", "area": "SOLDADURA", "db_key": "SOL", "grupos": ['CELDAS', 'PRP', 'MIG'], "color": '#D35400', "archivo": f"FAMMA_OEE_SOLDADURA_{año_filtro}_{mes_filtro}.pdf"}
    ]

    resultados = {}
    for cfg in config_famma:
        area, db_key, grupos_area, color_enc = cfg["area"], cfg["db_key"], cfg["grupos"], cfg["color"]
        lineas_activas = set()
        if not df_m04.empty:
            lineas_activas.update([g for g in grupos_area if FAMMA_TARGET_TO_DB_LINE.get(g, g) in df_m04['Grupo'].unique()])
        if not df_m03.empty:
            lineas_activas.update([g for g in grupos_area if g in df_m03['Grupo'].unique()])

        paginas = ['GENERAL'] + [g for g in grupos_area if g in lineas_activas]
        pdf_buffer = io.BytesIO()

        with PdfPages(pdf_buffer) as pdf:
            for target in paginas:
                titulo_planta = f"PLANTA {area} - {target}"
                valores_hist = {'OEE': {}, 'PERFORMANCE': {}, 'DISPONIBILIDAD': {}, 'CALIDAD': {}}
                df_r_t = df_raw[df_raw['Grupo'].isin(grupos_area)] if (target == 'GENERAL' and not df_raw.empty) else (df_raw[df_raw['Grupo'] == target] if not df_raw.empty else pd.DataFrame())

                for m in range(1, mes_filtro + 1):
                    val_oee, val_perf, val_disp, val_cal = 0.0, 0.0, 0.0, 0.0
                    found_literal = False

                    if target == 'GENERAL':
                        df_lit = df_m05[(df_m05['Month'] == m) & (df_m05['Grupo'].str.startswith(db_key, na=False))]
                    else:
                        db_target = FAMMA_TARGET_TO_DB_LINE.get(target, target)
                        df_lit = df_m04[(df_m04['Month'] == m) & (df_m04['Grupo'] == db_target)]

                    if not df_lit.empty and (df_lit.iloc[0]['OEE_Num'] > 0 or df_lit.iloc[0]['Perf_Num'] > 0):
                        row = df_lit.iloc[0]
                        val_oee, val_perf, val_disp, val_cal = row['OEE_Num'], row['Perf_Num'], row['Disp_Num'], row['Cal_Num']
                        found_literal = True

                    if not found_literal and not df_m03.empty:
                        if target == 'GENERAL':
                            filtro = 'LINEA' if area == 'ESTAMPADO' else 'CELL|PRP|MIG'
                            df_pond = df_m03[(df_m03['Month'] == m) & (df_m03['Máquina'].str.contains(filtro, case=False, na=False))]
                        else:
                            df_pond = df_m03[(df_m03['Month'] == m) & (df_m03['Grupo'] == target)]

                        if not df_pond.empty:
                            tp_m, to_m = df_pond['T_Planificado'].sum(), df_pond['T_Operativo'].sum()
                            val_oee = (df_pond['OEE_Num'] * df_pond['T_Planificado']).sum() / tp_m if tp_m > 0 else 0
                            val_perf = (df_pond['Perf_Num'] * df_pond['T_Operativo']).sum() / to_m if to_m > 0 else 0
                            val_disp = (df_pond['Disp_Num'] * df_pond['T_Planificado']).sum() / tp_m if tp_m > 0 else 0
                            val_cal = (df_pond['Cal_Num'] * df_pond['T_Operativo']).sum() / to_m if to_m > 0 else 0

                    valores_hist['OEE'][m] = format_kpi(val_oee)
                    valores_hist['PERFORMANCE'][m] = format_kpi(val_perf)
                    valores_hist['DISPONIBILIDAD'][m] = format_kpi(val_disp)
                    valores_hist['CALIDAD'][m] = format_kpi(val_cal)

                if target != 'GENERAL' and all(valores_hist[k].get(mes_filtro, 0.0) == 0.0 for k in valores_hist):
                    continue

                dibujar_pagina_pdf(pdf, "FAMMA", titulo_planta, color_enc, valores_hist, df_r_t, año_filtro, mes_filtro)

        pdf_buffer.seek(0)
        resultados[cfg["key"]] = {"label": cfg["label"], "archivo": cfg["archivo"], "bytes": pdf_buffer.getvalue()}

    return resultados

def generar_reportes_fumiscor(año_filtro, mes_filtro):
    df_m03, df_m04, df_m05, df_raw = extraer_datos_empresa("CONN_FUMISCOR", año_filtro, mes_filtro)

    config_fumiscor = [
        {
            "key": "FUM_EST",
            "label": "📥 FUMISCOR - Estampado",
            "archivo": f"FUMISCOR_OEE_ESTAMPADO_{año_filtro}_{mes_filtro}.pdf",
            "secciones": [
                {"area": "ESTAMPADO", "db_factory": "1", "factory_id": 1, "lineas": ['BAL', 'HID', 'MEC', 'PROG', 'PPG', 'CL'], "color": '#0F4C81'}
            ]
        },
        {
            "key": "FUM_SOL",
            "label": "📥 FUMISCOR - Soldadura (+ Nave Nueva)",
            "archivo": f"FUMISCOR_OEE_SOLDADURA_{año_filtro}_{mes_filtro}.pdf",
            "secciones": [
                {"area": "SOLDADURA", "db_factory": "2", "factory_id": 2, "lineas": ['ROB', 'PRP', 'DOB', 'SOC', 'SOM'], "color": '#D35400'},
                {"area": "SOLDADURA NAVE NUEVA", "db_factory": "4", "factory_id": 218, "lineas": ['1'], "color": '#B94A00'}
            ]
        }
    ]

    resultados = {}
    for rep in config_fumiscor:
        pdf_buffer = io.BytesIO()
        with PdfPages(pdf_buffer) as pdf:
            for sec in rep["secciones"]:
                area, db_factory, factory_id, lineas_area, color_enc = sec["area"], sec["db_factory"], sec["factory_id"], sec["lineas"], sec["color"]
                lineas_activas = [l for l in lineas_area if (not df_m04.empty and l in df_m04['Grupo'].unique()) or (not df_m03.empty and l in df_m03[df_m03['FactoryId'] == factory_id]['Linea_Real'].unique())]
                paginas = ['GENERAL'] + lineas_activas

                for target in paginas:
                    nombre_target = 'GENERAL' if target == 'GENERAL' else FUMISCOR_LINE_NAMES.get(target, target)
                    titulo_planta = f"PLANTA {area} - {nombre_target}"
                    valores_hist = {'OEE': {}, 'PERFORMANCE': {}, 'DISPONIBILIDAD': {}, 'CALIDAD': {}}

                    if not df_raw.empty:
                        df_r_t = df_raw[df_raw['FactoryId'] == factory_id] if target == 'GENERAL' else df_raw[(df_raw['FactoryId'] == factory_id) & (df_raw['Linea_Real'] == target)]
                    else:
                        df_r_t = pd.DataFrame()

                    for m in range(1, mes_filtro + 1):
                        val_oee, val_perf, val_disp, val_cal = 0.0, 0.0, 0.0, 0.0
                        found_literal = False

                        if target == 'GENERAL':
                            df_lit = df_m05[(df_m05['Month'] == m) & (df_m05['Grupo'] == db_factory)]
                        else:
                            df_lit = df_m04[(df_m04['Month'] == m) & (df_m04['Grupo'] == target)]

                        if not df_lit.empty and (df_lit.iloc[0]['OEE_Num'] > 0 or df_lit.iloc[0]['Perf_Num'] > 0):
                            row = df_lit.iloc[0]
                            val_oee, val_perf, val_disp, val_cal = row['OEE_Num'], row['Perf_Num'], row['Disp_Num'], row['Cal_Num']
                            found_literal = True

                        if not found_literal and not df_m03.empty:
                            if target == 'GENERAL':
                                df_pond = df_m03[(df_m03['Month'] == m) & (df_m03['FactoryId'] == factory_id)]
                            else:
                                df_pond = df_m03[(df_m03['Month'] == m) & (df_m03['FactoryId'] == factory_id) & (df_m03['Linea_Real'] == target)]

                            if not df_pond.empty:
                                tp_m, to_m = df_pond['T_Planificado'].sum(), df_pond['T_Operativo'].sum()
                                val_oee = (df_pond['OEE_Num'] * df_pond['T_Planificado']).sum() / tp_m if tp_m > 0 else 0
                                val_perf = (df_pond['Perf_Num'] * df_pond['T_Operativo']).sum() / to_m if to_m > 0 else 0
                                val_disp = (df_pond['Disp_Num'] * df_pond['T_Planificado']).sum() / tp_m if tp_m > 0 else 0
                                val_cal = (df_pond['Cal_Num'] * df_pond['T_Operativo']).sum() / to_m if to_m > 0 else 0

                        valores_hist['OEE'][m] = format_kpi(val_oee)
                        valores_hist['PERFORMANCE'][m] = format_kpi(val_perf)
                        valores_hist['DISPONIBILIDAD'][m] = format_kpi(val_disp)
                        valores_hist['CALIDAD'][m] = format_kpi(val_cal)

                    if target != 'GENERAL' and all(valores_hist[k].get(mes_filtro, 0.0) == 0.0 for k in valores_hist):
                        continue

                    dibujar_pagina_pdf(pdf, "FUMISCOR", titulo_planta, color_enc, valores_hist, df_r_t, año_filtro, mes_filtro)

        pdf_buffer.seek(0)
        resultados[rep["key"]] = {"label": rep["label"], "archivo": rep["archivo"], "bytes": pdf_buffer.getvalue()}

    return resultados

# ==============================================================================
# 3. INTERFAZ DE STREAMLIT
# ==============================================================================
col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    empresa_sel = st.selectbox("Empresa", ["Ambas (FAMMA + FUMISCOR)", "FAMMA", "FUMISCOR"])

with col2:
    mes_sel = st.selectbox("Mes", range(1, 13), index=7)  # Por defecto Agosto (8)

with col3:
    año_sel = st.selectbox("Año", [2026, 2025, 2024])

st.write("---")

if st.button("🚀 Generar Reportes PDF", use_container_width=True):
    st.session_state.pdfs_generados = {}
    try:
        with st.spinner("Conectando a las bases de datos y generando los PDFs..."):
            if empresa_sel in ["Ambas (FAMMA + FUMISCOR)", "FAMMA"]:
                st.session_state.pdfs_generados.update(generar_reportes_famma(año_sel, mes_sel))
            if empresa_sel in ["Ambas (FAMMA + FUMISCOR)", "FUMISCOR"]:
                st.session_state.pdfs_generados.update(generar_reportes_fumiscor(año_sel, mes_sel))
        st.success("✅ ¡Reportes generados con éxito! Descargá los archivos abajo:")
    except Exception as e:
        st.error(f"❌ Error durante la generación: {e}")

if st.session_state.pdfs_generados:
    cols = st.columns(2)
    for idx, (key, data) in enumerate(st.session_state.pdfs_generados.items()):
        with cols[idx % 2]:
            st.download_button(
                label=data["label"],
                data=data["bytes"],
                file_name=data["archivo"],
                mime="application/pdf",
                use_container_width=True
            )
