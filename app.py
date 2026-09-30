from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import COLUMN_CANDIDATES, suggest_column, prepare_data, pic_performance

st.set_page_config(page_title="Procurement PR Control Tower", page_icon="📦", layout="wide")

APP_DIR = Path(__file__).parent
SAMPLE_FILE = APP_DIR / "sample_purchase_report.csv"


def load_file(uploaded):
    if uploaded is None:
        return pd.read_csv(SAMPLE_FILE)
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded)
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(uploaded)
    raise ValueError("Format file tidak didukung.")


def format_int(v):
    return f"{int(v):,}".replace(",", ".") if pd.notna(v) else "0"


st.title("📦 Procurement PR Control Tower")
st.caption("Monitor aging PR, workload PIC, SLA, backlog, dan kebutuhan tindak lanjut dari export ERP.")

with st.sidebar:
    st.header("Data & Settings")
    uploaded = st.file_uploader("Upload Purchase Report ERP", type=["xlsx", "xls", "csv"])
    use_sample = uploaded is None
    if use_sample:
        st.info("Belum ada file di-upload. Dashboard menggunakan data demo.")

    default_sla = st.number_input("Default SLA (hari)", min_value=1, max_value=90, value=7, step=1)
    completed_text = st.text_area(
        "Keyword status selesai",
        value="closed, complete, completed, po created, selesai",
        help="Pisahkan dengan koma. PO number / PO date juga otomatis dianggap selesai pada item tersebut.",
    )
    completed_keywords = [x.strip() for x in completed_text.split(",") if x.strip()]

try:
    raw = load_file(uploaded)
except Exception as e:
    st.error(f"Gagal membaca file: {e}")
    st.stop()

with st.sidebar.expander("Mapping Kolom ERP", expanded=False):
    st.caption("Aplikasi mencoba memilih otomatis. Ubah jika nama kolom ERP berbeda.")
    options = ["— Tidak tersedia —"] + list(raw.columns)
    mapping = {}
    labels = {
        "pr_number": "No. PR *",
        "pr_date": "Tanggal PR *",
        "pic": "PIC *",
        "status": "Status *",
        "po_date": "Tanggal PO",
        "po_number": "No. PO",
        "item_code": "Kode Item",
        "item_name": "Nama Item / Uraian",
        "requester": "Diminta Oleh",
        "supplier": "Supplier / Vendor",
        "qty": "Jumlah / Qty",
        "value": "Total / Nilai",
    }
    for key in COLUMN_CANDIDATES:
        suggested = suggest_column(raw.columns, key)
        idx = options.index(suggested) if suggested in options else 0
        chosen = st.selectbox(labels[key], options, index=idx, key=f"map_{key}")
        mapping[key] = None if chosen == "— Tidak tersedia —" else chosen

try:
    item_df, pr_df = prepare_data(raw, mapping, sla_days=default_sla, completed_keywords=completed_keywords)
except Exception as e:
    st.error(str(e))
    st.info("Buka sidebar → Mapping Kolom ERP, lalu pastikan No. PR, Tanggal PR, PIC, dan Status sudah benar.")
    st.stop()

perf_df = pic_performance(pr_df)

# Global filters
st.subheader("Filter")
f1, f2, f3 = st.columns([1, 1, 1])
pic_values = sorted([x for x in pr_df["pic"].dropna().astype(str).unique() if x])
status_values = ["Open", "Partial", "Completed"]
sla_values = sorted(pr_df["sla_status"].dropna().unique().tolist())
with f1:
    selected_pics = st.multiselect("PIC", pic_values)
with f2:
    selected_status = st.multiselect("Status PR", status_values)
with f3:
    selected_sla = st.multiselect("Status SLA", sla_values)

filtered = pr_df.copy()
if selected_pics:
    filtered = filtered[filtered["pic"].isin(selected_pics)]
if selected_status:
    filtered = filtered[filtered["pr_status"].isin(selected_status)]
if selected_sla:
    filtered = filtered[filtered["sla_status"].isin(selected_sla)]

# Core KPIs
total_pr = filtered["pr_number"].nunique()
completed_pr = int(filtered["is_completed"].sum())
open_pr = total_pr - completed_pr
overdue_pr = int((filtered["sla_status"] == "Overdue").sum())
completed_sla = filtered[filtered["is_completed"] & filtered["lead_time_days"].notna()]
sla_met = int((completed_sla["lead_time_days"] <= completed_sla["sla_days"]).sum())
sla_compliance = (sla_met / len(completed_sla) * 100) if len(completed_sla) else 0
avg_open_age = filtered.loc[~filtered["is_completed"], "open_age_days"].mean()

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total PR", format_int(total_pr))
k2.metric("Open PR", format_int(open_pr))
k3.metric("Completed", format_int(completed_pr))
k4.metric("Overdue", format_int(overdue_pr))
k5.metric("SLA Compliance", f"{sla_compliance:.1f}%")

st.caption(f"Rata-rata aging PR terbuka: **{0 if pd.isna(avg_open_age) else avg_open_age:.1f} hari** · Data unik dihitung berdasarkan nomor PR.")

overview_tab, monitor_tab, pic_tab, sla_tab, data_tab = st.tabs([
    "🏠 Overview", "📋 PR Monitoring", "👥 PIC Performance", "⏱️ Aging & SLA", "🧩 Data & Mapping"
])

with overview_tab:
    c1, c2 = st.columns(2)
    with c1:
        status_counts = filtered["pr_status"].value_counts().rename_axis("Status").reset_index(name="PR")
        fig = px.bar(status_counts, x="Status", y="PR", title="PR Status")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        open_only = filtered[~filtered["is_completed"]]
        aging_counts = open_only["aging_bucket"].value_counts(sort=False).rename_axis("Aging").reset_index(name="PR")
        fig = px.bar(aging_counts, x="Aging", y="PR", title="Aging PR Terbuka")
        st.plotly_chart(fig, use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        workload = filtered[~filtered["is_completed"]].groupby("pic")["pr_number"].nunique().reset_index(name="Open PR")
        workload = workload.sort_values("Open PR", ascending=True)
        fig = px.bar(workload, x="Open PR", y="pic", orientation="h", title="Open Workload per PIC")
        st.plotly_chart(fig, use_container_width=True)
    with c4:
        monthly = filtered.dropna(subset=["pr_date"]).copy()
        monthly["Month"] = monthly["pr_date"].dt.to_period("M").astype(str)
        created = monthly.groupby("Month")["pr_number"].nunique().rename("Created")
        completed_m = monthly[monthly["is_completed"] & monthly["end_date"].notna()].copy()
        completed_m["Month"] = completed_m["end_date"].dt.to_period("M").astype(str)
        completed = completed_m.groupby("Month")["pr_number"].nunique().rename("Completed")
        trend = pd.concat([created, completed], axis=1).fillna(0).reset_index()
        trend_long = trend.melt("Month", var_name="Metric", value_name="PR")
        fig = px.line(trend_long, x="Month", y="PR", color="Metric", markers=True, title="PR Created vs Completed")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("### 🚨 Needs Attention")
    attention = filtered[~filtered["is_completed"]].copy()
    attention = attention.sort_values(["overdue_days", "open_age_days"], ascending=False).head(15)
    st.dataframe(
        attention[["pr_number", "pr_date", "pic", "pr_status", "open_age_days", "sla_days", "overdue_days", "requester", "supplier"]]
        .rename(columns={
            "pr_number": "No PR", "pr_date": "Tanggal PR", "pic": "PIC", "pr_status": "Status",
            "open_age_days": "Aging", "sla_days": "SLA", "overdue_days": "Overdue",
            "requester": "Diminta Oleh", "supplier": "Supplier"
        }),
        use_container_width=True,
        hide_index=True,
    )

with monitor_tab:
    st.markdown("### PR Monitoring")
    min_age, max_age = int(filtered["open_age_days"].min() if not filtered.empty else 0), int(filtered["open_age_days"].max() if not filtered.empty else 0)
    age_range = st.slider("Filter Aging (hari)", 0, max(max_age, 1), (0, max(max_age, 1)))
    table = filtered[(filtered["open_age_days"] >= age_range[0]) & (filtered["open_age_days"] <= age_range[1])].copy()
    table = table.sort_values(["is_completed", "open_age_days"], ascending=[True, False])
    display_cols = ["pr_number", "pr_date", "pic", "requester", "pr_status", "total_items", "completed_items", "open_age_days", "sla_status", "overdue_days", "supplier", "total_value"]
    st.dataframe(
        table[display_cols].rename(columns={
            "pr_number": "No PR", "pr_date": "Tanggal PR", "pic": "PIC", "requester": "Diminta Oleh",
            "pr_status": "Status", "total_items": "Total Item", "completed_items": "Item Selesai",
            "open_age_days": "Aging", "sla_status": "SLA Status", "overdue_days": "Overdue Days",
            "supplier": "Supplier", "total_value": "Total Value"
        }),
        use_container_width=True,
        hide_index=True,
    )
    csv = table.to_csv(index=False).encode("utf-8")
    st.download_button("Download hasil filter (CSV)", csv, "pr_monitoring_filtered.csv", "text/csv")

with pic_tab:
    st.markdown("### PIC Performance")
    current_perf = pic_performance(filtered)
    st.dataframe(current_perf, use_container_width=True, hide_index=True)
    if not current_perf.empty:
        c1, c2 = st.columns(2)
        with c1:
            fig = px.bar(current_perf, x="pic", y=["Completed PR", "Open PR"], barmode="group", title="Completed vs Open PR")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            perf_plot = current_perf.dropna(subset=["SLA Compliance %"])
            fig = px.bar(perf_plot, x="pic", y="SLA Compliance %", title="SLA Compliance per PIC", range_y=[0, 100])
            st.plotly_chart(fig, use_container_width=True)
        st.info("Gunakan KPI ini sebagai indikator operasional. Jangan menilai PIC hanya dari jumlah PR karena kompleksitas PR bisa berbeda.")

with sla_tab:
    st.markdown("### Aging & SLA")
    sla_counts = filtered["sla_status"].value_counts().rename_axis("SLA Status").reset_index(name="PR")
    c1, c2 = st.columns(2)
    with c1:
        fig = px.pie(sla_counts, names="SLA Status", values="PR", title="Distribusi SLA")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        lead = filtered[filtered["lead_time_days"].notna()]
        if not lead.empty:
            fig = px.box(lead, x="pic", y="lead_time_days", points="outliers", title="Lead Time per PIC")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Belum ada lead time yang bisa dihitung. Mapping Tanggal PO diperlukan untuk analisis PR selesai.")

    st.markdown("#### PR Overdue")
    overdue_table = filtered[filtered["sla_status"] == "Overdue"].sort_values("overdue_days", ascending=False)
    st.dataframe(overdue_table[["pr_number", "pr_date", "pic", "open_age_days", "sla_days", "overdue_days", "requester", "supplier"]], use_container_width=True, hide_index=True)

with data_tab:
    st.markdown("### Data Source")
    st.write(f"Baris item: **{len(item_df):,}** · PR unik: **{pr_df['pr_number'].nunique():,}**")
    st.caption("Raw report tetap berada pada level item. KPI utama dihitung pada level PR unik.")
    st.dataframe(item_df.head(100), use_container_width=True, hide_index=True)
    with st.expander("Mapping aktif"):
        st.json(mapping)

st.divider()
st.caption("MVP v1 · Next step: sesuaikan status ERP, SLA per kategori/jenis PR, dan koneksi data otomatis.")
