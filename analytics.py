import re
import pandas as pd
import numpy as np

COLUMN_CANDIDATES = {
    "pr_number": ["no pr", "no. pr", "nomor pr", "no transaksi", "pr number", "pr no", "purchase request"],
    "pr_date": ["tanggal pr", "tgl pr", "pr date", "request date", "tanggal request"],
    "pic": ["pic procurement", "pic purchasing", "pic", "buyer", "procurement"],
    "status": ["status", "status pr", "pr status"],
    "po_date": ["tanggal po", "tgl po", "po date", "tanggal purchase order"],
    "po_number": ["no po", "no. po", "nomor po", "po number"],
    "item_code": ["kode item", "item code", "kode barang"],
    "item_name": ["nama item", "uraian", "item name", "nama barang", "description"],
    "requester": ["diminta oleh", "requester", "requested by", "user"],
    "supplier": ["supplier", "vendor", "nama supplier", "nama vendor"],
    "qty": ["jumlah", "qty", "quantity"],
    "value": ["total", "nilai", "amount", "harga jual", "value"],
}


def normalize_name(value: str) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def suggest_column(columns, key):
    normalized = {normalize_name(c): c for c in columns}
    for candidate in COLUMN_CANDIDATES.get(key, []):
        c = normalize_name(candidate)
        if c in normalized:
            return normalized[c]
    for candidate in COLUMN_CANDIDATES.get(key, []):
        c = normalize_name(candidate)
        for norm, original in normalized.items():
            if c in norm or norm in c:
                return original
    return None


def clean_text(series):
    return series.fillna("").astype(str).str.strip()


def parse_dates(series):
    """Parse Excel datetimes, ISO yyyy-mm-dd, and common Indonesian day-first dates."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")

    raw = series.copy()
    result = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    text = raw.astype("string").str.strip()

    iso_mask = text.str.match(r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T].*)?$", na=False)
    if iso_mask.any():
        result.loc[iso_mask] = pd.to_datetime(text.loc[iso_mask], errors="coerce", yearfirst=True)

    other_mask = ~iso_mask & text.notna() & text.ne("")
    if other_mask.any():
        result.loc[other_mask] = pd.to_datetime(
            text.loc[other_mask], errors="coerce", dayfirst=True, format="mixed"
        )

    return result


def prepare_data(df, mapping, sla_days=7, completed_keywords=None):
    completed_keywords = completed_keywords or ["closed", "complete", "completed", "po created", "po", "selesai"]
    out = df.copy()

    rename_map = {v: k for k, v in mapping.items() if v and v in out.columns}
    out = out.rename(columns=rename_map)

    required = ["pr_number", "pr_date", "pic", "status"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise ValueError("Kolom wajib belum dipetakan: " + ", ".join(missing))

    for col in ["pr_number", "pic", "status", "po_number", "item_code", "item_name", "requester", "supplier"]:
        if col not in out.columns:
            out[col] = ""
        out[col] = clean_text(out[col])

    out["pr_number"] = out["pr_number"].replace("", np.nan)
    out = out.dropna(subset=["pr_number"]).copy()

    out["pr_date"] = parse_dates(out["pr_date"])
    if "po_date" not in out.columns:
        out["po_date"] = pd.NaT
    out["po_date"] = parse_dates(out["po_date"])

    for col in ["qty", "value"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")

    status_norm = out["status"].str.lower()
    keyword_pattern = "|".join(re.escape(k.lower()) for k in completed_keywords if k.strip())
    by_status = status_norm.str.contains(keyword_pattern, regex=True, na=False) if keyword_pattern else False
    by_po = out["po_date"].notna() | out["po_number"].ne("")
    out["item_completed"] = by_status | by_po

    today = pd.Timestamp.today().normalize()
    out["item_age_days"] = (today - out["pr_date"]).dt.days.clip(lower=0)

    def mode_or_first(s):
        s = s[s.astype(str).str.strip().ne("")]
        if s.empty:
            return "Unassigned"
        modes = s.mode()
        return modes.iloc[0] if not modes.empty else s.iloc[0]

    grouped = out.groupby("pr_number", dropna=False)
    pr = grouped.agg(
        pr_date=("pr_date", "min"),
        pic=("pic", mode_or_first),
        requester=("requester", mode_or_first),
        supplier=("supplier", mode_or_first),
        total_items=("pr_number", "size"),
        completed_items=("item_completed", "sum"),
        total_value=("value", "sum"),
        last_po_date=("po_date", "max"),
    ).reset_index()

    pr["completed_items"] = pr["completed_items"].astype(int)
    pr["is_completed"] = pr["completed_items"].eq(pr["total_items"])
    pr["is_partial"] = pr["completed_items"].gt(0) & ~pr["is_completed"]
    pr["pr_status"] = np.select(
        [pr["is_completed"], pr["is_partial"]],
        ["Completed", "Partial"],
        default="Open",
    )

    pr["end_date"] = pr["last_po_date"].where(pr["is_completed"])
    fallback_completed_date = grouped.apply(
        lambda g: g.loc[g["item_completed"], "po_date"].max() if g["item_completed"].any() else pd.NaT,
        include_groups=False,
    )
    fallback_completed_date.index.name = "pr_number"
    fallback_map = fallback_completed_date.to_dict()
    mask_completed_no_date = pr["is_completed"] & pr["end_date"].isna()
    pr.loc[mask_completed_no_date, "end_date"] = pr.loc[mask_completed_no_date, "pr_number"].map(fallback_map)

    # Jika status menyatakan selesai tetapi tanggal PO tidak tersedia, tanggal selesai tidak ditebak.
    # Lead time akan kosong; SLA selesai hanya dihitung jika tanggal selesai tersedia.
    pr["open_age_days"] = (today - pr["pr_date"]).dt.days.clip(lower=0)
    pr["lead_time_days"] = (pr["end_date"] - pr["pr_date"]).dt.days
    pr["age_days"] = np.where(pr["is_completed"] & pr["lead_time_days"].notna(), pr["lead_time_days"], pr["open_age_days"])
    pr["age_days"] = pd.to_numeric(pr["age_days"], errors="coerce")

    pr["sla_days"] = int(sla_days)
    pr["overdue_days"] = (pr["open_age_days"] - pr["sla_days"]).clip(lower=0)

    completed_with_date = pr["is_completed"] & pr["lead_time_days"].notna()
    pr["sla_status"] = "Open"
    pr.loc[completed_with_date & (pr["lead_time_days"] <= pr["sla_days"]), "sla_status"] = "Met"
    pr.loc[completed_with_date & (pr["lead_time_days"] > pr["sla_days"]), "sla_status"] = "Breached"
    pr.loc[pr["is_completed"] & pr["lead_time_days"].isna(), "sla_status"] = "Completed - No Date"
    due_soon_mask = (
        ~pr["is_completed"]
        & (pr["open_age_days"] >= (pr["sla_days"] - 2).clip(lower=0))
        & (pr["open_age_days"] <= pr["sla_days"])
    )
    overdue_mask = ~pr["is_completed"] & (pr["open_age_days"] > pr["sla_days"])
    pr.loc[due_soon_mask, "sla_status"] = "Due Soon"
    pr.loc[overdue_mask, "sla_status"] = "Overdue"

    bins = [-1, 3, 7, 14, np.inf]
    labels = ["0-3 days", "4-7 days", "8-14 days", ">14 days"]
    pr["aging_bucket"] = pd.cut(pr["open_age_days"], bins=bins, labels=labels)

    return out, pr


def pic_performance(pr):
    if pr.empty:
        return pd.DataFrame()

    def summarize(g):
        assigned = g["pr_number"].nunique()
        completed = int(g["is_completed"].sum())
        open_count = assigned - completed
        overdue = int((g["sla_status"] == "Overdue").sum())
        completed_sla = g[g["is_completed"] & g["lead_time_days"].notna()]
        sla_met = int((completed_sla["lead_time_days"] <= completed_sla["sla_days"]).sum())
        sla_compliance = (sla_met / len(completed_sla) * 100) if len(completed_sla) else np.nan
        avg_lead = completed_sla["lead_time_days"].mean() if len(completed_sla) else np.nan
        completion_rate = completed / assigned * 100 if assigned else 0
        overdue_rate = overdue / open_count * 100 if open_count else 0
        return pd.Series({
            "Assigned PR": assigned,
            "Completed PR": completed,
            "Open PR": open_count,
            "Overdue PR": overdue,
            "Completion Rate %": round(completion_rate, 1),
            "SLA Compliance %": round(sla_compliance, 1) if pd.notna(sla_compliance) else np.nan,
            "Avg Lead Time (days)": round(avg_lead, 1) if pd.notna(avg_lead) else np.nan,
            "Overdue Rate %": round(overdue_rate, 1),
        })

    perf = pr.groupby("pic", dropna=False).apply(summarize, include_groups=False).reset_index()
    perf = perf.sort_values(["Overdue PR", "Open PR"], ascending=[False, False])
    return perf
